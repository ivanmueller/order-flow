"""Hypothetical bankroll of the Study 5f fade (Matteo 2026-10-08). Descriptive only: never a gate decision.

Reads the saved Study 5 table of the active config (ES: data/, NQ: data_nq/ via config.nq.yaml), in sample
only, and takes the 5f trade every session: the S5b stop trade in the direction opposite to the rest-of-day
move (Study 5f rules, RUNLOG 2026-10-07). Per contract, from the table's own fills:
  gross_pts = pnl_pts (net of cost/pv) + cost/pv        (entry and exit ticks stay in)
  risk_pts  = |E - S| + tick                              (stop distance plus the tick the stop fills through)
Schemes (config.yaml bankroll):
  full_fixed   full_contracts full-size contracts:  usd = n (gross_pts x pv - cost_rt_usd)
  micro_fixed  one micro:                           usd = gross_pts x micro_pv - micro_cost
  micro_risk   whole micros = floor(risk_pct x equity / (risk_pts x micro_pv + micro_cost)), compounding;
               0 contracts -> no trade that session
Stress: the registered entry_slippage nudge (2 ticks) on entry and exit, i.e. (nudge - value) x 2 ticks more
per contract. Outputs: summary per scheme and the equity path per scheme (saved as bankroll_<scheme>).

  $env:GAMMA_EDGE_CONFIG = "config.nq.yaml"; python -m src.bankroll
"""
from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src import stats, store
from src.config import load_config

log = logging.getLogger("bankroll")
MODE = "momentum_stop"


def fade_legs(T: pd.DataFrame, cfg) -> pd.DataFrame:
    m = cfg["market"]
    cost_pts = cfg["params"]["cost_rt_usd"]["value"] / m["point_value"]
    T = T.sort_values("date").reset_index(drop=True)
    side = -T["d"].to_numpy(int)
    pick = lambda col: np.where(side == 1, T[f"{col}_long"].to_numpy(), T[f"{col}_short"].to_numpy())  # noqa: E731
    E, S = pick("E").astype(float), pick("S").astype(float)
    return pd.DataFrame({"date": T["date"].to_numpy(), "side": side,
                         "gross_pts": pick(f"pnl_pts_{MODE}").astype(float) + cost_pts,
                         "risk_pts": np.abs(E - S) + m["tick"],
                         "exit": pick(f"exit_{MODE}"), "price": E})


@dataclass
class Result:
    path: pd.DataFrame
    summary: dict


def simulate(L: pd.DataFrame, cfg, scheme: str, risk_pct: float | None = None, stress: bool = False) -> Result:
    b, m, p = cfg["bankroll"], cfg["market"], cfg["params"]
    extra_pts = 0.0
    if stress:
        es = p["entry_slippage"]
        extra_pts = 2 * (es["nudges"][0] - es["value"]) * m["tick"]
    eq = start = float(b["start_usd"])
    rows, skipped = [], 0
    for r in L.itertuples(index=False):
        g = r.gross_pts - extra_pts
        if scheme == "full_fixed":
            n, pv, cost = int(b["full_contracts"]), m["point_value"], p["cost_rt_usd"]["value"]
        elif scheme == "micro_fixed":
            n, pv, cost = 1, b["micro_point_value"], b["micro_cost_rt_usd"]
        elif scheme == "micro_risk":
            pv, cost = b["micro_point_value"], b["micro_cost_rt_usd"]
            per = r.risk_pts * pv + cost
            n = max(0, math.floor(risk_pct * eq / per + 1e-9)) if eq > 0 else 0
        else:
            raise ValueError(scheme)
        if n == 0:
            skipped += 1
        usd = n * (g * pv - cost) if n else 0.0
        eq_before = eq
        eq += usd
        rows.append({"date": r.date, "contracts": n, "pnl_usd": usd, "equity": eq,
                     "ret": usd / eq_before if eq_before > 0 else np.nan,
                     "notional_x": n * pv * r.price / eq_before if eq_before > 0 else np.nan, "exit": r.exit})
        if eq <= 0:
            break
    P = pd.DataFrame(rows)
    return Result(P, _summary(P, start, skipped))


