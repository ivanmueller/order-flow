"""Hypothetical bankroll of the 5f fade: hand-checked sizing, costs, compounding and drawdown (NQ overlay)."""
import datetime as dt

import pandas as pd
import pytest

from src import bankroll
from src.config import load_config

C = 3.98 / 20  # NQ cost in points


@pytest.fixture
def nq(monkeypatch):
    monkeypatch.delenv("GAMMA_EDGE_CONFIG", raising=False)
    c = load_config("config.nq.yaml")
    assert c["bankroll"]["micro_cost_rt_usd"] == 1.18          # broker rate (2026-10-08)
    c["bankroll"]["micro_cost_rt_usd"] = 3.98                   # hand numbers below were worked at 3.98
    return c


@pytest.fixture
def T():
    d = [dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)]
    return pd.DataFrame({
        "date": d, "d": [1, -1, 1],                       # momentum direction; the fade takes the other side
        "E_long": [0.0, 200.0, 0.0], "S_long": [0.0, 190.0, 0.0],
        "E_short": [100.0, 0.0, 300.0], "S_short": [110.0, 0.0, 320.0],
        "pnl_pts_momentum_stop_long": [-99.0, -10.25 - C, -99.0],
        "pnl_pts_momentum_stop_short": [5.0 - C, -99.0, 2.0 - C],
        "exit_momentum_stop_long": ["time", "stop", "time"],
        "exit_momentum_stop_short": ["time", "time", "time"],
        "P_prev": [100.0, 200.0, 300.0], "em_v": [20.0, 20.0, 40.0]})


def test_fade_legs(T, nq):
    L = bankroll.fade_legs(T, nq)
    assert list(L["side"]) == [-1, 1, -1]
    assert list(L["gross_pts"]) == pytest.approx([5.0, -10.25, 2.0])        # fills' ticks in, costs out
    assert list(L["risk_pts"]) == pytest.approx([10.25, 10.25, 20.25])      # stop distance + one tick through


def test_full_fixed(T, nq):
    r = bankroll.simulate(bankroll.fade_legs(T, nq), nq, "full_fixed")
    assert list(r.path["pnl_usd"]) == pytest.approx([96.02, -208.98, 36.02])
    assert r.summary["final_usd"] == pytest.approx(30000 - 76.94)
    assert r.summary["max_dd_usd"] == pytest.approx(208.98)


def test_micro_fixed(T, nq):
    r = bankroll.simulate(bankroll.fade_legs(T, nq), nq, "micro_fixed")
    assert list(r.path["pnl_usd"]) == pytest.approx([6.02, -24.48, 0.02])


def test_micro_risk_compounds_in_whole_contracts(T, nq):
    r = bankroll.simulate(bankroll.fade_legs(T, nq), nq, "micro_risk", risk_pct=0.01)
    assert list(r.path["contracts"]) == [12, 12, 6]
    assert list(r.path["pnl_usd"]) == pytest.approx([72.24, -293.76, 0.12])
    assert r.summary["final_usd"] == pytest.approx(29778.60)


def test_stress_adds_the_registered_slippage_nudge(T, nq):
    r = bankroll.simulate(bankroll.fade_legs(T, nq), nq, "full_fixed", stress=True)
    assert r.summary["final_usd"] == pytest.approx(30000 - 76.94 - 3 * 0.5 * 20)


def test_too_small_to_size_skips(T, nq):
    nq["bankroll"]["start_usd"] = 1000.0
    r = bankroll.simulate(bankroll.fade_legs(T, nq), nq, "micro_risk", risk_pct=0.01)
    assert (r.path["contracts"] == 0).all() and r.summary["skipped_too_small"] == 3
    assert r.summary["final_usd"] == pytest.approx(1000.0)


def test_micro_fixed_at_broker_rate(T, nq):
    nq["bankroll"]["micro_cost_rt_usd"] = 1.18
    r = bankroll.simulate(bankroll.fade_legs(T, nq), nq, "micro_fixed")
    assert list(r.path["pnl_usd"]) == pytest.approx([8.82, -21.68, 2.82])


def test_holdout_needs_the_flag(nq, monkeypatch):
    from src import calendar as calm
    monkeypatch.delenv(calm.HOLDOUT_ENV, raising=False)
    with pytest.raises(calm.HoldoutSealed):
        bankroll.run(nq, save=False, holdout=True)
