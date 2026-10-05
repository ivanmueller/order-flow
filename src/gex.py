"""Module 2: GEX engine (SPEC.md, "Module 2").

Per session D, from D-1 closing quotes and the open interest published the morning of D:
  1. T_q (16:15 ET D-1 -> expiry) to back out vols; T_o (09:30 ET D -> expiry) to evaluate gamma.
  2. Forward F and discount D per expiry from put-call parity:  C - P = D F - D K.
  3. Black-76 implied vol from the OTM side (calls K >= F, puts K < F), one vol per strike,
     linear interpolation across strike for strikes without a valid quote, flat beyond the ends.
  4. Gamma at S0 (nearest-expiry forward), sticky strike:
        Gamma_i(S) = phi(d1) / (S sigma_i sqrt(T_o)),  d1 = (ln(S/K) + sigma^2 T_o / 2) / (sigma sqrt(T_o))
        GEX_i(S)  = s_i Gamma_i(S) OI_i 100 S^2 0.01,   s_i = +1 call, -1 put
  5. Walls, top strikes, and the flip (zero crossing of NetGEX(S) nearest S0 on a 5-pt grid).
  6. Expected move (ATM straddle, nearest expiry), GEX percentile vs previous 252 sessions,
     and ES basis B_D = ES_16:00(D-1) - SPX_close(D-1) on D's front contract.

Run: python -m src.gex --start YYYY-MM-DD --end YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import math

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import ndtr

SQRT2 = math.sqrt(2.0)
INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)

from src import calendar as calm
from src import store
from src.config import load_config, param

log = logging.getLogger("gex")

MIN_PARITY_STRIKES = 3          # need at least this many strikes to fit the parity line
DISCOUNT_SANE = (0.8, 1.05)     # reject parity fits with an implausible discount factor
IV_BOUNDS = (0.01, 5.0)


# ---------------------------------------------------------------------------
# Black-76
# ---------------------------------------------------------------------------
def _d1(F, K, T, sigma):
    return (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))


def black76_price(F, K, T, sigma, D, is_call):
    F, K, T, sigma, D = map(np.asarray, (F, K, T, sigma, D))
    d1 = _d1(F, K, T, sigma)
    d2 = d1 - sigma * np.sqrt(T)
    call = D * (F * ndtr(d1) - K * ndtr(d2))
    put = D * (K * ndtr(-d2) - F * ndtr(-d1))
    return np.where(is_call, call, put)


def _b76_scalar(F: float, K: float, T: float, sigma: float, D: float, is_call: bool) -> float:
    """Same formula as black76_price, pure-float for the root finder's inner loop."""
    sd = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    N = lambda x: 0.5 * math.erfc(-x / SQRT2)  # noqa: E731
    if is_call:
        return D * (F * N(d1) - K * N(d2))
    return D * (K * N(-d2) - F * N(-d1))


def implied_vol(price: float, F: float, K: float, T: float, D: float, is_call: bool) -> float:
    """Brent's method on [0.01, 5]. NaN when the price is outside the bracket."""
    if not (np.isfinite(price) and price > 0 and T > 0):
        return np.nan
    lo, hi = IV_BOUNDS

    def f(s):
        return _b76_scalar(F, K, T, s, D, is_call) - price

    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return np.nan
    return brentq(f, lo, hi, xtol=1e-10, rtol=1e-10, maxiter=200)


