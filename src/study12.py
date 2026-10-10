"""Study 12 (Matteo 2026-10-09: "yes"): the Study 1 gamma-level fade traded in SPY shares at $0 commission.

Study 1 found a gross edge of about +0.10R (0.6 ES tick) on the naive fade against ~2.3 ticks of friction, most of it
the conservative fill rules in 0.25-point ES ticks. SPY's tick is one cent (~0.1 SPX point, ~0.4 ES tick), and
commission is $0. Same touches, same levels, same rules; only the instrument and its costs change.

  levels   (ES level - B_D) x r_D, r_D = SPY mid at the first 1-second quote at or after 09:30 ET / (the 09:30 ES
           bar's open - B_D); B_D = the ES-SPX basis from D-1 closes (gex_daily), the same B_D the levels were built
           with; all known before the first touch at 09:31; rounded to the cent. EM_spy = EM x r_D.
           (The first run omitted B_D, which shifted levels a few cents toward price: superseded, RUNLOG.)
  naive    (every in-sample touch) a limit at L from the touch bar's open for reclaim_window minutes, filled only when
           the far quote is one cent through it (ask <= L - 1c for a buy); stop L - d x round(fail_F EM_spy), target
           L + d x round(target_mult x that); stops exit one cent beyond, targets fill only when the near quote is one
           cent beyond; time exit at fill + time_exit or flat_time at the quote minus one cent. Quotes are 1-second
           snapshots: the stop is checked before the target in every second (same-second ambiguity = loss).
  confirmed (Stage 3 days with ES order flow features) entry at the first quote after t_dec, the far side plus
           entry_slippage cents; stop = p_ext x r_D - d x stop_buffer ES ticks x r_D; min_risk / max_risk as Study 1
           (distances in index terms, mapped by r_D; costs in SPY cents).
  costs    commission s12_commission_per_share_usd, SEC fee s12_sec_fee_rate x the sale, FINRA TAF a share sold.
           PnL_R = d (X - E) / R - C / R.
  gate     (SPEC Stage 3, gamma-tagged touches) >= study12_min_trades, expectancy >= study12_min_expectancy_r after
           costs, 90% day-bootstrap lower bound > 0; confirmed also needs confirmed - naive > 0 (lower bound).

  python -m src.study12 --count                         # sessions and touches (no data, no cost)
  python -m src.study12 --pull --price-only             # exact quote (nothing pulled)
  python -m src.study12 --pull --approve-usd X --allow-past-total
  python -m src.study12 --run                           # trades + report (saves study12_trades)
  python -m src.study12 --report-only
"""
from __future__ import annotations

import argparse
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from src import calendar as calm
from src import spend, stats, store, study10
from src.config import data_path, load_config, param

log = logging.getLogger("study12")
EPS = 1e-9


def rnd(x: float, tick: float) -> float:
    return float(round(np.round(x / tick) * tick, 10))


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def spy_path(cfg, day):
    return data_path(cfg, "raw", "equity", param(cfg, "s12_symbol").lower(),
                     param(cfg, "s12_schema").replace("-", "_"), f"{day}.parquet")


def spy_args(cfg, day) -> dict:
    m = cfg["market"]
    return dict(dataset=param(cfg, "s12_dataset"), symbols=[param(cfg, "s12_symbol")], stype_in="raw_symbol",
                schema=param(cfg, "s12_schema"), start=calm.et_time(day, m["rth_open"]).isoformat(),
                end=calm.et_time(day, m["rth_close"]).isoformat())


def pull(cfg, cl, budget, days, price_only: bool, workers: int = 1) -> dict:
    out = {"pieces": 0, "usd": 0.0, "written": 0, "skipped": 0}
    jobs = []
    for d in days:
        if spy_path(cfg, d).exists():
            out["skipped"] += 1
        else:
            jobs.append(d)
    out["pieces"] = len(jobs)
    get = cl if callable(cl) else (lambda: cl)

    def one(d):
        c = get()
        if price_only:
            return budget.price(c, spy_args(cfg, d)), False
        data, cost = budget.pull(c, "study12", f"SPY {param(cfg, 's12_schema')} {d}", spy_args(cfg, d))
        df = data.to_df()
        if "ts_recv" not in df.columns:
            df = df.reset_index()
        store.write(df, spy_path(cfg, d))
        return cost, True

    if workers > 1:
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(one, jobs))
    else:
        res = [one(d) for d in jobs]
    for cost, wrote in res:
        out["usd"] += cost
        out["written"] += int(wrote)
    return out


