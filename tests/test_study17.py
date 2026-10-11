"""Study 17 step 1: our own LP position (range, fees, perp hedge, costs) and the selection machinery, by hand."""
import math

import numpy as np
import pandas as pd
import pytest

from src import study17 as s17

Q96 = 2 ** 96


def _week(P, p0=None, p1=None, fees=0.0, Lpool=1e12, F0=0.0, F1=0.0, hi=1.0, lo=1.0):
    """One pool-week: 8 closes (prior Sunday + 7 days)."""
    P = np.asarray(P, float)[None, :]
    one = np.ones_like(P)
    p0 = P.copy() if p0 is None else np.asarray(p0, float)[None, :]   # token1 a stablecoin at $1 unless given
    p1 = one if p1 is None else np.asarray(p1, float)[None, :]
    col = lambda v: np.broadcast_to(np.asarray(v, float), P.shape).copy()
    return dict(P=P, p0=p0, p1=p1, fees=col(fees), Lpool=col(Lpool), F0=col(F0), F1=col(F1), hi_x=col(hi), lo_x=col(lo))


def test_full_range_daily_loss_vs_hedge_is_exact():
    P = [100.0, 121.0] + [121.0] * 6                                   # +21% on day 1, then flat
    w = _week(P)
    r = s17.simulate_weeks(**w, vol0=True, vol1=False, k=1, capital=1.0, perp_fee=0.0)
    assert r["g_none"][0] == pytest.approx(math.sqrt(1.21) - 1)       # unhedged: value 2L sqrt(P), $1 at start
    assert r["g_perp"][0] == pytest.approx(-(math.sqrt(1.21) - 1) ** 2 / 2)   # hedged: -(sqrt(r) - 1)^2 / 2
    assert r["open_notional"][0] == pytest.approx(0.5)                # half the position is the volatile token


def test_range_position_caps_and_stops_earning_outside():
    # k = 2 -> m = 1 / (1 - 1/2)^2 = 4: range 25..400 around 100; price runs to 900 (out of range above)
    P = [100.0, 900.0] + [900.0] * 6
    w = _week(P, fees=10.0, Lpool=0.0)
    r = s17.simulate_weeks(**w, vol0=True, vol1=False, k=2, capital=1.0, perp_fee=0.0)
    sa, sb = 5.0, 20.0                                                 # sqrt(25), sqrt(400)
    L = 1.0 / (10 * (1 - 0.5) + 0 + (10 - sa))                        # value at 100: L(1/10 - 1/20)*100 + L(10 - 5)
    v_end = L * (sb - sa)                                              # all token1 (the stablecoin) above the range
    assert r["g_none"][0] == pytest.approx(v_end - 1.0)                # no fees: every day out of range
    assert r["fees"][0] == 0.0


def test_fee_share_and_breach_rule():
    P = [100.0] * 8
    w = _week(P, fees=7.0, Lpool=1e3)
    r = s17.simulate_weeks(**w, vol0=True, vol1=False, k=1, capital=1000.0, perp_fee=0.0)
    L = 1.0 / 20.0                                                     # full range: value 2 L sqrt(P) = 1
    assert r["fees"][0] == pytest.approx(7 * 7.0 * L / (1e3 + L * 1000.0))
    w = _week(P, fees=7.0, Lpool=1e3, hi=[1.0, 5.0, 1, 1, 1, 1, 1, 1])  # day 1's range reaches 500 > 400 (k = 2)
    r2 = s17.simulate_weeks(**w, vol0=True, vol1=False, k=2, capital=1000.0, perp_fee=0.0)
    r3 = s17.simulate_weeks(**_week(P, fees=7.0, Lpool=1e3), vol0=True, vol1=False, k=2, capital=1000.0, perp_fee=0.0)
    assert r2["fees"][0] == pytest.approx(r3["fees"][0] * 6 / 7)      # one of seven days earns nothing


def test_funding_paid_to_the_short_and_rebalance_fees():
    P = [100.0] * 8
    r = s17.simulate_weeks(**_week(P, F0=0.001), vol0=True, vol1=False, k=1, capital=1.0, perp_fee=0.0)
    assert r["g_perp"][0] == pytest.approx(7 * 0.001 * 0.5)           # short $0.5 receives 0.1% a day
    P = [100.0, 121.0] + [121.0] * 6
    a = s17.simulate_weeks(**_week(P), vol0=True, vol1=False, k=1, capital=1.0, perp_fee=0.0)
    b = s17.simulate_weeks(**_week(P), vol0=True, vol1=False, k=1, capital=1.0, perp_fee=0.01)
    L = 1 / 20.0
    dx = L / 10 - L / 11                                               # token0 held falls from L/10 to L/11
    assert a["g_perp"][0] - b["g_perp"][0] == pytest.approx(0.01 * dx * 121)


