"""Study 9 (RUNLOG 2026-10-08, approved): can a patient, passive ES trader earn the spread?

Part A, realized spread to passive fills (ES tick trades on the on-disk Stage 3 sessions; report-only).
  ES trades one tick wide almost all session, so the quote is inferred from the tape: a buy aggressor prints at
  the ask, so mid = p - tick/2; a sell aggressor prints at the bid, so mid = p + tick/2 (checked: spread_check).
  For trade i with aggressor side s_i at price p_i, the passive counterparty's result after D seconds, in ticks:
      RS_i(D) = -s_i * (mid(t_i + D) - p_i) / tick,   mid(t) from the last print at or before t,
  dropped when t_i + D runs past the downloaded span. Contract-weighted means, CI by resampling sessions.
  Fill classes: "clearing" = the last print of a same-price, same-side run after which the next print is beyond
  that price in the aggressor's direction (the level was eaten through) -- the fill a back-of-queue resting order
  gets; "other" = every other passive fill (mostly front-of-queue).
  Net per side = RS - fee_rt / 2 / (point_value x tick).

Part B, ES level reversion with a resting order at the level (all in-sample Stage 2 touches; report-only).
  Order rests at L from the touch bar t for s9_fill_window bars, long at support (d = +1), short at resistance.
  perfect:      filled at L when a bar trades at L (low <= L, or high >= L); exit at the close of bar fill + H
  conservative: filled at L only when a bar trades through L by a tick (SPEC rule 5); exit one tick worse
  Holds past the last RTH bar are closed at that bar's close (flagged). gross = d (X - E) in points; fees by scenario.
  The perfect scenario is an upper bound (it ignores queue position and adverse selection) and is never a basis
  for a Go on its own.

  python -m src.study9 --realized-spread       # Part A
  python -m src.study9 --level-reversion       # Part B
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import store
from src.config import load_config, param
from src.ingest_futures import covered, load_trades
from src.levels import session_bars

log = logging.getLogger("study9")
ET = "America/New_York"


# ---------------------------------------------------------------------------
# Part A
# ---------------------------------------------------------------------------
def inferred_mid(price: np.ndarray, side: np.ndarray, tick: float) -> np.ndarray:
    return price - side * tick / 2.0


def realized_spread(tr: pd.DataFrame, horizons, span_end, tick: float) -> pd.DataFrame:
    tr = tr.sort_values(["ts_event_utc", "sequence"], kind="stable").reset_index(drop=True)
    ts = tr["ts_event_utc"].to_numpy("datetime64[ns]").astype("int64")
    p = tr["price"].to_numpy(float)
    s = tr["side"].to_numpy(float)
    mid = inferred_mid(p, s, tick)
    end = pd.Timestamp(span_end).value
    out = pd.DataFrame(index=tr.index)
    for h in horizons:
        tgt = ts + int(h * 1e9)
        j = np.searchsorted(ts, tgt, "right") - 1
        rs = -s * (mid[j] - p) / tick
        rs[tgt > end] = np.nan
        out[f"rs_{h}"] = rs
    return out


def clearing_flags(price: np.ndarray, side: np.ndarray) -> np.ndarray:
    n = len(price)
    flag = np.zeros(n, bool)
    if n < 2:
        return flag
    new_run = np.r_[True, (price[1:] != price[:-1]) | (side[1:] != side[:-1])]
    run_id = np.cumsum(new_run) - 1
    last = np.r_[run_id[1:] != run_id[:-1], True]                 # last trade of each run
    nxt = np.r_[price[1:], np.nan]
    moved = np.sign(np.nan_to_num(nxt - price, nan=0.0))
    flag = last & (moved == side) & (moved != 0)
    flag[-1] = False
    return flag


def spread_check(price: np.ndarray, side: np.ndarray, tick: float) -> dict:
    opp = side[1:] != side[:-1]
    buy_minus_sell = np.where(side[1:] == 1, price[1:] - price[:-1], price[:-1] - price[1:])[opp]
    n = int(opp.sum())
    return {"opposite_pairs": n,
            "one_tick_share": float(np.isclose(buy_minus_sell, tick).mean()) if n else np.nan,
            "zero_share": float(np.isclose(buy_minus_sell, 0.0).mean()) if n else np.nan}


def tod_label(ts_utc: pd.Series, edges: list[str]) -> np.ndarray:
    m = calm.minutes_of_day_et(pd.Series(ts_utc)).to_numpy()
    e = np.array([calm.hhmm_to_min(x) for x in edges])
    k = np.searchsorted(e, m, "right") - 1
    ok = (k >= 0) & (k < len(e) - 1)
    out = np.array([f"b{i}" for i in range(len(e) - 1)] + [None], dtype=object)
    return np.where(ok, out[np.clip(k, 0, len(e) - 1)], None)


def size_label(size: pd.Series, edges: list[int]) -> np.ndarray:
    names = []
    for i in range(len(edges)):
        if i == len(edges) - 1:
            names.append(f"{edges[i]}+")
        elif edges[i + 1] - edges[i] == 1:
            names.append(str(edges[i]))
        else:
            names.append(f"{edges[i]}-{edges[i + 1] - 1}")
    k = np.clip(np.searchsorted(np.array(edges), np.asarray(size, float), "right") - 1, 0, len(edges) - 1)
    return np.array(names, dtype=object)[k]


def boot_ratio(s: np.ndarray, c: np.ndarray, draws: int, seed: int, level: float) -> dict:
    """sum(s)/sum(c) with an interval from resampling whole sessions."""
    s, c = np.asarray(s, float), np.asarray(c, float)
    if c.sum() <= 0:
        return {"mean": np.nan, "lo": np.nan, "hi": np.nan, "sessions": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(draws, len(s)))
    num, den = s[idx].sum(1), c[idx].sum(1)
    b = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    a = (1 - level) / 2
    return {"mean": float(s.sum() / c.sum()), "lo": float(np.nanquantile(b, a)), "hi": float(np.nanquantile(b, 1 - a)),
            "sessions": int((c > 0).sum())}


def rs_session(tr: pd.DataFrame, span_end, day, cfg) -> pd.DataFrame:
    """Per-trade rows for one span: date, tod, size bucket, class, size and RS at each horizon."""
    tick = cfg["market"]["tick"]
    tr = tr.sort_values(["ts_event_utc", "sequence"], kind="stable").reset_index(drop=True)
    rs = realized_spread(tr, param(cfg, "s9_rs_horizons_sec"), span_end, tick)
    out = pd.DataFrame({"date": day, "size": tr["size"].to_numpy(float),
                        "tod": tod_label(tr["ts_event_utc"], param(cfg, "s9_tod_edges")),
                        "size_b": size_label(tr["size"], param(cfg, "s9_size_edges")),
                        "cls": np.where(clearing_flags(tr["price"].to_numpy(float), tr["side"].to_numpy(float)),
                                        "clearing", "other")})
    out = pd.concat([out, rs], axis=1)
    return out[out["tod"].notna()]


def run_rs(cfg=None, save: bool = True) -> tuple[pd.DataFrame, dict]:
    """Aggregate per session x tod x size bucket x class: sums of size*RS and size, per horizon."""
    from src.study6 import pilot_days
    cfg = cfg or load_config()
    hs = param(cfg, "s9_rs_horizons_sec")
    aggs, chk = [], {"opposite_pairs": 0, "one_tick": 0.0, "zero": 0.0}
    for day in pilot_days(cfg):
        for s, e in covered(cfg, day):
            tr = load_trades(cfg, day, s, e)
            if tr is None or len(tr) < 2:
                continue
            sc = spread_check(tr["price"].to_numpy(float), tr["side"].to_numpy(float), cfg["market"]["tick"])
            chk["opposite_pairs"] += sc["opposite_pairs"]
            chk["one_tick"] += sc["one_tick_share"] * sc["opposite_pairs"]
            chk["zero"] += sc["zero_share"] * sc["opposite_pairs"]
            R = rs_session(tr, e, day, cfg)
            for h in hs:
                ok = R[f"rs_{h}"].notna()
                g = R[ok].assign(w=R.loc[ok, "size"] * R.loc[ok, f"rs_{h}"]) \
                    .groupby(["date", "tod", "size_b", "cls"], as_index=False).agg(s=("w", "sum"), c=("size", "sum"))
                g["h"] = h
                aggs.append(g)
    A = pd.concat(aggs, ignore_index=True) if aggs else pd.DataFrame()
    n = max(chk["opposite_pairs"], 1)
    check = {"opposite_pairs": chk["opposite_pairs"], "one_tick_share": chk["one_tick"] / n,
             "zero_share": chk["zero"] / n}
    if save and len(A):
        store.save_derived(A, "realized_spread_agg", cfg)
    return A, check


def fee_points(fee_rt_usd: float, point_value: float) -> float:
    return fee_rt_usd / point_value


def report_rs(cfg, A: pd.DataFrame, check: dict | None = None) -> dict:
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    tick_usd = cfg["market"]["tick"] * cfg["market"]["point_value"]
    adv_side = cfg["gates"]["study9_advance_fee_usd"] / 2 / tick_usd          # ticks per side
    fee_side = {str(f): f / 2 / tick_usd for f in param(cfg, "s9_fee_scenarios_usd")}
    days = sorted(A["date"].unique())

    def cell(g):
        per = g.groupby("date")[["s", "c"]].sum().reindex(days, fill_value=0)
        r = boot_ratio(per["s"].to_numpy(), per["c"].to_numpy(), draws, seed, lvl)
        r["contracts"] = int(per["c"].sum())
        r["net_per_side"] = {k: r["mean"] - v for k, v in fee_side.items()}
        return r

    out = {"spread_check": check, "fee_ticks_per_side": fee_side, "cells": {}}
    for h in sorted(A["h"].unique()):
        Ah = A[A["h"] == h]
        hk = f"{h}s"
        out["cells"][hk] = {}
        for cls in ["all", "clearing", "other"]:
            Ac = Ah if cls == "all" else Ah[Ah["cls"] == cls]
            out["cells"][hk][cls] = {"all_day": cell(Ac),
                                     "by_tod": {t: cell(g) for t, g in Ac.groupby("tod")},
                                     "by_size": {b: cell(g) for b, g in Ac.groupby("size_b")}}
    # advance rule: clearing fills, some time-of-day bucket, 60 s CI lower bound above the $0-commission fee,
    # and the 30 s mean above it too
    hs = sorted(A["h"].unique())
    hl, hm = f"{hs[-1]}s", f"{hs[-2]}s" if len(hs) > 1 else f"{hs[-1]}s"
    passing = [t for t, c in out["cells"][hl]["clearing"]["by_tod"].items()
               if c["lo"] - adv_side > 0 and out["cells"][hm]["clearing"]["by_tod"].get(t, {}).get("mean", -1) - adv_side > 0]
    out["advance_rule"] = (f"clearing fills: {hl} CI lower bound and {hm} mean above the per-side fee "
                           f"({adv_side:.3f} tick at ${cfg['gates']['study9_advance_fee_usd']} a round trip) in some time bucket")
    out["advance_buckets"] = passing
    out["verdict"] = "ADVANCE" if passing else "KILL"
    out["tod_buckets"] = dict(zip([f"b{i}" for i in range(len(param(cfg, "s9_tod_edges")) - 1)],
                                  [f"{a}-{b}" for a, b in zip(param(cfg, "s9_tod_edges")[:-1], param(cfg, "s9_tod_edges")[1:])]))
    return out


# ---------------------------------------------------------------------------
# Part B
# ---------------------------------------------------------------------------
def resting_fill(bars: pd.DataFrame, t: int, L: float, d: int, window: int, tick: float, through: bool):
    lo, hi = bars["low"].to_numpy(float), bars["high"].to_numpy(float)
    eps = 1e-9
    for j in range(t, min(t + window, len(bars))):
        if d == 1 and lo[j] <= L - (tick if through else 0.0) + eps:
            return j
        if d == -1 and hi[j] >= L + (tick if through else 0.0) - eps:
            return j
    return None


def reversion_trade(bars: pd.DataFrame, t: int, L: float, d: int, horizons, cfg, rth_close: str | None = None) -> dict:
    tick = cfg["market"]["tick"]
    w = param(cfg, "s9_fill_window")
    rth_close = rth_close or cfg["market"]["rth_close"]
    mod = calm.minutes_of_day_et(bars["ts_open_utc"]).to_numpy()
    in_rth = np.where(mod < calm.hhmm_to_min(rth_close))[0]
    last = int(in_rth.max()) if len(in_rth) else len(bars) - 1
    cl = bars["close"].to_numpy(float)
    inst = bars["instrument_id"].to_numpy()
    out = {}
    for name, through in (("perfect", False), ("cons", True)):
        f = resting_fill(bars, t, L, d, w, tick, through)
        out[f"{name}_fill_bar"] = np.nan if f is None else f - t      # bars from the touch to the fill
        for h in horizons:
            if f is None or f > last:
                out[f"{name}_gross_{h}"] = np.nan
                continue
            x = min(f + h, last)
            if inst[x] != inst[f]:
                out[f"{name}_gross_{h}"] = np.nan
                continue
            exit_px = cl[x] - (d * tick if through else 0.0)
            out[f"{name}_gross_{h}"] = d * (exit_px - L)
            if name == "perfect":
                out[f"truncated_{h}"] = f + h > last
    return out


def run_lr(cfg=None, save: bool = True) -> pd.DataFrame:
    cfg = cfg or load_config()
    hs = param(cfg, "s9_lr_horizons_min")
    touches = store.load_derived("touches", cfg)                     # in sample (sealed loader)
    cal = store.load_calendar(cfg).set_index("date")
    bars = store.load_bars(cfg)
    by_day = {d: x for d, x in bars.groupby("date")}
    rows = []
    for day, g in touches.groupby("date"):
        if day not in cal.index:
            continue
        sess = session_bars(by_day, day, cal.loc[day, "instrument_id"])
        for r in g.itertuples():
            rec = reversion_trade(sess, int(r.bar_idx), float(r.level_es), int(r.d), hs, cfg)
            rows.append({"date": day, "touch_id": r.touch_id, "group": r.group, "d": int(r.d),
                         "first": bool(r.first), **rec})
    T = pd.DataFrame(rows)
    if save and len(T):
        store.save_derived(T, "level_reversion_resting", cfg)
    return T


def report_lr(cfg, T: pd.DataFrame) -> dict:
    from src import stats
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    pv = cfg["market"]["point_value"]
    fees = param(cfg, "s9_fee_scenarios_usd")
    adv_fee = fee_points(cfg["gates"]["study9_advance_fee_usd"], pv)
    out = {"n_touches": int(len(T)), "groups": T["group"].value_counts().to_dict(), "by_horizon": {}}
    real = T["group"] != "placebo"
    passing = []
    for h in param(cfg, "s9_lr_horizons_min"):
        row = {}
        for sc in ("perfect", "cons"):
            col = f"{sc}_gross_{h}"
            sub = T.dropna(subset=[col])
            cell = {"fill_rate": float(T[col].notna().mean())}
            for name, mask in (("real", sub["group"] != "placebo"), ("placebo", sub["group"] == "placebo")):
                s = sub[mask]
                g = stats.day_bootstrap_mean(s.assign(v=s[col]), "v", draws, seed, lvl) if len(s) else {"n": 0}
                cell[name] = {"n": int(len(s)), "gross_pts": g.get("mean", np.nan),
                              "gross_ticks": g.get("mean", np.nan) / cfg["market"]["tick"] if len(s) else np.nan,
                              "net_pts": {str(f): g.get("mean", np.nan) - fee_points(f, pv) for f in fees},
                              "ci_net_at_advance_fee": [g.get("lo", np.nan) - adv_fee, g.get("hi", np.nan) - adv_fee],
                              "win_rate_gross": float((s[col] > 0).mean()) if len(s) else np.nan}
            rr, pp = sub[sub["group"] != "placebo"], sub[sub["group"] == "placebo"]
            if len(rr) and len(pp):
                cell["real_minus_placebo_pts"] = stats.day_bootstrap_diff(rr.assign(v=rr[col]), pp.assign(v=pp[col]),
                                                                          "v", draws, seed, lvl)
            cell["by_group"] = {k: float(v) for k, v in sub.groupby("group")[col].mean().items()}
            row[sc] = cell
        tr = T.get(f"truncated_{h}")
        row["truncated_share"] = float(tr.dropna().astype(bool).mean()) if tr is not None and tr.notna().any() else 0.0
        c = row["cons"]
        if c["real"]["n"] and c["real"]["ci_net_at_advance_fee"][0] > 0 and \
                c.get("real_minus_placebo_pts", {}).get("diff", -1) > 0:
            passing.append(h)
        out["by_horizon"][f"{h}m"] = row
    out["advance_rule"] = ("conservative fills (trade-through, exit one tick worse), real levels, net of the $0-commission "
                           "fee: 90% CI lower bound > 0 and real minus placebo > 0 at some horizon. The perfect scenario "
                           "is an upper bound only.")
    out["advance_horizons"] = passing
    out["verdict"] = "ADVANCE" if passing else "KILL"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--realized-spread", action="store_true")
    ap.add_argument("--level-reversion", action="store_true")
    ap.add_argument("--report-only", action="store_true", help="report from the saved tables")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from src.analysis import to_json
    cfg = load_config()
    if a.realized_spread:
        if a.report_only:
            A, chk = store.load_derived("realized_spread_agg", cfg), None
        else:
            A, chk = run_rs(cfg)
        print(to_json(report_rs(cfg, A, chk)))
    if a.level_reversion:
        T = store.load_derived("level_reversion_resting", cfg) if a.report_only else run_lr(cfg)
        print(to_json(report_lr(cfg, T)))
    if not (a.realized_spread or a.level_reversion):
        ap.print_help()


if __name__ == "__main__":
    main()
