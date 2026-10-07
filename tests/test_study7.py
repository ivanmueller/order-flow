"""Study 7: Study 5 momentum (S5a) on CL, GC, ZN, 6E anchored to settlement, unit EM_R. Hand-checked."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from src import study5, study7
from src.config import load_config, param
from tests.conftest import make_bars

MARKETS = {"cl": ("CL.v.0", 0.01, 1000.0, "14:30", "14:00"),
           "gc": ("GC.v.0", 0.10, 100.0, "13:30", "13:00"),
           "zn": ("ZN.v.0", 1 / 64, 1000.0, "15:00", "14:30"),
           "6e": ("6E.v.0", 0.00005, 125000.0, "15:00", "14:30")}


@pytest.mark.parametrize("mkt", list(MARKETS))
def test_overlays(mkt, monkeypatch):
    monkeypatch.delenv("GAMMA_EDGE_CONFIG", raising=False)
    c = load_config(f"config.{mkt}.yaml")
    sym, tick, pv, settle, dec = MARKETS[mkt]
    assert c["data"]["es_symbol"] == sym and c["data"]["root"] == f"data_{mkt}"
    assert c["data"]["ledger"] == "data/spend_ledger.csv"
    assert c["market"]["tick"] == pytest.approx(tick) and c["market"]["point_value"] == pv
    assert c["market"]["rth_close"] == settle and param(c, "s5_exit_time") == settle
    assert param(c, "s5_decision_time") == dec and c["params"]["s5_decision_time"]["nudges"] == []
    assert param(c, "s5_em_unit") == "realized" and param(c, "s5_rv_sessions") == 20
    assert param(c, "s5_rv_factor") == 1.0 and param(c, "cost_rt_usd") == 3.98


def test_es_unit_unchanged(cfg):
    assert param(cfg, "s5_em_unit") == "vix" and cfg["data"]["es_symbol"] == "ES.v.0"


def _cal(closes, insts):
    d = [dt.date(2024, 1, 2) + dt.timedelta(days=k) for k in range(len(closes))]
    return pd.DataFrame({"date": d, "close": closes, "instrument_id": insts})


def test_realized_sigma_by_hand():
    s = _cal([100, 110, 99, 108.9, 50.0], [1] * 5)
    sig = study7.realized_sigma(s["date"], s["close"], s["instrument_id"], n=3)
    # returns dated d1..d3 (ln 1.1, ln 0.9, ln 1.1): SD (ddof 1) = 0.115857, usable from d4 on
    assert sig[s["date"][4]] == pytest.approx(0.115857, abs=1e-6)
    assert s["date"][3] not in sig                    # only two returns dated strictly before d3
    assert s["date"][4] in sig and len(sig) == 1      # d4's own return (to 50) never enters d4's sigma


def test_realized_sigma_skips_roll_returns():
    s = _cal([100, 110, 2000, 99 * 20, 108.9 * 20, 1.0, 1.0], [1, 1, 2, 2, 2, 2, 2])
    sig = study7.realized_sigma(s["date"], s["close"], s["instrument_id"], n=3)
    # the 110 -> 2000 jump is a contract change, not a return: returns used are 110/100 (d1), 1980/2000 (d3),
    # 2178/1980 (d4); d2 has none
    r = np.array([math.log(1.1), math.log(0.99), math.log(1.1)])
    assert sig[s["date"][5]] == pytest.approx(r.std(ddof=1), abs=1e-12)


def test_zn_and_6e_stop_grids():
    assert study5.stop_level(110.5, 1, 0.3, 1 / 64) == pytest.approx(7052 / 64)      # 110.2 -> 110.1875
    assert study5.stop_level(110.5, -1, 0.3, 1 / 64) == pytest.approx(7092 / 64)     # 110.8 -> 110.8125
    assert study5.stop_level(1.08, 1, 0.00123, 0.00005) == pytest.approx(1.07875)    # 1.07877 -> 1.07875
    assert study5.stop_level(1.08, -1, 0.00123, 0.00005) == pytest.approx(1.08125)   # 1.08123 -> 1.08125


def test_cl_session_trade_with_realized_unit(monkeypatch):
    monkeypatch.delenv("GAMMA_EDGE_CONFIG", raising=False)
    c = load_config("config.cl.yaml")
    prev, day = dt.date(2024, 3, 4), dt.date(2024, 3, 5)
    # D-1: 09:00 .. 14:29 closes at 80.00 (the 14:29 bar closes at 14:30 = settlement)
    pb = make_bars(prev, np.full(330, 80.00), start="09:00")
    # D: drifts up to 80.50 by the 13:59 bar, 80.50 to the 14:28 bar, 80.60 from the 14:29 bar on.
    # make_bars opens each bar at the previous close: entry bar 14:00 opens 80.50, exit bar 14:30 opens 80.60
    closes = np.concatenate([np.linspace(80.0, 80.5, 300), np.full(29, 80.5), np.full(32, 80.6)])
    db = make_bars(day, closes, start="09:00")
    row, why = study5.session_trade(pb, db, day, prev, np.nan, c, sigma=0.02)
    assert why is None
    assert row["em_v"] == pytest.approx(1.0 * 0.02 * 80.0)            # EM_R = 1.6 dollars
    assert row["d"] == 1 and row["P_prev"] == pytest.approx(80.0)
    E = 80.5 + 0.01                                                      # entry one tick adverse
    X = 80.6 - 0.01                                                      # time exit one tick adverse
    assert row["E_long"] == pytest.approx(E)
    assert row["pnl_pts_momentum"] == pytest.approx(X - E - 3.98 / 1000)
    assert row["pnl_em_momentum"] == pytest.approx((X - E - 3.98 / 1000) / 1.6)


def test_realized_unit_needs_sigma(monkeypatch):
    monkeypatch.delenv("GAMMA_EDGE_CONFIG", raising=False)
    c = load_config("config.cl.yaml")
    prev, day = dt.date(2024, 3, 4), dt.date(2024, 3, 5)
    pb = make_bars(prev, np.full(330, 80.0), start="09:00")
    db = make_bars(day, np.full(361, 80.0), start="09:00")
    assert study5.session_trade(pb, db, day, prev, 15.0, c, sigma=None)[1] == "no_vol"


def test_vix_path_ignores_sigma(cfg):
    prev, day = dt.date(2024, 3, 4), dt.date(2024, 3, 5)
    pb = make_bars(prev, np.full(480, 5000.0), start="08:00")
    db = make_bars(day, np.concatenate([np.full(450, 5010.0), np.full(31, 5000.0)]), start="08:00")
    row, why = study5.session_trade(pb, db, day, prev, 16.0, cfg, sigma=0.5)
    assert why is None
    assert row["em_v"] == pytest.approx(0.75 * 16 / 100 / math.sqrt(252) * 5000.0)


def test_market_verdict_uses_s5a():
    v = {"S5a_momentum": {"verdict_vs_rules": "KILL", "x": 1}, "S5b_momentum_stop": {"verdict_vs_rules": "PASS"}}
    assert study7.market_verdict(v) == "KILL"


def test_pooled_daily_equal_weight(cfg):
    d = [dt.date(2024, 1, 2), dt.date(2024, 1, 3)]
    frames = {"cl": pd.DataFrame({"date": d, "pnl_em": [0.10, -0.20]}),
              "gc": pd.DataFrame({"date": d[:1], "pnl_em": [0.30]})}
    P = study7.pooled_daily(frames)
    assert list(P["pnl_em"]) == pytest.approx([0.20, -0.20])            # mean of the markets trading that day
    assert list(P["n_markets"]) == [2, 1]
