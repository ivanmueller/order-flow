"""Study 15: go long early in crypto volume / volatility surges, searched over every key setting (STUDY15.md).

373,248 combinations (192 signal sets x 3 entries x 108 exits x 6 filters) walked on the hourly bars already on disk
for every coin; placebo noise test (the whole search on random non-event hours, 100 times); validation; stability;
at most 4 frozen; gating diagnose (own placebo, tails, 2x slippage, 5-minute re-walk); sealed holdout once.

  python -m src.study15 --all              # BTC hourly download (tiny) -> events -> discover, in one go
  python -m src.study15 --events | --discover            # the same steps one at a time
  python -m src.crypto_data --event-data --study 15      # 5-minute bars for the frozen combinations' trades
  python -m src.study15 --diagnose | --risk
  GAMMA_EDGE_RUN_HOLDOUT=1: crypto_data --hourly --holdout, crypto_data --btc --holdout, study15 --events --holdout,
  crypto_data --event-data --study 15 --holdout, then study15 --holdout     # once, on "run the holdout"
Options: --jobs N processes (default cores - 1, at most 8).

Walk (long, conservative): buy the next open 0.10% worse; inside a bar the low comes first: a bar opening through the
stop exits at its open, else the stop exits at its level, 0.10% worse; the trailing level uses earlier highs only;
stop before target; targets fill on a high above them, at their price; time exits at the bar open 0.10% worse; 0.05%
fee a side; funding paid by longs; a delisted coin exits at its last close; loss capped at the 1x collateral.
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
from src import study14 as s14
from src.config import load_config, param

log = logging.getLogger("study15")
HOUR, DAY, WEEK, ORIGIN, N_WEEKS = s14.HOUR, s14.DAY, s14.WEEK, s14.ORIGIN, s14.N_WEEKS
OBJECTIVES = s14.OBJECTIVES
_js = s14._js


# ---- grid ------------------------------------------------------------------------------------------------
class Grid15:
    ORDERED = ("W", "P", "V", "H", "S", "TR", "T", "F")

    def __init__(self, cfg):
        self.W = list(param(cfg, "s15_windows_h"))
        self.P = list(param(cfg, "s15_moves"))
        self.K = list(param(cfg, "s15_kinds"))
        self.levels = {"volume": list(param(cfg, "s15_volume_mult")), "volatility": list(param(cfg, "s15_vol_mult"))}
        self.X = list(param(cfg, "s15_prior_max"))
        self.E = list(param(cfg, "s15_entries"))
        self.H = list(param(cfg, "s15_holds_d"))
        self.S = list(param(cfg, "s15_stops"))
        self.TR = list(param(cfg, "s15_trails"))
        self.T = list(param(cfg, "s15_targets"))
        self.F = list(param(cfg, "s15_funding_max"))
        self.B = list(param(cfg, "s15_btc_filter"))
        self.sets = [(w, p, k, v, x) for w in self.W for p in self.P for k in self.K for v in self.levels[k]
                     for x in self.X]
        self.cols = list(itertools.product(self.H, self.S, self.TR, self.T))
        self.col_names = [f"{h}|{s}|{tr}|{t}" for h, s, tr, t in self.cols]
        self.nE, self.nF, self.nB = len(self.E), len(self.F), len(self.B)
        self.n_fb = self.nF * self.nB
        self.n_cells = len(self.sets) * self.nE * self.n_fb
        self.orders = {k: s14._order(getattr(self, k)) for k in ("W", "P", "H", "S", "TR", "T", "F")}

    def cell(self, set_i, e_i, f_i, b_i) -> int:
        return (set_i * self.nE + e_i) * self.n_fb + f_i * self.nB + b_i

    def combo(self, cell: int, c: int) -> dict:
        g, fb = divmod(int(cell), self.n_fb)
        set_i, e_i = divmod(g, self.nE)
        f_i, b_i = divmod(fb, self.nB)
        W, P, K, V, X = self.sets[set_i]
        H, S, TR, T = self.cols[int(c)]
        return {"W": W, "P": P, "K": K, "V": V, "X": X, "E": self.E[e_i], "H": H, "S": S, "TR": TR, "T": T,
                "F": self.F[f_i], "B": self.B[b_i]}

    def locate(self, d: dict) -> tuple[int, int]:
        set_i = self.sets.index((d["W"], d["P"], d["K"], d["V"], d["X"]))
        cell = self.cell(set_i, self.E.index(d["E"]), self.F.index(d["F"]), self.B.index(d["B"]))
        return cell, self.cols.index((d["H"], d["S"], d["TR"], d["T"]))

    def parts(self, d: dict) -> dict:
        return {"set_i": self.sets.index((d["W"], d["P"], d["K"], d["V"], d["X"])), "e_i": self.E.index(d["E"])}

    def neighbours(self, d: dict) -> list[dict]:
        out = []
        for k in self.ORDERED:
            o = sorted(self.levels[d["K"]]) if k == "V" else self.orders[k]
            i = o.index(d[k])
            for j in (i - 1, i + 1):
                if 0 <= j < len(o):
                    out.append({**d, k: o[j]})
        return out

    @staticmethod
    def name(d: dict) -> str:
        return (f"W{d['W']}h P{d['P']} {d['K']} {d['V']}x X{d['X']} {d['E']} H{d['H']}d S{d['S']} TR{d['TR']} "
                f"T{d['T']} F{d['F']} B{d['B']}")


def row_cells(g, fund, regime, grid: Grid15) -> np.ndarray:
    """For each row (group g = set x entry): the filter cells it belongs to (F nested levels x B), -1 padded."""
    g = np.asarray(g, int)
    fund = np.asarray(fund, float)
    regime = np.asarray(regime, bool)
    out = np.full((len(g), grid.n_fb), -1, int)
    j = 0
    for f_i, thr in enumerate(grid.F):
        okf = np.ones(len(g), bool) if thr is None else (np.nan_to_num(fund, nan=np.inf) <= thr)
        for b_i, b in enumerate(grid.B):
            ok = okf & (np.ones(len(g), bool) if b == "any" else regime)
            out[:, j] = np.where(ok, g * grid.n_fb + f_i * grid.nB + b_i, -1)
            j += 1
    return out


# ---- signals and entries ------------------------------------------------------------------------------------
def surge_signals(h: pd.DataFrame, W: int, lookback_h: int, prior_h: int) -> dict:
    """Per bar k: return over W bars, volume ratio and volatility ratio (each vs the median W-bar value over the
    lookback_h windows ending before this one), and the return over the prior_h bars before the window."""
    c, o, hi, lo = (h[k].to_numpy(float) for k in ("close", "open", "high", "low"))
    qv, real = h["qv"].to_numpy(float), h["real"].to_numpy(bool)
    n = len(c)
    out = {k: np.full(n, np.nan) for k in ("ret", "volume", "volatility", "prior")}
    if n <= W:
        return out
    out["ret"][W:] = np.where(real[W:] & real[:-W], c[W:] / c[:-W] - 1, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        rng = np.where(real & (o > 0), (hi - lo) / o, 0.0)
        for key, x in (("volume", qv), ("volatility", rng)):
            sw = pd.Series(x).rolling(W).sum()
            base = sw.rolling(lookback_h, min_periods=lookback_h).median().shift(W).to_numpy()
            out[key] = np.where(base > 0, sw.to_numpy() / base, np.nan)
        a = W + prior_h
        if n > a:
            out["prior"][a:] = np.where(real[prior_h:n - W] & real[:n - a], c[prior_h:n - W] / c[:n - a] - 1, np.nan)
    return out


def detect(sig: dict, h: pd.DataFrame, P, kind, level, prior_max, cooldown_h) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        ok = (sig["ret"] >= P) & (sig[kind] >= level) & h["real"].to_numpy(bool)
        if prior_max is not None:
            ok &= sig["prior"] <= prior_max
    return s14.first_with_cooldown(ok, cooldown_h)


def entry_index(h: pd.DataFrame, rule: str, window: int) -> np.ndarray:
    """For every bar k as a signal bar: the bar whose open is the entry, or -1."""
    o, c, hi = (h[k].to_numpy(float) for k in ("open", "close", "high"))
    n = len(o)
    k = np.arange(n)
    if rule == "next_hour":
        return np.where(k + 1 < n, k + 1, -1)
    idx = k[:, None] + np.arange(1, window + 1)
    inside = idx < n
    ix = np.minimum(idx, n - 1)
    cond = (c[ix] < o[ix]) if rule == "pullback" else (c[ix] > hi[k][:, None])
    cond &= inside
    j = idx[k, cond.argmax(1)] + 1
    return np.where(cond.any(1) & (j < n), j, -1)


def btc_above_ma(btc: pd.DataFrame | None, e, ma_h: int) -> np.ndarray:
    """True when BTC's last closed hourly close at time e is above the mean of its last ma_h closes."""
    e = np.asarray(e, "int64")
    if btc is None or len(btc) == 0:
        return np.zeros(len(e), bool)
    ts, c = btc["ts"].to_numpy("int64"), btc["close"].to_numpy(float)
    p = np.searchsorted(ts, e, "left") - 1
    cs = np.concatenate([[0.0], np.cumsum(c)])
    ok = p >= ma_h - 1
    pc = np.clip(p, ma_h - 1, len(c) - 1)
    mean = (cs[pc + 1] - cs[pc + 1 - ma_h]) / ma_h
    return ok & (c[pc] > mean)


