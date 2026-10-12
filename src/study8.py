"""Study 8 (RUNLOG 2026-10-08, approved): month-end compelled flow (Track A of THESIS.md).

E0, existence check (free, descriptive, no gate). FRED DGS10 and SP500, in sample only (dates before
holdout_start: 2026 ten-year yield changes are, in effect, M1's out-of-sample result, so E0 stops at 2025-12).
  - per complete month, the 10-year yield change over its last k trading days, k = 1..s8_e0_k_max:
    dy_k = 100 x (y(T0) - y(T-k)) in bp; mean with a Newey-West t; split at s8_e0_split (after first);
    the mean k-day change over all days of the same era is shown beside it.
  - rebalancing: r_last = S&P return on the last trading day T0 (T-1 close to T0 close), regressed on
    x = S&P month-to-date return to T-1 minus the month-to-date return of a 10-year par bond (DGS10 repriced
    daily, coupon = previous yield, plus calendar-day carry); Newey-West.
M1, month-end Treasury demand (gated): long 1 ZN at the open of the s8_m1_entry_time bar (settlement) on the
  trading day s8_m1_entry_days_before_end sessions before the month's last (open + 1 tick); exit at the open
  of the same bar on the last trading day (open - 1 tick); cost_rt_usd. The position stays on the entry
  contract: if the continuous symbol switches inside the window the month is skipped ("roll_in_window").
M2, rebalancing pressure (gated): at the ES 16:00 close s8_m2_entry_days_before_end sessions before the last,
  R = ES month-to-date log return (16:00 closes) minus ZN month-to-date log return (15:00 settlement-minute
  closes), each from the previous month's last close, chained on one contract (returns across a contract change
  are skipped and counted). R > 0 -> short 1 ES, R < 0 -> long; entry at the open of the 16:00 bar +- 1 tick
  (every input closes at or before 16:00:00), exit at the open of the 16:00 bar on the last trading day -+ 1 tick.
Gates per variant (in sample): n >= study8_min_events; after-cost mean > study8_min_friction_multiple x friction
  (friction = 2 ticks + cost); 90% month-bootstrap lower bound > 0; mean without the study8_tail_drop best months
  > 0. A pass also needs the same sign in the 2026 out-of-sample months (run once, only on "run the holdout").

  python -m src.study8 --count          # tradable month-ends per variant and skip reasons (no P&L)
  python -m src.study8 --e0             # E0 (downloads the FRED series once if missing)
  python -m src.study8 [--report-only]  # M1 and M2
Run with GAMMA_EDGE_CONFIG unset: the module loads config.yaml and config.zn.yaml itself.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import io
import logging
import math
import os

import numpy as np
import pandas as pd

from src import calendar as calm
from src import stats, store, study5
from src.config import DEFAULT_PATH, _load, _resolve, data_path, param
from src.levels import session_bars

log = logging.getLogger("study8")
ZN_OVERLAY = "config.zn.yaml"
BOND_YEARS, BOND_FREQ = 10, 2      # DGS10: 10-year constant-maturity par yield, semiannual coupons (structural)
PASS = "PASS_IN_SAMPLE (out-of-sample sign still required)"


def overlay(path) -> dict:
    """A config file by path, ignoring GAMMA_EDGE_CONFIG (Study 8 needs ES and ZN in one run)."""
    return copy.deepcopy(_load(str(_resolve(path))))


def base() -> dict:
    return overlay(DEFAULT_PATH)


def _data_end(cfg) -> dt.date:
    return calm.holdout_start(cfg) - dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# Month-end calendar
# ---------------------------------------------------------------------------
def _month(d) -> str:
    return f"{d.year}-{d.month:02d}"


def _last_weekday(year: int, month: int) -> dt.date:
    last = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    while last.weekday() >= 5:
        last -= pd.Timedelta(days=1)
    return last.date()


def month_end_days(dates, k: int, data_end) -> list[tuple[str, dt.date, dt.date]]:
    """(month, entry day = k sessions before the last, last session) for every complete month with at least
    k + 1 sessions. A month is complete when a later month has data or data_end reaches its last weekday."""
    ds = sorted(set(dates))
    by: dict[str, list] = {}
    for d in ds:
        by.setdefault(_month(d), []).append(d)
    months = sorted(by)
    out = []
    for i, m in enumerate(months):
        y, mo = (int(x) for x in m.split("-"))
        complete = i < len(months) - 1 or data_end >= _last_weekday(y, mo)
        v = by[m]
        if complete and len(v) >= k + 1:
            out.append((m, v[-(k + 1)], v[-1]))
    return out


# ---------------------------------------------------------------------------
# E0: FRED existence check
# ---------------------------------------------------------------------------
def fred_path(cfg, series: str):
    return data_path(cfg, "raw", "daily", f"fred_{series.lower()}.parquet")


def parse_fred(text: str, series: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    out = pd.DataFrame({"date": pd.to_datetime(df[date_col]).dt.date,
                        "value": pd.to_numeric(df[series], errors="coerce")})
    return out.dropna().reset_index(drop=True)


def fetch_fred(cfg, force: bool = False) -> list[str]:
    """Download each s8_fred_series once (idempotent). FRED needs no key."""
    import requests
    msgs = []
    for s in param(cfg, "s8_fred_series"):
        p = fred_path(cfg, s)
        if p.exists() and not force:
            msgs.append(f"exists: {p}")
            continue
        r = requests.get(cfg["data"]["fred_url"].format(series=s), timeout=60)
        r.raise_for_status()
        df = parse_fred(r.text, s)
        store.write(df, p)
        msgs.append(f"wrote {len(df)} rows -> {p}")
    return msgs


def load_fred(cfg, series: str) -> pd.Series:
    """In-sample observations from s8_e0_start, indexed by date."""
    df = store.read(fred_path(cfg, series))
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = calm.seal(df, "date", False, cfg)
    df = df[df["date"] >= pd.Timestamp(param(cfg, "s8_e0_start")).date()].sort_values("date")
    return pd.Series(df["value"].to_numpy(float), index=list(df["date"]))


def month_end_yield_changes(y: pd.Series, k_max: int, data_end) -> pd.DataFrame:
    """dy_k = 100 x (y(T0) - y(T-k)) in bp for each complete month (y in percent)."""
    y = y.sort_index()
    dates, v = list(y.index), y.to_numpy(float)
    pos = {d: i for i, d in enumerate(dates)}
    rows = []
    for m, _, t0 in month_end_days(dates, 0, data_end):
        i = pos[t0]
        row = {"month": m, "date": t0}
        for k in range(1, k_max + 1):
            row[f"dy_{k}"] = 100.0 * (v[i] - v[i - k]) if i - k >= 0 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def par_bond_return(y0: float, y1: float, days: float, years: int = BOND_YEARS, freq: int = BOND_FREQ) -> float:
    """Return of a par bond (coupon y0, decimal) repriced at yield y1, plus calendar-day carry y0 x days/365."""
    n, r = years * freq, y1 / freq
    if abs(r) < 1e-12:
        price = y0 / freq * n + 1.0
    else:
        price = (y0 / freq) * (1 - (1 + r) ** -n) / r + (1 + r) ** -n
    return price - 1.0 + y0 * days / 365.0


def rebalancing_frame(sp: pd.Series, y: pd.Series, data_end) -> pd.DataFrame:
    """Per complete month with a previous month-end close: sp_mtd and bond_mtd to T-1, x, and r_last."""
    j = pd.concat({"sp": sp, "y": y}, axis=1).dropna().sort_index()
    dates = list(j.index)
    spv, yv = j["sp"].to_numpy(float), j["y"].to_numpy(float) / 100.0
    pos = {d: i for i, d in enumerate(dates)}
    bond = np.full(len(dates), np.nan)
    for i in range(1, len(dates)):
        bond[i] = par_bond_return(yv[i - 1], yv[i], (dates[i] - dates[i - 1]).days)
    rows = []
    for m, tm1, t0 in month_end_days(dates, 1, data_end):
        first = dt.date(int(m[:4]), int(m[5:]), 1)
        prior = [d for d in dates if d < first]
        if not prior or _month(prior[-1]) != _month(first - dt.timedelta(days=1)):
            continue
        b, i1, i0 = pos[prior[-1]], pos[tm1], pos[t0]
        sp_mtd = spv[i1] / spv[b] - 1.0
        bond_mtd = float(np.prod(1.0 + bond[b + 1:i1 + 1]) - 1.0)
        rows.append({"month": m, "date": t0, "sp_mtd": sp_mtd, "bond_mtd": bond_mtd, "x": sp_mtd - bond_mtd,
                     "r_last": spv[i0] / spv[i1] - 1.0})
    return pd.DataFrame(rows)


def _nw_mean(v: pd.Series, lags: int) -> dict:
    v = v.dropna()
    if len(v) < 3:
        return {"n": int(len(v)), "mean": float(v.mean()) if len(v) else np.nan, "nw_t": np.nan}
    res = stats.ols_nw(pd.DataFrame({"v": v.to_numpy(float)}), "v ~ 1", lags)
    return {"n": int(len(v)), "mean": float(res.params["Intercept"]), "nw_t": float(res.tvalues["Intercept"])}


def _eras(cfg, first: dt.date, last: dt.date) -> list[tuple[str, dt.date, dt.date]]:
    split = pd.Timestamp(param(cfg, "s8_e0_split")).date()
    return [(f"{split.year}-{last.year} (after publication)", split, last),
            (f"{first.year}-{split.year - 1} (papers' era)", first, split - dt.timedelta(days=1))]


def e0(cfg=None) -> dict:
    cfg = cfg or base()
    lags, kmax, data_end = param(cfg, "nw_lags"), param(cfg, "s8_e0_k_max"), _data_end(cfg)
    y, sp = load_fred(cfg, "DGS10"), load_fred(cfg, "SP500")
    ych = month_end_yield_changes(y, kmax, data_end)
    yv, ydates = y.to_numpy(float), pd.Series(list(y.index))
    out = {"note": "descriptive, no gate (RUNLOG Study 8, E0); in sample only (dates < holdout_start)",
           "dgs10": {"first": str(y.index[0]), "last": str(y.index[-1]), "eras": {}},
           "rebalancing": {}}
    for label, a, b in _eras(cfg, y.index[0], y.index[-1]):
        sub = ych[(ych["date"] >= a) & (ych["date"] <= b)]
        era = {}
        for k in range(1, kmax + 1):
            allk = pd.Series(100.0 * (yv[k:] - yv[:-k]))
            dk = ydates.iloc[k:].reset_index(drop=True)
            allk = allk[((dk >= a) & (dk <= b)).to_numpy()]
            r = _nw_mean(sub[f"dy_{k}"], lags)
            era[f"k{k}"] = {"n_months": r["n"], "mean_bp": r["mean"], "nw_t": r["nw_t"],
                            "all_days_mean_bp": float(allk.mean()) if len(allk) else np.nan}
        out["dgs10"]["eras"][label] = era
    f = rebalancing_frame(sp, y, data_end)
    if not f.empty:
        out["rebalancing"]["sp500_first"] = str(sp.index[0])
        spr = pd.Series(sp.to_numpy(float)).pct_change()
        spd = pd.Series(list(sp.index))
        for label, a, b in _eras(cfg, sp.index[0], sp.index[-1]):
            g = f[(f["date"] >= a) & (f["date"] <= b)].reset_index(drop=True)
            e = {"n_months": int(len(g))}
            if len(g) >= 6:
                res = stats.ols_nw(g, "r_last ~ x", lags)
                e.update({"slope": float(res.params["x"]), "nw_t": float(res.tvalues["x"]),
                          "p": float(res.pvalues["x"]),
                          "mean_last_day_ret_bp": 1e4 * float(g["r_last"].mean()),
                          "all_days_mean_ret_bp": 1e4 * float(spr[((spd >= a) & (spd <= b)).to_numpy()].mean()),
                          "mean_last_day_ret_bp_x_pos": 1e4 * float(g.loc[g["x"] > 0, "r_last"].mean()),
                          "mean_last_day_ret_bp_x_neg": 1e4 * float(g.loc[g["x"] < 0, "r_last"].mean())})
            out["rebalancing"][label] = e
    out["reading"] = ("Hartley-Schwarz predicts negative month-end dy_k (Treasuries bid into the index extension); "
                      "Harvey et al. predict a negative slope (stocks sold into the last day after outperforming).")
    return out


# ---------------------------------------------------------------------------
# Futures: closes, month-to-date returns, holds
# ---------------------------------------------------------------------------
def session_closes(cal: pd.DataFrame, by_day: dict, cfg) -> pd.DataFrame:
    """Close of each session's bar ending at market.rth_close, on the session's front contract."""
    rows = []
    for r in cal.sort_values("date").itertuples():
        c, i = study5.prev_close(session_bars(by_day, r.date, r.instrument_id), r.date, cfg)
        rows.append({"date": r.date, "close": c, "instrument_id": i})
    return pd.DataFrame(rows, columns=["date", "close", "instrument_id"])


def mtd_log_return(closes: pd.DataFrame, entry_day) -> tuple[float, int]:
    """Sum of daily log returns from the previous month's last close to entry_day's close, on one contract at a
    time (a return across a contract change, or with a missing close, is skipped and counted)."""
    c = closes.sort_values("date")
    first = dt.date(entry_day.year, entry_day.month, 1)
    before = c[c["date"] < first]
    if before.empty or _month(before["date"].iloc[-1]) != _month(first - dt.timedelta(days=1)):
        return np.nan, 0
    seq = pd.concat([before.iloc[[-1]], c[(c["date"] >= first) & (c["date"] <= entry_day)]])
    if seq["date"].iloc[-1] != entry_day:
        return np.nan, 0
    p, k = seq["close"].to_numpy(float), seq["instrument_id"].to_numpy()
    tot, skipped = 0.0, 0
    for j in range(1, len(p)):
        if np.isfinite(p[j]) and np.isfinite(p[j - 1]) and p[j] > 0 and p[j - 1] > 0 and k[j] == k[j - 1]:
            tot += math.log(p[j] / p[j - 1])
        else:
            skipped += 1
    return tot, skipped


def hold_trade(by_day: dict, entry_day, exit_day, t_entry: str, t_exit: str, d: int, tick: float,
               cost_pts: float, entry_inst=None):
    """Enter at the open of the t_entry bar + d x tick, exit at the open of the t_exit bar on exit_day - d x tick,
    on the entry bar's contract. (row, None) or (None, reason)."""
    eb = by_day.get(entry_day)
    if eb is None or eb.empty:
        return None, "no_entry_bar"
    if entry_inst is not None:
        eb = eb[eb["instrument_id"] == entry_inst]
    eb = eb.sort_values("ts_open_utc").reset_index(drop=True)
    i = study5.bar_index_at(eb, entry_day, t_entry)
    if i is None:
        return None, "no_entry_bar"
    inst = int(eb["instrument_id"].iat[i])
    xall = by_day.get(exit_day)
    if xall is None or xall.empty:
        return None, "no_exit_bar"
    xb = session_bars(by_day, exit_day, inst)
    j = study5.bar_index_at(xb, exit_day, t_exit) if not xb.empty else None
    if j is None:
        other = study5.bar_index_at(xall.sort_values("ts_open_utc").reset_index(drop=True), exit_day, t_exit)
        return None, ("roll_in_window" if (xb.empty or other is not None) else "no_exit_bar")
    E = float(eb["open"].iat[i]) + d * tick
    X = float(xb["open"].iat[j]) - d * tick
    return {"instrument_id": inst, "E": E, "X": X, "gross_pts": d * (X - E), "pnl_pts": d * (X - E) - cost_pts}, None


