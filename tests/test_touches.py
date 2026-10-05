import datetime as dt

import numpy as np
import pytest

from src import touches
from tests.conftest import make_bars

DAY = dt.date(2024, 3, 6)
L, EM = 100.0, 50.0   # a = 5 pts, b = 0.5 pts, R = 5 pts, F = 2.5 pts


def path():
    # 09:00..09:39 at 110, slide to the level, touch at 09:45, bounce to 105 by 09:48 (success),
    # hold 108 until 10:10, touch again at 10:12 and break through L - F at 10:13 (failure).
    c = [110.0] * 40 + [108, 106, 104, 102, 101] + [100.25] + [101, 103, 105] + [108.0] * 22
    c += [101.5, 99.0, 97.0] + [96.0] * 30
    return c


def test_detect_and_label(cfg):
    bars = make_bars(DAY, path())
    t = touches.detect(bars, L, EM, cfg)
    assert [x["bar_idx"] for x in t] == [45, 72]
    assert [x["d"] for x in t] == [1, 1]
    assert [x["touch_n"] for x in t] == [0, 1]
    first = touches.label(bars, 45, L, 1, EM, cfg)
    assert first["success"] and not first["timeout"] and first["bars_to_outcome"] == 3
    second = touches.label(bars, 72, L, 1, EM, cfg)
    assert not second["success"] and not second["timeout"]
    # MFE on the first touch: highest high within 30 bars = 108.25 -> 8.25 / 50
    assert first["mfe"] == pytest.approx(8.25 / EM)


def test_debounce_blocks_retouch(cfg):
    # Price touches, lifts only 1 point (still within 10 bars of a proximity bar), touches again.
    c = [110.0] * 40 + [105, 102, 100.25, 101.5, 100.25] + [101] * 20
    bars = make_bars(DAY, c)
    t = touches.detect(bars, L, EM, cfg)
    assert [x["bar_idx"] for x in t] == [42]


def test_resistance_direction(cfg):
    c = [90.0] * 40 + [95, 99, 99.75] + [99] * 5
    bars = make_bars(DAY, c)
    t = touches.detect(bars, L, EM, cfg)
    assert t and t[0]["d"] == -1 and t[0]["bar_idx"] == 42


def test_same_bar_ambiguity_is_failure(cfg):
    c = [110.0] * 40 + [106, 104, 102, 101, 100.25]
    bars = make_bars(DAY, c)
    # Make bar 44 span both the target and the stop.
    bars.loc[44, "high"] = 106.0
    bars.loc[44, "low"] = 97.0
    lab = touches.label(bars, 44, L, 1, EM, cfg)
    assert not lab["success"] and not lab["timeout"] and lab["bars_to_outcome"] == 0


def test_timeout_is_failure(cfg):
    c = [110.0] * 40 + [106, 104, 102, 101, 100.25] + [101.0] * 70
    bars = make_bars(DAY, c)
    lab = touches.label(bars, 44, L, 1, EM, cfg)
    assert not lab["success"] and lab["timeout"] and lab["horizon_bars"] == 60


def test_no_touch_before_window(cfg):
    # Touch at 09:15 (before 09:31) must be ignored.
    c = [110.0] * 10 + [100.25] + [110.0] * 10
    assert touches.detect(make_bars(DAY, c), L, EM, cfg) == []


def test_label_stops_at_rth_close(cfg):
    c = [110.0] * 40 + [105, 101, 100.25] + [101.0] * 80
    bars = make_bars(DAY, c, start="14:50")   # touch bar at 15:32; RTH ends 16:00
    lab = touches.label(bars, 42, L, 1, EM, cfg)
    assert lab["horizon_bars"] == 28 and lab["timeout"]
    assert np.isfinite(lab["mae"])
