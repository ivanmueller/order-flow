"""Study 16: daily panel from hourly bars and funding, and the market-neutral portfolio simulator -- by hand."""
import numpy as np
import pandas as pd
import pytest

from src import study16 as s16

HR = 3_600 * 10**9
DAY = 24 * HR
T0 = pd.Timestamp("2022-03-01", tz="UTC").value


# ---- panel ---------------------------------------------------------------------------------------------
def test_panel_times_by_hand():
    n = 72
    h = pd.DataFrame({"ts": T0 + np.arange(n, dtype="int64") * HR, "open": 100.0 + np.arange(n),
                      "high": 100.0 + np.arange(n), "low": 100.0 + np.arange(n), "close": 100.0 + np.arange(n),
                      "qv": 1.0, "real": True})
    f = pd.DataFrame({"ts": np.array([T0 + 8 * HR, T0 + HR, T0 + 25 * HR], "int64"), "rate": [0.001, 0.002, 0.004]})
    pn = s16.panel_from_frames({"AAAUSDT": h}, {"AAAUSDT": f}, T0, T0 + 3 * DAY, trade_hour=1)
    P, C, QV = pn["P"][:, 0], pn["C"][:, 0], pn["QV"][:, 0]
    assert list(P) == [100.0, 124.0, 148.0]                  # close of the 00:00 bar = price at 01:00
    assert np.isnan(C[0]) and list(C[1:]) == [123.0, 147.0]   # close at 00:00 (the 23:00 bar of the day before)
    assert np.isnan(QV[0]) and list(QV[1:]) == [24.0, 24.0]  # the previous day's volume, known at 00:00
    # holding funding: settled in (01:00 day d, 01:00 day d+1]; 08:00 day 0 -> d 0; 01:00 day 1 -> d 0; 01:00 day 0 -> none
    assert list(pn["FH"][:, 0]) == pytest.approx([0.001 + 0.004, 0.0, 0.0])
    # signal funding: settled in (00:00 day d-1, 00:00 day d]: 01:00 and 08:00 day 0 -> d 1; 01:00 day 1 -> d 2
    assert list(pn["FS"][:, 0]) == pytest.approx([0.0, 0.003, 0.004])


# ---- simulator -------------------------------------------------------------------------------------------
G = np.array([-0.02, -0.01, 0.0, 0.01, 0.02, 0.03])


def panel(D=10, g=G, fh=None):
    d = np.arange(D)[:, None]
    P = 100 * (1 + g[None, :]) ** d
    N = len(g)
    return {"P": P, "C": P.copy(), "QV": np.full((D, N), 1e9), "FH": np.zeros((D, N)) if fh is None else fh,
            "FS": np.zeros((D, N)), "first_ns": np.full(N, T0 - 400 * DAY), "last_ns": np.full(N, T0 + 400 * DAY),
            "day_ns": T0 + np.arange(D, dtype="int64") * DAY, "syms": [f"C{i}" for i in range(N)],
            "btc": {"P": np.full(D, 100.0), "FH": np.zeros(D)}}


SPEC = {"kind": "c2", "lookback_d": 2, "hold_d": 2, "quantile": 1 / 3, "min_volume": 1.0, "weighting": "equal",
        "min_names": 2, "min_history_d": 30, "volume_days": 1, "vol_days": 3, "cost": 0.0}


def test_momentum_period_by_hand():
    out = s16.simulate(panel(), SPEC)
    assert list(out["start"]) == [0, 2, 4, 6] and not out["traded"].iloc[0]       # day 0 has no 2-day look-back
    r = (1 + G) ** 2 - 1
    hand = 0.5 * (r[4] + r[5]) / 2 + 0.5 * (-r[0] - r[1]) / 2
    assert out["net"].iloc[1] == pytest.approx(hand)
    rev = s16.simulate(panel(), {**SPEC, "kind": "c3"})                             # reversal: the mirror
    assert rev["net"].iloc[1] == pytest.approx(-hand)


def test_costs_follow_turnover():
    out = s16.simulate(panel(), {**SPEC, "cost": 0.01})
    assert out["cost"].iloc[1] == pytest.approx(0.01)        # first position: all 1.0 of gross weight traded
    assert out["cost"].iloc[2] == pytest.approx(0.0)         # same names, same weights
    assert out["cost"].iloc[0] == 0.0


