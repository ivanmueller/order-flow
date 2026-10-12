"""Study 14: pump events, entry rules, the conservative short walk, squeeze model, combination search, placebo
hours, periods, grid stability and the risk report. Every number below is worked by hand."""
import numpy as np
import pandas as pd
import pytest

from src import study14 as s14

HR = 3_600 * 10**9
M5 = 300 * 10**9
T0 = pd.Timestamp("2022-03-01", tz="UTC").value


def hourly(o, c, h=None, l=None, qv=None, t0=T0):
    o, c = np.asarray(o, float), np.asarray(c, float)
    h = np.maximum(o, c) if h is None else np.asarray(h, float)
    l = np.minimum(o, c) if l is None else np.asarray(l, float)
    qv = np.ones(len(o)) if qv is None else np.asarray(qv, float)
    return pd.DataFrame({"ts": t0 + np.arange(len(o), dtype="int64") * HR, "open": o, "high": h, "low": l,
                         "close": c, "qv": qv, "real": True})


# ---- events ---------------------------------------------------------------------------------------
def _pumped():
    c = np.full(200, 100.0)
    qv = np.ones(200)
    c[60:63] = [110, 130, 160]; c[63:] = 160; qv[60:63] = 10            # +60% over 3 h on 10x volume
    c[110:113] = [200, 300, 400]; c[113:] = 400; qv[110:113] = 10       # a second pump ~2 days later
    o = np.concatenate([[100.0], c[:-1]])
    return hourly(o, c, qv=qv)


def test_event_needs_return_and_volume_and_respects_cooldown():
    h = _pumped()
    # W=3: at k=62, 160/100 - 1 = 0.6; volume 30 vs median 3-h volume 3 over the prior 48 h -> 10x
    k = s14.detect_events(h, W=3, P=0.5, V=5, lookback_h=48, cooldown_h=7 * 24)
    assert list(k) == [62]
    assert list(s14.detect_events(h, W=3, P=0.5, V=5, lookback_h=48, cooldown_h=24)) == [62, 111]
    assert list(s14.detect_events(h, W=3, P=0.5, V=11, lookback_h=48, cooldown_h=24)) == []
    assert list(s14.detect_events(h, W=3, P=0.7, V=5, lookback_h=48, cooldown_h=24)) == [111]  # 300/160 at 111


# ---- entries --------------------------------------------------------------------------------------
def _entry_bars():
    o = [100] * 5 + [100, 150, 190, 195, 185, 179, 182, 170] + [150] * 11
    c = [100] * 5 + [150, 190, 195, 185, 179, 182, 170, 150] + [150] * 11
    h = [100] * 5 + [150, 195, 200, 196, 186, 183, 182, 170] + [150] * 11
    day1_o = np.linspace(150, 141, 24); day1_c = day1_o - 0.5           # day 1 opens 150, closes 140.5: red
    o = np.concatenate([o, day1_o, np.full(30, 140.5)]); c = np.concatenate([c, day1_c, np.full(30, 140.5)])
    h = np.concatenate([h, np.maximum(day1_o, day1_c), np.full(30, 140.5)])
    return hourly(o, c, h=h)


@pytest.mark.parametrize("rule,bar", [("next_hour", 6), ("red_hour", 9), ("drop_10", 10), ("drop_20", 13),
                                      ("red_day", 48)])
def test_entry_rules_by_hand(rule, bar):
    h = _entry_bars()
    e = s14.entry_times(h, np.array([5]), rule, red_hour_h=72, red_day_d=7, drop_h=72)
    assert e[0] == h["ts"].iloc[bar]


def test_entry_not_found_inside_its_window():
    h = hourly([100] * 30, [101] * 30)                                    # never red
    assert s14.entry_times(h, np.array([2]), "red_hour", red_hour_h=10, red_day_d=7, drop_h=72)[0] == -1


# ---- the short walk ------------------------------------------------------------------------------
def bars5(rows, n_total=12, t0=T0):
    rows = rows + [rows[-1][3:] * 4] * (n_total - len(rows))
    a = np.array(rows, float)
    return {"ts": t0 + np.arange(len(a), dtype="int64") * M5, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
            "close": a[:, 3], "real": np.ones(len(a), bool)}


ROWS = [[100, 101, 99, 100], [100, 116, 99, 110], [110, 112, 79, 80], [135, 140, 130, 132]]
SLIP, FEE = 0.001, 0.0005
PE = 100 * (1 - SLIP)


def r_of(x, funding=0.0):
    return 1 - x / PE - FEE * (1 + x / PE) + funding / PE


def _walk(b, last_ts=None, funding=None, holds=(2, 10), stops=(0.15, 0.3), targets=(None, 0.2), label_bars=4):
    f = funding or (np.array([], "int64"), np.array([]))
    return s14.walk(b, np.array([b["ts"][0]]), last_ts=last_ts if last_ts is not None else b["ts"][-1] + M5,
                    hold_bars=list(holds), stops=list(stops), targets=list(targets), slip=SLIP, fee=FEE,
                    funding_ts=f[0], funding_rate=f[1], label_rise=0.3, label_bars=label_bars, max_filled=0.25)