# ---- the long walk ---------------------------------------------------------------------------------------------
def walk_long(b: dict, entry_ts, last_ts: int, hold_bars, stops, trails, targets, slip, fee, funding_ts,
              funding_rate, max_filled, fee_in=None, chunk_cells: int = 4_000_000) -> dict:
    ts = np.asarray(b["ts"], "int64")
    o, hh, lo, c = (np.asarray(b[k], float) for k in ("open", "high", "low", "close"))
    real = np.asarray(b["real"], bool)
    entry_ts = np.asarray(entry_ts, "int64")
    fee_in = fee if fee_in is None else fee_in
    N, n = len(entry_ts), len(ts)
    shape = (N, len(hold_bars), len(stops), len(trails), len(targets))
    out = {"r": np.full(shape, np.nan), "exit_ts": np.full(shape, -1, "int64"), "valid": np.zeros(N, bool),
           "fund_at_entry": np.full(N, np.nan)}
    fts, frt = np.asarray(funding_ts, "int64"), np.asarray(funding_rate, float)
    if len(fts) and n:
        k = np.searchsorted(ts, fts, "left") - 1
        pf = np.where(k >= 0, c[np.clip(k, 0, n - 1)], np.nan)
        cum = np.concatenate([[0.0], np.cumsum(np.nan_to_num(frt * pf))])
        j = np.searchsorted(fts, entry_ts, "right") - 1
        out["fund_at_entry"] = np.where(j >= 0, frt[np.clip(j, 0, None)], np.nan)
    else:
        cum = np.array([0.0])

    def F(t):
        return cum[np.searchsorted(fts, t, "left")] if len(fts) else np.zeros(np.shape(t))
    if N == 0 or n == 0:
        return out
    step_ns = ts[1] - ts[0] if n > 1 else HOUR
    L = max(hold_bars) + 1
    last_idx = min(int(np.searchsorted(ts, last_ts, "left")) - 1, n - 1)
    step = max(1, chunk_cells // L)
    for a in range(0, N, step):
        sl = slice(a, min(N, a + step))
        e = entry_ts[sl]
        i0 = np.searchsorted(ts, e, "left")
        i0c = np.minimum(i0, n - 1)
        hit0 = (i0 < n) & (ts[i0c] == e) & (i0 <= last_idx)
        rel_last = last_idx - i0c
        covered = (i0c + max(hold_bars) <= n - 1) | (last_ts <= ts[-1] + step_ns)
        idx = np.minimum(i0c[:, None] + np.arange(L), n - 1)
        cols = np.arange(L)[None, :]
        inside = cols <= rel_last[:, None]
        win = cols <= np.minimum(max(hold_bars), rel_last)[:, None]
        filled = ((~real[idx]) & win).sum(1) / np.maximum(win.sum(1), 1)
        valid = hit0 & covered & (filled <= max_filled)
        p0 = o[i0c]
        pe = p0 * (1 + slip)
        H2 = np.where(inside, hh[idx], -np.inf)
        L2 = np.where(inside, lo[idx], np.inf)
        O2 = o[idx]
        cm = np.maximum.accumulate(H2, axis=1)
        peak = np.maximum(np.concatenate([p0[:, None], cm[:, :-1]], axis=1), p0[:, None])   # earlier highs only
        rows = np.arange(len(e))
        fe = F(e + 1)
        stops_out = []
        for S in stops:
            hard = pe * (1 - S)
            for TR in trails:
                lvl = hard[:, None] if TR is None else np.maximum(hard[:, None], (1 - TR) * peak)
                lvl = np.broadcast_to(lvl, H2.shape)
                hit = L2 <= lvl
                f = np.where(hit.any(1), hit.argmax(1), L)
                fc = np.minimum(f, L - 1)
                px = np.minimum(O2[rows, fc], lvl[rows, fc]) * (1 - slip)
                stops_out.append((f, px))
        tg = []
        for T in targets:
            if T is None:
                tg.append((np.full(len(e), L), np.full(len(e), np.nan)))
                continue
            lv = pe * (1 + T)
            hit = H2 > lv[:, None]
            tg.append((np.where(hit.any(1), hit.argmax(1), L), lv))
        for hi_, nb in enumerate(hold_bars):
            timed = nb <= rel_last
            end_i = np.where(timed, nb, rel_last + 1)
            end_px = np.where(timed, o[np.minimum(i0c + nb, n - 1)], c[max(last_idx, 0)]) * (1 - slip)
            end_ts = np.where(timed, ts[np.minimum(i0c + nb, n - 1)], ts[max(last_idx, 0)] + step_ns)
            for k_, (f_s, px_s) in enumerate(stops_out):
                si, ti = divmod(k_, len(trails))
                for gi, (f_t, px_t) in enumerate(tg):
                    stop = (f_s < end_i) & (f_s <= f_t)
                    tgt = ~stop & (f_t < end_i)
                    x = np.where(stop, px_s, np.where(tgt, px_t, end_px))
                    xi = np.where(stop, f_s, f_t)
                    xts = np.where(stop | tgt, ts[np.minimum(i0c + np.minimum(xi, L - 1), n - 1)], end_ts)
                    fund = F(xts) - fe
                    r = x / pe - 1 - fee_in - fee * x / pe - fund / pe
                    r = np.maximum(r, -1.0)
                    out["r"][sl, hi_, si, ti, gi] = np.where(valid, r, np.nan)
                    out["exit_ts"][sl, hi_, si, ti, gi] = np.where(valid, xts, -1)
        out["valid"][sl] = valid
    return out


# ---- placebo hours ---------------------------------------------------------------------------------------------
def _month(ts) -> np.ndarray:
    return np.asarray(ts, "int64").astype("datetime64[ns]").astype("datetime64[M]").astype(int)


def placebo_candidates(ts, ev_idx, allowed_idx, gap_h: int) -> dict:
    """Allowed bars at least gap_h bars from every event bar, grouped by calendar month (for fast draws)."""
    ev = np.sort(np.asarray(ev_idx, int))
    al = np.asarray(allowed_idx, int)
    if len(ev):
        k = np.searchsorted(ev, al)
        d1 = np.where(k > 0, al - ev[np.clip(k - 1, 0, None)], 10**9)
        d2 = np.where(k < len(ev), ev[np.clip(k, 0, len(ev) - 1)] - al, 10**9)
        al = al[np.minimum(d1, d2) >= gap_h]
    m = _month(np.asarray(ts)[al]) if len(al) else np.array([], int)
    order = np.argsort(m, kind="stable")
    al, m = al[order], m[order]
    um, start, cnt = np.unique(m, return_index=True, return_counts=True)
    return {"idx": al, "months": um, "start": start, "count": cnt}


def draw_placebo(rng, ts, ev_idx, cand: dict) -> np.ndarray:
    """One random allowed bar of the same calendar month per event bar (-1 when the month has none)."""
    ev = np.asarray(ev_idx, int)
    u = rng.random(len(ev))
    if len(ev) == 0 or len(cand["idx"]) == 0:
        return np.full(len(ev), -1)
    em = _month(np.asarray(ts)[ev])
    p = np.searchsorted(cand["months"], em)
    pc = np.clip(p, 0, len(cand["months"]) - 1)
    has = (p < len(cand["months"])) & (cand["months"][pc] == em)
    pick = cand["start"][pc] + np.minimum((u * cand["count"][pc]).astype(int), cand["count"][pc] - 1)
    return np.where(has, cand["idx"][np.clip(pick, 0, len(cand["idx"]) - 1)], -1)


# ---- periods ---------------------------------------------------------------------------------------------------
def reach_ns(cfg) -> int:
    return (param(cfg, "s15_entry_window_h") + 1) * HOUR + max(param(cfg, "s15_holds_d")) * DAY


def period_of(t, cfg) -> np.ndarray:
    b, r = s14.bounds(cfg), reach_ns(cfg)
    t = np.asarray(t, "int64")
    ve = min(b["val_end"], b["holdout"])
    out = np.full(len(t), None, object)
    out[t + r < b["train_end"]] = "train"
    out[(t >= b["train_end"]) & (t + r < ve)] = "val"
    out[t >= b["holdout"]] = "holdout"
    return out


# ---- accumulator ---------------------------------------------------------------------------------------------------
class Acc:
    """n, sum, sum of squares and weeks per (rep, cell, outcome column), filled with one sparse product per batch."""

    def __init__(self, n_cells, n_cols, reps=1, n_weeks=N_WEEKS):
        self.n = np.zeros((reps, n_cells))
        self.s = np.zeros((reps, n_cells, n_cols))
        self.ss = np.zeros_like(self.s)
        self.wk = np.zeros((reps, n_cells, n_weeks), bool)

    def add(self, rep, cells, Y, week):
        from scipy import sparse
        rr, mm = np.nonzero(cells >= 0)
        if len(rr) == 0:
            return
        cc = cells[rr, mm]
        M = sparse.csr_matrix((np.ones(len(rr)), (cc, rr)), shape=(self.n.shape[1], len(Y)))
        Y = np.asarray(Y, float)
        self.s[rep] += M @ Y
        self.ss[rep] += M @ (Y * Y)
        self.n[rep] += np.bincount(cc, minlength=self.n.shape[1])
        self.wk[rep, cc, np.clip(np.asarray(week, int)[rr], 0, self.wk.shape[2] - 1)] = True

    def merge(self, other):
        self.n += other.n
        self.s += other.s
        self.ss += other.ss
        self.wk |= other.wk

    def stats(self, rep, n_months) -> dict:
        C = self.s.shape[2]
        n = np.repeat(self.n[rep][:, None], C, 1)
        s, ss = self.s[rep], self.ss[rep]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(n > 0, s / n, np.nan)
            var = np.where(n > 1, (ss - s * s / np.maximum(n, 1)) / (n - 1), np.nan)
            sd = np.sqrt(np.maximum(var, 0))
            t = np.where(sd > 0, mean / sd * np.sqrt(n), np.nan)
        weeks = np.repeat(self.wk[rep].sum(1)[:, None], C, 1)
        return {"n": n, "weeks": weeks, "mean": mean, "sd": sd, "per_month": s / n_months, "t_stat": t}


# ---- per-coin work -------------------------------------------------------------------------------------------------
_BTC = {}


def _btc(cfg, include_holdout=False):
    key = include_holdout
    if key not in _BTC:
        _BTC[key] = cd.load_bars(cfg, "BTCUSDT", "1h", include_holdout)
    return _BTC[key]


def _coin(cfg, sym, include_holdout=False, interval="1h"):
    h = cd.load_bars(cfg, sym, "1h", include_holdout)
    real = h["real"].to_numpy(bool) if len(h) else np.array([], bool)
    last = int(h["ts"].to_numpy("int64")[real][-1] + HOUR) if real.any() else 0
    b = h if interval == "1h" else cd.load_bars(cfg, sym, interval, include_holdout)
    return h, b, cd.load_funding(cfg, sym, include_holdout), last


def _walk_all(cfg, grid, b, entry_ts, last, f, slip=None, fee=None, fee_in=None, bars_per_day=24):
    return walk_long({k: b[k].to_numpy() for k in ("ts", "open", "high", "low", "close", "real")}, entry_ts, last,
                     [H * bars_per_day for H in grid.H], grid.S, grid.TR, grid.T,
                     param(cfg, "s15_slippage") if slip is None else slip, param(cfg, "s15_fee") if fee is None else fee,
                     f["ts"].to_numpy("int64"), f["rate"].to_numpy(float), param(cfg, "s15_max_filled_share"),
                     fee_in=fee_in)


def _events_coin(task):
    cfg, s, holdout = task
    grid = Grid15(cfg)
    h = cd.load_bars(cfg, s, "1h", include_holdout=holdout)
    if len(h) == 0 or not h["real"].any():
        return None, None
    lb, cool = param(cfg, "s15_volume_lookback_d") * 24, param(cfg, "s15_cooldown_d") * 24
    prior_h = param(cfg, "s15_prior_days") * 24
    ts = h["ts"].to_numpy("int64")
    sig = {W: surge_signals(h, W, lb, prior_h) for W in grid.W}
    parts = []
    for i, (W, P, K, V, X) in enumerate(grid.sets):
        k = detect(sig[W], h, P, K, V, X, cool)
        if len(k):
            parts.append(pd.DataFrame({"sym": s, "set_i": np.int16(i), "k": k.astype("int32"), "t": ts[k] + HOUR}))
    end = int(ts[h["real"].to_numpy(bool)][-1] + HOUR)
    return (pd.concat(parts, ignore_index=True) if parts else None), end


def events(cfg, holdout=False, jobs=1) -> dict:
    grid = Grid15(cfg)
    excl = set(param(cfg, "s15_exclude"))
    syms = [s for s in cd.study_symbols(cd.inventory_cache(cfg), cfg) if s not in excl]
    log.info("events: %d coins, %d signal sets, %d processes", len(syms), len(grid.sets), jobs)
    res = s14._run(_events_coin, [(cfg, s, holdout) for s in syms], jobs)
    ev = pd.concat([r for r, _ in res if r is not None], ignore_index=True)
    data_end = max(e for _, e in res if e is not None)
    ev["period"] = period_of(ev["t"].to_numpy("int64"), cfg)
    if holdout:
        ev.loc[(ev["period"] == "holdout") & (ev["t"] + reach_ns(cfg) > data_end), "period"] = None
        ev = ev[ev["period"] == "holdout"]
    else:
        ev.loc[ev["period"] == "holdout", "period"] = None
    ev.to_parquet(s14.out_path(cfg, "study15_events_holdout.parquet" if holdout else "study15_events.parquet"))
    use = ev[ev["period"].notna()].copy()
    st = pd.DataFrame(grid.sets, columns=["W", "P", "K", "V", "X"])
    use = use.join(st, on="set_i")
    first = use[(use["X"].isna()) & (use["V"] == use["K"].map({k: min(v) for k, v in grid.levels.items()}))]
    tab = first.groupby(["K", "W", "P", "period"]).size().unstack(fill_value=0)
    return {"coins_with_events": int(use["sym"].nunique()), "events_kept": int(len(use)),
            "events_per_period": use["period"].value_counts().to_dict(),
            "dropped_boundary": int(ev["period"].isna().sum()),
            "distinct_coin_hours": int(use[["sym", "t"]].drop_duplicates().shape[0]),
            "lowest_level_no_prior_filter": {f"{k} W{w} P{p}": r.to_dict() for (k, w, p), r in tab.iterrows()},
            "data_end_utc": str(pd.Timestamp(data_end, tz="UTC"))}


def _coin_rows(cfg, grid, sym, ev, include_holdout=False):
    """Real rows of one coin: (set, entry rule, entry bar) with outcomes, funding at entry, BTC filter, week."""
    h, b, f, last = _coin(cfg, sym, include_holdout)
    if len(h) == 0:
        return None, None
    win = param(cfg, "s15_entry_window_h")
    eidx = [entry_index(h, E, win) for E in grid.E]
    k = ev["k"].to_numpy(int)
    rows = []
    for e_i in range(grid.nE):
        j = eidx[e_i][k]
        ok = j >= 0
        rows.append(pd.DataFrame({"set_i": ev["set_i"].to_numpy()[ok], "e_i": np.int8(e_i), "j": j[ok],
                                  "period": ev["period"].to_numpy(object)[ok]}))
    R = pd.concat(rows, ignore_index=True)
    if R.empty:
        return None, None
    ts = h["ts"].to_numpy("int64")
    uj, inv = np.unique(R["j"].to_numpy(), return_inverse=True)
    w = _walk_all(cfg, grid, b, ts[uj], last, f)
    ok = w["valid"][inv]
    R["entry_ts"] = ts[R["j"].to_numpy()]
    R["fund"] = w["fund_at_entry"][inv].astype("float32")
    R["regime"] = btc_above_ma(_btc(cfg, include_holdout), R["entry_ts"].to_numpy(), param(cfg, "s15_btc_ma_h"))
    R["week"] = ((R["entry_ts"] - ORIGIN) // WEEK).astype("int16")
    R["sym"] = sym
    Y = w["r"].reshape(len(uj), -1)[inv]
    return R[ok].reset_index(drop=True), Y[ok]


def _real_chunk(task):
    cfg, items = task
    grid = Grid15(cfg)
    acc = {p: Acc(grid.n_cells, len(grid.cols)) for p in ("train", "val")}
    keep = []
    for sym, ev in items:
        R, Y = _coin_rows(cfg, grid, sym, ev)
        if R is None:
            continue
        cells = row_cells(R["set_i"].to_numpy(int) * grid.nE + R["e_i"].to_numpy(int), R["fund"], R["regime"], grid)
        for p in ("train", "val"):
            m = (R["period"] == p).to_numpy()
            acc[p].add(0, cells[m], Y[m], R["week"].to_numpy()[m])
        keep.append(R.drop(columns=["j"]))
    return acc, (pd.concat(keep, ignore_index=True) if keep else None)


def _placebo_chunk(task):
    """Some placebo reps over every coin: per coin, all hours' outcomes are walked once, then each rep draws
    random hours (same month, >= gap from that signal set's events) and adds them to the accumulators."""
    cfg, reps, coins, cand_cells = task
    grid = Grid15(cfg)
    acc = Acc(grid.n_cells, len(grid.cols), reps=len(reps))
    win, gap = param(cfg, "s15_entry_window_h"), param(cfg, "s15_placebo_gap_h")
    hist = param(cfg, "s15_volume_lookback_d") * 24 + max(grid.W) + param(cfg, "s15_prior_days") * 24
    t0 = time.time()
    for ci, (sym, ev) in enumerate(coins):
        h, b, f, last = _coin(cfg, sym)
        if len(h) == 0:
            continue
        ts = h["ts"].to_numpy("int64")
        n = len(ts)
        allowed = np.flatnonzero(h["real"].to_numpy(bool) & (np.arange(n) >= hist)
                                 & (period_of(ts + HOUR, cfg) == "train"))
        if len(allowed) == 0:
            continue
        eidx = np.stack([entry_index(h, E, win) for E in grid.E], 1)
        need = np.unique(eidx[allowed][eidx[allowed] >= 0])
        w = _walk_all(cfg, grid, b, ts[need], last, f)
        pos = np.full(n, -1)
        pos[need] = np.arange(len(need))
        Yall = w["r"].reshape(len(need), -1)
        valid = w["valid"]
        regime = btc_above_ma(_btc(cfg), ts[need], param(cfg, "s15_btc_ma_h"))
        week = (ts[need] - ORIGIN) // WEEK
        groups = [(int(si), g["k"].to_numpy(int)) for si, g in ev.groupby("set_i")]
        cands = [placebo_candidates(ts, kk, allowed, gap) for _, kk in groups]
        for ri, rep in enumerate(reps):
            rng = np.random.default_rng([param(cfg, "s15_seed"), rep, ci])
            parts_c, parts_y, parts_w = [], [], []
            for (si, kk), cand in zip(groups, cands):
                pk = draw_placebo(rng, ts, kk, cand)
                pk = pk[pk >= 0]
                if len(pk) == 0:
                    continue
                for e_i in range(grid.nE):
                    j = eidx[pk, e_i]
                    j = j[j >= 0]
                    q = pos[j]
                    q = q[(q >= 0)]
                    q = q[valid[q]]
                    if len(q) == 0:
                        continue
                    parts_c.append(row_cells(np.full(len(q), si * grid.nE + e_i), w["fund_at_entry"][q], regime[q], grid))
                    parts_y.append(Yall[q])
                    parts_w.append(week[q])
            if parts_c:
                acc.add(ri, np.concatenate(parts_c), np.concatenate(parts_y), np.concatenate(parts_w))
        if ci % 50 == 0:
            log.info("placebo reps %d-%d: %d of %d coins (%.0f s)", reps[0], reps[-1], ci + 1, len(coins),
                     time.time() - t0)
    return [_rep_summary(acc, ri, cfg, cand_cells) for ri in range(len(reps))]


def _rep_summary(acc, ri, cfg, cand_cells):
    g = cfg["gates"]
    st = acc.stats(ri, s14._months(cfg, "train"))
    best = s14.best_values(st, g["study15_min_trades"], g["study15_min_weeks"])
    cm = [float(st["mean"][c, k]) if st["n"][c, k] > 0 else np.nan for c, k in cand_cells]
    return {"best": best, "cand_means": cm}


# ---- trades of chosen combinations ----------------------------------------------------------------------------------
def _combo_mask(R, grid, d):
    p = grid.parts(d)
    m = (R["set_i"].to_numpy() == p["set_i"]) & (R["e_i"].to_numpy() == p["e_i"])
    if d["F"] is not None:
        m &= np.nan_to_num(R["fund"].to_numpy(float), nan=np.inf) <= d["F"]
    if d["B"] == "above_ma":
        m &= R["regime"].to_numpy(bool)
    return m


def _trades_coin(task):
    cfg, sym, jobs_, interval, slip, fee, fee_in, include_holdout = task
    grid = Grid15(cfg)
    h, b, f, last = _coin(cfg, sym, include_holdout, interval)
    out = {}
    if len(b) == 0:
        return out
    per_day = DAY // ((b["ts"].iloc[1] - b["ts"].iloc[0]) if len(b) > 1 else HOUR)
    bd = {k: b[k].to_numpy() for k in ("ts", "open", "high", "low", "close", "real")}
    for name, d, rows in jobs_:
        w = walk_long(bd, rows["entry_ts"].to_numpy("int64"), last, [d["H"] * per_day], [d["S"]], [d["TR"]], [d["T"]],
                      param(cfg, "s15_slippage") if slip is None else slip, param(cfg, "s15_fee") if fee is None else fee,
                      f["ts"].to_numpy("int64"), f["rate"].to_numpy(float), param(cfg, "s15_max_filled_share"),
                      fee_in=fee_in)
        out[name] = pd.DataFrame({"sym": sym, "entry_ts": rows["entry_ts"].to_numpy("int64"),
                                  "exit_ts": w["exit_ts"][:, 0, 0, 0, 0], "r": w["r"][:, 0, 0, 0, 0],
                                  "week": rows["week"].to_numpy(), "period": rows["period"].to_numpy(object)})
    return out


def combo_trades(cfg, combos: dict, R: pd.DataFrame, jobs=1, interval="1h", slip=None, fee=None, fee_in=None,
                 include_holdout=False) -> dict:
    """{name: trades} for several combinations at once, each coin loaded once. Trades with no data: dropped
    (coverage reported by the caller)."""
    grid = Grid15(cfg)
    per_sym = {}
    for name, d in combos.items():
        sub = R[_combo_mask(R, grid, d)]
        for sym, rows in sub.groupby("sym"):
            per_sym.setdefault(sym, []).append((name, d, rows[["entry_ts", "week", "period"]]))
    tasks = [(cfg, s, j, interval, slip, fee, fee_in, include_holdout) for s, j in per_sym.items()]
    res = s14._run(_trades_coin, tasks, jobs)
    out = {name: [] for name in combos}
    for r in res:
        for name, df in r.items():
            out[name].append(df)
    cols = ["sym", "entry_ts", "exit_ts", "r", "week", "period"]
    return {k: (pd.concat(v, ignore_index=True).dropna(subset=["r"]) if v else pd.DataFrame(columns=cols))
            for k, v in out.items()}


def tails(tr: pd.DataFrame) -> dict:
    if len(tr) == 0:
        return {"n": 0}
    r = np.sort(tr["r"].to_numpy())
    k = int(np.ceil(0.05 * len(r)))
    m = pd.to_datetime(tr["entry_ts"], utc=True).dt.strftime("%Y-%m")
    return {"n": len(r), "mean": float(r.mean()), "median": float(np.median(r)), "win_share": float((r > 0).mean()),
            "mean_without_best_5pct": float(r[: len(r) - k].mean()) if len(r) > k else np.nan,
            "share_of_total_from_best_5pct": float(r[len(r) - k:].sum() / r.sum()) if r.sum() != 0 else np.nan,
            "worst_trade": float(r[0]), "best_trade": float(r[-1]),
            "worst_week": float(tr.groupby("week")["r"].sum().min()), "worst_month": float(tr.groupby(m)["r"].sum().min())}


# ---- discovery ------------------------------------------------------------------------------------------------------
def discover(cfg, jobs=1) -> dict:
    t0 = time.time()
    grid = Grid15(cfg)
    g_ = cfg["gates"]
    min_tr, min_wk = g_["study15_min_trades"], g_["study15_min_weeks"]
    ev = pd.read_parquet(s14.out_path(cfg, "study15_events.parquet"))
    ev = ev[ev["period"].isin(["train", "val"])]
    by = [(s, e) for s, e in ev.groupby("sym")]
    by.sort(key=lambda x: -len(x[1]))
    n_ch = max(1, min(jobs * 4, len(by)))
    chunks = [by[i::n_ch] for i in range(n_ch)]
    log.info("real pass: %d coins, %d events, %d processes", len(by), len(ev), jobs)
    res = s14._run(_real_chunk, [(cfg, ch) for ch in chunks], jobs)
    acc = {p: Acc(grid.n_cells, len(grid.cols)) for p in ("train", "val")}
    rows = []
    for a, r in res:
        for p in acc:
            acc[p].merge(a[p])
        if r is not None:
            rows.append(r)
    R = pd.concat(rows, ignore_index=True)
    R["sym"] = R["sym"].astype("category")
    R.to_parquet(s14.out_path(cfg, "study15_rows.parquet"))
    n_tr, n_va = s14._months(cfg, "train"), s14._months(cfg, "val")
    T, V = acc["train"].stats(0, n_tr), acc["val"].stats(0, n_va)
    ok = (T["n"] >= min_tr) & (T["weeks"] >= min_wk)
    real_best = s14.best_values(T, min_tr, min_wk)
    # candidates: top k per objective (discovery), fixed before the placebo run
    top, cand = {}, {}
    for o, col in OBJECTIVES.items():
        v = np.where(ok, T[col], -np.inf)
        flat = np.argsort(-v, axis=None)[: param(cfg, "s15_top_k")]
        lst = []
        for fi in flat:
            c_, k_ = np.unravel_index(fi, v.shape)
            if not np.isfinite(v[c_, k_]):
                continue
            d = grid.combo(c_, k_)
            nm = Grid15.name(d)
            cand[nm] = (int(c_), int(k_), d)
            lst.append(nm)
        top[o] = lst
    cand_list = list(cand.items())
    cand_cells = [(c_, k_) for _, (c_, k_, _) in cand_list]
    # placebo: reps split over processes; each process walks every coin once and draws its reps
    reps = param(cfg, "s15_null_reps")
    tr_ev = ev[ev["period"] == "train"]
    coins = sorted(((s, e[["set_i", "k"]]) for s, e in tr_ev.groupby("sym")), key=lambda x: x[0])
    n_p = max(1, min(jobs, reps))
    rep_chunks = [list(range(reps))[i::n_p] for i in range(n_p)]
    log.info("placebo: %d reps x %d coins on %d processes (each process walks every coin once)", reps, len(coins), n_p)
    pres = s14._run(_placebo_chunk, [(cfg, rc, coins, cand_cells) for rc in rep_chunks], n_p)
    by_rep = {}
    for rc, lst in zip(rep_chunks, pres):
        for rep, x in zip(rc, lst):
            by_rep[rep] = x
    pb = {o: [by_rep[r]["best"][o] for r in range(reps)] for o in OBJECTIVES}
    pc = np.array([by_rep[r]["cand_means"] for r in range(reps)], float)          # reps x candidates
    noise = {o: {"real_best": real_best[o], "placebo_median": float(np.nanmedian(pb[o])),
                 "placebo_95": float(np.nanquantile(pb[o], 0.95)), "p": s14.null_p(real_best[o], pb[o])}
             for o in OBJECTIVES}
    # validation, stability, tails
    combos = {nm: d for nm, (_, _, d) in cand_list}
    trades = combo_trades(cfg, combos, R, jobs=jobs)
    info = {}
    for i, (nm, (c_, k_, d)) in enumerate(cand_list):
        tr = trades[nm]
        va, tn = tr[tr["period"] == "val"], tr[tr["period"] == "train"]
        bt = s14.week_lb(va, "r", cfg) if len(va) else {"lo": np.nan, "hi": np.nan}
        nb = [grid.locate(x) for x in grid.neighbours(d)]
        nbe = [(a, b) for a, b in nb if ok[a, b]]
        stab = float(np.mean([T["mean"][a, b] > 0 for a, b in nbe])) if nbe else 0.0
        info[nm] = {"combo": d,
                    "disc": {k: float(T[k][c_, k_]) for k in ("n", "weeks", "mean", "per_month", "t_stat")},
                    "val": {"n": float(V["n"][c_, k_]), "weeks": float(V["weeks"][c_, k_]), "mean": float(V["mean"][c_, k_]),
                            "per_month": float(V["per_month"][c_, k_]), "lo": bt["lo"], "hi": bt.get("hi")},
                    "tails_disc": tails(tn), "tails_val": tails(va), "stability": stab,
                    "neighbours_eligible": len(nbe),
                    "placebo_same_combo": {"mean": float(np.nanmean(pc[:, i])), "p": s14.null_p(T["mean"][c_, k_], pc[:, i])}}
    frozen, dup, sigs = [], [], {}
    passing = [o for o in OBJECTIVES if noise[o]["p"] <= g_["study15_null_p"]]
    pool = sorted({nm for o in passing for nm in top[o]
                   if info[nm]["val"]["n"] >= g_["study15_val_min_trades"] and np.isfinite(info[nm]["val"]["lo"])
                   and info[nm]["val"]["lo"] > 0 and info[nm]["stability"] >= g_["study15_neighbour_share"]})
    for nm in sorted(pool, key=lambda x: -info[x]["val"]["per_month"]):
        if len(frozen) >= g_["study15_max_frozen"]:
            break
        sig = s14.trade_signature(trades[nm])
        if sig in sigs:
            dup.append({"name": nm, "same_trades_as": sigs[sig]})
            continue
        sigs[sig] = nm
        frozen.append({"name": nm, **info[nm]})
    m5 = set()
    for fz in frozen:
        for s, a, b in trades[fz["name"]][["sym", "entry_ts", "exit_ts"]].itertuples(index=False):
            for mo in pd.period_range(pd.Timestamp(a), pd.Timestamp(b), freq="M"):
                m5.add((str(s), str(mo)))
    p = cd.needs_path(cfg, 15)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"m5": sorted(m5), "metrics": [], "m1": sorted(m5)}))
    uncond = {}
    nh = grid.E.index("next_hour")
    for per, st in (("train", T), ("val", V)):
        cell = grid.cell(grid.sets.index((grid.W[1], grid.P[1], "volume", grid.levels["volume"][0], None)), nh, 0, 0)
        uncond[per] = {f"H{h} S{s} TR{tr} T{t}": round(float(st["mean"][cell, k]), 4)
                       for k, (h, s, tr, t) in enumerate(grid.cols) if tr is None and t is None}
    out = {"study": 15, "step": "discover", "rows": {"train": int((R["period"] == "train").sum()),
                                                   "val": int((R["period"] == "val").sum())},
           "coins": int(R["sym"].nunique()), "eligible_combinations": int(ok.sum()), "of": int(ok.size),
           "reference_W3_P0.1_volume3x_next_hour_no_filters": uncond, "noise_test": noise, "top": top,
           "candidates": info, "frozen": frozen, "skipped_identical_trades": dup,
           "five_minute_files_needed": len(m5), "seconds": round(time.time() - t0)}
    s14.out_path(cfg, "study15_discover.json").write_text(json.dumps(out, default=_js, indent=1))
    out = {k: v for k, v in out.items() if k != "candidates"}
    out["frozen"] = [{k: f[k] for k in ("name", "disc", "val", "tails_val", "stability")} for f in frozen]
    out["best_validation_candidates"] = sorted(
        [{"name": nm, "disc_mean": x["disc"]["mean"], "val_mean": x["val"]["mean"], "val_lo": x["val"]["lo"],
          "val_n": x["val"]["n"], "val_median": x["tails_val"].get("median"), "stability": x["stability"]}
         for nm, x in info.items()], key=lambda z: -(z["val_mean"] if np.isfinite(z["val_mean"]) else -9))[:10]
    return out


