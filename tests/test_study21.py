"""Study 21 survivors-only pilot: scoring, cheap tercile, entry rules, monthly calendar-time portfolios, by hand."""
import numpy as np
import pandas as pd
import pytest

from src import study21 as s

P = {"s21_min_signals": 7, "s21_strong_min": 7, "s21_weak_max": 2, "s21_float_min": 5e6, "s21_float_max": 300e6,
     "s21_bm_quantile": 2 / 3, "s21_entry_lag_days": 2, "s21_hold_days": 5, "s21_min_price": 1.0,
     "s21_min_dollar_volume": 50_000, "s21_adv_window": 3,
     "s21_cost_buckets": [[250_000, 0.03], [1_000_000, 0.02], [1e18, 0.01]]}
CFG = {"params": {k: {"value": v} for k, v in P.items()}}
SIG = ["s_roa", "s_cfo", "s_droa", "s_accrual", "s_dlever", "s_dliquid", "s_eq", "s_dmargin", "s_dturn"]


def _row(passes: int, n: int = 9, **kw):
    sig = [1.0] * passes + [0.0] * (n - passes) + [np.nan] * (9 - n)
    d = dict(zip(SIG, sig))
    d.update({"n_signals": n, "cik": 1, "filed": pd.Timestamp("2014-03-03"), "float_usd": 50e6, "equity": 60e6,
              "bm": 1.2, "sic": 3571, "tickers_now": "ABC"})
    d.update(kw)
    return d


def test_score_uses_share_of_available_signals():
    fy = pd.DataFrame([_row(7), _row(6, n=7), _row(2), _row(6), _row(5, n=6), _row(1, n=7)])
    g = s.score(fy, CFG)
    # 7/9 strong; 6/7 = 7.71/9 strong; 2/9 weak; 6/9 neither; 6 signals not eligible; 1/7 = 1.29/9 weak
    assert list(g["eligible"]) == [True, True, True, True, False, True]
    assert list(g["strong"]) == [True, True, False, False, False, False]
    assert list(g["weak"]) == [False, False, True, False, False, True]


def test_universe_cheap_tercile_within_filing_year_and_filters():
    rows = [_row(7, cik=i, bm=b) for i, b in enumerate([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], 1)]
    rows += [_row(7, cik=10, bm=5.0, sic=6022),                  # financial: out
             _row(7, cik=11, bm=5.0, float_usd=400e6),           # too big: out
             _row(7, cik=12, bm=5.0, equity=-1.0),               # negative book: out
             _row(7, cik=13, bm=0.05, filed=pd.Timestamp("2015-03-02"))]   # own cohort
    U = s.universe(pd.DataFrame(rows), CFG, "2013-01-01", "2018-12-31")
    assert set(U["cik"]) == {1, 2, 3, 4, 5, 6, 13}
    cheap = set(U.loc[U["cheap"], "cik"])
    assert cheap == {5, 6, 13}                                   # top third of 2014 (0.5, 0.6); 2015 alone -> itself


def _px(closes, vols=None, start="2014-03-03"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"close": closes, "volume": vols or [100_000] * len(closes)}, index=idx)


def test_entry_is_the_close_of_the_second_trading_day_after_filing_with_filters():
    px = _px([10, 10, 10, 11, 12, 13, 14, 15, 16, 17, 18])      # filed Mon 2014-03-03 = day 0
    p = s.position(pd.Timestamp("2014-03-03"), px, CFG, end=pd.Timestamp("2020-01-01"))
    assert p["entry"] == pd.Timestamp("2014-03-05") and p["entry_px"] == 10
    assert p["exit"] == pd.Timestamp("2014-03-12") and p["exit_px"] == 15   # day 2 + 5 trading days = day 7
    assert p["cost"] == 0.01                                     # $1.0M+ a day traded -> 1% round trip
    cheap = _px([0.5] * 11)
    assert s.position(pd.Timestamp("2014-03-03"), cheap, CFG, end=pd.Timestamp("2020-01-01")) is None   # < $1
    thin = _px([10] * 11, vols=[1_000] * 11)
    assert s.position(pd.Timestamp("2014-03-03"), thin, CFG, end=pd.Timestamp("2020-01-01")) is None    # $10k/day


def test_monthly_returns_of_a_position_and_costs():
    # entry 2014-01-30 at 10, Jan 31 close 11, Feb 28 close 12.1, exit 2014-03-04 at 13.31
    idx = pd.DatetimeIndex(["2014-01-30", "2014-01-31", "2014-02-28", "2014-03-03", "2014-03-04"])
    px = pd.Series([10, 11, 12.1, 12.5, 13.31], index=idx)
    m = s.monthly_legs(px, cost=0.02)
    assert m[pd.Period("2014-01", "M")] == pytest.approx(0.10 - 0.01)
    assert m[pd.Period("2014-02", "M")] == pytest.approx(0.10)
    assert m[pd.Period("2014-03", "M")] == pytest.approx(13.31 / 12.1 - 1 - 0.01)


def test_portfolio_is_equal_weight_across_positions_held_each_month():
    a = pd.Series({pd.Period("2014-01", "M"): 0.10, pd.Period("2014-02", "M"): 0.00})
    b = pd.Series({pd.Period("2014-02", "M"): 0.20})
    p = s.portfolio([a, b])
    assert p["ret"][pd.Period("2014-01", "M")] == pytest.approx(0.10)
    assert p["ret"][pd.Period("2014-02", "M")] == pytest.approx(0.10)
    assert list(p["names"]) == [1, 2]
