"""Study 4 (RUNLOG 2026-10-07, approved): regime-conditioned 0DTE straddle at the D-1 close.

For each in-sample session D (equity session, not a half day) with a GEX row whose nearest expiry
is the SPXW expiring on D:
  entry   the D-1 EOD report (17:00 ET Cboe curb close, the same report the engine's EM comes from)
  K       the strike nearest the nearest-expiry forward F = s0 with both legs valid (the engine's
          atm_straddle rule, so mid_C + mid_P at K equals gex_daily.em)
  regime  gex_pct of the session s4_regime_lag sessions BEFORE D (default 1: the D-1 row, whose
          inputs were all published before 17:00 on D-1). The D row uses open interest published
          the morning of D and is NOT knowable at entry; it is reported as a diagnostic only.
  settle  S_T = SPX close on D (SPXW is PM-settled to the index close)

  short straddle:  credit = bid_C(K) + bid_P(K);   pnl = credit - |S_T - K| - 2 f
  long straddle:   debit  = ask_C(K) + ask_P(K);   pnl = |S_T - K| - debit - 2 f
  iron fly:        wings K_up (call) and K_dn (put), the valid strikes nearest K +/- s4_wing_em EM,
                   bought at the ask; credit = bid_C(K) + bid_P(K) - ask_C(K_up) - ask_P(K_dn)
                   pnl = credit - (|S_T - K| - max(S_T - K_up, 0) - max(K_dn - S_T, 0)) - 4 f
  f = opt_cost_per_leg_usd / option_multiplier (points per leg, charged once per leg: entry
      commission, exchange and regulatory fees, and settlement).
  pnl_em = pnl_pts / EM;  pnl_usd = pnl_pts * option_multiplier.

All three structures are evaluated on every eligible session; the regime filter (regime_pct >=
regime_threshold for S4a and S4c, < for S4b) is applied in the report, with the complement as the
contrast, a permutation of the regime across sessions as the placebo, and the Stage 1 regression
restated with the straddle P&L as the outcome.

  python -m src.study4              # simulate and report
  python -m src.study4 --report-only
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import gex, stats, store, study3
from src.config import load_config, param

log = logging.getLogger("study4")

MODES = ("short_straddle", "long_straddle", "iron_fly")
VARIANTS = {  # name -> (mode, high regime?)
    "S4a_short_straddle_high_gamma": ("short_straddle", True),
    "S4b_long_straddle_low_gamma": ("long_straddle", False),
    "S4c_iron_fly_high_gamma": ("iron_fly", True),
}
WING_TOL = 0.25        # data-quality constant: a wing more than 25% off its target width is not the trade
N_TAIL_DAYS = 5        # worst / best days reported (not a tunable)


# ---------------------------------------------------------------------------
# Chain and structures
# ---------------------------------------------------------------------------
def wide_chain(q: pd.DataFrame, max_rel_spread: float) -> pd.DataFrame:
    """One row per strike: bid, ask, mid and valid flag per right."""
    q = q.assign(valid=gex.valid_quote_mask(q, max_rel_spread), mid=(q["bid"] + q["ask"]) / 2)
    w = q.pivot_table(index="strike", columns="right", values=["bid", "ask", "mid", "valid"], aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    for r in ("C", "P"):
        for c in ("bid", "ask", "mid"):
            if f"{c}_{r}" not in w.columns:
                w[f"{c}_{r}"] = np.nan
        w[f"valid_{r}"] = w[f"valid_{r}"].fillna(False).astype(bool) if f"valid_{r}" in w.columns else False
    return w.sort_index()


def select_atm(w: pd.DataFrame, F: float) -> float:
    """The engine's rule (gex.atm_straddle): strike nearest F among strikes with both legs valid."""
    both = w[w["valid_C"] & w["valid_P"]]
    if both.empty or not np.isfinite(F):
        return np.nan
    return float(both.index[np.argmin(np.abs(both.index.values - F))])


