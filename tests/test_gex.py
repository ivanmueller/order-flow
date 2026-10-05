import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import gex
from src.config import load_config


def test_black76_atm_hand_value():
    # d1 = 0.1, d2 = -0.1: C = 100 (N(0.1) - N(-0.1)) = 7.96557
    assert gex.black76_price(100, 100, 1.0, 0.2, 1.0, True) == pytest.approx(7.965567, abs=1e-5)
    assert gex.black76_price(100, 100, 1.0, 0.2, 1.0, False) == pytest.approx(7.965567, abs=1e-5)
    assert gex.black76_price(100, 100, 1.0, 0.2, 0.99, True) == pytest.approx(0.99 * 7.965567, abs=1e-5)


def test_put_call_parity():
    F, K, T, s, D = 4200.0, 4150.0, 20 / 365, 0.18, 0.997
    c = gex.black76_price(F, K, T, s, D, True)
    p = gex.black76_price(F, K, T, s, D, False)
    assert c - p == pytest.approx(D * (F - K), abs=1e-8)


@pytest.mark.parametrize("is_call,K,sigma", [(True, 4300, 0.14), (False, 4000, 0.31), (False, 4190, 0.05)])
def test_implied_vol_round_trip(is_call, K, sigma):
    F, T, D = 4200.0, 7 / 365, 0.999
    px = float(gex.black76_price(F, K, T, sigma, D, is_call))
    assert gex.implied_vol(px, F, K, T, D, is_call) == pytest.approx(sigma, abs=1e-7)


def test_implied_vol_out_of_bracket_is_nan():
    assert np.isnan(gex.implied_vol(0.0, 100, 100, 0.1, 1.0, True))
    assert np.isnan(gex.implied_vol(1e6, 100, 100, 0.1, 1.0, True))


def test_gamma_hand_value_and_gex_sign():
    # phi(0.1) / (100 * 0.2 * 1) = 0.39695255 / 20
    g = gex.gamma(100.0, 100.0, 1.0, 0.2)
    assert g == pytest.approx(0.019847627, abs=1e-9)
    # GEX = s * gamma * OI * 100 * S^2 * 0.01 = 0.0198476 * 10 * 100 * 100
    assert gex.contract_gex(100.0, 100.0, 1.0, 0.2, 1.0, 10.0) == pytest.approx(1984.7627, abs=1e-3)
    assert gex.contract_gex(100.0, 100.0, 1.0, 0.2, -1.0, 10.0) == pytest.approx(-1984.7627, abs=1e-3)


def test_fit_forward_recovers_F_and_D():
    K = np.arange(4150, 4255, 5.0)
    F, D = 4203.4, 0.9962
    c = gex.black76_price(F, K, 0.05, 0.2, D, True)
    p = gex.black76_price(F, K, 0.05, 0.2, D, False)
    f_hat, d_hat = gex.fit_forward(K, c, p)
    assert f_hat == pytest.approx(F, abs=1e-6)
    assert d_hat == pytest.approx(D, abs=1e-9)


def test_fit_forward_rejects_too_few_points():
    assert np.isnan(gex.fit_forward([100, 105], [3, 1], [1, 3])[0])


def test_find_flip_interpolates_and_picks_nearest():
    grid = np.array([0, 10, 20, 30, 40, 50.0])
    net = np.array([-2, -1, 1, 3, 2, -2.0])   # crossings at 15 and 45
    assert gex.find_flip(grid, net, 18) == pytest.approx(15.0)
    assert gex.find_flip(grid, net, 44) == pytest.approx(45.0)
    assert np.isnan(gex.find_flip(grid, np.ones(6), 20))
    assert gex.find_flip(grid, np.array([1, 1, 0, -1, -1, -1.0]), 21) == pytest.approx(20.0)


def test_pct_rank_prev():
    s = pd.Series([1.0, 2.0, 3.0, 0.5, 2.0])
    r = gex.pct_rank_prev(s, lookback=3, min_periods=3)
    assert np.isnan(r.iloc[:3]).all()
    assert r.iloc[3] == pytest.approx(0.0)             # 0.5 below 1, 2, 3
    assert r.iloc[4] == pytest.approx((1 + 0.5) / 3)   # among [2, 3, 0.5]: one below, one tie


def _chain(day_prev, exp, root, F, D, T, vol, strikes):
    rows = []
    for K in strikes:
        for right, is_call in (("C", True), ("P", False)):
            px = float(gex.black76_price(F, K, T, vol, D, is_call))
            half = max(0.05, 0.01 * px)
            rows.append({"quote_date": day_prev, "symbol": root, "expiration": exp, "strike": K, "right": right,
                         "bid": px - half, "ask": px + half, "close": px, "volume": 1})
    return pd.DataFrame(rows)


def test_compute_day_on_flat_vol_chain():
    cfg = load_config()
    day, prev = dt.date(2024, 3, 6), dt.date(2024, 3, 5)
    F, D, vol = 5100.0, 0.999, 0.15
    strikes = np.arange(4800, 5405, 5.0)
    quotes, oi = [], []
    for k_days in (0, 2, 9):
        exp = day + dt.timedelta(days=k_days)
        Tq = gex.year_frac(pd.Timestamp("2024-03-05 16:15", tz="America/New_York").tz_convert("UTC"),
                           gex.expiry_ts("SPXW", exp, cfg), cfg)
        quotes.append(_chain(prev, exp, "SPXW", F, D, Tq, vol, strikes))
        for K in strikes:
            # Calls concentrated at 5200, puts at 5000
            oi.append({"as_of_date": day, "symbol": "x", "root": "SPXW", "expiration": exp, "strike": K,
                       "right": "C", "open_interest": 5000 if K == 5200 else 100})
            oi.append({"as_of_date": day, "symbol": "x", "root": "SPXW", "expiration": exp, "strike": K,
                       "right": "P", "open_interest": 8000 if K == 5000 else 100})
    res = gex.compute_day(day, pd.concat(quotes), pd.DataFrame(oi), 5098.0, cfg, prev)
    row = res["row"]
    assert row["s0"] == pytest.approx(F, abs=1e-3)
    assert res["vols"]["iv"].between(vol - 0.003, vol + 0.003).all()
    assert row["call_wall"] == 5200
    assert row["put_wall"] == 5000
    # Calls above spot / puts below: expect a flip between the put and call walls
    assert np.isfinite(row["flip"]) and 5000 < row["flip"] < 5200
    # EM = ATM straddle on the 0DTE expiry
    assert 0 < row["em"] < 60
    assert row["net_gex_0dte"] != 0