def load_spy(cfg, day) -> pd.DataFrame | None:
    p = spy_path(cfg, day)
    if not p.exists():
        return None
    q = study10.norm_quotes(store.read(p))
    q = q[(q["bid"] > 0) & (q["ask"] > q["bid"] - EPS)]
    return q[["ts", "bid", "ask"]].sort_values("ts").reset_index(drop=True)


def spy_ratio(spy: pd.DataFrame, es_open_0930: float, day, rth_open: str, basis: float = 0.0) -> float:
    """r = SPY mid at the first quote at or after the open / SPX at the open (ES open - basis B_D)."""
    t0 = calm.et_time(day, rth_open)
    after = spy[spy["ts"] >= t0]
    spx0 = es_open_0930 - basis
    if after.empty or not np.isfinite(spx0) or spx0 <= 0:
        return np.nan
    r = after.iloc[0]
    return float((r["bid"] + r["ask"]) / 2 / spx0)


def to_spy(x_es: float, ratio: float, tick: float, basis: float = 0.0) -> float:
    return rnd((x_es - basis) * ratio, tick)


# ---------------------------------------------------------------------------
# Simulation on 1-second quotes
# ---------------------------------------------------------------------------
def _arr(spy: pd.DataFrame):
    return (spy["ts"].dt.as_unit("ns").astype("int64").to_numpy(), spy["bid"].to_numpy(float),
            spy["ask"].to_numpy(float))


def walk(t, p, start: int, S: float, T: float, d: int, t_exit: int, tick: float):
    """(exit, reason, index) for a position from quote index start on; p = the exit side (bid for a long).
    Stop before target in the same second; time exit at the first quote at or after t_exit, minus one cent."""
    n = len(t)
    if start >= n:
        return np.nan, "no_data", -1
    end = int(np.searchsorted(t, t_exit, "left"))
    seg = p[start:max(start, end)]
    ks = np.flatnonzero(d * (seg - S) <= EPS)
    kt = np.flatnonzero(d * (seg - T) >= tick - EPS)
    k_s = ks[0] if ks.size else np.inf
    k_t = kt[0] if kt.size else np.inf
    if k_s <= k_t and np.isfinite(k_s):
        j = start + int(k_s)
        fill = S - d * tick
        return (fill if d * (p[j] - fill) >= -EPS else p[j]), "stop", j
    if np.isfinite(k_t):
        return T, "target", start + int(k_t)
    if end < n:
        return p[end] - d * tick, "time", end
    return p[-1] - d * tick, "data_end", n - 1


def fees(E: float, X: float, d: int, cfg) -> float:
    """$ a share for the round trip: commission both sides, SEC fee and TAF on the sale."""
    sale = X if d == 1 else E
    return (2 * param(cfg, "s12_commission_per_share_usd") + param(cfg, "s12_sec_fee_rate") * sale
            + param(cfg, "s12_taf_per_share_usd"))


def _result(t, i, j, E, S, T, R, X, why, d, cfg):
    C = fees(E, X, d, cfg)
    return {"entry_ts": pd.Timestamp(t[i], tz="UTC"), "E": E, "S": S, "T": T, "R_k": R, "X": X, "exit_reason": why,
            "exit_ts": pd.Timestamp(t[j], tz="UTC"), "pnl_r": d * (X - E) / R - C / R,
            "usd_per_100": 100 * (d * (X - E) - C)}