def _em_fields(pnl: float, sigma, price: float) -> dict:
    em = sigma * price if sigma is not None and np.isfinite(sigma) else np.nan
    return {"em_r": em, "pnl_em_r": pnl / em if np.isfinite(em) and em > 0 else np.nan}


def m1_trades(cal: pd.DataFrame, by_day: dict, zcfg, data_end, sigma: dict):
    k, t = param(zcfg, "s8_m1_entry_days_before_end"), param(zcfg, "s8_m1_entry_time")
    tick, pv = zcfg["market"]["tick"], zcfg["market"]["point_value"]
    cost = param(zcfg, "cost_rt_usd") / pv
    inst = dict(zip(cal["date"], cal["instrument_id"]))
    rows, skipped = [], {}
    for m, a, z in month_end_days(cal["date"], k, data_end):
        r, why = hold_trade(by_day, a, z, t, t, 1, tick, cost, entry_inst=inst.get(a))
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        rows.append({"date": a, "month": m, "entry_date": a, "exit_date": z, "d": 1, **r,
                     "pnl_ticks": r["pnl_pts"] / tick, "pnl_usd": r["pnl_pts"] * pv,
                     **_em_fields(r["pnl_pts"], sigma.get(a), r["E"])})
    return pd.DataFrame(rows), skipped


