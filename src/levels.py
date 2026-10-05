"""Module 3: level builder (SPEC.md, "Module 3") plus Stage 2 placebo levels.

Per session D, every candidate ES level carries every reason it exists:
  gamma_flip, gamma_call_wall, gamma_put_wall, gamma_top   (gex_daily, SPX + basis)
  pd_high, pd_low     prior RTH session high/low (dropped on roll days: different contract)
  on_high, on_low     overnight 18:00 ET D-1 .. 09:30 ET D
  round               SPX multiples of round_step within 1 EM of S0, converted to ES
Rules: SPX -> ES by adding B_D and rounding to 0.25; keep within level_window EM of the 09:30 ES
price; merge levels within merge_tol ticks at their mean; flag is_gamma / is_structural.

Run: python -m src.levels [--start ...] [--end ...]
"""
from __future__ import annotations

import argparse
import logging
import zlib

import numpy as np
import pandas as pd

from src import calendar as calm
from src import store
from src.config import load_config, param

log = logging.getLogger("levels")

GAMMA_TAGS = ["gamma_flip", "gamma_call_wall", "gamma_put_wall", "gamma_top"]
STRUCT_TAGS = ["pd_high", "pd_low", "on_high", "on_low", "round"]


def round_tick(x, tick=0.25):
    return np.round(np.asarray(x, float) / tick) * tick


def session_bars(bars_by_day: dict, day, instrument_id) -> pd.DataFrame:
    """Bars of session `day` on its front contract only, so no series ever spans a roll."""
    b = bars_by_day.get(day)
    if b is None:
        return pd.DataFrame(columns=["ts_open_utc", "open", "high", "low", "close", "volume", "instrument_id"])
    return b[b["instrument_id"] == instrument_id].sort_values("ts_open_utc").reset_index(drop=True)


def rth_mask(b: pd.DataFrame, cfg) -> pd.Series:
    mod = calm.minutes_of_day_et(b["ts_open_utc"])
    return (mod >= calm.hhmm_to_min(cfg["market"]["rth_open"])) & (mod < calm.hhmm_to_min(cfg["market"]["rth_close"]))


def merge_levels(raw: list[tuple[float, str]], tol: float, tick: float) -> list[tuple[float, set]]:
    """Chain-merge levels whose neighbours are within tol points; mean price, union of tags."""
    if not raw:
        return []
    raw = sorted(raw)
    groups = [[raw[0]]]
    for px, tag in raw[1:]:
        if px - groups[-1][-1][0] <= tol + 1e-9:
            groups[-1].append((px, tag))
        else:
            groups.append([(px, tag)])
    return [(float(round_tick(np.mean([p for p, _ in g]), tick)), {t for _, t in g}) for g in groups]


def day_levels(day, g: pd.Series, sess: pd.DataFrame, prev_sess: pd.DataFrame, roll: bool, cfg) -> pd.DataFrame:
    """Candidate levels for one session. g is the gex_daily row for `day`."""
    tick = cfg["market"]["tick"]
    basis, em, s0 = g["basis"], g["em"], g["s0"]
    if sess.empty or not np.isfinite(basis) or not np.isfinite(em) or em <= 0:
        return pd.DataFrame()
    rth = sess[rth_mask(sess, cfg)]
    if rth.empty:
        return pd.DataFrame()
    es_open = float(rth.iloc[0]["open"])
    on = sess[sess["ts_open_utc"] < rth.iloc[0]["ts_open_utc"]]

    raw: list[tuple[float, str]] = []

    def add_spx(x, tag):
        if np.isfinite(x):
            raw.append((float(round_tick(x + basis, tick)), tag))

    add_spx(g["flip"], "gamma_flip")
    add_spx(g["call_wall"], "gamma_call_wall")
    add_spx(g["put_wall"], "gamma_put_wall")
    for c in ("top1", "top2", "top3"):
        add_spx(g[c], "gamma_top")
    step = param(cfg, "round_step")
    for k in np.arange(np.ceil((s0 - em) / step) * step, s0 + em + 1e-9, step):
        add_spx(k, "round")
    if not roll and not prev_sess.empty:
        prth = prev_sess[rth_mask(prev_sess, cfg)]
        if not prth.empty:
            raw += [(float(prth["high"].max()), "pd_high"), (float(prth["low"].min()), "pd_low")]
    if not on.empty:
        raw += [(float(on["high"].max()), "on_high"), (float(on["low"].min()), "on_low")]

    window = param(cfg, "level_window") * em
    raw = [(p, t) for p, t in raw if abs(p - es_open) <= window]
    merged = merge_levels(raw, param(cfg, "merge_tol") * tick, tick)
    flip_es = g["flip"] + basis if np.isfinite(g["flip"]) else np.nan
    rows = []
    for px, tags in merged:
        rows.append({"level_es": px, "tags": "|".join(sorted(tags))})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = _annotate(df, day, g, es_open, em, flip_es)
    df["is_placebo"] = False
    return df


