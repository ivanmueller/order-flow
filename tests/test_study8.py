"""Study 8: month-end compelled flow (E0 FRED existence check, M1 ZN month-end long, M2 ES rebalancing). Hand-checked."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from src import study8
from src.config import param
from tests.conftest import make_bars

D = dt.date


def test_config_entries(cfg):
    assert param(cfg, "s8_m1_entry_days_before_end") == 4 and param(cfg, "s8_m1_entry_time") == "15:00"
    assert param(cfg, "s8_m2_entry_days_before_end") == 2 and param(cfg, "s8_e0_k_max") == 5
    assert param(cfg, "s8_fred_series") == ["DGS10", "SP500"]
    g = cfg["gates"]
    assert (g["study8_min_events"], g["study8_min_friction_multiple"], g["study8_tail_drop"]) == (25, 3, 3)


def test_overlay_bypasses_env():
    z = study8.overlay("config.zn.yaml")          # conftest sets GAMMA_EDGE_CONFIG=config.yaml; overlay ignores it
    assert z["data"]["es_symbol"] == "ZN.v.0" and z["market"]["tick"] == pytest.approx(1 / 64)


# ---- calendar ---------------------------------------------------------------------------------
def test_month_end_days():
    dates = [D(2024, 1, 26), D(2024, 1, 29), D(2024, 1, 30), D(2024, 1, 31),
             D(2024, 2, 1), D(2024, 2, 2), D(2024, 2, 5)]
    # January complete (a February date exists); February incomplete (data ends 2024-02-05 < last weekday 02-29)
    out = study8.month_end_days(dates, k=2, data_end=D(2024, 2, 5))
    assert out == [("2024-01", D(2024, 1, 29), D(2024, 1, 31))]
    # k too large for the month's sessions -> skipped
    assert study8.month_end_days(dates, k=4, data_end=D(2024, 2, 5)) == []
    # the data end reaching the month's last weekday completes the last month
    dec = [D(2025, 12, 29), D(2025, 12, 30), D(2025, 12, 31)]
    assert study8.month_end_days(dec, k=1, data_end=D(2025, 12, 31)) == [("2025-12", D(2025, 12, 30), D(2025, 12, 31))]


# ---- E0 ---------------------------------------------------------------------------------------
def test_month_end_yield_changes():
    idx = [D(2024, 1, 29), D(2024, 1, 30), D(2024, 1, 31), D(2024, 2, 1), D(2024, 2, 29), D(2024, 3, 1)]
    y = pd.Series([4.00, 3.95, 3.90, 3.92, 4.10, 4.20], index=idx)        # percent
    out = study8.month_end_yield_changes(y, k_max=2, data_end=D(2024, 3, 1))
    jan = out[out["month"] == "2024-01"].iloc[0]
    assert jan["dy_1"] == pytest.approx(-5.0) and jan["dy_2"] == pytest.approx(-10.0)   # bp
    feb = out[out["month"] == "2024-02"].iloc[0]
    assert feb["dy_1"] == pytest.approx(18.0)
    assert feb["dy_2"] == pytest.approx(20.0)           # Feb has 2 sessions; T-2 is Jan 31 (3.90)
    assert "2024-03" not in set(out["month"])           # incomplete month


def _par_price_by_cashflows(c, y, years=10, freq=2):
    n, r = years * freq, y / freq
    return sum(c / freq / (1 + r) ** t for t in range(1, n + 1)) + 1 / (1 + r) ** n


def test_par_bond_return():
    assert study8.par_bond_return(0.04, 0.04, days=0) == pytest.approx(0.0)
    assert study8.par_bond_return(0.04, 0.04, days=365) == pytest.approx(0.04)
    r = study8.par_bond_return(0.04, 0.041, days=1)
    assert r == pytest.approx(_par_price_by_cashflows(0.04, 0.041) - 1 + 0.04 / 365)
    assert r < 0 and study8.par_bond_return(0.04, 0.039, days=1) > 0


def test_rebalancing_frame_by_hand():
    idx = [D(2024, 1, 30), D(2024, 1, 31), D(2024, 2, 1), D(2024, 2, 28), D(2024, 2, 29), D(2024, 3, 1)]
    sp = pd.Series([100.0, 100.0, 102.0, 104.0, 103.0, 103.0], index=idx)
    y = pd.Series([4.0, 4.0, 4.0, 4.0, 4.0, 4.0], index=idx)            # flat yields: bond return = carry only
    f = study8.rebalancing_frame(sp, y, data_end=D(2024, 3, 1))
    feb = f[f["month"] == "2024-02"].iloc[0]
    # last day of Feb = 02-29; T-1 = 02-28; MTD to T-1 from the Jan close (100) = +4% ; bond carry 28 days
    carry = (1 + 0.04 * 1 / 365) * (1 + 0.04 * 27 / 365) - 1
    assert feb["sp_mtd"] == pytest.approx(0.04)
    assert feb["bond_mtd"] == pytest.approx(carry)
    assert feb["x"] == pytest.approx(0.04 - carry)
    assert feb["r_last"] == pytest.approx(103.0 / 104.0 - 1)
    assert "2024-01" not in set(f["month"])             # no previous month-end close


# ---- futures helpers --------------------------------------------------------------------------
def test_mtd_log_return_skips_roll():
    closes = pd.DataFrame({"date": [D(2024, 2, 29), D(2024, 3, 1), D(2024, 3, 4), D(2024, 3, 5)],
                           "close": [100.0, 101.0, 205.0, 207.05], "instrument_id": [1, 1, 2, 2]})
    r, skipped = study8.mtd_log_return(closes, D(2024, 3, 5))
    assert r == pytest.approx(math.log(101 / 100) + math.log(207.05 / 205)) and skipped == 1
    r0, _ = study8.mtd_log_return(closes.iloc[1:], D(2024, 3, 5))
    assert np.isnan(r0)                                  # no prior month-end close


def _zn_day(day, open_1500, inst=7):
    b = make_bars(day, [open_1500] * 3, start="14:59", instrument_id=inst, spread=0.0)
    return b


def test_hold_trade_long_and_roll():
    tick, pv = 1 / 64, 1000.0
    cost = 3.98 / pv
    a, z = D(2024, 1, 25), D(2024, 1, 31)
    by_day = {a: _zn_day(a, 110.0), z: _zn_day(z, 110.5)}
    r, why = study8.hold_trade(by_day, a, z, "15:00", "15:00", d=1, tick=tick, cost_pts=cost)
    assert why is None
    assert r["E"] == pytest.approx(110.0 + tick) and r["X"] == pytest.approx(110.5 - tick)
    assert r["pnl_pts"] == pytest.approx(0.5 - 2 * tick - cost)
    by_day[z] = _zn_day(z, 110.5, inst=8)                # the continuous symbol switched inside the window
    assert study8.hold_trade(by_day, a, z, "15:00", "15:00", 1, tick, cost) == (None, "roll_in_window")
    assert study8.hold_trade({z: by_day[z]}, a, z, "15:00", "15:00", 1, tick, cost) == (None, "no_entry_bar")


def test_m2_direction():
    assert study8.m2_direction(0.01) == -1 and study8.m2_direction(-0.002) == 1 and study8.m2_direction(0.0) == 0


# ---- gates -----------------------------------------------------------------------------------
def test_friction():
    assert study8.friction_pts(1 / 64, 3.98, 1000.0) / (1 / 64) == pytest.approx(2 + 3.98 / 15.625)
    assert study8.friction_pts(0.25, 3.98, 50.0) == pytest.approx(0.5796)


def test_verdict_rules(cfg):
    months = [f"2023-{m:02d}" for m in range(1, 13)] + [f"2024-{m:02d}" for m in range(1, 13)] + ["2025-01", "2025-02"]
    big = pd.DataFrame({"month": months, "pnl_pts": [2.0] * 26})
    v = study8.verdict(big, "pnl_pts", friction=0.5, cfg=cfg)
    assert v["checks"] == {"min_events": True, "friction_multiple": True, "ci_lb_positive": True, "tail_drop": True}
    assert v["verdict_vs_rules"] == "PASS_IN_SAMPLE (out-of-sample sign still required)"
    small = big.assign(pnl_pts=1.0)                      # 1.0 < 3 x 0.5
    assert study8.verdict(small, "pnl_pts", 0.5, cfg)["verdict_vs_rules"] == "KILL"
    few = big.iloc[:24]
    assert study8.verdict(few, "pnl_pts", 0.5, cfg)["checks"]["min_events"] is False
    tail = big.assign(pnl_pts=[-0.1] * 23 + [30.0, 30.0, 30.0])
    vt = study8.verdict(tail, "pnl_pts", 0.5, cfg)
    assert vt["checks"]["tail_drop"] is False and vt["mean_ex_best"] == pytest.approx(-0.1)


# ---- pipeline on synthetic bars ---------------------------------------------------------------
def _cal_and_bars(days, opens, inst=7, start="14:59"):
    by_day = {d: make_bars(d, [o] * 3, start=start, instrument_id=inst, spread=0.0) for d, o in zip(days, opens)}
    cal = pd.DataFrame({"date": days, "instrument_id": inst})
    return cal, by_day


def test_m1_trades_synthetic():
    z = study8.overlay("config.zn.yaml")
    jan = [D(2024, 1, d) for d in (24, 25, 26, 29, 30, 31)]
    feb = [D(2024, 2, d) for d in (1, 2)]
    days = jan + feb
    opens = [110.0, 110.0, 110.25, 110.5, 110.5, 110.75, 110.75, 110.75]
    cal, by_day = _cal_and_bars(days, opens)
    T, skipped = study8.m1_trades(cal, by_day, z, data_end=D(2024, 2, 2), sigma={})
    assert len(T) == 1 and T.iloc[0]["entry_date"] == D(2024, 1, 25) and T.iloc[0]["exit_date"] == D(2024, 1, 31)
    tick = 1 / 64
    assert T.iloc[0]["pnl_ticks"] == pytest.approx((0.75 - 2 * tick - 3.98 / 1000.0) / tick)


def test_m2_trades_synthetic(cfg):
    days = [D(2024, 1, 31), D(2024, 2, 1), D(2024, 2, 26), D(2024, 2, 27), D(2024, 2, 28), D(2024, 2, 29), D(2024, 3, 1)]
    # ES closes (15:59 bar) and the 16:00 bar opens; stocks up 2% to Feb 27, bonds flat -> R > 0 -> short ES
    es_close = [5000.0, 5020.0, 5080.0, 5100.0, 5090.0, 5050.0, 5050.0]
    es_by_day = {}
    for d, c in zip(days, es_close):
        es_by_day[d] = make_bars(d, [c, c], start="15:59", instrument_id=3, spread=0.0)
    es_cal = pd.DataFrame({"date": days, "instrument_id": 3})
    zn_closes = pd.DataFrame({"date": days, "close": [110.0] * 7, "instrument_id": [7] * 7})
    T, skipped = study8.m2_trades(es_cal, es_by_day, zn_closes, cfg, data_end=D(2024, 3, 1), sigma={})
    assert len(T) == 1
    r = T.iloc[0]
    assert r["entry_date"] == D(2024, 2, 27) and r["exit_date"] == D(2024, 2, 29) and r["d"] == -1
    assert r["R"] == pytest.approx(math.log(5100 / 5000))
    # short at the 16:00-bar open 5100 - 1 tick, cover at 5050 + 1 tick, less $3.98
    assert r["pnl_pts"] == pytest.approx((5100 - 0.25) - (5050 + 0.25) - 3.98 / 50)


# ---- end-to-end smoke on synthetic inputs -----------------------------------------------------
def test_e0_end_to_end(cfg, tmp_path):
    from src import store
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    days = pd.bdate_range("2018-01-01", "2026-03-31").date      # 2026 rows exist on disk but must be sealed out
    rng = np.random.default_rng(3)
    y = 3.0 + np.cumsum(rng.normal(0, 0.03, len(days)))
    sp = 3000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    store.write(pd.DataFrame({"date": days, "value": y}), study8.fred_path(c, "DGS10"))
    store.write(pd.DataFrame({"date": days, "value": sp}), study8.fred_path(c, "SP500"))
    out = study8.e0(c)
    assert out["dgs10"]["last"] == "2025-12-31"                  # holdout sealed
    eras = list(out["dgs10"]["eras"])
    assert eras[0].startswith("2020-2025") and eras[1].startswith("2018-2019")
    assert out["dgs10"]["eras"][eras[0]]["k1"]["n_months"] == 72
    assert np.isfinite(out["rebalancing"][eras[0]]["slope"])


def test_report_on_synthetic_tables(cfg):
    z = study8.overlay("config.zn.yaml")
    months = [f"{y}-{m:02d}" for y in (2023, 2024) for m in range(1, 13)] + ["2025-01", "2025-02"]
    ent = [D(int(m[:4]), int(m[5:]), 20) for m in months]
    M1 = pd.DataFrame({"month": months, "entry_date": ent, "gross_pts": 10 / 64, "pnl_ticks": 7.0,
                       "pnl_usd": 109.0, "pnl_em_r": 0.1})
    M2 = pd.DataFrame({"month": months, "entry_date": ent, "gross_pts": [1.0, -1.0] * 13, "pnl_pts": [0.9, -1.1] * 13,
                       "pnl_usd": 0.0, "pnl_em_r": 0.0, "d": [1, -1] * 13, "E": 5000.0,
                       "R": np.linspace(-0.02, 0.02, 26)})
    rep = study8.report(M1, M2, cfg, z)
    assert rep["verdicts"]["M1_zn_month_end"].startswith("PASS")        # 7.0 > 3 x 2.2547 = 6.76 ticks
    assert rep["variants"]["M1_zn_month_end"]["threshold"] == pytest.approx(3 * (2 + 3.98 / 15.625))
    assert rep["verdicts"]["M2_es_rebalancing"] == "KILL"
    assert "M2_slope_long_ret_bp_on_R" in rep["descriptive"]
