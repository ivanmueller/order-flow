"""Study 20 data: Cboe VX settlement files, expiry rule, the pre-2007 x10 price scale, front/second panel, by hand."""
import datetime as dt

import pandas as pd
import pytest

from src import vix_futures as vf


def test_expiry_rule_is_30_days_before_next_months_third_friday():
    # Jan 2024 contract: third Friday of Feb 2024 is Feb 16 -> Wednesday Jan 17
    assert vf.expiry_for(2024, 1) == dt.date(2024, 1, 17)
    # Mar 2018: third Friday of Apr 2018 is Apr 20 -> Mar 21
    assert vf.expiry_for(2018, 3) == dt.date(2018, 3, 21)


def test_parse_new_and_archive_formats_and_scale():
    new = ("Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest\n"
           "2024-01-02,F (Jan 2024),13.9,14.2,13.8,14.0,14.05,0.1,1000,0,5000\n"
           "2024-01-17,F (Jan 2024),0,0,0,0,13.5,0,0,0,0\n")
    d = vf.parse(new, expiry=dt.date(2024, 1, 17))
    assert list(d["settle"]) == [14.05, 13.5] and (d["expiry"] == dt.date(2024, 1, 17)).all()
    old = ("Trade Date,Futures,Open,High,Low,Close,Settle,Change,Total Volume,EFP,Open Interest\n"
           "01/03/2006,F (Jan 06),120.0,121.0,119.0,120.5,120.3,0,10,0,100\n")
    o = vf.parse(old, expiry=dt.date(2006, 1, 18))
    assert o["settle"].iloc[0] == pytest.approx(12.03)       # VBI-era prices are 10 x VIX before 2007-03-26


def test_front_second_panel_skips_the_expiry_day():
    rows = []
    for exp, prices in ((dt.date(2024, 1, 17), {dt.date(2024, 1, 16): 13.0, dt.date(2024, 1, 17): 13.2}),
                        (dt.date(2024, 2, 14), {dt.date(2024, 1, 16): 14.0, dt.date(2024, 1, 17): 14.1,
                                                dt.date(2024, 1, 18): 14.2}),
                        (dt.date(2024, 3, 20), {dt.date(2024, 1, 16): 15.0, dt.date(2024, 1, 17): 15.1,
                                                dt.date(2024, 1, 18): 15.3})):
        for d, s in prices.items():
            rows.append({"date": d, "expiry": exp, "settle": s})
    p = vf.front_second(pd.DataFrame(rows))
    a = p.set_index("date")
    assert a.loc[dt.date(2024, 1, 16), "vx1"] == 13.0 and a.loc[dt.date(2024, 1, 16), "vx2"] == 14.0
    # on its expiry day the January contract is no longer the front: February and March are
    assert a.loc[dt.date(2024, 1, 17), "vx1"] == 14.1 and a.loc[dt.date(2024, 1, 17), "vx2"] == 15.1
    assert a.loc[dt.date(2024, 1, 16), "exp1"] == dt.date(2024, 1, 17)