def _annotate(df, day, g, es_open, em, flip_es):
    tagsets = df["tags"].str.split("|")
    df["is_gamma"] = tagsets.map(lambda t: any(x in GAMMA_TAGS for x in t))
    df["is_structural"] = tagsets.map(lambda t: any(x in STRUCT_TAGS for x in t))
    df["tag_round"] = tagsets.map(lambda t: "round" in t)
    df["tag_pd"] = tagsets.map(lambda t: "pd_high" in t or "pd_low" in t)
    df["tag_on"] = tagsets.map(lambda t: "on_high" in t or "on_low" in t)
    for t in GAMMA_TAGS:
        df[f"tag_{t}"] = tagsets.map(lambda s, t=t: t in s)
    df.insert(0, "date", day)
    df["es_open"] = es_open
    df["em"] = em
    df["dist_em"] = (df["level_es"] - es_open).abs() / em
    df["gex_pct"] = g["gex_pct"]
    df["gex_pct_0dte"] = g.get("gex_pct_0dte", np.nan)
    df["flip_es"] = flip_es
    df["above_flip"] = (es_open > flip_es) if np.isfinite(flip_es) else np.nan
    return df


def add_placebos(day, real: pd.DataFrame, g: pd.Series, cfg) -> pd.DataFrame:
    """placebo_per_day random levels within level_window EM of the open, >= placebo_min_gap ticks
    from any real level. Seeded per date so results don't depend on the run's date range."""
    if real.empty:
        return real
    tick = cfg["market"]["tick"]
    es_open, em = real["es_open"].iloc[0], real["em"].iloc[0]
    seed = param(cfg, "placebo_seed") + zlib.crc32(str(day).encode())
    rng = np.random.default_rng(seed)
    gap = param(cfg, "placebo_min_gap") * tick
    window = param(cfg, "level_window") * em
    reals = real["level_es"].to_numpy()
    out = []
    for _ in range(1000):
        if len(out) >= param(cfg, "placebo_per_day"):
            break
        px = float(round_tick(rng.uniform(es_open - window, es_open + window), tick))
        if abs(px - es_open) > window:
            continue
        if np.all(np.abs(reals - px) >= gap - 1e-9) and px not in out:
            out.append(px)
    pl = pd.DataFrame({"level_es": out, "tags": "placebo"})
    pl = _annotate(pl, day, g, es_open, em, real["flip_es"].iloc[0])
    pl["is_placebo"] = True
    return pl


def level_group(df: pd.DataFrame) -> pd.Series:
    return np.select([df["is_placebo"], df["is_gamma"] & df["is_structural"], df["is_gamma"], df["is_structural"]],
                     ["placebo", "both", "gamma_only", "structural_only"], "other")


def build(start=None, end=None, cfg=None, include_holdout: bool = False, save: bool = True,
          bars: pd.DataFrame | None = None, cal: pd.DataFrame | None = None,
          gex: pd.DataFrame | None = None) -> pd.DataFrame:
    """Levels for every session. include_holdout builds the holdout period only (from holdout_start).
    bars/cal/gex can be passed in to avoid reloading (robustness runs)."""
    cfg = cfg or load_config()
    if include_holdout and start is None:
        start = calm.holdout_start(cfg)
    cal_all = cal if cal is not None else store.load_calendar(cfg, include_holdout)
    cal = store.date_range_filter(cal_all, start, end)
    gex = (gex if gex is not None else store.load_derived("gex_daily", cfg, include_holdout)).set_index("date")
    bars = bars if bars is not None else store.load_bars(cfg, include_holdout)
    by_day = {d: x for d, x in bars.groupby("date")}
    inst = cal_all.set_index("date")["instrument_id"]
    out = []
    for r in cal.itertuples():
        if r.date not in gex.index or pd.isna(r.prev_date):
            continue
        sess = session_bars(by_day, r.date, r.instrument_id)
        prev = session_bars(by_day, r.prev_date, inst.get(r.prev_date, -1))
        lv = day_levels(r.date, gex.loc[r.date], sess, prev, bool(r.roll), cfg)
        if lv.empty:
            log.warning("%s: no levels", r.date)
            continue
        pl = add_placebos(r.date, lv, gex.loc[r.date], cfg)
        day_df = pd.concat([lv, pl], ignore_index=True).sort_values("level_es").reset_index(drop=True)
        day_df["level_id"] = [f"{store.ymd(r.date)}_{i:02d}" for i in range(len(day_df))]
        out.append(day_df)
    levels = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if not levels.empty:
        levels["group"] = level_group(levels)
        if save:
            store.save_derived(levels, "levels_holdout" if include_holdout else "levels", cfg)
    return levels


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    lv = build(a.start, a.end)
    print(lv.groupby("group").size() if not lv.empty else "no levels")


if __name__ == "__main__":
    main()
