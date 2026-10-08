"""Study 10 step 0: pricing OPRA data for the priority-customer pilot (quotes only). Hand-checked with a fake client."""
import pandas as pd
import pytest

from src import study10


class FakeMeta:
    def __init__(self, table):
        self.table, self.calls = table, []

    def get_cost(self, **kw):
        self.calls.append(kw)
        v = self.table[(kw["symbols"][0], kw["schema"])]
        if isinstance(v, Exception):
            raise v
        return v


class FakeClient:
    def __init__(self, table):
        self.metadata = FakeMeta(table)


def test_rth_args():
    a = study10.cost_args("OPRA.PILLAR", "SPY.OPT", "tcbbo", pd.Timestamp("2024-03-05"), "09:30", "16:00")
    assert a["stype_in"] == "parent" and a["symbols"] == ["SPY.OPT"] and a["schema"] == "tcbbo"
    assert a["start"] == "2024-03-05T14:30:00+00:00" and a["end"] == "2024-03-05T21:00:00+00:00"   # EST
    b = study10.cost_args("OPRA.PILLAR", "SPY.OPT", "tcbbo", pd.Timestamp("2024-07-09"), "09:30", "16:00")
    assert b["start"] == "2024-07-09T13:30:00+00:00"                                               # EDT


def test_price_table_means_and_errors():
    cl = FakeClient({("SPY.OPT", "tcbbo"): 2.0, ("SPY.OPT", "cbbo-1m"): 1.0,
                     ("XSP.OPT", "tcbbo"): 0.2, ("XSP.OPT", "cbbo-1m"): ValueError("schema not available")})
    days = [pd.Timestamp("2024-03-05"), pd.Timestamp("2024-03-06")]
    df = study10.price_table(cl, "OPRA.PILLAR", ["SPY.OPT", "XSP.OPT"], ["tcbbo", "cbbo-1m"], days,
                             "09:30", "16:00", sessions=5)
    spy = df[df["parent"] == "SPY.OPT"].set_index("schema")
    assert spy.loc["tcbbo", "usd_per_session"] == pytest.approx(2.0)
    assert spy.loc["tcbbo", "usd_for_sessions"] == pytest.approx(10.0)
    tot = df[(df["parent"] == "SPY.OPT") & (df["schema"] == "total")].iloc[0]
    assert tot["usd_per_session"] == pytest.approx(3.0) and tot["usd_for_sessions"] == pytest.approx(15.0)
    bad = df[(df["parent"] == "XSP.OPT") & (df["schema"] == "cbbo-1m")].iloc[0]
    assert pd.isna(bad["usd_per_session"]) and "schema not available" in bad["error"]
    xt = df[(df["parent"] == "XSP.OPT") & (df["schema"] == "total")].iloc[0]
    assert pd.isna(xt["usd_per_session"])                 # a missing pilot schema makes the total unknown, not cheap
    assert len(cl.metadata.calls) == 7          # an unavailable schema is not re-priced on later days


def test_sample_days_even_and_in_range():
    d = study10.sample_days("2023-06-01", "2025-12-31", 3)
    assert len(d) == 3 and d[0] == pd.Timestamp("2023-06-01") and d[-1] == pd.Timestamp("2025-12-31")
    assert all(x.weekday() < 5 for x in d)
