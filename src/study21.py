"""Study 21 pilot: Piotroski F-score in cheap US microcaps, SURVIVORS ONLY, discovery years (STUDY21.md).

Free and deliberately incomplete: only companies that still trade today can be priced (yfinance, current ticker from
the SEC submissions file). Survivorship removes most weak companies that failed, which narrows the strong-vs-average
gap, and it inflates every absolute return; so the pilot judges only RELATIVE results and can only ADVANCE (buy the
full price file with delisted stocks) or report "no advance" (Matteo decides: inconclusive vs close).

Approved design choices (Matteo 2026-10-12): a company is scored when >= 7 of 9 signals exist; strong = passes
>= 7/9 of its available signals, weak <= 2/9. Pilot = 10-Ks filed 2013-2018 (discovery); prices are read only up to
s21_pilot_price_end, so validation (2019-22 filings) and the sealed holdout (2023-24) stay untouched.

  python -m src.study21 --prices      # download (cached, resumable) yfinance prices for the candidate tickers
  python -m src.study21 --pilot       # survivors-only pilot report + gate
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from src.config import data_path, load_config, param

log = logging.getLogger("study21")
SIG = ["s_roa", "s_cfo", "s_droa", "s_accrual", "s_dlever", "s_dliquid", "s_eq", "s_dmargin", "s_dturn"]
EPS = 1e-9


# ---- scoring and universe -------------------------------------------------------------------------------------
def score(fy: pd.DataFrame, cfg) -> pd.DataFrame:
    g = fy.copy()
    n = g["n_signals"]
    share9 = g[SIG].sum(axis=1, skipna=True) / n.where(n > 0) * 9
    g["score9"] = share9
    g["eligible"] = n >= param(cfg, "s21_min_signals")
    g["strong"] = g["eligible"] & (share9 >= param(cfg, "s21_strong_min") - EPS)
    g["weak"] = g["eligible"] & (share9 <= param(cfg, "s21_weak_max") + EPS)
    return g


def universe(fy: pd.DataFrame, cfg, start: str, end: str) -> pd.DataFrame:
    """Non-financial, float in range, positive book, scored; `cheap` = top book-to-float share within the filing year."""
    g = score(fy, cfg)
    sic = pd.to_numeric(g.get("sic"), errors="coerce")
    keep = (g["filed"] >= pd.Timestamp(start)) & (g["filed"] <= pd.Timestamp(end)) & g["eligible"] \
        & sic.notna() & ~sic.between(6000, 6999) \
        & g["float_usd"].between(param(cfg, "s21_float_min"), param(cfg, "s21_float_max")) \
        & (g["equity"] > 0) & g["bm"].notna()
    U = g[keep].copy()
    q = param(cfg, "s21_bm_quantile")
    yr = U["filed"].dt.year
    U["cheap"] = U["bm"] >= U.groupby(yr)["bm"].transform(lambda b: b.quantile(q)) - EPS
    return U.reset_index(drop=True)


def ticker(tickers_now) -> str | None:
    t = str(tickers_now or "").split(",")[0].strip()
    return t.replace(".", "-") if t else None


# ---- positions and portfolios -----------------------------------------------------------------------------------
def position(filed: pd.Timestamp, px: pd.DataFrame, cfg, end: pd.Timestamp) -> dict | None:
    """Buy at the close of the s21_entry_lag_days-th trading day after the filing date; hold s21_hold_days trading days
    (or to the last price on or before `end`). Filters known at entry: price and median dollar volume over the window
    ending at the entry day. Round-trip cost from that dollar volume."""
    px = px[px.index <= end]
    after = np.flatnonzero(px.index > filed)
    lag = param(cfg, "s21_entry_lag_days")
    if len(after) < lag:
        return None
    i = after[lag - 1]
    w = param(cfg, "s21_adv_window")
    if i + 1 < w:
        return None
    close = px["close"].to_numpy(float)
    dv = float(np.median(close[i + 1 - w:i + 1] * px["volume"].to_numpy(float)[i + 1 - w:i + 1]))
    if not close[i] >= param(cfg, "s21_min_price") or not dv >= param(cfg, "s21_min_dollar_volume"):
        return None
    j = min(i + param(cfg, "s21_hold_days"), len(px) - 1)
    if j <= i:
        return None
    cost = next(c for cap, c in param(cfg, "s21_cost_buckets") if dv < cap)
    return {"entry": px.index[i], "entry_px": close[i], "exit": px.index[j], "exit_px": close[j], "adv": dv,
            "cost": cost, "series": px["close"].iloc[i:j + 1]}


def monthly_legs(series: pd.Series, cost: float) -> pd.Series:
    """Monthly returns of one held position: entry close -> each month's last close -> exit close; half the
    round-trip cost in the first month, half in the last."""
    s_ = series.dropna()
    me = s_.groupby(s_.index.to_period("M")).tail(1)
    pts = pd.concat([s_.iloc[:1], me, s_.iloc[-1:]])
    pts = pts[~pts.index.duplicated()].sort_index()
    r = (pts / pts.shift(1) - 1).iloc[1:]
    r.index = r.index.to_period("M")
    r = r.groupby(level=0).apply(lambda x: (1 + x).prod() - 1)
    if r.empty:
        return r
    r.iloc[0] -= cost / 2
    r.iloc[-1] -= cost / 2
    return r


def portfolio(legs: list[pd.Series]) -> pd.DataFrame:
    """Equal weight across positions held in each month (monthly rebalanced, calendar time)."""
    if not legs:
        return pd.DataFrame(columns=["ret", "names"])
    M = pd.concat(legs, axis=1)
    return pd.DataFrame({"ret": M.mean(axis=1), "names": M.notna().sum(axis=1)}).sort_index()


def block_lb(x: np.ndarray, level: float, block: int, reps: int, seed: int) -> tuple[float, float]:
    """One-sided lower bound (and upper) of the mean by moving-block bootstrap."""
    rng = np.random.default_rng(seed)
    n = len(x)
    if n < block * 2:
        return np.nan, np.nan
    starts = rng.integers(0, n - block + 1, size=(reps, int(np.ceil(n / block))))
    idx = (starts[..., None] + np.arange(block)).reshape(reps, -1)[:, :n]
    means = x[idx].mean(axis=1)
    return float(np.quantile(means, 1 - level)), float(np.quantile(means, level))


def _stats(d: pd.Series, cfg) -> dict:
    x = d.dropna().to_numpy(float)
    lo, hi = block_lb(x, param(cfg, "s21_lb_level"), param(cfg, "s21_block_months"), param(cfg, "s21_boot_reps"),
                      param(cfg, "s21_seed"))
    h = len(x) // 2
    return {"months": len(x), "mean_month": float(x.mean()) if len(x) else np.nan,
            "annual_x12": float(x.mean() * 12) if len(x) else np.nan, "lb": lo, "ub": hi,
            "first_half": float(x[:h].mean()) if h else np.nan, "second_half": float(x[h:].mean()) if h else np.nan,
            "months_positive": float((x > 0).mean()) if len(x) else np.nan}


# ---- data ---------------------------------------------------------------------------------------------------------
def _candidates(cfg) -> pd.DataFrame:
    fy = pd.read_parquet(data_path(cfg, "derived", "edgar_firm_years.parquet"))
    s0, s1 = param(cfg, "s21_pilot_filed")
    U = universe(fy, cfg, s0, s1)
    U["ticker"] = U["tickers_now"].map(ticker) if "tickers_now" in U else None
    return U


def fetch_prices(cfg, batch: int = 50, tickers: list[str] | None = None) -> dict:
    """yfinance daily (split/dividend adjusted) for every cheap-universe survivor (or the given tickers); cached per
    ticker, resumable."""
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("pip install yfinance")
    U = _candidates(cfg)
    tick = tickers if tickers is not None else sorted(set(U.loc[U["cheap"] & U["ticker"].notna(), "ticker"]))
    root = data_path(cfg, "raw", "yf_prices")
    root.mkdir(parents=True, exist_ok=True)
    missing_file = root / "_no_data.json"
    nodata = set(json.loads(missing_file.read_text())) if missing_file.exists() else set()
    todo = [t for t in tick if not (root / f"{t}.parquet").exists() and t not in nodata]
    start, end = param(cfg, "s21_price_start"), param(cfg, "s21_pilot_price_end")
    for k in range(0, len(todo), batch):
        part = todo[k:k + batch]
        df = yf.download(part, start=start, end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                         auto_adjust=True, progress=False, group_by="ticker", threads=True)
        for t in part:
            try:
                d = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                d = d[["Close", "Volume"]].dropna()
            except Exception:
                d = pd.DataFrame()
            if d.empty:
                nodata.add(t)
                continue
            out = pd.DataFrame({"close": d["Close"].astype(float), "volume": d["Volume"].astype(float)})
            out.index = pd.DatetimeIndex(out.index).tz_localize(None)
            out.to_parquet(root / f"{t}.parquet")
        missing_file.write_text(json.dumps(sorted(nodata)))
        print(f"  tickers {min(k + batch, len(todo))}/{len(todo)}", flush=True)
    have = sum((root / f"{t}.parquet").exists() for t in tick)
    return {"cheap_universe_firm_years": int(U["cheap"].sum()),
            "cheap_with_current_ticker": int((U["cheap"] & U["ticker"].notna()).sum()),
            "tickers": len(tick), "with_prices": have, "no_data": len(nodata & set(tick))}


def pilot(cfg) -> dict:
    U = _candidates(cfg)
    C = U[U["cheap"]].copy()
    root = data_path(cfg, "raw", "yf_prices")
    end = pd.Timestamp(param(cfg, "s21_pilot_price_end"))
    legs = {"strong": [], "weak": [], "all": []}
    gross = {"strong": [], "weak": [], "all": []}
    counts = {"cheap_firm_years": len(C), "with_ticker": int(C["ticker"].notna().sum()), "priced": 0,
              "failed_entry_filters": 0, "positions": 0}
    for _, r in C[C["ticker"].notna()].iterrows():
        f = root / f"{r['ticker']}.parquet"
        if not f.exists():
            continue
        px = pd.read_parquet(f)
        px = px[px.index <= end]                                     # seal: nothing after the pilot window
        counts["priced"] += 1
        p = position(r["filed"], px, cfg, end)
        if p is None:
            counts["failed_entry_filters"] += 1
            continue
        counts["positions"] += 1
        net, gr = monthly_legs(p["series"], p["cost"]), monthly_legs(p["series"], 0.0)
        for grp in ("all",) + (("strong",) if r["strong"] else ()) + (("weak",) if r["weak"] else ()):
            legs[grp].append(net)
            gross[grp].append(gr)
    P = {k: portfolio(v) for k, v in legs.items()}
    G = {k: portfolio(v) for k, v in gross.items()}
    out = {"counts": counts,
           "positions_by_group": {k: len(v) for k, v in legs.items()},
           "avg_names_held": {k: float(P[k]["names"].mean()) if len(P[k]) else 0.0 for k in P},
           "strong_minus_all_net": _stats(P["strong"]["ret"] - P["all"]["ret"], cfg),
           "strong_minus_weak_gross": _stats(G["strong"]["ret"] - G["weak"]["ret"], cfg),
           "strong_minus_all_gross": _stats(G["strong"]["ret"] - G["all"]["ret"], cfg),
           "survivor_inflated_absolute_net": {k: _stats(P[k]["ret"], cfg) for k in P}}
    a, b = out["strong_minus_all_net"], out["strong_minus_weak_gross"]
    gate = cfg["gates"]
    out["gate"] = {
        "strong_beats_all_after_costs": bool(a["mean_month"] > 0 and a["lb"] > 0),
        "strong_beats_weak_gross": bool(b["mean_month"] > 0),
        "enough_names": bool(out["avg_names_held"]["strong"] >= gate["study21_pilot_min_names"]),
        "lb_level": param(cfg, "s21_lb_level")}
    out["verdict"] = "ADVANCE" if all(v for k, v in out["gate"].items() if k != "lb_level") else "NO ADVANCE"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prices", action="store_true")
    g.add_argument("--pilot", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    r = fetch_prices(cfg) if a.prices else pilot(cfg)
    print(json.dumps(r, indent=1, default=str))


if __name__ == "__main__":
    main()
