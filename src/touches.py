"""Stage 2: touch detection and outcome labels on 1-minute ES bars (SPEC.md, "Stage 2").

Touch at bar t (09:31..15:50 ET), with a = approach_a EM, b = proximity_b ticks:
  d = +1 (support, from above):    low_t  <= L + b  and  max(close[t-30 .. t-1]) >= L + a
  d = -1 (resistance, from below): high_t >= L - b  and  min(close[t-30 .. t-1]) <= L - a
  d is chosen by which side of L the previous close sits on.
  Debounce: ignore the touch if any of the previous `debounce` bars came within b of L
  (low <= L + b and high >= L - b). touch_n = earlier accepted touches of this level today.
Label: scan bars t .. t+H-1 (inclusive of t, stopping at the RTH close). Success if price reaches
  L + d R before L - d F. Both in one bar, or neither within H bars -> failure (timeouts flagged).
MFE/MAE over bars t .. t+30 in EM units, from bar highs/lows.

Run: python -m src.touches [--start ...] [--end ...]
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import store
from src.config import load_config, param
from src.levels import session_bars

log = logging.getLogger("touches")


def detect(bars: pd.DataFrame, L: float, em: float, cfg) -> list[dict]:
    """Touches of one level in one session. bars: the whole Globex session on one contract."""
    tick = cfg["market"]["tick"]
    a, b = param(cfg, "approach_a") * em, param(cfg, "proximity_b") * tick
    lb, deb = param(cfg, "approach_lookback"), param(cfg, "debounce")
    hi, lo, cl = (bars[c].to_numpy(float) for c in ("high", "low", "close"))
    mod = calm.minutes_of_day_et(bars["ts_open_utc"]).to_numpy()
    t_start, t_end = calm.hhmm_to_min(cfg["market"]["touch_start"]), calm.hhmm_to_min(cfg["market"]["touch_end"])
    prox = (lo <= L + b) & (hi >= L - b)
    out, n = [], 0
    for t in np.where((mod >= t_start) & (mod <= t_end))[0]:
        if t == 0:
            continue
        if prox[max(0, t - deb):t].any():
            continue
        prev = cl[max(0, t - lb):t]
        d = 1 if cl[t - 1] > L else -1
        if d == 1:
            hit = lo[t] <= L + b and prev.max() >= L + a
        else:
            hit = hi[t] >= L - b and prev.min() <= L - a
        if hit:
            out.append({"bar_idx": int(t), "d": d, "touch_n": n})
            n += 1
    return out


def label(bars: pd.DataFrame, t: int, L: float, d: int, em: float, cfg) -> dict:
    hi, lo = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
    mod = calm.minutes_of_day_et(bars["ts_open_utc"]).to_numpy()
    rth_end = calm.hhmm_to_min(cfg["market"]["rth_close"])
    R, F = param(cfg, "success_R") * em, param(cfg, "fail_F") * em
    H, M = param(cfg, "label_horizon"), param(cfg, "mfe_horizon")
    last = t
    while last + 1 < len(bars) and last + 1 < t + H and mod[last + 1] < rth_end:
        last += 1
    fav = hi if d == 1 else lo      # price moving our way
    adv = lo if d == 1 else hi      # price moving against
    success, timeout, k_out = False, True, np.nan
    for k in range(t, last + 1):
        win = d * (fav[k] - L) >= R
        loss = d * (adv[k] - L) <= -F
        if win or loss:
            success, timeout, k_out = (win and not loss), False, k - t
            break
    m_last = t
    while m_last + 1 < len(bars) and m_last + 1 <= t + M and mod[m_last + 1] < rth_end:
        m_last += 1
    seg = slice(t, m_last + 1)
    mfe = float(np.max(d * (fav[seg] - L)) / em)
    mae = float(np.max(-d * (adv[seg] - L)) / em)
    return {"success": success, "timeout": timeout, "bars_to_outcome": k_out,
            "horizon_bars": last - t + 1, "mfe": mfe, "mae": mae}


def tod_bucket(ts_utc: pd.Series, cfg) -> pd.Series:
    m = calm.minutes_of_day_et(ts_utc)
    b1, b2 = (calm.hhmm_to_min(x) for x in cfg["market"]["tod_breaks"])
    return pd.Series(np.select([m < b1, m < b2], ["open", "mid"], "close"), index=ts_utc.index)


def day_touches(sess: pd.DataFrame, levels: pd.DataFrame, cfg) -> pd.DataFrame:
    rows = []
    for lv in levels.itertuples():
        for tch in detect(sess, lv.level_es, lv.em, cfg):
            lab = label(sess, tch["bar_idx"], lv.level_es, tch["d"], lv.em, cfg)
            rows.append({"level_id": lv.level_id, **tch, **lab,
                         "t0": sess["ts_open_utc"].iloc[tch["bar_idx"]]})
    return pd.DataFrame(rows)


def build(start=None, end=None, cfg=None, include_holdout: bool = False, levels: pd.DataFrame | None = None,
          save: bool = True, bars: pd.DataFrame | None = None, cal: pd.DataFrame | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    if levels is None:
        levels = store.load_derived("levels_holdout" if include_holdout else "levels", cfg, include_holdout)
    levels = store.date_range_filter(levels, start, end)
    cal = (cal if cal is not None else store.load_calendar(cfg, include_holdout)).set_index("date")
    bars = bars if bars is not None else store.load_bars(cfg, include_holdout)
    by_day = {d: x for d, x in bars.groupby("date")}
    out = []
    for day, lv in levels.groupby("date"):
        sess = session_bars(by_day, day, cal.loc[day, "instrument_id"])
        t = day_touches(sess, lv, cfg)
        if not t.empty:
            out.append(t)
    if not out:
        return pd.DataFrame()
    tc = pd.concat(out, ignore_index=True)
    tc = tc.merge(levels, on="level_id", how="left")
    tc["touch_id"] = tc["level_id"] + "_" + tc["bar_idx"].astype(str).str.zfill(4)
    tc["first"] = tc["touch_n"] == 0
    tc["tod"] = tod_bucket(tc["t0"], cfg)
    tc = tc.sort_values(["date", "t0", "level_es"]).reset_index(drop=True)
    if save:
        store.save_derived(tc, "touches_holdout" if include_holdout else "touches", cfg)
    return tc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    tc = build(a.start, a.end)
    if tc.empty:
        print("no touches")
        return
    print(tc.groupby("group").agg(n=("success", "size"), success=("success", "mean"), timeout=("timeout", "mean")))


if __name__ == "__main__":
    main()