def _summary(P: pd.DataFrame, start: float, skipped: int) -> dict:
    eq = np.concatenate([[start], P["equity"].to_numpy(float)])
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    i = int(dd.argmax())
    traded = P[P["contracts"] > 0]
    pnl = traded["pnl_usd"].to_numpy(float)
    years = max(1e-9, (pd.Timestamp(P["date"].iloc[-1]) - pd.Timestamp(P["date"].iloc[0])).days / 365.25)
    final = float(eq[-1])
    month = P.assign(m=pd.to_datetime(P["date"]).dt.to_period("M")).groupby("m")["pnl_usd"].sum()
    r = P["ret"].dropna().to_numpy(float)
    return {
        "sessions": int(len(P)), "trades": int(len(traded)), "skipped_too_small": skipped,
        "start_usd": start, "final_usd": final, "total_return_pct": 100 * (final / start - 1),
        "cagr_pct": 100 * ((final / start) ** (1 / years) - 1) if final > 0 else -100.0,
        "max_dd_usd": float(dd.max()), "max_dd_pct": float(100 * dd[i] / peak[i]) if peak[i] > 0 else 0.0,
        "ruined": bool(final <= 0),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else np.nan,
        "avg_win_usd": float(pnl[pnl > 0].mean()) if (pnl > 0).any() else 0.0,
        "avg_loss_usd": float(pnl[pnl <= 0].mean()) if (pnl <= 0).any() else 0.0,
        "best_day_usd": float(pnl.max()) if len(pnl) else 0.0, "worst_day_usd": float(pnl.min()) if len(pnl) else 0.0,
        "longest_losing_streak": stats.longest_losing_streak(pnl),
        "months": int(len(month)), "positive_months_pct": float(100 * (month > 0).mean()),
        "worst_month_usd": float(month.min()), "best_month_usd": float(month.max()),
        "sharpe_annual": float(r.mean() / r.std(ddof=1) * math.sqrt(252)) if len(r) > 2 and r.std(ddof=1) > 0 else np.nan,
        "max_contracts": int(P["contracts"].max()), "max_notional_x_equity": float(P["notional_x"].max()),
        "stopped_share": float((traded["exit"] == "stop").mean()) if len(traded) else np.nan,
    }


def run(cfg=None, save: bool = True) -> dict:
    cfg = cfg or load_config()
    T = store.load_derived("close_momentum_trades", cfg)          # in sample (sealed loader)
    L = fade_legs(T, cfg)
    schemes = [("full_fixed", None), ("micro_fixed", None)] + [("micro_risk", x) for x in cfg["bankroll"]["risk_pcts"]]
    out = {"market": cfg["data"]["es_symbol"], "first": str(L["date"].min()), "last": str(L["date"].max()),
           "note": "hypothetical, in sample only, descriptive; not a gate decision and not out-of-sample evidence",
           "assumptions": {"start_usd": cfg["bankroll"]["start_usd"], "cost_rt_usd": cfg["params"]["cost_rt_usd"]["value"],
                           "micro_cost_rt_usd": cfg["bankroll"]["micro_cost_rt_usd"],
                           "fills": "entry and time exit one tick adverse, stops one tick through (SPEC rule 5)"},
           "schemes": {}}
    for scheme, rp in schemes:
        name = scheme if rp is None else f"{scheme}_{int(round(rp * 100))}pct"
        base = simulate(L, cfg, scheme, rp)
        stressed = simulate(L, cfg, scheme, rp, stress=True)
        out["schemes"][name] = {**base.summary, "stress_extra_tick_each_side": {
            k: stressed.summary[k] for k in ("final_usd", "total_return_pct", "max_dd_usd", "max_dd_pct", "ruined")}}
        if save:
            store.save_derived(base.path, f"bankroll_{name}", cfg)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from src.analysis import to_json
    print(to_json(run()))


if __name__ == "__main__":
    main()
