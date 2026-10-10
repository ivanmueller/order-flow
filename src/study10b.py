"""Study 10b (Matteo 2026-10-09): conditions for quoting IWM options. Step 1, exploration on the pilot fills.

For every pilot fill, using only what was known at the fill (snapshots stamped at or before it):
  IWM price  each cbbo-1m minute, put-call parity on the nearest expiry after the session date:
             F = K + C_mid - P_mid, median over the s10b_parity_strikes strikes with the smallest |C - P|
  rv         trailing realized volatility of IWM, sqrt(sum of the last s10b_vol_window_min squared 1-minute log
             returns) in bp (NaN until the window is full, so the first half hour has no value)
  accel      volatility of the last s10b_accel_half_min minutes / of the half before (> 1 = expanding)
  sweep      the same contract was swept (prints within s10_sweep_ms on >= s10b_sweep_min_venues venues) in the
             s10b_sweep_lookback_s seconds before the fill
  gamma_day  the day's SPX net gamma graded by size (Matteo: deep vs weak matters): z = net_gex / median |net_gex|
             of prior sessions; deep if |z| >= s10b_gamma_deep_ratio -> deep/weak positive/negative
  gamma_live at each fill, SPX (last completed ES bar minus the day's ES-SPX basis) against the day's flip: the
             side of the flip (S0's side carries the sign of net gamma) and near-flip if within s10b_flip_near_em
             expected moves, so a weak negative day that trades through the flip reads positive from that minute.
             All gamma inputs are known before the open (gex_daily: OI before 09:30, quotes of the day before);
             SPX gamma is a market-wide proxy, as there is no IWM open interest on disk
Candidate filters (stated before any number is seen): C1 skip the top volatility tercile, C2 skip the top
acceleration tercile, C3 skip after a recent sweep, C4 skip deep-negative live gamma, C5 skip if C1, C2 or C3,
C6 skip if any of C1-C4.
Each is compared with all fills: mean RS kept minus mean RS of all (session-bootstrap CI), break-through share
kept vs all. Exploration only: filters chosen here must be frozen and confirmed on fresh sessions.

  python -m src.study10b --explore
  python -m src.study10b --confirm      # after: python -m src.study10 --confirm --run
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import store, study10
from src.config import load_config, param

log = logging.getLogger("study10b")


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
def _osi_map(symbols: pd.Series) -> pd.DataFrame:
    from src.ingest_options import parse_osi
    u = pd.Series(symbols.unique())
    p = parse_osi(u)
    p.index = u
    return p


def parity_prices(q: pd.DataFrame, day, n_strikes: int) -> pd.Series:
    """IWM per snapshot time from put-call parity on the nearest expiry after `day`."""
    q = q[(q["bid"] > 0) & (q["ask"] > q["bid"])].copy()
    if q.empty:
        return pd.Series(dtype=float)
    osi = _osi_map(q["symbol"])
    q = q.join(osi, on="symbol")
    later = q.loc[q["expiration"] > day, "expiration"]
    if later.empty:
        return pd.Series(dtype=float)
    q = q[q["expiration"] == later.min()]
    q["mid"] = (q["bid"] + q["ask"]) / 2
    wide = q.pivot_table(index=["ts", "strike"], columns="right", values="mid", aggfunc="last").dropna()
    if wide.empty or not {"C", "P"} <= set(wide.columns):
        return pd.Series(dtype=float)
    wide = wide.reset_index()
    wide["F"] = wide["strike"] + wide["C"] - wide["P"]
    wide["gap"] = (wide["C"] - wide["P"]).abs()
    near = wide.sort_values(["ts", "gap"]).groupby("ts").head(n_strikes)
    return near.groupby("ts")["F"].median().sort_index()


def vol_features(px: pd.Series, window: int, half: int) -> pd.DataFrame:
    """rv (bp) over `window` 1-minute log returns and accel = rv(last half) / rv(previous half)."""
    px = px.sort_index()
    if len(px):
        px = px.asfreq("min")
    r2 = np.log(px).diff() ** 2
    rv = np.sqrt(r2.rolling(window, min_periods=window).sum()) * 1e4
    last = np.sqrt(r2.rolling(half, min_periods=half).sum())
    prev = last.shift(half)
    accel = (last / prev).where(prev > 0)
    return pd.DataFrame({"rv": rv, "accel": accel})


def attach_minute_features(fills: pd.DataFrame, feat: pd.DataFrame) -> pd.DataFrame:
    """Features of the latest snapshot stamped at or before each fill."""
    f = feat.reset_index().rename(columns={"index": "m_ts"})
    f = f.rename(columns={f.columns[0]: "m_ts"}).sort_values("m_ts")
    left = fills[["ts"]].reset_index().sort_values("ts")
    m = pd.merge_asof(left, f, left_on="ts", right_on="m_ts", direction="backward").set_index("index").sort_index()
    return m[[c for c in feat.columns]]


def sweep_times(tr: pd.DataFrame, sweep_ms: int, min_venues: int) -> pd.DataFrame:
    """Start time of every multi-venue sweep: prints of one contract each within sweep_ms of the previous."""
    g = tr.sort_values(["symbol", "ts"], kind="stable").reset_index(drop=True)
    if g.empty:
        return pd.DataFrame(columns=["symbol", "ts"])
    new = ~(g["symbol"].eq(g["symbol"].shift()) & (g["ts"] - g["ts"].shift()).le(pd.Timedelta(milliseconds=sweep_ms)))
    g["cid"] = new.cumsum()
    agg = g.groupby("cid").agg(symbol=("symbol", "first"), ts=("ts", "first"), venues=("venue", "nunique"))
    return agg.loc[agg["venues"] >= min_venues, ["symbol", "ts"]].sort_values("ts").reset_index(drop=True)


def recent_sweep(fills: pd.DataFrame, sweeps: pd.DataFrame, lookback_s: float) -> np.ndarray:
    """A sweep of the same contract strictly before the fill and within lookback_s."""
    if fills.empty:
        return np.zeros(0, bool)
    if sweeps.empty:
        return np.zeros(len(fills), bool)
    left = fills[["ts", "symbol"]].reset_index().sort_values("ts")
    right = sweeps.rename(columns={"ts": "sw_ts"}).sort_values("sw_ts")
    m = pd.merge_asof(left, right, left_on="ts", right_on="sw_ts", by="symbol", direction="backward",
                      allow_exact_matches=False).set_index("index").sort_index()
    return ((m["ts"] - m["sw_ts"]).le(pd.Timedelta(seconds=lookback_s))).fillna(False).to_numpy(bool)


def gamma_grades(gx: pd.DataFrame, ratio: float, min_days: int, lookback: int) -> pd.DataFrame:
    """Day grade from net gamma relative to its typical size: scale = median |net_gex| of up to `lookback`
    previous sessions (at least min_days), z = net / scale; deep if |z| >= ratio. Prior sessions only."""
    g = gx.sort_index().copy()
    a = g["net_gex"].abs().to_numpy(float)
    scale = np.full(len(g), np.nan)
    for i in range(len(g)):
        prev = a[max(0, i - lookback):i]
        prev = prev[np.isfinite(prev)]
        if len(prev) >= min_days:
            scale[i] = np.median(prev)
    g["scale"] = scale
    z = g["net_gex"].to_numpy(float) / scale
    g["z"] = z
    grade = []
    for v in z:
        if not np.isfinite(v):
            grade.append(None)
        else:
            grade.append(("deep " if abs(v) >= ratio else "weak ") + ("positive" if v >= 0 else "negative"))
    g["grade"] = grade
    return g


def live_gamma(spx, row: dict, near_em: float):
    """Live gamma at each SPX level: the side of the day's flip (the side of S0 carries the sign of net_gex)
    and whether SPX is within near_em expected moves of the flip. No flip on the grid: the day's sign, deep."""
    spx = np.asarray(spx, float)
    net, s0, flip, em = (float(row.get(k, np.nan)) for k in ("net_gex", "s0", "flip", "em"))
    labels = np.full(len(spx), None, dtype=object)
    dist = np.full(len(spx), np.nan)
    if not np.isfinite(net):
        return labels, dist
    day_sign = 1 if net >= 0 else -1
    ok = np.isfinite(spx)
    if not (np.isfinite(flip) and np.isfinite(s0) and np.isfinite(em) and em > 0):
        labels[ok] = "deep " + ("positive" if day_sign > 0 else "negative")
        return labels, dist
    s0_side = 1 if s0 >= flip else -1
    dist = (spx - flip) / em
    side = np.where(spx >= flip, 1, -1)
    sign = np.where(side == s0_side, day_sign, -day_sign)
    for i in np.flatnonzero(ok):
        labels[i] = ("near-flip " if abs(dist[i]) < near_em else "deep ") + ("positive" if sign[i] > 0 else "negative")
    return labels, np.where(ok, dist, np.nan)