def m2_direction(R: float) -> int:
    if not np.isfinite(R) or R == 0:
        return 0
    return -1 if R > 0 else 1


def m2_trades(es_cal: pd.DataFrame, es_by_day: dict, zn_closes: pd.DataFrame, cfg, data_end, sigma: dict):
    k, t = param(cfg, "s8_m2_entry_days_before_end"), cfg["market"]["rth_close"]
    tick, pv = cfg["market"]["tick"], cfg["market"]["point_value"]
    cost = param(cfg, "cost_rt_usd") / pv
    es_closes = session_closes(es_cal, es_by_day, cfg)
    inst = dict(zip(es_cal["date"], es_cal["instrument_id"]))
    rows, skipped = [], {}
    for m, a, z in month_end_days(es_cal["date"], k, data_end):
        r_es, sk_es = mtd_log_return(es_closes, a)
        zc = zn_closes[zn_closes["date"] <= a]
        r_zn, sk_zn = mtd_log_return(zc, zc["date"].iloc[-1]) if len(zc) and _month(zc["date"].iloc[-1]) == m \
            else (np.nan, 0)
        if not (np.isfinite(r_es) and np.isfinite(r_zn)):
            skipped["no_mtd"] = skipped.get("no_mtd", 0) + 1
            continue
        R = r_es - r_zn
        d = m2_direction(R)
        if d == 0:
            skipped["flat_signal"] = skipped.get("flat_signal", 0) + 1
            continue
        r, why = hold_trade(es_by_day, a, z, t, t, d, tick, cost, entry_inst=inst.get(a))
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        rows.append({"date": a, "month": m, "entry_date": a, "exit_date": z, "es_mtd": r_es, "zn_mtd": r_zn,
                     "R": R, "d": d, "mtd_returns_skipped": sk_es + sk_zn, **r, "pnl_usd": r["pnl_pts"] * pv,
                     **_em_fields(r["pnl_pts"], sigma.get(a), r["E"])})
    return pd.DataFrame(rows), skipped


