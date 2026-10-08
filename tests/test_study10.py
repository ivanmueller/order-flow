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


# ================================================================================================
# The pilot: fills at the NBBO, marks, realized spread, buckets, fees, verdict (hand-checked)
# ================================================================================================
import datetime as dt

import numpy as np

from src.config import param

DAY = dt.date(2024, 3, 5)


def ts(hms):
    return pd.Timestamp(f"{DAY} {hms}", tz="America/New_York").tz_convert("UTC")


def trades(rows):
    """rows: (time, symbol, price, size, bid, ask, venue)."""
    return pd.DataFrame([{"ts": ts(t), "symbol": s, "price": p, "size": q, "bid": b, "ask": a, "venue": v}
                         for t, s, p, q, b, a, v in rows])


def quotes(rows):
    return pd.DataFrame([{"ts": ts(t), "symbol": s, "bid": b, "ask": a} for t, s, b, a in rows])


C = "IWM   240315C00200000"


def test_classify_fills_and_exclusions():
    tr = trades([("10:00:00", C, 1.00, 1, 1.00, 1.02, 1),      # at the bid: passive buyer, s = +1
                 ("10:00:01", C, 1.02, 3, 1.00, 1.02, 2),      # at the ask: passive seller, s = -1
                 ("10:00:02", C, 1.01, 1, 1.00, 1.02, 1),      # inside (auction / improvement)
                 ("10:00:03", C, 1.03, 1, 1.00, 1.02, 1),      # outside
                 ("10:00:04", C, 1.00, 1, 1.00, 1.00, 1),      # locked
                 ("10:00:05", C, 0.05, 1, 0.00, 0.05, 1),      # zero bid
                 ("10:00:06", C, 1.00, 1, np.nan, 1.02, 1)])   # no quote
    f, ex = study10.classify(tr)
    assert list(f["s"]) == [1, -1] and list(f["price"]) == [1.00, 1.02]
    assert ex == {"inside": 1, "outside": 1, "locked_or_crossed": 1, "zero_bid": 1, "no_quote": 1}


def test_sweep_prints_become_one_opportunity():
    tr = trades([("10:00:00.000", C, 1.00, 2, 1.00, 1.02, 1),
                 ("10:00:00.004", C, 1.00, 3, 1.00, 1.02, 2),    # same sweep (4 ms), another venue
                 ("10:00:00.008", C, 1.00, 1, 1.00, 1.02, 3),    # 4 ms after the previous print: same sweep
                 ("10:00:00.050", C, 1.00, 1, 1.00, 1.02, 1),    # 42 ms later: a new opportunity
                 ("10:00:00.051", C, 1.02, 1, 1.00, 1.02, 1)])   # other side: its own opportunity
    f, _ = study10.classify(tr)
    e = study10.dedupe_sweeps(f, sweep_ms=10)
    assert list(e["size"]) == [6, 1, 1] and list(e["n_prints"]) == [3, 1, 1]
    assert list(e["s"]) == [1, 1, -1]
    assert e["ts_last"].iloc[0] == ts("10:00:00.008")


def test_clearing_flag_next_print_through_the_price():
    tr = trades([("10:00:00", C, 1.00, 1, 1.00, 1.02, 1),       # passive buy at 1.00
                 ("10:00:30", C, 0.99, 1, 0.99, 1.01, 1),       # next print below 1.00 within 60 s: cleared
                 ("10:05:00", C, 1.02, 1, 1.00, 1.02, 1),       # passive sell at 1.02
                 ("10:05:20", C, 1.02, 1, 1.00, 1.02, 1),       # same price: not through
                 ("10:20:00", C, 1.00, 1, 1.00, 1.02, 1),       # passive buy
                 ("10:21:30", C, 0.90, 1, 0.90, 0.92, 1)])      # through, but 90 s later: not counted
    f, _ = study10.classify(tr)
    e = study10.dedupe_sweeps(f, sweep_ms=10)
    flags = study10.clearing_flags(e, tr, window_s=60)
    # six fills (the 0.99 and 0.90 prints sit at their own bids); only the first is traded through within 60 s
    assert list(flags) == [True, False, False, False, False, False]


