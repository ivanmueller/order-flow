"""Study 14: short pumped Binance USD-M perpetuals, searched over every key setting (STUDY14.md). Research only.

Grid (config s14_*): pump window W x size P x volume V x entry E x hold H x stop S x target T x squeeze filter Q x
funding filter F = 38,880 combinations, scored on the discovery years (2020-01..2023-12), with a placebo noise test
(the whole search on random non-event hours of the same coins and months), validation (2024-01..2025-09), grid
stability, at most 4 frozen, diagnostics, and a sealed holdout (2025-10..2026-09) run once on "run the holdout".

  python -m src.study14 --events        # pump events per (W, P, V); writes the 5-min / metrics download list ($0)
  python -m src.study14 --discover      # walks, squeeze model, search, placebo x100, validation, stability, freeze
  python -m src.study14 --diagnose      # frozen combinations: placebo, tails, worst periods, 1-minute re-walk
  python -m src.study14 --risk          # descriptive bankroll of the frozen combinations (discovery + validation)
  GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.study14 --events --holdout, then --holdout   # once, on "run the holdout"

Walk (conservative): short at the open after the signal, 0.10% worse; 5-minute bars; stop before target in a bar;
a bar opening through the stop exits at its open; stops and time exits 0.10% worse; targets only on a trade below;
0.05% fee a side; actual funding strictly inside the trade; a delisted coin exits at its last close; loss capped at
the 1x collateral.
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import time

import numpy as np
import pandas as pd

from src import crypto_data as cd
from src.config import data_path, load_config, param

log = logging.getLogger("study14")
HOUR = 3_600 * 10**9
M5 = 300 * 10**9
DAY = 24 * HOUR
WEEK = 7 * DAY
ORIGIN = pd.Timestamp("2019-12-30", tz="UTC").value          # a Monday: week index origin
N_WEEKS = 420                                                # covers 2019-12..2027-12
OBJECTIVES = {"per_trade": "mean", "per_month": "per_month", "t_stat": "t_stat"}
FEATURES = ["oi_chg_24h", "px_chg_24h", "oi_to_vol", "top_ls", "taker_ratio", "funding", "days_listed"]


# ---- events ---------------------------------------------------------------------------------------
def pump_signals(h: pd.DataFrame, W: int, lookback_h: int) -> tuple[np.ndarray, np.ndarray]:
    """Per bar k: close-to-close return over W bars, and quote volume over the W bars / the median W-bar quote
    volume over the lookback_h windows ending before this one starts (NaN without that much history or when the
    window does not start and end on real bars, e.g. across a delist-relist gap)."""
    c, qv, real = h["close"].to_numpy(float), h["qv"].to_numpy(float), h["real"].to_numpy(bool)
    n = len(c)
    ret, ratio = np.full(n, np.nan), np.full(n, np.nan)
    if n <= W:
        return ret, ratio
    ret[W:] = np.where(real[W:] & real[:-W], c[W:] / c[:-W] - 1, np.nan)
    qw = pd.Series(qv).rolling(W).sum()
    base = qw.rolling(lookback_h, min_periods=lookback_h).median().shift(W).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(base > 0, qw.to_numpy() / base, np.nan)
    return ret, ratio


def first_with_cooldown(ok: np.ndarray, cooldown_h: int) -> np.ndarray:
    out, last = [], -10**12
    for k in np.flatnonzero(ok):
        if k - last >= cooldown_h:
            out.append(k)
            last = k
    return np.array(out, int)


def detect_events(h: pd.DataFrame, W: int, P: float, V: float, lookback_h: int, cooldown_h: int,
                  signals=None) -> np.ndarray:
    """Signal bars k (event time = close of bar k): return over W >= P and volume ratio >= V; one per cooldown."""
    ret, ratio = signals if signals is not None else pump_signals(h, W, lookback_h)
    with np.errstate(invalid="ignore"):
        ok = (ret >= P) & (ratio >= V) & h["real"].to_numpy(bool)
    return first_with_cooldown(ok, cooldown_h)


# ---- entries --------------------------------------------------------------------------------------
def entry_times(h: pd.DataFrame, ks: np.ndarray, rule: str, red_hour_h: int, red_day_d: int, drop_h: int) -> np.ndarray:
    """Entry time (ns) for each signal bar k, or -1 when the rule does not fire inside its window."""
    ts = h["ts"].to_numpy("int64")
    o, c, hi = (h[x].to_numpy(float) for x in ("open", "close", "high"))
    n, ks = len(ts), np.asarray(ks, int)
    out = np.full(len(ks), -1, "int64")
    if len(ks) == 0:
        return out
    if rule == "next_hour":
        j = ks + 1
        ok = j < n
        out[ok] = ts[j[ok]]
        return out
    if rule == "red_hour" or rule.startswith("drop_"):
        span = red_hour_h if rule == "red_hour" else drop_h
        idx = ks[:, None] + np.arange(0, span + 1)                         # column 0 is the signal bar
        inside = idx < n
        ix = np.minimum(idx, n - 1)
        if rule == "red_hour":
            cond = c[ix] < o[ix]
        else:
            d = int(rule.split("_")[1]) / 100
            cond = c[ix] <= (1 - d) * np.maximum.accumulate(hi[ix], axis=1)
        cond[:, 0] = False
        cond &= inside
        found = cond.any(1)
        j = idx[np.arange(len(ks)), cond.argmax(1)] + 1
        ok = found & (j < n)
        out[ok] = ts[j[ok]]
        return out
    if rule == "red_day":
        sig = ts[ks] + HOUR
        d0 = (sig // DAY) * DAY
        for i, s in enumerate(d0):
            for m in range(red_day_d):
                D = s + m * DAY
                p = (D - ts[0]) // HOUR
                if p < 0 or p + 24 >= n:
                    continue
                if c[p + 23] < o[p]:
                    out[i] = ts[p + 24]
                    break
        return out
    raise ValueError(rule)


# ---- the short walk -------------------------------------------------------------------------------
def walk(b: dict, entry_ts, last_ts: int, hold_bars, stops, targets, slip, fee, funding_ts, funding_rate,
         label_rise, label_bars, max_filled, chunk_cells: int = 4_000_000) -> dict:
    """Short 1x at each entry (bar open) on the bar grid b (ts/open/high/low/close/real). Returns per entry:
    r and exit_ts [hold, stop, target], valid, squeeze label, last settled funding rate at entry."""
    ts = np.asarray(b["ts"], "int64")
    o, hh, lo, c = (np.asarray(b[k], float) for k in ("open", "high", "low", "close"))
    real = np.asarray(b["real"], bool)
    entry_ts = np.asarray(entry_ts, "int64")
    N, n = len(entry_ts), len(ts)
    nH, nS, nT = len(hold_bars), len(stops), len(targets)
    out = {"r": np.full((N, nH, nS, nT), np.nan), "exit_ts": np.full((N, nH, nS, nT), -1, "int64"),
           "valid": np.zeros(N, bool), "label": np.zeros(N, bool), "fund_at_entry": np.full(N, np.nan),
           "p0": np.full(N, np.nan)}
    fts = np.asarray(funding_ts, "int64")
    frt = np.asarray(funding_rate, float)
    if len(fts):
        k = np.searchsorted(ts, fts, "left") - 1
        pf = np.where(k >= 0, c[np.clip(k, 0, max(n - 1, 0))], np.nan) if n else np.full(len(fts), np.nan)
        cum = np.concatenate([[0.0], np.cumsum(np.nan_to_num(frt * pf))])
        j = np.searchsorted(fts, entry_ts, "right") - 1
        out["fund_at_entry"] = np.where(j >= 0, frt[np.clip(j, 0, None)], np.nan)
    else:
        cum = np.array([0.0])

    def F(t):
        return cum[np.searchsorted(fts, t, "left")] if len(fts) else np.zeros(np.shape(t))
    if N == 0 or n == 0:
        return out
    L = max(max(hold_bars), label_bars) + 1
    last_idx = min(int(np.searchsorted(ts, last_ts, "left")) - 1, n - 1)
    step = max(1, chunk_cells // L)
    for a in range(0, N, step):
        sl = slice(a, min(N, a + step))
        e = entry_ts[sl]
        i0 = np.searchsorted(ts, e, "left")
        hit0 = (i0 < n) & (ts[np.minimum(i0, n - 1)] == e) & (i0 <= last_idx)
        i0c = np.minimum(i0, n - 1)
        rel_last = last_idx - i0c                                           # last usable bar, relative
        covered = (i0c + max(hold_bars) <= n - 1) | (last_ts <= ts[-1] + (ts[1] - ts[0] if n > 1 else 0))
        idx = np.minimum(i0c[:, None] + np.arange(L), n - 1)
        inside = np.arange(L)[None, :] <= rel_last[:, None]
        span = np.minimum(max(hold_bars), rel_last)
        win = np.arange(L)[None, :] <= span[:, None]
        filled = ((~real[idx]) & win).sum(1) / np.maximum(win.sum(1), 1)
        valid = hit0 & covered & (filled <= max_filled)
        p0 = o[i0c]
        pe = p0 * (1 - slip)
        H2 = np.where(inside, hh[idx], -np.inf)
        L2 = np.where(inside, lo[idx], np.inf)
        O2 = o[idx]
        lab_cols = np.arange(L)[None, :] < label_bars
        out["label"][sl] = (np.where(lab_cols, H2, -np.inf).max(1) >= p0 * (1 + label_rise)) & valid
        out["p0"][sl] = p0
        rows = np.arange(len(e))
        fe = F(e + 1)
        fs = []
        for S in stops:
            lvl = pe * (1 + S)
            hit = H2 >= lvl[:, None]
            f = np.where(hit.any(1), hit.argmax(1), L)
            px = np.maximum(O2[rows, np.minimum(f, L - 1)], lvl) * (1 + slip)
            fs.append((f, px))
        ft = []
        for T in targets:
            if T is None:
                ft.append((np.full(len(e), L), np.full(len(e), np.nan)))
                continue
            lvl = pe * (1 - T)
            hit = L2 < lvl[:, None]
            ft.append((np.where(hit.any(1), hit.argmax(1), L), lvl))
        for hi_, nb in enumerate(hold_bars):
            timed = nb <= rel_last
            end_i = np.where(timed, nb, rel_last + 1)
            end_px = np.where(timed, o[np.minimum(i0c + nb, n - 1)], c[max(last_idx, 0)]) * (1 + slip)
            end_ts = np.where(timed, ts[np.minimum(i0c + nb, n - 1)], ts[max(last_idx, 0)] + (ts[1] - ts[0] if n > 1 else 0))
            for si, (f_s, px_s) in enumerate(fs):
                for ti, (f_t, px_t) in enumerate(ft):
                    stop = (f_s < end_i) & (f_s <= f_t)
                    tgt = ~stop & (f_t < end_i)
                    x = np.where(stop, px_s, np.where(tgt, px_t, end_px))
                    xi = np.where(stop, f_s, f_t)
                    xts = np.where(stop | tgt, ts[np.minimum(i0c + np.minimum(xi, L - 1), n - 1)], end_ts)
                    fund = F(xts) - fe
                    r = 1 - x / pe - fee * (1 + x / pe) + fund / pe
                    r = np.maximum(r, -1.0)
                    out["r"][sl, hi_, si, ti] = np.where(valid, r, np.nan)
                    out["exit_ts"][sl, hi_, si, ti] = np.where(valid, xts, -1)
        out["valid"][sl] = valid
    return out


# ---- squeeze score --------------------------------------------------------------------------------
def metric_features(m: pd.DataFrame, e, qv24) -> dict:
    """Open interest and long/short features from metrics rows stamped strictly before entry (stale > 1 h: NaN)."""
    e = np.asarray(e, "int64")
    n = len(e)
    out = {k: np.full(n, np.nan) for k in ("oi_chg_24h", "oi_to_vol", "top_ls", "taker_ratio")}
    if m is None or len(m) == 0:
        return out
    m = m.sort_values("ts")
    ts = m["ts"].to_numpy("int64")
    oi, tl, tk = (m[k].to_numpy(float) for k in ("oi_value", "top_ls", "taker_ratio"))
    i = np.searchsorted(ts, e, "left") - 1
    j = np.searchsorted(ts, e - DAY, "left") - 1
    ok = (i >= 0) & (e - ts[np.clip(i, 0, None)] <= HOUR)
    ok24 = ok & (j >= 0) & ((e - DAY) - ts[np.clip(j, 0, None)] <= HOUR)
    ic, jc = np.clip(i, 0, None), np.clip(j, 0, None)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["oi_chg_24h"] = np.where(ok24, oi[ic] / oi[jc] - 1, np.nan)
        out["oi_to_vol"] = np.where(ok & (np.asarray(qv24) > 0), oi[ic] / np.asarray(qv24, float), np.nan)
        out["top_ls"] = np.where(ok, tl[ic], np.nan)
        cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(tk))])
        cnt = np.concatenate([[0], np.cumsum(np.isfinite(tk))])
        a = np.where(j >= 0, j + 1, 0)
        num, den = cs[ic + 1] - cs[a], cnt[ic + 1] - cnt[a]
        out["taker_ratio"] = np.where(ok & (den > 0), num / np.maximum(den, 1), np.nan)
    return out


def hourly_features(h: pd.DataFrame, e, funding_at_entry) -> dict:
    ts = h["ts"].to_numpy("int64")
    c, qv = h["close"].to_numpy(float), h["qv"].to_numpy(float)
    e = np.asarray(e, "int64")
    p = np.searchsorted(ts, e, "left") - 1                                  # the bar that closes at entry
    ok = p >= 24
    pc = np.clip(p, 24, None)
    cq = np.concatenate([[0.0], np.cumsum(qv)])
    first = ts[h["real"].to_numpy(bool)][0] if h["real"].any() else ts[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        return {"px_chg_24h": np.where(ok, c[np.minimum(pc, len(c) - 1)] / c[np.minimum(pc - 24, len(c) - 1)] - 1, np.nan),
                "qv24": np.where(ok, cq[np.minimum(pc + 1, len(cq) - 1)] - cq[np.minimum(pc - 23, len(cq) - 1)], np.nan),
                "funding": np.asarray(funding_at_entry, float), "days_listed": (e - first) / DAY}


def fit_logit(X: pd.DataFrame, y, ridge: float = 1e-3, iters: int = 50) -> dict:
    """Logistic regression by IRLS on complete rows; features winsorized at their 1/99% and standardized."""
    X = X.astype(float)
    ok = X.notna().all(axis=1).to_numpy() & np.isfinite(np.asarray(y, float))
    Xo, yo = X[ok], np.asarray(y, float)[ok]
    lo, hi = Xo.quantile(0.01), Xo.quantile(0.99)
    Z = Xo.clip(lo, hi, axis=1)
    mu, sd = Z.mean(), Z.std(ddof=0).replace(0, 1.0)
    A = np.column_stack([np.ones(len(Z)), ((Z - mu) / sd).to_numpy()])
    w = np.zeros(A.shape[1])
    pen = np.full(A.shape[1], ridge)
    pen[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-A @ w))
        g = A.T @ (yo - p) - pen * w
        Hm = (A * (p * (1 - p))[:, None]).T @ A + np.diag(pen)
        step = np.linalg.solve(Hm, g)
        w += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return {"cols": list(X.columns), "lo": lo.tolist(), "hi": hi.tolist(), "mu": mu.tolist(), "sd": sd.tolist(),
            "intercept": float(w[0]), "coef": w[1:].tolist(), "n": int(len(yo)), "base_rate": float(yo.mean())}


def score(model: dict, X: pd.DataFrame) -> np.ndarray:
    X = X[model["cols"]].astype(float)
    Z = X.clip(pd.Series(model["lo"], X.columns), pd.Series(model["hi"], X.columns), axis=1)
    Z = (Z - pd.Series(model["mu"], X.columns)) / pd.Series(model["sd"], X.columns)
    eta = model["intercept"] + Z.to_numpy() @ np.asarray(model["coef"])
    return 1 / (1 + np.exp(-eta))                                            # NaN wherever a feature is missing


# ---- combination statistics ------------------------------------------------------------------------
class Accumulator:
    """Sufficient statistics per (rep, group (W,P,V,E), Q, F, outcome column): n, sum, sum of squares, weeks."""

    F_KEYS = ("any", "nonneg")

    def __init__(self, n_groups, n_cols, qcuts, n_weeks=N_WEEKS, reps=1):
        Q, Fn = len(qcuts), 2
        self.qcuts, self.n_weeks = list(qcuts), n_weeks
        self.n = np.zeros((reps, n_groups, Q, Fn))
        self.s = np.zeros((reps, n_groups, Q, Fn, n_cols))
        self.ss = np.zeros_like(self.s)
        self.wk = np.zeros((reps, n_groups, Q, Fn, n_weeks), bool)

    def add(self, rep: int, rows: pd.DataFrame, cols):
        if len(rows) == 0:
            return
        g = rows["g"].to_numpy(int)
        wk = np.clip(rows["week"].to_numpy(int), 0, self.n_weeks - 1)
        sc, fu = rows["score"].to_numpy(float), rows["fund"].to_numpy(float)
        Y = rows[list(cols)].to_numpy(float)
        Y2 = Y * Y
        for qi, cut in enumerate(self.qcuts):
            kq = np.ones(len(g), bool) if cut is None else (np.isnan(sc) | (sc <= cut))
            for fi in range(2):
                m = kq if fi == 0 else kq & (fu >= 0)
                if not m.any():
                    continue
                np.add.at(self.n[rep, :, qi, fi], g[m], 1)
                np.add.at(self.s[rep, :, qi, fi], g[m], Y[m])
                np.add.at(self.ss[rep, :, qi, fi], g[m], Y2[m])
                self.wk[rep, g[m], qi, fi, wk[m]] = True

    def stats(self, rep: int, n_months: float) -> dict:
        n = self.n[rep][..., None] * np.ones(self.s.shape[-1])
        s, ss = self.s[rep], self.ss[rep]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(n > 0, s / n, np.nan)
            var = np.where(n > 1, (ss - s * s / np.maximum(n, 1)) / (n - 1), np.nan)
            sd = np.sqrt(np.maximum(var, 0))
            t = np.where(sd > 0, mean / sd * np.sqrt(n), np.nan)
        weeks = self.wk[rep].sum(-1)[..., None] * np.ones(self.s.shape[-1])
        return {"n": n, "weeks": weeks, "mean": mean, "sd": sd, "per_month": s / n_months, "t_stat": t}

    def table(self, rep: int, n_months: float) -> pd.DataFrame:
        st = self.stats(rep, n_months)
        G, Q, Fn, C = self.s.shape[1:]
        g, q, f, c = np.meshgrid(np.arange(G), np.arange(Q), np.arange(Fn), np.arange(C), indexing="ij")
        d = {"g": g.ravel(), "q": q.ravel(), "f": f.ravel(), "c": c.ravel()}
        d.update({k: v.ravel() for k, v in st.items()})
        return pd.DataFrame(d)


def best_of(T: pd.DataFrame, min_trades: int, min_weeks: int) -> dict:
    ok = (T["n"] >= min_trades) & (T["weeks"] >= min_weeks)
    out = {}
    for obj, col in OBJECTIVES.items():
        v = T.loc[ok, col].dropna()
        out[obj] = None if v.empty else v.idxmax()
    return out


def best_values(st: dict, min_trades: int, min_weeks: int) -> dict:
    ok = (st["n"] >= min_trades) & (st["weeks"] >= min_weeks)
    return {obj: float(np.nanmax(np.where(ok, st[col], -np.inf))) if ok.any() else np.nan
            for obj, col in OBJECTIVES.items()}


def null_p(real: float, placebo_bests) -> float:
    a = np.asarray(placebo_bests, float)
    a = a[np.isfinite(a)]
    return float((1 + np.sum(a >= real)) / (1 + len(a)))


# ---- placebo, periods, grid ---------------------------------------------------------------------------
def placebo_times(rng, events, all_events, hours, gap_h: int) -> np.ndarray:
    """For each event, a random hour of the same calendar month at least gap_h from every real event (-1 if none)."""
    events, hours = np.asarray(events, "int64"), np.sort(np.asarray(hours, "int64"))
    allev = np.sort(np.asarray(all_events, "int64"))
    if len(hours) == 0:
        return np.full(len(events), -1, "int64")
    k = np.searchsorted(allev, hours)
    lo = np.where(k > 0, hours - allev[np.clip(k - 1, 0, None)], np.iinfo("int64").max) if len(allev) else np.full(len(hours), np.iinfo("int64").max)
    hi = np.where(k < len(allev), allev[np.clip(k, 0, max(len(allev) - 1, 0))] - hours, np.iinfo("int64").max) if len(allev) else lo
    far = np.minimum(lo, hi) >= gap_h * HOUR
    hm = pd.to_datetime(hours, utc=True).strftime("%Y-%m").to_numpy()
    em = pd.to_datetime(events, utc=True).strftime("%Y-%m").to_numpy()
    out = np.full(len(events), -1, "int64")
    for mth in np.unique(em):
        cand = hours[(hm == mth) & far]
        sel = em == mth
        if len(cand):
            out[sel] = rng.choice(cand, size=sel.sum())
    return out


def reach_ns(cfg) -> int:
    return (param(cfg, "s14_red_day_window_d") + 1 + max(param(cfg, "s14_holds_d"))) * DAY


def bounds(cfg) -> dict:
    t = lambda s: pd.Timestamp(s, tz="UTC").value                      # noqa: E731
    return {"train_end": t(param(cfg, "s14_train_end")) + DAY, "val_end": t(param(cfg, "s14_validation_end")) + DAY,
            "holdout": t(param(cfg, "s14_holdout_start"))}


def period_of(t, cfg) -> np.ndarray:
    """train / val / holdout by signal time; None when a trade from it could cross into the next period."""
    b, r = bounds(cfg), reach_ns(cfg)
    t = np.asarray(t, "int64")
    ve = min(b["val_end"], b["holdout"])
    out = np.full(len(t), None, object)
    out[(t + r < b["train_end"])] = "train"
    out[(t >= b["train_end"]) & (t + r < ve)] = "val"
    out[t >= b["holdout"]] = "holdout"
    return out


def _order(vals):
    """Ordered settings: numbers ascending, None (no target / no filter) last."""
    return sorted([v for v in vals if v is not None]) + [v for v in vals if v is None]


class Grid:
    ORDERED = ("W", "P", "V", "H", "S", "T", "Q")

    def __init__(self, cfg):
        self.W = list(param(cfg, "s14_windows_h"))
        self.P = list(param(cfg, "s14_pump"))
        self.V = list(param(cfg, "s14_volume_mult"))
        self.E = list(param(cfg, "s14_entries"))
        self.H = list(param(cfg, "s14_holds_d"))
        self.S = list(param(cfg, "s14_stops"))
        self.T = list(param(cfg, "s14_targets"))
        self.Q = list(param(cfg, "s14_squeeze_skip"))
        self.F = list(param(cfg, "s14_funding_filter"))
        self.wpv = list(itertools.product(self.W, self.P, self.V))
        self.groups = [(w, p, v, e) for (w, p, v) in self.wpv for e in self.E]
        self.cols = list(itertools.product(self.H, self.S, self.T))
        self.col_names = [f"{h}|{s}|{t}" for h, s, t in self.cols]
        self.orders = {k: _order(getattr(self, k)) for k in self.ORDERED}

    def g_index(self, wpv_i: int, e_i: int) -> int:
        return wpv_i * len(self.E) + e_i

    def combo(self, g, q, f, c) -> dict:
        W, P, V, E = self.groups[g]
        H, S, T = self.cols[c]
        return {"W": W, "P": P, "V": V, "E": E, "H": H, "S": S, "T": T, "Q": self.Q[q], "F": self.F[f]}

    def locate(self, d: dict) -> tuple:
        g = self.groups.index((d["W"], d["P"], d["V"], d["E"]))
        return g, self.Q.index(d["Q"]), self.F.index(d["F"]), self.cols.index((d["H"], d["S"], d["T"]))

    def neighbours(self, d: dict) -> list[dict]:
        out = []
        for k in self.ORDERED:
            o = self.orders[k]
            i = o.index(d[k])
            for j in (i - 1, i + 1):
                if 0 <= j < len(o):
                    out.append({**d, k: o[j]})
        return out

    @staticmethod
    def name(d: dict) -> str:
        return (f"W{d['W']}h P{d['P']} V{d['V']}x {d['E']} H{d['H']}d S{d['S']} T{d['T']} "
                f"Q{d['Q']} F{d['F']}")


# ---- risk report -------------------------------------------------------------------------------------
def risk_path(tr: pd.DataFrame, stop: float, share: float, max_open: int, start: float) -> dict:
    """Chronological account: each trade's notional = share x realized equity / stop distance; at most max_open
    trades at once (later signals skipped). Drawdown on realized equity."""
    tr = tr.sort_values("entry_ts")
    eq, peak, mdd = start, start, 0.0
    open_, taken, skipped, path = [], 0, 0, []

    def settle(upto):
        nonlocal eq, peak, mdd
        open_.sort()
        while open_ and open_[0][0] <= upto:
            x, pnl = open_.pop(0)
            eq += pnl
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak if peak > 0 else 0)
            path.append((x, eq))
    for e, x, r in tr[["entry_ts", "exit_ts", "r"]].itertuples(index=False):
        settle(e)
        if len(open_) >= max_open or eq <= 0:
            skipped += 1
            continue
        open_.append((x, share * eq / stop * r))
        taken += 1
    settle(np.iinfo("int64").max)
    return {"final": eq, "taken": taken, "skipped": skipped, "max_dd": mdd, "path": path}


# ---- per-coin rows -----------------------------------------------------------------------------------
class Coin:
    """One coin's sealed data: hourly grid, 5-min (or 1-min) grid, funding, metrics."""

    def __init__(self, cfg, sym, include_holdout=False, interval="5m"):
        self.sym = sym
        self.h = cd.load_bars(cfg, sym, "1h", include_holdout)
        real = self.h["real"].to_numpy(bool) if len(self.h) else np.array([], bool)
        self.last_ts = int(self.h["ts"].to_numpy("int64")[real][-1] + HOUR) if real.any() else 0
        self.b = cd.load_bars(cfg, sym, interval, include_holdout)
        self.f = cd.load_funding(cfg, sym, include_holdout)
        self.m = cd.load_metrics(cfg, sym, include_holdout)
        self.step = {"5m": M5, "1m": 60 * 10**9}[interval]