def test_walk_outcomes_by_hand():
    b = bars5(ROWS)
    w = _walk(b)
    R = w["r"][0]                                     # [hold, stop, target]
    assert R[0, 0, 0] == pytest.approx(r_of(PE * 1.15 * 1.001))      # stop in bar 1 (intrabar), 0.1% worse
    assert R[0, 1, 0] == pytest.approx(r_of(110 * 1.001))            # 2-bar hold: exit at bar 2's open
    assert R[0, 1, 1] == pytest.approx(r_of(110 * 1.001))            # target in bar 2 is too late for a 2-bar hold
    assert R[1, 1, 1] == pytest.approx(r_of(PE * 0.8))               # target fills at its price, no slippage
    assert R[1, 1, 0] == pytest.approx(r_of(135 * 1.001))            # bar 3 opens above the stop: exit at its open
    assert R[1, 0, 1] == pytest.approx(r_of(PE * 1.15 * 1.001))
    assert w["exit_ts"][0][1, 1, 0] == b["ts"][3] and w["exit_ts"][0][0, 1, 0] == b["ts"][2]
    assert bool(w["label"][0]) and w["valid"][0]
    assert not bool(_walk(b, label_bars=3)["label"][0])              # the 140 high is in bar 3


def test_stop_before_target_in_the_same_bar():
    b = bars5([[100, 101, 99, 100], [100, 120, 70, 90]])
    assert _walk(b)["r"][0][1, 0, 1] == pytest.approx(r_of(PE * 1.15 * 1.001))


def test_funding_counts_only_strictly_inside_the_trade():
    b = bars5(ROWS)
    w = _walk(b, funding=(np.array([b["ts"][2]]), np.array([0.001])))   # paid on bar 1's close (110)
    assert w["r"][0][0, 1, 0] == pytest.approx(r_of(110 * 1.001))       # exits at bar 2's open = the funding time
    assert w["r"][0][1, 1, 0] == pytest.approx(r_of(135 * 1.001, funding=0.11))
    assert w["r"][0][1, 0, 0] == pytest.approx(r_of(PE * 1.15 * 1.001))  # stopped in bar 1, before the funding
    assert w["fund_at_entry"][0] != w["fund_at_entry"][0]               # nothing settled before entry: NaN


def test_delisted_coin_exits_at_its_last_close_and_loss_is_capped():
    b = bars5([[100, 100, 100, 100], [100, 100, 100, 100], [100, 100, 100, 105]])
    w = _walk(b, last_ts=b["ts"][2] + M5, stops=(1.0,), targets=(None,))
    assert w["r"][0][1, 0, 0] == pytest.approx(r_of(105 * 1.001))       # 10-bar hold, data ends after bar 2
    b2 = bars5([[100, 100, 100, 100], [400, 400, 400, 400]])
    assert _walk(b2, stops=(1.0,), targets=(None,))["r"][0][1, 0, 0] == -1.0


def test_walk_with_mostly_filled_bars_is_invalid():
    b = bars5(ROWS)
    b["real"][2:] = False
    assert not _walk(b)["valid"][0]


# ---- squeeze model --------------------------------------------------------------------------------
def test_logit_recovers_the_sign_and_scores_point_in_time():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 2))
    y = (X[:, 0] - 0.5 * X[:, 1] + rng.logistic(size=2000) > 0).astype(float)
    m = s14.fit_logit(pd.DataFrame(X, columns=["a", "b"]), y)
    assert m["coef"][0] > 0.5 and m["coef"][1] < -0.2
    s = s14.score(m, pd.DataFrame({"a": [2.0, -2.0, np.nan], "b": [0.0, 0.0, 0.0]}))
    assert s[0] > 0.8 and s[1] < 0.2 and np.isnan(s[2])                 # missing feature: no score (never skipped)


def test_metric_features_use_only_rows_before_entry():
    ts = T0 + np.arange(0, 30 * 12) * M5                                  # 30 hours of 5-min metrics
    m = pd.DataFrame({"ts": ts, "oi_value": np.arange(len(ts), dtype=float) + 100, "top_ls": 2.0,
                      "taker_ratio": 1.0})
    e = np.array([T0 + 26 * HR])
    f = s14.metric_features(m, e, qv24=np.array([1000.0]))
    last = 26 * 12 - 1 + 100.0                                            # the row stamped just before entry
    assert f["oi_chg_24h"][0] == pytest.approx(last / (2 * 12 - 1 + 100.0) - 1)
    assert f["oi_to_vol"][0] == pytest.approx(last / 1000.0) and f["top_ls"][0] == 2.0


# ---- combination statistics ------------------------------------------------------------------------
def _rows():
    return pd.DataFrame({"g": [0, 0, 0, 1], "week": [1, 1, 2, 1], "score": [0.1, 0.9, np.nan, 0.2],
                         "fund": [0.001, -0.001, 0.0, np.nan], "c0": [0.1, -0.2, 0.3, 0.5], "c1": [0.0, 0.0, 0.6, 0.1]})


