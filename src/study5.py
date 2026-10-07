"""Study 5 (RUNLOG 2026-10-07, approved with sample A): last-30-minute ES momentum into the close.

For each eligible in-sample session D (equity session, not a half day, previous session not a half day,
not a roll day, VIX close of D-1 present), on D's front contract only:
  P_prev  close of D-1's last RTH bar (the 15:59 bar, closing at 16:00 ET)
  P_dec   close of D's bar opening one minute before s5_decision_time (15:29, closing at 15:30:00)
  EM_V    s5_em_vix_factor x VIX(D-1) / 100 / sqrt(252) x P_prev      (the unit, every session)
  r_ROD   (P_dec - P_prev) / EM_V;  direction d = sign(r_ROD)          (0 -> no trade)
  entry   E = open of the s5_decision_time bar + d x entry_slippage ticks
  S5a     exit X = open of the s5_exit_time bar (16:00) - d x 1 tick    (time exit, SPEC rule 5)
  S5b     stop S_g = E - d x s5_stop_em x EM_V, rounded to the tick grid away from the entry; from the
          entry bar to the bar before the exit bar, a bar whose adverse extreme reaches S_g fills at the
          worse of its open and S_g - d x tick; otherwise the S5a exit
  pnl_pts = d (X - E) - cost_rt_usd / point_value;  pnl_em = pnl_pts / EM_V
Every input closes at or before 15:30:00 on D, or is a D-1 close. Both directions are simulated for
both variants on every session, so the strategy, the always-long and always-short legs, the timing
contrast (gate 4) and both permutations (gate 5) read one table. Sample B is refused until the stage-3
day set is frozen (RUNLOG prerequisite). Gates and descriptive statistics: RUNLOG.md, Study 5.

  python -m src.study5              # simulate and report
  python -m src.study5 --report-only
"""
from __future__ import annotations

import argparse
import logging
import math

import numpy as np
import pandas as pd

from src import calendar as calm
from src import gex, stats, store
from src.config import load_config, param
from src.levels import session_bars

log = logging.getLogger("study5")

MODES = ("momentum", "momentum_stop")
VARIANTS = {"S5a_momentum": "momentum", "S5b_momentum_stop": "momentum_stop"}
DECIDING = "S5b_momentum_stop"          # the defined-risk variant carries the decision on disagreement
SIDES = {"long": 1, "short": -1}
GRID_EPS = 1e-9                         # tick-grid tolerance: floating noise never moves a stop a tick


# ---------------------------------------------------------------------------
# Units, levels, bars
# ---------------------------------------------------------------------------
def em_vix(vix_prev: float, p_prev: float, cfg) -> float:
    if not (np.isfinite(vix_prev) and np.isfinite(p_prev)) or vix_prev <= 0:
        return np.nan
    return param(cfg, "s5_em_vix_factor") * vix_prev / 100.0 / math.sqrt(252.0) * p_prev


def stop_level(E: float, d: int, dist: float, tick: float) -> float:
    """Stop rounded to the tick grid away from the entry (down for a long, up for a short)."""
    raw = E - d * dist
    if d == 1:
        return math.floor(raw / tick + GRID_EPS) * tick
    return math.ceil(raw / tick - GRID_EPS) * tick


def _shift(hhmm: str, minutes: int) -> str:
    m = calm.hhmm_to_min(hhmm) + minutes
    return f"{m // 60:02d}:{m % 60:02d}"


def bar_index_at(b: pd.DataFrame, day, hhmm: str) -> int | None:
    """Row of the bar opening exactly at hhmm ET on `day`, or None."""
    hit = np.flatnonzero((b["ts_open_utc"] == calm.et_time(day, hhmm)).to_numpy())
    return int(hit[0]) if len(hit) else None


def prev_close(prev_bars: pd.DataFrame, prev_day, cfg) -> tuple[float, int]:
    """Close and instrument of D-1's last RTH bar; NaN unless that bar opens one minute before rth_close."""
    if prev_bars.empty:
        return np.nan, -1
    close, inst = gex.es_close_at_spx_close(prev_bars, cfg)
    i = bar_index_at(prev_bars, prev_day, _shift(cfg["market"]["rth_close"], -1))
    if i is None or not np.isfinite(close) or prev_bars["close"].iat[i] != close:
        return np.nan, -1
    return float(close), int(inst)