# ---- diagnose, risk, holdout ------------------------------------------------------------------------------------------
def _load(cfg, name):
    p = s14.out_path(cfg, name)
    if not p.exists():
        raise SystemExit(f"{p.name} not found: run the earlier step first")
    return json.loads(p.read_text())


def diagnose(cfg, jobs=1) -> dict:
    D = _load(cfg, "study15_discover.json")
    R = pd.read_parquet(s14.out_path(cfg, "study15_rows.parquet"))
    g_ = cfg["gates"]
    combos = {f["name"]: f["combo"] for f in D["frozen"]}
    if not combos:
        return {"study": 15, "step": "diagnose", "note": "nothing frozen"}
    base = combo_trades(cfg, combos, R, jobs=jobs)
    stress = {sl: combo_trades(cfg, combos, R, jobs=jobs, slip=sl) for sl in param(cfg, "s15_stress_slippage")}
    maker = combo_trades(cfg, combos, R, jobs=jobs, fee_in=param(cfg, "s15_maker_fee"))
    m5 = combo_trades(cfg, combos, R, jobs=jobs, interval="5m")
    res = []
    for f in D["frozen"]:
        nm = f["name"]
        b = base[nm]
        x = {"name": nm, "placebo_same_combo": f["placebo_same_combo"],
             "train": tails(b[b["period"] == "train"]), "val": tails(b[b["period"] == "val"]),
             "costs": {f"slippage {sl}": {"mean": float(stress[sl][nm]["r"].mean()),
                                          "val_mean": float(stress[sl][nm].query("period == 'val'")["r"].mean())}
                       for sl in stress},
             "maker_entry_descriptive": float(maker[nm]["r"].mean())}
        t5 = m5[nm]
        x["five_minute"] = {"coverage": len(t5) / max(len(b), 1), "mean": float(t5["r"].mean()) if len(t5) else None,
                            "hourly_mean_same_trades": float(b.merge(t5[["sym", "entry_ts"]])["r"].mean()) if len(t5) else None}
        first_stress = param(cfg, "s15_stress_slippage")[0]
        x["checks"] = {
            "placebo_same_combo": f["placebo_same_combo"]["p"] <= g_["study15_null_p"],
            "positive_without_best_5pct": bool(x["train"].get("mean_without_best_5pct", -1) > 0
                                               and x["val"].get("mean_without_best_5pct", -1) > 0),
            "positive_at_2x_slippage": bool(x["costs"][f"slippage {first_stress}"]["mean"] > 0),
            "five_minute_positive": bool(x["five_minute"]["coverage"] >= 0.95 and (x["five_minute"]["mean"] or -1) > 0)}
        x["eligible_for_holdout"] = all(x["checks"].values())
        res.append(x)
    out = {"study": 15, "step": "diagnose", "frozen": res, "eligible": [r["name"] for r in res if r["eligible_for_holdout"]]}
    s14.out_path(cfg, "study15_diagnose.json").write_text(json.dumps(out, default=_js, indent=1))
    return out


