"""Study 15: long early in crypto surges. Signals, entries, the conservative long walk (hard stop, trailing stop,
target), filter cells, placebo draws, the BTC filter, periods and the accumulator -- worked by hand."""
import numpy as np
import pandas as pd
import pytest

from src import study15 as s15

HR = 3_600 * 10**9
T0 = pd.Timestamp("2022-03-01", tz="UTC").value


def hourly(o, c, h=None, l=None, qv=None, t0=T0):
    o, c = np.asarray(o, float), np.asarray(c, float)
    return pd.DataFrame({"ts": t0 + np.arange(len(o), dtype="int64") * HR, "open": o,
                         "high": np.maximum(o, c) if h is None else np.asarray(h, float),
                         "low": np.minimum(o, c) if l is None else np.asarray(l, float),
                         "close": c, "qv": np.ones(len(o)) if qv is None else np.asarray(qv, float), "real": True})


# ---- signals ----------------------------------------------------------------------------------------
def test_volatility_and_volume_surges_and_prior_filter():
    n = 120
    c = np.full(n, 100.0); o = c.copy(); hi = c * 1.01; lo = c * 0.99; qv = np.ones(n)
    c[100] = 120.0; hi[100] = 125.0; lo[100] = 99.0; qv[100] = 10.0         # 1-h surge at bar 100
    h = hourly(o, c, hi, lo, qv)
    sig = s15.surge_signals(h, W=1, lookback_h=48, prior_h=24)
    assert sig["ret"][100] == pytest.approx(0.2)
    assert sig["volume"][100] == pytest.approx(10.0)                            # 10 vs a median of 1
    assert sig["volatility"][100] == pytest.approx((26 / 100) / 0.02)           # range 26% vs 2%
    assert sig["prior"][100] == pytest.approx(0.0)
    ok = s15.detect(sig, h, P=0.1, kind="volume", level=5, prior_max=None, cooldown_h=168)
    assert list(ok) == [100]
    assert list(s15.detect(sig, h, P=0.1, kind="volatility", level=20, prior_max=None, cooldown_h=168)) == []
    c2 = c.copy(); c2[80:100] = 200.0; o2 = np.concatenate([[100.0], c2[:-1]])  # coin doubled before the window
    sig2 = s15.surge_signals(hourly(o2, c2, np.maximum(o2, c2), np.minimum(o2, c2), qv), W=1, lookback_h=48, prior_h=24)
    assert sig2["prior"][100] == pytest.approx(1.0)


# ---- entries ---------------------------------------------------------------------------------------------
def test_entry_rules_by_hand():
    o = [100, 100, 110, 115, 112, 113, 118, 120]
    c = [100, 110, 115, 112, 113, 118, 120, 121]
    h = [100, 111, 116, 116, 114, 119, 121, 122]
    H = hourly(o, c, h=h)
    k = np.array([1])                                                          # signal bar 1 (high 111)
    assert s15.entry_index(H, "next_hour", 24)[k][0] == 2
    assert s15.entry_index(H, "pullback", 24)[k][0] == 4                       # bar 3 is red -> buy bar 4
    assert s15.entry_index(H, "breakout", 24)[k][0] == 3                       # bar 2 closes 115 > 111 -> buy bar 3
    assert s15.entry_index(H, "pullback", 1)[k][0] == -1                       # not red within 1 h


# ---- the long walk --------------------------------------------------------------------------------------
SLIP, FEE = 0.001, 0.0005
PE = 100 * (1 + SLIP)


def r_of(x, funding=0.0):
    return x / PE - 1 - FEE * (1 + x / PE) - funding / PE


def bars(rows, n_total=12, t0=T0):
    rows = rows + [[rows[-1][3]] * 4] * (n_total - len(rows))
    a = np.array(rows, float)
    return {"ts": t0 + np.arange(len(a), dtype="int64") * HR, "open": a[:, 0], "high": a[:, 1],
            "low": a[:, 2], "close": a[:, 3], "real": np.ones(len(a), bool)}


ROWS = [[100, 105, 99, 104], [104, 130, 103, 125], [125, 126, 100, 110], [85, 90, 80, 88]]


def _walk(b, last_ts=None, funding=None, holds=(2, 10), stops=(0.1, 0.2), trails=(None, 0.15), targets=(None, 0.2)):
    f = funding or (np.array([], "int64"), np.array([]))
    return s15.walk_long(b, np.array([b["ts"][0]]), last_ts if last_ts is not None else b["ts"][-1] + HR,
                         list(holds), list(stops), list(trails), list(targets), SLIP, FEE, f[0], f[1], 0.25)


def test_long_walk_by_hand():
    b = bars(ROWS)
    w = _walk(b)
    R, X = w["r"][0], w["exit_ts"][0]                                         # [hold, stop, trail, target]
    assert R[0, 0, 0, 0] == pytest.approx(r_of(125 * 0.999))                  # 2-bar hold: bar 2's open
    assert R[1, 0, 0, 0] == pytest.approx(r_of(85 * 0.999))                   # bar 3 opens below the 10% stop
    assert R[1, 1, 0, 0] == pytest.approx(r_of(PE * 0.8 * 0.999))             # 20% stop hit inside bar 3
    assert R[1, 0, 1, 0] == pytest.approx(r_of(130 * 0.85 * 0.999))           # trail from the 130 high, bar 2
    assert R[1, 0, 0, 1] == pytest.approx(r_of(PE * 1.2))                     # target in bar 1, at its price
    assert R[0, 0, 1, 0] == pytest.approx(r_of(125 * 0.999))                  # bar 2 is the time exit for H=2
    assert X[1, 0, 1, 0] == b["ts"][2] and X[1, 0, 0, 1] == b["ts"][1]
    assert w["valid"][0]