def naive_trade(spy: pd.DataFrame, t0: pd.Timestamp, L: float, d: int, em: float, day, cfg) -> dict:
    tick = param(cfg, "s12_tick_usd")
    t, bid, ask = _arr(spy)
    far = ask if d == 1 else bid
    a = int(np.searchsorted(t, pd.Timestamp(t0).value, "left"))
    b = int(np.searchsorted(t, (pd.Timestamp(t0) + pd.Timedelta(minutes=param(cfg, "reclaim_window"))).value, "left"))
    k = np.flatnonzero(d * (L - far[a:b]) >= tick - EPS)
    if not k.size:
        return {"skip": "no_fill"}
    i = a + int(k[0])
    flat = calm.et_time(day, cfg["market"]["flat_time"]).value
    if t[i] >= flat:
        return {"skip": "too_late"}
    dist = max(tick, rnd(param(cfg, "fail_F") * em, tick))
    E, S, T = L, rnd(L - d * dist, tick), rnd(L + d * max(tick, rnd(param(cfg, "target_mult") * dist, tick)), tick)
    t_exit = min(t[i] + pd.Timedelta(minutes=param(cfg, "time_exit")).value, flat)
    X, why, j = walk(t, bid if d == 1 else ask, i + 1, S, T, d, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return _result(t, i, j, E, S, T, dist, X, why, d, cfg)


def confirmed_trade(spy: pd.DataFrame, feat: dict, d: int, em: float, ratio: float, day, cfg,
                    basis: float = 0.0) -> dict | None:
    if not feat.get("confirmed"):
        return None
    tick, es_tick = param(cfg, "s12_tick_usd"), cfg["market"]["tick"]
    t, bid, ask = _arr(spy)
    i = int(np.searchsorted(t, pd.Timestamp(feat["t_dec"]).value, "right"))
    if i >= len(t):
        return {"skip": "no_entry_quote"}
    flat = calm.et_time(day, cfg["market"]["flat_time"]).value
    if t[i] >= flat:
        return {"skip": "too_late"}
    E = rnd((ask[i] if d == 1 else bid[i]) + d * param(cfg, "entry_slippage") * tick, tick)
    S = rnd(to_spy(feat["p_ext"], ratio, tick, basis) - d * rnd(param(cfg, "stop_buffer") * es_tick * ratio, tick), tick)
    R = rnd(d * (E - S), tick)
    if R > param(cfg, "max_risk") * em + EPS:
        return {"skip": "risk_too_wide", "R_k": R}
    min_r = max(tick, rnd(param(cfg, "min_risk") * es_tick * ratio, tick))
    if R < min_r - EPS:
        S, R = rnd(E - d * min_r, tick), min_r
    T = rnd(E + d * max(tick, rnd(param(cfg, "target_mult") * R, tick)), tick)
    t_exit = min(pd.Timestamp(feat["t_dec"]).value + pd.Timedelta(minutes=param(cfg, "time_exit")).value, flat)
    X, why, j = walk(t, bid if d == 1 else ask, i + 1, S, T, d, t_exit, tick)
    if not np.isfinite(X):
        return {"skip": why}
    return _result(t, i, j, E, S, T, R, X, why, d, cfg)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def touch_days(cfg) -> list:
    tc = store.load_derived("touches", cfg)
    return sorted(d for d in tc["date"].unique() if calm.in_sample(d, cfg))


def run(cfg=None, save: bool = True) -> tuple[pd.DataFrame, dict]:
    cfg = cfg or load_config()
    tc = store.load_derived("touches", cfg)
    feats = store.load_derived("features", cfg) if store.derived_path(cfg, "features").exists() else pd.DataFrame()
    fmap = feats.set_index("touch_id").to_dict("index") if len(feats) else {}
    rows, info = [], {"days": 0, "days_missing_spy": 0, "days_no_ratio": 0}
    gx = store.load_derived("gex_daily", cfg).set_index("date")["basis"]
    tick = param(cfg, "s12_tick_usd")
    for day, g in tc.groupby("date"):
        if not calm.in_sample(day, cfg):
            continue
        info["days"] += 1
        spy = load_spy(cfg, day)
        if spy is None or spy.empty:
            info["days_missing_spy"] += 1
            continue
        # es_open = the open of the session's 09:30 ES bar on its front contract (levels table)
        B = float(gx.get(day, np.nan))
        ratio = spy_ratio(spy, float(g["es_open"].iloc[0]), day, cfg["market"]["rth_open"], B)
        if not np.isfinite(ratio):
            info["days_no_ratio"] += 1
            continue
        for tcr in g.itertuples():
            L, em = to_spy(tcr.level_es, ratio, tick, B), float(tcr.em) * ratio
            common = {"touch_id": tcr.touch_id, "date": day, "group": tcr.group, "is_gamma": bool(tcr.is_gamma),
                      "d": int(tcr.d), "level_es": tcr.level_es, "level_spy": L, "ratio": ratio, "basis": B, "em_spy": em,
                      "tod": tcr.tod, "stage3_day": tcr.touch_id in fmap or None}
            rows.append({**common, "mode": "naive", **naive_trade(spy, pd.Timestamp(tcr.t0), L, int(tcr.d), em, day, cfg)})
            f = fmap.get(tcr.touch_id)
            if f is not None:
                c = confirmed_trade(spy, f, int(tcr.d), em, ratio, day, cfg, B)
                if c is not None:
                    rows.append({**common, "mode": "confirmed", **c})
    T = pd.DataFrame(rows)
    if save:
        store.save_derived(T, "study12_trades", cfg)
    return T, info


def verdict(x: pd.DataFrame, cfg) -> dict:
    g = cfg["gates"]
    x = x.dropna(subset=["pnl_r"])
    b = stats.day_bootstrap_mean(x, "pnl_r", param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"),
                                 param(cfg, "ci_level"))
    ok = (b["n"] >= g["study12_min_trades"] and np.isfinite(b["mean"]) and b["mean"] >= g["study12_min_expectancy_r"]
          and np.isfinite(b["lo"]) and b["lo"] > 0)
    return {"n": b["n"], "days": b["days"], "mean_r": b["mean"], "ci_lo": b["lo"], "ci_hi": b["hi"],
            "verdict": "PASS" if ok else "KILL"}


def _summary(x: pd.DataFrame) -> dict:
    x = x.dropna(subset=["pnl_r"])
    if x.empty:
        return {"n": 0}
    return {"n": int(len(x)), "mean_r": float(x["pnl_r"].mean()), "win_rate": float((x["pnl_r"] > 0).mean()),
            "usd_per_100_shares": float(x["usd_per_100"].mean()),
            "mean_risk_cents": float(x["R_k"].mean() * 100),
            "exits": {k: int(v) for k, v in x["exit_reason"].value_counts().items()},
            "gross_r_before_fees": float((x["d"] * (x["X"] - x["E"]) / x["R_k"]).mean())}


def report(T: pd.DataFrame, cfg, info: dict | None = None) -> dict:
    out = {"note": "Study 12: Study 1 level fade in SPY shares, 1-second quotes, one-cent fill rules, $0 commission; "
                   "in sample; gate = SPEC Stage 3 on gamma-tagged touches", "data": info or {}}
    for mode in ("naive", "confirmed"):
        x = T[T["mode"] == mode]
        if x.empty:
            continue
        r = {"skips": {k: int(v) for k, v in x["skip"].value_counts().items()} if "skip" in x else {},
             "gamma_tagged": {**verdict(x[x["is_gamma"]], cfg), **_summary(x[x["is_gamma"]])},
             "by_group": {str(k): {**_summary(g), "ci": verdict(g, cfg)} for k, g in x.groupby("group")},
             "by_tod": {str(k): _summary(g[g["is_gamma"]]) for k, g in x.groupby("tod")}}
        out[mode] = r
    if {"naive", "confirmed"} <= set(T["mode"]):
        n = T[(T["mode"] == "naive") & T["is_gamma"] & T["stage3_day"].fillna(False).astype(bool)].dropna(subset=["pnl_r"])
        c = T[(T["mode"] == "confirmed") & T["is_gamma"]].dropna(subset=["pnl_r"])
        dfx = stats.day_bootstrap_diff(c, n, "pnl_r", param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"),
                                       param(cfg, "ci_level"))
        conf = out["confirmed"]["gamma_tagged"]
        out["confirmed_minus_naive_same_days"] = dfx
        out["confirmed_verdict_with_diff_rule"] = "PASS" if (conf["verdict"] == "PASS" and dfx["lo"] > 0) else "KILL"
    if store.derived_path(cfg, "sim_trades").exists():
        es = store.load_derived("sim_trades", cfg)
        es = es[es["mode"].isin(["naive", "confirmed"]) & es["is_gamma"]].dropna(subset=["pnl_r"])
        same = es[es["touch_id"].isin(T["touch_id"])]
        out["es_bridge_same_touches"] = {m: {"n": int(len(g)), "mean_r": float(g["pnl_r"].mean())}
                                         for m, g in same.groupby("mode")}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--price-only", action="store_true")
    ap.add_argument("--approve-usd", type=float, default=None)
    ap.add_argument("--allow-past-total", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    from src.analysis import to_json
    if a.count or a.pull:
        days = touch_days(cfg)
        tc = store.load_derived("touches", cfg)
        print(f"in-sample sessions with touches: {len(days)} ({days[0]} .. {days[-1]}); touches {len(tc)}, "
              f"gamma-tagged {int(tc['is_gamma'].sum())}")
        if a.pull:
            budget = spend.Budget(cfg, a.approve_usd, a.allow_past_total)
            local = threading.local()

            def client():
                if not hasattr(local, "cl"):
                    local.cl = spend.client()
                return local.cl
            r = pull(cfg, client, budget, days, a.price_only, workers=a.workers)
            what = "quote (nothing pulled)" if a.price_only else "pulled"
            print(f"{what}: {r['pieces']} sessions, ${r['usd']:.4f}; already on disk {r['skipped']}; "
                  f"written {r['written']}; ledger ${spend.total_spent(cfg):.2f}")
        return
    if a.run:
        T, info = run(cfg)
        print(to_json(report(T, cfg, info)))
    elif a.report_only:
        print(to_json(report(store.load_derived("study12_trades", cfg), cfg)))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
