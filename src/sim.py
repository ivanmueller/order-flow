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


def _risk_and_target(E, S, d, em, cfg, tm):
    """Shared risk floor/cap and target. Returns (S, R_k, T) or a skip dict."""
    tick = cfg["market"]["tick"]
    R_k = d * (E - S)
    if R_k > param(cfg, "max_risk") * em:
        return {"skip": "risk_too_wide", "R_k": R_k}
    min_r = param(cfg, "min_risk") * tick
    if R_k < min_r:
        S, R_k = E - d * min_r, min_r
    return S, R_k, E + d * tm * R_k


def limit_trade(trades: pd.DataFrame, feat: dict, L: float, d: int, em: float, day, cfg,
                target_mult: float | None = None, time_exit: float | None = None) -> dict | None:
    """Study 2 E1: at t_dec a limit at L + d*entry_offset ticks works for fill_window minutes and
    fills only on a print one tick through it (no slippage on a resting order). Stop stop_buffer
    ticks beyond the lowest/highest print seen from t0 to the fill; target target_mult R; time exit
    from the fill. The caller decides eligibility (confirmed_s2)."""
    if not feat.get("reclaim"):
        return None
    tick = cfg["market"]["tick"]
    tm = param(cfg, "target_mult") if target_mult is None else target_mult
    tx = param(cfg, "time_exit") if time_exit is None else time_exit
    trades = trades.reset_index(drop=True)
    ts, px = trades["ts_event_utc"], trades["price"].to_numpy(float)
    P = L + d * param(cfg, "entry_offset") * tick
    live = (ts > feat["t_dec"]) & (ts <= feat["t_dec"] + pd.Timedelta(minutes=param(cfg, "fill_window")))
    fills = np.where(live.to_numpy() & (d * (P - px) >= tick - 1e-9))[0]
    if len(fills) == 0:
        return {"skip": "no_fill"}
    i = int(fills[0])
    if ts.iat[i] >= _flat(day, cfg):
        return {"skip": "too_late"}
    path = trades[(ts >= feat["t0_trade"]) & (ts <= ts.iat[i])]["price"]
    p_ext = float(path.min() if d == 1 else path.max())
    E = P
    S = p_ext - d * param(cfg, "stop_buffer") * tick
    r = _risk_and_target(E, S, d, em, cfg, tm)
    if isinstance(r, dict):
        return r
    S, R_k, T = r
    t_exit = min(ts.iat[i] + pd.Timedelta(minutes=tx), _flat(day, cfg))
    X, why, t_x = walk(trades, i + 1, E, S, T, d, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return {"entry_ts": ts.iat[i], "E": E, "S": S, "T": T, "R_k": R_k, "X": X, "exit_reason": why,
            "exit_ts": t_x, "pnl_r": pnl_r(X, E, R_k, d, cfg)}


def retest_trade(trades: pd.DataFrame, feat: dict, L: float, d: int, em: float, day, cfg) -> dict | None:
    """Study 2 E2 (low-gamma continuation): after a V5 break, wait up to retest_window minutes for a
    print back within proximity_b of L. Look at the first clock minute that ends after that print:
    if its close fails to reclaim L (d*(close-L) < reclaim_ticks), enter with the break direction on
    the first print after that minute, one tick of slippage; stop stop_buffer ticks back on the
    original side of L; target target_mult R; time exit from entry."""
    if not feat.get("broke"):
        return None
    tick = cfg["market"]["tick"]
    b = param(cfg, "proximity_b") * tick
    trades = trades.reset_index(drop=True)
    ts, px = trades["ts_event_utc"], trades["price"].to_numpy(float)
    t_b = feat["t_break"]
    t_end = t_b + pd.Timedelta(minutes=param(cfg, "retest_window"))
    cand = np.where(((ts > t_b) & (ts <= t_end)).to_numpy() & (np.abs(px - L) <= b + 1e-9))[0]
    if len(cand) == 0:
        return {"skip": "no_retest"}
    t_rt = ts.iat[int(cand[0])]
    bar_end = t_rt.floor("1min") + pd.Timedelta(minutes=1)
    bar = trades[(ts >= t_rt.floor("1min")) & (ts < bar_end)]
    close = float(bar["price"].iloc[-1])
    if d * (close - L) >= param(cfg, "reclaim_ticks") * tick - 1e-9:
        return {"skip": "reclaimed"}
    after = np.where((ts >= bar_end).to_numpy())[0]
    if len(after) == 0:
        return {"skip": "no_entry_trade"}
    i = int(after[0])
    if ts.iat[i] >= _flat(day, cfg):
        return {"skip": "too_late"}
    dm = -d
    E = px[i] + dm * param(cfg, "entry_slippage") * tick
    S = L + d * param(cfg, "stop_buffer") * tick
    r = _risk_and_target(E, S, dm, em, cfg, param(cfg, "target_mult"))
    if isinstance(r, dict):
        return r
    S, R_k, T = r
    t_exit = min(ts.iat[i] + pd.Timedelta(minutes=param(cfg, "time_exit")), _flat(day, cfg))
    X, why, t_x = walk(trades, i + 1, E, S, T, dm, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return {"entry_ts": ts.iat[i], "E": E, "S": S, "T": T, "R_k": R_k, "X": X, "exit_reason": why,
            "exit_ts": t_x, "pnl_r": pnl_r(X, E, R_k, dm, cfg)}
