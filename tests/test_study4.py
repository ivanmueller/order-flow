"""Study 4 (RUNLOG 2026-10-07, approved): regime-conditioned 0DTE straddle at the D-1 close.
Hand-verified payoffs on a small chain; eligibility and regime-lag rules on synthetic frames."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study3, study4
from src.config import with_params

DAY, PREV = dt.date(2024, 3, 6), dt.date(2024, 3, 5)
EM = 39.0          # = mid_C + mid_P at the 5000 strike below (20.5 + 18.5)
F = 5002.0
FEE = 1.5 / 100    # opt_cost_per_leg_usd 1.50 at $100 per point = 0.015 points per leg


def chain(put_bid_5000=18.0):
    """SPXW expiring DAY, strikes 4950..5050 step 5, hand-set quotes near the money and at the wings;
    the rest generic and valid; 4950 and 5050 have zero bids (invalid)."""
    hand = {
        (4995, "C"): (23.0, 24.0), (4995, "P"): (15.0, 16.0),
        (5000, "C"): (20.0, 21.0), (5000, "P"): (put_bid_5000, 19.0),
        (5005, "C"): (17.0, 18.0), (5005, "P"): (21.0, 22.0),
        (5020, "C"): (2.5, 3.0), (4980, "P"): (2.0, 2.5),
        (5040, "C"): (1.0, 1.2), (4960, "P"): (0.8, 1.0),
    }
    rows = []
    for K in range(4950, 5055, 5):
        for right in "CP":
            if (K, right) in hand:
                bid, ask = hand[(K, right)]
            elif K in (4950, 5050):
                bid, ask = 0.0, 0.5
            else:
                px = 5.0 + abs(K - 5000) * 0.1
                bid, ask = px, px + 0.5
            rows.append({"quote_date": PREV, "symbol": "SPXW", "expiration": DAY, "strike": float(K),
                         "right": right, "bid": bid, "ask": ask, "close": (bid + ask) / 2, "volume": 1})
    return pd.DataFrame(rows)


def test_atm_selection_matches_engine_rule(cfg):
    w = study4.wide_chain(chain(), 0.5)
    assert study4.select_atm(w, F) == 5000.0
    assert study4.select_atm(w, 5003.0) == 5005.0            # nearest strike wins
    assert study4.straddle_mid(w, 5000.0) == pytest.approx(EM)
    # A zero bid on one leg makes the strike ineligible; the next nearest both-valid strike is used.
    w0 = study4.wide_chain(chain(put_bid_5000=0.0), 0.5)
    assert study4.select_atm(w0, F) == 5005.0
    # 4950 and 5050 are invalid (zero bids), so no wing can sit there.
    assert not w0.loc[4950.0, "valid_C"] and not w0.loc[5050.0, "valid_P"]


def test_short_straddle_hand_values(cfg):
    w = study4.wide_chain(chain(), 0.5)
    r = study4.short_straddle(w, 5000.0, settle=5010.0, em=EM, cfg=cfg)
    assert r["premium_pts"] == 38.0 and r["settle_value_pts"] == 10.0 and r["legs"] == 2
    assert r["fees_pts"] == pytest.approx(2 * FEE)
    assert r["pnl_pts"] == pytest.approx(38.0 - 10.0 - 2 * FEE)
    assert r["pnl_em"] == pytest.approx((38.0 - 10.0 - 2 * FEE) / EM)
    assert r["pnl_usd"] == pytest.approx((38.0 - 10.0 - 2 * FEE) * 100)
    assert r["spread_pts"] == pytest.approx(2.0)                            # (21-20) + (19-18)
    assert study4.short_straddle(w, 5000.0, 4950.0, EM, cfg)["pnl_pts"] == pytest.approx(38.0 - 50.0 - 2 * FEE)
    assert study4.short_straddle(w, 5000.0, 5000.0, EM, cfg)["pnl_pts"] == pytest.approx(38.0 - 2 * FEE)


def test_long_straddle_hand_values(cfg):
    w = study4.wide_chain(chain(), 0.5)
    r = study4.long_straddle(w, 5000.0, settle=5050.0, em=EM, cfg=cfg)
    assert r["premium_pts"] == 40.0                                          # paid at the ask
    assert r["pnl_pts"] == pytest.approx(50.0 - 40.0 - 2 * FEE)
    assert study4.long_straddle(w, 5000.0, 5000.0, EM, cfg)["pnl_pts"] == pytest.approx(-40.0 - 2 * FEE)


def test_iron_fly_hand_values(cfg):
    """Wings one EM (39 pts) out: targets 5039 and 4961 -> nearest valid strikes 5040 (call) and 4960
    (put), bought at the ask (1.2 and 1.0). Credit 38 - 2.2 = 35.8; four legs of fees."""
    w = study4.wide_chain(chain(), 0.5)
    r = study4.iron_fly(w, 5000.0, settle=5000.0, em=EM, cfg=cfg)
    assert r["K_up"] == 5040.0 and r["K_dn"] == 4960.0 and r["legs"] == 4
    assert r["premium_pts"] == pytest.approx(35.8)
    assert r["wing_up_pts"] == 40.0 and r["wing_dn_pts"] == 40.0
    assert r["max_loss_pts"] == pytest.approx(40.0 - 35.8 + 4 * FEE)
    assert r["pnl_pts"] == pytest.approx(35.8 - 4 * FEE)
    # Inside the wings: straddle payout only. 5020 -> 20.
    assert study4.iron_fly(w, 5000.0, 5020.0, EM, cfg)["pnl_pts"] == pytest.approx(35.8 - 20.0 - 4 * FEE)
    # Beyond either wing the loss is capped at the wing width.
    assert study4.iron_fly(w, 5000.0, 5060.0, EM, cfg)["pnl_pts"] == pytest.approx(35.8 - 40.0 - 4 * FEE)
    assert study4.iron_fly(w, 5000.0, 4930.0, EM, cfg)["pnl_pts"] == pytest.approx(35.8 - 40.0 - 4 * FEE)
    assert study4.iron_fly(w, 5000.0, 4000.0, EM, cfg)["pnl_pts"] == pytest.approx(-r["max_loss_pts"])
    # Half-EM wings: targets 5019.5 / 4980.5 -> 5020 (ask 3.0) and 4980 (ask 2.5); credit 32.5.
    c2 = with_params(cfg, s4_wing_em=0.5)
    r2 = study4.iron_fly(w, 5000.0, 5035.0, EM, c2)
    assert r2["K_up"] == 5020.0 and r2["K_dn"] == 4980.0 and r2["premium_pts"] == pytest.approx(32.5)
    assert r2["pnl_pts"] == pytest.approx(32.5 - 20.0 - 4 * FEE)
    assert study4.iron_fly(w, 5000.0, 5100.0, EM, c2)["pnl_pts"] == pytest.approx(32.5 - 20.0 - 4 * FEE)


def test_iron_fly_needs_valid_wings_on_the_right_side(cfg):
    w = study4.wide_chain(chain(), 0.5)
    # Wings 2 EM out (78 pts) would need 5078 / 4922: nothing listed within tolerance -> no trade.
    assert study4.iron_fly(w, 5000.0, 5000.0, EM, with_params(cfg, s4_wing_em=2.0)) is None
    # A wing 1.95 pts out would round to a strike 5 pts away: more than 25% off, so no trade either.
    assert study4.iron_fly(w, 5000.0, 5000.0, EM, with_params(cfg, s4_wing_em=0.05)) is None
    # A call wing must sit above K and a put wing below it, never at K itself: 4.68 pts -> 5005 / 4995
    # (5 pts is within 25% of 4.68).
    r = study4.iron_fly(w, 5000.0, 5000.0, EM, with_params(cfg, s4_wing_em=0.12))
    assert r is not None and r["K_up"] == 5005.0 and r["K_dn"] == 4995.0
    assert r["premium_pts"] == pytest.approx(38.0 - 18.0 - 16.0)              # 5005 call ask 18, 4995 put ask 16


def _frames():
    days = [dt.date(2024, 3, d) for d in (4, 5, 6, 7, 8)]
    cal = pd.DataFrame({"date": days, "prev_date": [None] + days[:-1], "half_day": [False] * 5})
    gex = pd.DataFrame({"date": days, "gex_pct": [0.9, 0.2, 0.7, np.nan, 0.4]}).set_index("date")
    return cal.set_index("date"), gex


def test_regime_lag_uses_the_prior_session(cfg):
    cal, gex = _frames()
    days = list(cal.index)
    assert np.isnan(study4.lagged_regime(days[0], cal, gex, 1))        # no prior session
    assert study4.lagged_regime(days[1], cal, gex, 1) == 0.9           # D-1's percentile
    assert study4.lagged_regime(days[2], cal, gex, 2) == 0.9           # two sessions back
    assert np.isnan(study4.lagged_regime(days[4], cal, gex, 1))        # D-1 has no percentile


def test_eligibility_rules(cfg):
    """A session trades only if: not a half day, has a GEX row whose nearest expiry is the SPXW
    expiring that day, has a lagged regime, a settlement close, and a both-valid ATM strike."""
    cal, gex = _frames()
    g = pd.Series({"s0": F, "em": EM, "gex_pct": 0.7, "nearest_root": "SPXW", "nearest_exp": DAY, "flip": np.nan})
    q = chain()
    rows, why = study4.session_trades(DAY, PREV, q, g, regime=0.9, settle=5010.0, cfg=cfg)
    assert why is None and [r["mode"] for r in rows] == list(study4.MODES)
    assert all(r["K"] == 5000.0 and r["em"] == EM and r["regime_pct"] == 0.9 for r in rows)
    assert study4.session_trades(DAY, PREV, q, g, np.nan, 5010.0, cfg)[1] == "no_prev_regime"
    assert study4.session_trades(DAY, PREV, q, g, 0.9, np.nan, cfg)[1] == "no_settlement"
    g2 = g.copy(); g2["nearest_exp"] = DAY + dt.timedelta(days=1)
    assert study4.session_trades(DAY, PREV, q, g2, 0.9, 5010.0, cfg)[1] == "nearest_not_spxw_0dte"
    g3 = g.copy(); g3["nearest_root"] = "SPX"
    assert study4.session_trades(DAY, PREV, q, g3, 0.9, 5010.0, cfg)[1] == "nearest_not_spxw_0dte"
    q0 = q.copy(); q0.loc[q0["right"] == "P", "bid"] = 0.0                  # every put unquoted
    assert study4.session_trades(DAY, PREV, q0, g, 0.9, 5010.0, cfg)[1] == "no_valid_atm"
    # Only the D-expiring SPXW rows are used: other expirations in the report are ignored.
    q1 = pd.concat([q, q.assign(expiration=DAY + dt.timedelta(days=7), bid=99.0, ask=100.0)])
    rows1, _ = study4.session_trades(DAY, PREV, q1, g, 0.9, 5010.0, cfg)
    assert rows1[0]["premium_pts"] == 38.0


def test_permutation_test_generalised():
    rng = np.random.default_rng(0)
    n = 300
    t = pd.DataFrame({"regime_pct": rng.uniform(0, 1, n)})
    t["pnl_em"] = np.where(t["regime_pct"] >= 0.5, 0.5, -0.5) + rng.normal(0, 0.2, n)
    res = study3.permutation_test(t, True, 0.5, 300, 1, value="pnl_em", pct="regime_pct")
    assert res["observed_gap"] > 0.8 and res["p"] < 0.02


def test_report_on_synthetic_frames(cfg):
    rng = np.random.default_rng(3)
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(300)]
    reg = rng.uniform(0, 1, 300)
    rows = []
    for d, r in zip(days, reg):
        base = 0.15 if r >= 0.5 else -0.1                   # short straddle pays in high gamma
        for mode, sgn in (("short_straddle", 1), ("long_straddle", -1), ("iron_fly", 1)):
            p = sgn * (base + rng.normal(0, 0.3))
            rows.append({"date": d, "prev_date": d - dt.timedelta(days=1), "mode": mode, "K": 5000.0, "em": 30.0,
                         "regime_pct": r, "regime_pct_same_day": min(1.0, r + 0.01), "pnl_em": p, "pnl_pts": p * 30,
                         "pnl_usd": p * 3000, "premium_pts": 30.0, "spread_pts": 0.4, "ln_vix": 2.6 + 0.1 * rng.normal(),
                         "ln_em_s0": -5.0, "dow": d.weekday() % 5, "settle": 5000.0})
    T = pd.DataFrame(rows)
    out = study4.report(cfg, T)
    assert set(out["variants"]) == set(study4.VARIANTS)
    a = out["variants"]["S4a_short_straddle_high_gamma"]
    assert a["n"] > 100 and a["mean_em"] > 0 and a["regime_contrast"]["diff"] > 0 and a["permutation"]["p"] < 0.05
    assert set(a["checks"]) == {"n>=200", "expectancy_em>=0.03", "ci_lower>0", "regime_contrast_ci_lower>0", "permutation_p<0.05"}
    assert a["verdict_vs_rules"] in ("PASS", "KILL", "INDICATIVE")
    assert len(a["tail"]["worst_days"]) == 5 and "mean_em_ex_best5" in a["tail"] and "max_dd_em" in a
    assert "short_straddle_all_sessions" in out["contrasts"] and "short_straddle_tercile_high" in out["contrasts"]
    s1 = out["stage1_restated"]["short_straddle_ln_vix"]
    assert s1["beta_regime"] > 0 and s1["p"] < 0.05 and s1["n"] == 300
    assert "S4a_short_straddle_high_gamma" in out["diagnostic_same_day_regime"]
    from src.analysis import to_json
    to_json(out)