def risk(cfg, holdout=False, jobs=1) -> dict:
    D = _load(cfg, "study15_discover.json")
    R = pd.read_parquet(s14.out_path(cfg, "study15_holdout_rows.parquet" if holdout else "study15_rows.parquet"))
    combos = {f["name"]: f["combo"] for f in D["frozen"]}
    tr = combo_trades(cfg, combos, R, jobs=jobs, include_holdout=holdout)
    start = cfg["bankroll"]["start_usd"]
    out = []
    for nm, d in combos.items():
        t = tr[nm]
        if t.empty:
            continue
        years = max((t["exit_ts"].max() - t["entry_ts"].min()) / (365.25 * DAY), 1e-9)
        paths = []
        for sh in param(cfg, "s15_risk_shares"):
            p = s14.risk_path(t, d["S"], sh, param(cfg, "s15_max_open"), start)
            paths.append({"risk_share": sh, "final_usd": round(p["final"]), "max_drawdown": p["max_dd"],
                          "cagr": (p["final"] / start) ** (1 / years) - 1 if p["final"] > 0 else -1.0,
                          "taken": p["taken"], "skipped_max_open": p["skipped"]})
        out.append({"name": nm, "trades": len(t), "years": years, "paths": paths})
    return {"study": 15, "step": "risk (descriptive)", "holdout": holdout, "combos": out}


