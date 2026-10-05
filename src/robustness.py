"""Robustness checks on in-sample data (report only, never tune) and the one-shot holdout run.

  python -m src.robustness nudges [--carry gamma_only,both,structural_only]
  python -m src.robustness splits
  GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.robustness holdout-prep
  GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.ingest_futures trades --holdout --price-only   (then --approve-usd)
  GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.robustness holdout --in-sample-expectancy 0.14

Nudges re-run levels -> touches -> flow -> sim in memory with one parameter moved one notch.
A nudged touch whose trade window was never downloaded is skipped; coverage is reported.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from src import analysis, flow, gex, levels, stage3, stats, store, touches
from src import calendar as calm
from src.config import load_config, param, with_params

LEVEL = {"round_step", "level_window", "merge_tol"}
TOUCH = {"approach_a", "proximity_b", "approach_lookback", "debounce", "success_R", "fail_F", "label_horizon"}
BASELINE = {"abs_window", "baseline_sessions"}
FLOW_SIM = {"abs_threshold", "reclaim_window", "reclaim_ticks", "entry_slippage", "stop_buffer", "max_risk",
            "target_mult", "time_exit", "cost_rt_usd"}
GEX_PCT = {"gex_pct_lookback"}
GEX_FULL = {"max_rel_spread"}   # needs a full GEX rebuild; opt in with --with-gex-rebuild


def _confirmed_expectancy(T: pd.DataFrame, carry) -> tuple[float, int]:
    if T.empty or "pnl_r" not in T:
        return np.nan, 0
    c = T[(T["mode"] == "confirmed") & T["pnl_r"].notna() & T["group"].isin(carry)]
    return (float(c["pnl_r"].mean()) if len(c) else np.nan), len(c)


def nudges(cfg=None, carry=None, with_gex_rebuild=False) -> pd.DataFrame:
    cfg = cfg or load_config()
    carry = carry or ["gamma_only", "both", "structural_only"]
    bars, cal = store.load_bars(cfg), store.load_calendar(cfg)
    g0 = store.load_derived("gex_daily", cfg)
    lv0, tc0 = store.load_derived("levels", cfg), store.load_derived("touches", cfg)
    base0 = flow.baseline_table(bars, cal, cfg)
    _, T0 = stage3.run(cfg, touches=tc0, baseline=base0, save=False)
    e0, n0 = _confirmed_expectancy(T0, carry)
    rows = [{"param": "BASE", "value": None, "expectancy_r": e0, "n_confirmed": n0, "touch_coverage": 1.0}]
    for name, spec in cfg["params"].items():
        for v in spec.get("nudges", []) or []:
            if name in GEX_FULL and not with_gex_rebuild:
                rows.append({"param": name, "value": v, "expectancy_r": np.nan, "note": "skipped (needs GEX rebuild)"})
                continue
            c = with_params(cfg, **{name: v})
            g = g0
            if name in GEX_PCT:
                g = g0.sort_values("date").copy()
                g["gex_pct"] = gex.pct_rank_prev(g["net_gex"], v, min(v, param(c, "gex_pct_min_periods")))
            elif name in GEX_FULL:
                g = gex.build(cfg=c, save=False)
            lv = lv0 if name not in (LEVEL | GEX_PCT | GEX_FULL) else levels.build(cfg=c, save=False, bars=bars, cal=cal, gex=g)
            tc = tc0 if name not in (LEVEL | TOUCH | GEX_PCT | GEX_FULL) else touches.build(
                cfg=c, levels=lv, save=False, bars=bars, cal=cal)
            base = base0 if name not in BASELINE else flow.baseline_table(bars, cal, c)
            F, T = stage3.run(c, touches=tc, baseline=base, save=False)
            e, n = _confirmed_expectancy(T, carry)
            rows.append({"param": name, "value": v, "expectancy_r": e, "n_confirmed": n,
                         "touch_coverage": len(F) / max(1, len(tc))})
    df = pd.DataFrame(rows)
    tested = df[(df["param"] != "BASE") & df["expectancy_r"].notna()]
    share = float((tested["expectancy_r"] > 0).mean()) if len(tested) else np.nan
    df.attrs["positive_share"] = share
    df.attrs["pass"] = bool(share >= cfg["gates"]["robustness_min_positive_share"]) if len(tested) else False
    return df


def splits(cfg=None, carry=None) -> dict:
    cfg = cfg or load_config()
    carry = carry or ["gamma_only", "both", "structural_only"]
    T = store.load_derived("sim_trades", cfg)
    if T.empty or "pnl_r" not in T:
        return {"n": 0}
    c = T[(T["mode"] == "confirmed") & T["pnl_r"].notna() & T["group"].isin(carry)].copy()
    if len(c) < 3:
        return {"n": len(c)}
    c["year"] = pd.to_datetime(c["date"]).dt.year
    c["gex_tercile"] = pd.qcut(c["gex_pct"], 3, labels=["low", "mid", "high"])
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    out = {}
    for by in ("year", "gex_tercile", "tod"):
        t = stats.bootstrap_group_means(c, "pnl_r", by, draws, seed, lvl)
        t["share_of_total_r"] = c.groupby(by, observed=True)["pnl_r"].sum().values / c["pnl_r"].sum() if len(c) else np.nan
        out[by] = t.to_dict("records")
    return out


def holdout_prep() -> dict:
    """Final frozen run, step 1: GEX, levels and touches on holdout dates. Then price and pull the
    holdout trade windows (python -m src.ingest_futures trades --holdout ...) before step 2."""
    if not calm.holdout_unsealed():
        raise calm.HoldoutSealed(f"Set {calm.HOLDOUT_ENV}=1 only when the user says 'run the holdout'.")
    cfg = load_config()
    gex.build(start=calm.holdout_start(cfg), cfg=cfg, include_holdout=True)
    lv = levels.build(cfg=cfg, include_holdout=True)
    tc = touches.build(cfg=cfg, include_holdout=True)
    return {"levels": len(lv), "touches": len(tc)}


def holdout(in_sample_expectancy: float, carry=None) -> dict:
    """Final frozen run, step 2: flow + sim on holdout touches, scored against the holdout rule."""
    if not calm.holdout_unsealed():
        raise calm.HoldoutSealed(f"Set {calm.HOLDOUT_ENV}=1 only when the user says 'run the holdout'.")
    cfg = load_config()
    stage3.run(cfg, include_holdout=True)
    return analysis.holdout(in_sample_expectancy, cfg, carry=carry)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", choices=["nudges", "splits", "holdout-prep", "holdout"])
    ap.add_argument("--carry")
    ap.add_argument("--with-gex-rebuild", action="store_true")
    ap.add_argument("--in-sample-expectancy", type=float)
    a = ap.parse_args(argv)
    carry = a.carry.split(",") if a.carry else None
    if a.job == "nudges":
        df = nudges(carry=carry, with_gex_rebuild=a.with_gex_rebuild)
        print(df.to_string())
        print(f"positive share {df.attrs['positive_share']:.2f}  pass={df.attrs['pass']}")
    elif a.job == "splits":
        print(analysis.to_json(splits(carry=carry)))
    elif a.job == "holdout-prep":
        print(holdout_prep())
    else:
        print(analysis.to_json(holdout(a.in_sample_expectancy, carry)))


if __name__ == "__main__":
    main()