def test_v3_daily_price_liquidity_and_excursions():
    rows = pd.DataFrame({"date": [0, 86_400], "sqrtPrice": [str(int(2e6 * Q96))] * 2,      # P_raw = 4e12
                         "liquidity": ["1000000000000"] * 2, "feesUSD": ["5", "6"], "tvlUSD": ["1e6", "1e6"],
                         "high": ["4.4", "4"], "low": ["4", "4"], "close": ["4", "4"]})
    d = s17.v3_daily(rows, dec0=6, dec1=18)
    assert d["P"].iloc[0] == pytest.approx(4.0)                       # human = raw x 10^(dec0 - dec1)
    assert d["Lpool"].iloc[0] == pytest.approx(1e12 / 10 ** 12)
    assert d["hi_x"].iloc[0] == pytest.approx(1.1) and d["lo_x"].iloc[0] == pytest.approx(4 / 4.4)


def test_market_filter_drops_disagreeing_days():
    days = np.arange(3)
    d = pd.DataFrame({"P": [100.0, 100.0, 100.0], "Lpool": 1.0, "fees": 1.0, "tvl": 1e6, "hi_x": 1.0, "lo_x": 1.0},
                     index=days)
    p0 = pd.Series([100.0, 110.0, 101.0], index=days)
    out = s17.attach_market(d, p0, None, None, None, max_gap=0.05, conc=(0.0, np.inf), v3=False)
    assert list(out["valid"]) == [True, False, True]


def test_weekly_features_use_only_earlier_days():
    n = 70
    P = 100 * np.exp(np.r_[0, np.cumsum(np.where(np.arange(1, n) == 40, 0.5, 0.01))])   # big move on day 40
    d = pd.DataFrame({"P": P, "Lpool": 1e9, "fees": 1.0, "tvl": 2e6, "hi_x": 1.0, "lo_x": 1.0, "p0": P, "p1": 1.0,
                      "F0": 0.0, "F1": 0.0, "valid": True}, index=np.arange(4, 4 + n))      # day 4 is a Monday
    W = s17.weekly_rows(d, vol0=True, vol1=False, v3=False, ks=[1], capital=1e5, perp_fee=0.0, fee_days=7,
                        trailing_days=28, min_days=(5, 20))
    row = W[W["t0"] == 4 + 42].iloc[0]                                 # week from day 46: the move (day 44) is 2 days before
    assert row["m7"] == pytest.approx((0.25 + 6 * 0.0001) / 7)
    early = W[W["t0"] == 4 + 35].iloc[0]                               # week from day 39 contains day 44: not in features
    assert early["m7"] == pytest.approx(0.0001) and early["rv_week"] > 0.25


def test_hold_blocks_and_continuity():
    rows = pd.DataFrame({"pool": "a", "wk": [0, 1, 2, 3, 4, 5, 6, 7], "sig": [True, False, False, False, False, True, True, False]})
    act, prev, nxt = s17.activate(rows, rows["sig"].to_numpy(), hold=4)
    assert list(act) == [True, True, True, True, False, False, False, False]   # week 5's signal is not a decision week
    assert list(prev[:4]) == [False, True, True, True] and list(nxt[:4]) == [True, True, True, False]
    act1, _, _ = s17.activate(rows, rows["sig"].to_numpy(), hold=1)
    assert list(act1) == list(rows["sig"])


def test_perp_symbol_mapping():
    avail = {"ETHUSDT", "LINKUSDT", "1000PEPEUSDT"}
    m = {"WETH": "ETHUSDT", "WBTC": "BTCUSDT"}
    assert s17.perp_symbol("WETH", avail, m) == ("ETHUSDT", 1.0)
    assert s17.perp_symbol("link", avail, m) == ("LINKUSDT", 1.0)
    assert s17.perp_symbol("PEPE", avail, m) == ("1000PEPEUSDT", 1000.0)
    assert s17.perp_symbol("WBTC", avail, m) is None and s17.perp_symbol("renBTC", avail, m) is None


def test_series_stats_and_tail():
    s = pd.Series([0.01] * 19 + [1.0])
    st = s17.series_stats(s, draws=500, seed=1, level=0.9, tail=0.05)
    assert st["mean"] == pytest.approx((0.19 + 1.0) / 20) and st["without_best_tail"] == pytest.approx(0.01)


def test_components_add_up_to_the_hedged_result():
    P = [100.0, 121.0, 110.0, 115.0, 115.0, 100.0, 104.0, 104.0]
    r = s17.simulate_weeks(**_week(P, fees=3.0, Lpool=10.0, F0=0.0003), vol0=True, vol1=False, k=1, capital=1.0,
                           perp_fee=0.0005)
    assert r["g_perp"][0] == pytest.approx(r["fees"][0] + r["value"][0] + r["hedge_px"][0] + r["funding"][0] - r["reb"][0])
    assert r["g_none"][0] == pytest.approx(r["fees"][0] + r["value"][0])
