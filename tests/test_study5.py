"""Study 5 (RUNLOG 2026-10-07, approved with sample A): last-30-minute ES momentum into the close.

Hand-verified on one synthetic session. Prior session: flat at 5000.00, so P_prev = 5000.00 (its 15:59
bar's close). Session D: flat at 5005 until the 15:29 bar closes at 5010.00, then flat at 5012 until the
15:59 bar closes at 5015.00, so the 16:00 bar opens at 5015.00. VIX(D-1) = 16.
  EM_V = 0.75 x 0.16 / sqrt(252) x 5000 = 37.7965 points
  r_ROD = +10 / 37.7965 > 0 -> long (d = +1)
  long:  E = 5010.00 + 0.25 = 5010.25, X = 5015.00 - 0.25 = 5014.75, pnl = 4.50 - 3.98/50 = 4.4204 pts
  short: E = 5009.75, X = 5015.25, pnl = -5.50 - 0.0796 = -5.5796 pts
  long stop  = 5010.25 - 0.5 x 37.7965 = 4991.3518 -> down to the grid: 4991.25 (fills 4991.00)
  short stop = 5009.75 + 18.8982 = 5028.6482 -> up to the grid: 5028.75 (fills 5029.00)
Bars are indexed from 09:30: 15:29 is row 359, 15:30 row 360, 15:40 row 370, 15:59 row 389, 16:00 row 390.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import calendar as calm
from src import study5
from src.config import with_params
from tests.conftest import make_bars

DAY, PREV = dt.date(2024, 3, 6), dt.date(2024, 3, 5)
VIX = 16.0
EMV = 0.75 * 0.16 / np.sqrt(252) * 5000.0          # 37.7965
COST = 3.98 / 50.0                                  # 0.0796 points per round trip
N_BARS = 405                                        # 09:30 .. 16:14


def prev_bars(close=5000.0, n=N_BARS, inst=1):
    return make_bars(PREV, [close] * n, start="09:30", instrument_id=inst, spread=0.0)


def day_bars(p1529=5010.0, p1559=5015.0, inst=1):
    closes = [5005.0] * 359 + [p1529] + [5012.0] * 29 + [p1559] + [5015.0] * (N_BARS - 390)
    return make_bars(DAY, closes, start="09:30", instrument_id=inst, spread=0.0)


def run_one(cfg, pb=None, db=None, vix=VIX):
    return study5.session_trade(prev_bars() if pb is None else pb, day_bars() if db is None else db,
                                DAY, PREV, vix, cfg)


def set_bar(b, i, **ohlc):
    b = b.copy()
    for k, v in ohlc.items():
        b.loc[i, k] = v
    return b


# ---------------------------------------------------------------------------
# Units, levels, predictor
# ---------------------------------------------------------------------------
def test_em_vix_unit(cfg):
    assert study5.em_vix(VIX, 5000.0, cfg) == pytest.approx(37.7965, abs=1e-4)
    assert np.isnan(study5.em_vix(np.nan, 5000.0, cfg))


def test_stop_level_rounds_away_from_entry_on_the_tick_grid():
    assert study5.stop_level(5010.25, 1, 0.5 * EMV, 0.25) == 4991.25
    assert study5.stop_level(5009.75, -1, 0.5 * EMV, 0.25) == 5028.75
    # Already on the grid: floating-point noise never moves the stop a further tick.
    assert study5.stop_level(5000.25, 1, 19.0, 0.25) == 4981.25
    assert study5.stop_level(5000.25, 1, 19.0 + 1e-12, 0.25) == 4981.25
    assert study5.stop_level(4999.75, -1, 19.0 - 1e-12, 0.25) == 5018.75


def test_hand_session_no_stop(cfg):
    r, why = run_one(cfg)
    assert why is None
    assert r["P_prev"] == 5000.0 and r["P_dec"] == 5010.0 and r["em_v"] == pytest.approx(EMV)
    assert r["d"] == 1 and r["r_rod_em"] == pytest.approx(10.0 / EMV)
    assert r["r_l30_em"] == pytest.approx(5.0 / EMV)
    assert r["E_long"] == 5010.25 and r["E_short"] == 5009.75
    assert r["pnl_pts_momentum_long"] == pytest.approx(4.50 - COST)
    assert r["pnl_pts_momentum_short"] == pytest.approx(-5.50 - COST)
    assert r["pnl_em_momentum_long"] == pytest.approx((4.50 - COST) / EMV)
    assert r["pnl_em_momentum"] == pytest.approx((4.50 - COST) / EMV)        # chosen side: long
    # The stop is never reached on this path, so S5b equals S5a on both sides.
    assert r["S_long"] == 4991.25 and r["S_short"] == 5028.75
    assert r["exit_momentum_stop_long"] == "time" and r["exit_momentum_stop_short"] == "time"
    assert r["pnl_pts_momentum_stop_long"] == pytest.approx(4.50 - COST)
    assert not r["stopped_momentum_stop"]
    assert r["pnl_bp_momentum"] == pytest.approx(1e4 * (4.50 - COST) / 5010.25)
    assert r["pnl_usd_momentum"] == pytest.approx((4.50 - COST) * 50)


def test_direction_ignores_the_entry_bar(cfg):
    """The 15:29 close is barely above the prior close; the 15:30 bar then falls 31 points. The
    direction must come from bars closing at or before 15:30:00, so it stays long."""
    db = day_bars(p1529=5001.0)
    db = set_bar(db, 360, high=5001.0, low=4970.0, close=4970.0)
    r, why = run_one(cfg, db=db)
    assert why is None and r["d"] == 1 and r["r_rod_em"] == pytest.approx(1.0 / EMV)
    # That entry bar reaches the long stop (5001.25 - 18.898 -> 4982.25): stopped on the entry bar.
    assert r["exit_momentum_stop_long"] == "stop" and r["X_momentum_stop_long"] == 4982.00


def test_short_when_the_day_fell(cfg):
    r, _ = run_one(cfg, db=day_bars(p1529=4990.0, p1559=4985.0))
    assert r["d"] == -1
    # short: E = 4990.00 - 0.25 = 4989.75; X = 4985.00 + 0.25 = 4985.25; +4.50 - cost
    assert r["pnl_pts_momentum"] == pytest.approx(4.50 - COST)


# ---------------------------------------------------------------------------
# Stops (SPEC rule 5)
# ---------------------------------------------------------------------------
def test_stop_touched_fills_one_tick_beyond(cfg):
    db = set_bar(day_bars(), 370, open=4995.0, high=4995.0, low=4991.25, close=4993.0)
    r, _ = run_one(cfg, db=db)
    assert r["exit_momentum_stop_long"] == "stop" and r["X_momentum_stop_long"] == 4991.00
    assert r["pnl_pts_momentum_stop_long"] == pytest.approx(4991.00 - 5010.25 - COST)
    assert r["stopped_momentum_stop"]
    # The no-stop variant still exits at 16:00.
    assert r["exit_momentum_long"] == "time" and r["X_momentum_long"] == 5014.75


def test_gapped_stop_fills_at_the_worse_of_open_and_one_tick_beyond(cfg):
    gap = set_bar(day_bars(), 370, open=4990.0, high=4990.0, low=4989.5, close=4990.0)
    assert run_one(cfg, db=gap)[0]["X_momentum_stop_long"] == 4990.00           # open is worse
    at = set_bar(day_bars(), 370, open=4991.25, high=4991.25, low=4991.0, close=4991.0)
    assert run_one(cfg, db=at)[0]["X_momentum_stop_long"] == 4991.00            # open at the stop: one tick beyond


def test_stop_on_the_entry_bar_and_on_the_last_bar(cfg):
    entry = set_bar(day_bars(), 360, low=4991.0)
    r, _ = run_one(cfg, db=entry)
    assert r["exit_momentum_stop_long"] == "stop" and r["X_momentum_stop_long"] == 4991.00
    last = set_bar(day_bars(), 389, low=4991.25)
    r, _ = run_one(cfg, db=last)
    assert r["exit_momentum_stop_long"] == "stop" and r["X_momentum_stop_long"] == 4991.00


def test_short_stop_fills_one_tick_above(cfg):
    db = set_bar(day_bars(), 370, open=5015.0, high=5028.75, low=5015.0, close=5016.0)
    r, _ = run_one(cfg, db=db)
    assert r["exit_momentum_stop_short"] == "stop" and r["X_momentum_stop_short"] == 5029.00
    assert r["pnl_pts_momentum_stop_short"] == pytest.approx(-(5029.00 - 5009.75) - COST)


# ---------------------------------------------------------------------------
# Skips
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("drop,reason", [(390, "no_exit_bar"), (359, "no_decision_bar"), (360, "no_entry_bar")])
def test_missing_bars_skip_the_session(cfg, drop, reason):
    db = day_bars().drop(index=drop).reset_index(drop=True)
    assert run_one(cfg, db=db) == (None, reason)


def test_prior_close_must_be_the_last_full_rth_bar(cfg):
    pb = prev_bars().drop(index=389).reset_index(drop=True)          # no 15:59 bar on D-1
    assert run_one(cfg, pb=pb) == (None, "no_prev_close")


def test_flat_predictor_no_vix_and_instrument_mismatch(cfg):
    assert run_one(cfg, db=day_bars(p1529=5000.0)) == (None, "flat_predictor")
    assert run_one(cfg, vix=np.nan) == (None, "no_vix")
    assert run_one(cfg, pb=prev_bars(inst=2)) == (None, "instrument_mismatch")


def test_calendar_eligibility():
    ok = {"half_day": False, "roll": False}
    assert study5.eligibility(ok, prev_half_day=False) is None
    assert study5.eligibility({**ok, "half_day": True}, False) == "half_day"
    assert study5.eligibility(ok, prev_half_day=True) == "prev_half_day"
    assert study5.eligibility({**ok, "roll": True}, False) == "roll_day"


# ---------------------------------------------------------------------------
# Descriptive extras
# ---------------------------------------------------------------------------
def test_descriptive_fields(cfg):
    r, _ = run_one(cfg)
    assert r["O"] == 5005.0
    assert r["gao_ret_em"] == pytest.approx(5.0 / EMV)          # 09:59 close 5005 - prior close 5000
    assert r["intraday_ret_em"] == pytest.approx(5.0 / EMV)     # 15:29 close 5010 - 09:30 open 5005
    # Sub-windows add up to the trade-direction move from the 15:30 open to the 16:00 open.
    assert r["mv_to_moc_em"] + r["mv_moc_to_exit_em"] == pytest.approx(r["d"] * r["r_l30_em"])
    assert r["mv_to_moc_em"] == pytest.approx(2.0 / EMV)        # 15:50 open 5012 - 15:30 open 5010
    assert not r["band_reached"]                                # 5 points < 0.5 EM_V from the open
    assert r["n_missing_window"] == 0


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def frame4():
    """d = (+1, -1, +1, +1); long outcomes (1, -1, 2, 0); short = -long - 0.2 (friction)."""
    L = np.array([1.0, -1.0, 2.0, 0.0])
    return pd.DataFrame({"date": pd.bdate_range("2024-01-02", periods=4).date, "d": [1, -1, 1, 1],
                         "long": L, "short": -L - 0.2})


def test_timing_contrast_hand_value():
    f = study5.choose(frame4())
    assert list(f["pnl_em"]) == [1.0, 0.8, 2.0, 0.0]
    # strategy 0.95; random-direction benchmark 0.75 x 0.5 + 0.25 x (-0.7) = 0.2; contrast 0.75
    out = study5.timing_contrast(f, draws=500, seed=1, level=0.9)
    assert out["strategy"] == pytest.approx(0.95) and out["benchmark"] == pytest.approx(0.2)
    assert out["diff"] == pytest.approx(0.75) and out["lo"] <= out["diff"] <= out["hi"]


def test_session_permutation_centres_on_the_benchmark():
    f = study5.choose(frame4())
    out = study5.permutation_p(f, draws=4000, seed=3)
    assert out["perm_mean"] == pytest.approx(0.2, abs=0.03)   # the shuffle's expectation is gate 4's benchmark
    assert 0 < out["p"] <= 1


def test_block_permutation_with_one_block_is_the_identity():
    f = study5.choose(frame4())
    out = study5.block_permutation_p(f, block=4, draws=50, seed=1)
    assert out["p"] == 1.0                                   # every draw reproduces the observed order


def test_era_check():
    assert study5.era_ok(0.04, 0.025) and study5.era_ok(0.04, 0.02)
    assert not study5.era_ok(0.04, 0.015) and not study5.era_ok(0.04, -0.01)


def test_newey_west_slope_recovers_beta():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, 800)
    df = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=800).date, "x": x,
                       "y": 0.3 * x + rng.normal(0, 0.1, 800)})
    out = study5.nw_slope(df, "y", "x", lags=5)
    assert out["beta"] == pytest.approx(0.3, abs=0.02) and out["n"] == 800 and out["se"] > 0


def test_decision_tie_break():
    assert study5.decide({"S5a_momentum": "PASS", "S5b_momentum_stop": "PASS"}) == "PASS"
    assert study5.decide({"S5a_momentum": "PASS", "S5b_momentum_stop": "KILL"}) == "KILL"
    assert study5.decide({"S5a_momentum": "KILL", "S5b_momentum_stop": "PASS"}) == "PASS"   # S5b carries it
    assert study5.decide({"S5a_momentum": "KILL", "S5b_momentum_stop": "KILL"}) == "KILL"


# ---------------------------------------------------------------------------
# Simulated sessions through the module
# ---------------------------------------------------------------------------
def random_sessions(cfg, n, momentum_pts=0.0, seed=0):
    """n independent sessions: a tick random walk from 09:30; momentum_pts is added over the last 30
    minutes in the direction of the 09:30-to-15:29 move. Prior close 5000, VIX 16."""
    rng = np.random.default_rng(seed)
    rows = []
    days = pd.bdate_range("2023-06-05", periods=n).date
    for k, day in enumerate(days):
        steps = rng.choice([-0.25, 0.0, 0.25], size=N_BARS) * rng.integers(1, 5, N_BARS)
        closes = 5000.0 + np.cumsum(steps)
        sign = np.sign(closes[359] - 5000.0) or 1.0
        drift = np.zeros(N_BARS)
        drift[360:390] = np.arange(1, 31) * momentum_pts / 30.0
        drift[390:] = momentum_pts
        closes = np.round((closes + sign * drift) * 4) / 4
        db = make_bars(day, closes, start="09:30", spread=0.0)
        pb = make_bars(day - dt.timedelta(days=1), [5000.0] * N_BARS, start="09:30", spread=0.0)
        r, why = study5.session_trade(pb, db, day, day - dt.timedelta(days=1), VIX, cfg)
        if r is not None:
            r["date"] = day
            rows.append(r)
    return pd.DataFrame(rows)


def test_driftless_walk_returns_minus_the_friction(cfg):
    T = random_sessions(cfg, 600, seed=11)
    p = T["pnl_pts_momentum"].to_numpy()
    se = p.std(ddof=1) / np.sqrt(len(p))
    friction = 2 * 0.25 + COST
    assert abs(p.mean() + friction) < 4 * se
    q = T["pnl_pts_momentum_stop"].to_numpy()
    assert q.mean() <= -friction + 4 * se                      # stops can only add a tick


def test_report_existence_passes_with_injected_momentum_and_fails_without(cfg):
    fast = with_params(cfg, bootstrap_draws=400, perm_draws=200)
    T = random_sessions(fast, 450, momentum_pts=4.0, seed=5)
    out = study5.report(fast, T)
    a = out["variants"]["S5a_momentum"]
    assert a["checks"]["timing_contrast_ci_lower>0"] and a["checks"]["both_permutation_p<0.05"]
    assert a["existence"] == "PASS" and a["n"] == len(T)
    T0 = random_sessions(fast, 450, momentum_pts=0.0, seed=6)
    a0 = study5.report(fast, T0)["variants"]["S5a_momentum"]
    assert a0["existence"] == "FAIL" and a0["verdict_vs_rules"] == "KILL"
    assert set(out["descriptive"]) >= {"drift", "slope_r_l30_on_r_rod", "terciles_abs_r_rod", "sub_windows",
                                       "band_reached", "friction", "other_predictors", "sanity"}


def test_sample_b_is_refused_until_the_stage3_day_set_is_frozen(cfg):
    with pytest.raises(RuntimeError, match="stage-3"):
        study5.run(with_params(cfg, s5_sample="B"), save=False)


# ---------------------------------------------------------------------------
# Study 5f: the fade, holdout only (RUNLOG 2026-10-07, approved)
# ---------------------------------------------------------------------------
def test_fade_takes_the_other_side():
    f = study5.fade(frame4())
    # momentum d = (+1, -1, +1, +1) -> fade d = (-1, +1, -1, -1): short, long, short, short outcomes
    assert list(f["d"]) == [-1, 1, -1, -1]
    assert list(f["pnl_em"]) == pytest.approx([-1.2, -1.0, -2.2, -0.2])


def test_fade_gate():
    assert study5.fade_gate(0.005, 0.009, 0.5) == "PASS"       # positive and >= half the reference
    assert study5.fade_gate(0.0045, 0.009, 0.5) == "PASS"      # exactly half
    assert study5.fade_gate(0.004, 0.009, 0.5) == "FAIL"
    assert study5.fade_gate(-0.01, 0.009, 0.5) == "FAIL"
    assert study5.fade_gate(0.02, 0.0, 0.5) == "VOID"          # reference not positive: holdout never opened
    assert study5.fade_gate(0.02, -0.01, 0.5) == "VOID"


def test_fade_report_on_momentum_sessions_is_negative(cfg):
    fast = with_params(cfg, bootstrap_draws=300, perm_draws=200)
    T = random_sessions(fast, 300, momentum_pts=4.0, seed=8)
    out = study5.fade_report(fast, T)
    assert out["n"] == len(T) and out["mean_em"] < 0
    assert out["momentum_direction_mean_em"] > 0
    assert {"ci_lo", "ci_hi", "timing_contrast", "permutation", "block_permutation", "tail"} <= set(out)


# ---------------------------------------------------------------------------
# Study 5f robustness and NQ replication (RUNLOG 2026-10-07, approved)
# ---------------------------------------------------------------------------
def test_fade_nudges_are_the_registered_eight(cfg):
    got = study5.fade_nudges(cfg)
    assert got == [("s5_decision_time", "15:25"), ("s5_decision_time", "15:35"),
                   ("s5_exit_time", "15:55"), ("s5_exit_time", "16:05"),
                   ("s5_stop_em", 0.35), ("s5_stop_em", 0.75),
                   ("entry_slippage", 2), ("cost_rt_usd", 5.97)]


def test_replication_gate():
    assert study5.replication_gate(0.004, 0.01) == "PASS"
    assert study5.replication_gate(-0.001, 0.01) == "FAIL"
    assert study5.replication_gate(0.004, -0.002) == "FAIL"


def test_single_year_carry_warning():
    # all-year mean of a frame where dropping 2024 leaves a negative mean -> warning names 2024
    f = pd.DataFrame({"date": [dt.date(2023, 6, 5), dt.date(2023, 6, 6), dt.date(2024, 6, 5), dt.date(2025, 6, 5)],
                      "pnl_em": [-0.01, -0.01, 0.10, -0.01]})
    assert study5.year_carry_warning(f) == ["2024"]
    g = f.assign(pnl_em=[0.01, 0.01, 0.01, 0.01])
    assert study5.year_carry_warning(g) == []


def test_nq_overlay_config(monkeypatch):
    from src.config import load_config
    monkeypatch.delenv("GAMMA_EDGE_CONFIG", raising=False)     # the env var would override the path argument
    c = load_config("config.nq.yaml")
    assert c["data"]["root"] == "data_nq" and c["data"]["es_symbol"] == "NQ.v.0"
    assert c["market"]["point_value"] == 20.0 and c["market"]["tick"] == 0.25
    assert c["data"]["ledger"] == "data/spend_ledger.csv"                 # one ledger for all spend
    assert c["params"]["s5_stop_em"]["value"] == 0.50 and c["sample"]["holdout_start"] == "2026-01-01"


def test_spend_ledger_can_be_shared(cfg):
    from src import spend
    from src.config import ROOT
    assert spend.ledger_path(cfg) == ROOT / "data" / "spend_ledger.csv"
    c = dict(cfg, data={**cfg["data"], "root": "data_nq", "ledger": "data/spend_ledger.csv"})
    assert spend.ledger_path(c) == ROOT / "data" / "spend_ledger.csv"
