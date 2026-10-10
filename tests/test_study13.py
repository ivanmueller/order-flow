"""Study 13: order-flow pattern discovery. Hand-worked features, fills, search, noise test, split and freeze."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study13

DAY = dt.date(2024, 3, 5)
T0 = pd.Timestamp(f"{DAY} 10:00:00", tz="America/New_York").tz_convert("UTC")
TICK = 0.25


def ns(sec):
    return (T0 + pd.Timedelta(seconds=sec)).value


def stream(rows):
    """(seconds after 10:00, price, size, side)"""
    a = np.array(rows, dtype=float)
    return np.array([ns(s) for s in a[:, 0]], dtype="int64"), a[:, 1], a[:, 2], a[:, 3]


# ---- features -----------------------------------------------------------------------------------
def test_features_by_hand():
    ts, px, sz, sg = stream([(88, 5000.00, 2, -1), (91, 5000.25, 3, 1), (96, 5000.50, 60, 1),
                             (99, 5000.25, 5, -1), (100, 5000.50, 1, 1)])     # the 100 s print is not before t
    X = study13.feature_matrix(ts, px, sz, sg, np.array([ns(100)]), [5, 15], TICK, big=50, burst_s=1,
                               open_ns=pd.Timestamp(f"{DAY} 09:30", tz="America/New_York").value)
    r = X.iloc[0]
    # [95, 100): prints at 96 (+60) and 99 (-5): imbalance 55/65, price 5000.50 -> 5000.25 = -1 tick
    assert r["imb_5"] == pytest.approx(55 / 65) and r["ret_5"] == pytest.approx(-1)
    assert r["big_5"] == pytest.approx(60 / 65) and r["div_5"] == pytest.approx(-55 / 65)
    # [85, 100): -2 + 3 + 60 - 5 = 56 of 70; 5000.00 -> 5000.25 = +1 tick
    assert r["imb_15"] == pytest.approx(0.8) and r["ret_15"] == pytest.approx(1)
    assert r["rate_ratio_5"] == pytest.approx((2 / 5) / (4 / 15))
    # absorption: volume per tick of range + 1: 65 / 2 vs 70 / 3
    assert r["absorb_5"] == pytest.approx((65 / 2) / (70 / 3))
    assert r["avgsize_5"] == pytest.approx((65 / 2) / (70 / 4))
    # VWAP over 15 s = 5000 + 32 / 70; last 5000.25
    assert r["vwap_dist"] == pytest.approx((0.25 - 32 / 70) / TICK)
    assert r["rv_ref"] == pytest.approx(3)                 # three 1-tick changes inside [85, 100)
    assert r["burst"] == pytest.approx(-5)                 # [99, 100): the -5 print
    assert r["tod_min"] == pytest.approx(30 + 100 / 60)


# ---- fills ----------------------------------------------------------------------------------------
COMM = 3.98 / 50


def _targets(rows, inst=None, horizons=(10, 200)):
    ts, px, sz, sg = stream(rows)
    inst = np.zeros(len(ts), int) if inst is None else np.asarray(inst)
    return study13.target_matrix(ts, px, sg, inst, np.array([ns(100)]), list(horizons), latency_s=1, tick=TICK,
                                 wait_s=30, comm=COMM, end_ns=ns(1000), look_ns=np.array([ns(0)]))


ROWS = [(0, 5000.25, 1, -1), (100.5, 5000.50, 1, 1), (101.5, 5000.25, 1, -1), (110, 5000.00, 1, -1),
        (120, 5000.75, 1, 1), (200, 5001.00, 1, -1)]


def test_fills_by_hand():
    Y = _targets(ROWS).iloc[0]
    # entry print 101.5 (5000.25, sell: bid 5000.25 / ask 5000.50); exit = first print >= 111.5: 120 (5000.75, buy)
    assert Y["taker_spec|long|10"] == pytest.approx((5000.75 - TICK) - (5000.25 + TICK) - COMM)
    assert Y["taker_spec|short|10"] == pytest.approx((5000.25 - TICK) - (5000.75 + TICK) - COMM)
    assert Y["taker_spread|long|10"] == pytest.approx(5000.50 - 5000.50 - COMM)      # bid at exit - ask at entry
    assert Y["taker_spread|short|10"] == pytest.approx(5000.25 - 5000.75 - COMM)
    # passive long: bid known at 101 = 5000.25 (the 100.5 buy print); through fill at 110 (5000.00), exit 120 bid
    assert Y["passive_through|long|10"] == pytest.approx(5000.50 - 5000.25 - COMM)
    assert Y["passive_touch|long|10"] == pytest.approx(5000.50 - 5000.25 - COMM)     # touched at 101.5
    # passive short: ask 5000.50; filled at 120 (5000.75 >= 5000.75); exit first print >= 130 = 200 (ask 5001.25)
    assert Y["passive_through|short|10"] == pytest.approx(5000.50 - 5001.25 - COMM)
    assert np.isnan(Y["taker_spec|long|200"])                                         # no print by 301.5


def test_roll_inside_the_trade_is_dropped():
    Y = _targets(ROWS, inst=[1, 1, 1, 1, 1, 2]).iloc[0]
    assert np.isfinite(Y["taker_spec|long|10"])            # exit at 120: same contract
    assert np.isnan(Y["passive_through|short|10"])         # its exit print (200) is the next contract


def test_passive_unfilled_is_no_trade():
    Y = _targets([(0, 5000.25, 1, -1), (100.5, 5000.50, 1, 1), (101.5, 5000.50, 1, 1), (140, 5000.25, 1, -1)],
                 horizons=(10,)).iloc[0]
    assert np.isnan(Y["passive_through|long|10"])          # nothing below 5000.25 within 30 s
    assert np.isfinite(Y["taker_spec|long|10"])


# ---- search, noise test, split, freeze ------------------------------------------------------------
def _obs(signal: float, n_days=40, per=300, seed=1):
    rng = np.random.default_rng(seed)
    days = [DAY + dt.timedelta(days=i) for i in range(n_days)]
    rows = []
    for d in days:
        f = rng.normal(size=(per, 3))
        noise = rng.normal(0, 1.0, size=per)
        y = noise - 0.3 + signal * (f[:, 0] > 0.84)        # f1 in its top fifth adds `signal`
        rows.append(pd.DataFrame({"date": d, "f1": f[:, 0], "f2": f[:, 1], "f3": f[:, 2],
                                  "taker_spec|long|60": y, "taker_spec|short|60": rng.normal(-0.3, 1.0, per)}))
    return pd.concat(rows, ignore_index=True)


def test_search_finds_a_planted_pattern_and_beats_noise(cfg):
    O = _obs(signal=1.0)
    feats = ["f1", "f2", "f3"]
    r = study13.search(O, feats, ["taker_spec|long|60", "taker_spec|short|60"], q=0.2, min_obs=1000, min_days=10,
                       reps=50, seed=3, top_k=3)
    best = r["best"]["per_trade"]["taker_spec"]
    assert best["test"] == "f1:hi" and best["column"] == "taker_spec|long|60"
    assert best["mean"] > 0.5 and r["null"]["per_trade"]["taker_spec"]["p"] <= 0.05
    assert r["null"]["t_stat"]["taker_spec"]["p"] <= 0.05


def test_noise_does_not_beat_noise(cfg):
    O = _obs(signal=0.0, seed=7)
    r = study13.search(O, ["f1", "f2", "f3"], ["taker_spec|long|60", "taker_spec|short|60"], q=0.2, min_obs=1000,
                       min_days=10, reps=50, seed=3, top_k=3)
    assert r["null"]["per_trade"]["taker_spec"]["p"] > 0.05


def test_split_is_by_date_and_ordered():
    days = [DAY + dt.timedelta(days=i) for i in range(10)]
    d, v, t = study13.split_days(days[::-1], [0.6, 0.2, 0.2])
    assert d == days[:6] and v == days[6:8] and t == days[8:]


def test_apply_pattern_uses_discovery_cuts():
    X = pd.DataFrame({"f1": [0.0, 1.0, 2.0, 3.0], "f2": [5.0, 5.0, -5.0, -5.0]})
    cuts = {"f1": (0.5, 2.5), "f2": (-1.0, 1.0)}
    assert list(study13.pattern_mask(X, "f1:hi", cuts)) == [False, False, False, True]
    assert list(study13.pattern_mask(X, "f1:lo&f2:hi", cuts)) == [True, False, False, False]


def test_clean_flags_drop_decisions_kept_only_by_a_future_touch():
    t0 = pd.Timestamp(f"{DAY} 10:10", tz="America/New_York").tz_convert("UTC")
    O = pd.DataFrame({"date": [DAY] * 4,
                      "t": [t0 - pd.Timedelta(minutes=5), t0, t0 + pd.Timedelta(minutes=10),
                            t0 + pd.Timedelta(minutes=20)]})
    touches = pd.DataFrame({"date": [DAY], "t0": [t0]})
    f = study13.clean_flags(O, touches, hold_s=1800, post_min=45, latency_s=1)
    # before the touch: selected on the future; at t0 and t0+10: the 30-min trade ends by t0+45; t0+20 runs past it
    assert list(f) == [False, True, True, False]


def test_per_day_objective_prefers_frequent_small_edges(cfg):
    """f1:hi (every decision of its block) earns +0.2 on 5-s holds; f2:hi earns +1.0 on 1800-s holds. Per trade the
    long hold wins; per day (one position at a time) the frequent small edge wins: ~60 slots x 0.2 vs ~1 x 1.0."""
    rng = np.random.default_rng(2)
    rows = []
    for i in range(30):
        d = DAY + dt.timedelta(days=i)
        n = 600
        t = pd.Timestamp(f"{d} 10:00", tz="America/New_York").tz_convert("UTC") + pd.to_timedelta(np.arange(n) * 10, unit="s")
        f1 = np.zeros(n); f1[::2] = 1.0                                  # every other decision, spread all day
        f2 = np.zeros(n); f2[100:220] = 1.0                              # one 20-minute block a day
        y5 = np.where(f1 > 0, 0.2, -0.5) + rng.normal(0, 0.01, n)
        y30 = np.where(f2 > 0, 1.0, -0.5) + rng.normal(0, 0.01, n)
        rows.append(pd.DataFrame({"date": d, "t": t, "f1": f1 + rng.normal(0, 1e-6, n), "f2": f2 + rng.normal(0, 1e-6, n),
                                  "taker_spec|long|5": y5, "taker_spec|long|1800": y30}))
    O = pd.concat(rows, ignore_index=True)
    r = study13.search(O, ["f1", "f2"], ["taker_spec|long|5", "taker_spec|long|1800"], q=0.2, min_obs=100,
                       min_days=10, reps=5, seed=1, top_k=3, latency_s=1)
    assert r["best"]["per_trade"]["taker_spec"]["column"] == "taker_spec|long|1800"
    assert r["best"]["per_day"]["taker_spec"]["column"] == "taker_spec|long|5"
    assert r["best"]["per_day"]["taker_spec"]["per_day"] > 10 * r["best"]["per_trade"]["taker_spec"]["per_day"]
