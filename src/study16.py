"""Study 16: three structural crypto theses, market-neutral, tested at once (STUDY16.md); then variations of any
variant that passes.

  python -m src.study16 --all          # panel from the on-disk hourly data -> Stage A (C1 carry, C2 momentum,
                                       # C3 reversal, C4 new-listing short) -> Stage B grid for each variant that passed
  GAMMA_EDGE_RUN_HOLDOUT=1: crypto_data --hourly --holdout, crypto_data --btc --holdout, then study16 --holdout
                                       # once, on "run the holdout"
Options: --jobs N processes (default cores - 1, at most 8).

Portfolio: signals from data up to 00:00 UTC, trades at the 01:00 UTC price; equal dollars long and short (C4: short
new listings, long BTC); funding paid by longs and received by shorts; a coin delisted during the hold exits at its
last price; a short's loss is capped at its notional; 0.05% fee + 0.10% slippage on every unit of weight traded.
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import math
import time

import numpy as np
import pandas as pd

from src import crypto_data as cd
from src import study14 as s14
from src.config import load_config, param

log = logging.getLogger("study16")
HOUR, DAY, WEEK, ORIGIN = s14.HOUR, s14.DAY, s14.WEEK, s14.ORIGIN
VARIANTS = ("c1", "c2", "c3", "c4")
NAMES = {"c1": "C1 funding carry", "c2": "C2 momentum", "c3": "C3 reversal", "c4": "C4 new-listing short"}
_js = s14._js


# ---- panel ---------------------------------------------------------------------------------------------------
def panel_from_frames(frames: dict, fundings: dict, start_ns: int, end_ns: int, trade_hour: int) -> dict:
    """Daily arrays (days x coins): P price at trade_hour UTC, C close at 00:00 UTC, QV previous day's quote volume,
    FH funding settled in (trade time d, trade time d+1], FS funding settled in (00:00 d-1, 00:00 d]."""
    D = int((end_ns - start_ns) // DAY)
    syms = list(frames)
    N = len(syms)
    P, C, QV = (np.full((D, N), np.nan) for _ in range(3))
    FH, FS = np.zeros((D, N)), np.zeros((D, N))
    first, last = np.full(N, np.iinfo("int64").max, "int64"), np.zeros(N, "int64")
    for i, s in enumerate(syms):
        h = frames[s]
        if h is None or len(h) == 0:
            continue
        ts = h["ts"].to_numpy("int64")
        real = h["real"].to_numpy(bool) if "real" in h else np.ones(len(ts), bool)
        if real.any():
            first[i], last[i] = ts[real][0], ts[real][-1] + HOUR
        k = (ts - start_ns) // HOUR
        ok = (k >= 0) & real
        day, hod = k // 24, k % 24
        c = h["close"].to_numpy(float)
        m = ok & (hod == trade_hour - 1) & (day < D)
        P[day[m], i] = c[m]
        m = ok & (hod == 23) & (day + 1 < D)
        C[day[m] + 1, i] = c[m]
        m = (k >= 0) & (day + 1 < D)
        q = np.bincount(day[m] + 1, weights=np.nan_to_num(h["qv"].to_numpy(float)[m]), minlength=D)[:D]
        seen = np.bincount(day[m] + 1, weights=real[m].astype(float), minlength=D)[:D] > 0
        QV[:, i] = np.where(seen, q, np.nan)
        f = fundings.get(s)
        if f is not None and len(f):
            ft, fr = f["ts"].to_numpy("int64"), f["rate"].to_numpy(float)
            dh = -((-(ft - start_ns - trade_hour * HOUR)) // DAY) - 1          # ceil(...) - 1
            mh = (dh >= 0) & (dh < D)
            np.add.at(FH[:, i], dh[mh], fr[mh])
            ds = -((-(ft - start_ns)) // DAY)                                    # ceil(...)
            ms = (ds >= 0) & (ds < D)
            np.add.at(FS[:, i], ds[ms], fr[ms])
    return {"P": P, "C": C, "QV": QV, "FH": FH, "FS": FS, "first_ns": first, "last_ns": last, "syms": syms,
            "day_ns": start_ns + np.arange(D, dtype="int64") * DAY}


def build_panel(cfg, include_holdout=False) -> dict:
    excl = set(param(cfg, "s16_exclude"))
    syms = [s for s in cd.study_symbols(cd.inventory_cache(cfg), cfg) if s not in excl]
    frames, funds, end = {}, {}, 0
    for s in syms:
        h = cd.load_bars(cfg, s, "1h", include_holdout)
        if len(h) == 0 or not h["real"].any():
            continue
        frames[s] = h
        funds[s] = cd.load_funding(cfg, s, include_holdout)
        end = max(end, int(h["ts"].to_numpy("int64")[h["real"].to_numpy(bool)][-1] + HOUR))
    start = min(int(h["ts"].iloc[0]) for h in frames.values())
    start = (start // DAY) * DAY
    end = (end // DAY) * DAY
    th = param(cfg, "s16_trade_hour")
    pn = panel_from_frames(frames, funds, start, end, th)
    b = panel_from_frames({"BTCUSDT": cd.load_bars(cfg, "BTCUSDT", "1h", include_holdout)},
                          {"BTCUSDT": cd.load_funding(cfg, "BTCUSDT", include_holdout)}, start, end, th)
    pn["btc"] = {"P": b["P"][:, 0], "FH": b["FH"][:, 0]}
    pn["new_from_ns"] = pd.Timestamp(param(cfg, "s16_start"), tz="UTC").value
    pn["rebalance_from"] = int(np.searchsorted(pn["day_ns"], pn["new_from_ns"]))
    return pn


# ---- simulator --------------------------------------------------------------------------------------------------
def _ffill(a):
    return pd.DataFrame(a).ffill().to_numpy()


def _prep(pn, spec):
    key = (spec.get("volume_days"), spec.get("vol_days"), spec["kind"] == "c4")
    cache = pn.setdefault("_cache", {})
    if key not in cache:
        QV = pd.DataFrame(pn["QV"])
        vd = spec["volume_days"]
        vm = QV.rolling(vd, min_periods=1 if spec["kind"] == "c4" else vd).median().to_numpy()
        lr = np.log(pd.DataFrame(pn["C"])).diff()
        vol = lr.rolling(spec["vol_days"], min_periods=spec["vol_days"]).std().to_numpy()
        cache[key] = (vm, vol)
    if "Pf" not in cache:
        cache["Pf"] = _ffill(pn["P"])
        cache["cumFH"] = np.vstack([np.zeros(pn["FH"].shape[1]), np.cumsum(np.nan_to_num(pn["FH"]), 0)])
        cache["cumFS"] = np.vstack([np.zeros(pn["FS"].shape[1]), np.cumsum(np.nan_to_num(pn["FS"]), 0)])
        b = pn.get("btc")
        if b is not None:
            cache["bPf"] = _ffill(b["P"][:, None])[:, 0]
            cache["bcum"] = np.concatenate([[0.0], np.cumsum(np.nan_to_num(b["FH"]))])
    return cache[key] + (cache,)


def simulate(pn: dict, spec: dict, rng=None, day_from=None, day_to=None) -> pd.DataFrame:
    """One market-neutral portfolio rebalanced every hold_d days. rng: placebo (C1-C3 shuffled ranks; C4 random
    coins listed >= placebo_min_age_d earlier). Rows: one per rebalance period."""
    P, C, day_ns = pn["P"], pn["C"], pn["day_ns"]
    D, N = P.shape
    H, kind = int(spec["hold_d"]), spec["kind"]
    vm, vol, cache = _prep(pn, spec)
    Pf, cumFH, cumFS = cache["Pf"], cache["cumFH"], cache["cumFS"]
    d0 = pn.get("rebalance_from", 0) if day_from is None else day_from
    d1 = D if day_to is None else min(D, day_to)
    rows, prev = [], np.zeros(N + 1)
    bP, bcum = cache.get("bPf"), cache.get("bcum")
    for d in range(d0, d1 - H, H):
        age = (day_ns[d] - pn["first_ns"]) / DAY
        base = np.isfinite(P[d]) & (day_ns[d] < pn["last_ns"]) & (np.nan_to_num(vm[d], nan=-1) >= spec["min_volume"])
        w = np.zeros(N + 1)                                                    # last slot: BTC (C4 hedge)
        if kind == "c4":
            new = (base & (age >= spec["delay_d"]) & (age < spec["delay_d"] + spec["window_d"])
                   & (pn["first_ns"] >= pn.get("new_from_ns", np.iinfo("int64").min)))   # listed after the archive began
            idx = np.flatnonzero(new)
            if rng is not None and len(idx):
                pool = np.flatnonzero(base & (age >= spec["placebo_min_age_d"]))
                idx = rng.choice(pool, size=len(idx), replace=False) if len(pool) >= len(idx) else np.array([], int)
            if len(idx) >= spec["min_names"] and bP is not None and np.isfinite(bP[d]) and bP[d] > 0:
                w[idx] = -0.5 / len(idx)
                w[N] = 0.5
        else:
            L = int(spec["lookback_d"])
            elig = base & np.isfinite(C[d]) & (age >= max(spec["min_history_d"], L))
            if kind == "c1":
                score = cumFS[d + 1] - cumFS[d + 1 - L] if d + 1 - L >= 0 else np.full(N, np.nan)
            else:
                score = C[d] / C[d - L] - 1 if d - L >= 0 else np.full(N, np.nan)
            elig &= np.isfinite(score)
            idx = np.flatnonzero(elig)
            n = int(math.floor(spec["quantile"] * len(idx)))
            if n >= spec["min_names"]:
                sc = score[idx] if rng is None else rng.permutation(score[idx])
                order = idx[np.argsort(sc, kind="stable")]
                low, high = order[:n], order[::-1][:n]
                long_, short_ = (low, high) if kind in ("c1", "c3") else (high, low)
                for leg, sign in ((long_, 1.0), (short_, -1.0)):
                    if spec.get("weighting") == "inv_vol":
                        iv = 1 / vol[d][leg]
                        iv = np.where(np.isfinite(iv) & (iv > 0), iv, np.nan)
                        iv = np.where(np.isfinite(iv), iv, np.nanmean(iv) if np.isfinite(iv).any() else 1.0)
                        w[leg] = sign * 0.5 * iv / iv.sum()
                    else:
                        w[leg] = sign * 0.5 / len(leg)
        wc = w[:N]
        lm, sm = wc > 0, wc < 0
        r = Pf[d + H] / P[d] - 1
        fund = cumFH[d + H] - cumFH[d]
        gross = float(np.sum(wc[lm] * (r[lm] - fund[lm])) + np.sum(-wc[sm] * np.maximum(-r[sm] + fund[sm], -1.0)))
        fpart = float(-np.sum(wc[lm] * fund[lm]) + np.sum(-wc[sm] * fund[sm]))
        if w[N]:
            fb = bcum[d + H] - bcum[d]
            gross += w[N] * (bP[d + H] / bP[d] - 1 - fb)
            fpart -= w[N] * fb
        turnover = float(np.abs(w - prev).sum())
        cost = turnover * spec["cost"]
        rows.append({"start": d, "day_ns": int(day_ns[d]), "week": int((day_ns[d] - ORIGIN) // WEEK),
                     "traded": bool(lm.any() or sm.any()), "n_long": int(lm.sum() + (w[N] > 0)),
                     "n_short": int(sm.sum()), "gross": gross, "funding": fpart,
                     "turnover": turnover, "cost": cost, "net": gross - cost})
        prev = w
    return pd.DataFrame(rows, columns=["start", "day_ns", "week", "traded", "n_long", "n_short", "gross", "funding",
                                       "turnover", "cost", "net"])


def weekly(per: pd.DataFrame, hold_d: int, tail: float = 0.05) -> dict:
    """Calendar view (flat periods count as 0): mean net return per week, and without the best share of weeks."""
    if per.empty:
        return {"mean": np.nan, "without_best": np.nan, "weeks": 0}
    k = 7 / hold_d
    wk = per.groupby("week")["net"].mean() * k
    s = np.sort(wk.to_numpy())
    drop = int(math.ceil(tail * len(s)))
    return {"mean": float(per["net"].mean() * k), "without_best": float(s[: len(s) - drop].mean()) if len(s) > drop else np.nan,
            "weeks": int(len(wk)), "weeks_traded": int(per.loc[per["traded"], "week"].nunique())}


def summary(per: pd.DataFrame, hold_d: int, cfg) -> dict:
    w = weekly(per, hold_d, cfg["gates"]["study16_tail_share"])
    k = 7 / hold_d
    bt = s14.week_lb(per, "net", cfg) if len(per) else {"lo": np.nan, "hi": np.nan}
    eq = np.cumprod(1 + per["net"].to_numpy())
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq))) if len(eq) else np.nan
    yrs = len(per) * hold_d / 365.25
    sd = per["net"].std()
    return {**w, "lo": bt["lo"] * k, "hi": bt["hi"] * k,
            "annual_return": float(eq[-1] ** (1 / yrs) - 1) if len(eq) and eq[-1] > 0 else -1.0,
            "sharpe": float(per["net"].mean() / sd * math.sqrt(365.25 / hold_d)) if sd > 0 else np.nan,
            "max_drawdown": dd, "turnover_per_period": float(per["turnover"].mean()),
            "cost_per_week": float(per["cost"].mean() * k), "funding_per_week": float(per["funding"].mean() * k),
            "share_traded": float(per["traded"].mean())}


# ---- specs ------------------------------------------------------------------------------------------------------
def base_spec(cfg, v: str, slip=None) -> dict:
    s = {"kind": v, "quantile": param(cfg, "s16_quantile"), "min_volume": param(cfg, "s16_min_volume_usd"),
         "volume_days": param(cfg, "s16_volume_days"), "vol_days": param(cfg, "s16_vol_days"),
         "min_names": param(cfg, "s16_min_names"), "min_history_d": param(cfg, "s16_min_history_d"),
         "weighting": "equal", "placebo_min_age_d": param(cfg, "s16_c4_placebo_min_age_d"),
         "cost": param(cfg, "s16_fee") + (param(cfg, "s16_slippage") if slip is None else slip)}
    if v == "c4":
        s.update(window_d=param(cfg, "s16_c4_window_d"), delay_d=param(cfg, "s16_c4_delay_d"),
                 hold_d=param(cfg, "s16_c4_hold_d"))
    else:
        s.update(lookback_d=param(cfg, f"s16_{v}_lookback_d"), hold_d=param(cfg, f"s16_{v}_hold_d"))
    return s


def grid_specs(cfg, v: str) -> list[dict]:
    base = base_spec(cfg, v)
    g = param(cfg, f"s16_grid_{v}")
    axes = {"min_volume": param(cfg, "s16_grid_min_volume"), "weighting": param(cfg, "s16_grid_weighting"),
            **({} if v == "c4" else {"quantile": param(cfg, "s16_grid_quantile")}), **g}
    keys = list(axes)
    return [{**base, **dict(zip(keys, vals))} for vals in itertools.product(*(axes[k] for k in keys))], axes


def spec_name(s: dict) -> str:
    parts = [NAMES[s["kind"]]]
    for k in ("lookback_d", "window_d", "delay_d", "hold_d", "quantile", "min_volume", "weighting"):
        if k in s and not (s["kind"] == "c4" and k == "quantile"):
            parts.append(f"{k}={s[k]}")
    return " ".join(parts)


def _split_days(cfg, pn):
    b = s14.bounds(cfg)
    return int(np.searchsorted(pn["day_ns"], b["train_end"])), int(np.searchsorted(pn["day_ns"], b["holdout"]))


# ---- Stage A ----------------------------------------------------------------------------------------------------
_PN = {}


def _panel_cached(cfg, include_holdout=False):
    if include_holdout not in _PN:
        p = s14.out_path(cfg, "study16_panel_holdout.npz" if include_holdout else "study16_panel.npz")
        if p.exists():
            z = np.load(p, allow_pickle=True)
            pn = {k: z[k] for k in z.files if k not in ("syms", "btcP", "btcFH", "rebalance_from", "new_from_ns")}
            pn["new_from_ns"] = int(z["new_from_ns"])
            pn["syms"] = list(z["syms"])
            pn["btc"] = {"P": z["btcP"], "FH": z["btcFH"]}
            pn["rebalance_from"] = int(z["rebalance_from"])
        else:
            pn = build_panel(cfg, include_holdout)
            np.savez_compressed(p, P=pn["P"], C=pn["C"], QV=pn["QV"], FH=pn["FH"], FS=pn["FS"], first_ns=pn["first_ns"],
                                last_ns=pn["last_ns"], day_ns=pn["day_ns"], syms=np.array(pn["syms"]),
                                btcP=pn["btc"]["P"], btcFH=pn["btc"]["FH"], rebalance_from=pn["rebalance_from"],
                                new_from_ns=pn["new_from_ns"])
        _PN[include_holdout] = pn
    return _PN[include_holdout]


def _placebo_task(task):
    cfg, spec, reps, day_to = task
    pn = _panel_cached(cfg)
    out = []
    for rep in reps:
        rng = np.random.default_rng([param(cfg, "s16_seed"), rep, VARIANTS.index(spec["kind"])])
        per = simulate(pn, spec, rng=rng, day_to=day_to)
        out.append(per["net"].mean() * 7 / spec["hold_d"])
    return out


def stage_a(cfg, jobs=1) -> dict:
    pn = _panel_cached(cfg)
    split, hold = _split_days(cfg, pn)
    g = cfg["gates"]
    reps = param(cfg, "s16_placebo_reps")
    res = {}
    for v in VARIANTS:
        spec = base_spec(cfg, v)
        per = simulate(pn, spec, day_to=hold)
        per = per[per["start"] + spec["hold_d"] < hold]
        a, b = per[per["start"] + spec["hold_d"] < split], per[per["start"] >= split]
        allp = summary(per, spec["hold_d"], cfg)
        chunks = [list(range(reps))[i::max(1, jobs)] for i in range(max(1, jobs))]
        pm = [x for lst in s14._run(_placebo_task, [(cfg, spec, c, hold) for c in chunks if c], jobs) for x in lst]
        stress = simulate(pn, base_spec(cfg, v, slip=param(cfg, "s16_stress_slippage")), day_to=hold)
        sa, sb = summary(a, spec["hold_d"], cfg), summary(b, spec["hold_d"], cfg)
        checks = {"mean_and_lower_bound_positive": bool(allp["mean"] > 0 and allp["lo"] > 0),
                  "beats_placebo": s14.null_p(allp["mean"], pm) <= g["study16_p"],
                  "positive_both_halves": bool(sa["mean"] > 0 and sb["mean"] > 0),
                  "positive_without_best_weeks": bool(allp["without_best"] > 0)}
        res[v] = {"name": NAMES[v], "spec": spec_name(spec), "all": allp, "2020-2023": sa, "2024-2025": sb,
                  "placebo": {"mean": float(np.nanmean(pm)), "p95": float(np.nanquantile(pm, 0.95)),
                              "p": s14.null_p(allp["mean"], pm)},
                  "at_2x_slippage_per_week": float(stress["net"].mean() * 7 / spec["hold_d"]),
                  "checks": checks, "pass": all(checks.values())}
        log.info("%s: %s, weekly %.4f, placebo p %.3f", v, "PASS" if res[v]["pass"] else "fail", allp["mean"],
                 res[v]["placebo"]["p"])
    return res


# ---- Stage B ----------------------------------------------------------------------------------------------------
def _grid_task(task):
    cfg, specs, reps, day_to = task
    pn = _panel_cached(cfg)
    out = []
    for si, spec in specs:
        if reps is None:
            per = simulate(pn, spec, day_to=day_to)
            out.append((si, per))
        else:
            vals = []
            for rep in reps:
                rng = np.random.default_rng([param(cfg, "s16_seed"), 1000 + rep, si])
                per = simulate(pn, spec, rng=rng, day_to=day_to)
                per = per[per["start"] + spec["hold_d"] < day_to]
                ok = per["traded"].groupby(per["week"]).any().sum() >= cfg["gates"]["study16_min_weeks"]
                vals.append(per["net"].mean() * 7 / spec["hold_d"] if ok else np.nan)
            out.append((si, vals))
    return out


def stage_b(cfg, v: str, jobs=1) -> dict:
    pn = _panel_cached(cfg)
    split, hold = _split_days(cfg, pn)
    g = cfg["gates"]
    specs, axes = grid_specs(cfg, v)
    tasks = [(cfg, [(i, s) for i, s in enumerate(specs)][k::max(1, jobs)], None, hold) for k in range(max(1, jobs))]
    pers = dict(x for lst in s14._run(_grid_task, tasks, jobs) for x in lst)
    rows = []
    for i, s in enumerate(specs):
        per = pers[i]
        per = per[per["start"] + s["hold_d"] < hold]
        disc = per[per["start"] + s["hold_d"] < split]
        val = per[per["start"] >= split]
        wd = weekly(disc, s["hold_d"], g["study16_tail_share"])
        rows.append({"i": i, "disc": wd["mean"], "weeks_traded": wd["weeks_traded"], "per": per, "disc_per": disc,
                     "val_per": val})
    ok = [r for r in rows if r["weeks_traded"] >= g["study16_min_weeks"] and np.isfinite(r["disc"])]
    real_best = max((r["disc"] for r in ok), default=np.nan)
    reps = param(cfg, "s16_explore_reps")
    ptasks = [(cfg, [(i, s) for i, s in enumerate(specs)][k::max(1, jobs)], list(range(reps)), split)
              for k in range(max(1, jobs))]
    pv = dict(x for lst in s14._run(_grid_task, ptasks, jobs) for x in lst)
    pbest = [np.nanmax([pv[i][r] for i in range(len(specs))]) for r in range(reps)]
    noise = {"real_best": real_best, "placebo_median": float(np.nanmedian(pbest)),
             "placebo_95": float(np.nanquantile(pbest, 0.95)), "p": s14.null_p(real_best, pbest)}
    by = {r["i"]: r for r in rows}
    key = {tuple(sorted((k, specs[i][k]) for k in axes)): i for i in range(len(specs))}
    top = sorted(ok, key=lambda r: -r["disc"])[: param(cfg, "s16_explore_top_k")]
    cands = []
    for r in top:
        s = specs[r["i"]]
        nb = []
        for k, vals in axes.items():
            if k == "weighting":
                continue
            o = sorted(vals)
            j = o.index(s[k])
            for jj in (j - 1, j + 1):
                if 0 <= jj < len(o):
                    t = tuple(sorted((kk, (o[jj] if kk == k else s[kk])) for kk in axes))
                    if t in key and by[key[t]]["weeks_traded"] >= g["study16_min_weeks"]:
                        nb.append(by[key[t]]["disc"] > 0)
        stab = float(np.mean(nb)) if nb else 0.0
        vs = summary(r["val_per"], s["hold_d"], cfg)
        stress = simulate(pn, {**s, "cost": param(cfg, "s16_fee") + param(cfg, "s16_stress_slippage")}, day_to=hold)
        stress = stress[stress["start"] + s["hold_d"] < hold]
        allw = weekly(r["per"], s["hold_d"], g["study16_tail_share"])
        checks = {"validation_lower_bound_positive": bool(vs["lo"] > 0), "stable": stab >= g["study16_neighbour_share"],
                  "positive_at_2x_slippage": bool(stress["net"].mean() > 0),
                  "positive_without_best_weeks": bool(allw["without_best"] > 0)}
        cands.append({"name": spec_name(s), "spec": s, "discovery_per_week": r["disc"], "validation": vs,
                      "stability": stab, "at_2x_slippage_per_week": float(stress["net"].mean() * 7 / s["hold_d"]),
                      "checks": checks, "ok": all(checks.values())})
    frozen = []
    if noise["p"] <= g["study16_p"]:
        frozen = sorted([c for c in cands if c["ok"]], key=lambda c: -c["validation"]["mean"])[: g["study16_max_frozen"]]
    return {"variant": NAMES[v], "grid_size": len(specs), "eligible": len(ok), "noise_test": noise,
            "top": cands, "frozen": [{k: c[k] for k in ("name", "spec", "discovery_per_week", "validation", "stability")}
                                     for c in frozen]}


# ---- holdout ------------------------------------------------------------------------------------------------------
def run_holdout(cfg) -> dict:
    from src.calendar import HoldoutSealed, holdout_unsealed
    if not holdout_unsealed():
        raise HoldoutSealed("Study 16 holdout: set GAMMA_EDGE_RUN_HOLDOUT=1 (only on 'run the holdout')")
    p = s14.out_path(cfg, "study16_results.json")
    R = json.loads(p.read_text())
    specs = [f["spec"] for b in R.get("stage_b", {}).values() for f in b["frozen"]]
    if not specs:
        specs = [base_spec(cfg, v) for v, a in R["stage_a"].items() if a["pass"]]
    pn = _panel_cached(cfg, include_holdout=True)
    hs = int(np.searchsorted(pn["day_ns"], s14.bounds(cfg)["holdout"]))
    ins = _panel_cached(cfg)
    out = []
    for s in specs:
        per = simulate(pn, s, day_from=hs)
        sm = summary(per, s["hold_d"], cfg)
        ref = simulate(ins, s)
        ref_mean = float(ref["net"].mean() * 7 / s["hold_d"])
        out.append({"name": spec_name(s), "holdout": sm, "in_sample_per_week": ref_mean,
                    "pass": bool(sm["weeks"] >= cfg["gates"]["study16_holdout_min_weeks"] and sm["mean"] > 0
                                 and sm["lo"] > 0 and sm["mean"] >= 0.5 * ref_mean)})
    return {"study": 16, "step": "holdout (once)", "results": out}


def run_all(cfg, jobs) -> dict:
    t0 = time.time()
    pn = _panel_cached(cfg)
    log.info("panel: %d days x %d coins", *pn["P"].shape)
    a = stage_a(cfg, jobs)
    b = {}
    for v, x in a.items():
        if x["pass"]:
            log.info("stage B for %s", v)
            b[v] = stage_b(cfg, v, jobs)
    out = {"study": 16, "panel": {"days": int(pn["P"].shape[0]), "coins": int(pn["P"].shape[1]),
                                  "first_rebalance": str(pd.Timestamp(pn["day_ns"][pn["rebalance_from"]], tz="UTC").date())},
           "stage_a": a, "stage_b": b,
           "next": ("frozen variations exist: the sealed year is the last step (only on 'run the holdout')"
                    if any(x["frozen"] for x in b.values()) else
                    "stage A passes but nothing froze in stage B" if b else "no variant passed stage A: stop"),
           "minutes": round((time.time() - t0) / 60, 1)}
    s14.out_path(cfg, "study16_results.json").write_text(json.dumps(out, default=_js, indent=1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--holdout", action="store_true")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    r = run_all(cfg, a.jobs or s14.default_jobs()) if a.all else run_holdout(cfg)
    print(json.dumps(r, default=_js, indent=1))


if __name__ == "__main__":
    main()
