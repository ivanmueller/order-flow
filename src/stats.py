"""Statistics: Newey-West OLS, clustered logit, Wilson intervals, day-bootstrap, trade summaries."""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.proportion import proportion_confint


# ---------------------------------------------------------------------------
# Regressions
# ---------------------------------------------------------------------------
def ols_nw(df: pd.DataFrame, formula: str, lags: int):
    """OLS with Newey-West (HAC) standard errors. Rows must be in time order."""
    return smf.ols(formula, data=df).fit(cov_type="HAC", cov_kwds={"maxlags": lags})


def logit_clustered(df: pd.DataFrame, formula: str, cluster: str = "date"):
    """Logistic regression with standard errors clustered by `cluster` (day)."""
    groups = pd.factorize(df[cluster])[0]
    return smf.logit(formula, data=df).fit(disp=0, cov_type="cluster", cov_kwds={"groups": groups})


def ols_clustered(df: pd.DataFrame, formula: str, cluster: str = "date"):
    groups = pd.factorize(df[cluster])[0]
    return smf.ols(formula, data=df).fit(cov_type="cluster", cov_kwds={"groups": groups})


def coef_table(res) -> pd.DataFrame:
    ci = res.conf_int()
    return pd.DataFrame({"coef": res.params, "se": res.bse, "p": res.pvalues, "lo95": ci[0], "hi95": ci[1]})


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------
def wilson(k: int, n: int, level: float = 0.90) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    return proportion_confint(k, n, alpha=1 - level, method="wilson")


def _day_sums(df: pd.DataFrame, value: str, date_col: str, days: np.ndarray):
    g = df.groupby(date_col)[value].agg(["sum", "count"]).reindex(days, fill_value=0)
    return g["sum"].to_numpy(float), g["count"].to_numpy(float)


def day_bootstrap_mean(df: pd.DataFrame, value: str, draws: int, seed: int, level: float = 0.90,
                       date_col: str = "date") -> dict:
    """Mean of `value` with an interval from resampling whole days."""
    if df.empty:
        return {"mean": np.nan, "lo": np.nan, "hi": np.nan, "n": 0, "days": 0}
    days = np.array(sorted(df[date_col].unique()))
    s, c = _day_sums(df, value, date_col, days)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(draws, len(days)))
    num, den = s[idx].sum(1), c[idx].sum(1)
    boot = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    a = (1 - level) / 2
    return {"mean": float(s.sum() / c.sum()), "lo": float(np.nanquantile(boot, a)),
            "hi": float(np.nanquantile(boot, 1 - a)), "n": int(c.sum()), "days": len(days)}


def day_bootstrap_diff(a: pd.DataFrame, b: pd.DataFrame, value: str, draws: int, seed: int,
                       level: float = 0.90, date_col: str = "date") -> dict:
    """mean(a) - mean(b), resampling the same days for both (paired by day)."""
    if a.empty or b.empty:
        return {"diff": np.nan, "lo": np.nan, "hi": np.nan}
    days = np.array(sorted(set(a[date_col]) | set(b[date_col])))
    sa, ca = _day_sums(a, value, date_col, days)
    sb, cb = _day_sums(b, value, date_col, days)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(draws, len(days)))
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = sa[idx].sum(1) / ca[idx].sum(1) - sb[idx].sum(1) / cb[idx].sum(1)
    q = (1 - level) / 2
    return {"diff": float(sa.sum() / ca.sum() - sb.sum() / cb.sum()),
            "lo": float(np.nanquantile(boot, q)), "hi": float(np.nanquantile(boot, 1 - q))}


def bootstrap_group_means(df: pd.DataFrame, value: str, by: str, draws: int, seed: int,
                          level: float = 0.90, date_col: str = "date") -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(by, observed=True):
        r = day_bootstrap_mean(g, value, draws, seed, level, date_col)
        rows.append({by: key, **r})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Trade summaries
# ---------------------------------------------------------------------------
def max_drawdown(pnl: np.ndarray) -> float:
    if len(pnl) == 0:
        return 0.0
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float(np.max(peak - eq))


def longest_losing_streak(pnl: np.ndarray) -> int:
    best = cur = 0
    for x in pnl:
        cur = cur + 1 if x <= 0 else 0
        best = max(best, cur)
    return best


def trade_summary(tr: pd.DataFrame, draws: int, seed: int, level: float = 0.90) -> dict:
    """N, win rate, average win/loss, expectancy in R with day-bootstrap CI, PF, max DD, streak."""
    if tr.empty:
        return {"n": 0}
    tr = tr.sort_values("entry_ts")
    p = tr["pnl_r"].to_numpy(float)
    wins, losses = p[p > 0], p[p <= 0]
    boot = day_bootstrap_mean(tr, "pnl_r", draws, seed, level)
    months = max(1.0, (pd.Timestamp(max(tr["date"])) - pd.Timestamp(min(tr["date"]))).days / 30.44)
    return {
        "n": len(p), "win_rate": len(wins) / len(p),
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(-losses.mean()) if len(losses) else 0.0,
        "expectancy_r": float(p.mean()), "ci_lo": boot["lo"], "ci_hi": boot["hi"],
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.inf,
        "max_dd_r": max_drawdown(p), "longest_losing_streak": longest_losing_streak(p),
        "trades_per_month": len(p) / months, "days": boot["days"],
    }
