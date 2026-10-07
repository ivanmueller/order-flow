"""Study 3 (RUNLOG 2026-10-07): expected-move band trades on 1-minute bars, hand-verified."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study3
from tests.conftest import ET, make_bars

DAY = dt.date(2024, 3, 6)
EM = 30.0          # band_a 0.5 -> band 15 pts from the open; band_stop_s 0.25 -> stop 7.5 pts
O = 5000.0


def ts(s):
    return pd.Timestamp(f"{DAY} {s}", tz=ET).tz_convert("UTC")


def path(tail):
    # 09:30-09:59 flat at 5000 (open O = 5000), then a push into the upper band at 10:03.
    return [5000.0] * 30 + [5005, 5010, 5014.5, 5015.5] + tail


def test_band_touch_and_fade(cfg):
    bars = make_bars(DAY, path([5012, 5008, 5003, 4999, 4999]), start="09:30")
    assert study3.session_open(bars, cfg) == O
    t = study3.find_band_touch(bars, O, EM, cfg)
    assert t == (33, 1)                                # bar 33 = 10:03, upper band
    r = study3.fade_trade(bars, 33, 1, O, EM, DAY, cfg)
    # short at the band less one tick of slippage; stop 0.25 EM beyond the band; target the open.
    assert r["E"] == 5014.75 and r["S"] == 5022.5 and r["T"] == 5000.0 and r["R_k"] == 7.75
    assert r["exit_reason"] == "target" and r["X"] == 5000.0 and r["exit_ts"] == ts("10:07")
    assert r["pnl_r"] == pytest.approx((5014.75 - 5000.0) / 7.75 - 3.98 / (7.75 * 50))


def test_breakout_stops_one_tick_beyond(cfg):
    bars = make_bars(DAY, path([5012, 5008, 5003, 4999, 4999]), start="09:30")
    r = study3.breakout_trade(bars, 33, 1, O, EM, DAY, cfg)
    assert r["E"] == 5015.25 and r["S"] == 5007.5 and r["T"] == 5030.0 and r["R_k"] == 7.75
    # 10:05 low is 5007.75 (not through); 10:06 low 5002.75 triggers the stop, fill one tick beyond.
    assert r["exit_reason"] == "stop" and r["X"] == 5007.25 and r["exit_ts"] == ts("10:06")
    assert r["pnl_r"] == pytest.approx((5007.25 - 5015.25) / 7.75 - 3.98 / (7.75 * 50))


def test_same_bar_both_barriers_is_a_loss(cfg):
    bars = make_bars(DAY, path([5012, 5008, 5003, 4999, 4999]), start="09:30")
    bars.loc[34, "high"] = 5023.0          # 10:04 bar spans the fade's stop (5022.5) and keeps falling later
    r = study3.fade_trade(bars, 33, 1, O, EM, DAY, cfg)
    assert r["exit_reason"] == "stop" and r["X"] == 5022.75


def test_touch_bar_itself_can_stop_the_fade(cfg):
    bars = make_bars(DAY, path([5012, 5008, 5003, 4999, 4999]), start="09:30")
    bars.loc[33, "high"] = 5023.0          # the touch bar ran straight through the stop
    r = study3.fade_trade(bars, 33, 1, O, EM, DAY, cfg)
    assert r["exit_reason"] == "stop" and r["exit_ts"] == ts("10:03")


def test_window_and_ambiguity_rules(cfg):
    # A band touch before band_start (10:00) does not count.
    early = [5000.0] * 10 + [5016.0] + [5000.0] * 30
    bars = make_bars(DAY, early, start="09:30")
    assert study3.find_band_touch(bars, O, EM, cfg) is None
    # A bar touching both bands at once is ambiguous: the session is skipped.
    bars = make_bars(DAY, path([5012]), start="09:30")
    bars.loc[33, "low"] = 4984.0
    assert study3.find_band_touch(bars, O, EM, cfg) is None
    # Lower band touch -> side -1 and a long fade.
    bars = make_bars(DAY, [5000.0] * 30 + [4995, 4990, 4984.5, 4990, 4995, 5001, 5001], start="09:30")
    assert study3.find_band_touch(bars, O, EM, cfg) == (32, -1)
    r = study3.fade_trade(bars, 32, -1, O, EM, DAY, cfg)
    assert r["E"] == 4985.25 and r["S"] == 4977.5 and r["T"] == 5000.0 and r["exit_reason"] == "target"


def test_time_exit_at_flat(cfg):
    bars = make_bars(DAY, path([5012.0] * 400), start="09:30")      # runs past 15:55
    r = study3.fade_trade(bars, 33, 1, O, EM, DAY, cfg)
    assert r["exit_reason"] == "time" and r["exit_ts"] == ts("15:55") and r["X"] == 5012.0 + 0.25


def test_permutation_test_detects_a_regime_gap():
    rng = np.random.default_rng(0)
    n = 300
    t = pd.DataFrame({"date": [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(n)],
                      "gex_pct": rng.uniform(0, 1, n)})
    t["pnl_r"] = np.where(t["gex_pct"] >= 0.5, 1.0, -1.0) + rng.normal(0, 0.3, n)
    res = study3.permutation_test(t, high_is_variant=True, thr=0.5, draws=300, seed=1)
    assert res["observed_gap"] > 1.5 and res["p"] < 0.02
    t["pnl_r"] = rng.normal(0, 1, n)
    res = study3.permutation_test(t, high_is_variant=True, thr=0.5, draws=300, seed=1)
    assert res["p"] > 0.05


def test_price_already_beyond_the_band_at_band_start_is_not_a_touch(cfg):
    # Price crossed the upper band at 09:40 and is still above it at 10:00: no clean touch, skip.
    c = [5000.0] * 10 + [5020.0] * 40
    bars = make_bars(DAY, c, start="09:30")
    assert study3.find_band_touch(bars, O, EM, cfg) is None
    # Came back inside before 10:00 and touched again at 10:05: that counts, from inside.
    c = [5000.0] * 10 + [5020.0] * 5 + [5005.0] * 20 + [5016.0] + [5005.0] * 10
    bars = make_bars(DAY, c, start="09:30")
    assert study3.find_band_touch(bars, O, EM, cfg) == (35, 1)


def test_breakout_fills_at_the_open_when_the_bar_gaps_through(cfg):
    # 10:03 bar opens at 5014.5 (inside), prints 5018: a stop at the band fills at the band plus slippage.
    bars = make_bars(DAY, path([5012, 5008, 5003, 4999, 4999]), start="09:30")
    assert study3.breakout_trade(bars, 33, 1, O, EM, DAY, cfg)["E"] == 5015.25
    # Make the 10:03 bar open above the band (previous close inside at 5014.5 -> open 5014.5 is inside;
    # force an open of 5018.0): the stop order fills at the open, not at the band.
    bars.loc[33, "open"] = 5018.0
    bars.loc[33, "high"] = 5019.0
    r = study3.breakout_trade(bars, 33, 1, O, EM, DAY, cfg)
    assert r["E"] == 5018.0 and r["S"] == 5007.5 and r["R_k"] == 10.5
    # The fade's limit at the band fills at the band less slippage regardless.
    assert study3.fade_trade(bars, 33, 1, O, EM, DAY, cfg)["E"] == 5014.75