def gamma(S, K, T, sigma):
    """Undiscounted spot gamma at S (SPEC step 4)."""
    S, K, T, sigma = map(np.asarray, (S, K, T, sigma))
    d1 = (np.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
    return np.exp(-0.5 * d1 ** 2) * INV_SQRT_2PI / (S * sigma * np.sqrt(T))


def contract_gex(S, K, T, sigma, sign, oi):
    """Dollar GEX for a 1% move: s * Gamma * OI * 100 * S^2 * 0.01."""
    return sign * gamma(S, K, T, sigma) * oi * 100.0 * np.asarray(S) ** 2 * 0.01


def net_gex_curve(grid, K, T, sigma, sign, oi) -> np.ndarray:
    """NetGEX(S) for every S in grid, holding vols and T_o fixed."""
    S = np.asarray(grid, dtype=float)[:, None]
    return contract_gex(S, K[None, :], T[None, :], sigma[None, :], sign[None, :], oi[None, :]).sum(axis=1)


def find_flip(grid, net, s0) -> float:
    """Zero crossing of net over grid nearest s0, linearly interpolated. NaN if none."""
    grid, net = np.asarray(grid, float), np.asarray(net, float)
    crossings = list(grid[net == 0])
    a, b = net[:-1], net[1:]
    idx = np.where(a * b < 0)[0]
    for i in idx:
        crossings.append(grid[i] - a[i] * (grid[i + 1] - grid[i]) / (b[i] - a[i]))
    if not crossings:
        return np.nan
    crossings = np.array(crossings)
    return float(crossings[np.argmin(np.abs(crossings - s0))])


# ---------------------------------------------------------------------------
# Forward, quotes, clocks
# ---------------------------------------------------------------------------
def fit_forward(strikes, call_mid, put_mid):
    """Regress C - P on K. Returns (F, D). D = -slope, F = intercept / D."""
    K = np.asarray(strikes, float)
    y = np.asarray(call_mid, float) - np.asarray(put_mid, float)
    if len(K) < MIN_PARITY_STRIKES or np.ptp(K) == 0:
        return np.nan, np.nan
    slope, intercept = np.polyfit(K, y, 1)
    D = -slope
    if not (DISCOUNT_SANE[0] < D < DISCOUNT_SANE[1]):
        return np.nan, np.nan
    return intercept / D, D


def valid_quote_mask(q: pd.DataFrame, max_rel_spread: float) -> pd.Series:
    mid = (q["bid"] + q["ask"]) / 2
    return (q["bid"] > 0) & (q["ask"] >= q["bid"]) & ((q["ask"] - q["bid"]) / mid <= max_rel_spread)


def expiry_ts(root: str, expiration, cfg) -> pd.Timestamp:
    hhmm = cfg["market"]["spx_expiry_time"] if root == "SPX" else cfg["market"]["spxw_expiry_time"]
    return calm.et_time(pd.Timestamp(expiration).date(), hhmm)


def year_frac(t0: pd.Timestamp, t1: pd.Timestamp, cfg) -> float:
    return (t1 - t0).total_seconds() / 60.0 / cfg["market"]["minutes_per_year"]


def _wide_chain(q: pd.DataFrame) -> pd.DataFrame:
    """One row per strike with call/put bid, ask, mid and valid flags."""
    q = q.assign(mid=(q["bid"] + q["ask"]) / 2)
    w = q.pivot_table(index="strike", columns="right", values=["mid", "valid"], aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    for c in ("mid_C", "mid_P", "valid_C", "valid_P"):
        if c not in w.columns:
            w[c] = np.nan
    w["valid_C"] = w["valid_C"].fillna(False).astype(bool)
    w["valid_P"] = w["valid_P"].fillna(False).astype(bool)
    return w.sort_index()


def expiry_surface(q: pd.DataFrame, ref_close: float, Tq: float, cfg) -> dict | None:
    """Forward, discount, and a per-strike vol function for one (root, expiration)."""
    w = _wide_chain(q)
    band = param(cfg, "parity_band") * ref_close
    par = w[w["valid_C"] & w["valid_P"] & (np.abs(w.index - ref_close) <= band)]
    F, D = fit_forward(par.index.values, par["mid_C"].values, par["mid_P"].values)
    if not np.isfinite(F):
        return None
    ks, vols = [], []
    for K, r in w.iterrows():
        is_call = K >= F
        if not (r["valid_C"] if is_call else r["valid_P"]):
            continue
        iv = implied_vol(r["mid_C"] if is_call else r["mid_P"], F, K, Tq, D, is_call)
        if np.isfinite(iv):
            ks.append(K)
            vols.append(iv)
    if len(ks) < 2:
        return None
    return {"F": F, "D": D, "strikes": np.array(ks), "vols": np.array(vols), "chain": w}


def interp_vol(surface: dict, K) -> np.ndarray:
    """Linear in strike within the quoted range, flat beyond the ends."""
    return np.interp(np.asarray(K, float), surface["strikes"], surface["vols"])


def atm_straddle(surface: dict) -> float:
    w = surface["chain"]
    both = w[w["valid_C"] & w["valid_P"]]
    if both.empty:
        return np.nan
    K = both.index[np.argmin(np.abs(both.index.values - surface["F"]))]
    return float(both.loc[K, "mid_C"] + both.loc[K, "mid_P"])


# ---------------------------------------------------------------------------
# One session
# ---------------------------------------------------------------------------
def compute_day(day: dt.date, quotes: pd.DataFrame, oi: pd.DataFrame, spx_prev_close: float,
                cfg: dict, prev_day: dt.date) -> dict | None:
    """GEX summary for session `day`. quotes: D-1 EOD (both roots). oi: published morning of D.

    Returns {"row": dict, "strikes": DataFrame, "vols": DataFrame} or None if unusable.
    """
    if quotes.empty or oi.empty or not np.isfinite(spx_prev_close):
        return None
    max_dte = param(cfg, "max_dte")
    t_quote = calm.et_time(prev_day, cfg["market"]["quote_time"])
    t_open = calm.et_time(day, cfg["market"]["rth_open"])

    q = quotes.copy()
    q["expiration"] = pd.to_datetime(q["expiration"]).dt.date
    q = q[(q["expiration"] >= day) & (q["expiration"] <= day + dt.timedelta(days=max_dte))]
    q = q[~((q["symbol"] == "SPX") & (q["expiration"] == day))]  # AM-settled: gone at the open
    q["valid"] = valid_quote_mask(q, param(cfg, "max_rel_spread"))

    surfaces, vol_rows = {}, []
    for (root, exp), g in q.groupby(["symbol", "expiration"]):
        Tq = year_frac(t_quote, expiry_ts(root, exp, cfg), cfg)
        if Tq <= 0:
            continue
        s = expiry_surface(g, spx_prev_close, Tq, cfg)
        if s is None:
            continue
        surfaces[(root, exp)] = s
        vol_rows.append(pd.DataFrame({"date": day, "root": root, "expiration": exp,
                                      "strike": s["strikes"], "iv": s["vols"], "F": s["F"]}))
    if not surfaces:
        return None

    # S0 = forward of the nearest expiry (prefer SPXW on ties: it's the PM-settled daily).
    nearest = sorted(surfaces, key=lambda k: (k[1], k[0] != "SPXW"))[0]
    s0 = surfaces[nearest]["F"]
    em = atm_straddle(surfaces[nearest])

    o = oi.copy()
    o["expiration"] = pd.to_datetime(o["expiration"]).dt.date
    o = o[(o["open_interest"] > 0) & (o["expiration"] >= day)
          & (o["expiration"] <= day + dt.timedelta(days=max_dte))]
    o = o[~((o["root"] == "SPX") & (o["expiration"] == day))]
    total_oi = o["open_interest"].sum()
    keep = [k in surfaces for k in zip(o["root"], o["expiration"])]
    o = o[keep].copy()
    if o.empty:
        return None
    o["iv"] = np.nan
    o["T_o"] = np.nan
    for (root, exp), idx in o.groupby(["root", "expiration"]).groups.items():
        o.loc[idx, "iv"] = interp_vol(surfaces[(root, exp)], o.loc[idx, "strike"])
        o.loc[idx, "T_o"] = year_frac(t_open, expiry_ts(root, exp, cfg), cfg)
    o = o[o["T_o"] > 0]
    sign = np.where(o["right"].values == "C", 1.0, -1.0)
    K, T, sig, OI = (o[c].to_numpy(float) for c in ("strike", "T_o", "iv", "open_interest"))
    o["gex"] = contract_gex(s0, K, T, sig, sign, OI)

    net = o["gex"].sum()
    net_0dte = o.loc[o["expiration"] == day, "gex"].sum()

    by_k = o.groupby(["strike", "right"])["gex"].sum().unstack(fill_value=0.0)
    for c in ("C", "P"):
        if c not in by_k.columns:
            by_k[c] = 0.0
    by_k["total"] = by_k["C"] + by_k["P"]
    call_wall = float(by_k["C"].idxmax()) if (by_k["C"] > 0).any() else np.nan
    put_wall = float(by_k["P"].abs().idxmax()) if (by_k["P"] != 0).any() else np.nan
    near = by_k[np.abs(by_k.index - s0) <= em] if np.isfinite(em) else by_k.iloc[0:0]
    tops = list(near["total"].abs().sort_values(ascending=False).index[:3]) + [np.nan] * 3

    hw, step = param(cfg, "flip_half_width"), param(cfg, "flip_step")
    grid = np.arange(s0 * (1 - hw), s0 * (1 + hw) + 1e-9, step)
    curve = net_gex_curve(grid, K, T, sig, sign, OI)
    flip = find_flip(grid, curve, s0)

    row = {
        "date": day, "s0": s0, "em": em, "net_gex": net, "net_gex_0dte": net_0dte, "flip": flip,
        "call_wall": call_wall, "put_wall": put_wall,
        "top1": tops[0], "top2": tops[1], "top3": tops[2],
        "spx_prev_close": spx_prev_close, "n_contracts": len(o), "n_expiries": len(surfaces),
        "oi_used_share": o["open_interest"].sum() / total_oi if total_oi else np.nan,
        "nearest_root": nearest[0], "nearest_exp": nearest[1],
    }
    strikes = by_k.reset_index().rename(columns={"C": "call_gex", "P": "put_gex"})
    strikes.insert(0, "date", day)
    vols = pd.concat(vol_rows, ignore_index=True)
    return {"row": row, "strikes": strikes, "vols": vols}


# ---------------------------------------------------------------------------
# Panel-level pieces
# ---------------------------------------------------------------------------
def pct_rank_prev(values: pd.Series, lookback: int, min_periods: int) -> pd.Series:
    """Percentile rank (0..1) of each value among the previous `lookback` values (ties count half)."""
    v = values.to_numpy(float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        prev = v[max(0, i - lookback):i]
        prev = prev[np.isfinite(prev)]
        if len(prev) < min_periods or not np.isfinite(v[i]):
            continue
        out[i] = ((prev < v[i]).sum() + 0.5 * (prev == v[i]).sum()) / len(prev)
    return pd.Series(out, index=values.index)


def es_close_at_spx_close(bars_day: pd.DataFrame, cfg) -> tuple[float, int]:
    """ES close of the last RTH bar of a session (16:00 ET, or the early close on half days)."""
    mod = calm.minutes_of_day_et(bars_day["ts_open_utc"])
    rth = bars_day[(mod >= calm.hhmm_to_min(cfg["market"]["rth_open"]))
                   & (mod < calm.hhmm_to_min(cfg["market"]["rth_close"]))]
    if rth.empty:
        return np.nan, -1
    last = rth.sort_values("ts_open_utc").iloc[-1]
    return float(last["close"]), int(last["instrument_id"])


def compute_basis(cal: pd.DataFrame, bars: pd.DataFrame, daily: pd.DataFrame, cfg) -> pd.DataFrame:
    """B_D = ES(D-1 close, D's front contract) - SPX_close(D-1).

    On roll days the D-1 ES close must come from D's contract, which ES.v.0 bars don't carry;
    it is read from data/raw/es/roll_basis (python -m src.ingest_futures roll-basis).
    """
    spx = daily.set_index("date")["spx_close"]
    by_day = {d: g for d, g in bars.groupby("date")}
    out = []
    for r in cal.itertuples():
        if pd.isna(r.prev_date) or r.prev_date not in by_day:
            out.append((r.date, np.nan, "no_prev"))
            continue
        es_px, inst = es_close_at_spx_close(by_day[r.prev_date], cfg)
        src = "bars"
        if inst != r.instrument_id:
            p = store.roll_basis_path(cfg, r.date)
            if p.exists():
                rb = store.read(p)
                es_px, src = float(rb["close"].iloc[-1]), "roll_file"
            else:
                es_px, src = np.nan, "roll_missing"
        out.append((r.date, es_px - spx.get(r.prev_date, np.nan), src))
    return pd.DataFrame(out, columns=["date", "basis", "basis_src"])


def build(start=None, end=None, cfg=None, include_holdout: bool = False, save: bool = True) -> pd.DataFrame:
    """Build gex_daily for [start, end]. save=False computes in memory only (robustness reruns)."""
    cfg = cfg or load_config()
    cal = store.load_calendar(cfg, include_holdout)
    daily = store.load_daily(cfg, include_holdout)
    spx = daily.set_index("date")["spx_close"]
    days = store.date_range_filter(cal, start, end)
    rows, strikes, vols = [], [], []
    for r in days.itertuples():
        if pd.isna(r.prev_date):
            continue
        res = compute_day(r.date, store.load_options_eod(cfg, r.prev_date), store.load_oi(cfg, r.date),
                          float(spx.get(r.prev_date, np.nan)), cfg, r.prev_date)
        if res is None:
            log.warning("%s: no usable GEX (missing quotes/OI/close)", r.date)
            continue
        rows.append(res["row"])
        strikes.append(res["strikes"])
        vols.append(res["vols"])
        log.info("%s net_gex=%.3g flip=%.1f em=%.1f", r.date, res["row"]["net_gex"],
                 res["row"]["flip"], res["row"]["em"])
    g = pd.DataFrame(rows)
    if g.empty:
        return g
    # Merge with any previously built days so the percentile always sees the full history.
    p = store.derived_path(cfg, "gex_daily")
    if save and p.exists():
        old = store.read(p)
        old["date"] = pd.to_datetime(old["date"]).dt.date
        g = pd.concat([old[~old["date"].isin(g["date"])], g], ignore_index=True)
    g = g.sort_values("date").reset_index(drop=True)
    lb, mp = param(cfg, "gex_pct_lookback"), param(cfg, "gex_pct_min_periods")
    g["gex_pct"] = pct_rank_prev(g["net_gex"], lb, mp)
    g["gex_pct_0dte"] = pct_rank_prev(g["net_gex_0dte"], lb, mp)
    bars = store.load_bars(cfg, include_holdout)
    basis = compute_basis(cal, bars, daily, cfg)
    g = g.drop(columns=[c for c in ("basis", "basis_src") if c in g]).merge(basis, on="date", how="left")
    if not save:
        return g
    store.save_derived(g, "gex_daily", cfg)
    _append(cfg, "gex_strikes", strikes)
    _append(cfg, "gex_vols", vols)
    return g


def _append(cfg, name, parts):
    new = pd.concat(parts, ignore_index=True)
    p = store.derived_path(cfg, name)
    if p.exists():
        old = store.read(p)
        old["date"] = pd.to_datetime(old["date"]).dt.date
        new = pd.concat([old[~old["date"].isin(new["date"].unique())], new], ignore_index=True)
    store.save_derived(new.sort_values("date"), name, cfg)


# ---------------------------------------------------------------------------
# Gate 0 validation
# ---------------------------------------------------------------------------
# Diagnostic only (not a tunable): which ES print does the option-implied forward S0 track?
# 16:00 = SPX cash close, 16:15 = SPX regular-session close, 17:00 = Cboe curb-session close.
# ES halts 16:15-16:30 ET, so 16:30 would read the same bar as 16:15.
QUOTE_TIME_CANDIDATES = ("16:00", "16:15", "16:45", "17:00")


def _es_at(b: pd.DataFrame, mod: pd.Series, hhmm: str) -> float:
    """Close of the last bar that ends at or before hhmm ET (bars are keyed by open time)."""
    x = b[mod < calm.hhmm_to_min(hhmm)]
    return float(x["close"].iloc[-1]) if not x.empty else np.nan


def forward_vs_es(gex_daily: pd.DataFrame, bars: pd.DataFrame, cal: pd.DataFrame, cfg) -> pd.DataFrame:
    """Per-day diagnostic for check 1.

    SPX cash closes at 16:00 but the EOD option quotes are stamped later, so S0 should track the ES
    print at the quote time on D-1 minus the day's basis, not the 16:00 cash close. For each
    candidate time t: es_<t> is that ES print and r_<t> = S0 - (es_<t> - B_D). `resid` is r at the
    configured quote_time. Roll days are skipped (D-1 bars are on the old contract).
    """
    times = list(QUOTE_TIME_CANDIDATES)
    qt = cfg["market"]["quote_time"]
    if qt not in times:
        times.append(qt)
    by_day = {d: x for d, x in bars.groupby("date")}
    c = cal.set_index("date")
    rows = []
    for r in gex_daily.itertuples():
        if r.date not in c.index or pd.isna(c.loc[r.date, "prev_date"]):
            continue
        prev = c.loc[r.date, "prev_date"]
        if prev not in by_day or not np.isfinite(r.basis):
            continue
        b = by_day[prev]
        b = b[b["instrument_id"] == c.loc[r.date, "instrument_id"]].sort_values("ts_open_utc")
        if b.empty:
            continue
        mod = calm.minutes_of_day_et(b["ts_open_utc"])
        row = {"date": r.date, "spx_prev_close": r.spx_prev_close, "s0": r.s0,
               "s0_minus_spx": r.s0 - r.spx_prev_close, "basis": r.basis}
        for t in times:
            es = _es_at(b, mod, t)
            k = t.replace(":", "")
            row[f"es_{k}"] = es
            row[f"r_{k}"] = r.s0 - (es - r.basis)
        row["resid"] = row[f"r_{qt.replace(':', '')}"]
        row["nearest"] = f"{r.nearest_root} {r.nearest_exp}"
        rows.append(row)
    return pd.DataFrame(rows)


def validate(gex_daily: pd.DataFrame, daily: pd.DataFrame, vols: pd.DataFrame | None, cfg,
             bars: pd.DataFrame | None = None, cal: pd.DataFrame | None = None) -> dict:
    """The automatable Gate 0 checks. Check 3 (match public GEX charts) is manual.

    Check 1 compares S0 with the ES print at market.quote_time on D-1 minus the basis: the EOD quotes
    are stamped at the Cboe curb close (17:00 ET), after the 16:00 cash close, so the cash close is
    the wrong reference (pilot diagnostic, RUNLOG 2026-10-05). The cash-close comparison is still
    reported as information. Without bars/cal the cash-close version decides 1_pass.
    """
    v = cfg["validation"]
    tol, share_req = v["forward_tol_pts"], v["forward_share"]
    g = gex_daily.copy()
    out = {}
    dev = (g["s0"] - g["spx_prev_close"]).abs()
    out["1_info_vs_spx_cash_close_median_abs"] = float(dev.median())
    out[f"1_info_vs_spx_cash_close_within_{tol:g}pt_share"] = float((dev <= tol).mean())
    f = forward_vs_es(g, bars, cal, cfg) if bars is not None and cal is not None else pd.DataFrame()
    if f.empty:
        out["1_reference"] = "SPX cash close (no bars given)"
        out["1_pass"] = bool((dev <= tol).mean() >= share_req)
    else:
        qt = cfg["market"]["quote_time"]
        out["1_reference"] = f"ES at {qt} on D-1 minus basis"
        out["1_forward_vs_es_median_abs"] = float(f["resid"].abs().median())
        out["1_forward_vs_es_median_signed"] = float(f["resid"].median())
        share = float((f["resid"].abs() <= tol).mean())
        out[f"1_forward_vs_es_within_{tol:g}pt_share"] = share
        out["1_n_days"] = len(f)
        out["1_pass"] = bool(share >= share_req)
        best, best_med = None, np.inf
        for t in QUOTE_TIME_CANDIDATES:
            k = t.replace(":", "")
            if f"r_{k}" not in f:
                continue
            med = float(f[f"r_{k}"].abs().median())
            out[f"1_scan_{t}_median_abs"] = med
            out[f"1_scan_{t}_within_{tol:g}pt_share"] = float((f[f"r_{k}"].abs() <= tol).mean())
            if med < best_med:
                best, best_med = t, med
        out["1_scan_best_time"] = best
        out["1_scan_configured"] = qt
    if vols is not None and not vols.empty:
        vv = vols.merge(g[["date", "nearest_root", "nearest_exp"]], on="date")
        vv = vv[(vv["root"] == vv["nearest_root"]) & (vv["expiration"] == vv["nearest_exp"])]
        lo = vv[vv["strike"] < 0.98 * vv["F"]].groupby("date")["iv"].mean()
        hi = vv[vv["strike"] > 1.02 * vv["F"]].groupby("date")["iv"].mean()
        skew = (lo - hi).dropna()
        out["2_put_vol_above_call_vol_share"] = float((skew > 0).mean()) if len(skew) else np.nan

        def rough(x):
            x = x.sort_values("strike")["iv"].to_numpy()
            return np.nanmedian(np.abs(np.diff(x, 2))) if len(x) > 3 else np.nan
        out["2_smile_median_abs_2nd_diff"] = float(vv.groupby("date").apply(rough).median())
    m = g.merge(daily[["date", "vix_close"]], left_on="date", right_on="date", how="left")
    # VIX for D-1 is the comparable (both prior close); shift VIX by one session.
    m["vix_prev"] = m["date"].map(_prev_value(daily, "vix_close"))
    x = m["em"] / m["s0"]
    y = m["vix_prev"] / 100 / np.sqrt(252)
    ok = x.notna() & y.notna()
    out["4_corr_em_vs_vix"] = float(np.corrcoef(x[ok], y[ok])[0, 1]) if ok.sum() > 2 else np.nan
    out["4_median_ratio_em_over_vix_daily"] = float((x[ok] / y[ok]).median()) if ok.any() else np.nan
    out["n_days"] = len(g)
    out["first_day"] = str(g["date"].min())
    out["last_day"] = str(g["date"].max())
    out["net_gex_negative_share"] = float((g["net_gex"] < 0).mean())
    out["net_gex_median_bn"] = float(g["net_gex"].median() / 1e9)
    out["n_contracts_median"] = float(g["n_contracts"].median()) if "n_contracts" in g else np.nan
    out["oi_used_share_mean"] = float(g["oi_used_share"].mean()) if "oi_used_share" in g else np.nan
    out["flip_missing_share"] = float(g["flip"].isna().mean())
    out["basis_missing_share"] = float(g["basis"].isna().mean()) if "basis" in g else np.nan
    return out


SHOW_COLS = ["date", "s0", "em", "net_gex_bn", "net_gex_0dte_bn", "gex_pct", "flip", "call_wall", "put_wall",
             "top1", "top2", "top3", "basis"]


def show(gex_daily: pd.DataFrame, dates) -> pd.DataFrame:
    """Rows for check 3 (compare sign, walls and flip with a public GEX chart for those dates)."""
    g = gex_daily[gex_daily["date"].isin({store.as_date(d) for d in dates})].copy()
    g["net_gex_bn"] = g["net_gex"] / 1e9
    g["net_gex_0dte_bn"] = g["net_gex_0dte"] / 1e9
    return g[[c for c in SHOW_COLS if c in g]]


def _prev_value(daily: pd.DataFrame, col: str) -> dict:
    d = daily.sort_values("date")
    return dict(zip(d["date"], d[col].shift(1)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--include-holdout", action="store_true")
    ap.add_argument("--validate-only", action="store_true", help="re-run the checks on the saved table")
    ap.add_argument("--diagnose", action="store_true", help="print the per-day forward-vs-ES table")
    ap.add_argument("--show", help="comma-separated dates: print levels for the check-3 chart comparison")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    start = a.start or cfg["sample"]["start"]
    end = a.end or cfg["sample"]["end"]
    if a.validate_only:
        g = store.date_range_filter(store.load_derived("gex_daily", cfg, a.include_holdout), start, end)
    else:
        g = build(start, end, cfg, a.include_holdout)
    if g.empty:
        print("No GEX rows built.")
        return
    daily = store.load_daily(cfg, a.include_holdout)
    vols = store.load_derived("gex_vols", cfg, a.include_holdout)
    bars = store.load_bars(cfg, a.include_holdout)
    cal = store.load_calendar(cfg, a.include_holdout)
    for k, v in validate(g, daily, vols, cfg, bars, cal).items():
        print(f"{k:40s} {v}")
    with pd.option_context("display.width", 220, "display.max_rows", 500, "display.float_format", "{:.2f}".format):
        if a.diagnose:
            print(forward_vs_es(g, bars, cal, cfg).to_string(index=False))
        if a.show:
            print(show(g, a.show.split(",")).to_string(index=False))


if __name__ == "__main__":
    main()