def walk(b: pd.DataFrame, i_entry: int, i_exit: int, E: float, d: int, S_g: float | None, tick: float):
    """Exit price and reason. Bars i_entry .. i_exit - 1 can stop the trade (the entry bar included);
    otherwise the time exit at the exit bar's open minus a tick. No target."""
    if S_g is not None:
        lo, hi, op = (b[c].to_numpy(float) for c in ("low", "high", "open"))
        for j in range(i_entry, i_exit):
            adv = lo[j] if d == 1 else hi[j]
            if d * (adv - S_g) <= GRID_EPS:
                fill = min(op[j], S_g - tick) if d == 1 else max(op[j], S_g + tick)
                return float(fill), "stop"
    return float(b["open"].iat[i_exit] - d * tick), "time"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def eligibility(row, prev_half_day: bool) -> str | None:
    """Calendar rules: half days, sessions after a half day, and roll days are ineligible."""
    get = row.get if isinstance(row, dict) else (lambda k, default=None: getattr(row, k, default))
    if bool(get("half_day", False)):
        return "half_day"
    if prev_half_day:
        return "prev_half_day"
    if bool(get("roll", False)):
        return "roll_day"
    return None


def session_trade(prev_bars: pd.DataFrame, day_bars: pd.DataFrame, day, prev_day, vix_prev: float, cfg):
    """Both variants, both directions, for one session; (row, None) or (None, skip_reason).
    prev_bars and day_bars must already be restricted to one contract each (levels.session_bars)."""
    m, tick = cfg["market"], cfg["market"]["tick"]
    if not np.isfinite(vix_prev):
        return None, "no_vix"
    b = day_bars.sort_values("ts_open_utc").reset_index(drop=True)
    p_prev, inst_prev = prev_close(prev_bars.sort_values("ts_open_utc").reset_index(drop=True), prev_day, cfg)
    if not np.isfinite(p_prev):
        return None, "no_prev_close"
    dec_t, exit_t = param(cfg, "s5_decision_time"), param(cfg, "s5_exit_time")
    i_dec = bar_index_at(b, day, _shift(dec_t, -1))
    i_ent = bar_index_at(b, day, dec_t)
    i_ext = bar_index_at(b, day, exit_t)
    if i_dec is None:
        return None, "no_decision_bar"
    if i_ent is None:
        return None, "no_entry_bar"
    if i_ext is None:
        return None, "no_exit_bar"
    used = b.loc[[i_dec, i_ent, i_ext], "instrument_id"].unique()
    if len(used) != 1 or int(used[0]) != inst_prev:
        return None, "instrument_mismatch"
    em = em_vix(vix_prev, p_prev, cfg)
    p_dec = float(b["close"].iat[i_dec])
    r_rod = (p_dec - p_prev) / em
    if r_rod == 0:
        return None, "flat_predictor"
    d = 1 if r_rod > 0 else -1
    open_ent, open_ext = float(b["open"].iat[i_ent]), float(b["open"].iat[i_ext])
    slip = param(cfg, "entry_slippage") * tick
    cost = param(cfg, "cost_rt_usd") / m["point_value"]
    row = {"date": day, "prev_date": prev_day, "instrument_id": inst_prev, "P_prev": p_prev, "P_dec": p_dec,
           "vix_prev": vix_prev, "em_v": em, "r_rod_em": r_rod, "d": d,
           "r_l30_em": (open_ext - open_ent) / em,
           "friction_pts": slip + tick + cost}      # entry slippage + the time exit's tick + round-trip cost
    for side, s in SIDES.items():
        E = open_ent + s * slip
        S_g = stop_level(E, s, param(cfg, "s5_stop_em") * em, tick)
        row[f"E_{side}"], row[f"S_{side}"] = E, S_g
        for mode in MODES:
            X, why = walk(b, i_ent, i_ext, E, s, S_g if mode == "momentum_stop" else None, tick)
            pts = s * (X - E) - cost
            row.update({f"X_{mode}_{side}": X, f"exit_{mode}_{side}": why,
                        f"pnl_pts_{mode}_{side}": pts, f"pnl_em_{mode}_{side}": pts / em})
    chosen = "long" if d == 1 else "short"
    for mode in MODES:
        pts = row[f"pnl_pts_{mode}_{chosen}"]
        row.update({f"pnl_pts_{mode}": pts, f"pnl_em_{mode}": pts / em,
                    f"pnl_bp_{mode}": 1e4 * pts / row[f"E_{chosen}"], f"pnl_usd_{mode}": pts * m["point_value"],
                    f"stopped_{mode}": row[f"exit_{mode}_{chosen}"] == "stop"})
    row.update(_descriptive(b, day, i_dec, i_ent, i_ext, p_prev, em, d, cfg))
    return row, None