def straddle_mid(w: pd.DataFrame, K: float) -> float:
    return float(w.loc[K, "mid_C"] + w.loc[K, "mid_P"])


def select_wing(w: pd.DataFrame, K: float, target_width: float, right: str) -> float:
    """Valid strike nearest K + width (calls, above K) or K - width (puts, below K); NaN when the
    nearest candidate is more than WING_TOL off the target width."""
    side = w[w[f"valid_{right}"] & ((w.index > K) if right == "C" else (w.index < K))]
    if side.empty or target_width <= 0:
        return np.nan
    target = K + target_width if right == "C" else K - target_width
    k = float(side.index[np.argmin(np.abs(side.index.values - target))])
    if abs(abs(k - K) - target_width) > WING_TOL * target_width:
        return np.nan
    return k


def fee_pts(cfg, legs: int) -> float:
    return legs * param(cfg, "opt_cost_per_leg_usd") / cfg["market"]["option_multiplier"]


def _finish(cfg, pnl_pts: float, em: float, extra: dict) -> dict:
    extra["pnl_pts"] = pnl_pts
    extra["pnl_em"] = pnl_pts / em
    extra["pnl_usd"] = pnl_pts * cfg["market"]["option_multiplier"]
    return extra


def short_straddle(w: pd.DataFrame, K: float, settle: float, em: float, cfg) -> dict:
    credit = float(w.loc[K, "bid_C"] + w.loc[K, "bid_P"])
    owed = abs(settle - K)
    fees = fee_pts(cfg, 2)
    return _finish(cfg, credit - owed - fees, em, {
        "mode": "short_straddle", "premium_pts": credit, "settle_value_pts": owed, "fees_pts": fees, "legs": 2,
        "spread_pts": float((w.loc[K, "ask_C"] - w.loc[K, "bid_C"]) + (w.loc[K, "ask_P"] - w.loc[K, "bid_P"]))})


def long_straddle(w: pd.DataFrame, K: float, settle: float, em: float, cfg) -> dict:
    debit = float(w.loc[K, "ask_C"] + w.loc[K, "ask_P"])
    paid = abs(settle - K)
    fees = fee_pts(cfg, 2)
    return _finish(cfg, paid - debit - fees, em, {
        "mode": "long_straddle", "premium_pts": debit, "settle_value_pts": paid, "fees_pts": fees, "legs": 2,
        "spread_pts": float((w.loc[K, "ask_C"] - w.loc[K, "bid_C"]) + (w.loc[K, "ask_P"] - w.loc[K, "bid_P"]))})


def iron_fly(w: pd.DataFrame, K: float, settle: float, em: float, cfg) -> dict | None:
    width = param(cfg, "s4_wing_em") * em
    K_up, K_dn = select_wing(w, K, width, "C"), select_wing(w, K, width, "P")
    if not (np.isfinite(K_up) and np.isfinite(K_dn)):
        return None
    credit = float(w.loc[K, "bid_C"] + w.loc[K, "bid_P"] - w.loc[K_up, "ask_C"] - w.loc[K_dn, "ask_P"])
    owed = abs(settle - K) - max(settle - K_up, 0.0) - max(K_dn - settle, 0.0)
    fees = fee_pts(cfg, 4)
    wing_up, wing_dn = K_up - K, K - K_dn
    spread = float((w.loc[K, "ask_C"] - w.loc[K, "bid_C"]) + (w.loc[K, "ask_P"] - w.loc[K, "bid_P"])
                   + (w.loc[K_up, "ask_C"] - w.loc[K_up, "bid_C"]) + (w.loc[K_dn, "ask_P"] - w.loc[K_dn, "bid_P"]))
    return _finish(cfg, credit - owed - fees, em, {
        "mode": "iron_fly", "premium_pts": credit, "settle_value_pts": owed, "fees_pts": fees, "legs": 4,
        "K_up": K_up, "K_dn": K_dn, "wing_up_pts": wing_up, "wing_dn_pts": wing_dn,
        "max_loss_pts": max(wing_up, wing_dn) - credit + fees, "spread_pts": spread})


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def lagged_regime(day, cal: pd.DataFrame, gex_daily: pd.DataFrame, lag: int) -> float:
    """gex_pct of the session `lag` equity sessions before `day` (cal and gex_daily indexed by date)."""
    d = day
    for _ in range(lag):
        if d not in cal.index:
            return np.nan
        d = cal.loc[d, "prev_date"]
        if d is None or pd.isna(d):
            return np.nan
    if d not in gex_daily.index:
        return np.nan
    return float(gex_daily.loc[d, "gex_pct"])


