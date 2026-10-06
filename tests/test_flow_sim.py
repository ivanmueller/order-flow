import datetime as dt

import pandas as pd
import pytest

from src import flow, sim
from src.config import with_params
from tests.conftest import ET, make_trades

DAY = dt.date(2024, 3, 6)
L, EM, BASELINE = 100.0, 50.0, 50.0
BAR_OPEN = pd.Timestamp(f"{DAY} 10:00", tz=ET).tz_convert("UTC")


def ts(s):
    return pd.Timestamp(f"{DAY} {s}", tz=ET).tz_convert("UTC")


def trades(tail=None):
    rows = [
        ("09:52:00", 101.0, 10, -1), ("09:54:00", 101.0, 10, -1), ("09:55:00", 101.0, 10, 1),
        ("09:56:00", 101.0, 10, -1), ("09:58:00", 101.0, 10, -1), ("09:59:00", 100.75, 10, -1),
        ("10:00:05", 100.5, 20, -1),   # t0: first print within b of L
        ("10:00:30", 100.0, 30, -1), ("10:01:00", 99.75, 50, -1),
        ("10:01:30", 100.0, 40, 1), ("10:02:00", 100.25, 60, 1), ("10:02:30", 100.75, 50, 1),
        ("10:03:10", 101.0, 5, 1),     # first trade after t_dec = 10:03:05 -> entry
    ]
    rows += tail if tail is not None else [("10:05:00", 103.0, 1, 1), ("10:06:00", 104.25, 1, 1),
                                           ("10:07:00", 104.5, 1, 1)]
    return make_trades(rows, DAY)


def test_features_hand_values(cfg):
    f = flow.features(trades(), BAR_OPEN, L, 1, BASELINE, cfg)
    assert f["t0_trade"] == ts("10:00:05")
    assert f["agg_in"] == 100 and f["pen"] == 1.0
    assert f["abs_ratio"] == pytest.approx(100 / 1 / (0.5 * 50))       # 4.0
    assert f["approach_delta"] == pytest.approx(-40 / 60)
    assert f["exhaustion"] == pytest.approx((10 / 2) / (40 / 8))        # 1.0
    assert f["reclaim"] and f["t_r"] == ts("10:03:00")
    assert f["t_dec"] == ts("10:03:05")                                  # waits for the abs window
    assert f["p_ext"] == 99.75
    assert f["confirmed"]


def test_penetration_scales_absorption(cfg):
    t = trades()
    t.loc[t["price"] == 99.75, "price"] = 99.0          # 4 ticks through
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    assert f["pen"] == 4.0 and f["abs_ratio"] == pytest.approx(1.0)
    assert f["reclaim"] and not f["confirmed"]          # below the 2.0 threshold


def test_no_reclaim_without_positive_delta(cfg):
    t = trades()
    t.loc[t["ts_event_utc"] == ts("10:02:30"), "side"] = -1   # close above L but delta negative
    t.loc[t["ts_event_utc"] == ts("10:02:00"), "side"] = -1
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    # 10:02 bar: delta -20-30-50+40-60-50 < 0; 10:03 bar (end 10:04) closes 101 with +5 -> still < 0
    assert not f["reclaim"] and not f["confirmed"]


def test_confirmed_trade_target(cfg):
    t = trades()
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    r = sim.confirmed_trade(t, f, L, 1, EM, DAY, cfg)
    assert r["E"] == 101.25 and r["S"] == 99.25 and r["R_k"] == 2.0 and r["T"] == 104.25
    assert r["exit_reason"] == "target" and r["X"] == 104.25
    assert r["pnl_r"] == pytest.approx(1.5 - 3.98 / (2.0 * 50))


