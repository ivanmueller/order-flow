"""Study 13 (Matteo 2026-10-09): order-flow pattern DISCOVERY on the on-disk ES ticks, with a noise test, then a
single test on untouched days. Data: the Study 6 sessions (in-sample Stage 3 days with tick spans, no roll or half
days); the spans are the ~55 minutes around Stage 2 touches, not whole sessions (a known bias, reported).

Decisions every s13_grid_s seconds inside each span (s13_grid_start .. s13_grid_end ET). At a decision t, using only
prints before t:
  per window W in s13_windows_s (the last, Wr, is the reference):
    imb_W   (buy - sell aggressor volume) / volume          ret_W  last price - first price in the window (ticks)
    big_W   share of volume in prints >= q_big              div_W  imb_W x sign(ret_W) (< 0: flow against price)
    for W < Wr: rate_ratio_W (prints a second vs Wr), absorb_W (volume per tick of range + 1, vs Wr),
                avgsize_W (contracts a print, vs Wr)
  vwap_dist (last - VWAP over Wr, ticks), rv_ref (sum of |price changes| over Wr, ticks),
  burst (signed volume in the last s13_burst_s seconds), tod_min (minutes since 09:30 ET)
Orders go in s13_latency_s after t. Outcomes for each holding time H in s13_horizons_s, long and short, after the
commission (cost_rt_usd), with bid = price - 1 tick after a buy print (price after a sell), ask likewise:
  taker_spec       SPEC rule 5: entry = first print after the order + 1 tick against, exit = first print at or
                   after entry + H - 1 tick against                                            (gating)
  taker_spread     pays the spread only: buy at the ask, sell at the bid                      (report)
  passive_through  SPEC limit rule: a limit at the bid (long) known when the order goes in, filled only if a print
                   trades one tick through it within s13_passive_wait_s; exit H after the fill at the bid; unfilled
                   = no trade                                                                  (gating)
  passive_touch    the same, filled on any print at the limit price                           (report, optimistic)
A trade whose exit print is on another contract than the window's first print, or past the span or grid end, is NaN.

Search (discovery days only): a condition is a feature in its bottom or top s13_quantile (cuts from discovery days);
patterns = single conditions and every pair; each pattern x outcome column is scored by its mean net points, with at
least study13_min_obs decisions on study13_min_days days. NOISE TEST: the whole search is rerun s13_null_reps times
with each day's outcomes circularly shifted against its features (keeps both series' structure, breaks the link);
p = share of noise-search bests >= the real best, per fill model. Validation days: the s13_top_k best patterns per
model, fixed direction and cuts, day-bootstrap mean. FROZEN: up to study13_max_frozen patterns from the gating models
whose model passes the noise test (p <= study13_null_p) and whose validation lower bound is > 0. TEST days (untouched
until then): PASS if the net mean > 0 with a 90% day-bootstrap lower bound > 0 on >= study13_test_min_days days.

  python -m src.study13 --build        # observation table (study13_obs)
  python -m src.study13 --discover     # search + noise test + validation -> study13_frozen.json (no test days read)
  python -m src.study13 --diagnose     # rule-6 checks of the frozen patterns (discovery + validation days only)
  python -m src.study13 --test         # once: the frozen patterns on the test days -> study13_test.json
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd
from scipy import sparse

from src import calendar as calm
from src import stats, store, study6
from src.config import data_path, load_config, param
from src.ingest_futures import covered, load_trades

log = logging.getLogger("study13")
MODELS = ("taker_spec", "taker_spread", "passive_through", "passive_touch")
GATING = ("taker_spec", "passive_through")
DIRS = ("long", "short")
SEC = 1_000_000_000


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
def _sparse_table(a: np.ndarray, fn):
    t = [a]
    k = 1
    while (1 << k) <= len(a):
        prev = t[-1]
        h = 1 << (k - 1)
        t.append(fn(prev[:-h], prev[h:]))
        k += 1
    return t


def _range_query(tab, i0, i1, fn):
    """fn over a[i0:i1] for each pair (i1 > i0); NaN where empty."""
    out = np.full(len(i0), np.nan)
    ok = i1 > i0
    if not ok.any():
        return out
    ln = (i1 - i0)[ok]
    k = np.floor(np.log2(ln)).astype(int)
    a0, a1 = i0[ok], i1[ok] - (1 << k)
    vals = np.empty(ok.sum())
    for kk in np.unique(k):
        m = k == kk
        vals[m] = fn(tab[kk][a0[m]], tab[kk][a1[m]])
    out[ok] = vals
    return out


def feature_matrix(ts, px, sz, sg, grid, windows, tick, big, burst_s, open_ns) -> pd.DataFrame:
    ts, px, sz, sg, grid = (np.asarray(x) for x in (ts, px, sz, sg, grid))
    z = lambda a: np.concatenate([[0.0], np.cumsum(a)])          # noqa: E731
    cv, cs, cn = z(sz), z(sz * sg), z(np.ones(len(ts)))
    cb, cpv = z(np.where(sz >= big, sz, 0.0)), z(px * sz)
    dabs = np.concatenate([[0.0], np.abs(np.diff(px))])
    cad = z(dabs)
    tmax, tmin = _sparse_table(px, np.maximum), _sparse_table(px, np.minimum)
    i1 = np.searchsorted(ts, grid, "left")
    last = np.where(i1 > 0, px[np.maximum(i1 - 1, 0)], np.nan)
    out, ref = {}, {}
    wr = windows[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        for W in windows:
            i0 = np.searchsorted(ts, grid - W * SEC, "left")
            vol, sv, cnt, bv = cv[i1] - cv[i0], cs[i1] - cs[i0], cn[i1] - cn[i0], cb[i1] - cb[i0]
            has = cnt > 0
            first = np.where(has, px[np.minimum(i0, len(px) - 1)], np.nan)
            rng = (_range_query(tmax, i0, i1, np.maximum) - _range_query(tmin, i0, i1, np.minimum)) / tick
            imb = np.where(has, sv / vol, np.nan)
            ret = np.where(has, (last - first) / tick, np.nan)
            out[f"imb_{W}"], out[f"ret_{W}"] = imb, ret
            out[f"big_{W}"] = np.where(has, bv / vol, np.nan)
            out[f"div_{W}"] = imb * np.sign(ret)
            ref[W] = {"rate": cnt / W, "absorb": vol / (rng + 1), "avg": np.where(has, vol / cnt, np.nan),
                      "i0": i0, "vol": vol, "has": has}
        R = ref[wr]
        for W in windows[:-1]:
            out[f"rate_ratio_{W}"] = ref[W]["rate"] / R["rate"]
            out[f"absorb_{W}"] = ref[W]["absorb"] / R["absorb"]
            out[f"avgsize_{W}"] = ref[W]["avg"] / R["avg"]
        i0r = R["i0"]
        vwap = (cpv[i1] - cpv[i0r]) / R["vol"]
        out["vwap_dist"] = np.where(R["has"], (last - vwap) / tick, np.nan)
        # |changes| between consecutive prints both inside [t - Wr, t)
        out["rv_ref"] = np.where(R["has"], (cad[i1] - cad[np.minimum(i0r + 1, len(cad) - 1)]) / tick, np.nan)
        ib = np.searchsorted(ts, grid - int(burst_s * SEC), "left")
        out["burst"] = cs[i1] - cs[ib]
        out["tod_min"] = (grid - open_ns) / (60 * SEC)
    X = pd.DataFrame(out)
    return X.replace([np.inf, -np.inf], np.nan)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------
def target_matrix(ts, px, sg, inst, grid, horizons, latency_s, tick, wait_s, comm, end_ns, look_ns) -> pd.DataFrame:
    ts, px, sg, inst, grid, look_ns = (np.asarray(x) for x in (ts, px, sg, inst, grid, look_ns))
    n, g = len(ts), len(grid)
    ask = np.where(sg > 0, px, px + tick)
    bid = np.where(sg < 0, px, px - tick)
    chg = np.concatenate([[0], np.cumsum(inst[1:] != inst[:-1])]) if n else np.zeros(0, int)
    t_ord = grid + int(latency_s * SEC)
    ie = np.searchsorted(ts, t_ord, "left")
    il = np.searchsorted(ts, look_ns, "left")
    cols = {f"{m}|{d}|{H}": np.full(g, np.nan) for m in MODELS for d in DIRS for H in horizons}

    def exit_ok(ix, i_look):
        return (ix < n) & (ts[np.minimum(ix, n - 1)] < end_ns) & (chg[np.minimum(ix, n - 1)] == chg[np.minimum(i_look, n - 1)])

    ok_e = (ie < n) & (ie > 0)
    ok_e &= ts[np.minimum(ie, n - 1)] < end_ns
    for H in horizons:
        ix = np.searchsorted(ts, ts[np.minimum(ie, n - 1)] + H * SEC, "left")
        ok = ok_e & exit_ok(ix, il)
        e, x = np.minimum(ie, n - 1), np.minimum(ix, n - 1)
        cols[f"taker_spec|long|{H}"] = np.where(ok, (px[x] - tick) - (px[e] + tick) - comm, np.nan)
        cols[f"taker_spec|short|{H}"] = np.where(ok, (px[e] - tick) - (px[x] + tick) - comm, np.nan)
        cols[f"taker_spread|long|{H}"] = np.where(ok, bid[x] - ask[e] - comm, np.nan)
        cols[f"taker_spread|short|{H}"] = np.where(ok, bid[e] - ask[x] - comm, np.nan)
    # resting entries: price known when the order goes in (the last print before it)
    wait = int(wait_s * SEC)
    for k in np.flatnonzero(ok_e):
        a = ie[k]
        b = np.searchsorted(ts, t_ord[k] + wait, "right")
        if b <= a:
            continue
        seg = px[a:b]
        B, A = bid[a - 1], ask[a - 1]
        fills = {("long", "passive_through"): np.flatnonzero(seg <= B - tick + 1e-9),
                 ("long", "passive_touch"): np.flatnonzero(seg <= B + 1e-9),
                 ("short", "passive_through"): np.flatnonzero(seg >= A + tick - 1e-9),
                 ("short", "passive_touch"): np.flatnonzero(seg >= A - 1e-9)}
        for (d, m), j in fills.items():
            if not j.size:
                continue
            tf = ts[a + j[0]]
            for H in horizons:
                ix = int(np.searchsorted(ts, tf + H * SEC, "left"))
                if not (ix < n and ts[ix] < end_ns and chg[ix] == chg[min(il[k], n - 1)]):
                    continue
                cols[f"{m}|{d}|{H}"][k] = (bid[ix] - B if d == "long" else A - ask[ix]) - comm
    return pd.DataFrame(cols)


# ---------------------------------------------------------------------------
# Observation table
# ---------------------------------------------------------------------------
def _et(day, hhmm):
    return pd.Timestamp(f"{day} {hhmm}", tz="America/New_York").tz_convert("UTC")


def session_obs(cfg, day, span_s, span_e, tr: pd.DataFrame) -> pd.DataFrame:
    if tr is None or tr.empty:
        return pd.DataFrame()
    tr = tr.sort_values(["ts_event_utc", "sequence"], kind="stable")
    ts = tr["ts_event_utc"].dt.as_unit("ns").astype("int64").to_numpy()
    px, sz = tr["price"].to_numpy(float), tr["size"].to_numpy(float)
    sg, inst = tr["side"].to_numpy(float), tr["instrument_id"].to_numpy()
    W = param(cfg, "s13_windows_s")
    gs, ge = _et(day, param(cfg, "s13_grid_start")), _et(day, param(cfg, "s13_grid_end"))
    step = param(cfg, "s13_grid_s")
    lo = max(gs, pd.Timestamp(span_s) + pd.Timedelta(seconds=W[-1])).ceil(f"{step}s")
    hi = min(ge, pd.Timestamp(span_e))
    if hi <= lo:
        return pd.DataFrame()
    grid = pd.date_range(lo, hi, freq=f"{step}s", inclusive="left").as_unit("ns").asi8
    X = feature_matrix(ts, px, sz, sg, grid, W, cfg["market"]["tick"], param(cfg, "q_big"),
                       param(cfg, "s13_burst_s"), _et(day, cfg["market"]["rth_open"]).value)
    Y = target_matrix(ts, px, sg, inst, grid, param(cfg, "s13_horizons_s"), param(cfg, "s13_latency_s"),
                      cfg["market"]["tick"], param(cfg, "s13_passive_wait_s"),
                      param(cfg, "cost_rt_usd") / cfg["market"]["point_value"],
                      min(pd.Timestamp(span_e).value, ge.value), grid - W[-1] * SEC)
    O = pd.concat([X, Y], axis=1)
    O.insert(0, "t", pd.to_datetime(grid, utc=True))
    O.insert(0, "date", day)
    return O


def build_obs(cfg, save: bool = True) -> pd.DataFrame:
    parts = []
    days = study6.pilot_days(cfg)
    for day in days:
        assert calm.in_sample(day, cfg)
        for s, e in covered(cfg, day):
            O = session_obs(cfg, day, s, e, load_trades(cfg, day, s, e))
            if len(O):
                parts.append(O)
    O = pd.concat(parts, ignore_index=True).drop_duplicates(["date", "t"]).sort_values(["date", "t"])
    O = O.reset_index(drop=True)
    if save:
        store.save_derived(O, "study13_obs", cfg)
    return O


def feature_names(O: pd.DataFrame) -> list:
    return [c for c in O.columns if c not in ("date", "t") and "|" not in c]


def outcome_names(O: pd.DataFrame) -> list:
    return [c for c in O.columns if "|" in c]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def split_days(days, split) -> tuple[list, list, list]:
    d = sorted(set(days))
    n1 = int(round(len(d) * split[0]))
    n2 = int(round(len(d) * (split[0] + split[1])))
    return d[:n1], d[n1:n2], d[n2:]


def cutpoints(X: pd.DataFrame, feats, q) -> dict:
    return {f: (float(X[f].quantile(q)), float(X[f].quantile(1 - q))) for f in feats}


def _cond(X: pd.DataFrame, cond: str, cuts) -> np.ndarray:
    f, side = cond.split(":")
    lo, hi = cuts[f]
    v = X[f].to_numpy(float)
    return (v <= lo) if side == "lo" else (v >= hi)


def pattern_mask(X: pd.DataFrame, test: str, cuts) -> np.ndarray:
    m = np.ones(len(X), bool)
    for c in test.split("&"):
        m &= _cond(X, c, cuts)
    return m


def tests_list(feats) -> list:
    singles = [f"{f}:{s}" for f in feats for s in ("lo", "hi")]
    pairs = [f"{a}:{sa}&{b}:{sb}" for i, a in enumerate(feats) for b in feats[i + 1:]
             for sa in ("lo", "hi") for sb in ("lo", "hi")]
    return singles + pairs


def mask_matrix(X: pd.DataFrame, tests, cuts) -> sparse.csr_matrix:
    base = {f"{f}:{s}": _cond(X, f"{f}:{s}", cuts) for f in cuts for s in ("lo", "hi")}
    idx, ptr = [], [0]
    for t in tests:
        parts = t.split("&")
        m = base[parts[0]].copy()
        for p in parts[1:]:
            m &= base[p]
        nz = np.flatnonzero(m)
        idx.append(nz)
        ptr.append(ptr[-1] + len(nz))
    ind = np.concatenate(idx) if idx else np.zeros(0, int)
    return sparse.csr_matrix((np.ones(len(ind), np.float32), ind, np.array(ptr)), shape=(len(tests), len(X)))


def _shift_index(day_codes: np.ndarray, rng) -> np.ndarray:
    perm = np.arange(len(day_codes))
    starts = np.flatnonzero(np.r_[True, day_codes[1:] != day_codes[:-1]])
    ends = np.r_[starts[1:], len(day_codes)]
    for s, e in zip(starts, ends):
        L = e - s
        if L > 1:
            perm[s:e] = s + (np.arange(L) + rng.integers(1, L)) % L
    return perm


OBJECTIVES = ("per_trade", "per_day", "t_stat")


def _slots(M, O: pd.DataFrame, cols, latency_s: float) -> np.ndarray:
    """Tests x columns: one-position-at-a-time trade slots, approximated by the number of (H + latency)-second
    blocks of each day that hold at least one signal (each decision is its own slot without a time column)."""
    if "t" in O:
        t = pd.to_datetime(O["t"], utc=True)
        secs = (t - t.groupby(O["date"]).transform("min")).dt.total_seconds().to_numpy()
    else:
        secs = None
    K = {}
    for H in sorted({float(c.split("|")[2]) for c in cols}):
        b = np.arange(len(O)) if secs is None else np.floor(secs / (H + latency_s)).astype(np.int64)
        code = pd.factorize(pd.Series(list(zip(O["date"], b))))[0]
        B = sparse.csr_matrix((np.ones(len(O)), (np.arange(len(O)), code)), shape=(len(O), code.max() + 1))
        K[H] = np.asarray(((M @ B) > 0).sum(axis=1)).ravel().astype(float)
    return np.column_stack([K[float(c.split("|")[2])] for c in cols])


def _objectives(S, S2, N, slots, n_days) -> dict:
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = S / N
        sd = np.sqrt(np.maximum(S2 / N - mean ** 2, 0.0))
        return {"per_trade": mean,                                     # net points a trade
                "per_day": mean * slots / n_days,                      # net points a day, one position at a time
                "t_stat": mean / sd * np.sqrt(np.maximum(slots, 1.0))}  # consistency on non-overlapping slots


def _best_of(score, ok, cols) -> dict:
    out = {}
    for m in MODELS:
        ci = [j for j, c in enumerate(cols) if c.split("|")[0] == m]
        if not ci:
            continue
        sub = np.where(ok[:, ci], score[:, ci], -np.inf)
        k = np.unravel_index(np.argmax(sub), sub.shape)
        out[m] = (float(sub[k]), int(k[0]), ci[k[1]])
    return out


def search(O: pd.DataFrame, feats, cols, q, min_obs, min_days, reps, seed, top_k, latency_s: float = 0.0) -> dict:
    """Every pattern x outcome column scored three ways (per trade, per day one position at a time, t-stat); each
    objective's best per fill model is compared with the best of the same search on circularly shifted outcomes."""
    O = O.sort_values(["date"] + (["t"] if "t" in O else []), kind="stable").reset_index(drop=True)
    cuts = cutpoints(O, feats, q)
    tests = tests_list(feats)
    M = mask_matrix(O, tests, cuts)
    Y = O[cols].to_numpy(float)
    V = np.isfinite(Y).astype(np.float64)
    Y0 = np.where(np.isfinite(Y), Y, 0.0)
    codes = pd.factorize(O["date"])[0]
    n_days = int(codes.max() + 1)
    D = sparse.csr_matrix((np.ones(len(O)), (np.arange(len(O)), codes)), shape=(len(O), n_days))
    ndays = np.asarray(((M @ D) > 0).sum(axis=1)).ravel()
    slots = _slots(M, O, cols, latency_s)

    def scored(Yx, Vx):
        S, S2, N = np.asarray(M @ Yx), np.asarray(M @ (Yx * Yx)), np.asarray(M @ Vx)
        return _objectives(S, S2, N, slots, n_days), N

    sc, N = scored(Y0, V)
    ok = (N >= min_obs) & (ndays >= min_days)[:, None]
    best = {o: _best_of(sc[o], ok, cols) for o in OBJECTIVES}
    rng = np.random.default_rng(seed)
    null = {o: {m: [] for m in best[o]} for o in OBJECTIVES}
    for _ in range(reps):
        p = _shift_index(codes, rng)
        scp, Np = scored(Y0[p], V[p])
        okp = (Np >= min_obs) & (ndays >= min_days)[:, None]
        for o in OBJECTIVES:
            for m, v in _best_of(scp[o], okp, cols).items():
                null[o][m].append(v[0])
    out = {"cuts": cuts, "n_tests": len(tests), "n_columns": len(cols), "best": {}, "null": {}, "top": {}}

    def item(i, j):
        return {"test": tests[i], "column": cols[j], "mean": float(sc["per_trade"][i, j]),
                "per_day": float(sc["per_day"][i, j]), "t_stat": float(sc["t_stat"][i, j]),
                "n": int(N[i, j]), "days": int(ndays[i]), "slots_per_day": float(slots[i, j] / n_days)}
    for o in OBJECTIVES:
        out["best"][o], out["null"][o], out["top"][o] = {}, {}, {}
        for m, (v, ti, cj) in best[o].items():
            out["best"][o][m] = item(ti, cj)
            nb = np.array(null[o][m]) if null[o][m] else np.array([np.nan])
            out["null"][o][m] = {"best_score": v,
                                 "p": float((1 + np.sum(nb >= v)) / (1 + len(null[o][m]))) if null[o][m] else np.nan,
                                 "noise_best_median": float(np.nanmedian(nb)),
                                 "noise_best_q95": float(np.nanquantile(nb, 0.95)), "reps": len(null[o][m])}
            ci = [j for j, c in enumerate(cols) if c.split("|")[0] == m]
            sub = np.where(ok[:, ci], sc[o][:, ci], -np.inf)
            order = np.argsort(sub, axis=None)[::-1][:top_k]
            out["top"][o][m] = [item(i, ci[j]) for i, j in (np.unravel_index(k, sub.shape) for k in order)
                                if np.isfinite(sub[i, j])]
    return out


