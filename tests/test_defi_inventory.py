"""Study 17 step 0: DefiLlama pool selection, parsers, sealing and the weekly fees-vs-LVR arithmetic, by hand."""
import numpy as np
import pandas as pd
import pytest

from src import defi_inventory as di

D = 86_400


def test_select_pools_keeps_volatile_two_token_pools():
    pools = {"data": [
        {"pool": "a", "chain": "Ethereum", "project": "uniswap-v3", "symbol": "PEPE-WETH", "tvlUsd": 5e6,
         "exposure": "multi", "ilRisk": "yes", "stablecoin": False, "underlyingTokens": ["0xp", "0xw"]},
        {"pool": "b", "chain": "Ethereum", "project": "uniswap-v3", "symbol": "USDC-USDT", "tvlUsd": 9e7,
         "exposure": "multi", "ilRisk": "no", "stablecoin": True, "underlyingTokens": ["0x1", "0x2"]},
        {"pool": "c", "chain": "Base", "project": "aave-v3", "symbol": "WETH", "tvlUsd": 9e7,
         "exposure": "single", "ilRisk": "no", "stablecoin": False, "underlyingTokens": ["0x3"]},
        {"pool": "d", "chain": "Base", "project": "aerodrome-v1", "symbol": "X-WETH", "tvlUsd": 5e4,
         "exposure": "multi", "ilRisk": "yes", "stablecoin": False, "underlyingTokens": ["0x4", "0x5"]},
        {"pool": "e", "chain": "BSC", "project": "pancakeswap-amm", "symbol": "A-B-C", "tvlUsd": 5e6,
         "exposure": "multi", "ilRisk": "yes", "stablecoin": False, "underlyingTokens": ["0x6", "0x7", "0x8"]}]}
    sel = di.select_pools(pools, min_tvl=1e5)
    assert [p["pool"] for p in sel] == ["a"]                 # stable pair, single asset, small, three tokens dropped
    assert sel[0]["coins"] == ["ethereum:0xp", "ethereum:0xw"]


def test_pool_chart_parser_and_seal():
    hs = pd.Timestamp("2025-10-01", tz="UTC")
    chart = {"status": "success", "data": [
        {"timestamp": "2025-09-29T23:01:12.000Z", "tvlUsd": 100.0, "apyBase": 36.5},
        {"timestamp": "2025-09-30T23:01:12.000Z", "tvlUsd": 110.0, "apyBase": None},
        {"timestamp": "2025-10-01T23:01:12.000Z", "tvlUsd": 999.0, "apyBase": 99.0}]}
    df = di.parse_pool_chart(chart, hs)
    assert list(df["day"]) == [pd.Timestamp("2025-09-29"), pd.Timestamp("2025-09-30")]   # holdout row dropped
    assert df["fee_yield"].iloc[0] == pytest.approx(0.365) and np.isnan(df["fee_yield"].iloc[1])


def test_price_chart_parser():
    js = {"coins": {"ethereum:0xp": {"prices": [{"timestamp": 1_700_000_000, "price": 2.0},
                                                {"timestamp": 1_700_000_000 + D, "price": 3.0}]}}}
    s = di.parse_price_chart(js, "ethereum:0xp", pd.Timestamp("2025-10-01", tz="UTC"))
    assert list(s.values) == [2.0, 3.0] and s.index[1] - s.index[0] == pd.Timedelta(days=1)


def test_weekly_fees_vs_lvr_by_hand():
    days = pd.date_range("2024-01-01", periods=14, freq="D")            # Monday start: two full weeks
    pool = pd.DataFrame({"day": days, "tvl": 1e6, "fee_yield": 0.365})   # 0.1% a day in fees
    p0 = pd.Series(100.0 * np.exp(np.r_[0, np.cumsum([0.1] * 7 + [0.0] * 6)]), index=days)  # +10%/day, then flat
    p1 = pd.Series(1.0, index=days)
    w = di.weekly_lp(pool, p0, p1)
    # week 1: day 1 has no prior price, so fees and LVR are both counted on days 2..7 only (6 days, 0.1 each)
    assert w["fees"].iloc[0] == pytest.approx(0.006)
    assert w["lvr"].iloc[0] == pytest.approx(6 * 0.01 / 8)
    assert w["net"].iloc[0] == pytest.approx(0.006 - 0.0075)
    # week 2: the 0.1 move into day 8 (Monday) then flat: one squared return
    assert w["lvr"].iloc[1] == pytest.approx(0.01 / 8) and w["jump_days"].iloc[1] == 0
    assert w["implied_vol"].iloc[0] == pytest.approx(np.sqrt(8 * 0.365))


def test_trailing_premium_uses_only_past_weeks():
    w = pd.DataFrame({"week": pd.date_range("2024-01-01", periods=6, freq="W-MON"), "net": [1.0, 1, 1, 1, -5, 2]})
    t = di.trailing_premium(w, weeks=4)
    assert np.isnan(t.iloc[3]) and t.iloc[4] == pytest.approx(1.0) and t.iloc[5] == pytest.approx(-0.5)