def test_confirmed_trade_stop_and_gap(cfg):
    t = trades(tail=[("10:05:00", 99.25, 1, -1)])
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    r = sim.confirmed_trade(t, f, L, 1, EM, DAY, cfg)
    assert r["exit_reason"] == "stop" and r["X"] == 99.0
    assert r["pnl_r"] == pytest.approx((99.0 - 101.25) / 2.0 - 3.98 / 100)
    t = trades(tail=[("10:05:00", 98.0, 1, -1)])        # gap through the stop: fill at the print
    r = sim.confirmed_trade(t, flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg), L, 1, EM, DAY, cfg)
    assert r["X"] == 98.0


def test_confirmed_trade_time_exit(cfg):
    t = trades(tail=[("10:20:00", 102.0, 1, 1), ("10:33:05", 102.5, 1, 1), ("10:34:00", 103.0, 1, 1)])
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    r = sim.confirmed_trade(t, f, L, 1, EM, DAY, cfg)
    assert r["exit_reason"] == "time" and r["X"] == 102.25   # next trade at/after t_dec + 30m, minus a tick


def test_target_needs_print_beyond(cfg):
    t = trades(tail=[("10:05:00", 104.25, 1, 1), ("10:06:00", 104.25, 1, 1)])
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    r = sim.confirmed_trade(t, f, L, 1, EM, DAY, cfg)
    assert r["exit_reason"] == "data_end"


def test_risk_too_wide_and_min_risk(cfg):
    t = trades()
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    assert sim.confirmed_trade(t, f, L, 1, 10.0, DAY, cfg)["skip"] == "risk_too_wide"   # 2.0 > 0.15*10
    c2 = with_params(cfg, stop_buffer=0, entry_slippage=0)
    f2 = dict(f, p_ext=100.75)                                                       # R_k = 0.25 -> widened
    r = sim.confirmed_trade(t, f2, L, 1, EM, DAY, c2)
    assert r["R_k"] == 1.0 and r["S"] == 100.0


def test_naive_trade(cfg):
    t = trades()
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    r = sim.naive_trade(t, f["t0_trade"], L, 1, EM, DAY, cfg)
    assert r["entry_ts"] == ts("10:01:00") and r["E"] == 100.0 and r["R_k"] == 2.5
    assert r["exit_reason"] == "target" and r["X"] == 103.75
    assert r["pnl_r"] == pytest.approx(1.5 - 3.98 / (2.5 * 50))


def test_resistance_mirror(cfg):
    # Mirror every price around L: resistance from below should give the same numbers.
    t = trades()
    t["price"] = 2 * L - t["price"]
    t["side"] = -t["side"]
    f = flow.features(t, BAR_OPEN, L, -1, BASELINE, cfg)
    assert f["abs_ratio"] == pytest.approx(4.0) and f["confirmed"]
    r = sim.confirmed_trade(t, f, L, -1, EM, DAY, cfg)
    assert r["pnl_r"] == pytest.approx(1.5 - 3.98 / 100)


def break_trades(tail=None):
    """Touch at 10:00:05, price pushes 6 ticks through, never reclaims; net flow keeps selling."""
    rows = [("09:55:00", 101.0, 10, -1), ("09:58:00", 101.0, 10, -1),
            ("10:00:05", 100.5, 20, -1), ("10:00:30", 100.0, 30, -1), ("10:01:00", 99.5, 50, -1),
            ("10:02:00", 98.5, 40, -1), ("10:04:00", 99.0, 10, 1), ("10:07:00", 98.75, 30, -1),
            ("10:10:10", 98.5, 5, -1)]   # first trade after t0 + 10 min -> entry
    rows += tail if tail is not None else [("10:12:00", 97.0, 1, -1), ("10:13:00", 96.5, 1, -1), ("10:14:00", 96.0, 1, -1)]
    return make_trades(rows, DAY)


