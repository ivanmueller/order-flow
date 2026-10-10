"""Price menu: hand-checked against a fake client that bills a fixed rate per minute."""
import pandas as pd
import pytest

from src import price_menu as pm

RATE = {"trades": 0.001, "ohlcv-1m": 0.00001}  # USD per minute of window


class FakeMeta:
    def __init__(self):
        self.calls = []

    def get_cost(self, **a):
        self.calls.append(a)
        mins = (pd.Timestamp(a["end"]) - pd.Timestamp(a["start"])).total_seconds() / 60
        return RATE[a["schema"]] * mins


class FakeClient:
    def __init__(self):
        self.metadata = FakeMeta()


def test_rth_window_utc_follows_dst():
    s, e = pm.rth_window_utc(pd.Timestamp("2024-07-10"), "09:30", "16:00")
    assert (s, e) == (pd.Timestamp("2024-07-10 13:30", tz="UTC"), pd.Timestamp("2024-07-10 20:00", tz="UTC"))
    s, e = pm.rth_window_utc(pd.Timestamp("2024-01-10"), "09:30", "16:00")
    assert (s, e) == (pd.Timestamp("2024-01-10 14:30", tz="UTC"), pd.Timestamp("2024-01-10 21:00", tz="UTC"))


def test_month_spans_clip_to_range():
    spans = pm.month_spans("2024-01-15", "2024-03-10")
    assert spans == [(pd.Timestamp("2024-01-15"), pd.Timestamp("2024-02-01")),
                     (pd.Timestamp("2024-02-01"), pd.Timestamp("2024-03-01")),
                     (pd.Timestamp("2024-03-01"), pd.Timestamp("2024-03-11"))]


def test_weekdays_and_even_sample():
    days = pm.weekdays("2024-07-01", "2024-07-14")  # Mon 1st .. Sun 14th -> 10 weekdays
    assert len(days) == 10 and days[0] == pd.Timestamp("2024-07-01") and days[-1] == pd.Timestamp("2024-07-12")
    assert pm.even_sample(days, 3) == [days[0], days[4], days[9]]
    assert pm.even_sample(days, 50) == days


def test_price_24h_is_exact_sum_over_months():
    cl = FakeClient()
    usd = pm.price_24h(cl, "GLBX.MDP3", "ES.v.0", "trades", "2024-02-01", "2024-03-31")
    # Feb 2024 (29 days) + Mar 2024 (31 days) = 60 days of minutes
    assert usd == pytest.approx(60 * 1440 * 0.001)
    a = cl.metadata.calls[0]
    assert a["stype_in"] == "continuous" and a["symbols"] == "ES.v.0" and a["dataset"] == "GLBX.MDP3"


def test_price_rth_extrapolates_sample_to_all_weekdays():
    cl = FakeClient()
    est, n_days, n_priced = pm.price_rth(cl, "GLBX.MDP3", "ES.v.0", "trades", "2024-07-01", "2024-07-14",
                                         "09:30", "16:00", sample=4)
    assert (n_days, n_priced) == (10, 4)
    assert est == pytest.approx(10 * 390 * 0.001)  # every RTH window is 390 minutes


def test_menu_rows_and_never_pulls():
    cl = FakeClient()
    df = pm.menu(cl, "GLBX.MDP3", ["ES.v.0", "CL.v.0"], "2024-07-01", "2024-07-31",
                 rth_open="09:30", rth_close="16:00", rth_sample=5)
    assert set(df["schema"]) == {"trades", "ohlcv-1m"}
    assert len(df) == 2 * 3  # per symbol: trades 24h, trades RTH, bars 24h
    es = df[(df.symbol == "ES.v.0") & (df.schema == "trades") & (df.window == "24h")].usd.item()
    assert es == pytest.approx(31 * 1440 * 0.001)
    assert not hasattr(cl, "timeseries")  # pricing only: the fake has no data endpoint at all