def test_marks_rs_and_staleness():
    tr = trades([("10:00:00", C, 1.00, 1, 1.00, 1.02, 1),
                 ("15:58:00", C, 1.02, 1, 1.00, 1.02, 1)])
    f, _ = study10.classify(tr)
    e = study10.dedupe_sweeps(f, 10)
    obs = pd.concat([quotes([("10:00:30", C, 1.02, 1.04), ("10:04:00", C, 0.98, 1.00)]),
                     tr[["ts", "symbol", "bid", "ask"]]], ignore_index=True)
    m = study10.mark_mids(e, obs, horizons=[1, 5], session_end=ts("16:00:00"))
    # fill 0 (passive buy 1.00 at 10:00): +1 min -> quote of 10:00:30 (mid 1.03); +5 min -> 10:04:00 (mid 0.99)
    assert m.loc[0, "mid_1"] == pytest.approx(1.03) and m.loc[0, "mid_5"] == pytest.approx(0.99)
    assert bool(m.loc[0, "later_1"]) and bool(m.loc[0, "later_5"])
    # fill 1 at 15:58: +1 min = 15:59 inside the session, only its own pre-trade quote is known -> not later
    assert m.loc[1, "mid_1"] == pytest.approx(1.01) and not bool(m.loc[1, "later_1"])
    assert np.isnan(m.loc[1, "mid_5"])                          # 16:03 is past the session end
    rs = study10.realized_spread_usd(m, horizons=[1, 5], mult=100.0)
    assert rs.loc[0, "rs_1"] == pytest.approx(3.0) and rs.loc[0, "rs_5"] == pytest.approx(-1.0)
    assert rs.loc[1, "rs_1"] == pytest.approx(1.0)              # passive sell 1.02, mid 1.01 -> +$1


def test_buckets():
    assert list(study10.bucket([0.01, 0.03, 0.05, 0.10, 0.30], [0.01, 0.02, 0.05, 0.10, 0.25])) == \
        ["0.01-0.02", "0.02-0.05", "0.05-0.10", "0.10-0.25", "0.25+"]
    assert list(study10.bucket([0, 3, 10, 45], [0, 1, 8, 31])) == ["0-1", "1-8", "8-31", "31+"]
    assert study10.bucket([0.004], [0.01, 0.02])[0] is None


def test_fee_scenarios(cfg):
    prem = pd.Series([0.03, 0.07, 1.20])
    f = study10.fee_table(prem, cfg)
    assert list(f["zero_commission"]) == [0.0, 0.0, 0.0]
    assert list(f["pass_through"]) == pytest.approx([0.05, 0.05, 0.05])
    assert list(f["ibkr_tiered"]) == pytest.approx([0.30, 0.55, 0.70])
    assert list(f["ibkr_one_lot"]) == pytest.approx([1.05, 1.05, 1.05])
    assert param(cfg, "s10_gate_fee_usd") == 0.0


def test_draw_sessions(cfg):
    days = list(pd.bdate_range("2023-06-01", "2025-12-31").date)
    cal = pd.DataFrame({"date": days, "half_day": [d == dt.date(2023, 11, 24) for d in days]})
    a, b = study10.draw_sessions(cal, cfg), study10.draw_sessions(cal, cfg)
    assert a == b and len(a) == 5 and a == sorted(a)
    assert all(dt.date(2023, 6, 1) <= d < dt.date(2026, 1, 1) for d in a)
    cal2 = cal.assign(half_day=True)
    assert study10.draw_sessions(cal2, cfg) == []


def _fills_frame(n_by_session, rs5, rs15, bucket="0.01"):
    rows = []
    for k, n in enumerate(n_by_session):
        for _ in range(n):
            rows.append({"date": dt.date(2024, 1, 2 + k), "spread_b": bucket, "net_5": rs5, "net_15": rs15})
    return pd.DataFrame(rows)


