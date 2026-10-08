"""Study 10 step 0: pricing OPRA data for the priority-customer pilot (quotes only). Hand-checked with a fake client."""
import pandas as pd
import pytest

from src import study10


class FakeMeta:
    """Cost = rate[(parent, schema)] per minute of the requested window; an Exception value always raises."""

    def __init__(self, rate, fail_first=None):
        self.rate, self.calls, self.fail_first = rate, [], set(fail_first or [])

    def get_cost(self, **kw):
        self.calls.append(kw)
        key = (kw["symbols"][0], kw["schema"])
        v = self.rate[key]
        if isinstance(v, Exception):
            raise v
        if (key, kw["start"]) in self.fail_first:          # one transient failure, then success
            self.fail_first.discard((key, kw["start"]))
            raise TimeoutError("504 The remote gateway timed out.")
        mins = (pd.Timestamp(kw["end"]) - pd.Timestamp(kw["start"])).total_seconds() / 60
        return v * mins


class FakeClient:
    def __init__(self, rate, fail_first=None):
        self.metadata = FakeMeta(rate, fail_first)


def test_rth_args_and_chunks():
    ch = study10.chunks(pd.Timestamp("2024-03-05"), "09:30", "16:00", 30)
    assert len(ch) == 13
    assert ch[0][0].isoformat() == "2024-03-05T14:30:00+00:00"            # EST
    assert ch[-1][1].isoformat() == "2024-03-05T21:00:00+00:00"
    assert all(e > s for s, e in ch) and all(ch[i][1] == ch[i + 1][0] for i in range(12))
    ch7 = study10.chunks(pd.Timestamp("2024-07-09"), "09:30", "16:00", 60)  # EDT; 6.5 h in 60-min pieces
    assert ch7[0][0].isoformat() == "2024-07-09T13:30:00+00:00" and len(ch7) == 7
    assert (ch7[-1][1] - ch7[-1][0]) == pd.Timedelta(minutes=30)            # last piece clipped at the close
    a = study10.cost_args("OPRA.PILLAR", "SPY.OPT", "tcbbo", *ch[0])
    assert a["stype_in"] == "parent" and a["symbols"] == ["SPY.OPT"] and a["schema"] == "tcbbo"


def test_price_table_sums_chunks_and_reports_errors():
    cl = FakeClient({("SPY.OPT", "tcbbo"): 0.01, ("SPY.OPT", "cbbo-1m"): 0.002,
                     ("XSP.OPT", "tcbbo"): 0.001, ("XSP.OPT", "cbbo-1m"): ValueError("schema not available")})
    days = [pd.Timestamp("2024-03-05"), pd.Timestamp("2024-03-06")]
    df = study10.price_table(cl, "OPRA.PILLAR", ["SPY.OPT", "XSP.OPT"], ["tcbbo", "cbbo-1m"], days,
                             "09:30", "16:00", sessions=5, chunk_min=30, workers=1, attempts=2)
    spy = df[df["parent"] == "SPY.OPT"].set_index("schema")
    assert spy.loc["tcbbo", "usd_per_session"] == pytest.approx(0.01 * 390)       # 13 pieces summed = 390 min
    assert spy.loc["tcbbo", "usd_for_sessions"] == pytest.approx(0.01 * 390 * 5)
    assert spy.loc["total", "usd_per_session"] == pytest.approx(0.012 * 390)
    bad = df[(df["parent"] == "XSP.OPT") & (df["schema"] == "cbbo-1m")].iloc[0]
    assert pd.isna(bad["usd_per_session"]) and "schema not available" in bad["error"]
    xt = df[(df["parent"] == "XSP.OPT") & (df["schema"] == "total")].iloc[0]
    assert pd.isna(xt["usd_per_session"])                 # a missing pilot schema makes the total unknown, not cheap


def test_transient_failure_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(study10, "RETRY_WAIT_S", 0)
    first = study10.chunks(pd.Timestamp("2024-03-05"), "09:30", "16:00", 30)[0][0].isoformat()
    cl = FakeClient({("SPY.OPT", "tcbbo"): 0.01}, fail_first={(("SPY.OPT", "tcbbo"), first)})
    df = study10.price_table(cl, "OPRA.PILLAR", ["SPY.OPT"], ["tcbbo"], [pd.Timestamp("2024-03-05")],
                             "09:30", "16:00", sessions=1, chunk_min=30, workers=2, attempts=2)
    assert df.set_index("schema").loc["tcbbo", "usd_per_session"] == pytest.approx(3.9)
    assert len(cl.metadata.calls) == 14                   # 13 pieces + one retry


def test_sample_days_even_and_in_range():
    d = study10.sample_days("2023-06-01", "2025-12-31", 3)
    assert len(d) == 3 and d[0] == pd.Timestamp("2023-06-01") and d[-1] == pd.Timestamp("2025-12-31")
    assert all(x.weekday() < 5 for x in d)


def test_one_sample_day_is_the_middle():
    d = study10.sample_days("2024-01-01", "2024-01-31", 1)
    assert d == [list(pd.bdate_range("2024-01-01", "2024-01-31"))[11]]
