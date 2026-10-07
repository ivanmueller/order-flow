"""Study 3 (RUNLOG 2026-10-07, approved): session-level gamma regime, expected-move band trades.

For each in-sample session D with a GEX row (EM and gex_pct are D-1 quantities):
  O     = open of the first RTH bar (09:30 ET) on D's front contract
  bands B_up = O + a*EM, B_dn = O - a*EM           (a = band_a)
  touch = first RTH bar from band_start to band_end whose high >= B_up (side +1) or low <= B_dn
          (side -1) with the previous bar's close inside the band; a bar touching both bands is
          ambiguous -> no trade; price already beyond a band when the window opens -> no trade
          (fill-realism rule added 2026-10-07 before the second run, see RUNLOG)
  breakout fill: if the touch bar opened through the band, the stop order fills at the open
  fade     (R1/R3): direction -side, E = B - side*slip ticks, S = B + side*s*EM, T = O
  breakout (R2):    direction +side, E = B + side*slip ticks, S = B - side*s*EM, T = B + side*a*EM
  R_k = |E - S| (about s*EM); costs cost_rt_usd; time exit flat_time.
Bar fills (SPEC rule 5, 1-minute bars): the touch bar itself can stop the trade out but never pays the
target; afterwards a bar whose extreme touches S fills one tick beyond S (or at the bar's open if it
opened beyond); the target fills at T only when a bar prints one tick beyond it; a bar that touches
both is a loss; the time exit fills at the flat_time bar's open minus one tick.
Both trades are simulated on every session; the regime filter (gex_pct >= regime_threshold for R1,
< for R2, R1 above the flip for R3) is applied in the report, with the complement as the contrast and
a permutation test of gex_pct across sessions as the placebo.

  python -m src.study3            # simulate and report
  python -m src.study3 --report-only
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import stats, store
from src.config import load_config, param
from src.levels import session_bars
from src.sim import pnl_r

log = logging.getLogger("study3")

VARIANTS = {  # name -> (mode, high regime?, needs above-flip?)
    "R1_fade_high_gamma": ("fade", True, False),
    "R2_breakout_low_gamma": ("breakout", False, False),
    "R3_fade_high_gamma_above_flip": ("fade", True, True),
}


def rth(bars: pd.DataFrame, cfg) -> pd.DataFrame:
    mod = calm.minutes_of_day_et(bars["ts_open_utc"])
    o, c = calm.hhmm_to_min(cfg["market"]["rth_open"]), calm.hhmm_to_min(cfg["market"]["rth_close"])
    return bars[(mod >= o) & (mod < c)].reset_index(drop=True)


def session_open(bars: pd.DataFrame, cfg) -> float:
    b = rth(bars, cfg)
    if b.empty:
        return np.nan
    mod = calm.minutes_of_day_et(b["ts_open_utc"])
    if int(mod.iloc[0]) != calm.hhmm_to_min(cfg["market"]["rth_open"]):
        return np.nan                      # the 09:30 bar is missing: no reliable open
    return float(b["open"].iloc[0])


def find_band_touch(bars: pd.DataFrame, O: float, em: float, cfg):
    """(bar index in the RTH frame, side) of the first band touch inside [band_start, band_end], or None."""
    b = rth(bars, cfg)
    a = param(cfg, "band_a") * em
    up, dn = O + a, O - a
    mod = calm.minutes_of_day_et(b["ts_open_utc"]).to_numpy()
    s, e = calm.hhmm_to_min(cfg["market"]["band_start"]), calm.hhmm_to_min(cfg["market"]["band_end"])
    hi, lo, cl = b["high"].to_numpy(float), b["low"].to_numpy(float), b["close"].to_numpy(float)
    for i in np.where((mod >= s) & (mod <= e))[0]:
        if i == 0:
            continue
        inside = dn < cl[i - 1] < up          # a clean touch starts from inside the band
        u, d = hi[i] >= up - 1e-9, lo[i] <= dn + 1e-9
        if u and d:
            return None                    # ambiguous bar: skip the session
        if not inside:
            if u or d:
                return None                # price already beyond the band at band_start: no clean touch
            continue
        if u:
            return int(i), 1
        if d:
            return int(i), -1
    return None


def bar_walk(b: pd.DataFrame, start: int, E: float, S: float, T: float, d: int, t_exit, tick: float,
             entry_bar: int | None = None):
    """Exit (price, reason, bar time). The entry bar can only stop the trade out; later bars follow SPEC
    rule 5 with bar extremes standing in for prints."""
    hi, lo, op = (b[c].to_numpy(float) for c in ("high", "low", "open"))
    ts = b["ts_open_utc"]
    stop_fill = S - d * tick
    if entry_bar is not None:
        adv = lo[entry_bar] if d == 1 else hi[entry_bar]
        if d * (adv - S) <= 0:
            return stop_fill, "stop", ts.iat[entry_bar]
    for j in range(start, len(b)):
        if ts.iat[j] >= t_exit:
            return op[j] - d * tick, "time", ts.iat[j]
        adv = lo[j] if d == 1 else hi[j]
        fav = hi[j] if d == 1 else lo[j]
        if d * (adv - S) <= 0:                                   # stop first: same-bar ambiguity is a loss
            gapped = d * (op[j] - S) < 0
            return (op[j] if gapped else stop_fill), "stop", ts.iat[j]
        if d * (fav - T) >= tick - 1e-9:
            return T, "target", ts.iat[j]
    return op[-1] - d * tick, "data_end", ts.iat[-1]


def _trade(bars, touch_idx, side, O, em, day, cfg, mode: str) -> dict:
    tick = cfg["market"]["tick"]
    b = rth(bars, cfg)
    a, s, slip = param(cfg, "band_a") * em, param(cfg, "band_stop_s") * em, param(cfg, "entry_slippage") * tick
    B = O + side * a
    if mode == "fade":
        d = -side
        E, S, T = B - side * slip, B + side * s, O
    else:
        d = side
        E, S, T = B + side * slip, B - side * s, B + side * a
        op = float(b["open"].iat[touch_idx])
        if side * (op - B) > 0:            # the bar opened through the band: a stop order fills at the open
            E = op
    R_k = d * (E - S)
    t_exit = calm.et_time(day, cfg["market"]["flat_time"])
    X, why, t_x = bar_walk(b, touch_idx + 1, E, S, T, d, t_exit, tick, entry_bar=touch_idx)
    return {"mode": mode, "side": side, "d": d, "entry_ts": b["ts_open_utc"].iat[touch_idx], "E": E, "S": S,
            "T": T, "R_k": R_k, "X": X, "exit_reason": why, "exit_ts": t_x, "pnl_r": pnl_r(X, E, R_k, d, cfg)}


def fade_trade(bars, touch_idx, side, O, em, day, cfg) -> dict:
    return _trade(bars, touch_idx, side, O, em, day, cfg, "fade")


def breakout_trade(bars, touch_idx, side, O, em, day, cfg) -> dict:
    return _trade(bars, touch_idx, side, O, em, day, cfg, "breakout")


def run(cfg=None, save: bool = True) -> pd.DataFrame:
    cfg = cfg or load_config()
    cal = store.load_calendar(cfg)
    gex = store.load_derived("gex_daily", cfg).set_index("date")
    bars = store.load_bars(cfg)
    by_day = {d: x for d, x in bars.groupby("date")}
    rows, skipped = [], {"no_gex_pct": 0, "no_open": 0, "no_touch": 0, "half_day": 0}
    for r in cal.itertuples():
        if r.date not in gex.index or pd.isna(gex.loc[r.date, "gex_pct"]) or not np.isfinite(gex.loc[r.date, "em"]):
            skipped["no_gex_pct"] += 1
            continue
        if bool(getattr(r, "half_day", False)):
            skipped["half_day"] += 1
            continue
        g = gex.loc[r.date]
        b = session_bars(by_day, r.date, r.instrument_id)
        O = session_open(b, cfg)
        if not np.isfinite(O):
            skipped["no_open"] += 1
            continue
        t = find_band_touch(b, O, float(g["em"]), cfg)
        if t is None:
            skipped["no_touch"] += 1
            continue
        idx, side = t
        common = {"date": r.date, "O": O, "em": float(g["em"]), "gex_pct": float(g["gex_pct"]),
                  "flip": float(g["flip"]) if np.isfinite(g["flip"]) else np.nan,
                  "above_flip": (O > float(g["flip"])) if np.isfinite(g["flip"]) else None}
        for fn in (fade_trade, breakout_trade):
            rows.append({**common, **fn(b, idx, side, O, float(g["em"]), r.date, cfg)})
    T = pd.DataFrame(rows)
    log.info("study 3: %d sessions traded, skipped %s", T["date"].nunique() if not T.empty else 0, skipped)
    T.attrs["skipped"] = skipped
    if save and not T.empty:
        store.save_derived(T, "band_trades", cfg)
    return T


def permutation_test(t: pd.DataFrame, high_is_variant: bool, thr: float, draws: int, seed: int,
                     value: str = "pnl_r", pct: str = "gex_pct") -> dict:
    """Observed gap = mean `value` on the variant's regime minus the complement; p = share of `pct`
    shuffles across sessions whose gap is at least as large. Study 4 passes pnl_em / regime_pct."""
    pnl, g = t[value].to_numpy(float), t[pct].to_numpy(float)
    hi = g >= thr
    sel = hi if high_is_variant else ~hi

    def gap(mask):
        if mask.sum() == 0 or (~mask).sum() == 0:
            return np.nan
        return pnl[mask].mean() - pnl[~mask].mean()

    obs = gap(sel)
    rng = np.random.default_rng(seed)
    perm = np.array([gap((rng.permutation(g) >= thr) if high_is_variant else ~(rng.permutation(g) >= thr))
                     for _ in range(draws)])
    p = float((np.sum(perm >= obs) + 1) / (len(perm) + 1)) if np.isfinite(obs) else np.nan   # never exactly 0
    return {"observed_gap": float(obs), "p": p, "perm_95th": float(np.nanquantile(perm, 0.95)), "draws": draws}


def _rw(g: pd.DataFrame, cfg, reward_em: float) -> dict:
    """Driftless baseline from the band price: stop s*EM away (touch), target a*EM plus one tick."""
    if g.empty:
        return {}
    tick = cfg["market"]["tick"]
    s = param(cfg, "band_stop_s") * g["em"]
    p = float((s / (s + reward_em * g["em"] + tick)).mean())
    wins, losses = g[g["pnl_r"] > 0], g[g["pnl_r"] <= 0]
    aw = float(wins["pnl_r"].mean()) if len(wins) else 0.0
    al = float(-losses["pnl_r"].mean()) if len(losses) else 0.0
    rw = p * aw - (1 - p) * al
    return {"rw_win_rate": p, "rw_expectancy_r": rw, "market_share_r": float(g["pnl_r"].mean()) - rw,
            "R_k_ticks_median": float((g["R_k"] / tick).median())}


def report(cfg=None, trades: pd.DataFrame | None = None) -> dict:
    cfg = cfg or load_config()
    if trades is None:
        trades = store.load_derived("band_trades", cfg)
    draws, seed, lvl = param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"), param(cfg, "ci_level")
    thr = param(cfg, "regime_threshold")
    g = cfg["gates"]
    out = {"sessions_traded": int(trades["date"].nunique()), "variants": {}, "contrasts": {},
           "variant_count_note": "studies 1-2 used 9; study 3 adds 3 -> 12 of 20"}
    for name, (mode, high, flipreq) in VARIANTS.items():
        m = trades[trades["mode"] == mode].copy()
        if flipreq:
            m = m[m["above_flip"] == True]  # noqa: E712
        regime = (m["gex_pct"] >= thr) if high else (m["gex_pct"] < thr)
        v, c = m[regime], m[~regime]
        s = stats.trade_summary(v, draws, seed, lvl, sampled_days=int(trades["date"].nunique()))
        contrast = stats.day_bootstrap_diff(v, c, "pnl_r", draws, seed, lvl)   # disjoint days: unpaired bootstrap
        perm = permutation_test(m, high_is_variant=high, thr=thr, draws=param(cfg, "perm_draws"), seed=seed)
        s["complement"] = stats.trade_summary(c, draws, seed, lvl)
        s["regime_contrast"] = contrast
        s["permutation"] = perm
        s["pre_cost"] = _rw(v, cfg, param(cfg, "band_a"))
        s["exit_reasons"] = v["exit_reason"].value_counts().to_dict() if not v.empty else {}
        checks = {f"n>={g['stage3_min_trades']}": s.get("n", 0) >= g["stage3_min_trades"],
                  f"expectancy>={g['stage3_min_expectancy_r']}R": s.get("expectancy_r", -1) >= g["stage3_min_expectancy_r"],
                  "ci_lower>0": s.get("ci_lo", -1) > 0,
                  "regime_contrast_ci_lower>0": contrast["lo"] > 0,
                  "permutation_p<0.05": np.isfinite(perm["p"]) and perm["p"] < 0.05}
        s["checks"] = checks
        s["verdict_vs_rules"] = "PASS" if all(checks.values()) else ("INDICATIVE" if s.get("n", 0) < g["stage3_min_trades"] else "KILL")
        out["variants"][name] = s
    for mode in ("fade", "breakout"):
        m = trades[trades["mode"] == mode]
        out["contrasts"][f"{mode}_all_sessions"] = {**stats.trade_summary(m, draws, seed, lvl), **_rw(m, cfg, param(cfg, "band_a"))}
        if m["gex_pct"].nunique() >= 3:
            ter = pd.qcut(m["gex_pct"].rank(method="first"), 3, labels=["low", "mid", "high"])
            for k, mm in m.groupby(ter, observed=True):
                out["contrasts"][f"{mode}_tercile_{k}"] = stats.trade_summary(mm, draws, seed, lvl)
    out["skipped"] = trades.attrs.get("skipped", {})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    T = store.load_derived("band_trades", cfg) if a.report_only else run(cfg)
    if T.empty:
        print("no band trades")
        return
    print(T.groupby(["mode", "exit_reason"]).size().to_string())
    from src.analysis import to_json
    print(to_json(report(cfg, T)))


if __name__ == "__main__":
    main()
