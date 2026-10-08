"""Study 9: realized spread to passive fills, and level reversion with a resting order. Hand-checked."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study9
from tests.conftest import make_bars, make_trades

DAY = dt.date(2024, 3, 5)
ET = "America/New_York"


def et(hhmm):
    return pd.Timestamp(f"{DAY} {hhmm}", tz=ET).tz_convert("UTC")


@pytest.fixture
def tape():
    # 1-tick ES market: a sell aggressor hits the 100.00 bid, buyers lift 100.25, the bid moves up to 100.25 ...
    return make_trades([
        ("10:00:00", 100.00, 2, -1),   # passive buyer at 100.00
        ("10:00:02", 100.25, 1, 1),    # passive seller at 100.25
        ("10:00:40", 100.25, 3, -1),   # bid now 100.25
        ("10:01:10", 100.50, 1, 1),
    ], DAY)


def test_mid_from_trades(tape):
    m = study9.inferred_mid(tape["price"].to_numpy(), tape["side"].to_numpy(), 0.25)
    np.testing.assert_allclose(m, [100.125, 100.125, 100.375, 100.375])


def test_realized_spread_by_hand(tape):
    rs = study9.realized_spread(tape, horizons=[30, 60], span_end=et("10:05"), tick=0.25)
    # trade 0 (passive buy 100.00): mid at +30 s = 100.125 -> +0.5 tick; at +60 s = 100.375 -> +1.5 ticks
    assert rs.loc[0, "rs_30"] == pytest.approx(0.5) and rs.loc[0, "rs_60"] == pytest.approx(1.5)
    # trade 1 (passive sell 100.25) at +30 s: last print is itself -> mid 100.125 -> +0.5 tick; at +60 s mid 100.375 -> -0.5
    assert rs.loc[1, "rs_30"] == pytest.approx(0.5) and rs.loc[1, "rs_60"] == pytest.approx(-0.5)
    # trade 3 + 30 s is still inside the span; + 60 s too
    assert np.isfinite(rs.loc[3, "rs_60"])


def test_horizon_past_span_end_is_dropped(tape):
    rs = study9.realized_spread(tape, horizons=[60], span_end=et("10:01:30"), tick=0.25)
    assert np.isnan(rs.loc[2, "rs_60"]) and np.isnan(rs.loc[3, "rs_60"])   # 10:00:40 + 60 s > 10:01:30
    assert np.isfinite(rs.loc[0, "rs_60"])


def test_clearing_flag():
    t = make_trades([
        ("10:00:00", 100.00, 1, -1), ("10:00:01", 100.00, 1, -1), ("10:00:02", 100.00, 1, -1),
        ("10:00:03", 99.75, 1, -1),                                   # bid at 100.00 consumed: last 100.00 print clears
        ("10:00:04", 100.00, 1, 1),                                   # ask 100.00 lifted, then ...
        ("10:00:05", 99.75, 1, -1),                                   # ... price back down: the ask level survived
    ], DAY)
    f = study9.clearing_flags(t["price"].to_numpy(), t["side"].to_numpy())
    assert list(f) == [False, False, True, False, False, False]


def test_spread_check():
    t = make_trades([("10:00:00", 100.00, 1, -1), ("10:00:01", 100.25, 1, 1), ("10:00:02", 100.00, 1, -1),
                     ("10:00:03", 100.50, 1, 1)], DAY)
    out = study9.spread_check(t["price"].to_numpy(), t["side"].to_numpy(), 0.25)
    assert out["opposite_pairs"] == 3 and out["one_tick_share"] == pytest.approx(2 / 3)


def test_bucket_edges():
    s = pd.Series([et("09:45"), et("10:15"), et("12:00"), et("15:00"), et("15:45"), et("16:30")])
    lab = study9.tod_label(s, ["09:30", "10:00", "11:30", "14:00", "15:30", "16:00"])
    assert list(lab) == ["b0", "b1", "b2", "b3", "b4", None]
    assert list(study9.size_label(pd.Series([1, 2, 9, 10, 49, 50, 300]), [1, 2, 10, 50])) == \
        ["1", "2-9", "2-9", "10-49", "10-49", "50+", "50+"]


# ---- level reversion -------------------------------------------------------------------------
@pytest.fixture
def bars():
    # 15:00 .. 15:59 ET; support level L = 100. Bar 0 trades down to exactly 100.00, then drifts up 0.25/bar
    closes = 100.25 + 0.25 * np.arange(60)
    b = make_bars(DAY, closes, start="15:00", spread=0.0)
    b.loc[0, ["open", "high", "low", "close"]] = [100.50, 100.50, 100.00, 100.25]
    return b


def test_perfect_fill_on_touch(bars):
    f = study9.resting_fill(bars, t=0, L=100.0, d=1, window=10, tick=0.25, through=False)
    assert f == 0


def test_conservative_fill_needs_a_trade_through(bars):
    assert study9.resting_fill(bars, t=0, L=100.0, d=1, window=10, tick=0.25, through=True) is None
    b = bars.copy()
    b.loc[2, "low"] = 99.75
    assert study9.resting_fill(b, t=0, L=100.0, d=1, window=10, tick=0.25, through=True) == 2


def test_reversion_pnl_by_hand(bars, cfg):
    r = study9.reversion_trade(bars, t=0, L=100.0, d=1, horizons=[1, 5, 60], cfg=cfg, rth_close="16:00")
    # perfect: entry 100.00 at bar 0; exit close of bar 0+H = 100.25 + 0.25 H
    assert r["perfect_gross_1"] == pytest.approx(0.50) and r["perfect_gross_5"] == pytest.approx(1.50)
    # 60-min hold runs past the last RTH bar (15:59, index 59): truncated at its close
    assert r["perfect_gross_60"] == pytest.approx(100.25 + 0.25 * 59 - 100.0) and r["truncated_60"]
    # conservative: no trade-through -> no fill
    assert np.isnan(r["cons_gross_1"])


def test_short_side_and_conservative_exit(cfg):
    closes = 99.75 - 0.25 * np.arange(20)
    b = make_bars(DAY, closes, start="11:00", spread=0.0)
    b.loc[0, ["open", "high", "low", "close"]] = [99.50, 100.25, 99.50, 99.75]   # resistance 100 traded through
    r = study9.reversion_trade(b, t=0, L=100.0, d=-1, horizons=[3], cfg=cfg, rth_close="16:00")
    assert r["perfect_gross_3"] == pytest.approx(100.0 - 99.0)                   # short 100, close of bar 3 = 99.00
    assert r["cons_gross_3"] == pytest.approx(100.0 - 99.0 - 0.25)                # fill on the trade-through, exit 1 tick worse


def test_fee_points():
    assert study9.fee_points(3.98, 50.0) == pytest.approx(0.0796)
    assert study9.fee_points(2.79, 50.0) == pytest.approx(0.0558)


def test_day_bootstrap_of_sums():
    s = np.array([1.0, 3.0, 2.0])
    c = np.array([1.0, 1.0, 2.0])
    out = study9.boot_ratio(s, c, draws=2000, seed=1, level=0.9)
    assert out["mean"] == pytest.approx(6.0 / 4.0) and out["lo"] <= out["mean"] <= out["hi"]
