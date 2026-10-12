"""Study 19: expiry choice, delta strikes, the three structures' settlement P&L, filters and the gate, by hand."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from src import study19 as s19


def _chain(strikes, bid_c, ask_c, bid_p, ask_p):
    return pd.DataFrame({"bid_C": bid_c, "ask_C": ask_c, "bid_P": bid_p, "ask_P": ask_p,
                         "mid_C": (np.array(bid_c) + np.array(ask_c)) / 2, "mid_P": (np.array(bid_p) + np.array(ask_p)) / 2,
                         "valid_C": True, "valid_P": True}, index=pd.Index(strikes, dtype=float, name="strike"))


def test_weekly_expiry_prefers_seven_days_and_handles_a_holiday():
    fri = dt.date(2024, 3, 22)
    exps = [dt.date(2024, 3, 25), dt.date(2024, 3, 28), dt.date(2024, 3, 29), dt.date(2024, 4, 5)]
    assert s19.pick_weekly(exps, fri, 4, 8) == dt.date(2024, 3, 29)
    exps2 = [dt.date(2024, 3, 25), dt.date(2024, 3, 28), dt.date(2024, 4, 5)]       # Good Friday: no 03-29
    assert s19.pick_weekly(exps2, fri, 4, 8) == dt.date(2024, 3, 28)
    assert s19.pick_weekly([dt.date(2024, 4, 5)], fri, 4, 8) is None                 # 14 days: out of range


def test_forward_delta_strike_selection():
    F, T, vol = 100.0, 7 / 365, 0.20
    strikes = np.arange(90.0, 111.0, 1.0)
    K = s19.delta_strike(strikes, np.full(len(strikes), vol), F, T, 0.20, "C")
    # forward call delta N(d1) = 0.20 -> d1 = -0.8416; K = F exp(-d1 sd + sd^2/2)
    sd = vol * math.sqrt(T)
    target = F * math.exp(0.8416 * sd + 0.5 * sd * sd)
    assert K == pytest.approx(round(target))
    Kp = s19.delta_strike(strikes, np.full(len(strikes), vol), F, T, 0.20, "P")
    assert Kp == pytest.approx(round(F * math.exp(-0.8416 * sd + 0.5 * sd * sd)))


def test_structures_settle_at_the_index_close_with_bid_ask_and_fees():
    w = _chain([95, 100, 105], bid_c=[6, 2.0, 0.5], ask_c=[6.4, 2.2, 0.6], bid_p=[0.4, 1.8, 6], ask_p=[0.5, 2.0, 6.5])
    f = 0.015                                                           # $1.50 a leg on a 100 multiplier
    assert s19.straddle_pnl(w, 100, 103.0, f) == pytest.approx(2.0 + 1.8 - 3.0 - 2 * f)
    assert s19.strangle_pnl(w, 105, 95, 107.0, f) == pytest.approx(0.5 + 0.4 - 2.0 - 2 * f)
    assert s19.strangle_pnl(w, 105, 95, 100.0, f) == pytest.approx(0.9 - 2 * f)
    # condor: short 105C/95P, long wings bought at the ask (here 105C/95P stand in for wings 110/90 via a wider chain)
    w2 = _chain([90, 95, 105, 110], bid_c=[11, 6, 0.5, 0.1], ask_c=[11.5, 6.4, 0.6, 0.15],
                bid_p=[0.1, 0.4, 6, 11], ask_p=[0.15, 0.5, 6.5, 11.5])
    pnl = s19.condor_pnl(w2, 105, 95, 110, 90, 120.0, f)
    assert pnl == pytest.approx(0.5 + 0.4 - 0.15 - 0.15 - (15 - 10) - 4 * f)   # loss capped by the 110 call


def test_inversion_filter_and_streak():
    assert s19.inverted(21.0, 20.5) and s19.inverted(20.0, 20.0) and not s19.inverted(15.0, 18.0)
    assert not s19.inverted(np.nan, 18.0)                               # unknown -> not skipped, flagged
    won = pd.Series([True, True, True, False, True, True, True, True])
    skip = s19.after_streak(won, 3)
    assert list(skip) == [False, False, False, True, False, False, False, True]


def test_gate():
    good = {"n": 60, "mean": 0.05, "lo": 0.01, "first_half": 0.04, "second_half": 0.06, "stress_mean": 0.02}
    assert s19.passes(good, min_trades=50)
    for k, v in [("n", 49), ("lo", 0.0), ("first_half", -0.01), ("stress_mean", -0.001)]:
        assert not s19.passes({**good, k: v}, min_trades=50), k


def test_series_stats_counts_the_worst_week_twice():
    x = pd.Series([0.1] * 9 + [-0.5])
    st = s19.series_stats(x, draws=200, seed=1, level=0.9)
    assert st["mean"] == pytest.approx(0.04) and st["worst_twice_mean"] == pytest.approx((0.9 - 1.0) / 11)
