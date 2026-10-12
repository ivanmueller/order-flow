"""Study 11b (Matteo 2026-10-09: "yes approve and build"): hedged market making in IWM options, the design
professionals use. EXPLORATION on the 10 seen sessions; report-only. Needs the 1-second IWM quotes
(python -m src.study11 --iwm-pull ...).

Study 11 found that a fixed exit at the opposite quote caps the wins and leaves the losses open. Here, per entry
(the Study 10 fill at the bid, s = +1, or the ask, s = -1, with its Study 11 delta):
  hedge   q = -s x round(delta x 100) IWM shares, sent s11b_hedge_latency_s after the fill and done at the first
          1-second IWM quote at or after that: buys pay the ask, sells get the bid; unwound the same way after the
          option exit; s11b_stock_fee_per_share_usd a share a side.
  exit    rests at the FAR side of the option quote (the ask for a long) and follows it: every observed quote whose
          far side differs re-pegs the order (a new order, at the back of the queue). Observed quotes = cbbo-1m
          snapshots and the pre-trade quote of each print in the contract. Fill rules:
            through (SPEC rule 5): a print one tick beyond the current exit price
            queue   through, or prints at the exit price since the order last moved exceed the size displayed
                    there when it moved (at the entry: the size displayed at entry)
            front   any print at or beyond the exit price (optimistic bound)
          Unfilled after s11b_max_hold_min (or at the close): crosses at the newest quote, one tick worse.
  value   option = s x (exit - entry) x 100; hedged = option + hedge; $ a contract, $0 option commission.
  orders  option orders per round trip = entry + first exit + re-pegs (the entry's own re-pegs are not modelled, so
          this is a lower bound), x fills a day, against s11b_priority_orders_per_day.

  python -m src.study11b --explore        # reads study11_entries + raw data + 1-second IWM; saves study11b_trips
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import stats, store, study10, study11
from src.config import load_config, param

log = logging.getLogger("study11b")

RULES = ("through", "queue", "front")
EPS = 1e-6


# ---------------------------------------------------------------------------
# Event streams
# ---------------------------------------------------------------------------
def session_events(tr: pd.DataFrame, q: pd.DataFrame) -> dict:
    """Per symbol, time-ordered events: quotes (snapshots) and prints (with their pre-trade quote). Quote events
    come first at equal times."""
    cols = ["ts", "symbol", "bid", "ask", "bid_sz", "ask_sz"]
    qq = q.reindex(columns=cols).assign(kind=0, price=np.nan, size=0.0)
    tt = tr.reindex(columns=cols + ["price", "size"]).assign(kind=1)
    ev = pd.concat([qq, tt], ignore_index=True)
    ev["t"] = study11._i8(ev["ts"])
    ev = ev.sort_values(["symbol", "t", "kind"], kind="stable")
    out = {}
    for k, g in ev.groupby("symbol", sort=False):
        out[k] = {c: g[c].to_numpy(float) for c in ("bid", "ask", "bid_sz", "ask_sz", "price", "size")}
        out[k]["t"] = g["t"].to_numpy()
        out[k]["isp"] = g["kind"].to_numpy() == 1
    return out


def _ffill(x: np.ndarray) -> np.ndarray:
    idx = np.where(np.isfinite(x), np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    y = x[idx]
    if len(x) and not np.isfinite(x[0]):
        y[: np.argmax(np.isfinite(x)) if np.isfinite(x).any() else len(x)] = np.nan
    return y


def _next(flag: np.ndarray) -> np.ndarray:
    """next[k] = first j >= k with flag[j], else len(flag)."""
    n = len(flag)
    idx = np.where(flag, np.arange(n), n)
    return np.minimum.accumulate(idx[::-1])[::-1] if n else idx


def side_stream(e: dict, s: int, tick: float) -> dict:
    """Arrays for a resting exit on side s (s = +1 sells at the ask, -1 buys at the bid), computed once a symbol."""
    valid = (e["bid"] > 0) & (e["ask"] > e["bid"] + EPS)
    far = _ffill(np.where(valid, e["ask"] if s == 1 else e["bid"], np.nan))
    farsz = _ffill(np.where(valid, e["ask_sz"] if s == 1 else e["bid_sz"], np.nan))
    near = _ffill(np.where(valid, e["bid"] if s == 1 else e["ask"], np.nan))
    n = len(far)
    chg = np.ones(n, bool)
    if n > 1:
        chg[1:] = ~(np.abs(far[1:] - far[:-1]) < EPS)
    seg = np.cumsum(chg) - 1
    d = s * (e["price"] - far)
    isp = e["isp"]
    atx = isp & (np.abs(d) < EPS)
    vol = np.where(atx, e["size"], 0.0)
    cs = np.cumsum(vol)
    starts = np.flatnonzero(chg)
    segbase = np.where(starts > 0, cs[np.maximum(starts - 1, 0)], 0.0)
    segahead = farsz[starts]
    segend = np.append(starts[1:] - 1, n - 1)
    cum_seg = cs - segbase[seg]
    q_ok = atx & np.isfinite(segahead[seg]) & (cum_seg > segahead[seg] + EPS)
    return {"t": e["t"], "far": far, "near": near, "farsz": farsz, "seg": seg, "cs": cs, "atx": atx,
            "segend": segend, "next_thr": _next(isp & (d >= tick - EPS)), "next_front": _next(isp & (d >= -EPS)),
            "next_q": _next(q_ok)}


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def simulate_entries(E: pd.DataFrame, events: dict, max_hold_min: float, tick: float, slip_ticks: int,
                     close: pd.Timestamp, mult: float) -> pd.DataFrame:
    n = len(E)
    out = {f"{c}_{r}": np.full(n, np.nan) for c in ("option", "hold", "repegs") for r in RULES}
    ex_t = {r: np.zeros(n, dtype="int64") for r in RULES}
    pas = {r: np.zeros(n, bool) for r in RULES}
    cache = {}
    t0s, tss = study11._i8(E["ts_last"]), study11._i8(E["ts"])
    H, cl = int(pd.Timedelta(minutes=max_hold_min).value), pd.Timestamp(close).value
    sym, s_arr, p = E["symbol"].to_numpy(), E["s"].to_numpy(int), E["price"].to_numpy(float)
    bid, ask = E["bid"].to_numpy(float), E["ask"].to_numpy(float)
    bsz = E["bid_sz"].to_numpy(float) if "bid_sz" in E else np.full(n, np.nan)
    asz = E["ask_sz"].to_numpy(float) if "ask_sz" in E else np.full(n, np.nan)
    for i in range(n):
        s = int(s_arr[i])
        X0, Q0 = (ask[i], asz[i]) if s == 1 else (bid[i], bsz[i])
        near0 = bid[i] if s == 1 else ask[i]
        t0, t1 = t0s[i], min(t0s[i] + H, cl)
        st = None
        if sym[i] in events:
            key = (sym[i], s)
            if key not in cache:
                cache[key] = side_stream(events[sym[i]], s, tick)
            st = cache[key]
        a = b = 0
        if st is not None:
            a, b = np.searchsorted(st["t"], t0, "right"), np.searchsorted(st["t"], t1, "right")
        idx = {r: -1 for r in RULES}
        moved0 = False
        if b > a:
            far, seg = st["far"], st["seg"]
            moved0 = not (abs(far[a] - X0) < EPS)
            th = st["next_thr"][a]
            idx["through"] = th if th < b else -1
            fr = st["next_front"][a]
            # first segment: our own queue position (entry size, or the size at a if we moved at a)
            e0 = min(st["segend"][seg[a]], b - 1)
            ahead = st["farsz"][a] if moved0 else Q0
            qi = -1
            if np.isfinite(ahead):
                base = st["cs"][a - 1] if a > 0 else 0.0
                cum = st["cs"][a:e0 + 1] - base
                k = np.flatnonzero(st["atx"][a:e0 + 1] & (cum > ahead + EPS))
                qi = a + k[0] if k.size else -1
            if qi < 0 and e0 + 1 < b:
                nq = st["next_q"][e0 + 1]
                qi = nq if nq < b else -1
            cand = [x for x in (qi, idx["through"]) if x >= 0]
            idx["queue"] = min(cand) if cand else -1
            cand = [x for x in (fr if fr < b else -1, idx["queue"]) if x >= 0]
            idx["front"] = min(cand) if cand else -1
        for r in RULES:
            k = idx[r]
            if k >= 0:
                px, te = st["far"][k], st["t"][k]
                rp = int(st["seg"][k] - st["seg"][a]) + int(moved0)
                pas[r][i] = True
            else:
                last_near = st["near"][b - 1] if b > a else near0
                if not np.isfinite(last_near):
                    last_near = near0
                px, te = max(last_near - s * slip_ticks * tick, 0.0), t1
                rp = int(st["seg"][b - 1] - st["seg"][a]) + int(moved0) if b > a else 0
            out[f"option_{r}"][i] = s * (px - p[i]) * mult
            out[f"hold_{r}"][i] = (te - tss[i]) / 6e10
            out[f"repegs_{r}"][i] = rp
            ex_t[r][i] = te
    R = pd.DataFrame(out, index=E.index)
    for r in RULES:
        R[f"passive_{r}"] = pas[r]
        R[f"exit_ts_{r}"] = pd.to_datetime(pd.Series(ex_t[r]), unit="ns", utc=True).set_axis(E.index)
    return R


def hedge_pnl(E: pd.DataFrame, delta, exit_ts, iwm: pd.DataFrame, latency_s: float, fee: float) -> np.ndarray:
    """$ of the IWM hedge: q = -s x round(delta x 100) shares, in at the first quote at or after fill + latency,
    out at the first at or after exit + latency (the last quote if none); buys at the ask, sells at the bid."""
    q = -E["s"].to_numpy(float) * np.round(np.nan_to_num(np.asarray(delta, float)) * 100)
    if iwm is None or iwm.empty:
        return np.full(len(E), np.nan)
    w = iwm.assign(t=study11._i8(iwm["ts"])).sort_values("t")
    t, b, a = w["t"].to_numpy(), w["bid"].to_numpy(float), w["ask"].to_numpy(float)
    lat = int(latency_s * 1e9)

    def at(times):
        k = np.minimum(np.searchsorted(t, times + lat, "left"), len(t) - 1)
        return b[k], a[k]
    b_in, a_in = at(study11._i8(E["ts_last"]))
    b_out, a_out = at(study11._i8(pd.Series(exit_ts).reset_index(drop=True)))
    p_in = np.where(q > 0, a_in, b_in)
    p_out = np.where(q > 0, b_out, a_out)
    return q * (p_out - p_in) - 2 * fee * np.abs(q)


def orders_per_round_trip(R: pd.DataFrame, rule: str) -> float:
    return float((2 + R[f"repegs_{rule}"]).mean())


def load_iwm_quotes(cfg, day) -> pd.DataFrame | None:
    p = study11.iwm_path(cfg, day)
    if not p.exists():
        return None
    q = study11._valid(study10.norm_quotes(store.read(p)))
    return q[["ts", "bid", "ask"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Pipeline and report
# ---------------------------------------------------------------------------
def session_trips(cfg, day, Ed: pd.DataFrame) -> pd.DataFrame:
    m = cfg["market"]
    iwm = load_iwm_quotes(cfg, day)
    if iwm is None or iwm.empty:
        raise FileNotFoundError(f"{day}: no 1-second IWM quotes; run python -m src.study11 --iwm-pull first")
    t0, close = calm.et_time(day, m["rth_open"]), calm.et_time(day, m["rth_close"])
    tr = study10.norm_trades(study10._read_pieces(cfg, "tcbbo", day))
    tr = tr[(tr["ts"] >= t0) & (tr["ts"] < close)].reset_index(drop=True)
    q = study10.norm_quotes(study10._read_pieces(cfg, "cbbo-1m", day))
    ev = session_events(tr, q)
    R = simulate_entries(Ed, ev, param(cfg, "s11b_max_hold_min"), param(cfg, "s11_tick_usd"),
                         param(cfg, "s11_fallback_slip_ticks"), close, m["option_multiplier"])
    keep = ["date", "ts", "ts_last", "symbol", "s", "price", "spread", "spread_b", "dte_b", "delta_b", "delta", "mid0", "rs_5",
            "cleared", "gross_through_5", "gross_front_5", "venue"]
    out = pd.concat([Ed[[c for c in keep if c in Ed]].reset_index(drop=True), R.reset_index(drop=True)], axis=1)
    lat, fee = param(cfg, "s11b_hedge_latency_s"), param(cfg, "s11b_stock_fee_per_share_usd")
    for r in RULES:
        out[f"hedge_{r}"] = hedge_pnl(out, out["delta"], out[f"exit_ts_{r}"], iwm, lat, fee)
        out[f"hedged_{r}"] = out[f"option_{r}"] + out[f"hedge_{r}"]
    return out


def trips_table(cfg, save: bool = True) -> pd.DataFrame:
    E = store.load_derived("study11_entries", cfg)
    parts = []
    for d, Ed in E.groupby("date"):
        if not calm.in_sample(d, cfg):
            raise RuntimeError(f"{d} is not in sample")
        log.info("session %s (%d entries)", d, len(Ed))
        parts.append(session_trips(cfg, d, Ed.sort_values("ts").reset_index(drop=True)))
    T = pd.concat(parts, ignore_index=True)
    if save:
        store.save_derived(T, "study11b_trips", cfg)
    return T


def _row(x: pd.DataFrame, cfg, ci: bool) -> dict:
    r = {"n": int(len(x)), "sessions": int(x["date"].nunique()), "half_spread_usd": float((x["spread"] * 50).mean())}
    for rule in RULES:
        h = f"hedged_{rule}"
        per = x.groupby("date")[h].mean()
        r[rule] = {"hedged": float(x[h].mean()), "unhedged": float(x[f"option_{rule}"].mean()),
                   "hedge_leg": float(x[f"hedge_{rule}"].mean()), "sd_hedged": float(x[h].std()),
                   "sd_unhedged": float(x[f"option_{rule}"].std()),
                   "exited_passively": float(x[f"passive_{rule}"].mean()),
                   "mean_hold_min": float(x[f"hold_{rule}"].mean()),
                   "option_orders_per_round_trip": orders_per_round_trip(x, rule),
                   "sessions_positive_hedged": int((per > 0).sum())}
        if ci:
            b = stats.day_bootstrap_mean(x.dropna(subset=[h]), h, param(cfg, "bootstrap_draws"),
                                         param(cfg, "bootstrap_seed"), param(cfg, "ci_level"))
            r[rule]["hedged_ci"] = [b["lo"], b["hi"]]
    return r


def explore(T: pd.DataFrame, cfg) -> dict:
    T = T.copy()
    T["side"] = np.where(T["s"] == 1, "buy at bid", "sell at ask")
    by_spread = {str(b): _row(x, cfg, True) for b, x in T.groupby("spread_b")}
    buckets = cfg["bankroll"]["s10_buckets"]
    A = T[T["spread_b"].isin(buckets)]
    lim = param(cfg, "s11b_priority_orders_per_day")
    orders = {}
    for rule in RULES:
        per = orders_per_round_trip(A, rule)
        mean = float(A[f"hedged_{rule}"].mean())
        orders[rule] = {f"{n}_fills_a_day": {"option_orders": round(per * n), "over_limit": bool(per * n > lim),
                                             "mean_day_hedged_usd": mean * n}
                        for n in cfg["bankroll"]["s10_fills_per_day"]}
    return {"note": "Study 11b EXPLORATION: hedged market making (exit follows the far side; IWM delta hedge from "
                    "1-second quotes); 10 seen sessions, in sample; $ a contract, $0 option commission; headline "
                    "rule = through (SPEC rule 5)",
            "sessions": sorted(str(d) for d in T["date"].unique()), "round_trips": int(len(T)),
            "by_spread": by_spread,
            "advancing_buckets": {"buckets": buckets, **_row(A, cfg, True)},
            "advancing_by_side": {k: _row(x, cfg, False) for k, x in A.groupby("side")},
            "advancing_by_dte": {str(k): _row(x, cfg, False) for k, x in A.groupby("dte_b")},
            "advancing_by_abs_delta": {str(k): _row(x, cfg, False) for k, x in A.groupby("delta_b")},
            "orders_and_days": orders, "priority_limit_orders_per_day": lim,
            "checks": {"hedge_missing": float(T["hedge_through"].isna().mean()),
                       # with a moving exit price an earlier fill need not pay more, so the guaranteed order is in time
                       "exit_time_front_le_queue_le_through": bool(
                           (T["exit_ts_front"] <= T["exit_ts_queue"]).all()
                           and (T["exit_ts_queue"] <= T["exit_ts_through"]).all())}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--explore", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    from src.analysis import to_json
    if a.explore:
        print(to_json(explore(trips_table(cfg), cfg)))
    elif a.report_only:
        print(to_json(explore(store.load_derived("study11b_trips", cfg), cfg)))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