def spx_at_fills(fills: pd.DataFrame, bars: pd.DataFrame, basis: float) -> np.ndarray:
    """SPX at each fill = close of the last ES bar completed by then (bar open + 1 minute <= fill) - basis."""
    if bars is None or bars.empty or not np.isfinite(basis):
        return np.full(len(fills), np.nan)
    b = bars[["ts_open_utc", "close"]].copy()
    b["ts_close"] = b["ts_open_utc"] + pd.Timedelta(minutes=1)
    b = b.sort_values("ts_close")
    left = fills[["ts"]].reset_index().sort_values("ts")
    m = pd.merge_asof(left, b[["ts_close", "close"]], left_on="ts", right_on="ts_close",
                      direction="backward").set_index("index").sort_index()
    return (m["close"] - basis).to_numpy(float)


def session_features(cfg, day, Fd: pd.DataFrame, gx: pd.DataFrame | None, grades: pd.DataFrame | None = None,
                     bars: pd.DataFrame | None = None):
    """Features for one session's fills, and an IWM sanity row."""
    m = cfg["market"]
    tr = study10.norm_trades(study10._read_pieces(cfg, "tcbbo", day))
    t0, t1 = calm.et_time(day, m["rth_open"]), calm.et_time(day, m["rth_close"])
    tr = tr[(tr["ts"] >= t0) & (tr["ts"] < t1)]
    q = study10.norm_quotes(study10._read_pieces(cfg, "cbbo-1m", day))
    px = parity_prices(q, day, param(cfg, "s10b_parity_strikes"))
    px = px[(px.index >= t0) & (px.index <= t1)]
    feat = vol_features(px, param(cfg, "s10b_vol_window_min"), param(cfg, "s10b_accel_half_min"))
    out = Fd.copy()
    out[["rv", "accel"]] = attach_minute_features(Fd, feat).to_numpy()
    sw = sweep_times(tr, param(cfg, "s10_sweep_ms"), param(cfg, "s10b_sweep_min_venues"))
    out["sweep_recent"] = recent_sweep(Fd, sw, param(cfg, "s10b_sweep_lookback_s"))
    out["gamma_day"] = grades["grade"].get(day) if grades is not None and day in grades.index else None
    out["gamma_live"], out["flip_dist_em"] = None, np.nan
    if gx is not None and day in gx.index:
        row = gx.loc[day].to_dict()
        spx = spx_at_fills(Fd, bars, float(row.get("basis", np.nan)))
        lab, dist = live_gamma(spx, row, param(cfg, "s10b_flip_near_em"))
        out["gamma_live"], out["flip_dist_em"] = lab, dist
    r = np.log(px).diff().dropna()
    sanity = {"iwm_minutes": int(len(px)), "iwm_min": float(px.min()) if len(px) else np.nan,
              "iwm_max": float(px.max()) if len(px) else np.nan,
              "median_abs_1min_bp": float((r.abs() * 1e4).median()) if len(r) else np.nan,
              "sweeps": int(len(sw)), "gamma_day": out["gamma_day"].iloc[0] if len(out) else None,
              "gamma_live_mix": {str(k): int(v) for k, v in out["gamma_live"].value_counts().items()},
              "share_fills_with_vol": float(out["rv"].notna().mean()) if len(out) else np.nan}
    return out, sanity


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def cutpoints(F: pd.DataFrame, cfg) -> dict:
    q = param(cfg, "s10b_top_quantile")
    return {c: float(F[c].dropna().quantile(q)) for c in ("rv", "accel")}


