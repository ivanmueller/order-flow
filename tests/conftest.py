import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.config import load_config

ET = "America/New_York"


@pytest.fixture
def cfg():
    return load_config()


def make_bars(day: dt.date, closes, start="09:00", instrument_id=1, spread=0.25):
    """1-minute bars from a close path: open = previous close, high/low = max/min(open, close) +/- spread."""
    closes = np.asarray(closes, float)
    t0 = pd.Timestamp(f"{day} {start}", tz=ET).tz_convert("UTC")
    ts = t0 + pd.to_timedelta(np.arange(len(closes)), unit="min")
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({"ts_open_utc": ts, "open": opens,
                         "high": np.maximum(opens, closes) + spread, "low": np.minimum(opens, closes) - spread,
                         "close": closes, "volume": 100, "instrument_id": instrument_id})


def make_trades(rows, day: dt.date):
    """rows: (HH:MM:SS, price, size, side)."""
    out = pd.DataFrame(rows, columns=["t", "price", "size", "side"])
    out["ts_event_utc"] = pd.to_datetime(str(day) + " " + out["t"]).dt.tz_localize(ET).dt.tz_convert("UTC")
    out["sequence"] = np.arange(len(out))
    out["instrument_id"] = 1
    return out[["ts_event_utc", "price", "size", "side", "instrument_id", "sequence"]]


@pytest.fixture(autouse=True)
def _default_config(monkeypatch):
    """Tests always start from config.yaml, even if a developer's .env selects the pilot."""
    from src.config import DEFAULT_PATH
    monkeypatch.setenv("GAMMA_EDGE_CONFIG", str(DEFAULT_PATH))
