"""Stage 1 daily outcome measures on ES, 09:30-16:00 ET (SPEC.md, "Stage 1").

  RR_D = (H_D - L_D) / EM_D                                  range vs options-implied move
  VR_D = Var(sum_{k=0..5} r_{t-k}) / (6 Var(r_t))            5-min log returns, overlapping 30-min sums
  ER_D = |C_D - O_D| / sum_j |p_j - p_{j-1}|                  1-minute closes
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.levels import rth_mask, session_bars


def variance_ratio(closes_5m: np.ndarray, k: int = 6) -> float:
    r = np.diff(np.log(closes_5m))
    if len(r) < 2 * k:
        return np.nan
    agg = np.convolve(r, np.ones(k), mode="valid")
    v1 = np.var(r, ddof=1)
    return float(np.var(agg, ddof=1) / (k * v1)) if v1 > 0 else np.nan


def efficiency_ratio(open_px: float, closes_1m: np.ndarray) -> float:
    path = np.concatenate([[open_px], closes_1m])
    tot = np.abs(np.diff(path)).sum()
    return float(abs(path[-1] - path[0]) / tot) if tot > 0 else np.nan


def day_outcomes(rth: pd.DataFrame, em: float) -> dict:
    rth = rth.sort_values("ts_open_utc")
    o, c = float(rth["open"].iloc[0]), rth["close"].to_numpy(float)
    s = rth.set_index("ts_open_utc")["close"]
    c5 = s.resample("5min", origin="epoch", label="right", closed="left").last().dropna().to_numpy(float)
    c5 = np.concatenate([[o], c5])
    return {"rth_high": float(rth["high"].max()), "rth_low": float(rth["low"].min()),
            "rr": (rth["high"].max() - rth["low"].min()) / em if em > 0 else np.nan,
            "vr": variance_ratio(c5), "er": efficiency_ratio(o, c), "n_bars": len(rth)}


def build(bars: pd.DataFrame, cal: pd.DataFrame, gex: pd.DataFrame, daily: pd.DataFrame, cfg) -> pd.DataFrame:
    by_day = {d: x for d, x in bars.groupby("date")}
    g = gex.set_index("date")
    vix = daily.sort_values("date").set_index("date")["vix_close"]
    rows = []
    for r in cal.itertuples():
        if r.date not in g.index or pd.isna(r.prev_date):
            continue
        sess = session_bars(by_day, r.date, r.instrument_id)
        rth = sess[rth_mask(sess, cfg)] if not sess.empty else sess
        if rth.empty:
            continue
        x = g.loc[r.date]
        rows.append({"date": r.date, **day_outcomes(rth, x["em"]), "em": x["em"], "s0": x["s0"],
                     "gex_pct": x["gex_pct"], "gex_pct_0dte": x.get("gex_pct_0dte", np.nan),
                     "net_gex": x["net_gex"], "vix_prev": vix.get(r.prev_date, np.nan),
                     "half_day": bool(r.half_day)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["ln_vix"] = np.log(df["vix_prev"])
    df["ln_em_s0"] = np.log(df["em"] / df["s0"])
    df["dow"] = pd.to_datetime(df["date"]).dt.dayofweek
    return df.sort_values("date").reset_index(drop=True)
