"""Study 11 (Matteo 2026-10-09): execution design for the IWM resting-order strategy. EXPLORATION on the 10 seen
sessions (the Study 10 pilot and confirmation days); report-only, $0. Whatever is chosen here is frozen as one
strategy spec and confirmed once on fresh sessions.

Studies 10 and 10b marked every fill to the mid 1, 5 and 15 minutes later. That is not a trade you can close: it
assumes an exit at the mid for free. This module trades the exit.

#1 Round trip. Entry = the Study 10 fill (a print at the bid, s = +1, or the ask, s = -1). At once an exit rests
   at the opposite side of the quote at entry, X (the ask for a long). It waits P minutes (s11_patience_min,
   clipped at the close); unfilled, it crosses the spread at the newest quote seen by then, one tick worse
   (s11_fallback_slip_ticks). gross = s x (exit - entry) x 100, $ a contract; fees on both sides: $0 (gate),
   pass-through $0.05 and IBKR tiered reported. Exit fill rules for the resting exit, side by side:
     through  (headline, SPEC rule 5) a print one tick through X, or a quote whose near side is one tick through X
     queue    back of the queue: through, or prints at X totalling more than the size displayed at X at entry
     front    front of the queue (optimistic bound): any print at or through X, or a quote touching X
   Only prints and quotes after the entry (its whole sweep) count.
#2 Stale quotes. Fair-value move against the resting order just before the fill:
     adverse_L = -s x delta x (S(t) - S(t - L)) x 100 ($ a contract), stale_L = adverse_L / half-spread ($)
   S = IWM: the 1-second Nasdaq mid when pulled (s11_iwm_*), else minute put-call parity (then L < 60 s is NaN).
   delta = Black-76 forward delta from the implied vol of the pre-trade mid, forward from put-call parity on the
   contract's own expiry at the newest snapshot (no implied vol: 1 / -1 in the money, 0 out). Known at the fill.
   Candidates fixed before any number: K1 skip stale >= 0.5, K2 skip stale >= 1.0 (s11_stale_skip), per window.
#4 Contract selection: the round trip by days to expiry, premium, |delta|, side, exchange, and days x spread;
   candidate K3 skip s11_skip_dte_bucket.
#5 Inventory and hedging: a day = n fills (s10_fills_per_day) drawn from one session, in time order, each held to
   its exit (through rule, headline patience); limits [max open contracts, max |net delta| shares] (s11_limits);
   a fill that would breach a limit is skipped. Hedged P&L: each fill's delta in IWM shares, sold/bought at the
   first IWM price after the fill and unwound at the first at or after the exit, s11_hedge_cost_per_share_usd a
   share a side. Buys only (a cash account cannot sell to open) shown separately.

  python -m src.study11 --iwm-pull --price-only        # quote 1-second IWM for the 10 sessions (nothing pulled)
  python -m src.study11 --iwm-pull --approve-usd X --allow-past-total
  python -m src.study11 --explore                      # entries, round trips, features, report (saves study11_entries)
  python -m src.study11 --report-only
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd
from scipy.special import ndtr

from src import calendar as calm
from src import gex, spend, stats, store, study10, study10b
from src.config import data_path, load_config, param

log = logging.getLogger("study11")

MODELS = ("through", "queue", "front")
EPS = 1e-6
YEAR_S = 365 * 86400
VENUES = {20: "NYSE American", 21: "BOX", 22: "Cboe", 23: "MIAX Emerald", 24: "Cboe EDGX", 25: "Nasdaq GEMX",
          26: "Nasdaq ISE", 27: "Nasdaq MRX", 28: "MIAX", 29: "NYSE Arca", 30: "OPRA consolidated",
          31: "MIAX Pearl", 32: "Nasdaq Options", 33: "Nasdaq BX", 34: "Cboe C2", 35: "Nasdaq PHLX",
          36: "Cboe BZX", 37: "MEMX", 61: "MIAX Sapphire"}


def _i8(x) -> np.ndarray:
    """Nanoseconds since the epoch, whatever the stored resolution."""
    return pd.to_datetime(pd.Series(np.asarray(x)), utc=True).dt.as_unit("ns").astype("int64").to_numpy()


def _ts(x) -> pd.Series:
    """UTC timestamps at nanosecond resolution (merge_asof needs one resolution on both sides)."""
    if not isinstance(x, pd.Series):
        x = pd.Series(np.asarray(x, dtype=object) if not isinstance(x, pd.DatetimeIndex) else x)
    return pd.to_datetime(x, utc=True).dt.as_unit("ns")


def _valid(q: pd.DataFrame) -> pd.DataFrame:
    return q[(q["bid"] > 0) & (q["ask"] > q["bid"] + EPS)]


# ---------------------------------------------------------------------------
# #1 Round trip
# ---------------------------------------------------------------------------
def exit_times(E: pd.DataFrame, tr: pd.DataFrame, obs: pd.DataFrame, horizon_min: float, tick: float,
               close: pd.Timestamp) -> pd.DataFrame:
    """First time the resting exit at X (opposite quote at entry) fills under each rule, within
    (ts_last, min(ts_last + horizon, close)]; NaT if never."""
    n = len(E)
    big = np.iinfo("int64").max
    out = {m: np.full(n, big, dtype="int64") for m in MODELS}
    if n:
        tg = {k: (_i8(g["ts"]), g["price"].to_numpy(float), g["size"].to_numpy(float))
              for k, g in tr.assign(ts=_ts(tr["ts"])).sort_values("ts", kind="stable")
              .groupby("symbol")}
        q = _valid(obs)
        qg = {k: (_i8(g["ts"]), g["bid"].to_numpy(float), g["ask"].to_numpy(float))
              for k, g in q.assign(ts=_ts(q["ts"])).sort_values("ts", kind="stable")
              .groupby("symbol")}
        H, cl = pd.Timedelta(minutes=horizon_min).value, pd.Timestamp(close).value
        t0s = _i8(E["ts_last"])
        sym, s = E["symbol"].to_numpy(), E["s"].to_numpy(int)
        X = np.where(s == 1, E["ask"].to_numpy(float), E["bid"].to_numpy(float))
        bs = E["bid_sz"].to_numpy(float) if "bid_sz" in E else np.full(n, np.nan)
        as_ = E["ask_sz"].to_numpy(float) if "ask_sz" in E else np.full(n, np.nan)
        Q = np.where(s == 1, as_, bs)
        for i in range(n):
            t0, t1 = t0s[i], min(t0s[i] + H, cl)
            th = qu = fr = big
            if sym[i] in tg:
                pt, pp, pz = tg[sym[i]]
                a, b = np.searchsorted(pt, t0, "right"), np.searchsorted(pt, t1, "right")
                if b > a:
                    d = s[i] * (pp[a:b] - X[i])
                    k = np.flatnonzero(d >= tick - EPS)
                    th = pt[a + k[0]] if k.size else big
                    k = np.flatnonzero(d >= -EPS)
                    fr = pt[a + k[0]] if k.size else big
                    if np.isfinite(Q[i]):
                        cs = np.cumsum(np.where(np.abs(d) < EPS, pz[a:b], 0.0))
                        k = np.flatnonzero(cs > Q[i] + EPS)
                        qu = pt[a + k[0]] if k.size else big
            if sym[i] in qg:
                qt, qb, qa = qg[sym[i]]
                a, b = np.searchsorted(qt, t0, "right"), np.searchsorted(qt, t1, "right")
                if b > a:
                    d = s[i] * ((qb if s[i] == 1 else qa)[a:b] - X[i])
                    k = np.flatnonzero(d >= tick - EPS)
                    if k.size:
                        th = min(th, qt[a + k[0]])
                    k = np.flatnonzero(d >= -EPS)
                    if k.size:
                        fr = min(fr, qt[a + k[0]])
            qu = min(qu, th)
            out["through"][i], out["queue"][i], out["front"][i] = th, qu, min(fr, qu)
    res = {}
    for m in MODELS:
        v = out[m]
        res[m] = pd.to_datetime(pd.Series(v).where(v != big), unit="ns", utc=True).dt.as_unit("ns").set_axis(E.index)
    return pd.DataFrame(res, index=E.index, columns=list(MODELS))


def round_trips(E: pd.DataFrame, T: pd.DataFrame, obs: pd.DataFrame, patience, tick: float, slip_ticks: int,
                close: pd.Timestamp, mult: float) -> pd.DataFrame:
    """gross_{rule}_{P} ($ a contract, before fees), passive_{rule}_{P}, hold_{rule}_{P} (minutes),
    exit_ts_{rule}_{P}."""
    out = E.copy()
    s = out["s"].to_numpy(int)
    X = np.where(s == 1, out["ask"].to_numpy(float), out["bid"].to_numpy(float))
    q = _valid(obs).rename(columns={"ts": "q_ts"}).sort_values("q_ts")[["q_ts", "symbol", "bid", "ask"]]
    q["q_ts"] = _ts(q["q_ts"])
    close = pd.Timestamp(close)
    for P in patience:
        t_end = (_ts(out["ts_last"]) + pd.Timedelta(minutes=P)).clip(upper=close)
        left = pd.DataFrame({"t_end": t_end, "symbol": out["symbol"]}).reset_index().sort_values("t_end")
        m = pd.merge_asof(left, q, left_on="t_end", right_on="q_ts", by="symbol",
                          direction="backward").set_index("index").reindex(out.index)
        fb = np.where(s == 1, m["bid"].to_numpy(float) - slip_ticks * tick, m["ask"].to_numpy(float) + slip_ticks * tick)
        fb = np.maximum(fb, 0.0)
        for rule in MODELS:
            ft = _ts(T[rule])
            fm = ft.notna() & (ft <= t_end)
            filled = fm.to_numpy(bool)
            px = np.where(filled, X, fb)
            out[f"gross_{rule}_{P}"] = s * (px - out["price"].to_numpy(float)) * mult
            out[f"passive_{rule}_{P}"] = filled
            ex = ft.where(fm, t_end)
            out[f"exit_ts_{rule}_{P}"] = ex
            out[f"hold_{rule}_{P}"] = (ex - _ts(out["ts"])).dt.total_seconds() / 60
    return out


def fee_net(R: pd.DataFrame, col: str, cfg) -> pd.DataFrame:
    """Net of a fee on the way in and out, per fee scenario."""
    f = study10.fee_table(R["mid0"], cfg)
    return pd.DataFrame({sc: R[col].to_numpy(float) - 2 * f[sc].to_numpy(float) for sc in study10.FEE_SCENARIOS},
                        index=R.index)


# ---------------------------------------------------------------------------
# #2 Delta, stale quotes; #5 hedge
# ---------------------------------------------------------------------------
def forward_by_expiry(q: pd.DataFrame, n_strikes: int) -> pd.DataFrame:
    """Forward per snapshot and expiry from put-call parity: F = K + C - P, median of the n_strikes strikes
    with the smallest |C - P|. Columns ts, expiration, F."""
    q = _valid(q).copy()
    if q.empty:
        return pd.DataFrame(columns=["ts", "expiration", "F"])
    q = q.join(study10b._osi_map(q["symbol"]), on="symbol")
    q["mid"] = (q["bid"] + q["ask"]) / 2
    w = q.pivot_table(index=["ts", "expiration", "strike"], columns="right", values="mid", aggfunc="last")
    if not {"C", "P"} <= set(w.columns):
        return pd.DataFrame(columns=["ts", "expiration", "F"])
    w = w.dropna(subset=["C", "P"]).reset_index()
    w["F"] = w["strike"] + w["C"] - w["P"]
    w["gap"] = (w["C"] - w["P"]).abs()
    near = w.sort_values(["ts", "expiration", "gap"]).groupby(["ts", "expiration"]).head(n_strikes)
    return near.groupby(["ts", "expiration"])["F"].median().reset_index()


def _asof(series: pd.Series, times, direction: str = "backward", exact: bool = True) -> np.ndarray:
    """Value of a time-indexed series at each time: newest at or before (backward) or first after (forward)."""
    if series is None or len(series) == 0:
        return np.full(len(times), np.nan)
    r = pd.DataFrame({"s_ts": _ts(series.index), "v": np.asarray(series, float)}).sort_values("s_ts")
    left = pd.DataFrame({"t": _ts(times).reset_index(drop=True)}).reset_index().sort_values("t")
    m = pd.merge_asof(left, r, left_on="t", right_on="s_ts", direction=direction,
                      allow_exact_matches=exact).set_index("index").sort_index()
    return m["v"].to_numpy(float)


def option_greeks(E: pd.DataFrame, fwd: pd.DataFrame, S: pd.Series):
    """(implied vol, Black-76 forward delta) from the pre-trade mid; forward of the contract's expiry at the newest
    snapshot at or before the fill, else IWM. No implied vol: delta 1 (call) / -1 (put) in the money, else 0."""
    n = len(E)
    F = np.full(n, np.nan)
    if len(fwd):
        f = fwd.assign(ts=_ts(fwd["ts"])).sort_values("ts")
        left = E[["ts", "expiration"]].assign(ts=_ts(E["ts"])).reset_index().sort_values("ts")
        m = pd.merge_asof(left, f.rename(columns={"ts": "f_ts"}), left_on="ts", right_on="f_ts", by="expiration",
                          direction="backward").set_index("index").reindex(E.index)
        F = m["F"].to_numpy(float)
    miss = ~np.isfinite(F)
    if miss.any():
        F = np.where(miss, _asof(S, E["ts"]), F)
    exp_ts = pd.to_datetime([f"{x} 16:00" for x in E["expiration"]]).tz_localize(calm.ET).tz_convert("UTC")
    T = np.maximum((_i8(exp_ts) - _i8(E["ts"])) / 1e9, 60) / YEAR_S
    K, mid = E["strike"].to_numpy(float), E["mid0"].to_numpy(float)
    call = (E["right"] == "C").to_numpy(bool)
    iv, delta = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        if not (np.isfinite(F[i]) and np.isfinite(K[i])):
            continue
        v = gex.implied_vol(mid[i], F[i], K[i], T[i], 1.0, bool(call[i]))
        if np.isfinite(v):
            iv[i] = v
            nd = float(ndtr(gex._d1(F[i], K[i], T[i], v)))
            delta[i] = nd if call[i] else nd - 1.0
        else:
            itm = F[i] > K[i] if call[i] else F[i] < K[i]
            delta[i] = (1.0 if call[i] else -1.0) if itm else 0.0
    return iv, delta


def stale_features(E: pd.DataFrame, delta, S: pd.Series, lookbacks_s, source: str) -> pd.DataFrame:
    """adverse_L ($ a contract, > 0 = against the resting order) and stale_L = adverse / half-spread ($)."""
    out = pd.DataFrame(index=E.index)
    t = _ts(E["ts"])
    now = _asof(S, t)
    half = E["spread"].to_numpy(float) / 2 * 100
    s = E["s"].to_numpy(float)
    for L in lookbacks_s:
        then = _asof(S, t - pd.Timedelta(seconds=L))
        adv = -s * np.asarray(delta, float) * (now - then) * 100
        if source == "parity_1m" and L < 60:
            adv = np.full(len(E), np.nan)                     # minute IWM cannot see a move this short
        out[f"adverse_{L}"] = adv
        out[f"stale_{L}"] = np.where(half > 0, adv / np.where(half > 0, half, 1), np.nan)
    return out


def hedge_pnl(E: pd.DataFrame, delta, exit_ts, S: pd.Series, cost: float) -> np.ndarray:
    """$ of hedging one contract's delta in IWM shares: in at the first IWM price after the fill, out at the
    first at or after the exit (the last price if none), cost a share a side."""
    h = np.round(np.asarray(delta, float) * 100)
    s = E["s"].to_numpy(float)
    s_in = _asof(S, _ts(E["ts"]), "forward", exact=False)
    s_out = _asof(S, _ts(pd.Series(exit_ts).reset_index(drop=True)), "forward", exact=True)
    if len(S):
        last = float(pd.Series(S).sort_index().iloc[-1])
        s_in, s_out = np.where(np.isfinite(s_in), s_in, last), np.where(np.isfinite(s_out), s_out, last)
    return -s * h * (s_out - s_in) - 2 * cost * np.abs(h)


# ---------------------------------------------------------------------------
# #5 Inventory
# ---------------------------------------------------------------------------
def inventory_day(D: pd.DataFrame, n: int, rng, max_open, max_delta, long_only: bool) -> dict:
    """One simulated day: n fills drawn from session D (columns ts, exit_ts, s, delta, gross, hedge, price), in
    time order; a fill that would take open contracts above max_open or |net delta| (shares) above max_delta is
    skipped."""
    return _inventory_core(_day_arrays(D), n, rng, max_open, max_delta, long_only)


def _day_arrays(D: pd.DataFrame) -> dict:
    return {"t": _i8(D["ts"]), "x": _i8(D["exit_ts"]), "s": D["s"].to_numpy(float),
            "dl": np.nan_to_num(D["delta"].to_numpy(float)), "g": D["gross"].to_numpy(float),
            "hg": np.nan_to_num(D["hedge"].to_numpy(float)), "pr": D["price"].to_numpy(float)}


def _inventory_core(a: dict, n: int, rng, max_open, max_delta, long_only: bool) -> dict:
    t, x, s, dl, g, hg, pr = (a[k] for k in ("t", "x", "s", "dl", "g", "hg", "pr"))
    idx = np.flatnonzero(s == 1) if long_only else np.arange(len(s))
    pick = rng.choice(idx, size=min(n, len(idx)), replace=False) if len(idx) else idx
    order = pick[np.argsort(t[pick], kind="stable")]
    open_, net = [], 0.0
    r = {"pnl": 0.0, "pnl_hedged": 0.0, "taken": 0, "skipped": 0, "peak_open": 0, "peak_delta": 0.0,
         "peak_premium": 0.0}
    for i in order:
        still = [p for p in open_ if p[0] > t[i]]
        if len(still) != len(open_):
            open_ = still
            net = sum(p[1] for p in open_)
        d = s[i] * dl[i] * 100
        if (max_open is not None and len(open_) + 1 > max_open) or \
                (max_delta is not None and abs(net + d) > max_delta + EPS):
            r["skipped"] += 1
            continue
        open_.append((x[i], d, pr[i] * 100 if s[i] > 0 else 0.0))
        net += d
        r["taken"] += 1
        r["pnl"] += g[i]
        r["pnl_hedged"] += g[i] + hg[i]
        r["peak_open"] = max(r["peak_open"], len(open_))
        r["peak_delta"] = max(r["peak_delta"], abs(net))
        r["peak_premium"] = max(r["peak_premium"], sum(p[2] for p in open_))
    return r


def inventory(E: pd.DataFrame, cfg) -> pd.DataFrame:
    P = param(cfg, "s11_headline_patience_min")
    A = E[E["spread_b"].isin(cfg["bankroll"]["s10_buckets"])]
    A = A.assign(exit_ts=A[f"exit_ts_through_{P}"], gross=A[f"gross_through_{P}"]).dropna(subset=["gross"])
    rng = np.random.default_rng(param(cfg, "s11_seed"))
    draws = param(cfg, "s11_inventory_draws")
    days = [_day_arrays(Dd.reset_index(drop=True)) for _, Dd in A.groupby("date")]
    rows = []
    for long_only in (False, True):
        for n in cfg["bankroll"]["s10_fills_per_day"]:
            for mo, md in param(cfg, "s11_limits"):
                res = [_inventory_core(arr, n, rng, mo, md, long_only) for arr in days for _ in range(draws)]
                R = pd.DataFrame(res)
                tot = (R["taken"] + R["skipped"]).replace(0, np.nan)
                rows.append({"entries": "buys only" if long_only else "buys and sells", "fills_per_day": n,
                             "max_open": mo, "max_delta_shares": md,
                             "mean_day_usd": R["pnl"].mean(), "sd_day_usd": R["pnl"].std(),
                             "p5_day_usd": R["pnl"].quantile(0.05),
                             "mean_day_hedged_usd": R["pnl_hedged"].mean(), "sd_day_hedged_usd": R["pnl_hedged"].std(),
                             "p5_day_hedged_usd": R["pnl_hedged"].quantile(0.05),
                             "share_skipped": float((R["skipped"] / tot).mean()),
                             "median_peak_open": R["peak_open"].median(),
                             "p95_peak_delta_shares": R["peak_delta"].quantile(0.95),
                             "p95_peak_long_premium_usd": R["peak_premium"].quantile(0.95)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1-second IWM (optional; priced first)
# ---------------------------------------------------------------------------
def iwm_path(cfg, day):
    return data_path(cfg, "raw", "equity", param(cfg, "s11_iwm_symbol").lower(),
                     param(cfg, "s11_iwm_schema").replace("-", "_"), f"{day}.parquet")


def iwm_args(cfg, day) -> dict:
    m = cfg["market"]
    return dict(dataset=param(cfg, "s11_iwm_dataset"), symbols=[param(cfg, "s11_iwm_symbol")],
                stype_in="raw_symbol", schema=param(cfg, "s11_iwm_schema"),
                start=calm.et_time(day, m["rth_open"]).isoformat(), end=calm.et_time(day, m["rth_close"]).isoformat())


def iwm_pull(cfg, cl, budget, days, price_only: bool) -> dict:
    out = {"pieces": 0, "usd": 0.0, "written": 0, "skipped": 0}
    for d in days:
        p = iwm_path(cfg, d)
        if p.exists():
            out["skipped"] += 1
            continue
        out["pieces"] += 1
        if price_only:
            out["usd"] += budget.price(cl, iwm_args(cfg, d))
            continue
        data, cost = budget.pull(cl, "study11", f"IWM {param(cfg, 's11_iwm_schema')} {d}", iwm_args(cfg, d))
        df = data.to_df()
        if "ts_recv" not in df.columns:
            df = df.reset_index()
        store.write(df, p)
        out["usd"] += cost
        out["written"] += 1
    return out


def load_iwm(cfg, day) -> pd.Series | None:
    p = iwm_path(cfg, day)
    if not p.exists():
        return None
    q = study10.norm_quotes(store.read(p))
    q = _valid(q)
    return pd.Series(((q["bid"] + q["ask"]) / 2).to_numpy(), index=pd.DatetimeIndex(q["ts"])).sort_index()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def seen_sessions(cal: pd.DataFrame, cfg) -> list:
    return sorted(study10.draw_sessions(cal, cfg) + study10.confirm_sessions(cal, cfg))


def session_entries(cfg, day):
    m = cfg["market"]
    raw_t, raw_q = study10._read_pieces(cfg, "tcbbo", day), study10._read_pieces(cfg, "cbbo-1m", day)
    E, ex = study10.session_fills(raw_t, raw_q, day, cfg)
    t0, close = calm.et_time(day, m["rth_open"]), calm.et_time(day, m["rth_close"])
    tr = study10.norm_trades(raw_t)
    tr = tr[(tr["ts"] >= t0) & (tr["ts"] < close)].reset_index(drop=True)
    q = study10.norm_quotes(raw_q)
    obs = pd.concat([q, tr[["ts", "symbol", "bid", "ask"]]], ignore_index=True)
    pat, tick = param(cfg, "s11_patience_min"), param(cfg, "s11_tick_usd")
    T = exit_times(E, tr, obs, max(pat), tick, close)
    E = round_trips(E, T, obs, pat, tick, param(cfg, "s11_fallback_slip_ticks"), close, m["option_multiplier"])
    Sm = study10b.parity_prices(q, day, param(cfg, "s10b_parity_strikes"))
    Sm = Sm[(Sm.index >= t0) & (Sm.index <= close)]
    S1 = load_iwm(cfg, day)
    if S1 is not None and len(S1):
        S1 = S1[(S1.index >= t0) & (S1.index <= close)]
    use_1s = S1 is not None and len(S1) > 0
    S, source = (S1, "iwm_1s") if use_1s else (Sm, "parity_1m")
    fwd = forward_by_expiry(q, param(cfg, "s10b_parity_strikes"))
    E["iv"], E["delta"] = option_greeks(E, fwd, Sm)
    E["delta_b"] = study10.bucket(E["delta"].abs(), param(cfg, "s11_delta_edges"))
    st = stale_features(E, E["delta"].to_numpy(), S, param(cfg, "s11_stale_lookback_s"), source)
    E = pd.concat([E, st], axis=1)
    P = param(cfg, "s11_headline_patience_min")
    E["hedge"] = hedge_pnl(E, E["delta"].to_numpy(), E[f"exit_ts_through_{P}"], S,
                           param(cfg, "s11_hedge_cost_per_share_usd"))
    E["iwm_source"] = source
    sanity = {"iwm_source": source, "entries": int(len(E)),
              "share_no_size_at_entry": float(E[["bid_sz", "ask_sz"]].isna().any(axis=1).mean())
              if {"bid_sz", "ask_sz"} <= set(E.columns) else 1.0,
              "share_iv_failed": float(E["iv"].isna().mean()) if len(E) else np.nan,
              "median_iv": float(E["iv"].median()) if len(E) else np.nan}
    if use_1s and len(Sm):
        a = _asof(S1, Sm.index)
        diff = np.abs(a / Sm.to_numpy() - 1) * 1e4
        r1, r2 = np.diff(np.log(a)), np.diff(np.log(Sm.to_numpy()))
        ok = np.isfinite(r1) & np.isfinite(r2)
        sanity["iwm_1s_vs_parity_median_abs_bp"] = float(np.nanmedian(diff))
        sanity["iwm_1s_vs_parity_corr_1min_returns"] = float(np.corrcoef(r1[ok], r2[ok])[0, 1]) if ok.sum() > 2 else np.nan
    return E, sanity


def entries_table(cfg, save: bool = True):
    cal = store.load_calendar(cfg)
    days = seen_sessions(cal, cfg)
    parts, sanity = [], {}
    for d in days:
        if not calm.in_sample(d, cfg):
            raise RuntimeError(f"{d} is not in sample")
        log.info("session %s", d)
        E, s = session_entries(cfg, d)
        parts.append(E)
        sanity[str(d)] = s
    E = pd.concat(parts, ignore_index=True)
    if save:
        store.save_derived(E, "study11_entries", cfg)
    return E, sanity


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _boot(x: pd.DataFrame, col: str, cfg) -> dict:
    b = stats.day_bootstrap_mean(x.dropna(subset=[col]), col, param(cfg, "bootstrap_draws"),
                                 param(cfg, "bootstrap_seed"), param(cfg, "ci_level"))
    return {"mean": b["mean"], "ci_lo": b["lo"], "ci_hi": b["hi"]}


def _summary(x: pd.DataFrame, P: int) -> dict:
    h = f"gross_through_{P}"
    per = x.groupby("date")[h].mean()
    return {"n": int(len(x)), "sessions": int(x["date"].nunique()), "mark_to_mid_rs_5": float(x["rs_5"].mean()),
            f"round_trip_through_{P}": float(x[h].mean()), f"round_trip_queue_{P}": float(x[f"gross_queue_{P}"].mean()),
            f"round_trip_front_{P}": float(x[f"gross_front_{P}"].mean()),
            f"exited_passively_through_{P}": float(x[f"passive_through_{P}"].mean()),
            "cleared": float(x["cleared"].mean()), "sessions_positive_through": int((per > 0).sum())}


def _table(A: pd.DataFrame, by, P: int) -> dict:
    return {(" | ".join(str(v) for v in k) if isinstance(k, tuple) else str(k)): _summary(g, P)
            for k, g in A.groupby(by, dropna=True)}


def explore(E: pd.DataFrame, cfg, sanity: dict | None = None) -> dict:
    pat, P = param(cfg, "s11_patience_min"), param(cfg, "s11_headline_patience_min")
    head = f"gross_through_{P}"
    buckets = cfg["bankroll"]["s10_buckets"]
    E = E.copy()
    E["venue_name"] = E["venue"].map(lambda v: VENUES.get(int(v), str(v)) if pd.notna(v) else None)
    E["side"] = np.where(E["s"] == 1, "buy at bid", "sell at ask")

    # #1 round trip by spread bucket
    rt, mono = {}, True
    for b, x in E.groupby("spread_b"):
        r = {"n": int(len(x)), "sessions": int(x["date"].nunique()), "mark_to_mid_rs_5": float(x["rs_5"].mean()),
             "half_spread_usd": float((x["spread"] / 2 * 100).mean())}
        for p in pat:
            for rule in MODELS:
                r[f"{rule}_{p}"] = {"mean": float(x[f"gross_{rule}_{p}"].mean()),
                                    "exited_passively": float(x[f"passive_{rule}_{p}"].mean()),
                                    "mean_hold_min": float(x[f"hold_{rule}_{p}"].mean())}
            f, q, t = (r[f"{rule}_{p}"]["mean"] for rule in ("front", "queue", "through"))
            mono &= bool(f >= q - 1e-9 and q >= t - 1e-9)
        r[f"headline_through_{P}_ci"] = _boot(x, head, cfg)
        r[f"front_{P}_ci"] = _boot(x, f"gross_front_{P}", cfg)
        r[f"through_{P}_by_fee"] = {sc: float(v) for sc, v in fee_net(x, head, cfg).mean().items()}
        r["by_side_through"] = {k: float(g[head].mean()) for k, g in x.groupby("side")}
        rt[str(b)] = r
    A = E[E["spread_b"].isin(buckets)].reset_index(drop=True)

    # #2 stale quotes
    L = param(cfg, "s11_stale_main_lookback_s")
    edges = param(cfg, "s11_stale_edges")
    lab = study10.bucket(A[f"stale_{L}"].fillna(np.inf), edges)
    lab = np.where(A[f"stale_{L}"].isna(), "no value", np.where(lab == None, "favourable (<0)", lab))   # noqa: E711
    stale = {"lookback_s": L, "by_ratio": _table(A.assign(_r=lab), "_r", P), "candidates": {}}
    for Lb in param(cfg, "s11_stale_lookback_s"):
        col = f"stale_{Lb}"
        if A[col].isna().all():
            continue
        for k, thr in zip(("K1", "K2"), param(cfg, "s11_stale_skip")):
            keep = ~(A[col] >= thr).to_numpy(bool)
            stale["candidates"][f"{k}_skip_stale_{thr}_{Lb}s"] = {
                "round_trip": study10b.compare(A, keep, cfg, head), "mark_to_mid": study10b.compare(A, keep, cfg, "rs_5")}

    # #4 contract selection
    k3 = param(cfg, "s11_skip_dte_bucket")
    keep3 = ~A["dte_b"].eq(k3).to_numpy(bool)
    sel = {"by_dte": _table(A, "dte_b", P), "by_premium": _table(A, "prem_b", P), "by_abs_delta": _table(A, "delta_b", P),
           "by_side": _table(A, "side", P), "by_right": _table(A, "right", P), "by_venue": _table(A, "venue_name", P),
           "by_spread_and_dte_all_buckets": _table(E, ["spread_b", "dte_b"], P),
           "candidates": {f"K3_skip_dte_{k3}": {"round_trip": study10b.compare(A, keep3, cfg, head),
                                                  "mark_to_mid": study10b.compare(A, keep3, cfg, "rs_5")}}}
    inv = inventory(E, cfg)
    checks = {"front_ge_queue_ge_through": mono,
              "share_round_trip_missing": float(E[head].isna().mean()),
              "share_delta_missing": float(E["delta"].isna().mean()),
              "iwm_source": sorted(E["iwm_source"].unique().tolist()) if "iwm_source" in E else None}
    return {"note": "Study 11 EXPLORATION (execution design) on the 10 seen sessions, in sample; $ a contract; "
                    "not a test: choices are frozen and confirmed on fresh sessions",
            "sessions": sorted(str(d) for d in E["date"].unique()), "entries": int(len(E)),
            "headline": f"through rule, {P}-minute patience, $0 commission (fees in through_{P}_by_fee)",
            "round_trip": rt, "checks": checks, "stale_quotes": stale, "contract_selection": sel,
            "inventory": inv.to_dict(orient="records"), "sanity": sanity or {}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iwm-pull", action="store_true", help="1-second IWM for the 10 sessions (with --price-only: quote)")
    ap.add_argument("--price-only", action="store_true")
    ap.add_argument("--approve-usd", type=float, default=None)
    ap.add_argument("--allow-past-total", action="store_true")
    ap.add_argument("--explore", action="store_true", help="build the entries table and print the report")
    ap.add_argument("--report-only", action="store_true", help="report from the saved study11_entries")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    from src.analysis import to_json
    if a.iwm_pull:
        days = seen_sessions(store.load_calendar(cfg), cfg)
        print("sessions:", ", ".join(str(d) for d in days))
        budget = spend.Budget(cfg, a.approve_usd, a.allow_past_total)
        r = iwm_pull(cfg, spend.client(), budget, days, a.price_only)
        what = "quote (nothing pulled)" if a.price_only else "pulled"
        print(f"{what}: {r['pieces']} sessions, ${r['usd']:.4f}; already on disk {r['skipped']}; "
              f"written {r['written']}; ledger ${spend.total_spent(cfg):.2f}")
        return
    if a.explore:
        E, sanity = entries_table(cfg)
        print(to_json(explore(E, cfg, sanity)))
        return
    if a.report_only:
        E = store.load_derived("study11_entries", cfg)
        print(to_json(explore(E, cfg)))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