def compare(F: pd.DataFrame, keep, cfg, col: str = "rs_5") -> dict:
    """Kept vs all: mean RS, the difference with a session-bootstrap CI, and the break-through share."""
    keep = np.asarray(keep, bool)
    x = F.assign(_k=keep).dropna(subset=[col])
    days = np.array(sorted(x["date"].unique()))
    ga = x.groupby("date")[col].agg(["sum", "count"]).reindex(days, fill_value=0)
    gk = x[x["_k"]].groupby("date")[col].agg(["sum", "count"]).reindex(days, fill_value=0)
    rng = np.random.default_rng(param(cfg, "bootstrap_seed"))
    idx = rng.integers(0, len(days), size=(param(cfg, "bootstrap_draws"), len(days)))
    sa, na = ga["sum"].to_numpy()[idx].sum(1), ga["count"].to_numpy()[idx].sum(1)
    sk, nk = gk["sum"].to_numpy()[idx].sum(1), gk["count"].to_numpy()[idx].sum(1)
    diff = np.where((nk > 0) & (na > 0), sk / np.where(nk > 0, nk, 1) - sa / np.where(na > 0, na, 1), np.nan)
    lvl = param(cfg, "ci_level")
    kx = x[x["_k"]]
    return {"n_kept": int(len(kx)), "share_kept": float(len(kx) / len(x)) if len(x) else np.nan,
            f"{col}_kept": float(kx[col].mean()) if len(kx) else np.nan, f"{col}_all": float(x[col].mean()),
            f"diff_{col}": float(kx[col].mean() - x[col].mean()) if len(kx) else np.nan,
            "diff_ci_lo": float(np.nanquantile(diff, (1 - lvl) / 2)),
            "diff_ci_hi": float(np.nanquantile(diff, 1 - (1 - lvl) / 2)),
            "cleared_kept": float(kx["cleared"].mean()) if len(kx) else np.nan,
            "cleared_all": float(x["cleared"].mean()),
            "sessions_kept_better": int(sum(
                (g[g["_k"]][col].mean() > g[col].mean()) for _, g in x.groupby("date") if g["_k"].any()))}


