"""Study 20: short VIX futures through SVXY/SVIX-like positions (Mack & Sinclair ch. 10/15; STUDY20.md).

Index: short-term VIX futures rebuilt from Cboe settlements. On each day the previous close's weights roll linearly from
the front to the second contract, the front weight reaching zero the day before it expires:
    w1(p) = (trading days in (p, exp1] - 1) / (trading days in (exp_prev, exp1]),   ret(t) = w1 r1 + (1 - w1) r2.
ETF day: -lev x ret - fee/252, floored at -100%. Signals use closes of day t; the position changes at the close of
t+1 (held over day t+2); each change costs s20_slippage per unit of account; $0 commission.

  X1 always -0.5x | X2 -0.5x while VX1 < VX2 | X3 -0.5x for s20_hold_d days after VIX falls back below VIX3M
  X4 -1x while VX1 < VX2, flat after any day VIX rises >= s20_breaker_vix_jump

  python -m src.study20 --discover      # 2008-2019, the four variants against the rule
  python -m src.study20 --validate      # ONCE, 2020-01..2025-09, only discovery passes
  python -m src.study20 --holdout       # ONCE, only on "run the holdout" (GAMMA_EDGE_RUN_HOLDOUT=1), validation passes
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging

import numpy as np
import pandas as pd

from src import store
from src.config import data_path, load_config, param

log = logging.getLogger("study20")


# ---- pure pieces (tested) ------------------------------------------------------------------------------------------
def index_returns(L: pd.DataFrame, prev_expiry: dict | None = None) -> pd.DataFrame:
    days = np.array(sorted(L["date"].unique()))
    exps = sorted(L["expiry"].unique())
    px = L.set_index(["date", "expiry"])["settle"]
    pos = {d: i for i, d in enumerate(days)}

    def ntd(a, b):                                   # trading days in (a, b]
        return int(np.searchsorted(days, b, "right") - np.searchsorted(days, a, "right"))

    out = []
    for i in range(1, len(days)):
        p, t = days[i - 1], days[i]
        live = [e for e in exps if e > p]
        if len(live) < 2:
            continue
        e1, e2 = live[0], live[1]
        k = exps.index(e1)
        e0 = exps[k - 1] if k > 0 else (prev_expiry or {}).get(e1, e1 - dt.timedelta(days=28))
        N = max(ntd(e0, e1), 1)
        w1 = max(ntd(p, e1) - 1, 0) / N
        def r(e):
            a, b = px.get((p, e), np.nan), px.get((t, e), np.nan)
            return b / a - 1 if np.isfinite(a) and np.isfinite(b) and a > 0 else np.nan
        r2 = r(e2)
        r1 = r(e1) if w1 > 0 else 0.0
        out.append({"date": t, "ret": w1 * r1 + (1 - w1) * r2, "w1": w1, "front": e1})
    return pd.DataFrame(out)


def etf_return(idx_ret, lev: float, fee: float) -> np.ndarray:
    return np.maximum(-lev * np.asarray(idx_ret, float) - fee / 252, -1.0)


def x3_signal(vix: pd.Series, vix3m: pd.Series, lookback: int, hold: int) -> pd.Series:
    """Enter on the first close with VIX < VIX3M after an inverted close (VIX >= VIX3M) within `lookback` days; stay
    `hold` days (re-entries extend). The crossing day always has the inversion one day back, inside the lookback."""
    inv = (vix >= vix3m).to_numpy()
    n = len(inv)
    trig = np.zeros(n, bool)
    for t in range(1, n):
        if not inv[t] and inv[max(0, t - lookback):t].any() and inv[t - 1]:
            trig[t] = True
    s = np.zeros(n, int)
    for t in np.where(trig)[0]:
        s[t:t + hold] = 1
    return pd.Series(s, index=vix.index)


def held(signal) -> np.ndarray:
    """Position held over day t = signal at the close of t-2 (decided at t-2, traded at the close of t-1)."""
    s = np.asarray(signal, int)
    h = np.zeros(len(s), int)
    h[2:] = s[:-2]
    return h


def strategy_returns(etf, held_pos, slip: float) -> np.ndarray:
    h = np.asarray(held_pos, float)
    nxt = np.r_[h[1:], h[-1]]
    return h * np.asarray(etf, float) - slip * np.abs(nxt - h)


def series_stats(r: pd.Series, draws: int, seed: int, level: float, in_market=None) -> dict:
    r = r.dropna()
    n = len(r)
    if n < 20:
        return {"days": n}
    v = r.to_numpy(float)
    months = pd.PeriodIndex(r.index, freq="M")
    groups = [v[months == m] for m in months.unique()]
    rng = np.random.default_rng(seed)
    sums = np.array([g.sum() for g in groups])
    cnts = np.array([len(g) for g in groups])
    idx = rng.integers(0, len(groups), (draws, len(groups)))
    boot = sums[idx].sum(1) / cnts[idx].sum(1)
    a = (1 - level) / 2
    eq = np.cumprod(1 + v)
    dd = 1 - eq / np.maximum.accumulate(np.r_[1.0, eq])[1:]
    h = n // 2
    with np.errstate(divide="ignore"):
        lg = np.log1p(v)
    geo = float(np.exp(lg.mean() * 252) - 1) if np.isfinite(lg).all() else -1.0
    yr = pd.Series(v, index=r.index).groupby(pd.DatetimeIndex(r.index).year).apply(lambda x: float(np.prod(1 + x) - 1))
    worst = r.nsmallest(5)
    return {"days": n, "mean": float(v.mean()), "lo": float(np.quantile(boot, a)), "hi": float(np.quantile(boot, 1 - a)),
            "geometric_annual": geo, "arith_annual": float(v.mean() * 252),
            "vol_annual": float(v.std(ddof=1) * np.sqrt(252)),
            "sharpe": float(v.mean() / v.std(ddof=1) * np.sqrt(252)) if v.std() > 0 else None,
            "first_half": float(v[:h].mean()), "second_half": float(v[h:].mean()),
            "max_drawdown": float(dd.max()), "final_equity": float(eq[-1]),
            "days_in_market": int(np.sum(in_market)) if in_market is not None else None,
            "by_year": {int(k): round(x, 4) for k, x in yr.items()},
            "worst_days": {str(pd.Timestamp(k).date()): round(float(x), 4) for k, x in worst.items()}}


def passes(st: dict, max_dd: float, min_days: int | None = None) -> bool:
    pos = lambda k: st.get(k) is not None and np.isfinite(st[k]) and st[k] > 0
    ok = (pos("mean") and pos("lo") and pos("geometric_annual") and pos("first_half") and pos("second_half")
          and st.get("max_drawdown", 1) <= max_dd)
    if min_days is not None:
        ok = ok and (st.get("days_in_market") or 0) >= min_days
    return bool(ok)


# ---- data and variants -----------------------------------------------------------------------------------------------
def load(cfg, include_holdout: bool = False) -> pd.DataFrame:
    L = pd.read_parquet(data_path(cfg, "derived", "vix_futures_long.parquet"))
    P = pd.read_parquet(data_path(cfg, "derived", "vix_futures_panel.parquet"))
    for X in (L, P):
        X["date"] = pd.to_datetime(X["date"]).dt.date
    L["expiry"] = pd.to_datetime(L["expiry"]).dt.date
    hs = pd.Timestamp(param(cfg, "s20_holdout_start")).date()
    if not include_holdout:
        L, P = L[L["date"] < hs], P[P["date"] < hs]
    I = index_returns(L)
    spot_f = data_path(cfg, "raw", "vix_futures", "spot.parquet")
    if not spot_f.exists():
        raise SystemExit("VIX/VIX3M history missing: run python -m src.vix_futures --spot")
    daily = pd.read_parquet(spot_f).rename(columns={"vix": "vix_close", "vix3m": "vix3m_close"})
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    if include_holdout:
        from src.calendar import holdout_unsealed
        if not holdout_unsealed():
            raise SystemExit("holdout needs GAMMA_EDGE_RUN_HOLDOUT=1")
    else:
        daily = daily[daily["date"] < hs]
    D = I.merge(P[["date", "vx1", "vx2"]], on="date").merge(daily[["date", "vix_close", "vix3m_close"]], on="date")
    D = D.dropna(subset=["ret"]).sort_values("date").reset_index(drop=True)
    return D


def variants(D: pd.DataFrame, cfg) -> dict:
    half, full = param(cfg, "s20_leverage_half"), param(cfg, "s20_leverage_full")
    fh, ff, slip = param(cfg, "s20_fee_half"), param(cfg, "s20_fee_full"), param(cfg, "s20_slippage")
    contango = (D["vx1"] < D["vx2"]).astype(int).to_numpy()
    x3 = x3_signal(D["vix_close"], D["vix3m_close"], param(cfg, "s20_inversion_lookback_d"),
                   param(cfg, "s20_hold_d")).to_numpy()
    jump = (D["vix_close"].pct_change() >= param(cfg, "s20_breaker_vix_jump")).to_numpy()
    sig = {"X1": (np.ones(len(D), int), half, fh), "X2": (contango, half, fh), "X3": (x3, half, fh),
           "X4": (contango * (~jump).astype(int), full, ff),
           "X1_full_reported": (np.ones(len(D), int), full, ff), "X3_full_reported": (x3, full, ff)}
    out = {}
    for k, (s, lev, fee) in sig.items():
        h = held(s)
        out[k] = (strategy_returns(etf_return(D["ret"], lev, fee), h, slip), h)
    return out


def _period(D, R, H, lo, hi):
    m = ((D["date"] >= pd.Timestamp(lo).date()) & (D["date"] <= pd.Timestamp(hi).date())).to_numpy()
    return pd.Series(R[m], index=pd.to_datetime(D.loc[m, "date"])), H[m]


def evaluate(cfg, D, period, min_days=None, only=None, seed_offset=0) -> dict:
    draws, lvl, seed = param(cfg, "bootstrap_draws"), param(cfg, "ci_level"), param(cfg, "s20_seed") + seed_offset
    out = {}
    for k, (R, H) in variants(D, cfg).items():
        if only is not None and k not in only:
            continue
        r, h = _period(D, R, H, *period)
        st = series_stats(r, draws, seed, lvl, in_market=h)
        if not k.endswith("_reported"):
            st["pass"] = passes(st, param(cfg, "s20_max_dd"), min_days)
        out[k] = st
    return out


def _sanity(D) -> dict:
    """Rule 6 inputs: the rebuilt index should average a strong negative drift (roll) and spike in 2008/2018/2020."""
    r = pd.Series(D["ret"].to_numpy(), index=pd.to_datetime(D["date"]))
    yr = r.groupby(r.index.year).apply(lambda x: round(float(np.prod(1 + x) - 1), 3))
    return {"index_by_year": {int(k): v for k, v in yr.items()},
            "index_biggest_days": {str(k.date()): round(float(v), 3) for k, v in r.nlargest(5).items()},
            "share_days_contango": round(float((D["vx1"] < D["vx2"]).mean()), 3),
            "share_days_vix_inverted": round(float((D["vix_close"] >= D["vix3m_close"]).mean()), 3),
            "first_date": str(D["date"].min()), "last_date": str(D["date"].max())}


def check_etf(cfg) -> dict:
    """Rule 6: the rebuilt index and the simulated -0.5x/-1x legs against the real ETFs (VXX, SVXY, SVIX) over their
    in-sample histories (yfinance, optional: pip install yfinance). Daily return correlation and yearly returns."""
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("pip install yfinance (only needed for this check)")
    D = load(cfg)
    idx = pd.Series(D["ret"].to_numpy(), index=pd.to_datetime(D["date"]))
    hs = pd.Timestamp(param(cfg, "s20_holdout_start"))
    legs = {"VXX": (idx, None), "SVXY": (pd.Series(etf_return(idx, param(cfg, "s20_leverage_half"),
                                                                param(cfg, "s20_fee_half")), index=idx.index),
                                         "2018-03-01"),
            "SVIX": (pd.Series(etf_return(idx, param(cfg, "s20_leverage_full"), param(cfg, "s20_fee_full")),
                               index=idx.index), "2022-04-01")}
    out = {}
    for tk, (sim, start) in legs.items():
        px = yf.download(tk, start=start or "2018-01-01", end=str(hs.date()), auto_adjust=True, progress=False)
        if px is None or len(px) == 0:
            out[tk] = "no data"
            continue
        close = px["Close"].squeeze()
        real = close.pct_change().dropna()
        real.index = pd.to_datetime(real.index).tz_localize(None)
        both = pd.concat([sim.rename("sim"), real.rename("real")], axis=1, join="inner").dropna()
        yr = both.groupby(both.index.year).apply(lambda x: (round(float(np.prod(1 + x["sim"]) - 1), 3),
                                                             round(float(np.prod(1 + x["real"]) - 1), 3)))
        out[tk] = {"days": int(len(both)), "corr": round(float(both["sim"].corr(both["real"])), 4),
                   "mean_abs_daily_gap": round(float((both["sim"] - both["real"]).abs().mean()), 5),
                   "by_year_sim_vs_real": {int(k): v for k, v in yr.items()},
                   "biggest_gaps": {str(k.date()): (round(float(r.sim), 3), round(float(r.real), 3))
                                    for k, r in both.assign(g=(both["sim"] - both["real"]).abs()).nlargest(5, "g").iterrows()}}
    return {"study": 20, "check": "rebuilt index vs real ETFs (in sample only)", **out}


def discover(cfg) -> dict:
    D = load(cfg)
    res = {"study": 20, "stage": "discovery", "period": param(cfg, "s20_discovery"), "sanity": _sanity(D),
           "variants": evaluate(cfg, D, param(cfg, "s20_discovery"))}
    d = data_path(cfg, "derived", "study20")
    d.mkdir(parents=True, exist_ok=True)
    (d / "study20_discovery.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def validate(cfg) -> dict:
    d = data_path(cfg, "derived", "study20")
    marker = d / "study20_validation_done.json"
    if marker.exists():
        raise SystemExit("Study 20 validation already run once")
    A = json.loads((d / "study20_discovery.json").read_text())
    passing = [k for k, v in A["variants"].items() if v.get("pass")]
    if not passing:
        raise SystemExit("no variant passed discovery: nothing to validate")
    D = load(cfg)
    res = {"study": 20, "stage": "validation", "period": param(cfg, "s20_validation"),
           "variants": evaluate(cfg, D, param(cfg, "s20_validation"), cfg["gates"]["study20_min_days_in_market"],
                                only=passing, seed_offset=1)}
    marker.write_text(json.dumps({"ran": pd.Timestamp.now(tz="UTC").isoformat()}))
    (d / "study20_validation.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def run_holdout(cfg) -> dict:
    from src.calendar import holdout_unsealed
    if not holdout_unsealed():
        raise SystemExit("holdout needs GAMMA_EDGE_RUN_HOLDOUT=1 (only on 'run the holdout')")
    d = data_path(cfg, "derived", "study20")
    marker = d / "study20_holdout_done.json"
    if marker.exists():
        raise SystemExit("Study 20 holdout already run once")
    V = json.loads((d / "study20_validation.json").read_text())
    passing = [k for k, v in V["variants"].items() if v.get("pass")]
    if not passing:
        raise SystemExit("no variant passed validation")
    D = load(cfg, include_holdout=True)
    hs = param(cfg, "s20_holdout_start")
    res = {"study": 20, "stage": "holdout", "variants": evaluate(cfg, D, (hs, "2100-01-01"), only=passing, seed_offset=2)}
    marker.write_text(json.dumps({"ran": pd.Timestamp.now(tz="UTC").isoformat()}))
    (d / "study20_holdout.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--discover", action="store_true")
    g.add_argument("--validate", action="store_true")
    g.add_argument("--holdout", action="store_true")
    g.add_argument("--check-etf", action="store_true", help="rule 6: compare with VXX/SVXY/SVIX (needs yfinance)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    r = (discover(cfg) if a.discover else validate(cfg) if a.validate else check_etf(cfg) if a.check_etf
         else run_holdout(cfg))
    from src.analysis import to_json
    print(to_json(r))


if __name__ == "__main__":
    main()
