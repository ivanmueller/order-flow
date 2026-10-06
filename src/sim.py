"""Stage 3 trade simulator: walks trades tick by tick, so there is no same-bar ambiguity.

Confirmed entry (SPEC "Trade rules: confirmed entry"):
  E = first trade after t_dec + d * entry_slippage ticks
  S = p_ext - d * stop_buffer ticks;  R_k = d (E - S)
  skip if R_k > max_risk EM; if R_k < min_risk ticks, widen S so R_k = min_risk ticks
  T = E + d * target_mult * R_k; target fills only on a print one tick beyond T
  stop exits one tick beyond S (or at the print itself if it gapped further)
  time exit at t_dec + time_exit or flat_time, whichever first, at the next trade minus one tick
Naive baseline: limit at L filled only by a print one tick through L, within reclaim_window of t0;
  stop L - d fail_F EM, target 1.5x that distance, same exits.
PnL_R = d (X - E) / R_k - C / (R_k PV)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import calendar as calm
from src.config import param


def round_tick(x, tick):
    return float(np.round(x / tick) * tick)


def walk(trades: pd.DataFrame, start: int, E: float, S: float, T: float, d: int,
         t_exit: pd.Timestamp, tick: float) -> tuple[float, str, pd.Timestamp]:
    """Exit price, reason, and time for a position entered on trade index start-1."""
    px = trades["price"].to_numpy(float)
    ts = trades["ts_event_utc"]
    stop_fill = S - d * tick
    for j in range(start, len(trades)):
        p = px[j]
        if ts.iat[j] >= t_exit:
            return p - d * tick, "time", ts.iat[j]
        if d * (p - S) <= 0:
            return (stop_fill if d * (p - stop_fill) >= 0 else p), "stop", ts.iat[j]
        if d * (p - T) >= tick - 1e-9:
            return T, "target", ts.iat[j]
    if start < len(trades):
        return px[-1] - d * tick, "data_end", ts.iat[-1]
    return np.nan, "no_data", pd.NaT


def pnl_r(X, E, R_k, d, cfg) -> float:
    C = param(cfg, "cost_rt_usd")
    return d * (X - E) / R_k - C / (R_k * cfg["market"]["point_value"])


def _flat(day, cfg):
    return calm.et_time(day, cfg["market"]["flat_time"])


def confirmed_trade(trades: pd.DataFrame, feat: dict, L: float, d: int, em: float, day, cfg) -> dict | None:
    if not feat.get("confirmed"):
        return None
    tick = cfg["market"]["tick"]
    trades = trades.reset_index(drop=True)
    ts = trades["ts_event_utc"]
    after = np.where(ts > feat["t_dec"])[0]
    if len(after) == 0:
        return {"skip": "no_entry_trade"}
    i = int(after[0])
    if ts.iat[i] >= _flat(day, cfg):
        return {"skip": "too_late"}
    E = trades["price"].iat[i] + d * param(cfg, "entry_slippage") * tick
    S = feat["p_ext"] - d * param(cfg, "stop_buffer") * tick
    R_k = d * (E - S)
    if R_k > param(cfg, "max_risk") * em:
        return {"skip": "risk_too_wide", "R_k": R_k}
    min_r = param(cfg, "min_risk") * tick
    if R_k < min_r:
        S, R_k = E - d * min_r, min_r
    T = E + d * param(cfg, "target_mult") * R_k
    t_exit = min(feat["t_dec"] + pd.Timedelta(minutes=param(cfg, "time_exit")), _flat(day, cfg))
    X, why, t_x = walk(trades, i + 1, E, S, T, d, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return {"entry_ts": ts.iat[i], "E": E, "S": S, "T": T, "R_k": R_k, "X": X, "exit_reason": why,
            "exit_ts": t_x, "pnl_r": pnl_r(X, E, R_k, d, cfg)}


def continuation_trade(trades: pd.DataFrame, feat: dict, L: float, d: int, em: float, day, cfg) -> dict | None:
    """V5: trade WITH the break, direction -d. Entry: first trade after t0 + reclaim_window, plus one
    tick against. Stop: stop_buffer ticks back inside the level. Target target_mult R, same fills and
    time exit as the confirmed trade."""
    if not feat.get("broke"):
        return None
    tick = cfg["market"]["tick"]
    db = -d
    trades = trades.reset_index(drop=True)
    ts = trades["ts_event_utc"]
    after = np.where(ts > feat["t_break"])[0]
    if len(after) == 0:
        return {"skip": "no_entry_trade"}
    i = int(after[0])
    if ts.iat[i] >= _flat(day, cfg):
        return {"skip": "too_late"}
    E = trades["price"].iat[i] + db * param(cfg, "entry_slippage") * tick
    S = L - db * param(cfg, "stop_buffer") * tick
    R_k = db * (E - S)
    if R_k > param(cfg, "max_risk") * em:
        return {"skip": "risk_too_wide", "R_k": R_k}
    min_r = param(cfg, "min_risk") * tick
    if R_k < min_r:
        S, R_k = E - db * min_r, min_r
    T = E + db * param(cfg, "target_mult") * R_k
    t_exit = min(feat["t_break"] + pd.Timedelta(minutes=param(cfg, "time_exit")), _flat(day, cfg))
    X, why, t_x = walk(trades, i + 1, E, S, T, db, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return {"entry_ts": ts.iat[i], "E": E, "S": S, "T": T, "R_k": R_k, "X": X, "exit_reason": why,
            "exit_ts": t_x, "pnl_r": pnl_r(X, E, R_k, db, cfg)}


def naive_trade(trades: pd.DataFrame, t0: pd.Timestamp, L: float, d: int, em: float, day, cfg) -> dict | None:
    tick = cfg["market"]["tick"]
    trades = trades.reset_index(drop=True)
    ts = trades["ts_event_utc"]
    px = trades["price"].to_numpy(float)
    live = (ts >= t0) & (ts < t0 + pd.Timedelta(minutes=param(cfg, "reclaim_window")))
    fills = np.where(live.to_numpy() & (d * (L - px) >= tick - 1e-9))[0]
    if len(fills) == 0:
        return {"skip": "no_fill"}
    i = int(fills[0])
    if ts.iat[i] >= _flat(day, cfg):
        return {"skip": "too_late"}
    dist = max(tick, round_tick(param(cfg, "fail_F") * em, tick))
    E, S, T = L, L - d * dist, L + d * param(cfg, "target_mult") * dist
    t_exit = min(ts.iat[i] + pd.Timedelta(minutes=param(cfg, "time_exit")), _flat(day, cfg))
    X, why, t_x = walk(trades, i + 1, E, S, T, d, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return {"entry_ts": ts.iat[i], "E": E, "S": S, "T": T, "R_k": dist, "X": X, "exit_reason": why,
            "exit_ts": t_x, "pnl_r": pnl_r(X, E, dist, d, cfg)}


def mirror_trade(trades: pd.DataFrame, t0: pd.Timestamp, L: float, d: int, em: float, day, cfg) -> dict | None:
    """Diagnostic only (not a strategy): the opposite side of the naive fill. Enter at the print that
    fills the naive limit (one tick through L), direction -d, one tick of slippage against; stop
    fail_F EM back on the other side of L; target target_mult R. Same walk, fills and costs."""
    tick = cfg["market"]["tick"]
    trades = trades.reset_index(drop=True)
    ts = trades["ts_event_utc"]
    px = trades["price"].to_numpy(float)
    live = (ts >= t0) & (ts < t0 + pd.Timedelta(minutes=param(cfg, "reclaim_window")))
    fills = np.where(live.to_numpy() & (d * (L - px) >= tick - 1e-9))[0]
    if len(fills) == 0:
        return {"skip": "no_fill"}
    i = int(fills[0])
    if ts.iat[i] >= _flat(day, cfg):
        return {"skip": "too_late"}
    dm = -d
    dist = max(tick, round_tick(param(cfg, "fail_F") * em, tick))
    E = px[i] + dm * param(cfg, "entry_slippage") * tick
    S = L + d * dist
    R_k = dm * (E - S)
    T = E + dm * param(cfg, "target_mult") * R_k
    t_exit = min(ts.iat[i] + pd.Timedelta(minutes=param(cfg, "time_exit")), _flat(day, cfg))
    X, why, t_x = walk(trades, i + 1, E, S, T, dm, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return {"entry_ts": ts.iat[i], "E": E, "S": S, "T": T, "R_k": R_k, "X": X, "exit_reason": why,
            "exit_ts": t_x, "pnl_r": pnl_r(X, E, R_k, dm, cfg)}
