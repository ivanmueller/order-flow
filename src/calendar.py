"""Sessions, the trading calendar, and the holdout seal.

Timestamps are stored in UTC; America/New_York is used only for session logic here.
Every data load goes through in_sample() so holdout days can never leak into research.

Note: this module is imported as `src.calendar`. Run code with `python -m src.<module>` from the
repo root so it never shadows the stdlib `calendar` module.
"""
from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.config import load_config

ET = ZoneInfo("America/New_York")
HOLDOUT_ENV = "GAMMA_EDGE_RUN_HOLDOUT"


class HoldoutSealed(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Holdout seal
# ---------------------------------------------------------------------------
def holdout_start(cfg: dict | None = None) -> dt.date:
    cfg = cfg or load_config()
    return pd.Timestamp(cfg["sample"]["holdout_start"]).date()


def _as_dates(x) -> pd.Series | dt.date:
    if isinstance(x, (pd.Series, pd.Index, np.ndarray, list)):
        return pd.Series(pd.to_datetime(pd.Series(x)).dt.date.values)
    return pd.Timestamp(x).date()


def in_sample(date, cfg: dict | None = None):
    """True for research dates (strictly before holdout_start). Works on scalars and vectors."""
    hs = holdout_start(cfg)
    d = _as_dates(date)
    if isinstance(d, pd.Series):
        return (d < hs).to_numpy()
    return d < hs


def holdout_unsealed() -> bool:
    """The holdout opens only when the user says "run the holdout" and sets GAMMA_EDGE_RUN_HOLDOUT=1."""
    return os.environ.get(HOLDOUT_ENV) == "1"


def seal(df: pd.DataFrame, date_col: str = "date", include_holdout: bool = False,
         cfg: dict | None = None) -> pd.DataFrame:
    """Drop holdout rows. include_holdout=True is refused unless the holdout env flag is set."""
    if include_holdout:
        if not holdout_unsealed():
            raise HoldoutSealed(
                f"Holdout requested but {HOLDOUT_ENV}=1 is not set. "
                "The holdout opens only for the final frozen run.")
        return df
    if df.empty:
        return df
    return df.loc[in_sample(df[date_col], cfg)].copy()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def to_et(ts) -> pd.Series | pd.Timestamp:
    if isinstance(ts, (pd.Series, pd.DatetimeIndex)):
        return ts.dt.tz_convert(ET) if isinstance(ts, pd.Series) else ts.tz_convert(ET)
    return pd.Timestamp(ts).tz_convert(ET)


def session_date(ts_utc: pd.Series) -> pd.Series:
    """Globex session date: bars from 18:00 ET on D-1 through 17:00 ET on D belong to D.

    Shifting ET by +6h maps 18:00 ET to midnight, so Sunday 18:00 becomes Monday.
    """
    et = ts_utc.dt.tz_convert(ET)
    return (et + pd.Timedelta(hours=6)).dt.date


def et_time(day: dt.date, hhmm: str) -> pd.Timestamp:
    """Timezone-aware ET timestamp for day at HH:MM, returned in UTC."""
    h, m = (int(x) for x in hhmm.split(":"))
    return pd.Timestamp(dt.datetime.combine(day, dt.time(h, m), ET)).tz_convert("UTC")


def minutes_of_day_et(ts_utc: pd.Series) -> pd.Series:
    et = ts_utc.dt.tz_convert(ET)
    return et.dt.hour * 60 + et.dt.minute


def hhmm_to_min(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


# ---------------------------------------------------------------------------
# Trading calendar (built once from ES bars)
# ---------------------------------------------------------------------------
def build_calendar(bars: pd.DataFrame, cfg: dict | None = None, equity_dates=None) -> pd.DataFrame:
    """One row per ES session that has RTH bars.

    Columns: date, prev_date, instrument_id (RTH front contract), roll (instrument differs from
    the previous equity session), half_day (RTH ends before the normal close), n_rth_bars,
    equity_session (SPX cash and options traded that day).

    ES trades on several equity holidays (MLK, Presidents', Memorial, Juneteenth, Labor Day,
    Thanksgiving) with an early close, while SPX cash, SPX options and OPRA are closed. Those ES-only
    sessions have no quotes, no OI and no SPX close, so they are not research sessions: `equity_dates`
    (dates with an SPX close) marks them, and prev_date / roll step over them.
    """
    cfg = cfg or load_config()
    m = cfg["market"]
    b = bars[["ts_open_utc", "instrument_id"]].reset_index(drop=True)
    b["date"] = session_date(b["ts_open_utc"])
    mod = minutes_of_day_et(b["ts_open_utc"])
    rth = b[(mod >= hhmm_to_min(m["rth_open"])) & (mod < hhmm_to_min(m["rth_close"]))].copy()
    rth["mod"] = mod[rth.index]
    g = rth.groupby("date")
    cal = pd.DataFrame({
        "instrument_id": g["instrument_id"].agg(lambda s: s.mode().iloc[0]),
        "n_rth_bars": g.size(),
        "last_rth_min": g["mod"].max(),
    }).reset_index().sort_values("date").reset_index(drop=True)
    cal["half_day"] = cal["last_rth_min"] < hhmm_to_min(m["rth_close"]) - 30
    cal["equity_session"] = True if equity_dates is None else cal["date"].isin(set(equity_dates))
    eq = cal.index[cal["equity_session"]]
    cal["prev_date"] = None
    cal.loc[eq, "prev_date"] = cal.loc[eq, "date"].shift(1)
    cal["roll"] = False
    cal.loc[eq, "roll"] = (cal.loc[eq, "instrument_id"] != cal.loc[eq, "instrument_id"].shift(1)).values
    if len(eq):
        cal.loc[eq[0], "roll"] = False
    return cal.drop(columns="last_rth_min")


def prev_trading_day(day: dt.date, cal: pd.DataFrame) -> dt.date | None:
    row = cal.loc[cal["date"] == day, "prev_date"]
    if row.empty or pd.isna(row.iloc[0]):
        return None
    return row.iloc[0]


def weekdays(start, end) -> list[dt.date]:
    """Fallback date list for ingestion before the ES calendar exists (holidays return empty)."""
    return [d.date() for d in pd.bdate_range(start, end)]
