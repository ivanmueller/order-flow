"""Study 20 data: Cboe VIX futures (VX) daily settlements, free, 2004+ (STUDY20.md).

Monthly VX contracts expire on the Wednesday 30 days before the third Friday of the next month (a day earlier
when that Friday is an exchange holiday). Files are fetched per contract and saved under data/raw/vix_futures/
(idempotent: existing files are skipped; a 404 is remembered in _missing.txt). Two URL patterns are tried:
  new      https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_<YYYY-MM-DD>.csv
  archive  https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_<M><YY>_VX.csv   (older contracts)
Before 2007-03-26 VX traded as 10 x VIX ("VBI"); those prices are divided by 10 on parsing.

  python -m src.vix_futures --probe        # which patterns answer (a handful of requests)
  python -m src.vix_futures                # fetch 2004-03..2026-12 contracts (~275 files), then build the panel
  python -m src.vix_futures --panel        # rebuild data/derived/vix_futures_panel.parquet from the files only
Holdout rows are kept in the raw files (prices only) but every Study 20 load seals them by s20_holdout_start.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import io
import json
import logging
import time
from pathlib import Path

import pandas as pd

from src.config import data_path, load_config

log = logging.getLogger("vix_futures")
NEW = "https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{d}.csv"
ARCHIVE = "https://cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_{m}{yy}_VX.csv"
MONTH_CODES = "FGHJKMNQUVXZ"
VBI_END = dt.date(2007, 3, 26)          # contract size change: VX quoted as 10 x VIX before this date


def third_friday(y: int, m: int) -> dt.date:
    c = calendar.Calendar()
    fridays = [d for d in c.itermonthdates(y, m) if d.month == m and d.weekday() == 4]
    return fridays[2]


def expiry_for(y: int, m: int) -> dt.date:
    """Standard expiry of the y-m monthly contract (holiday shifts are handled by trying the day before too)."""
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return third_friday(ny, nm) - dt.timedelta(days=30)


def parse(text: str, expiry: dt.date) -> pd.DataFrame:
    lines = text.strip().splitlines()
    start = next(i for i, l in enumerate(lines) if l.lower().startswith("trade date"))
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    df.columns = [c.strip().lower() for c in df.columns]
    out = pd.DataFrame({"date": pd.to_datetime(df["trade date"]).dt.date,
                        "settle": pd.to_numeric(df["settle"], errors="coerce"),
                        "volume": pd.to_numeric(df.get("total volume", 0), errors="coerce"),
                        "oi": pd.to_numeric(df.get("open interest", 0), errors="coerce")})
    out = out[out["settle"] > 0].copy()
    old = out["date"] < VBI_END
    out.loc[old, "settle"] = out.loc[old, "settle"] / 10.0
    out["expiry"] = expiry
    return out.reset_index(drop=True)


def front_second(df: pd.DataFrame) -> pd.DataFrame:
    """Per date: the two nearest contracts expiring AFTER that date (the expiring contract leaves the panel on its
    expiry day, when it settles to the special opening quotation and cannot be held over the close)."""
    rows = []
    for d, g in df.groupby("date"):
        g = g[g["expiry"] > d].sort_values("expiry")
        if len(g) >= 2:
            rows.append({"date": d, "vx1": float(g["settle"].iloc[0]), "exp1": g["expiry"].iloc[0],
                         "vx2": float(g["settle"].iloc[1]), "exp2": g["expiry"].iloc[1]})
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


# ---- fetching --------------------------------------------------------------------------------------------------------
def _root(cfg) -> Path:
    return data_path(cfg, "raw", "vix_futures")


def _get(url: str):
    import requests
    for k in range(4):
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (research script)"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
        except Exception as e:                                   # transient: retry, then give up on this file
            last = e
            time.sleep(2 * 2 ** k)
    raise RuntimeError(f"{url}: {last}")


def candidates(y: int, m: int) -> list[str]:
    e = expiry_for(y, m)
    return [NEW.format(d=e.isoformat()), NEW.format(d=(e - dt.timedelta(days=1)).isoformat()),
            ARCHIVE.format(m=MONTH_CODES[m - 1], yy=f"{y % 100:02d}")]


def fetch(cfg, first=(2004, 3), last=(2026, 12)) -> dict:
    root = _root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    miss_f = root / "_missing.txt"
    missing = set(miss_f.read_text().split()) if miss_f.exists() else set()
    counts = {"have": 0, "fetched": 0, "not_found": 0}
    y, m = first
    while (y, m) <= last:
        out = root / f"VX_{y}-{m:02d}.csv"
        if out.exists():
            counts["have"] += 1
        else:
            got = None
            e = expiry_for(y, m)
            for url, exp in zip(candidates(y, m), (e, e - dt.timedelta(days=1), None)):
                if url in missing:
                    continue
                text = _get(url)
                if text and "trade date" in text.lower():
                    got = (url, text, exp)
                    break
                missing.add(url)
            if got:
                tag = got[2].isoformat() if got[2] else "from-last-trade-date"
                out.write_text(f"# source {got[0]}\n# expiry {tag}\n" + got[1])
                counts["fetched"] += 1
            else:
                counts["not_found"] += 1
        m += 1
        if m == 13:
            y, m = y + 1, 1
    miss_f.write_text("\n".join(sorted(missing)))
    return counts


def build_panel(cfg) -> dict:
    parts = []
    for f in sorted(_root(cfg).glob("VX_*.csv")):
        text = f.read_text()
        head = [l for l in text.splitlines()[:3] if l.startswith("# expiry")]
        tag = head[0].split()[-1] if head else ""
        exp = dt.date.fromisoformat(tag) if tag[:2] in ("19", "20") else None
        body = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        d = parse(body, exp)
        if len(d):
            last = d["date"].max()                               # archive files: the last trade date is the expiry
            if exp is None or abs((last - exp).days) > 3:
                d["expiry"] = last
            parts.append(d)
    df = pd.concat(parts, ignore_index=True).drop_duplicates(["date", "expiry"], keep="last")
    P = front_second(df)
    out = data_path(cfg, "derived", "vix_futures_panel.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    P.to_parquet(out)
    df.to_parquet(data_path(cfg, "derived", "vix_futures_long.parquet"))     # per contract (Study 20's index)
    return {"contracts": len(parts), "days": int(len(P)), "first": str(P["date"].min()), "last_file_date":
            str(P["date"].max()), "note": "panel includes dates after the Study 20 holdout start; loads seal them"}


def probe() -> dict:
    out = {}
    for (y, m) in ((2024, 1), (2018, 2), (2010, 6), (2006, 3)):
        out[f"{y}-{m:02d}"] = {}
        for url in candidates(y, m):
            try:
                t = _get(url)
                out[f"{y}-{m:02d}"][url.split("/")[-1]] = "ok" if t and "trade date" in t.lower() else "missing"
            except Exception as e:
                out[f"{y}-{m:02d}"][url.split("/")[-1]] = f"error: {str(e)[-120:]}"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--panel", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    if a.probe:
        r = probe()
    elif a.panel:
        r = build_panel(cfg)
    else:
        r = {"fetch": fetch(cfg), "panel": build_panel(cfg)}
    print(json.dumps(r, indent=1, default=str))


if __name__ == "__main__":
    main()