def candidates(F: pd.DataFrame, cut: dict) -> dict:
    hi_rv = F["rv"] >= cut["rv"]
    hi_acc = F["accel"] >= cut["accel"]
    sw = F["sweep_recent"].astype(bool)
    deep_neg = F["gamma_live"].eq("deep negative")
    return {"C1_skip_top_vol": ~hi_rv.fillna(False),
            "C2_skip_top_accel": ~hi_acc.fillna(False),
            "C3_skip_after_sweep": ~sw,
            "C4_skip_deep_negative_live_gamma": ~deep_neg,
            "C5_skip_if_C1_C2_or_C3": ~(hi_rv.fillna(False) | hi_acc.fillna(False) | sw),
            "C6_skip_if_C1_to_C4": ~(hi_rv.fillna(False) | hi_acc.fillna(False) | sw | deep_neg)}


def by_level(F: pd.DataFrame, col: str, cut: float, horizon_cols=("rs_5", "rs_15")) -> dict:
    lab = np.where(F[col].isna(), "no value (first half hour)", np.where(F[col] >= cut, "top tercile", "lower two"))
    out = {}
    for b, g in F.groupby(lab):
        out[str(b)] = {"n": int(len(g)), **{h: float(g[h].mean()) for h in horizon_cols},
                       "cleared": float(g["cleared"].mean())}
    return out


def _by_label(A: pd.DataFrame, col: str) -> dict:
    return {str(k): {"n": int(len(g)), "sessions": int(g["date"].nunique()), "rs_5": float(g["rs_5"].mean()),
                     "rs_15": float(g["rs_15"].mean()), "cleared": float(g["cleared"].mean())}
            for k, g in A.groupby(A[col].fillna("unknown"))}


def explore(F: pd.DataFrame, cfg, sanity: dict | None = None) -> dict:
    buckets = cfg["bankroll"]["s10_buckets"]
    A = F[F["spread_b"].isin(buckets)].reset_index(drop=True)
    cut = cutpoints(A, cfg)
    out = {"note": "Study 10b step 1: exploration on the pilot fills (in sample); not a test. Filters chosen here "
                   "are frozen and confirmed on fresh sessions.",
           "fills_in_advancing_buckets": int(len(A)), "cutpoints_top_tercile": cut,
           "candidates": {k: compare(A, v.to_numpy(), cfg) for k, v in candidates(A, cut).items()},
           "candidates_15min": {k: compare(A, v.to_numpy(), cfg, "rs_15") for k, v in candidates(A, cut).items()},
           "by_volatility": by_level(A, "rv", cut["rv"]), "by_acceleration": by_level(A, "accel", cut["accel"]),
           "by_sweep": {str(k): {"n": int(len(g)), "rs_5": float(g["rs_5"].mean()), "rs_15": float(g["rs_15"].mean()),
                                 "cleared": float(g["cleared"].mean())} for k, g in A.groupby("sweep_recent")},
           "by_gamma_day": _by_label(A, "gamma_day"), "by_gamma_live": _by_label(A, "gamma_live")}
    P = F[F["spread_b"] == "0.01-0.02"].reset_index(drop=True)
    if len(P):
        out["penny_bucket_C5"] = compare(P, candidates(P, cut)["C5_skip_if_C1_C2_or_C3"].to_numpy(), cfg)
    if sanity:
        out["iwm_sanity"] = sanity
    return out