def test_accumulator_stats_by_hand():
    acc = s14.Accumulator(n_groups=2, n_cols=2, qcuts=[None, 0.5], n_weeks=3, reps=1)
    acc.add(0, _rows(), ["c0", "c1"])
    T = acc.table(0, n_months=2)
    # group 0, Q none, F any: c0 = 0.1, -0.2, 0.3
    r = T[(T.g == 0) & (T.q == 0) & (T.f == 0) & (T.c == 0)].iloc[0]
    assert r["n"] == 3 and r["weeks"] == 2 and r["mean"] == pytest.approx(0.2 / 3)
    assert r["per_month"] == pytest.approx(0.1) and r["t_stat"] == pytest.approx((0.2 / 3) / np.std([0.1, -0.2, 0.3], ddof=1) * np.sqrt(3))
    # Q cut 0.5 drops the 0.9 score (the NaN score is kept); F nonneg drops the negative funding
    r = T[(T.g == 0) & (T.q == 1) & (T.f == 1) & (T.c == 0)].iloc[0]
    assert r["n"] == 2 and r["mean"] == pytest.approx(0.2)
    r = T[(T.g == 1) & (T.q == 0) & (T.f == 1) & (T.c == 1)].iloc[0]
    assert r["n"] == 0                                                    # missing funding fails "nonneg"


def test_best_respects_eligibility_and_null_p():
    T = pd.DataFrame({"n": [150, 50, 200], "weeks": [25, 30, 10], "mean": [0.01, 0.5, 0.4],
                      "per_month": [1.0, 9.0, 9.0], "t_stat": [2.0, 9.0, 9.0]})
    b = s14.best_of(T, min_trades=100, min_weeks=20)
    assert b["per_trade"] == 0 and b["per_month"] == 0
    assert s14.null_p(1.0, np.array([0.5, 1.2, 0.9, 1.0])) == pytest.approx(3 / 5)


# ---- placebo hours, periods, stability ----------------------------------------------------------------
def test_placebo_hours_same_month_and_far_from_events():
    rng = np.random.default_rng(1)
    hours = T0 + np.arange(0, 31 * 24) * HR                               # March 2022
    ev = np.array([T0 + 100 * HR, T0 + 400 * HR])
    p = s14.placebo_times(rng, ev, ev, hours, gap_h=72)
    assert len(p) == 2
    for x in p:
        assert pd.Timestamp(x, tz="UTC").month == 3 and np.min(np.abs(x - ev)) >= 72 * HR


def test_periods_drop_events_whose_trades_could_cross_a_boundary(cfg):
    reach = s14.reach_ns(cfg)                                             # 7 + 1 + 14 days
    end = pd.Timestamp("2024-01-01", tz="UTC").value
    t = np.array([end - reach - HR, end - reach + HR, end, pd.Timestamp("2025-09-15", tz="UTC").value])
    assert list(s14.period_of(t, cfg)) == ["train", None, "val", None]


def test_neighbours_one_step_in_ordered_settings(cfg):
    g = s14.Grid(cfg)
    c = {"W": 24, "P": 0.5, "V": 5, "E": "red_hour", "H": 7, "S": 0.3, "T": None, "Q": None, "F": "any"}
    nb = g.neighbours(c)
    assert len(nb) == 2 + 2 + 2 + 2 + 2 + 1 + 1                          # T and Q sit at an end of their order
    assert {"W": 6, **{k: v for k, v in c.items() if k != "W"}} in nb
    assert all(n["E"] == "red_hour" and n["F"] == "any" for n in nb)


# ---- risk report ------------------------------------------------------------------------------------
def test_risk_sizes_by_stop_and_limits_open_trades():
    tr = pd.DataFrame({"entry_ts": [0, 5, 20], "exit_ts": [10, 15, 30], "r": [-0.15, 0.5, 0.30]})
    out = s14.risk_path(tr, stop=0.15, share=0.01, max_open=1, start=100.0)
    # trade 1: notional 0.01*100/0.15 = 6.667, loses 1.0; trade 2 skipped (one open); trade 3: 6.6 notional, +1.98
    assert out["final"] == pytest.approx(100.98) and out["taken"] == 2 and out["skipped"] == 1
    assert out["max_dd"] == pytest.approx(0.01)


def test_trade_signature_identifies_identical_trade_lists():
    a = pd.DataFrame({"sym": ["A", "B"], "entry_ts": [1, 2], "exit_ts": [3, 4], "r": [0.1, -0.2]})
    assert s14.trade_signature(a) == s14.trade_signature(a.iloc[::-1])
    assert s14.trade_signature(a) != s14.trade_signature(a.assign(r=[0.1, -0.3]))


def test_no_event_across_a_delist_relist_gap():
    h = _pumped()
    h.loc[59, "real"] = False                                             # the window's first bar is not real
    assert 62 not in s14.detect_events(h, W=3, P=0.5, V=5, lookback_h=48, cooldown_h=24)
