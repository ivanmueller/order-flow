"""Study 10b (Matteo 2026-10-09): conditions for quoting IWM options. Step 1, exploration on the pilot fills.

For every pilot fill, using only what was known at the fill (snapshots stamped at or before it):
  IWM price  each cbbo-1m minute, put-call parity on the nearest expiry after the session date:
             F = K + C_mid - P_mid, median over the s10b_parity_strikes strikes with the smallest |C - P|
  rv         trailing realized volatility of IWM, sqrt(sum of the last s10b_vol_window_min squared 1-minute log
             returns) in bp (NaN until the window is full, so the first half hour has no value)
  accel      volatility of the last s10b_accel_half_min minutes / of the half before (> 1 = expanding)
  sweep      the same contract was swept (prints within s10_sweep_ms on >= s10b_sweep_min_venues venues) in the
             s10b_sweep_lookback_s seconds before the fill
  gamma      sign of SPX net gamma for the day (gex_daily: OI published before 09:30, quotes of the day before);
             a market-wide regime, as there is no IWM open interest on disk
Candidate filters (stated before any number is seen): C1 skip the top volatility tercile, C2 skip the top
acceleration tercile, C3 skip after a recent sweep, C4 positive-gamma days only, C5 skip if C1, C2 or C3.
Each is compared with all fills: mean RS kept minus mean RS of all (session-bootstrap CI), break-through share
kept vs all. Exploration only: filters chosen here must be frozen and confirmed on fresh sessions.

  python -m src.study10b --explore
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


def gamma_regime(days, gx: pd.DataFrame) -> list:
    out = []
    for d in days:
        v = gx["net_gex"].get(d, np.nan) if d in gx.index else np.nan
        out.append(None if not np.isfinite(v) else ("positive" if v >= 0 else "negative"))
    return out


def session_features(cfg, day, Fd: pd.DataFrame, gx: pd.DataFrame | None):
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
    out["gamma"] = gamma_regime([day], gx)[0] if gx is not None else None
    r = np.log(px).diff().dropna()
    sanity = {"iwm_minutes": int(len(px)), "iwm_min": float(px.min()) if len(px) else np.nan,
              "iwm_max": float(px.max()) if len(px) else np.nan,
              "median_abs_1min_bp": float((r.abs() * 1e4).median()) if len(r) else np.nan,
              "sweeps": int(len(sw)), "gamma": out["gamma"].iloc[0] if len(out) else None,
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
    return {"C1_skip_top_vol": ~hi_rv.fillna(False),
            "C2_skip_top_accel": ~hi_acc.fillna(False),
            "C3_skip_after_sweep": ~sw,
            "C4_positive_gamma_only": F["gamma"].eq("positive"),
            "C5_skip_if_C1_C2_or_C3": ~(hi_rv.fillna(False) | hi_acc.fillna(False) | sw)}


def by_level(F: pd.DataFrame, col: str, cut: float, horizon_cols=("rs_5", "rs_15")) -> dict:
    lab = np.where(F[col].isna(), "no value (first half hour)", np.where(F[col] >= cut, "top tercile", "lower two"))
    out = {}
    for b, g in F.groupby(lab):
        out[str(b)] = {"n": int(len(g)), **{h: float(g[h].mean()) for h in horizon_cols},
                       "cleared": float(g["cleared"].mean())}
    return out


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
           "by_gamma": {str(k): {"n": int(len(g)), "sessions": int(g["date"].nunique()),
                                 "rs_5": float(g["rs_5"].mean()), "cleared": float(g["cleared"].mean())}
                        for k, g in A.groupby(A["gamma"].fillna("unknown"))}}
    P = F[F["spread_b"] == "0.01-0.02"].reset_index(drop=True)
    if len(P):
        out["penny_bucket_C5"] = compare(P, candidates(P, cut)["C5_skip_if_C1_C2_or_C3"].to_numpy(), cfg)
    if sanity:
        out["iwm_sanity"] = sanity
    return out


def run(cfg=None, save: bool = True) -> dict:
    cfg = cfg or load_config()
    F = store.load_derived("study10_fills", cfg)
    gx = None
    if store.derived_path(cfg, "gex_daily").exists():
        gx = store.load_derived("gex_daily", cfg).set_index("date")
    parts, sanity = [], {}
    for d, Fd in F.groupby("date"):
        x, s = session_features(cfg, d, Fd, gx)
        parts.append(x)
        sanity[str(d)] = s
    G = pd.concat(parts).sort_index()
    if save:
        store.save_derived(G.drop(columns=[c for c in ("ts_last",) if c in G]), "study10_features", cfg)
    return explore(G, cfg, sanity)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--explore", action="store_true", help="features for the pilot fills and the comparison")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not a.explore:
        ap.print_help()
        return
    from src.analysis import to_json
    print(to_json(run()))


if __name__ == "__main__":
    main()