def run_holdout(cfg, jobs=1) -> dict:
    from src.calendar import HoldoutSealed, holdout_unsealed
    if not holdout_unsealed():
        raise HoldoutSealed("Study 15 holdout: set GAMMA_EDGE_RUN_HOLDOUT=1 (only on 'run the holdout')")
    D = _load(cfg, "study15_discover.json")
    elig = set(_load(cfg, "study15_diagnose.json")["eligible"])
    grid = Grid15(cfg)
    ev = pd.read_parquet(s14.out_path(cfg, "study15_events_holdout.parquet"))
    rows = []
    for sym, e in ev.groupby("sym"):
        R, _ = _coin_rows(cfg, grid, sym, e, include_holdout=True)
        if R is not None:
            rows.append(R.drop(columns=["j"]))
    H = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    H.to_parquet(s14.out_path(cfg, "study15_holdout_rows.parquet"))
    R = pd.read_parquet(s14.out_path(cfg, "study15_rows.parquet"))
    combos = {f["name"]: f["combo"] for f in D["frozen"] if f["name"] in elig}
    th = combo_trades(cfg, combos, H, jobs=jobs, include_holdout=True) if len(H) else {k: pd.DataFrame() for k in combos}
    ti = combo_trades(cfg, combos, R, jobs=jobs)
    res = []
    for nm in combos:
        t = th[nm]
        bt = s14.week_lb(t, "r", cfg) if len(t) else {"mean": np.nan, "lo": np.nan, "hi": np.nan}
        ins = float(ti[nm]["r"].mean())
        res.append({"name": nm, "n": len(t), "mean": bt["mean"], "lo": bt["lo"], "hi": bt["hi"], "in_sample_mean": ins,
                    "tails": tails(t), "pass": bool(len(t) >= cfg["gates"]["study15_holdout_min_trades"]
                                                    and bt["mean"] > 0 and bt["lo"] > 0 and bt["mean"] >= 0.5 * ins)})
    return {"study": 15, "step": "holdout (once)", "events": int(len(ev)), "results": res}


