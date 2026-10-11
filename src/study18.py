"""Study 18: Study 17's forecast-selected, perp-hedged liquidity on Uniswap v3 Arbitrum and Base (STUDY18.md; $0).

Four variants fixed in advance (s18_variants), all full range, perp-hedged, hold 4 weeks; no search. A variant passes
Stage A (discovery, to s18_discovery_end) only if, after costs, the weekly mean and its 90% lower bound are > 0 both
WITH and WITHOUT funding, selection beats random pool-weeks of the same weeks (placebo p on gross), both halves and
the mean without the best 5% of weeks are > 0 WITHOUT funding, and it is active >= the minimum weeks. Validation
(2025-01..09) runs once, only for Stage A passes. The simulation is Study 17's (src/study17.py), reused unchanged.

  python -m src.study18 --build          # pool-week table from the cached L2 subgraph days + Binance marks
  python -m src.study18 --stage-a        # discovery: the four variants against the rules
  python -m src.study18 --validate       # ONCE, only variants that passed Stage A
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from src import study17 as s17
from src.config import data_path, load_config, param

log = logging.getLogger("study18")
DAY_S = 86_400


# ---- pure pieces (tested) --------------------------------------------------------------------------------------
def age_weeks(t0, first_day) -> np.ndarray:
    return (np.asarray(t0, "int64") - int(first_day)) // 7


def passes(r: dict, p_max: float, min_weeks: int) -> bool:
    w, n = r["with_funding"], r["without_funding"]
    pos = lambda x: x is not None and np.isfinite(x) and x > 0
    return bool(pos(w.get("mean")) and pos(w.get("lo")) and pos(n.get("mean")) and pos(n.get("lo"))
                and pos(n.get("first_half")) and pos(n.get("second_half")) and pos(n.get("without_best_tail"))
                and r["placebo_p"] <= p_max and r["active_weeks"] >= min_weeks)


def evaluate(T: pd.DataFrame, v: dict, har, gas, capital, swap, perp_fee, reps, seed, draws, level, tail) -> dict:
    """One variant on one period's pool-weeks: net with and without funding, placebo p, by chain, components."""
    if v.get("new_pool_weeks"):
        T = T[T["age_w"] < v["new_pool_weeks"]]
    T = T.reset_index(drop=True)
    if T.empty:
        return {"active_weeks": 0, "with_funding": {}, "without_funding": {}, "placebo_p": 1.0}
    c = {"margin": v["margin"], "forecast": v["forecast"], "hold": 4, "tvl_floor": v["tvl_floor"], "hedge": "perp"}
    cfg = {"params": {"s17_gas_usd": {"value": gas}, "s17_capital_main": {"value": capital},
                      "s17_swap_cost": {"value": swap}, "s17_perp_fee": {"value": perp_fee}}}
    fc = s17.forecast(T, v["forecast"], har) if har or v["forecast"] == "trailing" else None
    act, gross, net, uni = s17.cell_net(cfg, T, c, fc)
    weeks = np.arange(int(T["wk"].min()), int(T["wk"].max()) + 1)
    fund = T["funding_k1"].to_numpy(float)
    ws = lambda x: s17.weekly_series(T, act, np.asarray(x, float), weeks)
    st = lambda x: s17.series_stats(ws(x), draws, seed, level, tail)
    rng = np.random.default_rng(seed)
    pl = s17._placebo(T, act, uni, gross, weeks, reps, rng)
    p = (1 + int((pl.mean(1) >= ws(gross).mean()).sum())) / (1 + reps)
    counts = pd.Series(act).groupby(T["wk"].to_numpy()).sum()
    out = {"active_weeks": int((counts > 0).sum()), "positions": int(act.sum()),
           "positions_per_week": float(counts[counts > 0].mean()) if (counts > 0).any() else 0.0,
           "with_funding": st(net), "without_funding": st(net - fund), "placebo_p": p,
           "gross_mean": float(ws(gross).mean()), "placebo_gross_mean": float(pl.mean()),
           "components": {k: float(ws(T[col]).mean()) for k, col in
                          (("fees", "fees_k1"), ("funding", "funding_k1"), ("perp_rebalance", "reb_k1"))},
           "hedged_impermanent_loss": float(ws(T["value_k1"] + T["hedge_px_k1"]).mean()),
           "transaction_costs": float(ws(net - gross).mean()),
           "market_path_without_funding": float(ws(net - gross + T["mkt_g_perp_k1"].to_numpy(float) - fund).mean())}
    if "chain" in T:
        out["by_chain_without_funding"] = {
            ch: float(s17.weekly_series(T, act & (T["chain"] == ch).to_numpy(), net - fund, weeks).mean())
            for ch in sorted(T["chain"].unique())}
    if "week" in T:
        yr = T["week"].dt.year.to_numpy()
        out["by_year_without_funding_per_position"] = {str(y): float((net - fund)[act & (yr == y)].mean())
                                                       for y in sorted(set(yr[act]))}
    return out