def drift_reference(cal: pd.DataFrame, by_day: dict, k: int, t: str, d: int, tick: float, cost: float,
                    data_end) -> dict:
    """Descriptive: the same hold from every in-sample session (not only month-ends), after costs."""
    dates = sorted(x for x in cal["date"] if x <= data_end)
    inst = dict(zip(cal["date"], cal["instrument_id"]))
    pn = []
    for i in range(len(dates) - k):
        r, why = hold_trade(by_day, dates[i], dates[i + k], t, t, d, tick, cost, entry_inst=inst.get(dates[i]))
        if r:
            pn.append(r["pnl_pts"])
    return {"n": len(pn), "mean_pts": float(np.mean(pn)) if pn else np.nan}


# ---------------------------------------------------------------------------
# Gates and report
# ---------------------------------------------------------------------------
def friction_pts(tick: float, cost_usd: float, point_value: float) -> float:
    return 2 * tick + cost_usd / point_value


def verdict(T: pd.DataFrame, col: str, friction: float, cfg) -> dict:
    g = cfg["gates"]
    n = int(len(T))
    bs = stats.day_bootstrap_mean(T, col, param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"),
                                  param(cfg, "ci_level"), date_col="month")
    thr = g["study8_min_friction_multiple"] * friction
    kd = g["study8_tail_drop"]
    v = T[col].sort_values() if n else pd.Series(dtype=float)
    ex = float(v.iloc[:-kd].mean()) if n > kd else np.nan
    checks = {"min_events": bool(n >= g["study8_min_events"]),
              "friction_multiple": bool(np.isfinite(bs["mean"]) and bs["mean"] > thr),
              "ci_lb_positive": bool(np.isfinite(bs["lo"]) and bs["lo"] > 0),
              "tail_drop": bool(np.isfinite(ex) and ex > 0)}
    return {"n": n, "mean": bs["mean"], "ci_lo": bs["lo"], "ci_hi": bs["hi"], "threshold": thr,
            "friction": friction, "mean_ex_best": ex, "checks": checks,
            "verdict_vs_rules": PASS if all(checks.values()) else "KILL"}