def run_all(cfg, jobs, workers) -> dict:
    t0 = time.time()
    out = {"btc_download": s14._download(cfg, cd.btc_keys(cfg), workers, "BTC hourly")}
    e = events(cfg, jobs=jobs)
    s14.out_path(cfg, "study15_events.json").write_text(json.dumps(e, default=_js, indent=1))
    out["events"] = {k: e[k] for k in ("coins_with_events", "events_kept", "events_per_period", "distinct_coin_hours")}
    out["discover"] = discover(cfg, jobs=jobs)
    out["minutes"] = round((time.time() - t0) / 60, 1)
    out["saved"] = "data/derived/crypto/study15_events.json and study15_discover.json"
    out["next"] = ("python -m src.crypto_data --event-data --study 15, then python -m src.study15 --diagnose"
                   if out["discover"]["frozen"] else "nothing frozen: the study stops here")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    for f in ("--all", "--events", "--discover", "--diagnose", "--risk"):
        g.add_argument(f, action="store_true")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--workers", type=int, default=24)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    jobs = a.jobs or s14.default_jobs()
    if not (a.all or a.events or a.discover or a.diagnose or a.risk or a.holdout):
        ap.error("choose --all, --events, --discover, --diagnose, --risk or --holdout")
    if a.holdout and (a.all or a.discover or a.diagnose):
        ap.error("--holdout goes alone, or with --events / --risk")
    if a.all:
        r = run_all(cfg, jobs, a.workers)
    elif a.events:
        r = events(cfg, holdout=a.holdout, jobs=jobs)
    elif a.discover:
        r = discover(cfg, jobs=jobs)
    elif a.diagnose:
        r = diagnose(cfg, jobs=jobs)
    elif a.risk:
        r = risk(cfg, holdout=a.holdout, jobs=jobs)
    else:
        r = run_holdout(cfg, jobs=jobs)
    print(json.dumps(r, default=_js, indent=1))


if __name__ == "__main__":
    main()
