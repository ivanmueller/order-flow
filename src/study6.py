"""Study 6 (RUNLOG 2026-10-07, approved): pure order flow pilot on the on-disk Stage 3 ES ticks.

No levels, no gamma. Sessions: the in-sample Stage 3 day set that has trade files, minus roll and half days.
For each downloaded span [s, e) of a session and each (L, H), decision minutes t on a 1-minute grid with
  t >= s6_grid_start, t - L >= s, t + H <= s6_grid_end, a print at or after t + H inside the span,
  and one instrument_id from t - L to the exit print:
  I_L(t)   = sum(side x size) / sum(size) over trades in [t - L, t)      (needs volume > 0)
  dP_L(t)  = last price before t - first price at or after t - L
  entry    = first print at or after t;  exit = first print at or after t + H
Thresholds for session D, from the s6_lookback_sessions pilot sessions strictly before D (the first
s6_warmup_sessions are not traded): q = s6_flow_pct quantile of |I_L|, m = median |dP_L|.
Variants (s6_variants), scanned in time order with one position at a time (next decision >= the exit time):
  continuation  |I| >= q                              -> d = sign(I)
  absorption    |I| >= q and dP x sign(I) <= 0         -> d = -sign(I)   (aggression that did not move price)
  pressure      |I| >= q and dP x sign(I) >= mult x m  -> d = -sign(I)   (aggression that did move price)
Fills (SPEC rule 5): E = entry + d x slippage ticks, X = exit - d x slippage ticks; pnl = d (X - E) - cost/pv.
Gates (per variant, pilot can only ADVANCE or KILL): n >= study6_min_trades; existence: gross 90%
session-bootstrap CI lower bound > 0; economics: net mean >= study6_min_expectancy_pts and net CI lower
bound > 0; tail: net mean without the study6_tail_drop best sessions > 0.

  python -m src.study6 --count       # eligible slots and trade counts only (no outcomes)
  python -m src.study6               # run, save flow_trades, report
  python -m src.study6 --report-only
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import stats, store
from src.config import load_config, param
from src.ingest_futures import covered, load_trades

log = logging.getLogger("study6")
ET = "America/New_York"


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------
def slot_table(tr: pd.DataFrame, span_s, span_e, L: int, H: int, grid_start, grid_end) -> pd.DataFrame:
    """Eligible decision minutes inside one span with their signal and entry/exit prints."""
    cols = ["t", "I", "vol", "dP", "entry_px", "exit_px", "entry_ts", "exit_ts"]
    if tr is None or tr.empty:
        return pd.DataFrame(columns=cols)
    tr = tr.sort_values(["ts_event_utc", "sequence"], kind="stable")
    ts = tr["ts_event_utc"].to_numpy("datetime64[ns]")
    px = tr["price"].to_numpy(float)
    sz = tr["size"].to_numpy(float)
    sg = tr["side"].to_numpy(float)
    inst = tr["instrument_id"].to_numpy()
    cs_v = np.concatenate([[0.0], np.cumsum(sz)])
    cs_s = np.concatenate([[0.0], np.cumsum(sz * sg)])
    Lt, Ht = pd.Timedelta(minutes=L), pd.Timedelta(minutes=H)
    lo = max(pd.Timestamp(grid_start), pd.Timestamp(span_s) + Lt).ceil("min")
    hi = min(pd.Timestamp(grid_end), pd.Timestamp(span_e)) - Ht
    if hi < lo:
        return pd.DataFrame(columns=cols)
    grid = pd.date_range(lo, hi, freq="min")
    g = grid.to_numpy("datetime64[ns]")
    i0 = np.searchsorted(ts, g - np.timedelta64(L, "m"), "left")
    i1 = np.searchsorted(ts, g, "left")
    ix = np.searchsorted(ts, g + np.timedelta64(H, "m"), "left")
    vol = cs_v[i1] - cs_v[i0]
    ok = (vol > 0) & (ix < len(ts))
    ixc = np.minimum(ix, len(ts) - 1)
    ok &= ts[ixc] < np.datetime64(pd.Timestamp(span_e).tz_convert("UTC").tz_localize(None))
    if not ok.any():
        return pd.DataFrame(columns=cols)
    i0, i1, ix, grid, vol = i0[ok], i1[ok], ix[ok], grid[ok], vol[ok]
    # one contract from the first signal print to the exit print
    chg = np.concatenate([[0], np.cumsum(inst[1:] != inst[:-1])])
    same = chg[ix] == chg[i0]
    i0, i1, ix, grid, vol = i0[same], i1[same], ix[same], grid[same], vol[same]
    return pd.DataFrame({
        "t": grid, "I": (cs_s[i1] - cs_s[i0]) / vol, "vol": vol, "dP": px[i1 - 1] - px[i0],
        "entry_px": px[i1], "exit_px": px[ix],
        "entry_ts": pd.to_datetime(ts[i1], utc=True), "exit_ts": pd.to_datetime(ts[ix], utc=True),
    })


def thresholds(S: pd.DataFrame, pct: float, lookback: int, warmup: int) -> dict:
    """date -> (q of |I|, median |dP|) from the `lookback` sessions strictly before it; warm-up dates omitted."""
    dates = sorted(S["date"].unique())
    by = {d: g for d, g in S.groupby("date")}
    out = {}
    for k, d in enumerate(dates):
        if k < warmup:
            continue
        prev = pd.concat([by[x] for x in dates[max(0, k - lookback):k]])
        out[d] = (float(np.quantile(prev["I"].abs(), pct)), float(np.median(prev["dP"].abs())))
    return out


def select(slots: pd.DataFrame, kind: str, q: float, med: float, mult: float, H: int) -> pd.DataFrame:
    """Trades for one session (one position at a time, next decision at or after the exit time t + H)."""
    rows, free = [], None
    for r in slots.sort_values("t").itertuples(index=False):
        if free is not None and r.t < free:
            continue
        s = np.sign(r.I)
        if s == 0 or abs(r.I) < q:
            continue
        if kind == "continuation":
            d = s
        elif kind == "absorption":
            d = -s if r.dP * s <= 0 else 0
        elif kind == "pressure":
            d = -s if r.dP * s >= mult * med else 0
        else:
            raise ValueError(kind)
        if d == 0:
            continue
        rows.append({**r._asdict(), "d": int(d)})
        free = r.t + pd.Timedelta(minutes=H)
    cols = list(slots.columns) + ["d"]
    return pd.DataFrame(rows, columns=cols)


def price(T: pd.DataFrame, cfg) -> pd.DataFrame:
    T = T.copy()
    tick = cfg["market"]["tick"]
    slip = param(cfg, "entry_slippage") * tick
    cost = param(cfg, "cost_rt_usd") / cfg["market"]["point_value"]
    T["E"] = T["entry_px"] + T["d"] * slip
    T["X"] = T["exit_px"] - T["d"] * slip
    T["gross_pts"] = T["d"] * (T["exit_px"] - T["entry_px"])
    T["pnl_pts"] = T["d"] * (T["X"] - T["E"]) - cost
    return T


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def pilot_days(cfg) -> list:
    cal = store.load_calendar(cfg)                       # in-sample only
    days = calm.stage3_days(cal, cfg)
    c = cal.set_index("date")
    out = []
    for d in sorted(days):
        if bool(c.loc[d].get("roll", False)) or bool(c.loc[d].get("half_day", False)):
            continue
        if covered(cfg, d):
            out.append(d)
    return out


def _et(day, hhmm):
    return pd.Timestamp(f"{day} {hhmm}", tz=ET).tz_convert("UTC")


def build_slots(cfg, days=None) -> dict:
    """(L, H) -> slot table over all pilot sessions."""
    days = pilot_days(cfg) if days is None else days
    pairs = sorted({(v["L"], v["H"]) for v in param(cfg, "s6_variants").values()})
    out = {p: [] for p in pairs}
    for day in days:
        gs, ge = _et(day, param(cfg, "s6_grid_start")), _et(day, param(cfg, "s6_grid_end"))
        for s, e in covered(cfg, day):
            tr = load_trades(cfg, day, s, e)
            for L, H in pairs:
                S = slot_table(tr, s, e, L, H, gs, ge)
                if not S.empty:
                    S.insert(0, "date", day)
                    out[(L, H)].append(S)
    return {p: (pd.concat(v, ignore_index=True).drop_duplicates(["date", "t"]) if v else pd.DataFrame())
            for p, v in out.items()}


def trades(cfg, slots: dict) -> pd.DataFrame:
    rows = []
    pct, lb, wu = param(cfg, "s6_flow_pct"), param(cfg, "s6_lookback_sessions"), param(cfg, "s6_warmup_sessions")
    mult = param(cfg, "s6_pressure_mult")
    for name, v in param(cfg, "s6_variants").items():
        S = slots.get((v["L"], v["H"]))
        if S is None or S.empty:
            continue
        th = thresholds(S, pct, lb, wu)
        for d, g in S.groupby("date"):
            if d not in th:
                continue
            T = select(g, v["kind"], th[d][0], th[d][1], mult, v["H"])
            if not T.empty:
                T.insert(0, "variant", name)
                T["q"], T["med_dP"] = th[d]
                rows.append(T)
    if not rows:
        return pd.DataFrame()
    return price(pd.concat(rows, ignore_index=True), cfg)


# ---------------------------------------------------------------------------
# Statistics and gates
# ---------------------------------------------------------------------------
def summarize(T: pd.DataFrame, cfg) -> dict:
    if T.empty:
        return {"n": 0}
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    net = stats.day_bootstrap_mean(T, "pnl_pts", draws, seed, lvl)
    gross = stats.day_bootstrap_mean(T, "gross_pts", draws, seed, lvl)
    k = cfg["gates"]["study6_tail_drop"]
    by_day = T.groupby("date")["pnl_pts"].sum().sort_values(ascending=False)
    rest = T[~T["date"].isin(by_day.index[:k])]
    p = T["pnl_pts"].to_numpy(float)
    wins, losses = p[p > 0], p[p <= 0]
    out = {
        "n": len(T), "sessions": int(T["date"].nunique()), "trades_per_session": len(T) / T["date"].nunique(),
        "gross_mean": gross["mean"], "gross_lo": gross["lo"], "gross_hi": gross["hi"],
        "mean": net["mean"], "ci_lo": net["lo"], "ci_hi": net["hi"],
        "mean_ex_best": float(rest["pnl_pts"].mean()) if len(rest) else np.nan,
        "usd_per_trade": net["mean"] * cfg["market"]["point_value"],
        "win_rate": len(wins) / len(p),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.inf,
        "max_dd_pts": stats.max_drawdown(p), "longest_losing_streak": stats.longest_losing_streak(p),
        "long_share": float((T["d"] > 0).mean()),
    }
    if "entry_ts" in T:
        yr = pd.to_datetime(T["date"]).dt.year
        out["by_year"] = {int(y): {"n": int(len(g)), "mean": float(g["pnl_pts"].mean())} for y, g in T.groupby(yr)}
        out["long_mean"] = float(T.loc[T["d"] > 0, "pnl_pts"].mean()) if (T["d"] > 0).any() else np.nan
        out["short_mean"] = float(T.loc[T["d"] < 0, "pnl_pts"].mean()) if (T["d"] < 0).any() else np.nan
    return out


def verdict(s: dict, cfg) -> dict:
    g = cfg["gates"]
    if s.get("n", 0) == 0:
        gates = {"1_n": False, "2_existence": False, "3_expectancy": False, "4_tail": False}
    else:
        gates = {"1_n": s["n"] >= g["study6_min_trades"],
                 "2_existence": bool(s["gross_lo"] > 0),
                 "3_expectancy": bool(s["mean"] >= g["study6_min_expectancy_pts"] and s["ci_lo"] > 0),
                 "4_tail": bool(s["mean_ex_best"] > 0)}
    return {"gates": gates, "verdict": "ADVANCE" if all(gates.values()) else "KILL"}


def descriptive(slots: dict, first_traded: dict) -> dict:
    """Pooled forward-return slope on I and decile means (pre-cost, traded sessions only)."""
    out = {}
    for (L, H), S in slots.items():
        if S.empty:
            continue
        S = S[S["date"] >= first_traded.get((L, H), S["date"].min())].copy()
        S["fwd"] = S["exit_px"] - S["entry_px"]
        if len(S) < 30 or S["date"].nunique() < 2:
            continue
        res = stats.ols_clustered(S, "fwd ~ I", cluster="date")
        S["dec"] = pd.qcut(S["I"].rank(method="first"), 10, labels=False)
        out[f"L{L}_H{H}"] = {
            "slots": len(S), "slope_pts_per_unit_I": float(res.params["I"]), "slope_t": float(res.tvalues["I"]),
            "corr": float(np.corrcoef(S["I"], S["fwd"])[0, 1]),
            "decile_fwd_mean": [float(x) for x in S.groupby("dec")["fwd"].mean()],
            "decile_I_mean": [float(x) for x in S.groupby("dec")["I"].mean()],
            "abs_I_p80": float(S["I"].abs().quantile(0.8)),
        }
    return out


def count(cfg, slots: dict) -> dict:
    """Signal-only counts: no outcome column is read."""
    T = trades(cfg, {k: v.drop(columns=["exit_px"], errors="ignore").assign(exit_px=np.nan) for k, v in slots.items()})
    return {"slots": {f"L{L}_H{H}": {"n": len(S), "sessions": int(S["date"].nunique()) if len(S) else 0}
                      for (L, H), S in slots.items()},
            "trades": ({} if T.empty else T.groupby("variant").size().to_dict())}


def report(cfg, T: pd.DataFrame, slots: dict | None = None) -> dict:
    out = {"sessions_with_ticks": int(T["date"].nunique()) if len(T) else 0, "variants": {}}
    for name, v in param(cfg, "s6_variants").items():
        s = summarize(T[T["variant"] == name] if len(T) else T, cfg)
        out["variants"][name] = {**v, **s, **verdict(s, cfg)}
    if slots is not None:
        wu = param(cfg, "s6_warmup_sessions")
        first = {}
        for k, S in slots.items():
            ds = sorted(S["date"].unique()) if len(S) else []
            if len(ds) > wu:
                first[k] = ds[wu]
        out["descriptive"] = descriptive(slots, first)
    out["note"] = "Pilot on reused Stage 3 sessions: ADVANCE means a confirmation run on fresh sessions, never Go."
    return out


def run(cfg=None, save: bool = True):
    cfg = cfg or load_config()
    days = pilot_days(cfg)
    log.info("study 6: %d pilot sessions with ticks", len(days))
    slots = build_slots(cfg, days)
    T = trades(cfg, slots)
    if len(T):
        assert (T["date"] < calm.holdout_start(cfg)).all()
        assert not T.duplicated(["variant", "date", "t"]).any()
        assert (T["entry_ts"] >= T["t"]).all()
    if save and len(T):
        store.save_derived(T, "flow_trades", cfg)
    return T, slots


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", action="store_true", help="eligible slots and trade counts only (no outcomes)")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    from src.analysis import to_json
    if a.count:
        print(to_json(count(cfg, build_slots(cfg))))
        return
    if a.report_only:
        print(to_json(report(cfg, store.load_derived("flow_trades", cfg))))
        return
    T, slots = run(cfg)
    print(to_json(report(cfg, T, slots)))


if __name__ == "__main__":
    main()
