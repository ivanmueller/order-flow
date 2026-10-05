"""Stage 3 runner: order flow features + confirmed and naive trades for every Stage 2 touch.

Needs trades downloaded first: python -m src.ingest_futures trades --price-only / --approve-usd X
Run: python -m src.stage3
Outputs: data/derived/features.parquet, data/derived/sim_trades.parquet
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import flow, sim, store
from src.config import load_config
from src.ingest_futures import load_trades

log = logging.getLogger("stage3")


def run(cfg=None, include_holdout: bool = False, touches: pd.DataFrame | None = None,
        baseline: pd.DataFrame | None = None, save: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = cfg or load_config()
    sfx = "_holdout" if include_holdout else ""
    if touches is None:
        touches = store.load_derived("touches" + sfx, cfg, include_holdout)
    if baseline is None:
        cal = store.load_calendar(cfg, include_holdout)
        baseline = flow.baseline_table(store.load_bars(cfg, include_holdout), cal, cfg)
    base = baseline.set_index(["date", "slot"])["baseline"]
    pre = pd.Timedelta(minutes=cfg["data"]["trades_pre_min"])
    post = pd.Timedelta(minutes=cfg["data"]["trades_post_min"])
    feats, trades_out, missing = [], [], 0
    for tc in touches.itertuples():
        t0 = pd.Timestamp(tc.t0)
        tr = load_trades(cfg, tc.date, t0 - pre, t0 + post)
        if tr is None or tr.empty:
            missing += 1
            continue
        slot = int(calm.minutes_of_day_et(pd.Series([t0])).iloc[0] // 30)
        bl = base.get((tc.date, slot), np.nan)
        f = flow.features(tr, t0, tc.level_es, tc.d, bl, cfg)
        feats.append({"touch_id": tc.touch_id, **f})
        common = {"touch_id": tc.touch_id, "date": tc.date, "group": tc.group, "d": tc.d,
                  "level_es": tc.level_es, "em": tc.em, "gex_pct": tc.gex_pct, "tod": tc.tod}
        c = sim.confirmed_trade(tr, f, tc.level_es, tc.d, tc.em, tc.date, cfg)
        if c is not None:
            trades_out.append({**common, "mode": "confirmed", **c})
        if f.get("has_t0"):
            n = sim.naive_trade(tr, f["t0_trade"], tc.level_es, tc.d, tc.em, tc.date, cfg)
            trades_out.append({**common, "mode": "naive", **n})
    if missing:
        log.warning("%d of %d touches have no downloaded trades", missing, len(touches))
    F = pd.DataFrame(feats)
    T = pd.DataFrame(trades_out)
    if not F.empty:
        F = touches.merge(F, on="touch_id", how="inner")
    if save:
        store.save_derived(F, "features" + sfx, cfg)
        store.save_derived(T, "sim_trades" + sfx, cfg)
    return F, T


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    F, T = run()
    if T.empty:
        print("no trades simulated")
        return
    done = T[T["pnl_r"].notna()]
    print(done.groupby(["group", "mode"])["pnl_r"].agg(["size", "mean"]))


if __name__ == "__main__":
    main()
