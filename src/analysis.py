"""Stage reports, scored against the frozen pass/kill rules. Reports numbers; gate calls are the user's.

  python -m src.analysis stage1
  python -m src.analysis stage2
  python -m src.analysis stage3 [--carry gamma_only,both]
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from src import regime, stats, store
from src.config import ROOT, load_config, param


def _boot(cfg):
    return param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")


# ---------------------------------------------------------------------------
# Stage 1
# ---------------------------------------------------------------------------
def stage1_frame(cfg=None, include_holdout=False) -> pd.DataFrame:
    cfg = cfg or load_config()
    df = regime.build(store.load_bars(cfg, include_holdout), store.load_calendar(cfg, include_holdout),
                      store.load_derived("gex_daily", cfg, include_holdout), store.load_daily(cfg, include_holdout), cfg)
    return df


def _stage1_models(df: pd.DataFrame, pct: str, control: str, lags: int) -> dict:
    out = {}
    for y in ("rr", "vr", "er"):
        d = df.dropna(subset=[y, pct, control])
        res = stats.ols_nw(d, f"{y} ~ {pct} + {control} + C(dow)", lags)
        out[y] = {"beta_gex": float(res.params[pct]), "p": float(res.pvalues[pct]), "n": int(res.nobs),
                  "r2": float(res.rsquared)}
    return out


def stage1(cfg=None, df: pd.DataFrame | None = None) -> dict:
    cfg = cfg or load_config()
    if df is None:
        df = stage1_frame(cfg)
    df = df[~df["half_day"]].dropna(subset=["gex_pct", "ln_vix", "rr"]).sort_values("date").reset_index(drop=True)
    draws, seed, lvl = _boot(cfg)
    lags = param(cfg, "nw_lags")
    main = _stage1_models(df, "gex_pct", "ln_vix", lags)
    df["quintile"] = pd.qcut(df["gex_pct"], 5, labels=[1, 2, 3, 4, 5])
    q = {y: stats.bootstrap_group_means(df, y, "quintile", draws, seed, lvl) for y in ("rr", "vr", "er")}
    rr_q = q["rr"].set_index("quintile")["mean"]
    gap = float((rr_q[1] - rr_q[5]) / rr_q[1])
    g = cfg["gates"]
    checks = {
        "rr_beta_negative_and_significant": main["rr"]["beta_gex"] < 0 and main["rr"]["p"] < g["stage1_p"],
        "rr_top_vs_bottom_quintile_gap>=15%": gap >= g["stage1_quintile_gap"],
        "vr_same_direction": main["vr"]["beta_gex"] < 0,
    }
    # Robustness: report, never tune on.
    robust = {"ln_em_s0_control": _stage1_models(df.dropna(subset=["ln_em_s0"]), "gex_pct", "ln_em_s0", lags)}
    ev = _event_dates()
    if ev:
        robust["drop_event_days"] = _stage1_models(df[~df["date"].isin(ev)], "gex_pct", "ln_vix", lags)
        robust["event_days_dropped"] = int(df["date"].isin(ev).sum())
    if df["gex_pct_0dte"].notna().sum() > 30:
        robust["gex_pct_0dte"] = _stage1_models(df.dropna(subset=["gex_pct_0dte"]), "gex_pct_0dte", "ln_vix", lags)
    return {"n_days": len(df), "first": str(df["date"].min()), "last": str(df["date"].max()),
            "models": main, "quintiles": {k: v.to_dict("records") for k, v in q.items()},
            "rr_quintile_gap": gap, "checks": checks,
            "verdict_vs_rules": "PASS" if all(checks.values()) else "KILL", "robustness": robust}


def _event_dates() -> set:
    """FOMC/CPI/NFP dates for the Stage 1 robustness check (static/events.csv)."""
    f = ROOT / "static" / "events.csv"
    if not f.exists():
        return set()
    return set(pd.to_datetime(pd.read_csv(f)["date"]).dt.date)


# ---------------------------------------------------------------------------
# Stage 2
# ---------------------------------------------------------------------------
STAGE2_FORMULA = ("success ~ G + G:gex_pct + gex_pct + R_nd + PD + ON + First + Dist"
                  " + C(tod, Treatment('open')) + d")


def stage2_frame(touches: pd.DataFrame) -> pd.DataFrame:
    t = touches.copy()
    t["success"] = t["success"].astype(int)
    t["G"] = t["is_gamma"].astype(int)
    t["R_nd"] = t["tag_round"].astype(int)
    t["PD"] = t["tag_pd"].astype(int)
    t["ON"] = t["tag_on"].astype(int)
    t["First"] = t["first"].astype(int)
    t["Dist"] = t["dist_em"]
    return t.dropna(subset=["gex_pct"])


def stage2(cfg=None, touches: pd.DataFrame | None = None) -> dict:
    cfg = cfg or load_config()
    if touches is None:
        touches = store.load_derived("touches", cfg)
    t = stage2_frame(touches)
    res = stats.logit_clustered(t, STAGE2_FORMULA)
    ct = stats.coef_table(res)
    lvl = param(cfg, "ci_level")
    t["gex_tercile"] = pd.qcut(t["gex_pct"], 3, labels=["low", "mid", "high"])
    rows = []
    for (grp, ter), g in t.groupby(["group", "gex_tercile"], observed=True):
        lo, hi = stats.wilson(int(g["success"].sum()), len(g), lvl)
        rows.append({"group": grp, "gex_tercile": ter, "n": len(g), "success": g["success"].mean(),
                     "lo": lo, "hi": hi, "timeout": g["timeout"].mean()})
    for grp, g in t.groupby("group"):
        lo, hi = stats.wilson(int(g["success"].sum()), len(g), lvl)
        rows.append({"group": grp, "gex_tercile": "all", "n": len(g), "success": g["success"].mean(),
                     "lo": lo, "hi": hi, "timeout": g["timeout"].mean()})
    table = pd.DataFrame(rows)
    p_lim = cfg["gates"]["stage2_p"]
    bg = ct.loc["G"] if "G" in ct.index else None
    bgx = ct.loc["G:gex_pct"] if "G:gex_pct" in ct.index else None
    keep_gamma = bool((bg is not None and bg["coef"] > 0 and bg["p"] < p_lim) or
                      (bgx is not None and bgx["coef"] > 0 and bgx["p"] < p_lim))
    allg = table[table["gex_tercile"] == "all"].set_index("group")
    placebo = allg["success"].get("placebo", np.nan)
    beats = {g: bool(allg.loc[g, "lo"] > placebo) for g in allg.index if g != "placebo"}
    return {"n_touches": len(t), "n_days": t["date"].nunique(), "coefs": ct.reset_index(names="term").to_dict("records"),
            "success_table": table.to_dict("records"), "keep_gamma_tags": keep_gamma,
            "carry_groups": ["gamma_only", "both", "structural_only"] if keep_gamma else ["structural_only", "both"],
            "groups_whose_90ci_beats_placebo_rate": beats,
            "note": None if any(beats.values()) else "No real level type beats placebo: Stage 3 tests pure order flow."}


# ---------------------------------------------------------------------------
# Stage 3
# ---------------------------------------------------------------------------
def stage3(cfg=None, sim_trades: pd.DataFrame | None = None, features: pd.DataFrame | None = None,
           carry: list[str] | None = None) -> dict:
    cfg = cfg or load_config()
    if sim_trades is None:
        sim_trades = store.load_derived("sim_trades", cfg)
    draws, seed, lvl = _boot(cfg)
    done = sim_trades[sim_trades["pnl_r"].notna()].copy() if "pnl_r" in sim_trades else sim_trades.iloc[0:0]
    sampled_days = int(features["date"].nunique()) if features is not None and not features.empty else None
    table = []
    for grp in ["gamma_only", "structural_only", "both", "placebo", "ALL_REAL"]:
        sub = done[done["group"] != "placebo"] if grp == "ALL_REAL" else done[done["group"] == grp]
        row = {"group": grp}
        for mode in ("naive", "confirmed"):
            s = stats.trade_summary(sub[sub["mode"] == mode], draws, seed, lvl, sampled_days)
            row.update({f"{mode}_{k}": v for k, v in s.items()})
        d = stats.day_bootstrap_diff(sub[sub["mode"] == "confirmed"], sub[sub["mode"] == "naive"],
                                     "pnl_r", draws, seed, lvl)
        row.update({"conf_minus_naive": d["diff"], "diff_lo": d["lo"], "diff_hi": d["hi"]})
        table.append(row)
    carry = carry or ["gamma_only", "both", "structural_only"]
    cs = done[done["group"].isin(carry)]
    conf, naive = cs[cs["mode"] == "confirmed"], cs[cs["mode"] == "naive"]
    s = stats.trade_summary(conf, draws, seed, lvl, sampled_days)
    s["sampled_days"] = sampled_days
    dd = stats.day_bootstrap_diff(conf, naive, "pnl_r", draws, seed, lvl)
    g = cfg["gates"]
    checks = {
        f"n_confirmed>={g['stage3_min_trades']}": s.get("n", 0) >= g["stage3_min_trades"],
        f"expectancy>={g['stage3_min_expectancy_r']}R": s.get("expectancy_r", -1) >= g["stage3_min_expectancy_r"],
        "ci_lower>0": s.get("ci_lo", -1) > 0,
        "conf_minus_naive_ci_lower>0": dd["lo"] > 0,
    }
    secondary = None
    if features is not None and not features.empty and len(conf) > 30:
        m = conf.merge(features[["touch_id", "abs_ratio", "approach_delta", "exhaustion", "is_gamma",
                                 "tag_round", "tag_pd", "tag_on"]], on="touch_id")
        m = m.replace([np.inf, -np.inf], np.nan).dropna(subset=["abs_ratio", "approach_delta", "exhaustion"])
        for c in ("is_gamma", "tag_round", "tag_pd", "tag_on"):
            m[c] = m[c].astype(int)
        if len(m) > 30:
            r = stats.ols_clustered(m, "pnl_r ~ abs_ratio + approach_delta + exhaustion + is_gamma + tag_round + tag_pd + tag_on")
            secondary = stats.coef_table(r).reset_index(names="term").to_dict("records")
    skips = sim_trades["skip"].value_counts().to_dict() if "skip" in sim_trades else {}
    exits = done.groupby(["mode", "exit_reason"]).size().to_dict() if not done.empty else {}
    return {"carry_groups": carry, "confirmed": s, "conf_minus_naive": dd, "checks": checks,
            "verdict_vs_rules": "PASS" if all(checks.values()) else "KILL", "table": table,
            "secondary_regression": secondary, "skips": skips,
            "exit_reasons": {f"{k[0]}:{k[1]}": v for k, v in exits.items()}}


def holdout(in_sample_expectancy: float, cfg=None, sim_trades: pd.DataFrame | None = None,
            carry: list[str] | None = None) -> dict:
    cfg = cfg or load_config()
    if sim_trades is None:
        sim_trades = store.load_derived("sim_trades_holdout", cfg, include_holdout=True)
    r = stage3(cfg, sim_trades, carry=carry)
    e = r["confirmed"].get("expectancy_r", np.nan)
    ok = np.sign(e) == np.sign(in_sample_expectancy) and e >= cfg["gates"]["holdout_min_fraction"] * in_sample_expectancy
    return {"holdout_expectancy_r": e, "in_sample_expectancy_r": in_sample_expectancy,
            "verdict_vs_rules": "PASS" if ok else "FAIL", "detail": r}


def to_json(x) -> str:
    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        return str(o)
    return json.dumps(x, indent=2, default=default)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["stage1", "stage2", "stage3"])
    ap.add_argument("--carry", help="comma-separated level groups carried from Stage 2")
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.stage == "stage1":
        out = stage1(cfg)
    elif a.stage == "stage2":
        out = stage2(cfg)
    else:
        feats = store.load_derived("features", cfg)
        out = stage3(cfg, features=feats, carry=a.carry.split(",") if a.carry else None)
    print(to_json(out))


if __name__ == "__main__":
    main()
