"""Study 19: harvesting the index volatility premium (Mack & Sinclair ch. 10/14/15) in SPXW weeklies (STUDY19.md).

Entry at the 17:00 ET end-of-day report of the entry day (Friday for the weeklies, Monday/Wednesday for the one-day
trades), held to cash settlement at the SPX close of the expiry day; sell at the bid, buy at the ask, cost
opt_cost_per_leg_usd per leg, no adjustment. Forward and discount from put-call parity (gex.fit_forward), deltas
are forward Black-76 deltas from mid implied vols. P&L in SPX points, in EM units (EM = ATM straddle mid at entry),
and in dollars per XSP contract (1/10 SPX).

  V1  weekly 20-delta short strangle, every entry
  V2  V1, skipped while VIX >= VIX3M at entry
  V3  weekly short ATM straddle, skipped while VIX >= VIX3M
  V4  one-day short straddle: Monday entry, Tuesday expiry
  reported: iron condor (2-delta wings), Thursday one-day, skip-after-streak, VIX deciles, worst weeks, stress

  python -m src.study19 --run          # in-sample (sample A), Stage A against the gate
  python -m src.study19 --holdout      # once, only on "run the holdout" (GAMMA_EDGE_RUN_HOLDOUT=1), passes only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math

import numpy as np
import pandas as pd
from scipy.special import ndtr

from src import gex, store
from src.config import data_path, load_config, param

log = logging.getLogger("study19")


# ---- pure pieces (tested) ----------------------------------------------------------------------------------------
def pick_weekly(expirations, entry: dt.date, min_days: int, max_days: int):
    """The expiry min..max calendar days out that is closest to 7 days (next Friday, or Thursday on a holiday)."""
    c = [e for e in expirations if min_days <= (e - entry).days <= max_days]
    return min(c, key=lambda e: (abs((e - entry).days - 7), (e - entry).days)) if c else None


def delta_strike(strikes, vols, F: float, T: float, target: float, right: str) -> float:
    """OTM strike whose forward delta (N(d1) for calls, N(d1) - 1 for puts) is nearest +/- target."""
    K, v = np.asarray(strikes, float), np.asarray(vols, float)
    ok = np.isfinite(v) & (v > 0) & ((K >= F) if right == "C" else (K <= F))
    if not ok.any():
        return np.nan
    K, v = K[ok], v[ok]
    sd = v * math.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    delta = ndtr(d1) if right == "C" else ndtr(d1) - 1.0
    return float(K[np.argmin(np.abs(np.abs(delta) - target))])


def straddle_pnl(w, K, settle, fee):
    return float(w.loc[K, "bid_C"] + w.loc[K, "bid_P"]) - abs(settle - K) - 2 * fee


def strangle_pnl(w, Kc, Kp, settle, fee):
    credit = float(w.loc[Kc, "bid_C"] + w.loc[Kp, "bid_P"])
    return credit - max(settle - Kc, 0.0) - max(Kp - settle, 0.0) - 2 * fee


def condor_pnl(w, Kc, Kp, Kc2, Kp2, settle, fee):
    credit = float(w.loc[Kc, "bid_C"] + w.loc[Kp, "bid_P"] - w.loc[Kc2, "ask_C"] - w.loc[Kp2, "ask_P"])
    owed = max(settle - Kc, 0) - max(settle - Kc2, 0) + max(Kp - settle, 0) - max(Kp2 - settle, 0)
    return credit - owed - 4 * fee


def inverted(vix, vix3m) -> bool:
    return bool(np.isfinite(vix) and np.isfinite(vix3m) and vix >= vix3m)


def after_streak(won: pd.Series, n: int) -> pd.Series:
    """True where each of the previous n trades won (known before this entry)."""
    w = won.astype(float)
    return (w.shift(1).rolling(n, min_periods=n).sum() == n).fillna(False)


def series_stats(x: pd.Series, draws: int, seed: int, level: float) -> dict:
    v = np.asarray(x, float)
    v = v[np.isfinite(v)]
    n = len(v)
    if n < 2:
        return {"n": n}
    boot = v[np.random.default_rng(seed).integers(0, n, (draws, n))].mean(1)
    a = (1 - level) / 2
    h = n // 2
    sd = v.std(ddof=1)
    return {"n": n, "mean": float(v.mean()), "lo": float(np.quantile(boot, a)), "hi": float(np.quantile(boot, 1 - a)),
            "t": float(v.mean() / sd * math.sqrt(n)) if sd > 0 else None, "win_rate": float((v > 0).mean()),
            "first_half": float(v[:h].mean()), "second_half": float(v[h:].mean()),
            "worst": float(v.min()), "best": float(v.max()),
            "worst_twice_mean": float((v.sum() + v.min()) / (n + 1))}


def passes(st: dict, min_trades: int) -> bool:
    ok = lambda k: st.get(k) is not None and np.isfinite(st[k]) and st[k] > 0
    return bool(st.get("n", 0) >= min_trades and ok("mean") and ok("lo") and ok("first_half")
                and ok("second_half") and ok("stress_mean"))


# ---- chains and trades ----------------------------------------------------------------------------------------------
def _wide(q: pd.DataFrame, max_rel_spread: float) -> pd.DataFrame:
    q = q.assign(valid=gex.valid_quote_mask(q, max_rel_spread), mid=(q["bid"] + q["ask"]) / 2)
    w = q.pivot_table(index="strike", columns="right", values=["bid", "ask", "mid", "valid"], aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    for r in ("C", "P"):
        for c in ("bid", "ask", "mid"):
            w[f"{c}_{r}"] = w.get(f"{c}_{r}", np.nan)
        w[f"valid_{r}"] = w[f"valid_{r}"].fillna(False).astype(bool) if f"valid_{r}" in w else False
    return w.sort_index()


def trade_one(q: pd.DataFrame, entry: dt.date, expiry: dt.date, settle: float, ref: float, cfg) -> dict | None:
    """All structures for one entry/expiry pair, or None when the chain cannot price them."""
    w = _wide(q, param(cfg, "max_rel_spread"))
    band = param(cfg, "parity_band") * ref
    par = w[w["valid_C"] & w["valid_P"] & (np.abs(w.index - ref) <= band)]
    F, D = gex.fit_forward(par.index.values, par["mid_C"].values, par["mid_P"].values)
    if not np.isfinite(F):
        return None
    t0 = pd.Timestamp(dt.datetime.combine(entry, dt.time(17, 0)), tz="America/New_York").tz_convert("UTC")
    T = gex.year_frac(t0, gex.expiry_ts("SPXW", expiry, cfg), cfg)
    if T <= 0:
        return None
    both = w[w["valid_C"] & w["valid_P"]]
    if both.empty:
        return None
    K = float(both.index[np.argmin(np.abs(both.index.values - F))])
    em = float(both.loc[K, "mid_C"] + both.loc[K, "mid_P"])
    vols = {}
    for right in ("C", "P"):
        side = w[w[f"valid_{right}"] & ((w.index >= F) if right == "C" else (w.index <= F))]
        vols[right] = (side.index.values, np.array([gex.implied_vol(float(m), F, float(k), T, D, right == "C")
                                                    for k, m in zip(side.index.values, side[f"mid_{right}"].values)]))
    d, wd = param(cfg, "s19_delta"), param(cfg, "s19_wing_delta")
    Kc, Kp = delta_strike(*vols["C"], F, T, d, "C"), delta_strike(*vols["P"], F, T, d, "P")
    Kc2, Kp2 = delta_strike(*vols["C"], F, T, wd, "C"), delta_strike(*vols["P"], F, T, wd, "P")
    fee = param(cfg, "opt_cost_per_leg_usd") / cfg["market"]["option_multiplier"]
    if not (np.isfinite(em) and em > 0 and np.isfinite(Kc) and np.isfinite(Kp)):
        return None
    row = {"entry": entry, "expiry": expiry, "F": F, "K": K, "em": em, "settle": settle, "T": T,
           "Kc": Kc, "Kp": Kp, "straddle": straddle_pnl(w, K, settle, fee), "strangle": strangle_pnl(w, Kc, Kp, settle, fee),
           "straddle_credit": float(w.loc[K, "bid_C"] + w.loc[K, "bid_P"]),
           "strangle_credit": float(w.loc[Kc, "bid_C"] + w.loc[Kp, "bid_P"]), "fee": fee}
    if np.isfinite(Kc2) and np.isfinite(Kp2) and Kc2 > Kc and Kp2 < Kp:
        row.update(Kc2=Kc2, Kp2=Kp2, condor=condor_pnl(w, Kc, Kp, Kc2, Kp2, settle, fee))
    if row["straddle_credit"] <= 0 or row["strangle_credit"] <= 0:
        return None
    return row


def build_trades(cfg, include_holdout: bool = False) -> pd.DataFrame:
    cal = store.load_calendar(cfg, include_holdout=include_holdout)
    daily = store.load_daily(cfg, include_holdout=include_holdout).set_index("date")
    if "vix3m_close" not in daily:
        raise SystemExit("VIX3M missing: run python -m src.ingest_daily --force (adds FRED VXVCLS)")
    start = pd.Timestamp(param(cfg, "s19_start")).date()
    days = sorted(d for d in pd.to_datetime(cal["date"]).dt.date if d >= start)
    sessions = set(days)
    nxt = {a: b for a, b in zip(days[:-1], days[1:])}
    rows, skipped = [], {}
    wk = param(cfg, "s19_entry_weekday")
    for d in days:
        kinds = []
        if d.weekday() == wk or (d.weekday() == wk - 1 and (d + dt.timedelta(days=1)) not in sessions
                                 and (d + dt.timedelta(days=1)).weekday() == wk):
            kinds.append("weekly")
        if d in nxt and (nxt[d] - d).days == 1 and nxt[d].weekday() in (1, 3):
            kinds.append("oneday_tue" if nxt[d].weekday() == 1 else "oneday_thu")
        if not kinds:
            continue
        q = store.load_options_eod(cfg, d)
        if q.empty:
            skipped["no_quotes"] = skipped.get("no_quotes", 0) + 1
            continue
        q = q[q["symbol"] == "SPXW"].copy()
        q["expiration"] = pd.to_datetime(q["expiration"]).dt.date
        exps = sorted(q["expiration"].unique())
        ref = daily["spx_close"].get(d, np.nan)
        for kind in kinds:
            e = pick_weekly(exps, d, param(cfg, "s19_min_days"), param(cfg, "s19_max_days")) if kind == "weekly" else nxt[d]
            if e is None or e not in sessions or e not in daily.index:
                skipped[f"{kind}_no_expiry_or_settle"] = skipped.get(f"{kind}_no_expiry_or_settle", 0) + 1
                continue
            r = trade_one(q[q["expiration"] == e], d, e, float(daily.loc[e, "spx_close"]), ref, cfg)
            if r is None:
                skipped[f"{kind}_unpriced"] = skipped.get(f"{kind}_unpriced", 0) + 1
                continue
            r.update(kind=kind, vix=daily["vix_close"].get(d, np.nan), vix3m=daily["vix3m_close"].get(d, np.nan))
            r["inverted"] = inverted(r["vix"], r["vix3m"])
            rows.append(r)
    T = pd.DataFrame(rows)
    T.attrs["skipped"] = skipped
    return T


# ---- report -----------------------------------------------------------------------------------------------------------
def _variant_series(T, name, cfg):
    W = T[T["kind"] == "weekly"].sort_values("entry")
    if name == "V1":
        return W, "strangle"
    if name == "V2":
        return W[~W["inverted"]], "strangle"
    if name == "V3":
        return W[~W["inverted"]], "straddle"
    if name == "V4":
        return T[T["kind"] == "oneday_tue"].sort_values("entry"), "straddle"
    raise KeyError(name)


def evaluate(T: pd.DataFrame, cfg, seed_offset: int = 0) -> dict:
    g, draws, lvl = cfg["gates"], param(cfg, "bootstrap_draws"), param(cfg, "ci_level")
    seed, stress = param(cfg, "s19_seed") + seed_offset, param(cfg, "s19_cost_stress")
    mult = cfg["market"]["option_multiplier"] / 10                        # $ per XSP contract per SPX point
    out = {}
    for v in ("V1", "V2", "V3", "V4"):
        X, col = _variant_series(T, v, cfg)
        legs = 2
        em_pnl = X[col] / X["em"]
        st = series_stats(em_pnl, draws, seed, lvl)
        st["stress_mean"] = float(((X[col] - (stress - 1) * legs * X["fee"]) / X["em"]).mean()) if len(X) else np.nan
        st["pass"] = passes(st, g["study19_min_trades"])
        usd = X[col] * mult
        cum = usd.cumsum()
        st["xsp_usd"] = {"mean_per_trade": float(usd.mean()) if len(X) else None,
                         "total": float(usd.sum()), "max_drawdown": float((cum.cummax() - cum).max()) if len(X) else None,
                         "worst_5": [round(float(u), 1) for u in usd.nsmallest(5)]}
        st["mean_points"] = float(X[col].mean()) if len(X) else None
        st["skipped_inverted"] = int(T[(T["kind"] == ("oneday_tue" if v == "V4" else "weekly")) & T["inverted"]].shape[0]) \
            if v in ("V2", "V3") else 0
        out[v] = st
    W = T[T["kind"] == "weekly"].sort_values("entry")
    rep = {}
    if "condor" in W:
        Wc = W.dropna(subset=["condor"])
        rep["iron_condor_em"] = series_stats(Wc["condor"] / Wc["em"], draws, seed, lvl)
    Th = T[T["kind"] == "oneday_thu"]
    rep["thursday_oneday_straddle_em"] = series_stats(Th["straddle"] / Th["em"], draws, seed, lvl)
    skip = after_streak(W["strangle"] > 0, param(cfg, "s19_skip_streak")).to_numpy()
    rep["v1_after_streak_em"] = series_stats((W["strangle"] / W["em"])[skip], draws, seed, lvl)
    rep["v1_not_after_streak_em"] = series_stats((W["strangle"] / W["em"])[~skip], draws, seed, lvl)
    rep["v1_inverted_weeks_em"] = series_stats((W["strangle"] / W["em"])[W["inverted"].to_numpy()], draws, seed, lvl)
    if len(W) >= 10:
        dec = pd.qcut(W["vix"], 5, labels=False, duplicates="drop")
        rep["v1_by_vix_quintile_em"] = {int(k): round(float(x.mean()), 4)
                                        for k, x in (W["strangle"] / W["em"]).groupby(dec.to_numpy())}
        rep["v1_worst_weeks"] = [{"entry": str(r.entry), "pnl_pts": round(float(r.strangle), 2), "vix": r.vix,
                                  "move_pct": round(float(r.settle / r.F - 1) * 100, 2)}
                                 for r in W.nsmallest(5, "strangle").itertuples()]
        moves = (W["settle"] / W["F"] - 1).to_numpy()
        worst_dn, worst_up = float(moves.min()), float(moves.max())
        stress_loss = []
        for r in W.itertuples():
            for m in (worst_dn, worst_up):
                s = r.F * (1 + m)
                stress_loss.append(r.strangle_credit - max(s - r.Kc, 0) - max(r.Kp - s, 0) - 2 * r.fee)
        rep["v1_stress"] = {"worst_weekly_move_down_pct": round(worst_dn * 100, 2), "up_pct": round(worst_up * 100, 2),
                            "worst_loss_xsp_usd": round(float(min(stress_loss)) * mult, 1),
                            "median_loss_xsp_usd": round(float(np.median(stress_loss)) * mult, 1)}
    return {"variants": out, "reported": rep}


def run(cfg) -> dict:
    T = build_trades(cfg)
    d = data_path(cfg, "derived", "study19")
    d.mkdir(parents=True, exist_ok=True)
    T.to_parquet(d / "study19_trades.parquet")
    r = evaluate(T, cfg)
    res = {"study": 19, "stage": "A (in-sample, sample A)", "first_entry": str(T["entry"].min()) if len(T) else None,
           "last_expiry": str(T["expiry"].max()) if len(T) else None,
           "trades_by_kind": T["kind"].value_counts().to_dict() if len(T) else {}, "skipped": T.attrs.get("skipped", {}),
           **r}
    (d / "study19_stage_a.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def run_holdout(cfg) -> dict:
    from src.calendar import holdout_start, holdout_unsealed
    if not holdout_unsealed():
        raise SystemExit("holdout needs GAMMA_EDGE_RUN_HOLDOUT=1 (only on 'run the holdout')")
    d = data_path(cfg, "derived", "study19")
    marker = d / "study19_holdout_done.json"
    if marker.exists():
        raise SystemExit("Study 19 holdout already run once")
    A = json.loads((d / "study19_stage_a.json").read_text())
    passing = [v for v, s in A["variants"].items() if s.get("pass")]
    if not passing:
        raise SystemExit("no variant passed Stage A: nothing to take to the holdout")
    T = build_trades(cfg, include_holdout=True)
    hs = holdout_start(cfg)
    H = T[pd.to_datetime(T["entry"]).dt.date >= hs]
    r = evaluate(H, cfg, seed_offset=1)
    for v in list(r["variants"]):
        s = r["variants"][v]
        if v not in passing:
            r["variants"].pop(v)
            continue
        s["pass"] = passes(s, cfg["gates"]["study19_holdout_min_trades"])
    marker.write_text(json.dumps({"ran": pd.Timestamp.now(tz="UTC").isoformat()}))
    res = {"study": 19, "stage": "holdout", "first_entry": str(H["entry"].min()), "last_expiry": str(H["expiry"].max()), **r}
    (d / "study19_holdout.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", action="store_true")
    g.add_argument("--holdout", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    r = run(cfg) if a.run else run_holdout(cfg)
    from src.analysis import to_json
    print(to_json(r))


if __name__ == "__main__":
    main()