def session_trades(day, prev, quotes: pd.DataFrame, g: pd.Series, regime: float, settle: float, cfg):
    """All three structures for one session, or (None, skip_reason)."""
    if g.get("nearest_root") != "SPXW" or pd.isna(g.get("nearest_exp")) or pd.Timestamp(g["nearest_exp"]).date() != day:
        return None, "nearest_not_spxw_0dte"
    if not np.isfinite(regime):
        return None, "no_prev_regime"
    if not np.isfinite(settle):
        return None, "no_settlement"
    em, F = float(g["em"]), float(g["s0"])
    if not (np.isfinite(em) and em > 0 and np.isfinite(F)):
        return None, "no_em"
    if quotes.empty:
        return None, "no_quotes"
    q = quotes.copy()
    q["expiration"] = pd.to_datetime(q["expiration"]).dt.date
    q = q[(q["symbol"] == "SPXW") & (q["expiration"] == day)]
    if q.empty:
        return None, "no_quotes"
    w = wide_chain(q, param(cfg, "max_rel_spread"))
    K = select_atm(w, F)
    if not np.isfinite(K):
        return None, "no_valid_atm"
    common = {"date": day, "prev_date": prev, "K": K, "F": F, "em": em, "settle": settle,
              "regime_pct": regime, "straddle_mid_pts": straddle_mid(w, K)}
    rows = []
    for fn in (short_straddle, long_straddle, iron_fly):
        r = fn(w, K, settle, em, cfg)
        if r is not None:
            rows.append({**common, **r})
    # Quote sanity (rule 6): the ATM bid cannot exceed the ask, the straddle credit cannot exceed the
    # mid by more than the spread, and an iron fly's credit cannot exceed its narrower wing (that
    # would be a riskless position, i.e. a stale print). Such a session is dropped, not traded.
    sane = all(r["premium_pts"] > 0 for r in rows)
    for r in rows:
        if r["mode"] == "iron_fly" and r["premium_pts"] >= min(r["wing_up_pts"], r["wing_dn_pts"]):
            sane = False
    if not sane:
        return None, "quote_sanity"
    return rows, None