# ---------------------------------------------------------------------------
# Validation, freeze, test
# ---------------------------------------------------------------------------
def evaluate(O: pd.DataFrame, test: str, column: str, cuts, cfg) -> dict:
    m = pattern_mask(O, test, cuts)
    x = O.loc[m, ["date", column]].dropna().rename(columns={column: "pnl"})
    if x.empty:
        return {"n": 0, "days": 0, "mean": np.nan, "lo": np.nan, "hi": np.nan}
    b = stats.day_bootstrap_mean(x, "pnl", param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"),
                                 param(cfg, "ci_level"))
    return {"n": b["n"], "days": b["days"], "mean": b["mean"], "lo": b["lo"], "hi": b["hi"],
            "usd_per_trade": b["mean"] * cfg["market"]["point_value"]}


def _paths(cfg):
    d = cfg["data"].get("derived_dir", "derived")
    return data_path(cfg, d, "study13_frozen.json"), data_path(cfg, d, "study13_test.json")


def discover(cfg, O: pd.DataFrame, reps: int | None = None) -> dict:
    fz, tz = _paths(cfg)
    if tz.exists():
        raise RuntimeError("the test days were already used (study13_test.json); discovery cannot be rerun")
    days_d, days_v, days_t = split_days(O["date"].unique(), param(cfg, "s13_split"))
    Od = O[O["date"].isin(days_d)]
    Ov = O[O["date"].isin(days_v)]
    g = cfg["gates"]
    feats, cols = feature_names(O), outcome_names(O)
    r = search(Od, feats, cols, param(cfg, "s13_quantile"), g["study13_min_obs"], g["study13_min_days"],
               param(cfg, "s13_null_reps") if reps is None else reps, param(cfg, "s13_seed"), param(cfg, "s13_top_k"),
               param(cfg, "s13_latency_s"))
    lat = param(cfg, "s13_latency_s")
    val, seen = {}, {}
    for o, by_m in r["top"].items():
        val[o] = {}
        for m, lst in by_m.items():
            rows = []
            for c in lst:
                key = (c["test"], c["column"])
                if key not in seen:
                    ev = evaluate(Ov, c["test"], c["column"], r["cuts"], cfg)
                    H = float(c["column"].split("|")[2])
                    no = non_overlap(Ov, pattern_mask(Ov, c["test"], r["cuts"]), c["column"], H + lat)
                    ev["one_at_a_time_mean"] = float(no[c["column"]].mean()) if len(no) else np.nan
                    ev["one_at_a_time_trades_per_day"] = float(len(no) / max(1, Ov["date"].nunique()))
                    ev["per_day"] = (ev["one_at_a_time_mean"] * ev["one_at_a_time_trades_per_day"]
                                     if len(no) else np.nan)
                    seen[key] = ev
                rows.append({**c, "validation": seen[key]})
            val[o][m] = rows
    cand, keys = [], set()
    for o in OBJECTIVES:
        for m in GATING:
            if not (r["null"][o].get(m, {}).get("p", 1.0) <= g["study13_null_p"]):
                continue
            for c in val[o].get(m, []):
                v = c["validation"]
                k = (c["test"], c["column"])
                if k in keys:
                    continue
                if v["days"] >= g["study13_test_min_days"] and v["mean"] > 0 and v["lo"] > 0 and v["per_day"] > 0:
                    cand.append({"model": m, "objective": o, **c})
                    keys.add(k)
    cand.sort(key=lambda c: -c["validation"]["per_day"])                # most net points a day, one at a time
    frozen = cand[: g["study13_max_frozen"]]
    out = {"note": "Study 13 discovery (in sample, Study 6 tick spans); test days NOT read",
           "days": {"discovery": [str(days_d[0]), str(days_d[-1]), len(days_d)],
                    "validation": [str(days_v[0]), str(days_v[-1]), len(days_v)] if days_v else [],
                    "test_untouched": len(days_t)},
           "observations": {"discovery": int(len(Od)), "validation": int(len(Ov))},
           "features": feats, "n_patterns": r["n_tests"], "n_outcome_columns": r["n_columns"],
           "objectives": list(OBJECTIVES), "best_by_objective_and_model": r["best"], "noise_test": r["null"],
           "top_with_validation": val,
           "frozen": frozen, "gating_models": list(GATING)}
    fz.parent.mkdir(parents=True, exist_ok=True)
    stale = tz.with_name("study13_diagnose.json")
    if stale.exists():
        stale.unlink()                                                   # diagnostics belong to the old frozen set
    fz.write_text(json.dumps({"cuts": r["cuts"], "frozen": frozen, "test_days": [str(d) for d in days_t]},
                             default=float, indent=1))
    return out


