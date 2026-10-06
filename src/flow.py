"""Stage 3: order flow features around each touch (SPEC.md, "Stage 3").

Trades: price p_j, size q_j, aggressor sign s_j (+1 buy, -1 sell); signed volume v_j = s_j q_j.
t0 is reset to the first trade at or through L + d b (i.e. d (p - L) <= b) from the touch bar onward.

  AbsRatio = (AggIn / Pen) / (0.5 * Baseline_slot)
      AggIn = sum of q_j with s_j = -d over W = [t0, t0 + abs_window)
      Pen   = max(1, d (L - p_ext_W) / tick),  p_ext_W = lowest (support) / highest (resistance) trade in W
      Baseline_slot = median over the prior baseline_sessions of abs_window-minute bar volume per tick
                      of range, in the same half-hour slot (counts both sides, hence the 0.5)
  ApproachDelta = sum v / sum q over [t0 - 10m, t0)
  Exhaustion    = (AggIn[t0-2m, t0) / 2) / (AggIn[t0-10m, t0-2m) / 8)
  Reclaim: first 1-minute bar k ending in (t0, t0 + reclaim_window] with
      d (close_k - L) >= reclaim_ticks ticks  and  d * sum v over [t0, end_k) > 0;  t_r = end_k
  Confirmed = reclaim and AbsRatio >= abs_threshold.
  V5 break (pre-registered variant, RUNLOG 2026-10-06): no reclaim within reclaim_window, price pushed
      at least break_ticks through the level inside that window, and -d * sum v over [t0, t0 + rw) > 0.
      Decision time t0 + reclaim_window; the continuation trade goes in direction -d.

Point-in-time fix (flagged in README): AbsRatio needs trades through t0 + abs_window, but a reclaim
can complete earlier. The entry decision time is therefore t_dec = max(t_r, t0 + abs_window), and the
stop extreme is taken over [t0, t_dec]. With the default 3-minute window this only delays fast reclaims.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import calendar as calm
from src.config import param

APPROACH_MIN = 10      # SPEC: approach window is the 10 minutes before t0
EXH_RECENT_MIN = 2     # SPEC: exhaustion compares the last 2 minutes against the 8 before


def agg_in(tr: pd.DataFrame, d: int) -> float:
    return float(tr.loc[tr["side"] == -d, "size"].sum())


def reset_t0(trades: pd.DataFrame, bar_open: pd.Timestamp, L: float, d: int, b: float):
    """Index and time of the first trade at or through the level band from the touch bar onward."""
    after = trades[trades["ts_event_utc"] >= bar_open]
    hit = after[d * (after["price"] - L) <= b + 1e-9]
    if hit.empty:
        return None, None
    i = hit.index[0]
    return i, trades.at[i, "ts_event_utc"]


def baseline_table(bars: pd.DataFrame, cal: pd.DataFrame, cfg) -> pd.DataFrame:
    """Baseline_slot for every (date, half-hour slot): median over the prior N sessions of
    abs_window-minute bar volume per tick of range. Only prior sessions are used (no lookahead)."""
    tick = cfg["market"]["tick"]
    win = param(cfg, "abs_window")
    n = param(cfg, "baseline_sessions")
    inst = cal.set_index("date")["instrument_id"]
    rth_open, rth_close = (calm.hhmm_to_min(cfg["market"][k]) for k in ("rth_open", "rth_close"))
    per_day: dict = {}
    for day, b in bars.groupby("date"):
        b = b[b["instrument_id"] == inst.get(day, -1)]
        mod = calm.minutes_of_day_et(b["ts_open_utc"])
        b = b[(mod >= rth_open) & (mod < rth_close)]
        if b.empty:
            continue
        r = b.set_index("ts_open_utc").resample(f"{win}min", origin="epoch").agg(
            {"high": "max", "low": "min", "volume": "sum"}).dropna()
        v = r["volume"] / np.maximum(1.0, (r["high"] - r["low"]) / tick)
        slot = (calm.minutes_of_day_et(pd.Series(r.index, index=r.index)) // 30).to_numpy()
        per_day[day] = pd.DataFrame({"slot": slot, "v": v.to_numpy()})
    days = sorted(per_day)
    rows = []
    for i, day in enumerate(days):
        if i < n:
            continue
        pool = pd.concat([per_day[d] for d in days[i - n:i]])
        med = pool.groupby("slot")["v"].median()
        rows += [(day, int(s), float(m)) for s, m in med.items()]
    return pd.DataFrame(rows, columns=["date", "slot", "baseline"])


def minute_bars(tr: pd.DataFrame) -> pd.DataFrame:
    """Clock-aligned 1-minute bars from trades: end time, close, and signed volume."""
    if tr.empty:
        return pd.DataFrame(columns=["end", "close"])
    g = tr.groupby(tr["ts_event_utc"].dt.floor("1min"))
    out = pd.DataFrame({"close": g["price"].last()})
    out["end"] = out.index + pd.Timedelta(minutes=1)
    return out.reset_index(drop=True)


def features(trades: pd.DataFrame, bar_open: pd.Timestamp, L: float, d: int, baseline: float, cfg) -> dict:
    """All Stage 3 features for one touch. trades: the window around the touch, time-sorted."""
    tick = cfg["market"]["tick"]
    b = param(cfg, "proximity_b") * tick
    out = {"has_t0": False, "confirmed": False, "reclaim": False, "broke": False}
    trades = trades.reset_index(drop=True)
    i0, t0 = reset_t0(trades, bar_open, L, d, b)
    if t0 is None:
        return out
    out.update(has_t0=True, t0_trade=t0)
    ts = trades["ts_event_utc"]
    v = trades["side"] * trades["size"]

    aw = pd.Timedelta(minutes=param(cfg, "abs_window"))
    W = trades[(ts >= t0) & (ts < t0 + aw)]
    a_in = agg_in(W, d)
    p_ext = W["price"].min() if d == 1 else W["price"].max()
    pen = max(1.0, d * (L - p_ext) / tick)
    out.update(agg_in=a_in, pen=pen, baseline=baseline,
               abs_ratio=(a_in / pen) / (0.5 * baseline) if baseline and np.isfinite(baseline) and baseline > 0 else np.nan)

    pre = (ts >= t0 - pd.Timedelta(minutes=APPROACH_MIN)) & (ts < t0)
    q_pre = trades.loc[pre, "size"].sum()
    out["approach_delta"] = float(v[pre].sum() / q_pre) if q_pre > 0 else np.nan
    recent = trades[(ts >= t0 - pd.Timedelta(minutes=EXH_RECENT_MIN)) & (ts < t0)]
    early = trades[(ts >= t0 - pd.Timedelta(minutes=APPROACH_MIN)) & (ts < t0 - pd.Timedelta(minutes=EXH_RECENT_MIN))]
    den = agg_in(early, d) / (APPROACH_MIN - EXH_RECENT_MIN)
    out["exhaustion"] = (agg_in(recent, d) / EXH_RECENT_MIN) / den if den > 0 else np.nan

    rw = pd.Timedelta(minutes=param(cfg, "reclaim_window"))
    post = trades[(ts >= t0) & (ts < t0 + rw + pd.Timedelta(minutes=1))]
    mb = minute_bars(trades[(ts >= t0.floor("1min")) & (ts < t0 + rw + pd.Timedelta(minutes=1))])
    t_r = None
    for bar in mb.itertuples():
        if bar.end <= t0 or bar.end > t0 + rw:
            continue
        if d * (bar.close - L) < param(cfg, "reclaim_ticks") * tick - 1e-9:
            continue
        seg = post[post["ts_event_utc"] < bar.end]
        if d * (seg["side"] * seg["size"]).sum() > 0:
            t_r = bar.end
            break
    # V5 break: no reclaim, price pushed >= break_ticks through the level inside the reclaim window,
    # and net aggressive flow since t0 points through the level (the mirror of the reclaim test).
    win = trades[(ts >= t0) & (ts < t0 + rw)]
    if not win.empty:
        p_thru = win["price"].min() if d == 1 else win["price"].max()
        out["break_pen"] = float(d * (L - p_thru) / tick)
        out["break_flow"] = float(-d * (win["side"] * win["size"]).sum())
    out["broke"] = bool(t_r is None and out.get("break_pen", 0) >= param(cfg, "break_ticks")
                        and out.get("break_flow", 0) > 0)
    out["t_break"] = t0 + rw
    if t_r is None:
        return out
    t_dec = max(t_r, t0 + aw)
    span = trades[(ts >= t0) & (ts <= t_dec)]
    out.update(reclaim=True, t_r=t_r, t_dec=t_dec,
               p_ext=float(span["price"].min() if d == 1 else span["price"].max()))
    out["confirmed"] = bool(np.isfinite(out["abs_ratio"]) and out["abs_ratio"] >= param(cfg, "abs_threshold"))
    return out