def coin_rows(cfg, grid: Grid, coin: Coin, ev: pd.DataFrame, model=None) -> pd.DataFrame:
    """ev: one coin's events (wpv_i, k = signal bar, t, period, rep). Rows = event x entry rule with the 48
    outcomes (r and exit time), squeeze features and score, funding at entry, week."""
    if len(ev) == 0 or len(coin.b) == 0:
        return pd.DataFrame()
    h = coin.h
    E_ts = np.stack([entry_times(h, ev["k"].to_numpy(int), E, param(cfg, "s14_red_hour_window_h"),
                                 param(cfg, "s14_red_day_window_d"), param(cfg, "s14_drop_window_h"))
                     for E in grid.E], 1)
    ei = np.tile(np.arange(len(grid.E)), len(ev))
    src = np.repeat(np.arange(len(ev)), len(grid.E))
    et = E_ts.ravel()
    keep = et >= 0
    ei, src, et = ei[keep], src[keep], et[keep]
    if len(et) == 0:
        return pd.DataFrame()
    uniq, inv = np.unique(et, return_inverse=True)
    per_day = DAY // coin.step
    b = {k: coin.b[k].to_numpy() for k in ("ts", "open", "high", "low", "close", "real")}
    w = walk(b, uniq, coin.last_ts, [H * per_day for H in grid.H], grid.S, grid.T, param(cfg, "s14_slippage"),
             param(cfg, "s14_fee"), coin.f["ts"].to_numpy("int64"), coin.f["rate"].to_numpy(float),
             param(cfg, "s14_squeeze_label_rise"), param(cfg, "s14_squeeze_label_days") * per_day,
             param(cfg, "s14_max_filled_share"))
    hf = hourly_features(h, uniq, w["fund_at_entry"])
    mf = metric_features(coin.m, uniq, hf["qv24"])
    feats = pd.DataFrame({**{k: hf[k] for k in ("px_chg_24h", "funding", "days_listed")}, **mf})[FEATURES]
    sc = score(model, feats) if model is not None else np.full(len(uniq), np.nan)
    ok = w["valid"][inv]
    evr = ev.iloc[src]
    d = {"sym": coin.sym, "rep": evr["rep"].to_numpy(int), "wpv": evr["wpv"].to_numpy(int), "e_i": ei,
         "g": evr["wpv"].to_numpy(int) * len(grid.E) + ei, "t": evr["t"].to_numpy("int64"), "e": et,
         "period": evr["period"].to_numpy(object), "week": (et - ORIGIN) // WEEK,
         "fund": w["fund_at_entry"][inv], "score": sc[inv], "label": w["label"][inv]}
    for k in FEATURES:
        d[k] = feats[k].to_numpy()[inv]
    R = w["r"].reshape(len(uniq), -1)[inv]
    X = w["exit_ts"].reshape(len(uniq), -1)[inv]
    out = pd.DataFrame(d)
    rc = pd.DataFrame(R, columns=grid.col_names)
    xc = pd.DataFrame(X, columns=["x|" + c for c in grid.col_names])
    return pd.concat([out, rc, xc], axis=1)[ok].reset_index(drop=True)