def run(cfg=None, save: bool = True) -> pd.DataFrame:
    cfg = cfg or load_config()
    cal = store.load_calendar(cfg)
    gex_daily = store.load_derived("gex_daily", cfg).set_index("date")
    daily = store.load_daily(cfg).sort_values("date")
    spx = daily.set_index("date")["spx_close"]
    vix_prev = dict(zip(daily["date"], daily["vix_close"].shift(1)))
    cal_i = cal.set_index("date")
    lag = int(param(cfg, "s4_regime_lag"))
    if lag < 1:
        raise ValueError("s4_regime_lag must be >= 1: the D row uses OI published after the 17:00 D-1 entry")
    rows, skipped = [], {"half_day": 0, "no_gex_row": 0, "fly_no_wing": 0, "quote_sanity": 0}
    em_mismatch = 0
    for r in cal.itertuples():
        if pd.isna(r.prev_date):
            continue
        if bool(getattr(r, "half_day", False)):
            skipped["half_day"] += 1
            continue
        if r.date not in gex_daily.index:
            skipped["no_gex_row"] += 1
            continue
        g = gex_daily.loc[r.date]
        regime = lagged_regime(r.date, cal_i, gex_daily, lag)
        settle = float(spx.get(r.date, np.nan))
        out, why = session_trades(r.date, r.prev_date, store.load_options_eod(cfg, r.prev_date), g, regime, settle, cfg)
        if why is not None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        if abs(out[0]["straddle_mid_pts"] - float(g["em"])) > 1e-6:
            em_mismatch += 1
        if len(out) < len(MODES):
            skipped["fly_no_wing"] += 1
        if any(not r.get("quotes_sane", True) for r in out):
            skipped["quote_sanity"] += 1
        same_day = float(g["gex_pct"]) if pd.notna(g.get("gex_pct")) else np.nan
        lv = vix_prev.get(r.date, np.nan)
        for row in out:
            row.update({"regime_pct_same_day": same_day, "ln_vix": np.log(lv) if lv and lv > 0 else np.nan,
                        "ln_em_s0": np.log(row["em"] / row["F"]), "dow": pd.Timestamp(r.date).dayofweek})
        rows.extend(out)
    T = pd.DataFrame(rows)
    skipped["em_mismatch_sessions"] = em_mismatch
    log.info("study 4: %d sessions, skipped %s", T["date"].nunique() if not T.empty else 0, skipped)
    T.attrs["skipped"] = skipped
    if save and not T.empty:
        store.save_derived(T, "straddle_trades", cfg)
    return T


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
SESSIONS_PER_MONTH = stats.SESSIONS_PER_MONTH