def non_overlap(O: pd.DataFrame, mask, column: str, hold_s: float) -> pd.DataFrame:
    """One position at a time: the next decision only after the previous one's hold has passed."""
    x = O.loc[mask, ["date", "t", column]].dropna().sort_values(["date", "t"])
    keep, last = [], {}
    for i, r in zip(x.index, x.itertuples(index=False)):
        d, t = r[0], r[1]
        if d in last and t < last[d]:
            continue
        keep.append(i)
        last[d] = t + pd.Timedelta(seconds=hold_s)
    return x.loc[keep]


def run_test(cfg, O: pd.DataFrame) -> dict:
    fz, tz = _paths(cfg)
    if not fz.exists():
        raise FileNotFoundError("run --discover first")
    F = json.loads(fz.read_text())
    dz = tz.with_name("study13_diagnose.json")
    if not dz.exists():
        raise FileNotFoundError("run --diagnose first: only patterns that pass the rule-6 checks go to the test days")
    ok = {(p["test"], p["column"]) for p in json.loads(dz.read_text())["patterns"] if p["eligible_for_test"]}
    F["frozen"] = [c for c in F["frozen"] if (c["test"], c["column"]) in ok]
    days_t = [pd.Timestamp(d).date() for d in F["test_days"]]
    Ot = O[O["date"].isin(days_t)]
    g = cfg["gates"]
    res = []
    for c in F["frozen"]:
        ev = evaluate(Ot, c["test"], c["column"], F["cuts"], cfg)
        H = float(c["column"].split("|")[2])
        no = non_overlap(Ot, pattern_mask(Ot, c["test"], F["cuts"]), c["column"], H + param(cfg, "s13_latency_s"))
        verdict = "PASS" if (ev["days"] >= g["study13_test_min_days"] and ev["mean"] > 0 and ev["lo"] > 0) else "FAIL"
        # descriptive drift controls (reported, not part of the pre-registered verdict)
        conds = c["test"].split("&")
        tod = [x for x in conds if x.startswith("tod_min")]
        base = Ot.loc[pattern_mask(Ot, tod[0], F["cuts"]) if tod else np.ones(len(Ot), bool), c["column"]].mean()
        x = Ot.loc[pattern_mask(Ot, c["test"], F["cuts"]), ["date", c["column"]]].dropna()
        res.append({**{k: c[k] for k in ("model", "test", "column")}, "discovery_mean": c["mean"],
                    "validation": c["validation"], "test": ev, "verdict": verdict,
                    "one_at_a_time": {"trades_per_day": float(len(no) / max(1, no["date"].nunique())) if len(no) else 0.0,
                                      "mean": float(no[c["column"]].mean()) if len(no) else np.nan},
                    "drift_control": {"same_time_of_day_without_flow": float(base),
                                      "flow_condition_adds": float(ev["mean"] - base) if np.isfinite(ev["mean"]) else np.nan,
                                      "unconditional_long_same_hold": float(Ot[c["column"]].mean()),
                                      "by_day": _day_stats(x, c["column"])}})
    out = {"note": "Study 13 TEST (frozen patterns, untouched days, run once)", "test_days": len(days_t),
           "observations": int(len(Ot)), "results": res,
           "verdict": "PASS" if any(r["verdict"] == "PASS" for r in res) else ("NOTHING FROZEN" if not res else "FAIL")}
    tz.write_text(json.dumps(out, default=float, indent=1))
    return out