# ---- build --------------------------------------------------------------------------------------------------------
def build(cfg) -> dict:
    from src import crypto_data as cd
    from src import defi_subgraph as sg
    stables = {s.upper() for s in param(cfg, "s17_stables")}
    tmap, avail = param(cfg, "s17_token_map"), s17._available_perps(cfg)
    ks, cap, pf = param(cfg, "s17_concentration"), param(cfg, "s18_capital_main"), param(cfg, "s17_perp_fee")
    md = tuple(param(cfg, "s17_min_days"))
    gap, conc = param(cfg, "s17_max_gap"), (param(cfg, "s17_conc_min"), param(cfg, "s17_conc_max"))
    holdout_day = pd.Timestamp(param(cfg, "s14_holdout_start"), tz="UTC").value // (DAY_S * 10**9)
    marks: dict = {}

    def mark(sym):
        if sym.upper() in stables:
            return "stable"
        ps = s17.perp_symbol(sym, avail, tmap)
        if ps is None:
            return None
        if ps[0] not in marks:
            c = s17.daily_close(cd.load_bars(cfg, ps[0], "1h")) / ps[1]
            marks[ps[0]] = (c, s17.daily_funding(cd.load_funding(cfg, ps[0]))) if len(c) else None
        return marks[ps[0]]

    root = data_path(cfg, "raw", "defi_subgraph")
    counts, rows = {}, []
    for chain in param(cfg, "s18_chains"):
        n = {"pools": 0, "stable_pair": 0, "no_perp": 0, "no_history": 0, "used": 0, "errors": 0}
        idf = root / chain / "subgraph_id.txt"
        if not idf.exists():
            raise SystemExit(f"{chain}: no subgraph id saved; run python -m src.defi_subgraph --chain {chain} --probe")
        g = sg.Graph("", idf.read_text().strip(), root / chain / "v3", 0, offline=True)
        for p in g.q("pools_0", sg.V3_POOLS, {"skip": 0})["pools"]:
            n["pools"] += 1
            s0, s1 = p["token0"]["symbol"], p["token1"]["symbol"]
            if s0.upper() in stables and s1.upper() in stables:
                n["stable_pair"] += 1
                continue
            m0, m1 = mark(s0), mark(s1)
            if m0 is None or m1 is None:
                n["no_perp"] += 1
                continue
            try:
                days = pd.DataFrame(g.paged(f"days_{p['id']}", sg.V3_DAYS, "poolDayDatas", {"pool": p["id"]}, "date"))
            except RuntimeError:
                n["no_history"] += 1
                continue
            if len(days) < 40:
                n["no_history"] += 1
                continue
            try:
                d = s17.v3_daily(days, p["token0"]["decimals"], p["token1"]["decimals"])
                first_day = int(d.index.min())
                d = d[d.index < holdout_day]
                st = lambda m: (None, None) if m == "stable" else m
                (c0, f0), (c1, f1) = st(m0), st(m1)
                d = s17.attach_market(d, c0, c1, f0, f1, gap, conc, True)
                W = s17.weekly_rows(d, m0 != "stable", m1 != "stable", True, ks, cap, pf,
                                    param(cfg, "s17_fee_days"), param(cfg, "s17_trailing_days"), md)
            except Exception as e:
                n["errors"] += 1
                log.warning("%s %s: %s", chain, p["id"], str(e)[:200])
                continue
            if len(W):
                W["pool"], W["chain"], W["symbol"], W["v3"] = f"{chain}:{p['id']}", chain, f"{s0}-{s1}", True
                W["project"] = f"{chain} v3 ({int(p['feeTier']) / 1e4:.2f}%)"
                W["age_w"] = age_weeks(W["t0"].to_numpy(), first_day)
                rows.append(W)
                n["used"] += 1
        counts[chain] = n
        log.info("%s: %s", chain, n)
    T = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    out = data_path(cfg, "derived", "defi")
    out.mkdir(parents=True, exist_ok=True)
    if len(T):
        T = T[T["t0"] + 7 <= holdout_day]
        T["week"] = pd.to_datetime(T["t0"] * DAY_S, unit="s")
        T["wk"] = (T["t0"] - 4) // 7
        T.to_parquet(out / "study18_pool_weeks.parquet")
    res = {"study": 18, "counts": counts, "pool_weeks": int(len(T)),
           "pools": int(T["pool"].nunique()) if len(T) else 0,
           "first_week": str(T["week"].min().date()) if len(T) else None,
           "last_week": str(T["week"].max().date()) if len(T) else None,
           "by_chain_and_year": (T.groupby(["chain", T["week"].dt.year]).size().rename("pool_weeks")
                                 .reset_index().to_dict("records")) if len(T) else []}
    (out / "study18_build.json").write_text(json.dumps(res, indent=1, default=str))
    return res


