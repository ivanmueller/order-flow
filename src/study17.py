"""Study 17 step 1: forecast-selected Uniswap liquidity, optionally perp-hedged (STUDY17.md; $0, data on disk).

Our own position is simulated, not approximated. A pool position's value depends only on the price, so per day:
amounts x (token0) and y (token1) follow the pool's close; they are marked at Binance prices (stablecoins at $1);
fees = pool fees x our liquidity / (pool liquidity + ours) on days whose whole range stays inside ours; the hedge is
a short perpetual of each volatile token's amount, reset at each daily close, with actual Binance funding and a
taker fee on every change. Weeks run Monday 00:00 to Monday 00:00 UTC; each week is a fresh position worth $1 at the
prior Sunday close. Transaction costs (gas, the swap into the pair, perp open/close) are charged by continuity:
entering, leaving, and (concentrated ranges) re-centering.

Selection, decided at Monday 00:00 from earlier days only: enter when 8 x trailing 7-day fee yield >=
(1 + margin)^2 x forecast weekly variance (fee-implied vol >= (1 + margin) x forecast vol), trailing TVL >= floor.
Forecast: log-HAR on 1/7/28-day realized variance, fit on discovery weeks only, or the trailing 28-day variance.

  python -m src.crypto_data --majors          # BTCUSDT and ETHUSDT hourly bars and funding (free)
  python -m src.study17 --build               # pool-week table from the step-0b subgraph cache + Binance data
  python -m src.study17 --discover            # 48 cells, placebo per cell, search noise test, freeze <= 4
  python -m src.study17 --diagnose            # discovery only: components, market path, concentration (rule 6)
  python -m src.study17 --validate            # ONCE: frozen cells on 2025-01..09 (never looked at ex ante)
"""
from __future__ import annotations

import argparse
import json
import logging
import math

import numpy as np
import pandas as pd

from src.config import data_path, load_config, param

log = logging.getLogger("study17")
DAY_S, DAY_NS = 86_400, 86_400 * 10**9
Q96 = 2.0 ** 96
EPS = 1e-8


# ---- our position (tested by hand) ------------------------------------------------------------------------------
def _range(P0, k):
    """sqrt of the range bounds: full range for k = 1, else [P0/m, P0 m] with m = 1/(1 - 1/k)^2 (k x efficiency)."""
    if k == 1:
        return np.zeros_like(P0), np.full_like(P0, np.inf)
    m = 1.0 / (1.0 - 1.0 / k) ** 2
    return np.sqrt(P0 / m), np.sqrt(P0 * m)


def _amounts(L, P, sa, sb):
    s = np.clip(np.sqrt(P), sa, sb)
    with np.errstate(divide="ignore"):
        x = L * (1.0 / s - np.where(np.isfinite(sb), 1.0 / sb, 0.0))
    return x, L * (s - sa)


def simulate_weeks(P, p0, p1, fees, Lpool, F0, F1, hi_x, lo_x, vol0, vol1, k, capital, perp_fee) -> dict:
    """Arrays (weeks, 8): column 0 is the prior Sunday close, 1..7 the days (fees, liquidity, funding, the day's
    excursion of the pool price around its close). Returns per-$1 gross results per week."""
    P0 = P[:, :1]
    sa, sb = _range(P0, k)
    x1, y1 = _amounts(1.0, P0, sa, sb)
    L = 1.0 / (x1 * p0[:, :1] + y1 * p1[:, :1])
    x, y = _amounts(L, P, sa, sb)
    V = x * p0 + y * p1
    d = slice(1, None)
    if k == 1:
        inside = np.ones_like(P[:, d], bool)
    else:
        inside = (P[:, d] * hi_x[:, d] <= sb ** 2) & (P[:, d] * lo_x[:, d] >= sa ** 2)
    fee = np.where(inside, fees[:, d] * L / (Lpool[:, d] + L * capital), 0.0)
    h0, h1 = x[:, :-1] * vol0, y[:, :-1] * vol1
    hedge = (-h0 * np.diff(p0, axis=1) - h1 * np.diff(p1, axis=1)
             + h0 * p0[:, :-1] * F0[:, d] + h1 * p1[:, :-1] * F1[:, d])
    reb = perp_fee * (np.abs(np.diff(x[:, :-1], axis=1)) * p0[:, 1:-1] * vol0      # resets at the ends of days 1..6
                      + np.abs(np.diff(y[:, :-1], axis=1)) * p1[:, 1:-1] * vol1)
    g_none = V[:, -1] - V[:, 0] + fee.sum(1)
    funding = (h0 * p0[:, :-1] * F0[:, d] + h1 * p1[:, :-1] * F1[:, d]).sum(1)
    return {"g_none": g_none, "g_perp": g_none + hedge.sum(1) - reb.sum(1), "fees": fee.sum(1),
            "value": V[:, -1] - V[:, 0], "hedge_px": hedge.sum(1) - funding, "funding": funding, "reb": reb.sum(1),
            "open_notional": x[:, 0] * p0[:, 0] * vol0 + y[:, 0] * p1[:, 0] * vol1,
            "close_notional": x[:, -1] * p0[:, -1] * vol0 + y[:, -1] * p1[:, -1] * vol1,
            "in_range_days": inside.sum(1)}


