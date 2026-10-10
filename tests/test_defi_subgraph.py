"""Study 17 step 0b: exact pool-level LVR from Uniswap subgraph data (v3 liquidity at the price, v2 reserves)."""
import math

import numpy as np
import pandas as pd
import pytest

from src import defi_subgraph as sg

Q96 = 2 ** 96
D = 86_400
T0 = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())          # a Monday


def test_virtual_value_from_liquidity_and_sqrt_price():
    # P_raw = 4 (token1 raw per token0 raw) -> sqrtP = 2; L = 1e18 -> virtual token1 reserve = L*sqrtP = 2e18 raw
    v = sg.v3_virtual_usd(liquidity=1e18, sqrt_price_x96=2 * Q96, dec1=18, t1_usd=3.0)
    assert v == pytest.approx(2 * 2.0 * 3.0)                          # 2 x (2 token1 x $3)


def test_daily_variance_takes_the_larger_estimate():
    df = pd.DataFrame({"high": [100.0, 110.0, 50.0], "low": [100.0, 100.0, 60.0],   # day 3: high < low (bad)
                       "close": [100.0, 105.0, 120.0]})
    v = sg.daily_variance(df)
    park = math.log(1.1) ** 2 / (4 * math.log(2))
    c2c = math.log(105 / 100) ** 2
    assert np.isnan(v[0])                                             # no prior close, flat range: unknown
    assert v[1] == pytest.approx(max(park, c2c))
    assert v[2] == pytest.approx(math.log(120 / 105) ** 2)           # invalid range: close-to-close only


def test_v3_daily_lvr_and_fees():
    days = pd.DataFrame({"date": [T0, T0 + D], "liquidity": [1e18, 1e18], "sqrtPrice": [2 * Q96, 2 * Q96],
                         "feesUSD": [10.0, 20.0], "tvlUSD": [6.0, 6.0], "volumeUSD": [1.0, 1.0],
                         "high": [100.0, 110.0], "low": [100.0, 100.0], "close": [100.0, 105.0]})
    t1 = pd.Series([3.0, 3.0], index=pd.to_datetime([T0, T0 + D], unit="s"))
    d = sg.v3_daily(days, dec1=18, t1_usd=t1, fee_tier=3000)
    var = max(math.log(1.1) ** 2 / (4 * math.log(2)), math.log(1.05) ** 2)
    assert d["lvr_usd"].iloc[1] == pytest.approx(var * 12.0 / 8)     # virtual value $12
    assert d["fees_usd"].iloc[1] == 20.0 and d["concentration"].iloc[1] == pytest.approx(12.0 / 6.0)


def test_v2_daily_fees_from_volume_and_lvr_from_reserves():
    days = pd.DataFrame({"date": [T0, T0 + D], "reserveUSD": [1000.0, 1000.0], "dailyVolumeUSD": [0.0, 500.0],
                         "reserve0": [10.0, 10.0], "reserve1": [100.0, 121.0]})
    d = sg.v2_daily(days, fee=0.003)
    assert d["fees_usd"].iloc[1] == pytest.approx(1.5)
    assert d["lvr_usd"].iloc[1] == pytest.approx(math.log(12.1 / 10) ** 2 * 1000 / 8)
    assert d["concentration"].iloc[1] == 1.0


def test_weekly_normalises_by_mean_tvl_and_drops_holdout():
    days = pd.date_range("2025-09-22", periods=14, freq="D")          # a Monday; second week crosses 2025-10-01
    d = pd.DataFrame({"day": days, "fees_usd": 10.0, "lvr_usd": 4.0, "tvl_usd": 1000.0, "var": 0.0004,
                      "jump": False, "concentration": 2.0})
    w = sg.weekly(d, holdout=pd.Timestamp("2025-10-01"))
    assert len(w) == 1                                                 # the week reaching the holdout is dropped
    assert w["fees"].iloc[0] == pytest.approx(70 / 1000) and w["lvr"].iloc[0] == pytest.approx(28 / 1000)
    assert w["net"].iloc[0] == pytest.approx(0.042)
    assert w["implied_vol"].iloc[0] == pytest.approx(math.sqrt(8 * (70 / 7 * 365) / 2000))   # vs virtual value


def test_stable_pairs_excluded():
    assert sg.is_stable_pair("USDC", "USDT") and not sg.is_stable_pair("WETH", "USDC")
