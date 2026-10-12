"""Study 18: pool age (for V4), the pre-registered pass rule, and the variant evaluation on a synthetic table."""
import numpy as np
import pandas as pd
import pytest

from src import study18 as s18


def test_pool_age_in_whole_weeks_before_the_decision():
    t0 = np.array([100, 107, 282])
    assert list(s18.age_weeks(t0, first_day=93)) == [1, 2, 27]       # (t0 - first day) // 7


def test_pass_rule_needs_both_funding_views_and_every_check():
    good = {"with_funding": {"mean": 0.002, "lo": 0.001}, "without_funding": {"mean": 0.001, "lo": 0.0002,
            "first_half": 0.001, "second_half": 0.0005, "without_best_tail": 0.0004},
            "placebo_p": 0.01, "active_weeks": 40}
    assert s18.passes(good, p_max=0.05, min_weeks=26)
    for path, val in [(("without_funding", "lo"), -0.0001), (("with_funding", "lo"), 0.0),
                      (("without_funding", "second_half"), -0.0001), (("without_funding", "without_best_tail"), 0.0),
                      (("placebo_p",), 0.06), (("active_weeks",), 25)]:
        bad = {k: (dict(v) if isinstance(v, dict) else v) for k, v in good.items()}
        if len(path) == 2:
            bad[path[0]][path[1]] = val
        else:
            bad[path[0]] = val
        assert not s18.passes(bad, p_max=0.05, min_weeks=26), path


def _table(n_pools=6, n_weeks=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_pools):
        for w in range(n_weeks):
            fee = 0.004 if p < 3 else 0.0001                           # pools 0-2 rich in fees, and they look it
            rows.append({"pool": f"p{p}", "chain": "base" if p % 2 else "arbitrum", "wk": w, "t0": 4 + 7 * w,
                         "week": pd.Timestamp("2023-01-02") + pd.Timedelta(weeks=w), "v3": True,
                         "rv1": 1e-4, "m7": 1e-4, "m28": 1e-4, "rv_week": 7e-4, "fy7": fee, "tvl7": 2e6,
                         "age_w": w, "g_none_k1": 0.0, "g_perp_k1": fee - 0.0007 / 8 * 8 + rng.normal(0, 1e-4),
                         "funding_k1": 0.0005, "open_notional_k1": 0.5, "close_notional_k1": 0.5,
                         "fees_k1": fee, "value_k1": 0.0, "hedge_px_k1": -0.0007, "reb_k1": 0.0,
                         "mkt_g_perp_k1": fee - 0.0007})
    return pd.DataFrame(rows)


def test_evaluate_variant_separates_funding_and_selection():
    T = _table()
    v = {"name": "V1", "margin": 0.25, "forecast": "trailing", "tvl_floor": 1e6, "new_pool_weeks": None}
    r = s18.evaluate(T, v, har=None, gas=0.0, capital=1e5, swap=0.0, perp_fee=0.0, reps=50, seed=1, draws=200,
                     level=0.9, tail=0.05)
    # trailing forecast 7 x 1e-4; enter when 8 x fy7 >= 1.5625 x 7e-4: only the rich pools (fy7 0.004) qualify
    assert r["positions_per_week"] == pytest.approx(3.0)
    wf, nf = r["with_funding"]["mean"], r["without_funding"]["mean"]
    assert wf - nf == pytest.approx(0.0005, abs=1e-9)                  # funding separated exactly
    assert r["placebo_p"] < 0.05                                       # beats random pools of the same weeks
    v4 = dict(v, new_pool_weeks=10)
    r4 = s18.evaluate(T, v4, har=None, gas=0.0, capital=1e5, swap=0.0, perp_fee=0.0, reps=50, seed=1, draws=200,
                      level=0.9, tail=0.05)
    assert r4["active_weeks"] == 10                                    # only weeks 0-9 are "young"
