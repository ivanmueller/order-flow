"""Study 21b pilot: buy strong, low-coverage microcaps shortly BEFORE their earnings (STUDY21.md), survivors only.

Earnings date = the earlier of the 8-K earnings release (item 2.02) and the 10-Q/10-K filing for that period (many
microcaps never issue a release; the filing is the news). Expected date = this period's end + last year's lag for the
same quarter (known in advance). Buy at the close k trading days before the expected date (skipped if the company has
already reported by then); sell at the close 2 trading days after the actual announcement, or 15 trading days after
the expected date if no report has come. Strong/eligible from the latest 10-K filed before the entry day. Same price,
liquidity and cost rules as the Study 21 pilot; discovery announcements 2013-2018 only, no price after 2019-12-31.

Gated variants (family of 4 with the Study 21 pilot): k = 5, 10, 20. Other k are reported, not gated.

  python -m src.study21b --events     # earnings events from the cached SEC submissions.zip (free)
  python -m src.study21b --prices     # yfinance prices for the universe survivors (cached, resumable)
  python -m src.study21b --run        # pilot report + gate
"""
from __future__ import annotations

import argparse
import json
import logging
import zipfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from src import study21 as s21
from src.config import data_path, load_config, param

log = logging.getLogger("study21b")
PERIODIC = {"10-Q", "10-K", "10-KT", "10-QT", "10-K405", "10-KSB", "10-QSB"}


# ---- earnings events --------------------------------------------------------------------------------------------
def parse_submissions(js: dict, cik: int | None = None) -> pd.DataFrame:
    """Filings table from a submissions JSON (main file: filings.recent; paging file: arrays at the top)."""
    rec = js.get("filings", {}).get("recent", js)
    n = len(rec.get("form", []))
    cik = cik if cik is not None else int(js.get("cik", 0) or 0)
    return pd.DataFrame({"cik": [cik] * n, "accn": rec.get("accessionNumber", [None] * n),
                         "form": rec.get("form", []),
                         "filed": pd.to_datetime(pd.Series(rec.get("filingDate", [None] * n), dtype="object"),
                                                 errors="coerce"),
                         "report": pd.to_datetime(pd.Series(rec.get("reportDate", [None] * n), dtype="object"),
                                                  errors="coerce"),
                         "items": [str(x or "") for x in rec.get("items", [""] * n)]})


def earnings_events(F: pd.DataFrame, cfg) -> pd.DataFrame:
    """One row per periodic report: period end, filing date, announcement = min(filing, 8-K 2.02 release filed after
    the period end, on or before the filing, within s21b_8k_window_days of it)."""
    win = pd.Timedelta(days=param(cfg, "s21b_8k_window_days"))
    out = []
    for cik, g in F.groupby("cik"):
        per = g[g["form"].isin(PERIODIC) & g["report"].notna() & g["filed"].notna()]
        per = per.sort_values("filed").drop_duplicates("report", keep="first")
        rel = g[(g["form"] == "8-K") & g["items"].str.contains("2.02", regex=False)]["filed"].dropna().sort_values()
        for _, r in per.iterrows():
            cand = rel[(rel > r["report"]) & (rel <= r["filed"]) & (rel >= r["filed"] - win)]
            ann = min(cand.min(), r["filed"]) if len(cand) else r["filed"]
            out.append({"cik": cik, "period": r["report"], "form": r["form"], "filed": r["filed"], "ann": ann,
                        "release": bool(len(cand))})
    return pd.DataFrame(out, columns=["cik", "period", "form", "filed", "ann", "release"])


def expected_dates(E: pd.DataFrame, cfg) -> pd.DataFrame:
    """expected = period end + (last year's announcement - last year's period end), same quarter (+/- tolerance)."""
    tol = pd.Timedelta(days=param(cfg, "s21b_prior_tol_days"))
    E = E.sort_values(["cik", "period"]).reset_index(drop=True)
    exp = []
    for cik, g in E.groupby("cik", sort=False):
        per, ann = g["period"].to_numpy(), g["ann"].to_numpy()
        for p in per:
            target = p - np.timedelta64(365, "D")
            k = np.flatnonzero(np.abs(per - target) <= tol)
            exp.append(p + (ann[k[0]] - per[k[0]]) if len(k) else np.datetime64("NaT", "ns"))
    E["expected"] = pd.to_datetime(pd.Series(exp, index=E.index))
    return E