def test_verdict(cfg):
    good = _fills_frame([300] * 5, 0.5, 0.3)
    v = study10.verdict(good, cfg, n_sessions=5)
    assert v["verdict"] == "ADVANCE" and v["advancing_buckets"] == ["0.01"]
    assert study10.verdict(_fills_frame([300] * 4, 0.5, 0.3), cfg, n_sessions=5)["verdict"] == "KILL"   # a session missing
    assert study10.verdict(_fills_frame([150] * 5, 0.5, 0.3), cfg, n_sessions=5)["verdict"] == "KILL"   # 750 < 1000
    assert study10.verdict(_fills_frame([300] * 5, 0.5, -0.1), cfg, n_sessions=5)["verdict"] == "KILL"  # 15 min < 0
    assert study10.verdict(_fills_frame([300] * 5, -0.2, 0.3), cfg, n_sessions=5)["verdict"] == "KILL"


class FakeBudget:
    def __init__(self, cost):
        self.cost, self.pulled = cost, []

    def price(self, cl, args):
        return self.cost

    def pull(self, cl, job, what, args):
        self.pulled.append(args)

        class D:
            def to_df(self_inner):
                return pd.DataFrame({"ts_recv": [pd.Timestamp(args["start"])], "symbol": [C], "bid_px_00": [1.0],
                                     "ask_px_00": [1.02], "price": [1.0], "size": [1], "publisher_id": [1]}).set_index("ts_recv")
        return D(), self.cost


def test_pull_prices_then_writes_and_is_idempotent(cfg, tmp_path):
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    days = [DAY]
    b = FakeBudget(0.01)
    q = study10.pull(c, None, b, days, price_only=True)
    assert q["pieces"] == 26 and q["usd"] == pytest.approx(0.26) and b.pulled == []     # 13 pieces x 2 schemas
    r = study10.pull(c, None, b, days, price_only=False)
    assert r["written"] == 26 and len(b.pulled) == 26
    r2 = study10.pull(c, None, b, days, price_only=False)
    assert r2["written"] == 0 and r2["skipped"] == 26                                   # idempotent


def test_session_pipeline_end_to_end(cfg, tmp_path):
    raw_t = pd.DataFrame({
        "ts_recv": [ts("10:00:00"), ts("10:00:00.003"), ts("10:02:00"), ts("11:00:00")],
        "symbol": [C, C, C, C], "price": [1.00, 1.00, 1.03, 1.01], "size": [1, 2, 1, 1],
        "bid_px_00": [1.00, 1.00, 1.01, 1.00], "ask_px_00": [1.02, 1.02, 1.03, 1.02], "publisher_id": [1, 2, 1, 1]})
    raw_q = pd.DataFrame({"ts_recv": [ts("10:01:00"), ts("10:06:00"), ts("10:16:00")], "symbol": [C, C, C],
                          "bid_px_00": [1.01, 1.02, 1.00], "ask_px_00": [1.03, 1.04, 1.02]})
    F, ex = study10.session_fills(raw_t, raw_q, DAY, cfg)
    assert len(F) == 2 and ex["inside"] == 1                        # the sweep is one fill; 1.01 is inside
    buy = F[F["s"] == 1].iloc[0]
    assert buy["rs_1"] == pytest.approx(2.0)                        # mid 1.02 at 10:01
    assert buy["rs_5"] == pytest.approx(2.0)                        # newest quote by 10:05: the 10:02 print's 1.01/1.03
    assert buy["rs_15"] == pytest.approx(3.0)                       # mid 1.03 at 10:06 (last quote before 10:15)
    assert buy["spread_b"] == "0.02-0.05" and buy["dte_b"] == "8-31" and buy["size"] == 3
    assert buy["net_5"] == pytest.approx(2.0)                       # gate fee $0
    rep = study10.report(F, cfg, n_sessions=1, excluded=ex)
    assert rep["verdict"]["verdict"] == "KILL"                      # far below 1,000 fills
    assert "0.02-0.05" in rep["by_spread"]
