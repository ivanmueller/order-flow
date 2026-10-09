"""Study 12: the Study 1 level fade in SPY shares (1-second quotes, one-cent ticks, $0 commission).
Every expected number is worked by hand in the comments."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study12

DAY = dt.date(2024, 3, 5)


def ts(hms):
    return pd.Timestamp(f"{DAY} {hms}", tz="America/New_York").tz_convert("UTC")


def q(rows):
    """(time, bid, ask)"""
    return pd.DataFrame([{"ts": ts(t), "bid": b, "ask": a} for t, b, a in rows])


def test_level_mapping_uses_the_0930_ratio():
    spy = q([("09:29:59", 497.00, 497.02), ("09:30:00", 497.99, 498.01), ("09:30:01", 499.0, 499.02)])
    r = study12.spy_ratio(spy, es_open_0930=5000.0, day=DAY, rth_open="09:30")
    assert r == pytest.approx(498.00 / 5000.0)                       # first quote at or after 09:30:00
    assert study12.to_spy(4990.0, r, 0.01) == pytest.approx(497.00)   # 4990 x 0.0996 = 497.004 -> 497.00


def test_level_mapping_removes_the_es_basis():
    # ES 5040 with basis 40 -> SPX 5000; SPY 498.00 -> r = 498 / 5000 = 0.0996.
    # An ES level 5030 is SPX 4990 -> SPY 497.004 -> 497.00 (ES x SPY/ES would give 497.01: the basis error)
    spy = q([("09:30:00", 497.99, 498.01)])
    r = study12.spy_ratio(spy, es_open_0930=5040.0, day=DAY, rth_open="09:30", basis=40.0)
    assert r == pytest.approx(0.0996)
    assert study12.to_spy(5030.0, r, 0.01, basis=40.0) == pytest.approx(497.00)
    assert study12.to_spy(5030.0, 498.0 / 5040.0, 0.01) == pytest.approx(497.01)


def _fees(price, R, cfg):
    return (cfg["params"]["s12_sec_fee_rate"]["value"] * price + cfg["params"]["s12_taf_per_share_usd"]["value"]) / R


def test_naive_long_target(cfg):
    spy = q([("10:00:00", 497.05, 497.06), ("10:00:01", 497.00, 497.01),
             ("10:00:02", 496.98, 496.99),          # ask 496.99 <= 497.00 - 1 cent: our 497.00 bid is filled
             ("10:00:03", 497.20, 497.21),
             ("10:00:04", 497.39, 497.40)])         # bid >= target 497.38 + 1 cent: sold at 497.38
    t = study12.naive_trade(spy, ts("10:00:00"), L=497.00, d=1, em=4.98, day=DAY, cfg=cfg)
    # dist = round(0.05 x 4.98 = 0.249) = 0.25; target distance round(1.5 x 0.25 = 0.375) = 0.38
    assert (t["E"], t["S"], t["T"], t["R_k"]) == pytest.approx((497.00, 496.75, 497.38, 0.25))
    assert t["exit_reason"] == "target" and t["X"] == pytest.approx(497.38)
    # 0.38 / 0.25 = 1.52 R, minus SEC + TAF on the sale
    assert t["pnl_r"] == pytest.approx(1.52 - _fees(497.38, 0.25, cfg))
    assert t["usd_per_100"] == pytest.approx(38.0 - 100 * _fees(497.38, 1.0, cfg))


def test_naive_long_stop_and_time(cfg):
    base = [("10:00:00", 497.05, 497.06), ("10:00:02", 496.98, 496.99)]
    t = study12.naive_trade(q(base + [("10:00:03", 496.75, 496.76)]), ts("10:00:00"), 497.00, 1, 4.98, DAY, cfg)
    # bid 496.75 reaches the stop: exit one cent beyond, 496.74 -> -0.26 / 0.25 = -1.04 R
    assert t["exit_reason"] == "stop" and t["X"] == pytest.approx(496.74)
    assert t["pnl_r"] == pytest.approx(-1.04 - _fees(496.74, 0.25, cfg))
    t = study12.naive_trade(q(base + [("10:10:00", 497.10, 497.11), ("10:30:02", 497.10, 497.11)]),
                            ts("10:00:00"), 497.00, 1, 4.98, DAY, cfg)
    # 30 minutes after the 10:00:02 fill: the bid minus one cent, 497.09 -> +0.36 R
    assert t["exit_reason"] == "time" and t["X"] == pytest.approx(497.09)
    assert t["pnl_r"] == pytest.approx(0.36 - _fees(497.09, 0.25, cfg))


def test_naive_short_and_no_fill(cfg):
    spy = q([("10:00:00", 496.95, 496.96), ("10:00:02", 497.01, 497.02),     # bid 497.01: sells at 497.00 filled
             ("10:00:05", 496.60, 496.61)])                                  # ask <= 496.62 - 1 cent: covered
    t = study12.naive_trade(spy, ts("10:00:00"), 497.00, -1, 4.98, DAY, cfg)
    assert (t["S"], t["T"], t["X"]) == pytest.approx((497.25, 496.62, 496.62))
    assert t["pnl_r"] == pytest.approx(1.52 - _fees(497.00, 0.25, cfg))      # the sale is the entry
    late = q([("10:00:00", 497.05, 497.06), ("10:10:01", 496.98, 496.99)])   # through only after the window
    assert study12.naive_trade(late, ts("10:00:00"), 497.00, 1, 4.98, DAY, cfg)["skip"] == "no_fill"


def test_confirmed_long(cfg):
    spy = q([("10:05:00", 497.00, 497.01), ("10:05:01", 497.09, 497.10), ("10:05:02", 497.66, 497.67)])
    feat = {"confirmed": True, "t_dec": ts("10:05:00"), "p_ext": 4988.0}
    t = study12.confirmed_trade(spy, feat, d=1, em=4.98, ratio=0.0996, day=DAY, cfg=cfg)
    tb = study12.confirmed_trade(spy, {**feat, "p_ext": 5028.0}, d=1, em=4.98, ratio=0.0996, day=DAY, cfg=cfg,
                                 basis=40.0)
    assert tb["S"] == pytest.approx(496.75)                          # p_ext 5028 - basis 40 = 4988 SPX
    # E: first quote after t_dec, ask 497.10 + 1 cent = 497.11; p_ext 4988 x 0.0996 = 496.80;
    # stop buffer 2 ES ticks = 0.5 pt x 0.0996 = 0.05 -> S 496.75; R 0.36; T = E + round(0.54) = 497.65
    assert (t["E"], t["S"], t["R_k"], t["T"]) == pytest.approx((497.11, 496.75, 0.36, 497.65))
    assert t["exit_reason"] == "target"
    assert t["pnl_r"] == pytest.approx(1.5 - _fees(497.65, 0.36, cfg))
    assert study12.confirmed_trade(spy, {"confirmed": False}, 1, 4.98, 0.0996, DAY, cfg) is None


def test_verdict(cfg):
    rng = np.random.default_rng(0)
    days = pd.date_range("2024-01-01", periods=100).date
    good = pd.DataFrame({"date": np.repeat(days, 3), "pnl_r": rng.normal(0.3, 0.5, 300)})
    assert study12.verdict(good, cfg)["verdict"] == "PASS"
    assert study12.verdict(good.assign(pnl_r=good["pnl_r"] - 0.25), cfg)["verdict"] == "KILL"     # mean < 0.10
    assert study12.verdict(good.iloc[:150], cfg)["verdict"] == "KILL"                              # < 200 trades


class FakeBudget:
    def __init__(self, cost):
        self.cost, self.pulled = cost, []

    def price(self, cl, args):
        return self.cost

    def pull(self, cl, job, what, args):
        self.pulled.append(args)

        class Dd:
            def to_df(self_inner):
                return pd.DataFrame({"ts_recv": [pd.Timestamp(args["start"])], "symbol": ["SPY"],
                                     "bid_px_00": [500.0], "ask_px_00": [500.01]}).set_index("ts_recv")
        return Dd(), self.cost


def test_pull_prices_then_writes(cfg, tmp_path):
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    b = FakeBudget(0.01)
    days = [DAY, dt.date(2024, 3, 6)]
    r = study12.pull(c, None, b, days, price_only=True)
    assert r["pieces"] == 2 and r["usd"] == pytest.approx(0.02) and b.pulled == []
    assert study12.pull(c, None, b, days, price_only=False)["written"] == 2
    assert study12.pull(c, None, b, days, price_only=False)["skipped"] == 2
    a = b.pulled[0]
    assert a["symbols"] == ["SPY"] and a["schema"] == "bbo-1s" and a["dataset"] == "XNAS.ITCH"
    assert study12.load_spy(c, DAY)["bid"].iloc[0] == pytest.approx(500.0)


def test_run_end_to_end(cfg, tmp_path):
    """touches + features tables and a SPY file -> one naive and one confirmed trade, report and verdicts."""
    from src import store
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    c["params"] = {**cfg["params"], "bootstrap_draws": {"value": 200}}
    tc = pd.DataFrame([{"touch_id": "a_0001", "date": DAY, "group": "gamma_only", "is_gamma": True, "d": 1,
                        "level_es": 4990.0, "em": 50.0, "t0": ts("10:00:00"), "tod": "mid", "es_open": 5000.0}])
    store.save_derived(tc, "touches", c)
    store.save_derived(pd.DataFrame({"date": [DAY], "basis": [0.0]}), "gex_daily", c)
    store.save_derived(pd.DataFrame([{"touch_id": "a_0001", "confirmed": True, "t_dec": ts("10:05:00"),
                                      "p_ext": 4988.0}]), "features", c)
    spy = pd.DataFrame({"ts_recv": [ts("09:30:00"), ts("10:00:00"), ts("10:00:02"), ts("10:05:01"), ts("10:05:02")],
                        "symbol": "SPY", "bid_px_00": [497.99, 497.05, 496.98, 497.09, 497.66],
                        "ask_px_00": [498.01, 497.06, 496.99, 497.10, 497.67]})
    store.write(spy, study12.spy_path(c, DAY))
    T, info = study12.run(c)
    assert info == {"days": 1, "days_missing_spy": 0, "days_no_ratio": 0}
    nv = T[T["mode"] == "naive"].iloc[0]
    assert nv["level_spy"] == pytest.approx(497.00) and nv["exit_reason"] == "target"   # 10:05:02 bid 497.66
    cf = T[T["mode"] == "confirmed"].iloc[0]
    assert cf["E"] == pytest.approx(497.11) and cf["exit_reason"] == "target"
    rep = study12.report(T, c, info)
    assert rep["naive"]["gamma_tagged"]["verdict"] == "KILL"                            # 1 trade < 200
    assert rep["confirmed_verdict_with_diff_rule"] == "KILL"