# ---- trades -------------------------------------------------------------------------------------------------------
def trade(expected: pd.Timestamp, ann: pd.Timestamp, px: pd.DataFrame, k: int, cfg) -> dict | None:
    idx = px.index
    e = int(idx.searchsorted(expected))                       # first trading day on/after the expected date
    i = e - k
    if i < 0 or e >= len(idx):
        return None
    if ann <= idx[i]:                                         # already reported by the entry day: no trade
        return None
    w = param(cfg, "s21_adv_window")
    if i + 1 < w:
        return None
    close, vol = px["close"].to_numpy(float), px["volume"].to_numpy(float)
    dv = float(np.median(close[i + 1 - w:i + 1] * vol[i + 1 - w:i + 1]))
    if not close[i] >= param(cfg, "s21_min_price") or not dv >= param(cfg, "s21_min_dollar_volume"):
        return None
    a = int(idx.searchsorted(ann))
    j = min(a + param(cfg, "s21b_exit_after_days"), e + param(cfg, "s21b_no_report_exit_days"), len(idx) - 1)
    if j <= i:
        return None
    cost = next(c for cap, c in param(cfg, "s21_cost_buckets") if dv < cap)
    gross = close[j] / close[i] - 1
    return {"entry": idx[i], "exit": idx[j], "gross": gross, "cost": cost, "net": gross - cost, "adv": dv}


def latest_10k(fy: pd.DataFrame, cik: int, date: pd.Timestamp, cfg):
    """The company's latest 10-K filed strictly before `date` (not older than s21b_fy_max_age_days)."""
    g = fy[(fy["cik"] == cik) & (fy["filed"] < date)] if "cik" in fy else fy[fy["filed"] < date]
    if g.empty:
        return None
    r = g.loc[g["filed"].idxmax()]
    return r if (date - r["filed"]).days <= param(cfg, "s21b_fy_max_age_days") else None


# ---- data ---------------------------------------------------------------------------------------------------------
def _scored_fy(cfg) -> pd.DataFrame:
    """Every firm-year, scored; `in_univ` = the low-coverage universe (scored, non-financial, float in range,
    positive book; no cheapness filter)."""
    fy = pd.read_parquet(data_path(cfg, "derived", "edgar_firm_years.parquet"))
    g = s21.score(fy, cfg)
    sic = pd.to_numeric(g.get("sic"), errors="coerce")
    g["in_univ"] = g["eligible"] & sic.notna() & ~sic.between(6000, 6999) & (g["equity"] > 0) \
        & g["float_usd"].between(param(cfg, "s21_float_min"), param(cfg, "s21_float_max"))
    g["ticker"] = g["tickers_now"].map(s21.ticker) if "tickers_now" in g else None
    return g


def _universe_fy(cfg) -> pd.DataFrame:
    g = _scored_fy(cfg)
    return g[g["in_univ"]].copy()


def _events_chunk(args) -> list[pd.DataFrame]:
    path, names, ciks = args
    out = []
    with zipfile.ZipFile(path) as z:
        for n in names:
            try:
                out.append(parse_submissions(json.loads(z.read(n)), cik=int(n[3:13])))
            except Exception:
                continue
    return out