def _descriptive(b, day, i_dec, i_ent, i_ext, p_prev, em, d, cfg) -> dict:
    """Registered descriptive fields (RUNLOG Study 5, statistics 4, 6, 7 and the data-sanity count)."""
    m = cfg["market"]
    out = {"O": np.nan, "gao_ret_em": np.nan, "intraday_ret_em": np.nan, "band_reached": None,
           "mv_to_moc_em": np.nan, "mv_moc_to_exit_em": np.nan}
    i_open = bar_index_at(b, day, m["rth_open"])
    if i_open is not None:
        O = float(b["open"].iat[i_open])
        out["O"] = O
        out["intraday_ret_em"] = (float(b["close"].iat[i_dec]) - O) / em
        band = param(cfg, "band_a") * em
        seg = b.iloc[i_open:i_ent]
        out["band_reached"] = bool((seg["high"] >= O + band).any() or (seg["low"] <= O - band).any())
    i_gao = bar_index_at(b, day, _shift(m["first_half_hour_end"], -1))
    if i_gao is not None:
        out["gao_ret_em"] = (float(b["close"].iat[i_gao]) - p_prev) / em
    i_moc = bar_index_at(b, day, m["moc_imbalance_time"])
    if i_moc is not None:
        o_ent, o_moc, o_ext = (float(b["open"].iat[i]) for i in (i_ent, i_moc, i_ext))
        out["mv_to_moc_em"] = d * (o_moc - o_ent) / em
        out["mv_moc_to_exit_em"] = d * (o_ext - o_moc) / em
    want = pd.date_range(b["ts_open_utc"].iat[i_dec], b["ts_open_utc"].iat[i_ext], freq="min")
    out["n_missing_window"] = int(len(want) - want.isin(b["ts_open_utc"]).sum())
    out["n_duplicate_bars"] = int(b["ts_open_utc"].duplicated().sum())
    return out


def _sample_start(cfg):
    s = param(cfg, "s5_sample")
    if s != "A":
        raise RuntimeError(f"Study 5 sample {s!r} is not approved: sample B first needs the stage-3 day set "
                           "frozen to disk (RUNLOG Study 5 prerequisite), so a calendar rebuild cannot redraw it.")
    return pd.Timestamp(cfg["sample"]["s5_starts"][s]).date()


def run(cfg=None, save: bool = True, include_holdout: bool = False) -> pd.DataFrame:
    cfg = cfg or load_config()
    start = _sample_start(cfg)
    cal = store.load_calendar(cfg, include_holdout)
    bars = store.load_bars(cfg, include_holdout)
    daily = store.load_daily(cfg, include_holdout).set_index("date")
    vix = daily["vix_close"]
    by_day = {d: x for d, x in bars.groupby("date")}
    half = dict(zip(cal["date"], cal["half_day"])) if "half_day" in cal else {}
    gx = None
    p = store.derived_path(cfg, "gex_daily")
    if p.exists():
        gx = store.load_derived("gex_daily", cfg, include_holdout).set_index("date")
    rows, skipped = [], {}
    for r in cal.itertuples():
        if r.date < start or pd.isna(r.prev_date):
            continue
        why = eligibility(r, bool(half.get(r.prev_date, False)))
        out = None
        if why is None:
            out, why = session_trade(session_bars(by_day, r.prev_date, r.instrument_id),
                                     session_bars(by_day, r.date, r.instrument_id),
                                     r.date, r.prev_date, float(vix.get(r.prev_date, np.nan)), cfg)
        if why is not None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        out.update(_gex_fields(gx, r.date))
        rows.append(out)
    T = pd.DataFrame(rows)
    log.info("study 5: %d sessions, skipped %s", len(T), skipped)
    T.attrs["skipped"] = skipped
    if save and not T.empty:
        store.save_derived(T, "close_momentum_trades", cfg)
    return T