def _by_year(T: pd.DataFrame, col: str) -> dict:
    y = T.assign(year=[d.year for d in T["entry_date"]]).groupby("year")[col]
    return {int(k): {"n": int(len(s)), "mean": float(s.mean())} for k, s in y}


def _load_inputs(cfg, z):
    zcal = store.load_calendar(z)
    zby = {d: x for d, x in store.load_bars(z).groupby("date")}
    ecal = store.load_calendar(cfg)
    eby = {d: x for d, x in store.load_bars(cfg).groupby("date")}
    return zcal, zby, ecal, eby


def run(save: bool = True, count_only: bool = False) -> dict:
    from src.study7 import sigma_by_session
    cfg, z = base(), overlay(ZN_OVERLAY)
    end = _data_end(cfg)
    zcal, zby, ecal, eby = _load_inputs(cfg, z)
    M1, sk1 = m1_trades(zcal, zby, z, end, {} if count_only else sigma_by_session(zcal, zby, z))
    zn_closes = session_closes(zcal, zby, z)
    M2, sk2 = m2_trades(ecal, eby, zn_closes, cfg, end, {} if count_only else sigma_by_session(ecal, eby, cfg))
    if count_only:
        return {"note": "counts only, no P&L", "min_events": cfg["gates"]["study8_min_events"],
                "M1_zn_month_end": {"tradable_months": int(len(M1)), "skipped": sk1,
                                    "first": str(M1["month"].min()) if len(M1) else None},
                "M2_es_rebalancing": {"tradable_months": int(len(M2)), "skipped": sk2,
                                      "first": str(M2["month"].min()) if len(M2) else None}}
    if save:
        if len(M1):
            store.save_derived(M1, "study8_m1_zn", cfg)
        if len(M2):
            store.save_derived(M2, "study8_m2_es", cfg)
    rep = report(M1, M2, cfg, z)
    k1, k2 = param(z, "s8_m1_entry_days_before_end"), param(cfg, "s8_m2_entry_days_before_end")
    zt, et = z["market"]["tick"], cfg["market"]["tick"]
    d1 = drift_reference(zcal, zby, k1, param(z, "s8_m1_entry_time"), 1, zt,
                         param(z, "cost_rt_usd") / z["market"]["point_value"], end)
    rep["descriptive"]["M1_drift_any_day_long_ticks"] = {**d1, "mean_ticks": d1["mean_pts"] / zt}
    rep["descriptive"]["M2_drift_any_day_long_pts"] = drift_reference(
        ecal, eby, k2, cfg["market"]["rth_close"], 1, et, param(cfg, "cost_rt_usd") / cfg["market"]["point_value"], end)
    rep["skipped"] = {"M1": sk1, "M2": sk2}
    return rep