# ---------------------------------------------------------------------------
# Rule-6 diagnostics before the test (discovery + validation days only)
# ---------------------------------------------------------------------------
def clean_flags(O: pd.DataFrame, touches: pd.DataFrame, hold_s: float, post_min: float, latency_s: float) -> np.ndarray:
    """True where the decision's tick window exists because of a touch already in the past: some touch t0 <= t with
    the whole trade (t + latency + hold) inside t0 + post_min. Decisions before a span's touch (or in a span
    extended by a later touch) are kept in the data only because price later reached a level: selection on the
    future."""
    if O.empty or touches.empty:
        return np.zeros(len(O), bool)
    tch = touches[["date", "t0"]].assign(t0=pd.to_datetime(touches["t0"], utc=True).dt.as_unit("ns")).sort_values("t0")
    O = O.reset_index(drop=True)
    left = O[["date", "t"]].assign(t=pd.to_datetime(O["t"], utc=True).dt.as_unit("ns")).reset_index().sort_values("t")
    out = np.zeros(len(O), bool)
    end_need = pd.to_timedelta(hold_s + latency_s, unit="s")
    post = pd.Timedelta(minutes=post_min)
    # the latest touch at or before t is the one with the most room left
    m = pd.merge_asof(left, tch, left_on="t", right_on="t0", by="date", direction="backward")
    ok = m["t0"].notna() & ((m["t"] + end_need) <= (m["t0"] + post))
    out[m.loc[ok, "index"].to_numpy()] = True
    return out