# ---- pool days ---------------------------------------------------------------------------------------------------
def _num(s):
    return pd.to_numeric(s, errors="coerce").to_numpy(float)


def _excursions(h, lo, c):
    ok = (lo > 0) & (h >= lo) & (h / np.where(lo > 0, lo, 1) < 5) & (c >= lo * 0.99) & (c <= h * 1.01) & (c > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        hi_x = np.where(ok, np.maximum(h / c, c / lo), np.inf)       # the pool's price orientation is not assumed
        lo_x = np.where(ok, np.minimum(lo / c, c / h), 0.0)
    return hi_x, lo_x


def v3_daily(rows: pd.DataFrame, dec0: int, dec1: int) -> pd.DataFrame:
    r = rows.sort_values("date")
    sp = _num(r["sqrtPrice"]) / Q96
    hi_x, lo_x = _excursions(_num(r["high"]), _num(r["low"]), _num(r["close"]))
    return pd.DataFrame({"P": sp ** 2 * 10.0 ** (int(dec0) - int(dec1)),
                         "Lpool": _num(r["liquidity"]) / 10.0 ** ((int(dec0) + int(dec1)) / 2),
                         "fees": _num(r["feesUSD"]), "tvl": _num(r["tvlUSD"]), "hi_x": hi_x, "lo_x": lo_x},
                        index=(_num(r["date"]) // DAY_S).astype("int64"))


def v2_daily(rows: pd.DataFrame, fee: float = 0.003) -> pd.DataFrame:
    r = rows.sort_values("date")
    r0, r1 = _num(r["reserve0"]), _num(r["reserve1"])
    with np.errstate(divide="ignore", invalid="ignore"):
        P = r1 / r0
    return pd.DataFrame({"P": P, "Lpool": np.sqrt(r0 * r1), "fees": fee * _num(r["dailyVolumeUSD"]),
                         "tvl": _num(r["reserveUSD"]), "hi_x": 1.0, "lo_x": 1.0},
                        index=(_num(r["date"]) // DAY_S).astype("int64"))


def attach_market(d: pd.DataFrame, p0, p1, F0, F1, max_gap: float, conc: tuple, v3: bool) -> pd.DataFrame:
    """Marks (Binance daily closes; None = stablecoin at $1) and daily funding; valid = usable day by the stated rules."""
    d = d[~d.index.duplicated(keep="last")].copy()
    idx = d.index
    d["p0"] = 1.0 if p0 is None else p0.reindex(idx).to_numpy(float)
    d["p1"] = 1.0 if p1 is None else p1.reindex(idx).to_numpy(float)
    d["F0"] = 0.0 if F0 is None else F0.reindex(idx).fillna(0.0).to_numpy(float)
    d["F1"] = 0.0 if F1 is None else F1.reindex(idx).fillna(0.0).to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = np.abs(d["P"] * d["p1"] / d["p0"] - 1)
        virt = 2 * d["Lpool"] * np.sqrt(d["P"]) * d["p1"]
        c = virt / d["tvl"]
    ok = ((d["P"] > 0) & (d["p0"] > 0) & (d["p1"] > 0) & (d["Lpool"] > 0) & (d["fees"] >= 0) & (d["tvl"] > 1000)
          & (d["fees"] <= d["tvl"]) & (gap <= max_gap))
    if v3:
        ok &= (c >= conc[0]) & (c <= conc[1])
    d["valid"] = ok.fillna(False).to_numpy(bool)
    d["fee_yield"] = np.where(d["valid"], d["fees"] / virt, np.nan)
    return d


def weekly_rows(d: pd.DataFrame, vol0: bool, vol1: bool, v3: bool, ks, capital, perp_fee, fee_days, trailing_days,
                min_days) -> pd.DataFrame:
    """One row per eligible Monday: features from days before it, the week's realized variance, simulated results."""
    if "fee_yield" not in d:
        d = d.assign(fee_yield=np.where(d["valid"], d["fees"] / (2 * d["Lpool"] * np.sqrt(d["P"]) * d["p1"]), np.nan))
    lo, hi = int(d.index.min()), int(d.index.max())
    full = d.reindex(np.arange(lo, hi + 1))
    valid = full["valid"].fillna(False).to_numpy(bool)
    P = full["P"].to_numpy(float)
    r2 = np.full(len(P), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        r2[1:] = np.where(valid[1:] & valid[:-1], np.log(P[1:] / P[:-1]) ** 2, np.nan)
    R2 = pd.Series(r2)
    m7 = R2.rolling(7, min_periods=min_days[0]).mean().shift(1).to_numpy()            # days before t
    m28 = R2.rolling(trailing_days, min_periods=min_days[1]).mean().shift(1).to_numpy()
    rv1 = R2.shift(1).to_numpy()
    fy = pd.Series(full["fee_yield"].to_numpy(float))
    fy7 = (fy.rolling(fee_days, min_periods=min_days[0]).mean() * 7).shift(1).to_numpy()
    tvl7 = pd.Series(np.where(valid, full["tvl"], np.nan)).rolling(fee_days, min_periods=min_days[0]).mean().shift(1).to_numpy()
    days = np.arange(lo, hi + 1)
    mondays = np.where((days % 7 == 4) & (days - 1 >= lo) & (days + 6 <= hi))[0]
    if len(mondays) == 0:
        return pd.DataFrame()
    win = mondays[:, None] + np.arange(-1, 7)[None, :]
    elig = valid[win].all(1)
    mondays, win = mondays[elig], win[elig]
    if len(mondays) == 0:
        return pd.DataFrame()
    A = {c: full[c].to_numpy(float)[win] for c in ("P", "p0", "p1", "fees", "Lpool", "F0", "F1", "hi_x", "lo_x")}
    out = pd.DataFrame({"t0": days[mondays], "rv1": rv1[mondays], "m7": m7[mondays], "m28": m28[mondays],
                        "fy7": fy7[mondays], "tvl7": tvl7[mondays],
                        "rv_week": np.nansum(r2[win[:, 1:]], axis=1)})
    for k in ks:
        if k > 1 and not v3:
            continue
        r = simulate_weeks(**A, vol0=vol0, vol1=vol1, k=k, capital=capital, perp_fee=perp_fee)
        names = ("g_none", "g_perp", "open_notional", "close_notional", "fees", "in_range_days")
        names += ("value", "hedge_px", "funding", "reb") if k == 1 else ()
        for name in names:
            out[f"{name}_k{k}"] = r[name]
    # market path (diagnostic): amounts follow the Binance price ratio instead of the pool's own close, so a stale
    # pool price (no swap late in the day) cannot hide impermanent loss; fees as in the base
    Am = dict(A, P=A["p0"] / A["p1"])
    r = simulate_weeks(**Am, vol0=vol0, vol1=vol1, k=1, capital=capital, perp_fee=perp_fee)
    for name in ("g_none", "g_perp", "value", "hedge_px", "funding", "reb", "fees"):
        out[f"mkt_{name}_k1"] = r[name]
    out["stale_gap"] = np.abs(np.log(A["P"][:, 1:] * A["p1"][:, 1:] / A["p0"][:, 1:])).mean(1)
    return out


# ---- Binance marks ---------------------------------------------------------------------------------------------
def perp_symbol(sym: str, available: set, token_map: dict):
    s = str(sym).upper()
    if s in {k.upper(): v for k, v in token_map.items()}:
        p = {k.upper(): v for k, v in token_map.items()}[s]
        return (p, 1.0) if p in available else None
    if f"{s}USDT" in available:
        return f"{s}USDT", 1.0
    if f"1000{s}USDT" in available:
        return f"1000{s}USDT", 1000.0
    return None


def daily_close(bars: pd.DataFrame) -> pd.Series:
    """Close of the 23:00 UTC hourly bar = close of the UTC day (real bars only)."""
    if bars is None or len(bars) == 0:
        return pd.Series(dtype=float)
    ts = bars["ts"].to_numpy("int64")
    m = ((ts % DAY_NS) == 23 * 3_600 * 10**9) & bars["real"].to_numpy(bool)
    return pd.Series(bars["close"].to_numpy(float)[m], index=ts[m] // DAY_NS)


def daily_funding(f: pd.DataFrame) -> pd.Series:
    """Funding rates settled in (day 00:00, next day 00:00]."""
    if f is None or len(f) == 0:
        return pd.Series(dtype=float)
    day = (f["ts"].to_numpy("int64") - 1) // DAY_NS
    return pd.Series(f["rate"].to_numpy(float)).groupby(day).sum()


# ---- selection machinery (tested) ------------------------------------------------------------------------------
def activate(rows: pd.DataFrame, signal: np.ndarray, hold: int):
    """Decide on weeks with wk % hold == 0 (the row must exist); hold that pool for the block's weeks that exist.
    Returns active, active the week before (same pool), active the week after."""
    r = rows[["pool", "wk"]].copy()
    r["sig"] = np.asarray(signal, bool).astype(float)
    r["blk"] = r["wk"] // hold
    dec = r["sig"].where(r["wk"] % hold == 0)
    on = (dec.groupby([r["pool"], r["blk"]]).transform("max").fillna(0.0) > 0).to_numpy()
    return (on, *continuity(rows, on))


def continuity(rows: pd.DataFrame, act: np.ndarray):
    """For active rows: was the same pool active the week before / the week after?"""
    act = np.asarray(act, bool)
    key = pd.MultiIndex.from_arrays([rows["pool"].to_numpy(), rows["wk"].to_numpy()])
    on = set(key[act])
    prev = np.array([(p, w - 1) in on for p, w in key]) & act
    nxt = np.array([(p, w + 1) in on for p, w in key]) & act
    return prev, nxt


def series_stats(s: pd.Series, draws: int, seed: int, level: float, tail: float) -> dict:
    v = np.asarray(s, float)
    n = len(v)
    if n < 2:
        return {"weeks": n}
    rng = np.random.default_rng(seed)
    boot = v[rng.integers(0, n, (draws, n))].mean(1)
    a = (1 - level) / 2
    sd = v.std(ddof=1)
    drop = int(math.ceil(tail * n))
    h = n // 2
    return {"weeks": n, "mean": float(v.mean()), "lo": float(np.quantile(boot, a)), "hi": float(np.quantile(boot, 1 - a)),
            "sharpe_annual": float(v.mean() / sd * math.sqrt(52)) if sd > 0 else None,
            "t": float(v.mean() / sd * math.sqrt(n)) if sd > 0 else None,
            "first_half": float(v[:h].mean()), "second_half": float(v[h:].mean()),
            "without_best_tail": float(np.sort(v)[: n - drop].mean()) if n > drop else None}


# ---- build ---------------------------------------------------------------------------------------------------------
def _available_perps(cfg) -> set:
    from src import crypto_data as cd
    root = cd.raw_root(cfg) / "klines_1h"
    return {p.name for p in root.iterdir() if p.is_dir()} if root.exists() else set()


def build(cfg) -> dict:
    from src import crypto_data as cd
    from src import defi_subgraph as sg
    root = data_path(cfg, "raw", "defi_subgraph")
    stables = {s.upper() for s in param(cfg, "s17_stables")}
    tmap, avail = param(cfg, "s17_token_map"), _available_perps(cfg)
    ks, cap, pf = param(cfg, "s17_concentration"), param(cfg, "s17_capital_main"), param(cfg, "s17_perp_fee")
    md = tuple(param(cfg, "s17_min_days"))
    gap, conc = param(cfg, "s17_max_gap"), (param(cfg, "s17_conc_min"), param(cfg, "s17_conc_max"))
    holdout_day = pd.Timestamp(param(cfg, "s14_holdout_start"), tz="UTC").value // DAY_NS
    marks: dict = {}

    def mark(sym):
        if sym.upper() in stables:
            return "stable"
        ps = perp_symbol(sym, avail, tmap)
        if ps is None:
            return None
        if ps[0] not in marks:
            c = daily_close(cd.load_bars(cfg, ps[0], "1h")) / ps[1]
            f = daily_funding(cd.load_funding(cfg, ps[0]))
            marks[ps[0]] = (c, f) if len(c) else None
        return marks[ps[0]]

    counts = {"pools_seen": 0, "stable_pair": 0, "no_perp": 0, "no_history": 0, "used": 0, "errors": 0}
    rows = []
    v3 = sg.Graph("", sg.V3_ID, root / "v3", 0, offline=True)
    v2 = sg.Graph("", sg.V2_ID, root / "v2", 0, offline=True)
    jobs = []
    try:
        for p in v3.q("pools_0", sg.V3_POOLS, {"skip": 0})["pools"]:
            jobs.append(("v3", p))
    except RuntimeError:
        log.warning("no v3 pool list in the cache")
    try:
        for p in v2.q("pairs_0", sg.V2_PAIRS, {"skip": 0})["pairs"]:
            jobs.append(("v2", p))
    except RuntimeError:
        log.warning("no v2 pair list in the cache")
    for i, (kind, p) in enumerate(jobs):
        counts["pools_seen"] += 1
        s0, s1 = p["token0"]["symbol"], p["token1"]["symbol"]
        if s0.upper() in stables and s1.upper() in stables:
            counts["stable_pair"] += 1
            continue
        m0, m1 = mark(s0), mark(s1)
        if m0 is None or m1 is None:
            counts["no_perp"] += 1
            continue
        try:
            if kind == "v3":
                days = pd.DataFrame(v3.paged(f"days_{p['id']}", sg.V3_DAYS, "poolDayDatas", {"pool": p["id"]}, "date"))
                d = v3_daily(days, p["token0"]["decimals"], p["token1"]["decimals"]) if len(days) else None
                project = f"uniswap-v3 ({int(p['feeTier']) / 1e4:.2f}%)"
            else:
                days = pd.DataFrame(v2.paged(f"days_{p['id']}", sg.V2_DAYS, "pairDayDatas", {"pair": p["id"]}, "date"))
                d = v2_daily(days) if len(days) else None
                project = "uniswap-v2"
        except RuntimeError:                                           # page not in the cache (pool skipped in 0b)
            counts["no_history"] += 1
            continue
        except Exception as e:
            counts["errors"] += 1
            log.warning("%s %s: %s", kind, p["id"], str(e)[:200])
            continue
        if d is None or len(d) < 40:
            counts["no_history"] += 1
            continue
        d = d[d.index < holdout_day]
        st = lambda m: (None, None) if m == "stable" else m
        (c0, f0), (c1, f1) = st(m0), st(m1)
        d = attach_market(d, c0, c1, f0, f1, gap, conc, kind == "v3")
        W = weekly_rows(d, m0 != "stable", m1 != "stable", kind == "v3", ks, cap, pf, param(cfg, "s17_fee_days"),
                        param(cfg, "s17_trailing_days"), md)
        if len(W):
            W["pool"], W["project"], W["symbol"], W["v3"] = p["id"], project, f"{s0}-{s1}", kind == "v3"
            rows.append(W)
            counts["used"] += 1
        if i % 100 == 0:
            log.info("%d of %d pools", i, len(jobs))
    T = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    out = data_path(cfg, "derived", "defi")
    out.mkdir(parents=True, exist_ok=True)
    if len(T):
        T = T[T["t0"] + 7 <= holdout_day]
        T["week"] = pd.to_datetime(T["t0"] * DAY_S, unit="s")
        T["wk"] = (T["t0"] - 4) // 7
        T.to_parquet(out / "study17_pool_weeks.parquet")
    res = {"counts": counts, "pool_weeks": int(len(T)), "pools": int(T["pool"].nunique()) if len(T) else 0,
           "first_week": str(T["week"].min().date()) if len(T) else None,
           "last_week": str(T["week"].max().date()) if len(T) else None,
           "by_project": T.groupby("project").size().to_dict() if len(T) else {},
           "perps_used": sorted(k for k, v in marks.items() if v is not None)}
    (out / "study17_build.json").write_text(json.dumps(res, indent=1, default=str))
    return res


# ---- discovery and validation ---------------------------------------------------------------------------------------
def fit_har(T: pd.DataFrame) -> dict:
    X, y, ok = _har_xy(T)
    b, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
    resid = y[ok] - X[ok] @ b
    return {"coef": [float(v) for v in b], "smear": float(np.mean(np.exp(resid))), "n": int(ok.sum()),
            "r2_log": float(1 - resid.var() / y[ok].var())}


def _har_xy(T):
    X = np.column_stack([np.ones(len(T)), np.log(7 * T["rv1"] + EPS), np.log(7 * T["m7"] + EPS),
                         np.log(7 * T["m28"] + EPS)])
    y = np.log(T["rv_week"].to_numpy(float) + EPS)
    return X, y, np.isfinite(X).all(1) & np.isfinite(y)


def forecast(T: pd.DataFrame, kind: str, har: dict) -> np.ndarray:
    if kind == "trailing":
        return 7 * T["m28"].to_numpy(float)
    X, _, _ = _har_xy(T)
    return np.exp(X @ np.array(har["coef"])) * har["smear"]


def cells(cfg) -> list[dict]:
    return [{"margin": m, "forecast": f, "hold": h, "tvl_floor": t, "hedge": g}
            for m in param(cfg, "s17_margin") for f in param(cfg, "s17_forecast") for h in param(cfg, "s17_hold_w")
            for t in param(cfg, "s17_tvl_floor") for g in param(cfg, "s17_hedge")]


def cell_name(c):
    return f"m{c['margin']}_{c['forecast']}_h{c['hold']}_tvl{c['tvl_floor'] / 1e6:g}M_{c['hedge']}"


def universe(T, c, k):
    ok = (T["tvl7"] >= c["tvl_floor"]).to_numpy() & np.isfinite(T["m28"].to_numpy()) & np.isfinite(T["fy7"].to_numpy())
    ok &= np.isfinite(T["rv1"].to_numpy()) & np.isfinite(T["m7"].to_numpy())
    if k > 1:
        ok &= T["v3"].to_numpy(bool) & T[f"g_none_k{k}"].notna().to_numpy()
    return ok


def cell_net(cfg, T, c, fc, k=1, capital=None):
    """Active flags and per-row gross and net (after gas, swap and perp open/close), for one cell."""
    capital = capital or param(cfg, "s17_capital_main")
    uni = universe(T, c, k)
    sig = uni & (8 * T["fy7"].to_numpy(float) >= (1 + c["margin"]) ** 2 * fc)
    act, _, _ = activate(T, sig, c["hold"])
    act = act & uni
    prev, nxt = continuity(T, act)
    gross = T[f"g_{c['hedge']}_k{k}"].to_numpy(float)
    gas, sw, pf = param(cfg, "s17_gas_usd") / capital, param(cfg, "s17_swap_cost") * 0.5, param(cfg, "s17_perp_fee")
    perp = c["hedge"] == "perp"
    cost = np.where(~prev, gas + sw + (pf * T[f"open_notional_k{k}"].to_numpy(float) if perp else 0.0), 0.0)
    cost += np.where(~nxt, gas + sw + (pf * T[f"close_notional_k{k}"].to_numpy(float) if perp else 0.0), 0.0)
    if k > 1:
        cost += np.where(prev, 2 * gas + sw, 0.0)
    return act, gross, gross - cost, uni


def weekly_series(T, act, values, weeks) -> pd.Series:
    s = pd.Series(values[act]).groupby(T["wk"].to_numpy()[act]).mean()
    return s.reindex(weeks, fill_value=0.0)


def _placebo(T, act, uni, gross, weeks, reps, rng) -> np.ndarray:
    """reps x weeks: each week, as many random pool-weeks from that week's universe as the cell holds (gross)."""
    out = np.zeros((reps, len(weeks)))
    wk = T["wk"].to_numpy()
    n_act = pd.Series(act).groupby(wk).sum()
    for j, w in enumerate(weeks):
        n = int(n_act.get(w, 0))
        if n == 0:
            continue
        cand = gross[(wk == w) & uni]
        if len(cand) == 0:
            continue
        out[:, j] = cand[rng.integers(0, len(cand), (reps, n))].mean(1)
    return out


def _t(m):
    sd = m.std(axis=-1, ddof=1)
    return np.where(sd > 0, m.mean(-1) / np.where(sd > 0, sd, 1) * np.sqrt(m.shape[-1]), 0.0)


def _period(T, cfg, which):
    end = pd.Timestamp(param(cfg, "s17_discovery_end"))
    m = (T["week"] <= end) if which == "discovery" else (T["week"] > end)
    return m.to_numpy()


def _weeks(T, mask):
    w = T.loc[mask, "wk"]
    return np.arange(int(w.min()), int(w.max()) + 1) if len(w) else np.array([], int)


def discover(cfg) -> dict:
    d = data_path(cfg, "derived", "defi")
    T = pd.read_parquet(d / "study17_pool_weeks.parquet")
    g, reps = cfg["gates"], param(cfg, "s17_placebo_reps")
    draws, lvl = param(cfg, "bootstrap_draws"), param(cfg, "ci_level")
    rng = np.random.default_rng(param(cfg, "s17_seed"))
    disc = _period(T, cfg, "discovery")
    har = fit_har(T[disc])
    D = T[disc].reset_index(drop=True)
    weeks = _weeks(D, np.ones(len(D), bool))
    fcs = {f: forecast(D, f, har) for f in param(cfg, "s17_forecast")}
    X, y, ok = _har_xy(D)
    acc = {f: float(np.corrcoef(np.log(fcs[f][ok] + EPS), y[ok])[0, 1]) for f in fcs}
    base = {}
    for h in param(cfg, "s17_hedge"):
        u = universe(D, {"tvl_floor": min(param(cfg, "s17_tvl_floor"))}, 1)
        base[h] = series_stats(weekly_series(D, u, D[f"g_{h}_k1"].to_numpy(float), weeks), draws, 1, lvl,
                               g["study17_tail_share"])
    res, real_t, plac_t = [], [], []
    for c in cells(cfg):
        act, gross, net, uni = cell_net(cfg, D, c, fcs[c["forecast"]])
        sn = weekly_series(D, act, net, weeks)
        sg_ = weekly_series(D, act, gross, weeks)
        pl = _placebo(D, act, uni, gross, weeks, reps, rng)
        p = (1 + int((pl.mean(1) >= sg_.mean()).sum())) / (1 + reps)
        active_weeks = int((pd.Series(act).groupby(D["wk"].to_numpy()).sum() > 0).sum())
        st = series_stats(sn, draws, 1, lvl, g["study17_tail_share"])
        res.append({"cell": cell_name(c), **c, "active_weeks": active_weeks, "positions": int(act.sum()),
                    "net": st, "gross_mean": float(sg_.mean()), "placebo_gross_mean": float(pl.mean()),
                    "placebo_p": p, "sig": hash(tuple(np.round(sn.to_numpy(), 12)))})
        real_t.append(float(_t(sg_.to_numpy()[None, :])[0]))
        plac_t.append(_t(pl))
    search_p = (1 + int((np.max(np.vstack(plac_t), axis=0) >= max(real_t)).sum())) / (1 + reps)
    ok_cells = [r for r in res if r["active_weeks"] >= g["study17_min_active_weeks"] and r["net"].get("lo", -1) > 0
                and r["placebo_p"] <= g["study17_p"] and r["net"]["first_half"] > 0 and r["net"]["second_half"] > 0
                and (r["net"]["without_best_tail"] or -1) > 0]
    ok_cells.sort(key=lambda r: -(r["net"]["t"] or 0))
    frozen, seen = [], set()
    if search_p <= g["study17_p"]:
        for r in ok_cells:
            if r["sig"] in seen:
                continue
            seen.add(r["sig"])
            frozen.append(r)
            if len(frozen) >= g["study17_max_frozen"]:
                break
    for r in frozen:                                                   # reported scalings (not selected on)
        c = {k: r[k] for k in ("margin", "forecast", "hold", "tvl_floor", "hedge")}
        r["by_concentration"], r["by_capital"] = {}, {}
        for k in param(cfg, "s17_concentration"):
            if k > 1:
                a, _, n, _ = cell_net(cfg, D, c, fcs[c["forecast"]], k=k)
                r["by_concentration"][str(k)] = series_stats(weekly_series(D, a, n, weeks), draws, 1, lvl,
                                                              g["study17_tail_share"])
        for cap in param(cfg, "s17_capital_usd"):
            a, _, n, _ = cell_net(cfg, D, c, fcs[c["forecast"]], capital=cap)
            r["by_capital"][str(cap)] = series_stats(weekly_series(D, a, n, weeks), draws, 1, lvl, g["study17_tail_share"])
    top = sorted(res, key=lambda r: -(r["net"].get("t") or -99))[:10]
    out = {"study": 17, "stage": "discovery", "weeks": len(weeks), "pool_weeks": int(len(D)), "har": har,
           "forecast_accuracy_corr_log": acc, "unconditional_all_pools_tvl_1M": base,
           "search_noise_p": search_p, "max_real_gross_t": max(real_t), "cells": len(res),
           "cells_passing_discovery_rules": len(ok_cells), "frozen": frozen, "top10_by_net_t": top,
           "all_cells": [{"cell": r["cell"], "mean": r["net"].get("mean"), "lo": r["net"].get("lo"),
                          "t": r["net"].get("t"), "gross": r["gross_mean"], "placebo_p": r["placebo_p"],
                          "active_weeks": r["active_weeks"]} for r in res]}
    (d / "study17_discover.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def validate(cfg) -> dict:
    d = data_path(cfg, "derived", "defi")
    marker = d / "study17_validation_done.json"
    if marker.exists():
        raise SystemExit("validation already run once (study17_validation_done.json); it is not rerun")
    disc = json.loads((d / "study17_discover.json").read_text())
    if not disc["frozen"]:
        raise SystemExit("nothing frozen in discovery: nothing to validate")
    T = pd.read_parquet(d / "study17_pool_weeks.parquet")
    g, reps = cfg["gates"], param(cfg, "s17_placebo_reps")
    draws, lvl = param(cfg, "bootstrap_draws"), param(cfg, "ci_level")
    rng = np.random.default_rng(param(cfg, "s17_seed") + 1)
    V = T[_period(T, cfg, "validation")].reset_index(drop=True)
    weeks = _weeks(V, np.ones(len(V), bool))
    out = {"study": 17, "stage": "validation", "weeks": len(weeks), "first_week": str(V["week"].min().date()),
           "last_week": str(V["week"].max().date()), "results": []}
    for r in disc["frozen"]:
        c = {k: r[k] for k in ("margin", "forecast", "hold", "tvl_floor", "hedge")}
        fc = forecast(V, c["forecast"], disc["har"])
        act, gross, net, uni = cell_net(cfg, V, c, fc)
        sn, sg_ = weekly_series(V, act, net, weeks), weekly_series(V, act, gross, weeks)
        pl = _placebo(V, act, uni, gross, weeks, reps, rng)
        p = (1 + int((pl.mean(1) >= sg_.mean()).sum())) / (1 + reps)
        st = series_stats(sn, draws, 1, lvl, g["study17_tail_share"])
        aw = int((pd.Series(act).groupby(V["wk"].to_numpy()).sum() > 0).sum())
        passed = bool(aw >= g["study17_min_active_weeks_validation"] and st.get("mean", -1) > 0 and st.get("lo", -1) > 0
                      and p <= g["study17_p"] and (st.get("without_best_tail") or -1) > 0)
        conc = {}
        for k in param(cfg, "s17_concentration"):
            if k > 1:
                a, _, n, _ = cell_net(cfg, V, c, fc, k=k)
                conc[str(k)] = series_stats(weekly_series(V, a, n, weeks), draws, 1, lvl, g["study17_tail_share"])
        out["results"].append({"cell": r["cell"], "active_weeks": aw, "positions": int(act.sum()), "net": st,
                               "gross_mean": float(sg_.mean()), "placebo_p": p, "pass": passed,
                               "by_concentration": conc})
    marker.write_text(json.dumps({"ran": pd.Timestamp.now(tz="UTC").isoformat()}))
    (d / "study17_validation.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def diagnose(cfg) -> dict:
    """Rule 6 on discovery only: where the hedged result comes from (fees, hedged impermanent loss, funding, rebalance
    fees, transaction costs), the market-path version, years, pool types, pool concentration, fee-yield tails."""
    d = data_path(cfg, "derived", "defi")
    T = pd.read_parquet(d / "study17_pool_weeks.parquet")
    if "mkt_g_perp_k1" not in T:
        raise SystemExit("rebuild first: python -m src.study17 --build (adds the components)")
    disc = json.loads((d / "study17_discover.json").read_text())
    g = cfg["gates"]
    draws, lvl = param(cfg, "bootstrap_draws"), param(cfg, "ci_level")
    rng = np.random.default_rng(param(cfg, "s17_seed") + 2)
    D = T[_period(T, cfg, "discovery")].reset_index(drop=True)
    weeks = _weeks(D, np.ones(len(D), bool))
    D["year"] = D["week"].dt.year
    specs = [("unconditional_tvl_1M_perp", None)] + [(r["cell"], {k: r[k] for k in ("margin", "forecast", "hold",
                                                                                    "tvl_floor", "hedge")})
                                                     for r in disc["frozen"]]
    out = {"study": 17, "stage": "discovery diagnose", "cells": {}}
    for name, c in specs:
        if c is None:
            act = universe(D, {"tvl_floor": min(param(cfg, "s17_tvl_floor"))}, 1)
            gross = D["g_perp_k1"].to_numpy(float)
            net, uni = gross.copy(), act
        else:
            act, gross, net, uni = cell_net(cfg, D, c, forecast(D, c["forecast"], disc["har"]))
        cost = gross - net
        comp = {"fees": D["fees_k1"], "hedged_impermanent_loss": D["value_k1"] + D["hedge_px_k1"],
                "funding": D["funding_k1"], "perp_rebalance_fees": -D["reb_k1"], "transaction_costs": -pd.Series(cost)}
        ws = lambda v: weekly_series(D, act, np.asarray(v, float), weeks)
        S = {k: float(ws(v).mean()) for k, v in comp.items()}
        net_mkt = net - gross + D["mkt_g_perp_k1"].to_numpy(float)
        res = {"components_weekly_mean": S, "net_weekly_mean": float(ws(net).mean()),
               "net_without_funding": series_stats(ws(net - D["funding_k1"].to_numpy(float)), draws, 1, lvl,
                                                   g["study17_tail_share"]),
               "net_market_path": series_stats(ws(net_mkt), draws, 1, lvl, g["study17_tail_share"]),
               "market_path_hedged_impermanent_loss": float(ws(D["mkt_value_k1"] + D["mkt_hedge_px_k1"]).mean()),
               "mean_stale_gap_of_positions": float(D.loc[act, "stale_gap"].mean())}
        if c is not None:
            gm = D["mkt_g_perp_k1"].to_numpy(float)
            pl = _placebo(D, act, uni, gm, weeks, param(cfg, "s17_placebo_reps"), rng)
            res["market_path_selection_placebo_p"] = (1 + int((pl.mean(1) >= ws(gm).mean()).sum())) / (1 + pl.shape[0])
            gf = gross - D["funding_k1"].to_numpy(float)
            pl = _placebo(D, act, uni, gf, weeks, param(cfg, "s17_placebo_reps"), rng)
            res["without_funding_selection_placebo_p"] = (1 + int((pl.mean(1) >= ws(gf).mean()).sum())) / (1 + pl.shape[0])
        A = D[act]
        res["by_year"] = {str(y): {"positions": int(len(x)), "net_per_position": float(net[act][A["year"] == y].mean()),
                                   "fees": float(x["fees_k1"].mean()), "funding": float(x["funding_k1"].mean()),
                                   "hedged_il": float((x["value_k1"] + x["hedge_px_k1"]).mean()),
                                   "mkt_hedged_il": float((x["mkt_value_k1"] + x["mkt_hedge_px_k1"]).mean())}
                          for y, x in A.groupby("year")}
        res["by_project"] = {k: {"positions": int(len(x)), "net_per_position": float(net[act][(A["project"] == k).to_numpy()].mean()),
                                 "funding": float(x["funding_k1"].mean())} for k, x in A.groupby("project")}
        contrib = pd.Series(net[act]).groupby(A["symbol"].to_numpy()).sum().sort_values(ascending=False)
        res["top_pairs_by_net"] = {k: round(float(v / contrib.sum()), 3) for k, v in contrib.head(10).items()}
        keep = ~A["symbol"].isin(contrib.head(5).index).to_numpy()
        a2 = act.copy()
        a2[np.where(act)[0][~keep]] = False
        res["net_without_top5_pairs"] = float(weekly_series(D, a2, net, weeks).mean())
        fy = A["fees_k1"] * 52
        res["fee_yield_annual_quantiles"] = {str(q): round(float(fy.quantile(q)), 3) for q in (0.5, 0.9, 0.99)}
        res["funding_annual_quantiles"] = {str(q): round(float((A["funding_k1"] * 52).quantile(q)), 3) for q in (0.1, 0.5, 0.9)}
        res["top_fee_weeks"] = [{"symbol": r.symbol, "project": r.project, "week": str(r.week.date()),
                                 "fees": round(float(r.fees_k1), 4), "stale_gap": round(float(r.stale_gap), 4)}
                                for r in A.nlargest(8, "fees_k1").itertuples()]
        out["cells"][name] = res
    (d / "study17_diagnose.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", action="store_true")
    g.add_argument("--discover", action="store_true")
    g.add_argument("--validate", action="store_true")
    g.add_argument("--diagnose", action="store_true", help="discovery only: components, market path, concentration")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    r = build(cfg) if a.build else discover(cfg) if a.discover else diagnose(cfg) if a.diagnose else validate(cfg)
    from src.analysis import to_json
    print(to_json({k: v for k, v in r.items() if k != "all_cells"}))


if __name__ == "__main__":
    main()