# ---- events step ---------------------------------------------------------------------------------------
def out_path(cfg, name):
    p = data_path(cfg, "derived", "crypto", name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def default_jobs() -> int:
    import os
    return max(1, min((os.cpu_count() or 2) - 1, 8))


def _worker_init():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s[%(process)d] %(message)s")


def _run(fn, tasks, jobs: int):
    """fn over tasks, in order; jobs > 1 uses that many processes (results identical to a serial run)."""
    if jobs <= 1 or len(tasks) <= 1:
        return [fn(t) for t in tasks]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=jobs, initializer=_worker_init) as ex:
        return list(ex.map(fn, tasks, chunksize=max(1, len(tasks) // (jobs * 8))))


def _events_coin(task):
    cfg, s, holdout = task
    grid = Grid(cfg)
    lb, cool = param(cfg, "s14_volume_lookback_d") * 24, param(cfg, "s14_cooldown_d") * 24
    h = cd.load_bars(cfg, s, "1h", include_holdout=holdout)
    if len(h) == 0:
        return [], None
    real = h["real"].to_numpy(bool)
    end = int(h["ts"].to_numpy("int64")[real][-1] + HOUR)
    sig = {W: pump_signals(h, W, lb) for W in grid.W}
    rows = []
    for wi, (W, P, V) in enumerate(grid.wpv):
        k = detect_events(h, W, P, V, lb, cool, signals=sig[W])
        if len(k):
            rows.append(pd.DataFrame({"sym": s, "wpv": wi, "W": W, "P": P, "V": V, "k": k,
                                      "t": h["ts"].to_numpy("int64")[k] + HOUR}))
    return rows, end


def events(cfg, holdout=False, jobs: int = 1) -> dict:
    syms = cd.study_symbols(cd.inventory_cache(cfg), cfg)
    log.info("events: %d coins on %d processes", len(syms), jobs)
    res = _run(_events_coin, [(cfg, s, holdout) for s in syms], jobs)
    rows = [r for rr, _ in res for r in rr]
    ends = [e for _, e in res if e is not None]
    data_end, n_coins = (max(ends) if ends else 0), len(ends)
    ev = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["sym", "wpv", "W", "P", "V", "k", "t"])
    ev["period"] = period_of(ev["t"].to_numpy("int64"), cfg)
    if holdout:
        ev.loc[(ev["period"] == "holdout") & (ev["t"] + reach_ns(cfg) > data_end), "period"] = None
        ev = ev[ev["period"] == "holdout"]
    else:
        ev.loc[ev["period"] == "holdout", "period"] = None               # never kept in a research run
    ev.to_parquet(out_path(cfg, "study14_events_holdout.parquet" if holdout else "study14_events.parquet"))
    use = ev[ev["period"].notna()]
    m5, met = set(), set()
    for s, t in use[["sym", "t"]].drop_duplicates().itertuples(index=False):
        mo = pd.Timestamp(t, tz="UTC").tz_localize(None).to_period("M")
        m5 |= {(s, str(mo)), (s, str(mo + 1))}
        first = mo.to_timestamp()
        days = pd.date_range(first - pd.Timedelta(days=1), (mo + 1).to_timestamp() + pd.Timedelta(days=7), freq="D")
        met |= {(s, str(d.date())) for d in days}
    p = cd.needs_path(cfg)
    need = json.loads(p.read_text()) if p.exists() else {}
    need["m5"] = sorted(set(map(tuple, need.get("m5", []))) | m5)
    need["metrics"] = sorted(set(map(tuple, need.get("metrics", []))) | met)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(need))
    counts = (use.groupby(["W", "P", "V", "period"]).size().unstack(fill_value=0)
              if len(use) else pd.DataFrame())
    return {"coins_with_hourly_data": n_coins, "events_kept": int(len(use)),
            "dropped_boundary_or_holdout": int(ev["period"].isna().sum()),
            "distinct_coin_hours": int(use[["sym", "t"]].drop_duplicates().shape[0]),
            "coins_with_events": int(use["sym"].nunique()),
            "per_W_P_V": {f"W{w} P{p_} V{v}": r.to_dict() for (w, p_, v), r in counts.iterrows()},
            "to_download": {"m5_files": len(m5), "metric_days": len(met)}, "data_end_utc": str(pd.Timestamp(data_end, tz="UTC")),
            "next": "python -m src.crypto_data --event-data" + (" --holdout" if holdout else "")}


# ---- discovery -----------------------------------------------------------------------------------------
def _months(cfg, period):
    b = {"train": (pd.Timestamp("2020-01-01"), pd.Timestamp(param(cfg, "s14_train_end"))),
         "val": (pd.Timestamp(param(cfg, "s14_train_end")) + pd.Timedelta(days=1), pd.Timestamp(param(cfg, "s14_validation_end"))),
         "holdout": (pd.Timestamp(param(cfg, "s14_holdout_start")), pd.Timestamp(param(cfg, "s14_holdout_start")) + pd.DateOffset(months=12) - pd.Timedelta(days=1))}[period]
    return len(pd.period_range(b[0], b[1], freq="M"))


def week_lb(rows: pd.DataFrame, col: str, cfg) -> dict:
    from src.stats import day_bootstrap_mean
    d = rows[["week", col]].dropna()
    return day_bootstrap_mean(d, col, param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"),
                              param(cfg, "ci_level"), date_col="week")


def _qcuts(grid, model_scores):
    s = np.asarray(model_scores, float)
    s = s[np.isfinite(s)]
    return [None if q is None else (float(np.quantile(s, q)) if len(s) else None) for q in grid.Q]


def _mask(rows, grid, d, qcuts):
    g, q, f, _ = grid.locate(d)
    m = rows["g"].to_numpy() == g
    if qcuts[q] is not None:
        m &= rows["score"].isna().to_numpy() | (rows["score"].to_numpy() <= qcuts[q])
    if grid.F[f] == "nonneg":
        m &= rows["fund"].to_numpy() >= 0
    return m


def combo_rows(rows, grid, d, qcuts) -> pd.DataFrame:
    """Trades of one combination: entry, exit, return, week, month."""
    sub = rows[_mask(rows, grid, d, qcuts)]
    col = f"{d['H']}|{d['S']}|{d['T']}"
    return pd.DataFrame({"sym": sub["sym"], "entry_ts": sub["e"], "exit_ts": sub["x|" + col], "r": sub[col],
                         "week": sub["week"], "period": sub["period"],
                         "month": pd.to_datetime(sub["e"], utc=True).dt.strftime("%Y-%m")}).dropna(subset=["r"])


def trade_signature(cr: pd.DataFrame) -> int:
    """Identity of a combination's trade list: the same (coin, entry, exit, return) set means the same strategy."""
    t = cr[["sym", "entry_ts", "exit_ts", "r"]].copy()
    t["r"] = t["r"].round(10)
    return hash(tuple(sorted(map(tuple, t.itertuples(index=False)))))


def _real_coin(task):
    cfg, s, e_ = task
    coin = Coin(cfg, s)
    if len(coin.b) == 0:
        return s, None
    return s, coin_rows(cfg, Grid(cfg), coin, e_)


def _placebo_chunk(task):
    """A list of coins' placebo searches into one accumulator (summed across processes afterwards)."""
    cfg, items, model, qcuts, reps = task
    grid = Grid(cfg)
    gap = param(cfg, "s14_placebo_gap_h")
    lb = param(cfg, "s14_volume_lookback_d") * 24 + max(grid.W)
    acc = Accumulator(len(grid.groups), len(grid.cols), qcuts, reps=reps)
    t0 = time.time()
    for j, (ci_, s, e_, allev_s) in enumerate(items):
        coin = Coin(cfg, s)
        if len(coin.b) == 0:
            continue
        h = coin.h
        hts = h["ts"].to_numpy("int64")
        okh = h["real"].to_numpy(bool) & (np.arange(len(h)) >= lb)
        sig = hts[okh] + HOUR
        sig = sig[period_of(sig, cfg) == "train"]
        taus = np.unique(e_["t"].to_numpy("int64"))
        blocks = []
        for rep in range(reps):
            rng = np.random.default_rng([param(cfg, "s14_seed"), rep, ci_])
            pt = placebo_times(rng, taus, allev_s, sig, gap)
            pe = e_.copy()
            pe["t"] = pe["t"].map(dict(zip(taus, pt))).astype("int64")
            pe = pe[pe["t"] >= 0]
            pe["k"] = ((pe["t"] - HOUR - hts[0]) // HOUR).astype(int)
            pe["rep"] = rep
            blocks.append(pe)
        rows = coin_rows(cfg, grid, coin, pd.concat(blocks, ignore_index=True), model)
        for rep, rr in rows.groupby("rep"):
            acc.add(int(rep), rr, grid.col_names)
        if j % 10 == 0:
            log.info("placebo: %d of %d coins in this process (%.0f s)", j + 1, len(items), time.time() - t0)
    return acc.n, acc.s, acc.ss, acc.wk


def discover(cfg, jobs: int = 1) -> dict:
    t_start = time.time()
    grid = Grid(cfg)
    ev = pd.read_parquet(out_path(cfg, "study14_events.parquet"))
    ev = ev[ev["period"].isin(["train", "val"])].copy()
    ev["rep"] = -1
    G, C = len(grid.groups), len(grid.cols)
    min_tr, min_wk = cfg["gates"]["study14_min_trades"], cfg["gates"]["study14_min_weeks"]
    reps = param(cfg, "s14_null_reps")
    # pass 1: real events
    log.info("real rows: %d coins on %d processes", ev["sym"].nunique(), jobs)
    res = _run(_real_coin, [(cfg, s, e_) for s, e_ in ev.groupby("sym")], jobs)
    parts = [r for _, r in res if r is not None and len(r)]
    missing = [s for s, r in res if r is None]
    R = pd.concat(parts, ignore_index=True)
    tr = R[R["period"] == "train"].drop_duplicates(["sym", "e"])
    model = fit_logit(tr[FEATURES], tr["label"].astype(float))
    R["score"] = score(model, R[FEATURES])
    qcuts = _qcuts(grid, R.loc[R["period"] == "train"].drop_duplicates(["sym", "e"])["score"])
    R.to_parquet(out_path(cfg, "study14_rows.parquet"))
    n_tr, n_va = _months(cfg, "train"), _months(cfg, "val")
    acc = Accumulator(G, C, qcuts, reps=1)
    acc.add(0, R[R["period"] == "train"], grid.col_names)
    T = acc.table(0, n_tr)
    accv = Accumulator(G, C, qcuts, reps=1)
    accv.add(0, R[R["period"] == "val"], grid.col_names)
    Tv = accv.table(0, n_va)
    real_best = best_values(acc.stats(0, n_tr), min_tr, min_wk)
    # pass 2: placebo searches (discovery years only), each rep a full rerun of the grid; coins split over
    # processes (balanced by event count); each coin's random hours depend only on (seed, rep, coin index)
    evt = ev[ev["period"] == "train"]
    syms = sorted(evt["sym"].unique())
    ev_all = pd.read_parquet(out_path(cfg, "study14_events.parquet"))          # every real event, kept or not
    allev = ev_all.groupby("sym")["t"].apply(lambda x: np.unique(x.to_numpy("int64"))).to_dict()
    items = [(ci_, s, evt[evt["sym"] == s], allev[s]) for ci_, s in enumerate(syms)]
    n_chunks = max(1, min(jobs, len(items)))
    order = sorted(range(len(items)), key=lambda i: -len(items[i][2]))
    chunks = [[items[i] for i in order[k::n_chunks]] for k in range(n_chunks)]
    log.info("placebo: %d reps x %d coins on %d processes", reps, len(items), n_chunks)
    pacc = Accumulator(G, C, qcuts, reps=reps)
    for n_, s_, ss_, wk_ in _run(_placebo_chunk, [(cfg, ch, model, qcuts, reps) for ch in chunks], n_chunks):
        pacc.n += n_
        pacc.s += s_
        pacc.ss += ss_
        pacc.wk |= wk_
    np.savez_compressed(out_path(cfg, "study14_placebo.npz"), n=pacc.n, s=pacc.s, ss=pacc.ss,
                        weeks=pacc.wk.sum(-1))
    pb = {o: [] for o in OBJECTIVES}
    for rep in range(reps):
        bv = best_values(pacc.stats(rep, n_tr), min_tr, min_wk)
        for o in OBJECTIVES:
            pb[o].append(bv[o])
    noise = {o: {"real_best": real_best[o], "placebo_median": float(np.nanmedian(pb[o])),
                 "placebo_95": float(np.nanquantile(pb[o], 0.95)), "p": null_p(real_best[o], pb[o])}
             for o in OBJECTIVES}
    # validation, stability, freeze
    ok = (T["n"] >= min_tr) & (T["weeks"] >= min_wk)
    key = {tuple(grid.locate(grid.combo(*r))): i for i, r in enumerate(T[["g", "q", "f", "c"]].to_numpy())}
    top, seen = {}, {}
    for o, col in OBJECTIVES.items():
        lst = []
        for i in T[ok].sort_values(col, ascending=False).head(param(cfg, "s14_top_k")).index:
            r = T.loc[i]
            d = grid.combo(int(r.g), int(r.q), int(r.f), int(r.c))
            nm = Grid.name(d)
            if nm not in seen:
                nb = [key[grid.locate(x)] for x in grid.neighbours(d)]
                nbe = [j for j in nb if ok.iloc[j]]
                stab = float(np.mean([T.loc[j, "mean"] > 0 for j in nbe])) if nbe else 0.0
                v = Tv.loc[i]
                cr = combo_rows(R[R["period"] == "val"], grid, d, qcuts)
                bt = week_lb(cr, "r", cfg) if len(cr) else {"lo": np.nan}
                seen[nm] = {"combo": d, "disc": {k: float(r[k]) for k in ("n", "weeks", "mean", "per_month", "t_stat")},
                            "val": {"n": float(v["n"]), "weeks": float(v["weeks"]), "mean": float(v["mean"]),
                                    "per_month": float(v["per_month"]), "lo": bt["lo"], "hi": bt.get("hi")},
                            "stability": stab, "neighbours_eligible": len(nbe)}
            lst.append(nm)
        top[o] = lst
    g = cfg["gates"]
    cand = []
    for o in OBJECTIVES:
        if noise[o]["p"] > g["study14_null_p"]:
            continue
        for nm in top[o]:
            x = seen[nm]
            if (x["val"]["n"] >= g["study14_val_min_trades"] and np.isfinite(x["val"]["lo"]) and x["val"]["lo"] > 0
                    and x["stability"] >= g["study14_neighbour_share"]):
                cand.append(nm)
    frozen, dup, sigs = [], [], {}
    for nm in sorted(sorted(set(cand)), key=lambda nm: -seen[nm]["val"]["per_month"]):
        if len(frozen) >= g["study14_max_frozen"]:
            break
        cr = combo_rows(R, grid, seen[nm]["combo"], qcuts)
        sig = trade_signature(cr)
        if sig in sigs:                                  # the same trades as a frozen one: not a new strategy
            dup.append({"name": nm, "same_trades_as": sigs[sig]})
            continue
        sigs[sig] = nm
        frozen.append({"name": nm, **seen[nm]})
    # 1-minute download list for the frozen combinations' trades
    m1 = set()
    for fz in frozen:
        cr = combo_rows(R, grid, fz["combo"], qcuts)
        for s, a, b_ in cr[["sym", "entry_ts", "exit_ts"]].itertuples(index=False):
            for mo in pd.period_range(pd.Timestamp(a, tz="UTC").tz_localize(None), pd.Timestamp(b_, tz="UTC").tz_localize(None), freq="M"):
                m1.add((s, str(mo)))
    p = cd.needs_path(cfg)
    need = json.loads(p.read_text()) if p.exists() else {}
    need["m1"] = sorted(m1)
    p.write_text(json.dumps(need))
    out = {"study": 14, "step": "discover", "rows": {"train": int((R["period"] == "train").sum()),
                                                   "val": int((R["period"] == "val").sum())},
           "coins_without_5m_data": missing, "squeeze_model": model, "q_cuts": qcuts,
           "squeeze_label_rate_train": float(tr["label"].mean()),
           "eligible_combinations": int(ok.sum()), "of": int(len(T)),
           "unconditional": _uncond(R, grid), "noise_test": noise, "top": top, "candidates": seen,
           "frozen": frozen, "skipped_identical_trades": dup, "minute_files_needed": len(m1), "seconds": round(time.time() - t_start)}
    out_path(cfg, "study14_discover.json").write_text(json.dumps(out, default=_js, indent=1))
    out.pop("candidates")
    return out


def _uncond(R, grid):
    """Reference rows: next-hour entry, no filters, per hold and stop with no target, discovery and validation."""
    e = grid.E.index("next_hour") if "next_hour" in grid.E else 0
    sub = R[R["e_i"] == e]
    out = {}
    for per in ("train", "val"):
        x = sub[sub["period"] == per]
        out[per] = {f"H{h} S{s}": round(float(x[f"{h}|{s}|None"].mean()), 4) for h in grid.H for s in grid.S
                    if f"{h}|{s}|None" in x}
    return out


def _js(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return str(o)


def _load_discover(cfg):
    p = out_path(cfg, "study14_discover.json")
    if not p.exists():
        raise SystemExit("run `python -m src.study14 --discover` first")
    return json.loads(p.read_text())


def _combo_from_json(d):
    return {k: d[k] for k in ("W", "P", "V", "E", "H", "S", "T", "Q", "F")}


# ---- diagnose ------------------------------------------------------------------------------------------
def diagnose(cfg) -> dict:
    D = _load_discover(cfg)
    grid = Grid(cfg)
    R = pd.read_parquet(out_path(cfg, "study14_rows.parquet"))
    qcuts = D["q_cuts"]
    pl = np.load(out_path(cfg, "study14_placebo.npz"))
    g_ = cfg["gates"]
    res = []
    for fz in D["frozen"]:
        d = _combo_from_json(fz["combo"])
        gi, qi, fi, ci = grid.locate(d)
        cr = combo_rows(R, grid, d, qcuts)
        with np.errstate(invalid="ignore", divide="ignore"):
            pm = pl["s"][:, gi, qi, fi, ci] / pl["n"][:, gi, qi, fi]
        disc_mean = fz["disc"]["mean"]
        x = {"name": fz["name"], "placebo_same_combo": {"real": disc_mean, "placebo_mean": float(np.nanmean(pm)),
                                                         "placebo_95": float(np.nanquantile(pm, 0.95)),
                                                         "p": null_p(disc_mean, pm)}}
        for per in ("train", "val"):
            c = cr[cr["period"] == per].sort_values("r")
            k = int(np.ceil(0.05 * len(c)))
            x[per] = {"n": len(c), "mean": float(c["r"].mean()),
                      "mean_without_best_5pct": float(c["r"].iloc[: len(c) - k].mean()) if len(c) > k else np.nan,
                      "worst_trade": float(c["r"].min()), "worst_week": float(c.groupby("week")["r"].sum().min()),
                      "worst_month": float(c.groupby("month")["r"].sum().min()),
                      "share_stopped_or_capped": float((c["r"] <= -d["S"]).mean())}
        x["minute_rewalk"] = _rewalk_1m(cfg, grid, d, cr)
        mw = x["minute_rewalk"]
        x["checks"] = {
            "placebo_same_combo": x["placebo_same_combo"]["p"] <= g_["study14_null_p"],
            "positive_without_best_5pct": bool(x["train"]["mean_without_best_5pct"] > 0
                                               and x["val"]["mean_without_best_5pct"] > 0),
            "minute_rewalk_positive": bool(mw.get("coverage", 0) >= 0.95 and mw.get("mean", -1) > 0)}
        x["eligible_for_holdout"] = all(x["checks"].values())
        res.append(x)
    out = {"study": 14, "step": "diagnose", "frozen": res,
           "eligible": [r["name"] for r in res if r["eligible_for_holdout"]]}
    out_path(cfg, "study14_diagnose.json").write_text(json.dumps(out, default=_js, indent=1))
    return out


def _rewalk_1m(cfg, grid, d, cr) -> dict:
    """The combination's trades re-walked on 1-minute bars (same entries; missing 1-minute data reported)."""
    vals, n_ok = [], 0
    for s, g in cr.groupby("sym"):
        coin = Coin(cfg, s, interval="1m")
        if len(coin.b) == 0:
            continue
        w = walk({k: coin.b[k].to_numpy() for k in ("ts", "open", "high", "low", "close", "real")},
                 g["entry_ts"].to_numpy("int64"), coin.last_ts, [d["H"] * 1440], [d["S"]], [d["T"]],
                 param(cfg, "s14_slippage"), param(cfg, "s14_fee"), coin.f["ts"].to_numpy("int64"),
                 coin.f["rate"].to_numpy(float), param(cfg, "s14_squeeze_label_rise"),
                 param(cfg, "s14_squeeze_label_days") * 1440, param(cfg, "s14_max_filled_share"))
        r = w["r"][:, 0, 0, 0]
        n_ok += int(np.isfinite(r).sum())
        vals.append(pd.DataFrame({"r1": r, "r5": g["r"].to_numpy(), "period": g["period"].to_numpy()}))
    if not vals:
        return {"coverage": 0.0, "note": "no 1-minute data: run `python -m src.crypto_data --minute`"}
    v = pd.concat(vals).dropna()
    return {"coverage": n_ok / max(len(cr), 1), "n": len(v), "mean": float(v["r1"].mean()),
            "mean_5m_same_trades": float(v["r5"].mean()),
            "by_period": {p: float(x["r1"].mean()) for p, x in v.groupby("period")}}


# ---- risk report -------------------------------------------------------------------------------------------
def risk(cfg, holdout=False) -> dict:
    D = _load_discover(cfg)
    grid = Grid(cfg)
    R = pd.read_parquet(out_path(cfg, "study14_holdout_rows.parquet" if holdout else "study14_rows.parquet"))
    start = cfg["bankroll"]["start_usd"]
    out = []
    for fz in D["frozen"]:
        d = _combo_from_json(fz["combo"])
        cr = combo_rows(R, grid, d, D["q_cuts"])
        years = max((cr["exit_ts"].max() - cr["entry_ts"].min()) / (365.25 * DAY), 1e-9) if len(cr) else np.nan
        r = cr["r"].to_numpy()
        ks = np.linspace(0.01, 1.0, 100)
        with np.errstate(invalid="ignore", divide="ignore"):
            glog = [np.mean(np.log1p(k * r)) if np.all(1 + k * r > 0) else -np.inf for k in ks]
        kelly = float(ks[int(np.argmax(glog))]) if len(r) else np.nan
        wk = cr.groupby("week")["r"].sum()
        rows = []
        for sh in param(cfg, "s14_risk_shares"):
            p = risk_path(cr, d["S"], sh, param(cfg, "s14_max_open"), start)
            eq = pd.DataFrame(p["path"], columns=["ts", "eq"])
            eq["month"] = pd.to_datetime(eq["ts"], utc=True).dt.strftime("%Y-%m")
            me = eq.groupby("month")["eq"].last()
            mret = me.pct_change().fillna(me.iloc[0] / start - 1) if len(me) else pd.Series(dtype=float)
            losses = (cr.sort_values("exit_ts")["r"] < 0).astype(int).to_numpy()
            streak = max((len(list(gr)) for v_, gr in itertools.groupby(losses) if v_ == 1), default=0)
            rows.append({"risk_share": sh, "final_usd": round(p["final"], 0),
                         "cagr": (p["final"] / start) ** (1 / years) - 1 if p["final"] > 0 else -1.0,
                         "max_drawdown": p["max_dd"], "worst_month": float(mret.min()) if len(mret) else np.nan,
                         "taken": p["taken"], "skipped_max_open": p["skipped"], "longest_losing_streak": int(streak)})
        out.append({"name": fz["name"], "trades": len(cr), "years": years, "kelly_notional": kelly,
                    "quarter_kelly_worst_week": float(kelly / 4 * wk.min()) if len(wk) else np.nan, "paths": rows})
    return {"study": 14, "step": "risk (descriptive, never a gate)", "holdout": holdout, "start_usd": start,
            "max_open": param(cfg, "s14_max_open"), "combos": out}


# ---- holdout -----------------------------------------------------------------------------------------------
def run_holdout(cfg) -> dict:
    from src.calendar import HoldoutSealed, holdout_unsealed
    if not holdout_unsealed():
        raise HoldoutSealed("Study 14 holdout: set GAMMA_EDGE_RUN_HOLDOUT=1 (only on 'run the holdout')")
    D = _load_discover(cfg)
    dg = out_path(cfg, "study14_diagnose.json")
    if not dg.exists():
        raise SystemExit("run --diagnose first; only combinations passing its checks are tested")
    elig = set(json.loads(dg.read_text())["eligible"])
    grid = Grid(cfg)
    ev = pd.read_parquet(out_path(cfg, "study14_events_holdout.parquet"))
    ev["rep"] = -1
    parts = []
    for s, e_ in ev.groupby("sym"):
        coin = Coin(cfg, s, include_holdout=True)
        parts.append(coin_rows(cfg, grid, coin, e_, D["squeeze_model"]))
    H = pd.concat([p for p in parts if len(p)], ignore_index=True)
    H.to_parquet(out_path(cfg, "study14_holdout_rows.parquet"))
    R = pd.read_parquet(out_path(cfg, "study14_rows.parquet"))
    res = []
    for fz in D["frozen"]:
        if fz["name"] not in elig:
            continue
        d = _combo_from_json(fz["combo"])
        cr = combo_rows(H, grid, d, D["q_cuts"])
        ins = combo_rows(R, grid, d, D["q_cuts"])["r"].mean()
        bt = week_lb(cr, "r", cfg) if len(cr) else {"mean": np.nan, "lo": np.nan, "hi": np.nan, "n": 0}
        res.append({"name": fz["name"], "n": len(cr), "mean": bt["mean"], "lo": bt["lo"], "hi": bt["hi"],
                    "in_sample_mean": float(ins),
                    "pass": bool(bt["mean"] > 0 and bt["lo"] > 0 and bt["mean"] >= 0.5 * ins)})
    return {"study": 14, "step": "holdout (once)", "events": int(len(ev)), "results": res}


def _download(cfg, keys, workers, label):
    """Download, then retry once whatever failed; stop if anything still fails (rerun --all to resume)."""
    r = cd.download(keys, cd.raw_root(cfg), workers=workers)
    log.info("%s: %s", label, r)
    if r["failed"]:
        r = cd.download(keys, cd.raw_root(cfg), workers=max(1, workers // 2))
        log.info("%s retry: %s", label, r)
    if r["failed"]:
        raise SystemExit(f"{label}: {r['failed']} files still failing; rerun the same command (it resumes)")
    return r


def run_all(cfg, jobs: int, workers: int) -> dict:
    """The whole research run in one command; every step resumes, and each step's output is saved as JSON."""
    t0 = time.time()
    out = {"hourly_download": _download(cfg, cd.hourly_keys(cfg), workers, "hourly + funding")}
    ev = events(cfg, jobs=jobs)
    out_path(cfg, "study14_events.json").write_text(json.dumps(ev, default=_js, indent=1))
    out["events"] = {k: ev[k] for k in ("events_kept", "coins_with_events", "distinct_coin_hours", "to_download")}
    out["event_data_download"] = _download(cfg, cd.needed_keys(cfg, "event"), workers, "5-min + metrics")
    d = discover(cfg, jobs=jobs)
    out["discover"] = d
    out["minutes"] = round((time.time() - t0) / 60, 1)
    out["saved"] = "data/derived/crypto/study14_events.json and study14_discover.json"
    out["next"] = ("python -m src.crypto_data --minute, then python -m src.study14 --diagnose and --risk"
                   if d["frozen"] else "nothing frozen: the study stops here")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    for f in ("--events", "--discover", "--diagnose", "--risk"):
        g.add_argument(f, action="store_true")
    g.add_argument("--all", action="store_true", help="hourly download -> events -> event-data download -> discover")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--jobs", type=int, default=None, help="processes (default: cores - 1, at most 8)")
    ap.add_argument("--workers", type=int, default=24, help="parallel downloads for --all")
    a = ap.parse_args(argv)
    jobs = a.jobs or default_jobs()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    if not (a.events or a.discover or a.diagnose or a.risk or a.holdout or a.all):
        ap.error("choose --all, --events, --discover, --diagnose, --risk or --holdout")
    if a.holdout and (a.discover or a.diagnose or a.all):
        ap.error("--holdout goes alone, or with --events / --risk")
    if a.all:
        r = run_all(cfg, jobs, a.workers)
    elif a.events:
        r = events(cfg, holdout=a.holdout, jobs=jobs)
    elif a.discover:
        r = discover(cfg, jobs=jobs)
    elif a.diagnose:
        r = diagnose(cfg)
    elif a.risk:
        r = risk(cfg, holdout=a.holdout)
    else:
        r = run_holdout(cfg)
    print(json.dumps(r, default=_js, indent=1))


if __name__ == "__main__":
    main()
