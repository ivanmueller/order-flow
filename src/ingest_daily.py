"""Ingest SPX and VIX daily closes from FRED (no key needed).

Run: python -m src.ingest_daily [--force]
Output: data/raw/daily/spx_vix.parquet  (date, spx_close, vix_close)
"""
from __future__ import annotations

import argparse
import io

import pandas as pd
import requests

from src import store
from src.config import load_config

SERIES = {"SP500": "spx_close", "VIXCLS": "vix_close"}


def parse_fred_csv(text: str, series: str) -> pd.DataFrame:
    """FRED CSVs use either DATE or observation_date, and '.' for missing values."""
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    out = pd.DataFrame({"date": pd.to_datetime(df[date_col]).dt.date,
                        SERIES[series]: pd.to_numeric(df[series], errors="coerce")})
    return out.dropna()


def fetch(cfg) -> pd.DataFrame:
    frames = []
    for series in SERIES:
        r = requests.get(cfg["data"]["fred_url"].format(series=series), timeout=60)
        r.raise_for_status()
        frames.append(parse_fred_csv(r.text, series))
    df = frames[0].merge(frames[1], on="date", how="outer").sort_values("date")
    return df[df["date"] >= pd.Timestamp("2019-01-01").date()].reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download even if the file exists")
    a = ap.parse_args(argv)
    cfg = load_config()
    path = store.daily_path(cfg)
    if path.exists() and not a.force:
        print(f"exists: {path} (use --force to refresh)")
        return
    df = fetch(cfg)
    store.write(df, path)
    print(f"wrote {len(df)} rows -> {path}; last date {df['date'].max()}")


if __name__ == "__main__":
    main()