def test_funding_paid_by_longs_and_short_loss_capped():
    fh = np.zeros((10, 6)); fh[:, 5] = 0.01
    base = s16.simulate(panel(), SPEC)["net"].iloc[1]
    withf = s16.simulate(panel(fh=fh), SPEC)
    assert withf["net"].iloc[1] == pytest.approx(base - 0.5 * 0.02 / 2)               # coin 5 long, 2 days of 1%
    assert withf["funding"].iloc[1] == pytest.approx(-0.5 * 0.02 / 2)
    g = G.copy(); g[0] = 2.0                                                          # coin 0 triples daily
    out = s16.simulate(panel(g=g), {**SPEC, "kind": "c3"})                           # reversal shorts the winners
    r = (1 + g) ** 2 - 1
    hand = 0.5 * (r[1] + r[2]) / 2 + 0.5 * (max(-r[0], -1.0) + -r[5]) / 2
    assert out["net"].iloc[1] == pytest.approx(hand)


def test_funding_carry_shorts_the_highest_funding():
    pn = panel()
    pn["FS"][:, :] = np.array([0.005, 0.004, 0.003, 0.002, 0.001, 0.0])               # coin 0 most crowded
    out = s16.simulate(pn, {**SPEC, "kind": "c1", "lookback_d": 1})
    r = (1 + G) ** 2 - 1
    assert out["net"].iloc[1] == pytest.approx(0.5 * (r[4] + r[5]) / 2 + 0.5 * (-r[0] - r[1]) / 2)


def test_new_listing_short_hedged_with_btc():
    pn = panel(D=12)
    pn["first_ns"][3] = T0 + 2 * DAY                          # coin 3 lists on day 2
    pn["btc"]["P"] = 100 * 1.01 ** np.arange(12)
    spec = {**SPEC, "kind": "c4", "window_d": 4, "delay_d": 1, "hold_d": 2, "min_names": 1}
    out = s16.simulate(pn, spec)
    # rebalances 0,2,4,6,8: coin 3's age at day 4 is 2 (in [1, 5)); at day 2 it is 0 (< delay); at 6 it is 4
    traded = out.set_index("start")["traded"]
    assert not traded[0] and not traded[2] and traded[4] and traded[6] and not traded[8]
    r3, rb = (1.01) ** 2 - 1, (1.01) ** 2 - 1
    assert out.set_index("start")["net"][4] == pytest.approx(0.5 * -r3 + 0.5 * rb)


def test_volume_and_history_filters():
    pn = panel()
    pn["QV"][:, 5] = 10.0                                     # coin 5 too thin
    pn["first_ns"][4] = T0 - 5 * DAY                          # coin 4 too young
    out = s16.simulate(pn, {**SPEC, "min_volume": 1e6, "quantile": 0.5})
    r = (1 + G) ** 2 - 1
    # universe 0-3, half a leg each: top two 2, 3; bottom two 0, 1
    assert out["net"].iloc[1] == pytest.approx(0.5 * (r[2] + r[3]) / 2 + 0.5 * (-r[0] - r[1]) / 2)


def test_shuffled_placebo_keeps_leg_sizes():
    pn = panel(D=40, g=np.linspace(-0.02, 0.03, 15))
    real = s16.simulate(pn, {**SPEC, "quantile": 0.2})
    shuf = s16.simulate(pn, {**SPEC, "quantile": 0.2}, rng=np.random.default_rng(1))
    assert (real["n_long"] == shuf["n_long"]).all() and (real["n_short"] == shuf["n_short"]).all()
    assert real["net"].iloc[1:].mean() > shuf["net"].iloc[1:].mean()


def test_weekly_mean_and_gate(cfg):
    per = pd.DataFrame({"start": np.arange(0, 70, 7), "net": [0.01] * 9 + [0.2], "traded": True,
                        "week": np.arange(10), "day_ns": T0 + np.arange(0, 70, 7, dtype="int64") * DAY})
    w = s16.weekly(per, hold_d=7)
    assert w["mean"] == pytest.approx(0.029) and w["without_best"] == pytest.approx(0.01)
    assert s16.weekly(per.assign(net=per["net"] / 7), hold_d=1)["mean"] == pytest.approx(0.029)


def test_coins_present_when_the_archive_begins_are_not_new_listings():
    pn = panel(D=12)
    pn["first_ns"][:] = T0 - 400 * DAY
    pn["first_ns"][3] = T0 + 2 * DAY
    pn["first_ns"][2] = T0 - 1 * DAY                          # first seen before the study start: not "new"
    pn["new_from_ns"] = T0 - 0
    out = s16.simulate(pn, {**SPEC, "kind": "c4", "window_d": 4, "delay_d": 1, "hold_d": 2, "min_names": 1})
    assert out.set_index("start")["n_short"][4] == 1          # only coin 3


def test_detail_contributions_add_up_to_gross():
    det = []
    out = s16.simulate(panel(), SPEC, detail=det)
    D = pd.concat(det)
    g = D.groupby("start")["contrib"].sum()
    assert g[2] == pytest.approx(out.set_index("start")["gross"][2])
    assert s16.series_signature(out) == s16.series_signature(s16.simulate(panel(), SPEC))