def _gex_fields(gx, day) -> dict:
    """D row of gex_daily (OI published before 09:30 D, quotes of D-1: point in time at 15:30 on D).
    Used only for the descriptive gamma slope and the option-EM cross-check."""
    out = {"net_gex": np.nan, "em_option": np.nan}
    if gx is None or day not in gx.index:
        return out
    g = gx.loc[day]
    out["net_gex"] = float(g["net_gex"]) if pd.notna(g.get("net_gex")) else np.nan
    if g.get("nearest_root") == "SPXW" and pd.notna(g.get("nearest_exp")) and pd.Timestamp(g["nearest_exp"]).date() == day:
        out["em_option"] = float(g["em"])
    return out


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def choose(f: pd.DataFrame) -> pd.DataFrame:
    """Add the strategy's pnl_em from its direction d and the precomputed long and short outcomes."""
    f = f.copy()
    f["pnl_em"] = np.where(f["d"].to_numpy() == 1, f["long"].to_numpy(float), f["short"].to_numpy(float))
    return f


def variant_frame(T: pd.DataFrame, mode: str) -> pd.DataFrame:
    f = pd.DataFrame({"date": T["date"].to_numpy(), "d": T["d"].to_numpy(int),
                      "long": T[f"pnl_em_{mode}_long"].to_numpy(float),
                      "short": T[f"pnl_em_{mode}_short"].to_numpy(float)})
    return choose(f).sort_values("date").reset_index(drop=True)


def _contrast(d, L, S) -> tuple[float, float, float]:
    p = np.where(d == 1, L, S)
    share = (d == 1).mean()
    bench = share * L.mean() + (1 - share) * S.mean()
    return float(p.mean()), float(bench), float(p.mean() - bench)


def timing_contrast(f: pd.DataFrame, draws: int, seed: int, level: float) -> dict:
    """Gate 4: strategy mean minus the same long/short mix in random directions; sessions resampled
    (one trade per session, so a day bootstrap), with the long share and both legs recomputed each draw."""
    d, L, S = f["d"].to_numpy(int), f["long"].to_numpy(float), f["short"].to_numpy(float)
    strat, bench, diff = _contrast(d, L, S)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(f), size=(draws, len(f)))
    boot = np.array([_contrast(d[i], L[i], S[i])[2] for i in idx])
    a = (1 - level) / 2
    return {"strategy": strat, "benchmark": bench, "diff": diff,
            "lo": float(np.quantile(boot, a)), "hi": float(np.quantile(boot, 1 - a))}


def _perm_p(obs: float, perm: np.ndarray) -> float:
    return float((np.sum(perm >= obs - 1e-12) + 1) / (len(perm) + 1))


def permutation_p(f: pd.DataFrame, draws: int, seed: int) -> dict:
    """Gate 5 (session half): shuffle the directions across sessions, keeping the long/short counts."""
    d, L, S = f["d"].to_numpy(int), f["long"].to_numpy(float), f["short"].to_numpy(float)
    obs = float(np.where(d == 1, L, S).mean())
    rng = np.random.default_rng(seed)
    perm = np.array([np.where(dp == 1, L, S).mean() for dp in (rng.permutation(d) for _ in range(draws))])
    return {"observed": obs, "perm_mean": float(perm.mean()), "p": _perm_p(obs, perm), "draws": draws}


