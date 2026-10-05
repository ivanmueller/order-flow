import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import calendar as calm
from src import levels, regime, stats
from src.ingest_futures import merge_intervals, subtract_intervals
from src.ingest_options import normalize_eod, parse_osi


def test_in_sample_and_seal(cfg, monkeypatch):
    hs = calm.holdout_start(cfg)
    assert calm.in_sample(hs - dt.timedelta(days=1), cfg)
    assert not calm.in_sample(hs, cfg)
    df = pd.DataFrame({"date": [hs - dt.timedelta(days=1), hs, hs + dt.timedelta(days=3)]})
    assert len(calm.seal(df, "date", cfg=cfg)) == 1
    monkeypatch.delenv(calm.HOLDOUT_ENV, raising=False)
    with pytest.raises(calm.HoldoutSealed):
        calm.seal(df, "date", include_holdout=True, cfg=cfg)
    monkeypatch.setenv(calm.HOLDOUT_ENV, "1")
    assert len(calm.seal(df, "date", include_holdout=True, cfg=cfg)) == 3


def test_session_date_globex():
    ts = pd.Series(pd.to_datetime([
        "2024-03-03 18:00", "2024-03-04 09:30", "2024-03-04 16:59", "2024-03-04 18:01"]).tz_localize(calm.ET)
    ).dt.tz_convert("UTC")
    assert list(calm.session_date(ts)) == [dt.date(2024, 3, 4)] * 3 + [dt.date(2024, 3, 5)]


def test_build_calendar_flags_roll():
    from tests.conftest import make_bars
    b = pd.concat([make_bars(dt.date(2024, 3, 13), [1.0] * 400, start="09:30", instrument_id=1),
                   make_bars(dt.date(2024, 3, 14), [1.0] * 400, start="09:30", instrument_id=2),
                   make_bars(dt.date(2024, 3, 15), [1.0] * 150, start="09:30", instrument_id=2)])
    c = calm.build_calendar(b)
    assert list(c["roll"]) == [False, True, False]
    assert list(c["half_day"]) == [False, False, True]
    assert c.loc[1, "prev_date"] == dt.date(2024, 3, 13)


def test_merge_levels_chain_and_tags():
    raw = [(100.0, "round"), (100.5, "gamma_top"), (101.0, "pd_high"), (110.0, "on_low")]
    m = levels.merge_levels(raw, tol=0.5, tick=0.25)
    assert m[0][0] == 100.5 and m[0][1] == {"round", "gamma_top", "pd_high"}
    assert m[1] == (110.0, {"on_low"})


def test_day_levels_and_placebos(cfg):
    from tests.conftest import make_bars
    day = dt.date(2024, 3, 6)
    sess = pd.concat([make_bars(dt.date(2024, 3, 5), [5130.0] * 30, start="20:00"),
                      make_bars(day, [5105.0] * 400, start="09:30")])
    sess = sess.assign(ts_open_utc=sess["ts_open_utc"]).reset_index(drop=True)
    prev = make_bars(dt.date(2024, 3, 5), list(np.linspace(5080, 5120, 390)), start="09:30")
    g = pd.Series({"basis": 10.0, "em": 40.0, "s0": 5095.0, "flip": 5090.1, "call_wall": 5150.0,
                   "put_wall": 5000.0, "top1": 5100.0, "top2": 5075.0, "top3": np.nan,
                   "gex_pct": 0.7, "gex_pct_0dte": 0.4})
    lv = levels.day_levels(day, g, sess, prev, False, cfg)
    by_px = dict(zip(lv["level_es"], lv["tags"]))
    assert by_px[5100.0] == "gamma_flip"                                  # 5090.1 + 10 -> 5100.0
    assert "round" in by_px[5110.0] and "gamma_top" in by_px[5110.0]       # 5100 + 10
    assert 5160.0 not in by_px                                              # call wall outside 1 EM of 5105
    assert any("pd_high" in t for t in lv["tags"]) and any("on_low" in t for t in lv["tags"])
    assert (lv["dist_em"] <= 1.0).all()
    pl = levels.add_placebos(day, lv, g, cfg)
    assert len(pl) == 5 and pl["is_placebo"].all()
    gaps = np.abs(pl["level_es"].to_numpy()[:, None] - lv["level_es"].to_numpy()[None, :])
    assert (gaps >= 1.0).all()
    assert (levels.add_placebos(day, lv, g, cfg)["level_es"].values == pl["level_es"].values).all()
    # Roll day: no prior-day levels
    lv_roll = levels.day_levels(day, g, sess, prev, True, cfg)
    assert not any("pd_" in t for t in lv_roll["tags"])


def test_regime_measures():
    # Alternating returns -> strong mean reversion (VR well below 1)
    up_down = 100 * np.exp(np.cumsum(np.tile([0.001, -0.001], 40)))
    assert regime.variance_ratio(up_down) < 0.3
    trend = 100 * np.exp(np.cumsum(np.full(80, 0.001) + np.random.default_rng(0).normal(0, 1e-5, 80)))
    assert regime.variance_ratio(trend) > 0.0
    assert regime.efficiency_ratio(100, np.array([101, 102, 103.0])) == 1.0
    assert regime.efficiency_ratio(100, np.array([101, 100, 101.0])) == pytest.approx(1 / 3)


