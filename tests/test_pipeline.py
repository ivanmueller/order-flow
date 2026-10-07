"""End-to-end plumbing on synthetic data: GEX -> levels -> touches -> trades -> flow/sim -> reports."""
import numpy as np
import pandas as pd
import pytest

from src import analysis, calendar as calm, gex, levels, stage3, store, touches
from src.config import load_config
from tests import synth


@pytest.fixture
def synth_env(tmp_path, monkeypatch):
    cfg_path, bars = synth.build(tmp_path)
    monkeypatch.setenv("GAMMA_EDGE_CONFIG", str(cfg_path))
    monkeypatch.delenv(calm.HOLDOUT_ENV, raising=False)
    return load_config(), bars


def test_end_to_end(synth_env):
    cfg, bars = synth_env
    g = gex.build(cfg=cfg)
    hs = calm.holdout_start(cfg)
    assert len(g) > 20 and (g["date"] < hs).all()            # holdout never built by default
    assert g["basis"].notna().all()                          # roll day resolved from roll_basis file
    assert g["gex_pct"].notna().sum() > 5
    v = gex.validate(g, store.load_daily(cfg), store.load_derived("gex_vols", cfg), cfg)
    assert v["1_pass"] and "cash close" in v["1_reference"]       # no bars given: cash-close fallback
    assert v["2_put_vol_above_call_vol_share"] > 0.95

    lv = levels.build(cfg=cfg)
    assert set(lv["group"]) >= {"placebo", "structural_only"}
    assert (lv["dist_em"] <= cfg["params"]["level_window"]["value"] + 1e-9).all()

    tc = touches.build(cfg=cfg)
    assert len(tc) > 20
    assert tc["touch_id"].is_unique

    synth.write_trades_for_touches(cfg, bars, tc, np.random.default_rng(1))
    F, T = stage3.run(cfg)
    assert len(F) == len(tc)
    assert set(T["mode"]) >= {"naive", "mirror"}
    done = T[T["pnl_r"].notna()]
    assert np.isfinite(done["pnl_r"]).all()
    # Exits respect the fill rules: no exit ever better than target, R_k always positive
    assert (done["R_k"] > 0).all()
    trade_dir = np.where(done["mode"].isin(["continuation", "mirror", "s2_retest"]), -done["d"], done["d"])   # V5, the mirror diagnostic and the E2 retest trade against d
    assert (trade_dir * (done["X"] - done["T"]) <= 1e-9).all()

    s1 = analysis.stage1(cfg)
    assert s1["verdict_vs_rules"] in ("PASS", "KILL") and s1["n_days"] > 5
    s2 = analysis.stage2(cfg)
    assert "keep_gamma_tags" in s2
    s3 = analysis.stage3(cfg, features=F)
    assert s3["verdict_vs_rules"] in ("PASS", "KILL")
    analysis.to_json(s3)
    v = analysis.variants(cfg, T, F)
    assert set(v["variants"]) == set(analysis.VARIANTS) and set(v["contrasts"]) == set(analysis.CONTRASTS)
    assert all(x["verdict_vs_rules"] in ("PASS", "KILL", "INDICATIVE") for x in v["variants"].values())
    assert "continuation" in set(T["mode"])
    analysis.to_json(v)


def test_holdout_requires_flag(synth_env):
    cfg, _ = synth_env
    with pytest.raises(calm.HoldoutSealed):
        gex.build(cfg=cfg, include_holdout=True)


def test_robustness_and_holdout(synth_env, monkeypatch):
    from src import robustness
    cfg, bars = synth_env
    gex.build(cfg=cfg)
    levels.build(cfg=cfg)
    tc = touches.build(cfg=cfg)
    synth.write_trades_for_touches(cfg, bars, tc, np.random.default_rng(1))
    stage3.run(cfg)
    c = load_config()
    for name, spec in c["params"].items():
        if name not in ("abs_threshold", "round_step", "gex_pct_lookback"):
            spec.pop("nudges", None)
    df = robustness.nudges(cfg=c)
    assert set(df["param"]) == {"BASE", "abs_threshold", "round_step", "gex_pct_lookback"}
    assert "positive_share" in df.attrs
    robustness.splits(cfg=c)

    # Holdout is refused until the flag is set, then runs end to end on holdout dates only.
    with pytest.raises(calm.HoldoutSealed):
        robustness.holdout_prep()
    monkeypatch.setenv(calm.HOLDOUT_ENV, "1")
    out = robustness.holdout_prep()
    assert out["touches"] > 0
    th = store.load_derived("touches_holdout", cfg, include_holdout=True)
    assert (th["date"] >= calm.holdout_start(cfg)).all()
    synth.write_trades_for_touches(cfg, bars, th, np.random.default_rng(2))
    res = robustness.holdout(0.2)
    assert res["verdict_vs_rules"] in ("PASS", "FAIL")
    # In-sample tables were not overwritten by the holdout run
    assert (store.load_derived("touches", cfg)["date"] < calm.holdout_start(cfg)).all()