def block_permutation_p(f: pd.DataFrame, block: int, draws: int, seed: int) -> dict:
    """Gate 5 (block half): shuffle the directions in contiguous blocks of `block` sessions (date order),
    so slow drift that moves with the share of up days survives the shuffle."""
    f = f.sort_values("date")
    d, L, S = f["d"].to_numpy(int), f["long"].to_numpy(float), f["short"].to_numpy(float)
    obs = float(np.where(d == 1, L, S).mean())
    blocks = [d[i:i + block] for i in range(0, len(d), block)]
    rng = np.random.default_rng(seed)
    perm = np.array([np.where(np.concatenate([blocks[k] for k in rng.permutation(len(blocks))]) == 1, L, S).mean()
                     for _ in range(draws)])
    return {"observed": obs, "p": _perm_p(obs, perm), "block_sessions": block, "draws": draws}


def era_ok(full: float, recent: float) -> bool:
    """Same sign as the full-sample value and at least half its size (the holdout rule's form)."""
    return bool(np.sign(recent) == np.sign(full) and abs(recent) >= 0.5 * abs(full))


def nw_slope(df: pd.DataFrame, y: str, x: str, lags: int) -> dict:
    d = df.dropna(subset=[y, x]).sort_values("date")
    if len(d) < 30:
        return {"n": int(len(d)), "note": "too few sessions"}
    res = stats.ols_nw(d, f"{y} ~ {x}", lags)
    return {"beta": float(res.params[x]), "se": float(res.bse[x]), "t": float(res.tvalues[x]), "n": int(res.nobs)}


def decide(verdicts: dict) -> str:
    """S5b (defined risk) carries the decision when the two variants disagree."""
    return verdicts[DECIDING]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _summary(f: pd.DataFrame, cfg) -> dict:
    p = f["pnl_em"].to_numpy(float)
    wins, losses = p[p > 0], p[p <= 0]
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    boot = stats.day_bootstrap_mean(f, "pnl_em", draws, seed, lvl)
    k = cfg["gates"]["study5_tail_drop"]
    best, worst = f.nlargest(k, "pnl_em"), f.nsmallest(k, "pnl_em")
    return {"n": len(p), "mean_em": float(p.mean()), "ci_lo": boot["lo"], "ci_hi": boot["hi"],
            "win_rate": float((p > 0).mean()), "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.inf,
            "max_dd_em": stats.max_drawdown(p), "longest_losing_streak": stats.longest_losing_streak(p),
            "trades_per_month": len(p) / max(1.0, (pd.Timestamp(max(f["date"])) - pd.Timestamp(min(f["date"]))).days / 30.44),
            "long_share": float((f["d"] == 1).mean()),
            "tail": {"mean_em_ex_best": float(f.drop(best.index)["pnl_em"].mean()) if len(f) > k else np.nan,
                     "best": [{"date": str(a), "pnl_em": float(b)} for a, b in zip(best["date"], best["pnl_em"])],
                     "worst": [{"date": str(a), "pnl_em": float(b)} for a, b in zip(worst["date"], worst["pnl_em"])],
                     "best_sum_em": float(best["pnl_em"].sum()), "worst_sum_em": float(worst["pnl_em"].sum())}}