def test_stats_helpers():
    p = np.array([1, -1, -1, -1, 2, -0.5])
    assert stats.longest_losing_streak(p) == 3
    assert stats.max_drawdown(p) == pytest.approx(3.0)
    df = pd.DataFrame({"date": np.repeat(np.arange(50), 4), "x": 1.0})
    r = stats.day_bootstrap_mean(df, "x", 200, 1)
    assert r["mean"] == r["lo"] == r["hi"] == 1.0
    lo, hi = stats.wilson(50, 100, 0.90)
    assert lo < 0.5 < hi
    a = pd.DataFrame({"date": range(30), "pnl_r": 1.0})
    b = pd.DataFrame({"date": range(30), "pnl_r": 0.25})
    assert stats.day_bootstrap_diff(a, b, "pnl_r", 100, 1)["lo"] == pytest.approx(0.75)


def test_intervals():
    t = lambda m: pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(minutes=m)
    assert merge_intervals([(t(0), t(10)), (t(5), t(20)), (t(30), t(40))]) == [(t(0), t(20)), (t(30), t(40))]
    assert subtract_intervals([(t(0), t(40))], [(t(5), t(10)), (t(20), t(50))]) == [(t(0), t(5)), (t(10), t(20))]
    assert subtract_intervals([(t(0), t(10))], [(t(0), t(10))]) == []


def test_parse_osi_and_eod_normalize():
    o = parse_osi(pd.Series(["SPXW  230601C04200000", "SPX   230616P03950500"]))
    assert list(o["root"]) == ["SPXW", "SPX"] and list(o["right"]) == ["C", "P"]
    assert list(o["strike"]) == [4200.0, 3950.5]
    assert o["expiration"].iloc[0] == dt.date(2023, 6, 1)
    raw = pd.DataFrame({"symbol": ["SPXW"] * 2, "expiration": ["2023-06-02", "2023-06-02"], "strike": [4200.0, 4200.0],
                        "right": ["CALL", "PUT"], "bid": [10.0, 9.0], "ask": [10.5, 9.4], "close": [10.2, 9.1],
                        "volume": [5, 6]})
    n = normalize_eod(raw, "SPXW", dt.date(2023, 6, 1))
    assert list(n["right"]) == ["C", "P"] and n["expiration"].iloc[0] == dt.date(2023, 6, 2)
    v2 = raw.assign(strike=[4200000, 4200000], right=["C", "P"], expiration=[20230602, 20230602])
    assert normalize_eod(v2, "SPXW", dt.date(2023, 6, 1))["strike"].iloc[0] == 4200.0


def test_load_env_file(tmp_path, monkeypatch):
    from src.config import load_env_file
    f = tmp_path / ".env"
    f.write_text("# comment\nexport FOO_TEST_KEY='abc'\nBAR_TEST_KEY=keep\n")
    monkeypatch.delenv("FOO_TEST_KEY", raising=False)
    monkeypatch.setenv("BAR_TEST_KEY", "shell")
    load_env_file(f)
    import os
    assert os.environ["FOO_TEST_KEY"] == "abc"
    assert os.environ["BAR_TEST_KEY"] == "shell"     # shell value wins


def test_pilot_overlay(monkeypatch):
    from src.config import load_config, param
    from src import store
    monkeypatch.setenv("GAMMA_EDGE_CONFIG", "config.pilot.yaml")
    p = load_config()
    monkeypatch.setenv("GAMMA_EDGE_CONFIG", "config.yaml")
    m = load_config()
    assert p["name"] == "pilot" and m["name"] == "main"
    assert param(p, "gex_pct_min_periods") == 20 and param(m, "gex_pct_min_periods") == 252
    # Everything not overridden is inherited unchanged
    assert p["params"]["abs_threshold"] == m["params"]["abs_threshold"]
    assert p["sample"]["holdout_start"] == m["sample"]["holdout_start"]
    assert store.derived_path(p, "x").parent.name == "derived_pilot"
    assert store.derived_path(m, "x").parent.name == "derived"
    assert store.eod_path(p, "2025-03-03", "SPXW") == store.eod_path(m, "2025-03-03", "SPXW")


def test_with_retries_only_on_transient(monkeypatch):
    from databento.common.error import BentoClientError, BentoServerError
    from src import spend
    monkeypatch.setattr(spend.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise BentoServerError(http_status=504, http_body=None, message="The remote gateway timed out.")
        return "ok"
    assert spend.with_retries(flaky, "x") == "ok" and calls["n"] == 3

    def bad():
        raise BentoClientError(http_status=422, http_body=None, message="bad request")
    with pytest.raises(BentoClientError):
        spend.with_retries(bad, "x")

    def always():
        raise BentoServerError(http_status=504, http_body=None, message="timeout")
    with pytest.raises(BentoServerError):
        spend.with_retries(always, "x")