def report(M1: pd.DataFrame, M2: pd.DataFrame, cfg=None, z=None) -> dict:
    cfg, z = cfg or base(), z or overlay(ZN_OVERLAY)
    out = {"note": "in sample; gates RUNLOG Study 8; out-of-sample sign pending for any pass", "variants": {},
           "descriptive": {}}
    if len(M1):
        zt, zpv = z["market"]["tick"], z["market"]["point_value"]
        fr = friction_pts(zt, param(z, "cost_rt_usd"), zpv) / zt
        v = verdict(M1, "pnl_ticks", fr, z)
        v.update({"unit": "ZN ticks (1/64) after costs", "mean_gross_ticks": float((M1["gross_pts"] / zt).mean()),
                  "mean_usd": float(M1["pnl_usd"].mean()), "mean_em_r": float(M1["pnl_em_r"].mean()),
                  "win_rate": float((M1["pnl_ticks"] > 0).mean()), "first": M1["month"].min(),
                  "last": M1["month"].max(), "by_year": _by_year(M1, "pnl_ticks")})
        out["variants"]["M1_zn_month_end"] = v
    if len(M2):
        et, epv = cfg["market"]["tick"], cfg["market"]["point_value"]
        v = verdict(M2, "pnl_pts", friction_pts(et, param(cfg, "cost_rt_usd"), epv), cfg)
        v.update({"unit": "ES points after costs", "mean_gross_pts": float(M2["gross_pts"].mean()),
                  "mean_usd": float(M2["pnl_usd"].mean()), "mean_em_r": float(M2["pnl_em_r"].mean()),
                  "win_rate": float((M2["pnl_pts"] > 0).mean()), "share_short": float((M2["d"] == -1).mean()),
                  "first": M2["month"].min(), "last": M2["month"].max(), "by_year": _by_year(M2, "pnl_pts")})
        out["variants"]["M2_es_rebalancing"] = v
        lags = param(cfg, "nw_lags")
        # gross_pts = d (X - E), so d x gross_pts is the long-side move; Harvey et al. predict a negative slope
        g = M2.assign(long_ret_bp=1e4 * M2["d"] * M2["gross_pts"] / M2["E"]).reset_index(drop=True)
        if len(g) >= 6:
            res = stats.ols_nw(g, "long_ret_bp ~ R", lags)
            out["descriptive"]["M2_slope_long_ret_bp_on_R"] = {"slope": float(res.params["R"]),
                                                              "nw_t": float(res.tvalues["R"]), "n": int(len(g))}
    out["verdicts"] = {k: v["verdict_vs_rules"] for k, v in out["variants"].items()}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", action="store_true", help="tradable month-ends per variant, no P&L")
    ap.add_argument("--e0", action="store_true", help="E0 existence check from FRED (downloads once)")
    ap.add_argument("--report-only", action="store_true", help="report from the saved M1/M2 tables")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    env = os.environ.get("GAMMA_EDGE_CONFIG")
    if env and _resolve(env) != DEFAULT_PATH:
        raise SystemExit("unset GAMMA_EDGE_CONFIG first: Study 8 loads config.yaml and config.zn.yaml itself")
    from src.analysis import to_json
    if a.e0:
        cfg = base()
        for msg in fetch_fred(cfg):
            print(msg)
        print(to_json(e0(cfg)))
    elif a.count:
        print(to_json(run(save=False, count_only=True)))
    elif a.report_only:
        cfg = base()
        print(to_json(report(store.load_derived("study8_m1_zn", cfg), store.load_derived("study8_m2_es", cfg), cfg)))
    else:
        print(to_json(run()))


if __name__ == "__main__":
    main()