def _day_bars(cfg, day, cal: pd.DataFrame) -> pd.DataFrame | None:
    """ES 1-minute bars of `day` on its front contract, from that month's file only (lean)."""
    p = store.bars_path(cfg, f"{day:%Y-%m}")
    if not p.exists() or day not in cal.index:
        return None
    b = store.read(p)
    b["date"] = calm.session_date(b["ts_open_utc"])
    return b[(b["date"] == day) & (b["instrument_id"] == cal.loc[day, "instrument_id"])]


def features_table(cfg, fills_name: str, save_name: str | None):
    cfg = cfg or load_config()
    F = store.load_derived(fills_name, cfg)
    gx = grades = None
    if store.derived_path(cfg, "gex_daily").exists():
        gx = store.load_derived("gex_daily", cfg).set_index("date")
        grades = gamma_grades(gx, param(cfg, "s10b_gamma_deep_ratio"), param(cfg, "s10b_gamma_min_days"),
                              param(cfg, "gex_pct_lookback"))
    cal = store.load_calendar(cfg).set_index("date")
    parts, sanity = [], {}
    for d, Fd in F.groupby("date"):
        x, s = session_features(cfg, d, Fd, gx, grades, _day_bars(cfg, d, cal))
        parts.append(x)
        sanity[str(d)] = s
    G = pd.concat(parts).sort_index()
    if save_name:
        store.save_derived(G.drop(columns=[c for c in ("ts_last",) if c in G]), save_name, cfg)
    return G, sanity


def run(cfg=None, save: bool = True) -> dict:
    cfg = cfg or load_config()
    G, sanity = features_table(cfg, "study10_fills", "study10_features" if save else None)
    return explore(G, cfg, sanity)


# ---------------------------------------------------------------------------
# Confirmation (fresh sessions): frozen hypotheses H1, H2 (RUNLOG 2026-10-09)
# ---------------------------------------------------------------------------
NEAR_FLIP = ("near-flip positive", "near-flip negative")


def h1_keep(F: pd.DataFrame, cfg) -> np.ndarray:
    """H1: no fills in the first s10b_skip_open_min minutes after the open."""
    cut = calm.hhmm_to_min(cfg["market"]["rth_open"]) + param(cfg, "s10b_skip_open_min")
    return (calm.minutes_of_day_et(pd.Series(F["ts"]).reset_index(drop=True)) >= cut).to_numpy(bool)


def h2_keep(F: pd.DataFrame) -> np.ndarray:
    """H2: quote only while live gamma is near the flip (either side)."""
    return F["gamma_live"].isin(NEAR_FLIP).to_numpy(bool)


def hypothesis_verdict(A: pd.DataFrame, keep, cfg) -> dict:
    """The agreed rule: kept minus all 5-minute RS with a 90% session-bootstrap lower bound > 0 AND a lower
    break-through share; NOT_TESTABLE with too few kept fills or sessions."""
    keep = np.asarray(keep, bool)
    r = compare(A, keep, cfg)
    sessions = int(A.loc[keep, "date"].nunique())
    r["sessions_kept"] = sessions
    if r["n_kept"] < param(cfg, "s10b_min_kept_fills") or sessions < param(cfg, "s10b_min_kept_sessions"):
        r["verdict"] = "NOT_TESTABLE"
    elif r["diff_ci_lo"] > 0 and r["cleared_kept"] < r["cleared_all"]:
        r["verdict"] = "PASS"
    else:
        r["verdict"] = "FAIL"
    return r


def confirm(cfg=None, save: bool = True) -> dict:
    cfg = cfg or load_config()
    G, sanity = features_table(cfg, "study10_fills_confirm", "study10_features_confirm" if save else None)
    A = G[G["spread_b"].isin(cfg["bankroll"]["s10_buckets"])].reset_index(drop=True)
    return {"note": "Study 10b CONFIRMATION on fresh sessions: frozen hypotheses, agreed rule (RUNLOG 2026-10-09)",
            "sessions": sorted(str(d) for d in G["date"].unique()), "fills_in_advancing_buckets": int(len(A)),
            "H1_skip_first_30_min": hypothesis_verdict(A, h1_keep(A, cfg), cfg),
            "H2_near_flip_live_gamma_only": hypothesis_verdict(A, h2_keep(A), cfg),
            "by_gamma_live": _by_label(A, "gamma_live"), "by_gamma_day": _by_label(A, "gamma_day"),
            "iwm_sanity": sanity}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--explore", action="store_true", help="features for the pilot fills and the comparison")
    ap.add_argument("--confirm", action="store_true", help="frozen hypotheses on the fresh confirmation fills")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from src.analysis import to_json
    if a.explore:
        print(to_json(run()))
    elif a.confirm:
        print(to_json(confirm()))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