def test_forward_vs_es_diagnostic(synth_env):
    cfg, bars = synth_env
    g = gex.build(cfg=cfg)
    cal = store.load_calendar(cfg)
    f = gex.forward_vs_es(g, store.load_bars(cfg), cal, cfg)
    assert len(f) >= len(g) - 2                      # roll day (and first day) skipped
    assert np.isfinite(f["resid"]).all()
    # Synthetic data: S0 == SPX close and basis == ES_16:00 - SPX, so r_1600 == 0 and
    # the residual at any later time is minus the ES move from 16:00 to that time.
    assert np.allclose(f["r_1600"], 0, atol=1e-6)
    assert np.allclose(f["r_1700"], -(f["es_1700"] - f["es_1600"]), atol=1e-6)
    assert np.allclose(f["resid"], f["r_1700"])            # configured quote_time is 17:00
    v = gex.validate(g, store.load_daily(cfg), None, cfg, store.load_bars(cfg), cal)
    assert "1_forward_vs_es_within_5pt_share" in v and v["1_n_days"] == len(f)
    assert v["nearest_exp_beyond_3d_n"] == 0                 # synth always has a 0/1-DTE expiry
    assert v["1_scan_best_time"] == "16:00" and v["1_scan_configured"] == "17:00"
    assert v["1_scan_16:00_within_5pt_share"] == 1.0
    assert set(gex.show(g, [str(g["date"].iloc[3])])["date"]) == {g["date"].iloc[3]}
    ts = gex.top_strikes(store.load_derived("gex_strikes", cfg), g["date"].iloc[3], n=5)
    assert len(ts) == 5 and ts["net_bn"].abs().is_monotonic_decreasing


def test_audit_missing_days(synth_env, capsys):
    """missing_days compares in-sample sessions with files on disk: synthetic data writes SPXW only,
    so every session is missing SPX; restricting the roots to SPXW shows the complete set, and
    deleting one file surfaces exactly that date."""
    from src import ingest_options as io_
    cfg, _ = synth_env
    m = io_.missing_days(cfg)
    assert len(m["eod"]) > 20 and len(m["oi"]) > 20          # SPX files never written by synth
    cfg["data"]["thetadata_symbols"] = ["SPXW"]
    cfg["data"]["opra_parents"] = ["SPXW.OPT"]
    cal = store.load_calendar(cfg)
    first = cal["date"].iloc[0]
    m = io_.missing_days(cfg)
    assert m == {"eod": [], "oi": [first]}                   # synth writes no OI for the first session
    d = cal["date"].iloc[10]
    store.oi_path(cfg, d, "SPXW").unlink()
    store.eod_path(cfg, cal["prev_date"].iloc[10], "SPXW").unlink()
    m = io_.missing_days(cfg)
    assert m["oi"] == [first, d] and m["eod"] == [cal["prev_date"].iloc[10]]
    io_.audit(cfg)
    out = capsys.readouterr().out
    assert "oi: 2 in-sample session(s) without files" in out and "eod: 1 in-sample" in out


def test_gex_why(synth_env):
    from src import gex as gx
    cfg, _ = synth_env
    g = gx.build(cfg=cfg)
    d = g["date"].iloc[5]
    w = gx.why(cfg, d)
    assert w["quote_rows"]["SPXW"] > 0 and w["oi_rows"]["SPXW"] > 0
    assert np.isfinite(w["s0"]) and np.isfinite(w["em"]) and "nearest_expiry" in w
    assert (w["expiries"]["status"] == "ok").all() and len(w["expiries"]) >= 1
    first = store.load_calendar(cfg)["date"].iloc[0]
    assert "quote_rows" not in gx.why(cfg, first)              # no D-1: stops after prev_date
    with pytest.raises(calm.HoldoutSealed):
        gx.why(cfg, calm.holdout_start(cfg))


def test_levels_skip_stale_nearest_expiry(synth_env):
    from src import levels as lv
    cfg, _ = synth_env
    g = gex.build(cfg=cfg)
    n_all = levels.build(cfg=cfg)["date"].nunique()
    d = g["date"].iloc[7]
    g.loc[g["date"] == d, "nearest_exp"] = d + pd.Timedelta(days=10)
    assert lv.stale_nearest_expiry(g.set_index("date").loc[d]) and not lv.stale_nearest_expiry(g.set_index("date").loc[g["date"].iloc[8]])
    out = levels.build(cfg=cfg, gex=g)
    assert out["date"].nunique() == n_all - 1 and d not in set(out["date"])


def test_study3_end_to_end(synth_env):
    from src import study3
    cfg, _ = synth_env
    gex.build(cfg=cfg)
    T = study3.run(cfg)
    assert not T.empty and set(T["mode"]) == {"fade", "breakout"}
    assert (T.groupby("date").size() == 2).all()                       # one touch -> two simulated trades per session
    assert (T["R_k"] > 0).all() and np.isfinite(T["pnl_r"]).all()
    fade = T[T["mode"] == "fade"]
    assert ((fade["d"] * (fade["X"] - fade["T"])) <= 1e-9).all()       # never better than the target
    out = study3.report(cfg, T)
    assert set(out["variants"]) == set(study3.VARIANTS)
    for v in out["variants"].values():
        assert v["verdict_vs_rules"] in ("PASS", "KILL", "INDICATIVE") and "permutation" in v
    analysis.to_json(out)