def _variant(T: pd.DataFrame, mode: str, cfg, era_start=None) -> dict:
    g = cfg["gates"]
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    f = variant_frame(T, mode)
    s = _summary(f, cfg)
    s["timing_contrast"] = timing_contrast(f, draws, seed, lvl)
    s["permutation"] = permutation_p(f, param(cfg, "perm_draws"), seed)
    s["block_permutation"] = block_permutation_p(f, param(cfg, "s5_perm_block"), param(cfg, "perm_draws"), seed + 1)
    s["mean_pts"] = float(T[f"pnl_pts_{mode}"].mean())
    s["mean_bp"] = float(T[f"pnl_bp_{mode}"].mean())
    s["mean_usd"] = float(T[f"pnl_usd_{mode}"].mean())
    pp = g["study5_perm_p"]
    checks = {f"n>={g['study5_min_sessions']}": s["n"] >= g["study5_min_sessions"],
              f"mean_em>={g['study5_min_expectancy_em']}": s["mean_em"] >= g["study5_min_expectancy_em"],
              "ci_lower>0": s["ci_lo"] > 0,
              "timing_contrast_ci_lower>0": s["timing_contrast"]["lo"] > 0,
              f"both_permutation_p<{pp}": s["permutation"]["p"] < pp and s["block_permutation"]["p"] < pp,
              f"mean_ex_best{g['study5_tail_drop']}>0": s["tail"]["mean_em_ex_best"] > 0}
    existence = ["timing_contrast_ci_lower>0", f"both_permutation_p<{pp}"]
    if era_start is not None:
        rec = f[f["date"] >= era_start]
        s["era"] = {"recent_n": len(rec), "recent_mean_em": float(rec["pnl_em"].mean()) if len(rec) else np.nan,
                    "recent_timing_contrast": _contrast(rec["d"].to_numpy(int), rec["long"].to_numpy(float),
                                                        rec["short"].to_numpy(float))[2] if len(rec) else np.nan}
        checks["era_existence"] = era_ok(s["timing_contrast"]["diff"], s["era"]["recent_timing_contrast"])
        checks["era_economics"] = era_ok(s["mean_em"], s["era"]["recent_mean_em"])
        existence.append("era_existence")
    s["checks"] = checks
    s["existence"] = "PASS" if all(checks[k] for k in existence) else "FAIL"
    s["economics"] = "PASS" if all(v for k, v in checks.items() if k not in existence and not k.startswith("n>=")) else "FAIL"
    s["verdict_vs_rules"] = ("PASS" if all(checks.values())
                             else "INDICATIVE" if s["n"] < g["study5_min_sessions"] else "KILL")
    s["stopped_share"] = float(T[f"stopped_{mode}"].mean())
    return s


