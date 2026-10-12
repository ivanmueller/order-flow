"""Study 21 step 0 (free): how many US microcaps can we score with Piotroski's F-score, point in time, from EDGAR?

Source: SEC bulk files (no key; the SEC asks for a User-Agent with a name and email, read from SEC_USER_AGENT in .env):
  companyfacts.zip  every XBRL fact every filer ever reported, with the filing (accn) and its filing date
  submissions.zip   per company SIC code (to set financials aside)
Both are cached under data/raw/edgar/ (skipped if present; --refresh re-downloads).

Point in time: a firm-year is one original 10-K. Every value used for it was filed ON OR BEFORE that 10-K's filing
date, and when a number was reported more than once (restatements, comparatives) the FIRST-filed version is used.
Dead companies stay in (companyfacts covers every filer). Firm-years filed on or after holdout_start are dropped
(in_sample). This step reports counts only -- no prices, no returns.

  python -m src.edgar_inventory --limit 500     # quick check on the first 500 companies
  python -m src.edgar_inventory                 # everything (a few GB of downloads, then ~10-30 min)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from src.calendar import holdout_start, in_sample
from src.config import data_path, load_config, load_env_file

log = logging.getLogger("edgar_inventory")
URLS = {"companyfacts.zip": "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
        "submissions.zip": "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"}

# Piotroski (2000) inputs; tags in priority order (first tag present for a period wins)
CONCEPTS = {
    "ni": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "assets": ["Assets"],
    "ca": ["AssetsCurrent"],
    "cl": ["LiabilitiesCurrent"],
    "ltd": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
            "LongTermNotesPayable"],
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "gp": ["GrossProfit"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "shares": ["WeightedAverageNumberOfSharesOutstandingBasic"],
}
FLOWS = {"ni", "cfo", "rev", "gp", "cogs", "shares"}            # duration facts (one fiscal year)
DUR = (330, 380)                                                # days: an annual duration, 52/53-week years included
TOL = pd.Timedelta(days=20)                                     # period-end matching tolerance
SIGNALS = ["s_roa", "s_cfo", "s_droa", "s_accrual", "s_dlever", "s_dliquid", "s_eq", "s_dmargin", "s_dturn"]
DAY = 86_400 * 10**9


# ---- facts --------------------------------------------------------------------------------------------------
def _facts(cf: dict) -> dict[str, dict[str, np.ndarray]]:
    """Per variable: arrays of (rank, start, end, filed, val, accn) from 10-K-family filings only."""
    gaap = cf.get("facts", {}).get("us-gaap", {})
    out = {}
    for var, tags in CONCEPTS.items():
        rows = []
        for rank, tag in enumerate(tags):
            for unit_rows in gaap.get(tag, {}).get("units", {}).values():
                for x in unit_rows:
                    if not str(x.get("form", "")).startswith("10-K") or x.get("val") is None:
                        continue
                    rows.append((rank, x.get("start"), x["end"], x["filed"], float(x["val"]), x["accn"]))
        if not rows:
            continue
        r = list(zip(*rows))
        start = pd.to_datetime(pd.Series(r[1], dtype="object"), errors="coerce")
        out[var] = {"rank": np.array(r[0]),
                    "start": start.to_numpy("datetime64[ns]").astype("int64"),
                    "has_start": start.notna().to_numpy(),
                    "end": pd.to_datetime(pd.Series(r[2])).to_numpy("datetime64[ns]").astype("int64"),
                    "filed": pd.to_datetime(pd.Series(r[3])).to_numpy("datetime64[ns]").astype("int64"),
                    "val": np.array(r[4]), "accn": np.array(r[5])}
    return out


def _lookup(f: dict | None, var: str, end: pd.Timestamp, filed_by: pd.Timestamp) -> float:
    """First-filed value of `var` for the fiscal period ending near `end`, among facts filed by `filed_by`."""
    if f is None:
        return np.nan
    m = (f["filed"] <= filed_by.value) & (np.abs(f["end"] - end.value) <= TOL.value)
    if var in FLOWS:
        dur = (f["end"] - f["start"]) / DAY
        m &= f["has_start"] & (dur >= DUR[0]) & (dur <= DUR[1])
    else:
        m &= ~f["has_start"]
    if not m.any():
        return np.nan
    idx = np.flatnonzero(m)
    best = idx[f["rank"][idx] == f["rank"][idx].min()]
    return float(f["val"][best[np.argmin(f["filed"][best])]])


def _dei_float(cf: dict, accn: str) -> float:
    rows = [x for u in cf.get("facts", {}).get("dei", {}).get("EntityPublicFloat", {}).get("units", {}).values()
            for x in u if x.get("accn") == accn and x.get("val") is not None]
    return float(max(rows, key=lambda x: x["end"])["val"]) if rows else np.nan


def _sig(cond, *inputs) -> float:
    return np.nan if any(np.isnan(v) for v in inputs) else float(cond)


def firm_years(cf: dict) -> pd.DataFrame:
    """One row per original 10-K: inputs at t, t-1, t-2 (point in time, first reported) and the nine signals."""
    F = _facts(cf)
    filings: dict[str, int] = {}
    for f in F.values():
        for a, fl in zip(f["accn"], f["filed"]):
            filings[a] = min(filings.get(a, fl), fl)
    gaap = cf.get("facts", {}).get("us-gaap", {})
    forms = {x["accn"]: x["form"] for t in gaap.values() for u in t.get("units", {}).values() for x in u}
    out = []
    for accn, filed_ns in filings.items():
        if forms.get(accn, "").endswith("/A"):
            continue
        filed = pd.Timestamp(filed_ns)
        ends = [f["end"][(f["accn"] == accn) & f["has_start"] & ((f["end"] - f["start"]) / DAY >= DUR[0])]
                for v, f in F.items() if v in FLOWS]
        ends = np.concatenate(ends) if ends else np.array([], dtype="int64")
        if not len(ends):
            inst = [f["end"][f["accn"] == accn] for v, f in F.items() if v not in FLOWS]
            ends = np.concatenate(inst) if inst else ends
        if not len(ends):
            continue
        fye = pd.Timestamp(int(ends.max()))
        t1, t2 = fye - pd.DateOffset(years=1), fye - pd.DateOffset(years=2)
        g = {f"{v}{s}": _lookup(F.get(v), v, d, filed) for v in CONCEPTS for s, d in (("", fye), ("_1", t1))}
        g["assets_2"] = _lookup(F.get("assets"), "assets", t2, filed)
        ltd_seen = "ltd" in F and bool((F["ltd"]["filed"] <= filed.value).any())
        if not ltd_seen:
            g["ltd"], g["ltd_1"] = 0.0, 0.0
        for s in ("", "_1"):
            if np.isnan(g[f"gp{s}"]):
                g[f"gp{s}"] = g[f"rev{s}"] - g[f"cogs{s}"]
        a, a1, a2 = g["assets"], g["assets_1"], g["assets_2"]
        roa, roa1 = g["ni"] / a1, g["ni_1"] / a2
        cfoa = g["cfo"] / a1
        lev, lev1 = g["ltd"] / ((a + a1) / 2), g["ltd_1"] / ((a1 + a2) / 2)
        cr, cr1 = g["ca"] / g["cl"], g["ca_1"] / g["cl_1"]
        gm, gm1 = g["gp"] / g["rev"], g["gp_1"] / g["rev_1"]
        tu, tu1 = g["rev"] / a1, g["rev_1"] / a2
        s = {"s_roa": _sig(roa > 0, roa), "s_cfo": _sig(cfoa > 0, cfoa),
             "s_droa": _sig(roa > roa1, roa, roa1), "s_accrual": _sig(cfoa > roa, cfoa, roa),
             "s_dlever": _sig(lev < lev1, lev, lev1), "s_dliquid": _sig(cr > cr1, cr, cr1),
             "s_eq": _sig(g["shares"] <= g["shares_1"], g["shares"], g["shares_1"]),
             "s_dmargin": _sig(gm > gm1, gm, gm1), "s_dturn": _sig(tu > tu1, tu, tu1)}
        n = sum(not np.isnan(v) for v in s.values())
        fl = _dei_float(cf, accn)
        out.append({"cik": cf.get("cik"), "accn": accn, "filed": filed, "fye": fye, **g, **s,
                    "ltd_imputed": not ltd_seen, "n_signals": n,
                    "fscore": float(sum(s.values())) if n == 9 else np.nan,
                    "float_usd": fl, "bm": g["equity"] / fl if fl and fl > 0 else np.nan})
    return pd.DataFrame(out)


# ---- summary ------------------------------------------------------------------------------------------------
def _lab(x: float) -> str:
    return f"{x / 1e9:g}b" if x >= 1e9 else f"{x / 1e6:g}m"


def summarize(fy: pd.DataFrame, last_filed_cut: pd.Timestamp, buckets: list[float]) -> dict:
    fy = fy.copy()
    fy["fin"] = fy["sic"].between(6000, 6999) if "sic" in fy else False
    edges = [0.0, *buckets, np.inf]
    names = [f"float_lt_{_lab(buckets[0])}"] + [f"float_{_lab(a)}_{_lab(b)}" for a, b in zip(buckets, buckets[1:])] \
        + [f"float_ge_{_lab(buckets[-1])}"]
    by = {}
    for yr, d in fy.groupby(fy["filed"].dt.year):
        nf = d[~d["fin"]]
        full = nf[nf["n_signals"] == 9]
        row = {"firm_years": len(d), "financials": int(d["fin"].sum()), "nonfinancial": len(nf),
               "all_9_signals": len(full), "ge_7_signals": int((nf["n_signals"] >= 7).sum()),
               "ltd_imputed_share": round(float(full["ltd_imputed"].mean()), 3) if len(full) else None,
               "float_missing": int(full["float_usd"].isna().sum()),
               "positive_book_and_float": int((full["bm"] > 0).sum())}
        cut = pd.cut(full["float_usd"], edges, labels=names, right=False)
        row.update({k: int(v) for k, v in cut.value_counts().reindex(names, fill_value=0).items()})
        micro = full[full["float_usd"] < buckets[1]] if len(buckets) > 1 else full
        row["micro_fscore_hist"] = {int(k): int(v) for k, v in micro["fscore"].value_counts().sort_index().items()}
        row["micro_f_ge_8"] = int((micro["fscore"] >= 8).sum())
        by[str(int(yr))] = row
    last = fy.groupby("cik")["filed"].max()
    cov = {c: round(float(fy[c].notna().mean()), 3) for c in SIGNALS}
    return {"by_filed_year": by, "signal_coverage_all_firm_years": cov,
            "companies": {"with_10k_firm_years": int(len(last)),
                          "stopped_filing_before_cut": int((last < last_filed_cut).sum()),
                          "cut": str(last_filed_cut.date())}}


# ---- download and run ----------------------------------------------------------------------------------------
def _ua() -> str:
    load_env_file()
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise SystemExit("Set SEC_USER_AGENT in .env, e.g. SEC_USER_AGENT=Your Name you@example.com "
                         "(the SEC requires a contact in the User-Agent; it is never logged).")
    return ua


def download(name: str, root: Path, refresh: bool = False) -> Path:
    import requests
    dest = root / name
    if dest.exists() and not refresh:
        return dest
    root.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    log.info("downloading %s ...", name)
    with requests.get(URLS[name], headers={"User-Agent": _ua(), "Accept-Encoding": "gzip, deflate"},
                      stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    return dest


def _cf_chunk(args) -> pd.DataFrame:
    path, names = args
    parts = []
    with zipfile.ZipFile(path) as z:
        for n in names:
            try:
                d = firm_years(json.loads(z.read(n)))
            except Exception as e:                                  # malformed company file: count, don't stop
                log.warning("skip %s: %s", n, e)
                continue
            if len(d):
                parts.append(d)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _sic_chunk(args) -> list[tuple]:
    path, names = args
    out = []
    with zipfile.ZipFile(path) as z:
        for n in names:
            try:
                js = json.loads(z.read(n))
                out.append((int(js.get("cik")), int(js.get("sic") or 0) or None,
                            ",".join(js.get("tickers") or []), ",".join(e or "" for e in js.get("exchanges") or [])))
            except Exception:
                continue
    return out


def _chunks(names: list[str], k: int) -> list[list[str]]:
    size = max(1, len(names) // (k * 8) + 1)
    return [names[i:i + size] for i in range(0, len(names), size)]


def run(cfg, limit: int | None, jobs: int, sic: bool, refresh: bool) -> dict:
    root = data_path(cfg, "raw", "edgar")
    cf_zip = download("companyfacts.zip", root, refresh)
    with zipfile.ZipFile(cf_zip) as z:
        names = sorted(n for n in z.namelist() if n.endswith(".json"))
    names = names[:limit] if limit else names
    with ProcessPoolExecutor(jobs) as ex:
        parts = [p for p in ex.map(_cf_chunk, [(cf_zip, c) for c in _chunks(names, jobs)]) if len(p)]
    fy = pd.concat(parts, ignore_index=True)
    fy = fy[in_sample(fy["filed"], cfg)].reset_index(drop=True)          # seal: nothing filed on/after holdout_start
    if sic:
        sub_zip = download("submissions.zip", root, refresh)
        with zipfile.ZipFile(sub_zip) as z:
            ciks = {f"CIK{int(c):010d}.json" for c in fy["cik"].unique()}
            snames = [n for n in z.namelist() if n in ciks]
        with ProcessPoolExecutor(jobs) as ex:
            rows = [r for part in ex.map(_sic_chunk, [(sub_zip, c) for c in _chunks(snames, jobs)]) for r in part]
        meta = pd.DataFrame(rows, columns=["cik", "sic", "tickers_now", "exchanges_now"])
        fy = fy.merge(meta, on="cik", how="left")
    out = data_path(cfg, "derived")
    out.mkdir(parents=True, exist_ok=True)
    fy.to_parquet(out / "edgar_firm_years.parquet", index=False)
    s = summarize(fy, last_filed_cut=pd.Timestamp("2024-01-01"), buckets=[50e6, 300e6, 2e9])
    s["companies"]["files_read"] = len(names)
    s["sealed_from"] = str(holdout_start(cfg))
    if sic:
        nt = fy.drop_duplicates("cik")
        s["companies"]["without_a_current_ticker"] = int((nt["tickers_now"].fillna("") == "").sum())
    (out / "edgar_inventory.json").write_text(json.dumps(s, indent=1))
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only the first N companies (quick check)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--no-sic", action="store_true", help="skip submissions.zip (no financials split)")
    ap.add_argument("--refresh", action="store_true", help="re-download the bulk files")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(run(load_config(), a.limit, a.jobs, not a.no_sic, a.refresh), indent=1))


if __name__ == "__main__":
    main()
