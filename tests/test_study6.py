"""Study 6, pure order flow pilot: hand-checked slots, thresholds, selection, fills and gates."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study6
from tests.conftest import make_trades

DAY = dt.date(2024, 3, 5)  # EST: ET = UTC - 5
ET = "America/New_York"


def et(hhmm):
    return pd.Timestamp(f"{DAY} {hhmm}", tz=ET).tz_convert("UTC")


@pytest.fixture
def tape():
    return make_trades([
        ("09:34:00", 100.00, 10, 1),
        ("09:36:00", 100.50, 30, 1),
        ("09:38:00", 100.25, 20, -1),
        ("09:40:00", 100.75, 5, 1),
        ("09:44:30", 101.00, 5, -1),
        ("09:45:00", 101.25, 5, 1),
        ("09:50:10", 101.50, 1, 1),
    ], DAY)


@pytest.fixture
def slots(tape):
    return study6.slot_table(tape, et("09:30"), et("10:00"), L=5, H=5,
                             grid_start=et("09:40"), grid_end=et("10:00"))


def test_slot_features_by_hand(slots):
    # t = 09:40 .. 09:45; 09:46 has no print at or after 09:51 inside the span
    assert list(slots["t"]) == [et(f"09:4{k}") for k in range(6)]
    np.testing.assert_allclose(slots["I"], [10 / 50, 15 / 55, -15 / 25, -15 / 25, 1.0, 0.0])
    np.testing.assert_allclose(slots["vol"], [50, 55, 25, 25, 5, 10])
    np.testing.assert_allclose(slots["dP"], [-0.25, 0.25, 0.5, 0.5, 0.0, 0.25])
    np.testing.assert_allclose(slots["entry_px"], [100.75, 101.00, 101.00, 101.00, 101.00, 101.25])
    np.testing.assert_allclose(slots["exit_px"], [101.25, 101.50, 101.50, 101.50, 101.50, 101.50])
    # the signal window ends strictly before t; the entry print is at or after t
    assert (slots["entry_ts"] >= slots["t"]).all() and (slots["exit_ts"] >= slots["t"] + pd.Timedelta(minutes=5)).all()


def test_slot_window_must_lie_inside_the_span(tape):
    s = study6.slot_table(tape, et("09:37"), et("10:00"), L=5, H=5, grid_start=et("09:40"), grid_end=et("10:00"))
    assert s["t"].min() == et("09:42")  # t - L must not precede the span start


def test_slot_grid_end_caps_the_exit(tape):
    s = study6.slot_table(tape, et("09:30"), et("10:00"), L=5, H=5, grid_start=et("09:40"), grid_end=et("09:47"))
    assert s["t"].max() == et("09:42")


def test_slot_skips_instrument_change(tape):
    t = tape.copy()
    t.loc[t.index >= 5, "instrument_id"] = 2  # 09:45:00 onwards is a new contract: every exit print is on it
    s = study6.slot_table(t, et("09:30"), et("10:00"), L=5, H=5, grid_start=et("09:40"), grid_end=et("10:00"))
    assert s.empty


def test_thresholds_use_only_earlier_sessions():
    d = [dt.date(2024, 1, k) for k in (2, 3, 4, 5)]
    S = pd.DataFrame({"date": [d[0], d[0], d[1], d[1], d[2], d[2], d[3]],
                      "I": [0.1, -0.3, 0.2, -0.4, 0.9, 0.9, 0.0],
                      "dP": [1.0, -3.0, 2.0, 4.0, 9.0, 9.0, 0.0]})
    th = study6.thresholds(S, pct=0.5, lookback=2, warmup=2)
    assert d[0] not in th and d[1] not in th          # warm-up
    assert th[d[2]] == pytest.approx((0.25, 2.5))     # from d0, d1 only
    assert th[d[3]] == pytest.approx((0.65, 6.5))     # |I| {0.2, 0.4, 0.9, 0.9}; |dP| {2, 4, 9, 9}


def test_continuation_selection_and_pnl(slots, cfg):
    T = study6.select(slots, "continuation", q=0.5, med=0.25, mult=1.0, H=5)
    assert list(T["t"]) == [et("09:42")]              # 09:43 and 09:44 fall inside the open trade
    assert list(T["d"]) == [-1]
    P = study6.price(T, cfg)
    assert P["gross_pts"].iloc[0] == pytest.approx(-0.5)
    assert P["E"].iloc[0] == pytest.approx(100.75) and P["X"].iloc[0] == pytest.approx(101.75)
    assert P["pnl_pts"].iloc[0] == pytest.approx(-0.5 - 0.5 - 3.98 / 50)


def test_absorption_fades_flow_that_did_not_move_price(slots, cfg):
    T = study6.select(slots, "absorption", q=0.5, med=0.25, mult=1.0, H=5)
    assert list(T["t"]) == [et("09:42")] and list(T["d"]) == [1]   # sellers, price up: buy
    P = study6.price(T, cfg)
    assert P["pnl_pts"].iloc[0] == pytest.approx(0.5 - 0.5 - 3.98 / 50)


def test_pressure_fades_flow_that_moved_price(slots):
    assert study6.select(slots, "pressure", q=0.5, med=0.25, mult=1.0, H=5).empty
    T = study6.select(slots, "pressure", q=0.25, med=0.25, mult=1.0, H=5)
    assert list(T["t"]) == [et("09:41")] and list(T["d"]) == [-1]  # buyers lifted price 0.25 >= 1 x 0.25


def test_next_trade_waits_for_the_exit(slots):
    T = study6.select(slots, "continuation", q=0.0, med=0.0, mult=1.0, H=5)
    assert list(T["t"]) == [et("09:40")] and list(T["d"]) == [1]  # busy to 09:45; 09:45 has I = 0


def test_zero_imbalance_never_trades(slots):
    T = study6.select(slots, "continuation", q=0.0, med=0.0, mult=1.0, H=1)
    assert (T["d"] != 0).all() and et("09:45") not in set(T["t"])


def _trades(days, per_day, pnl, gross=None):
    rows = []
    for i, d in enumerate(days):
        for k in range(per_day):
            rows.append({"date": d, "pnl_pts": pnl[(i * per_day + k) % len(pnl)],
                         "gross_pts": (gross or pnl)[(i * per_day + k) % len(gross or pnl)], "d": 1 if k % 2 else -1})
    return pd.DataFrame(rows)


def test_gates(cfg):
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=k) for k in range(100)]
    good = _trades(days, 4, [1.0, 0.5, 0.25, 0.75], gross=[1.6, 1.1, 0.85, 1.35])
    r = study6.verdict(study6.summarize(good, cfg), cfg)
    assert r["verdict"] == "ADVANCE" and all(r["gates"].values())
    few = _trades(days[:50], 4, [1.0, 0.5, 0.25, 0.75])
    assert study6.verdict(study6.summarize(few, cfg), cfg)["verdict"] == "KILL"  # n 200 < 300
    thin = _trades(days, 4, [0.2, 0.1, 0.15, 0.25], gross=[0.8, 0.7, 0.75, 0.85])
    r = study6.verdict(study6.summarize(thin, cfg), cfg)
    assert r["verdict"] == "KILL" and not r["gates"]["3_expectancy"] and r["gates"]["2_existence"]


def test_tail_gate_bites_when_five_sessions_carry_it(cfg):
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=k) for k in range(100)]
    T = _trades(days, 4, [-0.05])
    T.loc[T["date"].isin(days[:5]), "pnl_pts"] = 30.0
    T["gross_pts"] = T["pnl_pts"] + 0.58
    s = study6.summarize(T, cfg)
    assert s["mean_ex_best"] < 0 and not study6.verdict(s, cfg)["gates"]["4_tail"]