def _day_stats(x: pd.DataFrame, col: str) -> dict:
    per = x.groupby("date")[col].mean().sort_values(ascending=False)
    if per.empty:
        return {}
    return {"days": int(len(per)), "days_positive": int((per > 0).sum()), "best_day": float(per.iloc[0]),
            "mean_of_day_means": float(per.mean()),
            "mean_of_day_means_without_best_3": float(per.iloc[3:].mean()) if len(per) > 3 else np.nan}


def diagnose(cfg, O: pd.DataFrame, touches: pd.DataFrame) -> dict:
    """Is a frozen pattern order flow, or drift / time of day / sample selection? Never reads the test days."""
    fz, _ = _paths(cfg)
    F = json.loads(fz.read_text())
    test_days = {pd.Timestamp(d).date() for d in F["test_days"]}
    days_d, days_v, _ = split_days(O["date"].unique(), param(cfg, "s13_split"))
    assert not (set(days_d) | set(days_v)) & test_days
    parts = {"discovery": O[O["date"].isin(days_d)], "validation": O[O["date"].isin(days_v)]}
    lat, post = param(cfg, "s13_latency_s"), cfg["data"]["trades_post_min"]
    out = {"note": "Study 13 rule-6 diagnostics (discovery + validation only; test days not read)", "patterns": []}
    # the same outcome with no condition, by time of day: what drift alone gives
    cols = sorted({c["column"] for c in F["frozen"]})
    for c in cols:
        rows = {}
        for name, X in parts.items():
            tb = pd.cut(X["tod_min"], [0, 30, 60, 120, 240, 400])
            rows[name] = {"all_decisions": float(X[c].mean()),
                          "by_minutes_after_open": {str(k): float(g[c].mean()) for k, g in X.groupby(tb, observed=True)},
                          "short_side_all": float(X[c.replace("|long|", "|short|")].mean())}
        out[f"unconditional::{c}"] = rows
    for f in F["frozen"]:
        H = float(f["column"].split("|")[2])
        r = {"model": f["model"], "test": f["test"], "column": f["column"]}
        conds = f["test"].split("&")
        for name, X in parts.items():
            m = pattern_mask(X, f["test"], F["cuts"])
            col = f["column"]
            x = X.loc[m, ["date", "t", col]].dropna()
            # the flow condition's own contribution: the pattern vs the same time-of-day condition alone
            tod = [c for c in conds if c.startswith("tod_min")]
            base = X.loc[pattern_mask(X, tod[0], F["cuts"]) if tod else np.ones(len(X), bool), col].mean()
            clean = clean_flags(X.loc[m], touches, H, post, lat)
            xc = X.loc[m][clean][["date", col]].dropna()
            no = non_overlap(X, m, col, H + lat)
            r[name] = {"n": int(len(x)), "mean": float(x[col].mean()) if len(x) else np.nan,
                       "time_of_day_condition_alone": float(base),
                       "flow_condition_adds": float(x[col].mean() - base) if len(x) else np.nan,
                       "by_day": _day_stats(x, col),
                       "one_position_at_a_time": {"n": int(len(no)), "mean": float(no[col].mean()) if len(no) else np.nan},
                       "no_future_selection": {"n": int(len(xc)), "days": int(xc["date"].nunique()),
                                               "mean": float(xc[col].mean()) if len(xc) else np.nan}}
        v, d = r["validation"], r["discovery"]
        checks = {"flow_adds_discovery": bool(d["flow_condition_adds"] > 0),
                  "flow_adds_validation": bool(v["flow_condition_adds"] > 0),
                  "no_future_selection_positive": bool(v["no_future_selection"]["days"] >= cfg["gates"]["study13_test_min_days"]
                                                       and v["no_future_selection"]["mean"] > 0),
                  "positive_without_best_3_days": bool(v["by_day"].get("mean_of_day_means_without_best_3", np.nan) > 0),
                  "positive_one_at_a_time": bool(v["one_position_at_a_time"]["mean"] > 0)}
        r["checks"] = checks
        r["eligible_for_test"] = all(checks.values())
        out["patterns"].append(r)
    _, tz = _paths(cfg)
    dz = tz.with_name("study13_diagnose.json")
    dz.write_text(json.dumps(out, default=float, indent=1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--diagnose", action="store_true", help="rule-6 checks on the frozen patterns (no test days)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    from src.analysis import to_json
    if a.build:
        O = build_obs(cfg)
        print(f"observations {len(O):,} on {O['date'].nunique()} days ({O['date'].min()} .. {O['date'].max()}); "
              f"features {len(feature_names(O))}; outcome columns {len(outcome_names(O))}")
    elif a.discover:
        print(to_json(discover(cfg, store.load_derived("study13_obs", cfg))))
    elif a.diagnose:
        print(to_json(diagnose(cfg, store.load_derived("study13_obs", cfg), store.load_derived("touches", cfg))))
    elif a.test:
        print(to_json(run_test(cfg, store.load_derived("study13_obs", cfg))))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
