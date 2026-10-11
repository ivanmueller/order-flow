"""Study 20: the rebuilt short-term VIX futures index, ETF-like returns, signals with the one-day lag, stats, gate."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study20 as s20

D = [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10)]       # trading days


def _long(prices):
    """prices: {expiry: [settle per day in D]} (NaN = not trading)."""
    rows = []
    for e, ps in prices.items():
        for d, p in zip(D, ps):
            if np.isfinite(p):
                rows.append({"date": d, "expiry": e, "settle": p})
    return pd.DataFrame(rows)


def test_index_rolls_linearly_and_drops_the_front_on_the_day_before_expiry():
    e1, e2, e3 = dt.date(2024, 1, 5), dt.date(2024, 1, 9), dt.date(2024, 1, 30)
    L = _long({e1: [10, 11, 11, 11, np.nan, np.nan, np.nan],
               e2: [20, 20, 22, 22, 22, 22, np.nan],
               e3: [30, 30, 30, 33, 33, 36, 36]})
    I = s20.index_returns(L, prev_expiry={e1: dt.date(2023, 12, 29)}).set_index("date")
    # Jan 2 -> 3: front e1 has 3 trading days left after Jan 2 (3, 4, 5); period Dec 29..Jan 5 has 5 (Jan 2,3,4,5 + ...)
    w = I.loc[dt.date(2024, 1, 3), "w1"]
    r = I.loc[dt.date(2024, 1, 3), "ret"]
    assert r == pytest.approx(w * (11 / 10 - 1) + (1 - w) * 0.0)
    # the day before e1's expiry (Jan 4) the front weight is zero, so Jan 4 -> 5 uses only e2
    assert I.loc[dt.date(2024, 1, 5), "w1"] == pytest.approx(0.0)
    assert I.loc[dt.date(2024, 1, 5), "ret"] == pytest.approx(0.0)        # e2 flat Jan 4 -> 5


def test_etf_return_fee_and_floor():
    r = s20.etf_return(np.array([0.10, -0.05, 2.5]), lev=0.5, fee=0.0252)
    assert r[0] == pytest.approx(-0.05 - 0.0001) and r[1] == pytest.approx(0.025 - 0.0001)
    assert r[2] == -1.0                                                     # a -125% day is a total loss


def test_signals_and_one_day_execution_lag():
    vix = pd.Series([15, 22, 21, 18, 17, 16, 15, 15], dtype=float)
    vix3m = pd.Series([17, 20, 20, 19, 19, 19, 19, 19], dtype=float)
    s = s20.x3_signal(vix, vix3m, lookback=10, hold=3)
    # inverted on days 1-2; day 3 back below with an inversion in the lookback -> entry signal days 3, 4, 5
    assert list(s) == [0, 0, 0, 1, 1, 1, 0, 0]
    held = s20.held(s)
    assert list(held) == [0, 0, 0, 0, 0, 1, 1, 1]                          # signal at close t, trade at close t+1


def test_strategy_returns_charge_slippage_on_changes():
    etf = np.array([0.0, 0.01, 0.01, -0.02, 0.01])
    held = np.array([0, 1, 1, 0, 0])
    r = s20.strategy_returns(etf, held, slip=0.001)
    # bought at the close of day 0 (cost on day 0), held days 1-2, sold at the close of day 2 (cost on day 2)
    assert list(np.round(r, 6)) == [-0.001, 0.01, 0.009, 0.0, 0.0]


def test_stats_and_gate():
    dates = pd.date_range("2010-01-01", periods=600, freq="B")
    r = pd.Series(np.where(np.arange(600) % 2, 0.004, -0.002), index=dates)
    st = s20.series_stats(r, draws=300, seed=1, level=0.9)
    assert st["mean"] == pytest.approx(0.001) and st["max_drawdown"] == pytest.approx(0.002, abs=1e-9)
    assert s20.passes(st, max_dd=0.5) and not s20.passes({**st, "max_drawdown": 0.6}, max_dd=0.5)
    assert not s20.passes({**st, "geometric_annual": -0.01}, max_dd=0.5)