def build_events(cfg, jobs: int = 4) -> dict:
    U = _universe_fy(cfg)
    ciks = set(int(c) for c in U["cik"].unique())
    sub = data_path(cfg, "raw", "edgar", "submissions.zip")
    with zipfile.ZipFile(sub) as z:
        names = [n for n in z.namelist() if n.endswith(".json") and int(n[3:13]) in ciks]
    size = max(1, len(names) // (jobs * 8) + 1)
    chunks = [names[i:i + size] for i in range(0, len(names), size)]
    with ProcessPoolExecutor(jobs) as ex:
        parts = [d for res in ex.map(_events_chunk, [(sub, c, None) for c in chunks]) for d in res]
    F = pd.concat(parts, ignore_index=True)
    E = expected_dates(earnings_events(F, cfg), cfg)
    s0, s1 = param(cfg, "s21_pilot_filed")
    E = E[(E["ann"] >= pd.Timestamp(s0) - pd.Timedelta(days=60)) & (E["ann"] <= pd.Timestamp(s1))]   # seal
    E.to_parquet(data_path(cfg, "derived", "s21b_events.parquet"), index=False)
    return {"companies": len(ciks), "events": len(E), "with_expected": int(E["expected"].notna().sum()),
            "with_8k_release": float(E["release"].mean())}


def fetch(cfg) -> dict:
    U = _universe_fy(cfg)
    s0, s1 = param(cfg, "s21_pilot_filed")
    U = U[(U["filed"] >= pd.Timestamp(s0) - pd.Timedelta(days=456)) & (U["filed"] <= pd.Timestamp(s1))]
    return s21.fetch_prices(cfg, tickers=sorted(set(U["ticker"].dropna())))


def _month_series(T: pd.DataFrame, col: str) -> pd.Series:
    return T.groupby(T["entry"].dt.to_period("M"))[col].mean()


def run(cfg) -> dict:
    G = _scored_fy(cfg)
    by_cik = {c: g.sort_values("filed") for c, g in G.groupby("cik")}
    U = G[G["in_univ"]]
    E = pd.read_parquet(data_path(cfg, "derived", "s21b_events.parquet"))
    E = E[E["expected"].notna()]
    s0, s1 = param(cfg, "s21_pilot_filed")
    end = pd.Timestamp(param(cfg, "s21_pilot_price_end"))
    root = data_path(cfg, "raw", "yf_prices")
    tick = U.drop_duplicates("cik").set_index("cik")["ticker"]
    gated, curve = param(cfg, "s21b_entry_days_gated"), param(cfg, "s21b_entry_days_curve")
    ks = sorted(set(gated) | set(curve))
    rows, counts = [], {"events": len(E), "no_ticker": 0, "no_prices": 0, "no_10k": 0}
    cache: dict[str, pd.DataFrame] = {}
    for _, ev in E.iterrows():
        t = tick.get(ev["cik"])
        if not isinstance(t, str):
            counts["no_ticker"] += 1
            continue
        if t not in cache:
            f = root / f"{t}.parquet"
            cache[t] = pd.read_parquet(f).loc[:end] if f.exists() else None
        px = cache[t]
        if px is None or px.empty:
            counts["no_prices"] += 1
            continue
        for k in ks:
            tr = trade(ev["expected"], ev["ann"], px, k, cfg)
            if tr is None or not (pd.Timestamp(s0) <= tr["entry"] <= pd.Timestamp(s1)):
                continue
            f10 = latest_10k(by_cik.get(ev["cik"], G.iloc[:0]), ev["cik"], tr["entry"], cfg)
            if f10 is None or not bool(f10["in_univ"]):              # latest 10-K must put it in the universe
                counts["no_10k"] += 1
                continue
            rows.append({**tr, "k": k, "cik": ev["cik"], "strong": bool(f10["strong"]), "weak": bool(f10["weak"]),
                         "release": ev["release"]})
    T = pd.DataFrame(rows)
    out = {"counts": counts, "variants": {}, "curve": {}}
    min_trades = cfg["gates"]["study21b_min_trades"]
    for k in ks:
        Tk = T[T["k"] == k]
        S, A = Tk[Tk["strong"]], Tk
        ms, ma = _month_series(S, "net"), _month_series(A, "net")
        st = s21._stats(ms, cfg)
        diff = s21._stats((ms - ma).dropna(), cfg)
        row = {"trades_strong": len(S), "trades_all": len(A),
               "strong_net_per_trade": float(S["net"].mean()) if len(S) else np.nan,
               "strong_gross_per_trade": float(S["gross"].mean()) if len(S) else np.nan,
               "all_net_per_trade": float(A["net"].mean()) if len(A) else np.nan,
               "all_gross_per_trade": float(A["gross"].mean()) if len(A) else np.nan,
               "weak_gross_per_trade": float(Tk[Tk["weak"]]["gross"].mean()) if Tk["weak"].any() else np.nan,
               "strong_hit_rate_net": float((S["net"] > 0).mean()) if len(S) else np.nan,
               "avg_cost": float(S["cost"].mean()) if len(S) else np.nan,
               "strong_monthly": st, "strong_minus_all_monthly": diff}
        if k in gated:
            row["gate"] = {"strong_net_positive": bool(st["mean_month"] > 0 and st["lb"] > 0),
                           "strong_beats_all": bool(diff["mean_month"] > 0),
                           "enough_trades": bool(len(S) >= min_trades)}
            row["verdict"] = "ADVANCE" if all(row["gate"].values()) else "NO ADVANCE"
            out["variants"][f"k{k}"] = row
        else:
            out["curve"][f"k{k}"] = {x: row[x] for x in ("trades_strong", "strong_gross_per_trade",
                                                          "strong_net_per_trade", "all_gross_per_trade")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--events", action="store_true")
    g.add_argument("--prices", action="store_true")
    g.add_argument("--run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    r = build_events(cfg) if a.events else fetch(cfg) if a.prices else run(cfg)
    print(json.dumps(r, indent=1, default=str))


if __name__ == "__main__":
    main()