def test_break_detected_and_continuation_trade(cfg):
    t = break_trades()
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    assert not f["reclaim"] and f["broke"]
    assert f["break_pen"] == pytest.approx(6.0) and f["break_flow"] > 0
    assert f["t_break"] == ts("10:10:05")
    r = sim.continuation_trade(t, f, L, 1, EM, DAY, cfg)
    # short: entry 98.5 - 0.25 = 98.25; stop 2 ticks back inside the level = 100.5; R = 2.25; target 94.875
    assert r["E"] == 98.25 and r["S"] == 100.5 and r["R_k"] == pytest.approx(2.25)
    assert r["T"] == pytest.approx(98.25 - 1.5 * 2.25)
    assert r["exit_reason"] == "data_end"          # target 94.875 never printed one tick beyond


def test_no_break_when_reclaimed_or_shallow(cfg):
    t = trades()                                    # the reclaim fixture
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    assert f["reclaim"] and not f["broke"]
    assert sim.continuation_trade(t, f, L, 1, EM, DAY, cfg) is None
    t2 = break_trades()
    t2.loc[t2["price"] < 99.5, "price"] = 99.5      # only 2 ticks through: not a break
    f2 = flow.features(t2, BAR_OPEN, L, 1, BASELINE, cfg)
    assert not f2["broke"]


def test_continuation_stop_fills_inside_level(cfg):
    t = break_trades(tail=[("10:11:00", 100.5, 1, 1), ("10:12:00", 101.0, 1, 1)])
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    r = sim.continuation_trade(t, f, L, 1, EM, DAY, cfg)
    assert r["exit_reason"] == "stop" and r["X"] == 100.75     # one tick through the 100.5 stop
    assert r["pnl_r"] == pytest.approx((98.25 - 100.75) / 2.25 - 3.98 / (2.25 * 50))


def test_mirror_trade_is_the_naive_opposite(cfg):
    """Naive fills long at 10:01:00 on the 99.75 print (one tick through L=100). The mirror sells
    that print with one tick of slippage: E = 99.5, stop at L + fail_F*EM = 102.5, R_k = 3.0,
    target 95.0. The bounce to 103.0 at 10:05 trips the stop; the print gapped past the one-tick-beyond
    fill (102.75), so the exit is the print itself, 103.0."""
    t = trades()
    f = flow.features(t, BAR_OPEN, L, 1, BASELINE, cfg)
    r = sim.mirror_trade(t, f["t0_trade"], L, 1, EM, DAY, cfg)
    assert r["entry_ts"] == ts("10:01:00") and r["E"] == 99.5 and r["S"] == 102.5 and r["R_k"] == 3.0
    assert r["T"] == 95.0 and r["exit_reason"] == "stop" and r["X"] == 103.0
    assert r["pnl_r"] == pytest.approx(-(103.0 - 99.5) / 3.0 - 3.98 / (3.0 * 50))
    # Mirror of the mirror: flip every price around L and the numbers come back identical.
    t2 = trades(); t2["price"] = 2 * L - t2["price"]; t2["side"] = -t2["side"]
    f2 = flow.features(t2, BAR_OPEN, L, -1, BASELINE, cfg)
    r2 = sim.mirror_trade(t2, f2["t0_trade"], L, -1, EM, DAY, cfg)
    assert r2["pnl_r"] == pytest.approx(r["pnl_r"]) and r2["R_k"] == 3.0


def test_fairness_block(cfg):
    from src import analysis
    done = pd.DataFrame({"mode": ["naive"] * 4 + ["mirror"] * 4, "R_k": 2.5, "pnl_r": [1.4, -1.1, -1.1, -1.1, 1.4, -1.1, 1.4, -1.1],
                         "exit_reason": ["target", "stop", "stop", "stop", "target", "stop", "target", "stop"]})
    fb = analysis.fairness(done, cfg)
    assert fb["naive"]["rw_win_rate"] == pytest.approx(2.5 / (2.5 * 2.5 + 0.25))
    assert fb["naive"]["R_k_ticks_median"] == 10 and fb["naive"]["win_rate"] == 0.25
    assert "naive_plus_mirror" in fb and fb["naive_plus_mirror"]["sum_expectancy_r"] == pytest.approx(0.25 * 1.4 - 0.75 * 1.1 + 0.5 * 1.4 - 0.5 * 1.1)
