"""Study 21b: earnings dates from SEC submissions, expected dates from last year, entry/exit around them, by hand."""
import numpy as np
import pandas as pd
import pytest

from src import study21b as e

P = {"s21_min_price": 1.0, "s21_min_dollar_volume": 50_000, "s21_adv_window": 3,
     "s21_cost_buckets": [[250_000, 0.03], [1_000_000, 0.02], [1e18, 0.01]],
     "s21b_exit_after_days": 2, "s21b_no_report_exit_days": 15, "s21b_8k_window_days": 60,
     "s21b_prior_tol_days": 20, "s21b_fy_max_age_days": 456}
CFG = {"params": {k: {"value": v} for k, v in P.items()}}


def _sub(rows):
    keys = ["accessionNumber", "form", "filingDate", "reportDate", "items"]
    return {"cik": "7", "filings": {"recent": {k: [r[i] for r in rows] for i, k in enumerate(keys)}}}


def test_announcement_is_the_earlier_of_the_8k_earnings_release_and_the_filing():
    js = _sub([
        ("a1", "10-Q", "2014-05-12", "2014-03-31", ""),
        ("a2", "8-K", "2014-05-01", "", "2.02,9.01"),          # earnings release before the 10-Q -> announcement
        ("a3", "8-K", "2014-04-20", "", "5.02"),               # not an earnings item
        ("a4", "10-Q/A", "2014-06-01", "2014-03-31", ""),      # amendment ignored
        ("a5", "10-Q", "2014-08-11", "2014-06-30", ""),        # no release: the filing is the news
        ("a6", "8-K", "2014-01-15", "", "2.02"),               # belongs to an earlier period: not before 03-31
    ])
    E = e.earnings_events(e.parse_submissions(js), CFG)
    E = E.set_index("period")
    assert E.loc[pd.Timestamp("2014-03-31"), "ann"] == pd.Timestamp("2014-05-01")
    assert E.loc[pd.Timestamp("2014-06-30"), "ann"] == pd.Timestamp("2014-08-11")
    assert len(E) == 2


def test_paging_files_have_arrays_at_the_top():
    js = _sub([("a1", "10-K", "2015-03-10", "2014-12-31", "")])["filings"]["recent"]
    F = e.parse_submissions(js, cik=7)
    assert list(F["form"]) == ["10-K"] and F["cik"].iloc[0] == 7


def test_expected_date_uses_last_years_lag_after_period_end():
    E = pd.DataFrame({"cik": [7, 7, 7], "period": pd.to_datetime(["2013-03-31", "2014-03-31", "2014-06-30"]),
                      "ann": pd.to_datetime(["2013-05-06", "2014-05-01", "2014-08-11"])})
    X = e.expected_dates(E, CFG).set_index("period")
    assert X.loc[pd.Timestamp("2014-03-31"), "expected"] == pd.Timestamp("2014-05-06")   # 36 days after 03-31
    assert pd.isna(X.loc[pd.Timestamp("2014-06-30"), "expected"])                         # no prior-year match
    assert pd.isna(X.loc[pd.Timestamp("2013-03-31"), "expected"])


def _px(n=60, start="2014-04-01", vol=200_000):
    idx = pd.bdate_range(start, periods=n)                      # 04-01 = day 0, 05-02 = day 23, 05-06 = day 25
    return pd.DataFrame({"close": 10.0 + np.arange(n) * 0.1, "volume": float(vol)}, index=idx)


def test_trade_window_entry_k_days_before_expected_exit_two_after_actual():
    px = _px()
    t = e.trade(pd.Timestamp("2014-05-06"), pd.Timestamp("2014-05-08"), px, 2, CFG)
    assert t["entry"] == pd.Timestamp("2014-05-02")            # day 25 - 2 = day 23
    assert t["exit"] == pd.Timestamp("2014-05-12")             # actual day 27 + 2 = day 29
    assert t["gross"] == pytest.approx(12.9 / 12.3 - 1)
    assert t["cost"] == 0.01 and t["net"] == pytest.approx(12.9 / 12.3 - 1 - 0.01)


def test_trade_skips_when_the_company_already_reported_by_entry():
    px = _px()
    assert e.trade(pd.Timestamp("2014-05-06"), pd.Timestamp("2014-05-01"), px, 2, CFG) is None   # reported day 22
    assert e.trade(pd.Timestamp("2014-05-06"), pd.Timestamp("2014-05-02"), px, 2, CFG) is None   # same day: skip


def test_trade_exits_when_no_report_arrives_in_time_and_applies_filters():
    px = _px()
    t = e.trade(pd.Timestamp("2014-05-06"), pd.Timestamp("2014-07-30"), px, 2, CFG)
    assert t["exit"] == px.index[25 + 15]                      # expected + 15 trading days
    thin = _px(vol=1_000)
    assert e.trade(pd.Timestamp("2014-05-06"), pd.Timestamp("2014-05-08"), thin, 2, CFG) is None


def test_score_at_entry_uses_latest_10k_filed_before_entry():
    fy = pd.DataFrame({"cik": [7, 7, 7], "filed": pd.to_datetime(["2013-03-01", "2014-03-03", "2014-05-02"]),
                       "strong": [False, True, False], "eligible": [True, True, True], "float_usd": [1, 2, 3]})
    r = e.latest_10k(fy, 7, pd.Timestamp("2014-05-02"), CFG)
    assert r["float_usd"] == 2 and bool(r["strong"])           # the 05-02 filing is not known before that day's close
    assert e.latest_10k(fy, 7, pd.Timestamp("2016-05-02"), CFG) is None   # older than ~15 months: stale