# ---- stages ---------------------------------------------------------------------------------------------------------
def _common(cfg):
    g = cfg["gates"]
    return dict(gas=param(cfg, "s18_gas_usd"), capital=param(cfg, "s18_capital_main"),
                swap=param(cfg, "s17_swap_cost"), perp_fee=param(cfg, "s17_perp_fee"),
                reps=param(cfg, "s17_placebo_reps"), seed=param(cfg, "s18_seed"),
                draws=param(cfg, "bootstrap_draws"), level=param(cfg, "ci_level"), tail=g["study18_tail_share"])


def stage_a(cfg) -> dict:
    d = data_path(cfg, "derived", "defi")
    T = pd.read_parquet(d / "study18_pool_weeks.parquet")
    D = T[T["week"] <= pd.Timestamp(param(cfg, "s18_discovery_end"))].reset_index(drop=True)
    har = s17.fit_har(D)
    g = cfg["gates"]
    out = {"study": 18, "stage": "A (discovery)", "weeks": int(D["wk"].nunique()), "pool_weeks": int(len(D)),
           "first_week": str(D["week"].min().date()), "last_week": str(D["week"].max().date()), "har": har,
           "variants": {}}
    for v in param(cfg, "s18_variants"):
        r = evaluate(D, v, har, **_common(cfg))
        r["pass"] = passes(r, g["study18_p"], g["study18_min_active_weeks"]) if r["with_funding"] else False
        conc = {}
        vv = dict(v)
        for k in param(cfg, "s17_concentration"):
            if k > 1:
                c = {"margin": v["margin"], "forecast": v["forecast"], "hold": 4, "tvl_floor": v["tvl_floor"],
                     "hedge": "perp"}
                DD = D[D["age_w"] < v["new_pool_weeks"]].reset_index(drop=True) if vv.get("new_pool_weeks") else D
                cf = {"params": {"s17_gas_usd": {"value": param(cfg, "s18_gas_usd")},
                                 "s17_capital_main": {"value": param(cfg, "s18_capital_main")},
                                 "s17_swap_cost": {"value": param(cfg, "s17_swap_cost")},
                                 "s17_perp_fee": {"value": param(cfg, "s17_perp_fee")}}}
                a, _, n, _ = s17.cell_net(cf, DD, c, s17.forecast(DD, v["forecast"], har), k=k)
                wk = np.arange(int(DD["wk"].min()), int(DD["wk"].max()) + 1)
                conc[str(k)] = float(s17.weekly_series(DD, a, n, wk).mean())
        r["concentration_with_funding"] = conc
        out["variants"][v["name"]] = r
    (d / "study18_stage_a.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def validate(cfg) -> dict:
    d = data_path(cfg, "derived", "defi")
    marker = d / "study18_validation_done.json"
    if marker.exists():
        raise SystemExit("Study 18 validation already run once; it is not rerun")
    A = json.loads((d / "study18_stage_a.json").read_text())
    passing = [n for n, r in A["variants"].items() if r["pass"]]
    if not passing:
        raise SystemExit("no variant passed Stage A: nothing to validate")
    T = pd.read_parquet(d / "study18_pool_weeks.parquet")
    V = T[T["week"] > pd.Timestamp(param(cfg, "s18_discovery_end"))].reset_index(drop=True)
    g = cfg["gates"]
    out = {"study": 18, "stage": "validation", "weeks": int(V["wk"].nunique()),
           "first_week": str(V["week"].min().date()), "last_week": str(V["week"].max().date()), "variants": {}}
    for v in param(cfg, "s18_variants"):
        if v["name"] not in passing:
            continue
        r = evaluate(V, v, A["har"], **{**_common(cfg), "seed": param(cfg, "s18_seed") + 1})
        r["pass"] = passes(r, g["study18_p"], g["study18_min_active_weeks_validation"]) if r["with_funding"] else False
        out["variants"][v["name"]] = r
    marker.write_text(json.dumps({"ran": pd.Timestamp.now(tz="UTC").isoformat()}))
    (d / "study18_validation.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", action="store_true")
    g.add_argument("--stage-a", action="store_true")
    g.add_argument("--validate", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    r = build(cfg) if a.build else stage_a(cfg) if a.stage_a else validate(cfg)
    from src.analysis import to_json
    print(to_json(r))


if __name__ == "__main__":
    main()