def test_trail_uses_only_earlier_highs_and_stop_beats_target():
    # bar 1: new high 140 and low 115; with the 140 high the 15% trail (119) would be hit, but the low came first
    b = bars([[100, 101, 99, 100], [100, 140, 115, 130], [130, 131, 129, 130]])
    R = _walk(b)["r"][0]
    assert R[1, 0, 1, 0] == pytest.approx(r_of(130 * 0.999))                  # never stopped: time exit at 130
    b2 = bars([[100, 101, 99, 100], [100, 125, 85, 90]])                       # stop and target in one bar
    assert _walk(b2)["r"][0][1, 0, 0, 1] == pytest.approx(r_of(PE * 0.9 * 0.999))


def test_funding_paid_by_longs_and_delisting():
    b = bars(ROWS)
    w = _walk(b, funding=(np.array([b["ts"][1]]), np.array([0.001])))        # settles on bar 0's close (104)
    assert w["r"][0][0, 0, 0, 0] == pytest.approx(r_of(125 * 0.999, funding=0.104))
    b2 = bars([[100, 101, 99, 100], [100, 101, 99, 101], [101, 102, 100, 102]])
    w2 = _walk(b2, last_ts=b2["ts"][2] + HR)
    assert w2["r"][0][1, 0, 0, 0] == pytest.approx(r_of(102 * 0.999))         # data ends after bar 2
    assert _walk(bars([[100, 100, 100, 100], [0.5, 0.5, 0.5, 0.5]]), stops=(0.3,))["r"][0][1, 0, 0, 0] == pytest.approx(
        r_of(0.5 * 0.999))
    assert _walk(bars([[100, 100, 100, 100], [0.01, 0.01, 0.01, 0.01]]))["r"][0].min() >= -1.0


# ---- filters, placebo, BTC, periods -----------------------------------------------------------------------
def test_cells_from_funding_and_regime(cfg):
    g = s15.Grid15(cfg)
    cells = s15.row_cells(np.array([3, 3, 3]), np.array([0.00005, np.nan, -0.001]), np.array([True, True, False]), g)
    fb = [sorted(c % 6 for c in row if c >= 0) for row in cells]
    # cell = g * 6 + f * 2 + b; F levels [any, <= 0.0001, <= 0]; B [any, above_ma]
    assert fb[0] == [0, 1, 2, 3]
    assert fb[1] == [0, 1]                                                     # no funding: only F=any
    assert fb[2] == [0, 2, 4]                                                  # negative funding, BTC below its mean
    assert all(c // 6 == 3 for row in cells for c in row if c >= 0)


def test_placebo_draws_same_month_and_away_from_events():
    rng = np.random.default_rng(3)
    hours = np.arange(0, 31 * 24)                                             # March 2022, bar indices
    ts = T0 + hours.astype("int64") * HR
    ev = np.array([100, 400])
    cand = s15.placebo_candidates(ts, ev, hours, gap_h=24)
    pk = s15.draw_placebo(rng, ts, ev, cand)
    assert len(pk) == 2 and all(abs(pk[:, None] - ev[None, :]).min(1) >= 24)
    assert all(pd.to_datetime(ts[pk], utc=True).month == 3)


def test_btc_filter_uses_only_closed_bars():
    btc = hourly([10, 10, 10, 10, 20], [10, 10, 10, 10, 20])
    e = np.array([btc["ts"].iloc[4], btc["ts"].iloc[4] + HR])
    # at bar 4's open only bars 0-3 have closed (all 10): not above; after bar 4 closes (20 > mean 12.5): above
    assert list(s15.btc_above_ma(btc, e, ma_h=4)) == [False, True]


def test_periods_trim_trades_that_could_cross(cfg):
    r = s15.reach_ns(cfg)                                                      # 24 h + 1 h + 14 days
    assert r == (25 + 14 * 24) * HR
    end = pd.Timestamp("2024-01-01", tz="UTC").value
    assert list(s15.period_of(np.array([end - r - HR, end - r + HR, end]), cfg)) == ["train", None, "val"]


def test_accumulator_by_hand():
    acc = s15.Acc(n_cells=2, n_cols=2, reps=1)
    cells = np.array([[0, 1], [0, -1], [1, -1]])
    Y = np.array([[0.1, 0.2], [0.3, -0.2], [0.5, 0.0]])
    acc.add(0, cells, Y, np.array([1, 2, 1]))
    st = acc.stats(0, n_months=2)
    assert st["n"][0, 0] == 2 and st["n"][1, 0] == 2 and st["weeks"][0, 0] == 2 and st["weeks"][1, 0] == 1
    assert st["mean"][0, 0] == pytest.approx(0.2) and st["mean"][1, 1] == pytest.approx(0.1)
    assert st["per_month"][0, 1] == pytest.approx(0.0) and st["t_stat"][0, 0] == pytest.approx(
        0.2 / np.std([0.1, 0.3], ddof=1) * np.sqrt(2))


def test_neighbours_and_grid_size(cfg):
    g = s15.Grid15(cfg)
    assert len(g.sets) * len(g.E) * len(g.cols) * 6 == 373_248
    d = {"W": 3, "P": 0.1, "K": "volume", "V": 5, "X": None, "E": "pullback", "H": 7, "S": 0.2, "TR": 0.15,
         "T": None, "F": 0.0001, "B": "any"}
    nb = g.neighbours(d)
    assert len(nb) == 2 * 6 + 1 + 1          # W P V H S F two each; TR 0.15 and T none sit at an end of their order
    assert g.combo(*g.locate(d)) == d