def summary_em(t: pd.DataFrame, draws: int, seed: int, level: float, sessions_total: int | None = None) -> dict:
    """Per-session P&L in expected-move units: mean with a day-bootstrap interval, win rate, profit
    factor, drawdown, and the tail block (worst days, their share, the mean without the best days)."""
    if t.empty:
        return {"n": 0}
    t = t.sort_values("date")
    p = t["pnl_em"].to_numpy(float)
    wins, losses = p[p > 0], p[p <= 0]
    boot = stats.day_bootstrap_mean(t, "pnl_em", draws, seed, level)
    months = max(1.0, (sessions_total or len(t)) / SESSIONS_PER_MONTH)
    worst = t.nsmallest(N_TAIL_DAYS, "pnl_em")
    best = t.nlargest(N_TAIL_DAYS, "pnl_em")
    total = float(p.sum())
    return {
        "n": len(p), "win_rate": float((p > 0).mean()),
        "mean_em": float(p.mean()), "ci_lo": boot["lo"], "ci_hi": boot["hi"],
        "mean_pts": float(t["pnl_pts"].mean()), "mean_usd": float(t["pnl_usd"].mean()),
        "avg_win_em": float(wins.mean()) if len(wins) else 0.0, "avg_loss_em": float(-losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.inf,
        "max_dd_em": stats.max_drawdown(p), "longest_losing_streak": stats.longest_losing_streak(p),
        "trades_per_month": len(p) / months,
        "premium_em_mean": float((t["premium_pts"] / t["em"]).mean()),
        "spread_em_mean": float((t["spread_pts"] / t["em"]).mean()),
        "friction_em_mean": float(((t["spread_pts"] / 2 + t["fees_pts"]) / t["em"]).mean()),   # half-spread + fees
        "tail": {
            "worst_days": [{"date": str(d), "pnl_em": float(x), "pnl_pts": float(y)}
                           for d, x, y in zip(worst["date"], worst["pnl_em"], worst["pnl_pts"])],
            "total_em": total,
            "worst5_sum_em": float(worst["pnl_em"].sum()), "best5_sum_em": float(best["pnl_em"].sum()),
            "worst5_share_of_total": float(worst["pnl_em"].sum() / total) if total > 0 else np.nan,   # only meaningful for a positive total
            "best5_share_of_total": float(best["pnl_em"].sum() / total) if total > 0 else np.nan,
            "mean_em_ex_best5": float(t[~t["date"].isin(best["date"])]["pnl_em"].mean()) if len(t) > N_TAIL_DAYS else np.nan,
            "mean_em_ex_worst5": float(t[~t["date"].isin(worst["date"])]["pnl_em"].mean()) if len(t) > N_TAIL_DAYS else np.nan,
        },
    }


def _variant_block(m: pd.DataFrame, high: bool, pct_col: str, cfg, draws, seed, lvl, sessions_total) -> dict:
    thr = param(cfg, "regime_threshold")
    regime = (m[pct_col] >= thr) if high else (m[pct_col] < thr)
    v, c = m[regime], m[~regime]
    s = summary_em(v, draws, seed, lvl, sessions_total)
    s["complement"] = summary_em(c, draws, seed, lvl, sessions_total)
    s["regime_contrast"] = stats.day_bootstrap_diff(v, c, "pnl_em", draws, seed, lvl)   # disjoint days: unpaired
    s["permutation"] = study3.permutation_test(m, high_is_variant=high, thr=thr, draws=param(cfg, "perm_draws"),
                                               seed=seed, value="pnl_em", pct=pct_col)
    s["block_permutation"] = block_permutation_test(m, high, thr, param(cfg, "perm_draws"), seed, pct_col)
    return s


BLOCK_SESSIONS = 21     # diagnostic only: one-month blocks keep the regime's persistence under the shuffle


def block_permutation_test(t: pd.DataFrame, high_is_variant: bool, thr: float, draws: int, seed: int,
                           pct: str = "regime_pct", block: int = BLOCK_SESSIONS) -> dict:
    """Diagnostic, not a gate: shuffle the regime in contiguous blocks of `block` sessions so the
    persistence of the rolling percentile survives the shuffle. p = (count + 1) / (draws + 1)."""
    t = t.sort_values("date")
    pnl, g = t["pnl_em"].to_numpy(float), t[pct].to_numpy(float)
    n = len(t)
    if n < 2 * block:
        return {"p": np.nan, "note": "too few sessions"}
    sel = (g >= thr) if high_is_variant else (g < thr)
    if sel.sum() == 0 or (~sel).sum() == 0:
        return {"p": np.nan}
    obs = pnl[sel].mean() - pnl[~sel].mean()
    rng = np.random.default_rng(seed + 1)
    blocks = [g[i:i + block] for i in range(0, n, block)]
    count = 0
    for _ in range(draws):
        order = rng.permutation(len(blocks))
        gp = np.concatenate([blocks[i] for i in order])[:n]
        s = (gp >= thr) if high_is_variant else (gp < thr)
        if s.sum() == 0 or (~s).sum() == 0:
            continue
        if pnl[s].mean() - pnl[~s].mean() >= obs:
            count += 1
    return {"observed_gap": float(obs), "p": float((count + 1) / (draws + 1)), "block_sessions": block, "draws": draws,
            "note": "diagnostic: block shuffle of the persistent regime; the gate uses the session permutation"}


def _stage1_restated(m: pd.DataFrame, pct_col: str, control: str, lags: int) -> dict:
    d = m.dropna(subset=["pnl_em", pct_col, control, "dow"])
    if len(d) < 30:
        return {"n": int(len(d)), "note": "too few sessions"}
    res = stats.ols_nw(d.sort_values("date"), f"pnl_em ~ {pct_col} + {control} + C(dow)", lags)
    return {"beta_regime": float(res.params[pct_col]), "p": float(res.pvalues[pct_col]), "n": int(res.nobs),
            "r2": float(res.rsquared)}


def report(cfg=None, trades: pd.DataFrame | None = None) -> dict:
    cfg = cfg or load_config()
    if trades is None:
        trades = store.load_derived("straddle_trades", cfg)
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    lags = param(cfg, "nw_lags")
    g = cfg["gates"]
    min_e = g["study4_min_expectancy_em"]
    sessions = int(trades["date"].nunique())
    out = {"sessions_traded": sessions, "regime": f"gex_pct lagged {param(cfg, 's4_regime_lag')} session(s), threshold {param(cfg, 'regime_threshold')}",
           "variants": {}, "contrasts": {}, "stage1_restated": {}, "diagnostic_same_day_regime": {},
           "variant_count_note": "studies 1-3 used 12; study 4 adds 3 -> 15 of 20"}
    for name, (mode, high) in VARIANTS.items():
        m = trades[trades["mode"] == mode].copy()
        s = _variant_block(m, high, "regime_pct", cfg, draws, seed, lvl, sessions)
        checks = {f"n>={g['stage3_min_trades']}": s.get("n", 0) >= g["stage3_min_trades"],
                  f"expectancy_em>={min_e}": s.get("mean_em", -1) >= min_e,
                  "ci_lower>0": s.get("ci_lo", -1) > 0,
                  "regime_contrast_ci_lower>0": s["regime_contrast"]["lo"] > 0,
                  "permutation_p<0.05": np.isfinite(s["permutation"]["p"]) and s["permutation"]["p"] < 0.05}
        s["checks"] = checks
        s["verdict_vs_rules"] = "PASS" if all(checks.values()) else ("INDICATIVE" if s.get("n", 0) < g["stage3_min_trades"] else "KILL")
        out["variants"][name] = s
        # Same-day regime (OI published the morning of D): NOT knowable at the 17:00 D-1 entry.
        if m["regime_pct_same_day"].notna().any():
            d = _variant_block(m.dropna(subset=["regime_pct_same_day"]), high, "regime_pct_same_day", cfg, draws, seed, lvl, sessions)
            d["note"] = "diagnostic only: uses OI published after the entry time; not tradeable"
            out["diagnostic_same_day_regime"][name] = d
    for mode in MODES:
        m = trades[trades["mode"] == mode]
        if m.empty:
            continue
        out["contrasts"][f"{mode}_all_sessions"] = summary_em(m, draws, seed, lvl, sessions)
        if m["regime_pct"].nunique() >= 3:
            ter = pd.qcut(m["regime_pct"].rank(method="first"), 3, labels=["low", "mid", "high"])
            for k, mm in m.groupby(ter, observed=True):
                out["contrasts"][f"{mode}_tercile_{k}"] = summary_em(mm, draws, seed, lvl, sessions)
    ss = trades[trades["mode"] == "short_straddle"]
    if not ss.empty:
        out["stage1_restated"]["short_straddle_ln_vix"] = _stage1_restated(ss, "regime_pct", "ln_vix", lags)
        out["stage1_restated"]["short_straddle_ln_em_s0"] = _stage1_restated(ss, "regime_pct", "ln_em_s0", lags)
        out["stage1_restated"]["short_straddle_same_day_regime_diagnostic"] = _stage1_restated(ss, "regime_pct_same_day", "ln_vix", lags)
        out["stage1_restated"]["hypothesis"] = "beta_regime > 0: the short straddle pays more when prior-session gamma is high"
    out["skipped"] = trades.attrs.get("skipped", {})
    ss_all = trades[trades["mode"] == "short_straddle"]
    out["em_mismatch_sessions_from_table"] = int(((ss_all["straddle_mid_pts"] - ss_all["em"]).abs() > 1e-6).sum()) if "straddle_mid_pts" in ss_all else None
    out["sessions_missing_iron_fly"] = int(sessions - trades[trades["mode"] == "iron_fly"]["date"].nunique())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    T = store.load_derived("straddle_trades", cfg) if a.report_only else run(cfg)
    if T.empty:
        print("no straddle trades")
        return
    print(T.groupby("mode")["pnl_em"].agg(["size", "mean"]).to_string())
    from src.analysis import to_json
    print(to_json(report(cfg, T)))


if __name__ == "__main__":
    main()
