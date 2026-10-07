"""Study 7 (RUNLOG 2026-10-07, approved): Study 5 momentum on four non-equity markets, anchored to settlement.

Rules are Study 5's S5a (no stop) with each overlay's clock (config.cl/gc/zn/6e.yaml): P_prev = D-1's bar
closing at the settlement minute (market.rth_close), decision on the bar closing 30 minutes earlier, entry at
the next bar's open + 1 tick, time exit at the settlement bar's open - 1 tick, $3.98 a round trip.
Unit EM_R = s5_rv_factor x sigma_D x P_prev, sigma_D = SD (ddof 1) of the last s5_rv_sessions daily log
returns of the settlement-minute closes dated strictly before D; returns across a contract change are not
returns and are skipped (the continuous symbol is not back-adjusted).
Gates: Study 5's per-variant rules applied to S5a in each market (4 gated variants). Reported, not gated:
the fade mirror, the S5b stop variant, years, the cross-market pooled series and correlations, and the
ES bridge (5f fade and S5a on ES under EM_R).

  $env:GAMMA_EDGE_CONFIG = "config.cl.yaml"; python -m src.study7 --market     # per market (saves its table)
  python -m src.study7 --cross config.cl.yaml config.gc.yaml config.zn.yaml config.6e.yaml   # no overlay set
  python -m src.study7 --bridge                                                # ES, no overlay set
"""
from __future__ import annotations

import argparse
import logging
import os

import numpy as np
import pandas as pd

from src import stats, store
from src import study5
from src.config import load_config, param, with_params
from src.levels import session_bars

log = logging.getLogger("study7")
GATED = "S5a_momentum"


def realized_sigma(dates, closes, insts, n: int) -> dict:
    """date -> SD of the last n log returns dated strictly before it (returns need two finite closes on the
    same contract on consecutive calendar rows)."""
    dates, c, k = list(dates), np.asarray(closes, float), np.asarray(insts)
    ret_dates, rets = [], []
    for j in range(1, len(c)):
        if np.isfinite(c[j]) and np.isfinite(c[j - 1]) and c[j] > 0 and c[j - 1] > 0 and k[j] == k[j - 1]:
            ret_dates.append(dates[j])
            rets.append(np.log(c[j] / c[j - 1]))
    out, r = {}, np.asarray(rets)
    pos = 0
    for d in dates:
        while pos < len(ret_dates) and ret_dates[pos] < d:
            pos += 1
        if pos >= n:
            out[d] = float(r[pos - n:pos].std(ddof=1))
    return out


def sigma_by_session(cal: pd.DataFrame, by_day: dict, cfg) -> dict:
    """Settlement-minute close of every calendar session on its own contract, then realized_sigma."""
    cal = cal.sort_values("date")
    closes, insts = [], []
    for r in cal.itertuples():
        c, i = study5.prev_close(session_bars(by_day, r.date, r.instrument_id), r.date, cfg)
        closes.append(c)
        insts.append(i)
    return realized_sigma(cal["date"], closes, insts, param(cfg, "s5_rv_sessions"))


def market_verdict(variants: dict) -> str:
    return variants[GATED]["verdict_vs_rules"]


def momentum_market(cfg=None) -> dict:
    cfg = cfg or load_config()
    if param(cfg, "s5_em_unit") != "realized":
        raise RuntimeError("study 7 runs under a market overlay (config.cl/gc/zn/6e.yaml), not the ES config")
    T = study5.run(cfg)
    rep = study5.report(cfg, T)
    f = study5.variant_frame(T, "momentum")
    fade = study5.fade(f)
    out = {"market": cfg["data"]["es_symbol"], "first": rep["first"], "last": rep["last"],
           "gated_variant": GATED, "verdict_vs_rules": market_verdict(rep["variants"]),
           GATED: rep["variants"][GATED],
           "not_gated": {"S5b_momentum_stop": {k: rep["variants"]["S5b_momentum_stop"][k]
                                               for k in ("n", "mean_em", "ci_lo", "ci_hi", "verdict_vs_rules")},
                         "fade_mirror_mean_em": float(fade["pnl_em"].mean()),
                         "slope_r_l30_on_r_rod": rep["descriptive"]["slope_r_l30_on_r_rod"],
                         "by_year": rep["descriptive"]["by_year"],
                         "friction": rep["descriptive"]["friction"],
                         "drift": rep["descriptive"]["drift"],
                         "sanity": rep["descriptive"]["sanity"]},
           "skipped": T.attrs.get("skipped", {})}
    return out


def pooled_daily(frames: dict) -> pd.DataFrame:
    """Equal weight across the markets trading each date (EM units)."""
    allf = pd.concat([f.assign(market=m)[["date", "market", "pnl_em"]] for m, f in frames.items()])
    g = allf.groupby("date")["pnl_em"]
    return pd.DataFrame({"date": g.mean().index, "pnl_em": g.mean().to_numpy(), "n_markets": g.size().to_numpy()})


def cross_report(paths: list[str]) -> dict:
    if os.environ.get("GAMMA_EDGE_CONFIG"):
        raise RuntimeError("unset GAMMA_EDGE_CONFIG first: --cross loads each overlay by path")
    frames = {}
    for p in paths:
        c = load_config(p)
        T = store.load_derived("close_momentum_trades", c)
        frames[c["data"]["es_symbol"]] = study5.variant_frame(T, "momentum")[["date", "pnl_em"]]
    P = pooled_daily(frames)
    base = load_config()
    draws, seed, lvl = param(base, "bootstrap_draws"), param(base, "bootstrap_seed"), param(base, "ci_level")
    wide = pd.concat({m: f.set_index("date")["pnl_em"] for m, f in frames.items()}, axis=1)
    return {"note": "descriptive, not gated (RUNLOG Study 7)",
            "per_market_mean_em": {m: float(f["pnl_em"].mean()) for m, f in frames.items()},
            "pooled": stats.day_bootstrap_mean(P, "pnl_em", draws, seed, lvl),
            "pooled_fade_mirror_mean_em": float(-P["pnl_em"].mean()),
            "daily_corr": wide.corr().round(3).to_dict()}


def bridge(cfg=None) -> dict:
    """ES under EM_R (descriptive): does the unit change alone move the ES results?"""
    cfg = cfg or load_config()
    if cfg["data"]["es_symbol"] != "ES.v.0":
        raise RuntimeError("--bridge runs on the ES config (unset GAMMA_EDGE_CONFIG)")
    c = with_params(cfg, s5_em_unit="realized")
    T = study5.run(c, save=False)
    f = study5.variant_frame(T, "momentum")
    fr = study5.fade_report(c, T)
    return {"note": "descriptive; ES tables are not overwritten", "n": int(len(T)),
            "S5a_mean_em_r": float(f["pnl_em"].mean()),
            "fade_5f_mean_em_r": fr["mean_em"], "fade_5f_ci": [fr["ci_lo"], fr["ci_hi"]],
            "fade_5f_timing_contrast": fr["timing_contrast"],
            "median_em_r_over_price": float((T["em_v"] / T["P_prev"]).median())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", action="store_true", help="run S5a on the active overlay's market")
    ap.add_argument("--cross", nargs="+", metavar="OVERLAY", help="pooled report from saved per-market tables")
    ap.add_argument("--bridge", action="store_true", help="ES under EM_R (descriptive)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from src.analysis import to_json
    if a.market:
        print(to_json(momentum_market()))
    elif a.cross:
        print(to_json(cross_report(a.cross)))
    elif a.bridge:
        print(to_json(bridge()))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