def _descriptive_report(T: pd.DataFrame, cfg) -> dict:
    lags = param(cfg, "nw_lags")
    f = variant_frame(T, "momentum")
    out = {}
    long_all, short_all = T["pnl_em_momentum_long"], T["pnl_em_momentum_short"]
    out["drift"] = {"always_long_mean_em": float(long_all.mean()), "always_short_mean_em": float(short_all.mean()),
                    "strategy_long_leg_mean_em": float(f.loc[f["d"] == 1, "pnl_em"].mean()),
                    "strategy_short_leg_mean_em": float(f.loc[f["d"] == -1, "pnl_em"].mean()),
                    "n_long": int((f["d"] == 1).sum()), "n_short": int((f["d"] == -1).sum())}
    out["slope_r_l30_on_r_rod"] = nw_slope(T, "r_l30_em", "r_rod_em", lags)
    k = param(cfg, "s5_outlier_n")
    trimmed = T.drop(T["r_rod_em"].abs().nlargest(k).index)
    out["slope_without_largest_abs_r_rod"] = {**nw_slope(trimmed, "r_l30_em", "r_rod_em", lags), "dropped": k}
    out["other_predictors"] = {"prior_close_to_first_half_hour": nw_slope(T, "r_l30_em", "gao_ret_em", lags),
                               "open_to_decision": nw_slope(T, "r_l30_em", "intraday_ret_em", lags)}

    def terciles(col):
        q = pd.qcut(col.rank(method="first"), 3, labels=["low", "mid", "high"])
        return {str(lab): {"n": int(len(g)), "mean_em": float(g["pnl_em_momentum"].mean())}
                for lab, g in T.assign(_q=q.values).groupby("_q", observed=True)}
    if len(T) >= 3:
        out["terciles_abs_r_rod"] = terciles(T["r_rod_em"].abs())
        out["terciles_em_v_over_price"] = terciles(T["em_v"] / T["P_prev"])
    out["by_year"] = {str(y): {"n": int(len(g)), "mean_em": float(g["pnl_em_momentum"].mean())}
                      for y, g in T.groupby(pd.to_datetime(T["date"]).dt.year)}
    from src.analysis import _event_dates
    ev = _event_dates()
    fomc = T["date"].isin(ev)
    out["fomc"] = {"n_event": int(fomc.sum()), "event_mean_em": float(T.loc[fomc, "pnl_em_momentum"].mean()) if fomc.any() else np.nan,
                   "other_mean_em": float(T.loc[~fomc, "pnl_em_momentum"].mean())}
    gam = T.dropna(subset=["net_gex"]) if "net_gex" in T else T.iloc[0:0]
    out["gamma_slopes"] = {"note": "descriptive only (RUNLOG Study 5 statistic 5); cannot produce a Go",
                           "net_gex_negative": nw_slope(gam[gam["net_gex"] < 0], "r_l30_em", "r_rod_em", lags) if len(gam) else {"n": 0},
                           "net_gex_non_negative": nw_slope(gam[gam["net_gex"] >= 0], "r_l30_em", "r_rod_em", lags) if len(gam) else {"n": 0}}
    out["sub_windows"] = {"to_moc_mean_em": float(T["mv_to_moc_em"].mean()), "moc_to_exit_mean_em": float(T["mv_moc_to_exit_em"].mean())}
    br = T["band_reached"].astype("boolean")
    out["band_reached"] = {"n_reached": int(br.sum()), "reached_mean_em": float(T.loc[br.fillna(False).to_numpy(), "pnl_em_momentum"].mean()) if br.any() else np.nan,
                           "not_reached_mean_em": float(T.loc[(~br).fillna(False).to_numpy(), "pnl_em_momentum"].mean()) if (~br).any() else np.nan}
    out["friction"] = {"mean_pts": float(T["friction_pts"].mean()), "mean_em": float((T["friction_pts"] / T["em_v"]).mean()),
                       "stopped_share_s5b": float(T["stopped_momentum_stop"].mean())}
    if "em_option" in T and T["em_option"].notna().any():
        o = T.dropna(subset=["em_option"])
        out["option_em_cross_check"] = {"n": int(len(o)), "S5a_mean_option_em": float((o["pnl_pts_momentum"] / o["em_option"]).mean()),
                                        "S5b_mean_option_em": float((o["pnl_pts_momentum_stop"] / o["em_option"]).mean()),
                                        "median_em_option_over_em_v": float((o["em_option"] / o["em_v"]).median())}
    big = T[T["r_l30_em"].abs() > param(cfg, "s5_sanity_em")]
    out["sanity"] = {"sessions_missing_minutes": int((T["n_missing_window"] > 0).sum()),
                     "duplicate_bars": int(T["n_duplicate_bars"].sum()),
                     "large_r_l30": [{"date": str(a), "r_l30_em": float(b)} for a, b in zip(big["date"], big["r_l30_em"])]}
    return out


def report(cfg=None, trades: pd.DataFrame | None = None) -> dict:
    cfg = cfg or load_config()
    if trades is None:
        trades = store.load_derived("close_momentum_trades", cfg)
    T = trades.sort_values("date").reset_index(drop=True)
    era = None if param(cfg, "s5_sample") == "A" else pd.Timestamp(cfg["sample"]["s5_starts"]["A"]).date()
    out = {"sessions": int(len(T)), "first": str(T["date"].min()), "last": str(T["date"].max()),
           "sample": param(cfg, "s5_sample"), "variants": {},
           "variant_count_note": "studies 1-4 used 15; study 5 adds 2 -> 17 of 20"}
    for name, mode in VARIANTS.items():
        out["variants"][name] = _variant(T, mode, cfg, era)
    out["decision_vs_rules"] = decide({k: v["verdict_vs_rules"] for k, v in out["variants"].items()})
    out["deciding_variant_note"] = f"{DECIDING} carries the decision when the variants disagree"
    out["descriptive"] = _descriptive_report(T, cfg)
    out["skipped"] = trades.attrs.get("skipped", {})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    T = store.load_derived("close_momentum_trades", cfg) if a.report_only else run(cfg)
    if T.empty:
        print("no study 5 trades")
        return
    from src.analysis import to_json
    print(to_json(report(cfg, T)))


if __name__ == "__main__":
    main()
