"""Parquet read/write helpers. Every research load is filtered through calendar.seal()."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from src import calendar as cal_mod
from src.config import data_path, load_config


def ymd(day) -> str:
    return pd.Timestamp(day).strftime("%Y%m%d")


def write(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)  # atomic: a crash never leaves a half-written file that looks complete
    return path


def read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# Raw
# ---------------------------------------------------------------------------
def eod_path(cfg, quote_date, symbol) -> Path:
    return data_path(cfg, "raw", "options", "eod", f"{ymd(quote_date)}_{symbol}.parquet")


def oi_path(cfg, as_of_date, symbol) -> Path:
    return data_path(cfg, "raw", "options", "oi", f"{ymd(as_of_date)}_{symbol}.parquet")


def bars_path(cfg, month: str) -> Path:
    return data_path(cfg, "raw", "es", "bars", f"{month}.parquet")


def roll_basis_path(cfg, day) -> Path:
    return data_path(cfg, "raw", "es", "roll_basis", f"{ymd(day)}.parquet")


def trades_path(cfg, touch_id: str) -> Path:
    return data_path(cfg, "raw", "es", "trades", f"{touch_id}.parquet")


def daily_path(cfg) -> Path:
    return data_path(cfg, "raw", "daily", "spx_vix.parquet")


def derived_path(cfg, name: str) -> Path:
    # Raw downloads are shared across configs; derived tables are per config (pilot vs main).
    return data_path(cfg, cfg["data"].get("derived_dir", "derived"), f"{name}.parquet")


def load_options_eod(cfg, quote_date) -> pd.DataFrame:
    """Quotes from the D-1 EOD report, both roots. Sealing is by the session D that uses them."""
    parts = [read(p) for s in cfg["data"]["thetadata_symbols"]
             if (p := eod_path(cfg, quote_date, s)).exists()]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def load_oi(cfg, as_of_date) -> pd.DataFrame:
    roots = [p.split(".")[0] for p in cfg["data"]["opra_parents"]]
    parts = [read(p) for r in roots if (p := oi_path(cfg, as_of_date, r)).exists()]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def load_bars(cfg=None, include_holdout: bool = False) -> pd.DataFrame:
    """All ES 1-minute bars with a session `date` column, sealed by session date."""
    cfg = cfg or load_config()
    files = sorted(data_path(cfg, "raw", "es", "bars").glob("*.parquet"))
    if not files:
        raise FileNotFoundError("No ES bars. Run: python -m src.ingest_futures bars ...")
    bars = pd.concat([read(f) for f in files], ignore_index=True)
    bars = bars.drop_duplicates("ts_open_utc").sort_values("ts_open_utc").reset_index(drop=True)
    bars["date"] = cal_mod.session_date(bars["ts_open_utc"])
    return cal_mod.seal(bars, "date", include_holdout, cfg).reset_index(drop=True)


def load_daily(cfg=None, include_holdout: bool = False) -> pd.DataFrame:
    cfg = cfg or load_config()
    d = read(daily_path(cfg))
    d["date"] = pd.to_datetime(d["date"]).dt.date
    return cal_mod.seal(d, "date", include_holdout, cfg).reset_index(drop=True)


def load_derived(name: str, cfg=None, include_holdout: bool = False) -> pd.DataFrame:
    cfg = cfg or load_config()
    df = read(derived_path(cfg, name))
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = cal_mod.seal(df, "date", include_holdout, cfg)
    return df.reset_index(drop=True)


def save_derived(df: pd.DataFrame, name: str, cfg=None) -> Path:
    cfg = cfg or load_config()
    return write(df, derived_path(cfg, name))


def load_calendar(cfg=None, include_holdout: bool = False) -> pd.DataFrame:
    return load_derived("calendar", cfg, include_holdout)


def date_range_filter(df: pd.DataFrame, start=None, end=None, col: str = "date") -> pd.DataFrame:
    if start is not None:
        df = df[df[col] >= pd.Timestamp(start).date()]
    if end is not None:
        df = df[df[col] <= pd.Timestamp(end).date()]
    return df


def as_date(x) -> dt.date:
    return pd.Timestamp(x).date()
