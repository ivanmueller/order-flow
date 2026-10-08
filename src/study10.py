"""Study 10 (APPROVED 2026-10-08, RUNLOG): can a priority-customer resting option order earn the spread? (IWM)

Pilot (one gated variant: IWM options, zero commission). For s10_sessions random in-sample sessions:
  fills    every OPRA print exactly at the consolidated bid (the resting side bought, s = +1) or ask (s = -1),
           quote valid (0 < bid < ask); prints of one contract, side and price within s10_sweep_ms are one
           opportunity (a multi-venue sweep fills a single resting order once). Excluded and counted: inside,
           outside, locked or crossed, zero bid, no quote, outside RTH.
  marks    mid at t + D (D in s10_horizons_min) = newest valid quote at or before t + D from cbbo-1m snapshots and
           the pre-trade quotes of later prints; dropped when t + D passes the close. later_D = a quote newer
           than the fill was seen (otherwise the quote is assumed unchanged).
  value    RS_D = s x (mid(t + D) - p) x 100, $ a contract; net_D = RS_D - s10_gate_fee_usd ($0); other fee
           scenarios reported: pass-through $0.05, IBKR tiered + $0.05, IBKR one-lot $1.05.
  ADVANCE  if some quoted-spread bucket has >= study10_min_fills fills, present in every session, a 90%
           session-bootstrap lower bound of the 5-minute net mean > 0, and a 15-minute net mean > 0. Else KILL.
Every input to a fill's classification and buckets is the quote at the print; marks are outcomes.

  python -m src.study10 --sessions-list                    # the drawn sessions (no data)
  python -m src.study10 --pull --price-only                # exact quote for every piece (free)
  python -m src.study10 --pull --approve-usd 12 --allow-past-total
  python -m src.study10 --run                              # fills, marks, report (saves study10_fills)
  python -m src.study10 --report-only

Step 0, pricing only (free; nothing is downloaded). Calls Databento metadata.get_cost for one RTH session
(09:30-16:00 ET) of each OPRA parent and schema on a few evenly spaced in-sample weekdays, and prints the cost
per session and for the proposed number of sessions. Pricing touches no market data.

A whole option chain over a whole session is too heavy for one get_cost call (the gateway answers 504 after
about two minutes), so each session is priced in --chunk-min pieces, in parallel, and summed: the same total from
small requests. A piece that still fails after --attempts tries is reported, and that row's cost shows as unknown.

  python -m src.study10 --price                                   # SPY, QQQ, IWM, XSP, SPXW; 1 day; tcbbo + cbbo-1m
  python -m src.study10 --price --parents SPY.OPT --days 3
  python -m src.study10 --price --schemas tcbbo cbbo-1m cbbo-1s --chunk-min 15

Schemas: tcbbo = every trade with the consolidated NBBO at the trade (the fills); cbbo-1m = consolidated NBBO
sampled each minute (the marks at +1, +5, +15 minutes); cbbo-1s can be priced for reference.
The pilot itself is built after the draft is approved.
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from src import calendar as calm
from src import spend, stats, store
from src.config import data_path, load_config, param

log = logging.getLogger("study10")

DEFAULT_PARENTS = ["SPY.OPT", "QQQ.OPT", "IWM.OPT", "XSP.OPT", "SPXW.OPT"]
DEFAULT_SCHEMAS = ["tcbbo", "cbbo-1m"]
PILOT_SCHEMAS = {"tcbbo", "cbbo-1m"}          # what the pilot would pull
RETRY_WAIT_S = 5


def chunks(day, rth_open: str, rth_close: str, minutes: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[start, end) pieces of the RTH session in UTC, the last one clipped at the close."""
    d = pd.Timestamp(day).strftime("%Y-%m-%d")
    s = pd.Timestamp(f"{d} {rth_open}", tz="America/New_York").tz_convert("UTC")
    e = pd.Timestamp(f"{d} {rth_close}", tz="America/New_York").tz_convert("UTC")
    out, t = [], s
    while t < e:
        u = min(t + pd.Timedelta(minutes=minutes), e)
        out.append((t, u))
        t = u
    return out


def cost_args(dataset: str, parent: str, schema: str, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    return dict(dataset=dataset, symbols=[parent], stype_in="parent", schema=schema,
                start=start.isoformat(), end=end.isoformat())


def sample_days(start, end, n: int) -> list[pd.Timestamp]:
    days = list(pd.bdate_range(start, end))
    if n >= len(days):
        return days
    if n == 1:
        return [days[len(days) // 2]]                 # one day: the middle of the period, not its first day
    return [days[i] for i in np.linspace(0, len(days) - 1, n).round().astype(int)]


def _is_permanent(e: Exception) -> bool:
    """A 4xx other than 429 (e.g. an unknown schema) will not succeed on retry."""
    try:
        from databento.common.error import BentoClientError
        return isinstance(e, BentoClientError) and getattr(e, "http_status", None) != 429
    except ImportError:
        return isinstance(e, ValueError)


def _price_piece(cl, args: dict, attempts: int):
    """(cost, error). Transient errors are retried up to `attempts` tries in all; permanent ones are not."""
    err = ""
    for k in range(attempts):
        try:
            return float(cl.metadata.get_cost(**args)), ""
        except Exception as e:  # noqa: BLE001
            err = str(e).splitlines()[0][:120] if str(e) else type(e).__name__
            if _is_permanent(e) or isinstance(e, ValueError):
                break
            if k < attempts - 1:
                log.warning("get_cost %s %s %s: %s; retry in %ds", args["symbols"][0], args["schema"],
                            args["start"][11:16], err, RETRY_WAIT_S)
                time.sleep(RETRY_WAIT_S)
    return np.nan, err


def price_table(cl, dataset, parents, schemas, days, rth_open, rth_close, sessions: int,
                chunk_min: int = 30, workers: int = 6, attempts: int = 2) -> pd.DataFrame:
    jobs = [(p, sc, i, cost_args(dataset, p, sc, s, e))
            for p in parents for sc in schemas for i, d in enumerate(days)
            for s, e in chunks(d, rth_open, rth_close, chunk_min)]
    run = lambda j: _price_piece(cl, j[3], attempts)  # noqa: E731
    if workers > 1:
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(run, jobs))
    else:
        res = [run(j) for j in jobs]
    got = pd.DataFrame([{"parent": j[0], "schema": j[1], "day": j[2], "usd": r[0], "error": r[1]}
                        for j, r in zip(jobs, res)])
    rows = []
    for p in parents:
        pilot_total, pilot_missing = 0.0, False
        for sc in schemas:
            g = got[(got["parent"] == p) & (got["schema"] == sc)]
            failed = g[g["usd"].isna()]
            per = float(g.groupby("day")["usd"].sum().mean()) if failed.empty else np.nan
            err = "" if failed.empty else f"{len(failed)}/{len(g)} pieces failed: {failed['error'].iloc[0]}"
            if sc in PILOT_SCHEMAS:
                pilot_missing |= not np.isfinite(per)
                pilot_total += per if np.isfinite(per) else 0.0
            rows.append({"parent": p, "schema": sc, "usd_per_session": per, "usd_for_sessions": per * sessions,
                         "days_priced": len(days), "error": err})
        tot = np.nan if pilot_missing else pilot_total
        rows.append({"parent": p, "schema": "total", "usd_per_session": tot, "usd_for_sessions": tot * sessions,
                     "days_priced": len(days),
                     "error": "pilot = tcbbo + cbbo-1m" + (" (a pilot schema is unpriced)" if pilot_missing else "")})
    return pd.DataFrame(rows)


# ===========================================================================
# The pilot (approved 2026-10-08): IWM, one gated variant at zero commission
# ===========================================================================
PILOT_PULL = ("tcbbo", "cbbo-1m")
EPS = 1e-6
FEE_SCENARIOS = ("zero_commission", "pass_through", "ibkr_tiered", "ibkr_one_lot")


def draw_sessions(cal: pd.DataFrame, cfg) -> list:
    """s10_sessions random in-sample sessions from sample.start, no half days (seeded, sorted)."""
    start, hs = pd.Timestamp(cfg["sample"]["start"]).date(), calm.holdout_start(cfg)
    c = cal[(cal["date"] >= start) & (cal["date"] < hs)]
    if "half_day" in c:
        c = c[~c["half_day"].astype(bool)]
    days = sorted(c["date"])
    if not days:
        return []
    rng = np.random.default_rng(param(cfg, "s10_seed"))
    pick = rng.choice(len(days), size=min(param(cfg, "s10_sessions"), len(days)), replace=False)
    return sorted(days[i] for i in pick)


def _root(cfg) -> str:
    return param(cfg, "s10_parent").split(".")[0].lower()


def piece_path(cfg, schema: str, day, start: pd.Timestamp):
    return data_path(cfg, "raw", "opra", _root(cfg), schema.replace("-", "_"), str(day),
                     f"{start.tz_convert('America/New_York'):%H%M}.parquet")


def pull(cfg, cl, budget, days, price_only: bool, workers: int = 1) -> dict:
    """Every piece is priced with get_cost (Budget) before it is pulled; existing pieces are skipped.
    `cl` is a client, or a zero-argument factory giving each worker its own client."""
    ds, parent, m = cfg["data"]["opra_dataset"], param(cfg, "s10_parent"), cfg["market"]
    jobs, out = [], {"pieces": 0, "usd": 0.0, "written": 0, "skipped": 0, "empty": 0}
    for d in days:
        for sc in PILOT_PULL:
            for s, e in chunks(d, m["rth_open"], m["rth_close"], param(cfg, "s10_chunk_min")):
                p = piece_path(cfg, sc, d, s)
                if p.exists():
                    out["skipped"] += 1
                else:
                    jobs.append((sc, d, s, p, cost_args(ds, parent, sc, s, e)))
    out["pieces"] = len(jobs)
    get = cl if callable(cl) else (lambda: cl)

    def one(j):
        sc, d, s, p, args = j
        c = get()
        if price_only:
            return budget.price(c, args), None
        data, cost = budget.pull(c, "study10", f"{parent} {sc} {d} {s.tz_convert('America/New_York'):%H%M}", args)
        df = data.to_df()
        if "ts_recv" not in df.columns:
            df = df.reset_index()
        store.write(df, p)
        return cost, len(df)

    if workers > 1:
        with ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(one, jobs))
    else:
        res = [one(j) for j in jobs]
    for cost, n in res:
        out["usd"] += cost
        if n is not None:
            out["written"] += 1
            out["empty"] += int(n == 0)
    return out


def _read_pieces(cfg, schema: str, day) -> pd.DataFrame | None:
    m = cfg["market"]
    want = [piece_path(cfg, schema, day, s) for s, _ in chunks(day, m["rth_open"], m["rth_close"],
                                                               param(cfg, "s10_chunk_min"))]
    missing = [p for p in want if not p.exists()]
    if missing:
        raise FileNotFoundError(f"{schema} {day}: {len(missing)} of {len(want)} pieces missing; run --pull first")
    parts = [store.read(p) for p in want]
    parts = [x for x in parts if len(x)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _px(x: pd.Series) -> pd.Series:
    v = pd.to_numeric(x, errors="coerce").astype(float)
    if len(v) and np.nanmedian(np.abs(v.to_numpy())) > 1e6:          # fixed-point prices (1e-9 units)
        v = v / 1e9
    return v.where(v < 1e9)                                          # undefined price sentinel -> NaN


def norm_trades(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.reset_index() if "ts_recv" not in raw.columns else raw
    out = pd.DataFrame({"ts": pd.to_datetime(df["ts_recv"], utc=True), "symbol": df["symbol"].astype(str),
                        "price": _px(df["price"]), "size": pd.to_numeric(df["size"]).astype(int),
                        "bid": _px(df["bid_px_00"]), "ask": _px(df["ask_px_00"]), "venue": df["publisher_id"]})
    return out.sort_values("ts", kind="stable").reset_index(drop=True)


def norm_quotes(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["ts", "symbol", "bid", "ask"])
    df = raw.reset_index() if "ts_recv" not in raw.columns else raw
    return pd.DataFrame({"ts": pd.to_datetime(df["ts_recv"], utc=True), "symbol": df["symbol"].astype(str),
                         "bid": _px(df["bid_px_00"]), "ask": _px(df["ask_px_00"])})


def classify(tr: pd.DataFrame):
    """(prints at the bid or ask with s, counts of the excluded kinds)."""
    b, a, p = tr["bid"], tr["ask"], tr["price"]
    no_q = b.isna() | a.isna() | p.isna()
    zero = ~no_q & (b <= 0)
    lc = ~no_q & ~zero & (b >= a - EPS)
    ok = ~(no_q | zero | lc)
    at_bid = ok & ((p - b).abs() < EPS)
    at_ask = ok & ~at_bid & ((p - a).abs() < EPS)
    inside = ok & ~at_bid & ~at_ask & (p > b) & (p < a)
    outside = ok & ~at_bid & ~at_ask & ~inside
    f = tr[at_bid | at_ask].copy()
    f["s"] = np.where(at_bid[at_bid | at_ask], 1, -1)
    ex = {"inside": int(inside.sum()), "outside": int(outside.sum()), "locked_or_crossed": int(lc.sum()),
          "zero_bid": int(zero.sum()), "no_quote": int(no_q.sum())}
    return f.reset_index(drop=True), ex


def dedupe_sweeps(f: pd.DataFrame, sweep_ms: int) -> pd.DataFrame:
    """Prints of one contract, side and price, each within sweep_ms of the previous one, are one opportunity."""
    if f.empty:
        return f.assign(ts_last=f["ts"], n_prints=0)
    g = f.sort_values(["symbol", "s", "price", "ts"], kind="stable").reset_index(drop=True)
    same = (g["symbol"].eq(g["symbol"].shift()) & g["s"].eq(g["s"].shift())
            & (g["price"] - g["price"].shift()).abs().lt(EPS)
            & (g["ts"] - g["ts"].shift()).le(pd.Timedelta(milliseconds=sweep_ms)))
    g["eid"] = (~same).cumsum()
    e = g.groupby("eid").agg(ts=("ts", "first"), ts_last=("ts", "last"), symbol=("symbol", "first"),
                             s=("s", "first"), price=("price", "first"), size=("size", "sum"),
                             n_prints=("size", "size"), bid=("bid", "first"), ask=("ask", "first"),
                             venue=("venue", "first"))
    return e.sort_values("ts", kind="stable").reset_index(drop=True)


def clearing_flags(e: pd.DataFrame, tr: pd.DataFrame, window_s: int) -> np.ndarray:
    """The next print in the contract after the fill, within window_s, trades through the fill price."""
    if e.empty:
        return np.zeros(0, bool)
    left = e[["ts_last", "symbol", "s", "price"]].reset_index().sort_values("ts_last")
    right = tr[["ts", "symbol", "price"]].rename(columns={"price": "p_next", "ts": "ts_next"}).sort_values("ts_next")
    m = pd.merge_asof(left, right, left_on="ts_last", right_on="ts_next", by="symbol", direction="forward",
                      allow_exact_matches=False).set_index("index").sort_index()
    gap_ok = (m["ts_next"] - m["ts_last"]).le(pd.Timedelta(seconds=window_s))
    through = (m["s"] * (m["p_next"] - m["price"])) < -EPS
    return (gap_ok & through).fillna(False).to_numpy(bool)


def mark_mids(e: pd.DataFrame, obs: pd.DataFrame, horizons, session_end: pd.Timestamp) -> pd.DataFrame:
    """mid_D and later_D for every fill: the newest valid quote at or before t + D (NaN past the close)."""
    out = e.copy()
    q = obs[(obs["bid"] > 0) & (obs["ask"] > obs["bid"] + EPS)].copy()
    q = q.assign(mid=(q["bid"] + q["ask"]) / 2).rename(columns={"ts": "q_ts"})[["q_ts", "symbol", "mid"]]
    q = q.sort_values("q_ts")
    for h in horizons:
        left = out[["ts", "symbol"]].reset_index()
        left["target"] = left["ts"] + pd.Timedelta(minutes=h)
        left = left.sort_values("target")
        m = pd.merge_asof(left, q, left_on="target", right_on="q_ts", by="symbol",
                          direction="backward").set_index("index").sort_index()
        past = m["target"] > session_end
        out[f"mid_{h}"] = m["mid"].where(~past)
        out[f"later_{h}"] = (m["q_ts"] > m["ts"]) & ~past
    return out


def realized_spread_usd(m: pd.DataFrame, horizons, mult: float) -> pd.DataFrame:
    out = m.copy()
    for h in horizons:
        out[f"rs_{h}"] = out["s"] * (out[f"mid_{h}"] - out["price"]) * mult
    return out


def _fmt(x) -> str:
    x = float(x)
    return str(int(x)) if x.is_integer() else f"{x:.2f}"


def bucket(values, edges) -> np.ndarray:
    v = np.round(np.asarray(values, float), 6) + 1e-9
    k = np.searchsorted(np.asarray(edges, float), v, side="right") - 1
    labels = [f"{_fmt(edges[i])}-{_fmt(edges[i + 1])}" for i in range(len(edges) - 1)] + [f"{_fmt(edges[-1])}+"]
    return np.array([labels[i] if i >= 0 else None for i in k], dtype=object)


def fee_table(premium: pd.Series, cfg) -> pd.DataFrame:
    """$ a contract a side under each scenario."""
    other, tiers = param(cfg, "s10_other_fee_usd"), param(cfg, "s10_commission_tiers")
    prem = np.asarray(premium, float)
    comm = np.full(len(prem), tiers[-1][1], float)
    for bound, rate in reversed(tiers):
        comm = np.where(prem < bound, rate, comm)
    return pd.DataFrame({"zero_commission": np.full(len(prem), float(param(cfg, "s10_gate_fee_usd"))),
                         "pass_through": np.full(len(prem), other),
                         "ibkr_tiered": comm + other,
                         "ibkr_one_lot": np.full(len(prem), param(cfg, "s10_order_min_usd") + other)},
                        index=premium.index if isinstance(premium, pd.Series) else None)


def session_fills(raw_t: pd.DataFrame, raw_q: pd.DataFrame, day, cfg):
    """(one row per fill opportunity with RS, net, buckets; excluded counts) for one session."""
    from src.ingest_options import parse_osi
    from src.study9 import tod_label
    m, hz = cfg["market"], param(cfg, "s10_horizons_min")
    tr = norm_trades(raw_t)
    t0, t1 = calm.et_time(day, m["rth_open"]), calm.et_time(day, m["rth_close"])
    rth = (tr["ts"] >= t0) & (tr["ts"] < t1)
    n_out = int((~rth).sum())
    tr = tr[rth].reset_index(drop=True)
    f, ex = classify(tr)
    ex["outside_rth"] = n_out
    ex["prints"] = int(len(tr))
    e = dedupe_sweeps(f, param(cfg, "s10_sweep_ms"))
    e["cleared"] = clearing_flags(e, tr, param(cfg, "s10_clearing_window_s"))
    obs = pd.concat([norm_quotes(raw_q), tr[["ts", "symbol", "bid", "ask"]]], ignore_index=True)
    F = realized_spread_usd(mark_mids(e, obs, hz, t1), hz, m["option_multiplier"])
    osi = parse_osi(F["symbol"])
    F["date"] = day
    F["expiration"], F["right"], F["strike"] = osi["expiration"], osi["right"], osi["strike"]
    F["dte"] = [(x - day).days if pd.notna(x) else np.nan for x in F["expiration"]]
    F["mid0"] = (F["bid"] + F["ask"]) / 2
    F["spread"] = (F["ask"] - F["bid"]).round(4)
    F["spread_b"] = bucket(F["spread"], param(cfg, "s10_spread_edges"))
    F["dte_b"] = bucket(F["dte"], param(cfg, "s10_dte_edges"))
    F["prem_b"] = bucket(F["mid0"], [0.0] + list(param(cfg, "s10_premium_edges")))
    F["size_b"] = bucket(F["size"], param(cfg, "s10_size_edges"))
    edges = param(cfg, "s9_tod_edges")
    names = {f"b{i}": f"{edges[i]}-{edges[i + 1]}" for i in range(len(edges) - 1)}
    F["tod_b"] = [names.get(x) for x in tod_label(F["ts"], edges)]
    fees = fee_table(F["mid0"], cfg)
    for h in hz:
        F[f"net_{h}"] = F[f"rs_{h}"] - fees["zero_commission"]
        for sc in FEE_SCENARIOS[1:]:
            F[f"net_{h}_{sc}"] = F[f"rs_{h}"] - fees[sc]
    return F, ex


def verdict(F: pd.DataFrame, cfg, n_sessions: int, prefix: str = "net") -> dict:
    g = cfg["gates"]
    ha, hc = g["study10_advance_horizon_min"], g["study10_confirm_horizon_min"]
    col_a = f"{prefix}_{ha}" if prefix == "net" else f"net_{ha}_{prefix}"
    col_c = f"{prefix}_{hc}" if prefix == "net" else f"net_{hc}_{prefix}"
    rows = {}
    for b, x in F.groupby("spread_b"):
        xa = x.dropna(subset=[col_a])
        bs = stats.day_bootstrap_mean(xa, col_a, param(cfg, "bootstrap_draws"), param(cfg, "bootstrap_seed"),
                                      param(cfg, "ci_level"))
        conf = float(x[col_c].mean())
        r = {"n": int(len(x)), "sessions": int(x["date"].nunique()), f"mean_{col_a}": bs["mean"],
             "ci_lo": bs["lo"], "ci_hi": bs["hi"], f"mean_{col_c}": conf}
        r["advance"] = bool(r["n"] >= g["study10_min_fills"] and r["sessions"] == n_sessions
                            and np.isfinite(bs["lo"]) and bs["lo"] > 0 and np.isfinite(conf) and conf > 0)
        rows[str(b)] = r
    adv = [b for b, r in rows.items() if r["advance"]]
    return {"verdict": "ADVANCE" if adv else "KILL", "advancing_buckets": adv, "fee": prefix, "buckets": rows}


def _group(F: pd.DataFrame, by: str, hz) -> dict:
    out = {}
    for b, x in F.groupby(by, dropna=True):
        r = {"n": int(len(x)), "per_session": round(len(x) / max(1, F["date"].nunique()), 1)}
        for h in hz:
            r[f"rs_{h}"] = float(x[f"rs_{h}"].mean())
        out[str(b)] = r
    return out


def report(F: pd.DataFrame, cfg, n_sessions: int, excluded: dict | None = None) -> dict:
    hz = param(cfg, "s10_horizons_min")
    ha = cfg["gates"]["study10_advance_horizon_min"]
    by_spread = {}
    for b, x in F.groupby("spread_b"):
        r = {"n": int(len(x)), "per_session": round(len(x) / n_sessions, 1), "sessions": int(x["date"].nunique()),
             "cleared_share": float(x["cleared"].mean())}
        for h in hz:
            r[f"rs_{h}"] = float(x[f"rs_{h}"].mean())
            r[f"quote_seen_after_fill_{h}"] = float(x[f"later_{h}"].mean())
        r[f"rs_{ha}_cleared"] = float(x.loc[x["cleared"], f"rs_{ha}"].mean()) if x["cleared"].any() else np.nan
        r[f"rs_{ha}_not_cleared"] = float(x.loc[~x["cleared"], f"rs_{ha}"].mean()) if (~x["cleared"]).any() else np.nan
        for sc in FEE_SCENARIOS[1:]:
            r[f"net_{ha}_{sc}"] = float(x[f"net_{ha}_{sc}"].mean())
        by_spread[str(b)] = r
    out = {"note": "Study 10 pilot, IWM options, in sample; $ a contract; gate fee $0 (zero commission)",
           "sessions": sorted(str(d) for d in F["date"].unique()), "fills": int(len(F)),
           "fills_per_session": round(len(F) / max(1, n_sessions), 1),
           "excluded_prints": excluded or {},
           "verdict": verdict(F, cfg, n_sessions),
           "would_advance_at_pass_through_fee": verdict(F, cfg, n_sessions, "pass_through")["verdict"],
           "by_spread": by_spread}
    for col in ("dte_b", "prem_b", "tod_b", "size_b", "venue"):
        out[f"by_{col.replace('_b', '')}"] = _group(F, col, hz)
    return out


def sanity(raw_t: pd.DataFrame, raw_q: pd.DataFrame, F: pd.DataFrame, ex: dict) -> dict:
    """Data checks for one session: units, quote validity, snapshot clock, fill share."""
    t = norm_trades(raw_t)
    q = norm_quotes(raw_q)
    on_minute = float((q["ts"].dt.second.eq(0) & q["ts"].dt.microsecond.eq(0)).mean()) if len(q) else np.nan
    return {"prints": ex.get("prints", 0), "contracts_traded": int(t["symbol"].nunique()),
            "median_trade_price": float(t["price"].median()) if len(t) else np.nan,
            "share_at_bid_or_ask": round(F["n_prints"].sum() / max(1, ex.get("prints", 0)), 3) if len(F) else 0.0,
            "fill_opportunities": int(len(F)), "quote_snapshots": int(len(q)),
            "snapshot_share_on_whole_minute": on_minute,
            "median_quoted_spread_at_fills": float(F["spread"].median()) if len(F) else np.nan}


# ---------------------------------------------------------------------------
# Rule-6 diagnostics (descriptive; data already on disk)
# ---------------------------------------------------------------------------
def snapshot_convention(tr: pd.DataFrame, snaps: pd.DataFrame, tol_s: float = 1.0) -> dict:
    """Which moment a cbbo-1m snapshot stamped T describes: compare it with the market state at T - 60 s, T and
    T + 60 s, each read from the pre-trade quote of the first print within tol_s after that moment. If the
    state at T matches best, marks at t + D use no quote from after t + D."""
    q = tr[["ts", "symbol", "bid", "ask"]].rename(columns={"ts": "p_ts", "bid": "pb", "ask": "pa"}).sort_values("p_ts")
    out = {}
    for name, off in (("one_minute_before", -60), ("at_stamp", 0), ("one_minute_after", 60)):
        left = snaps[["ts", "symbol", "bid", "ask"]].copy()
        left["at"] = left["ts"] + pd.Timedelta(seconds=off)
        m = pd.merge_asof(left.sort_values("at"), q, left_on="at", right_on="p_ts", by="symbol",
                          direction="forward", tolerance=pd.Timedelta(seconds=tol_s)).dropna(subset=["pb"])
        hit = ((m["bid"] - m["pb"]).abs() < EPS) & ((m["ask"] - m["pa"]).abs() < EPS)
        out[f"match_state_{name}"] = float(hit.mean()) if len(m) else np.nan
        out[f"n_{name}"] = int(len(m))
    return out


def stability(F: pd.DataFrame, horizon: int = 5) -> dict:
    """Per spread bucket: the mean by session, sessions positive, the share of the quoted half-spread kept,
    and the marks that saw no quote newer than the fill."""
    out = {}
    col = f"rs_{horizon}"
    for b, x in F.groupby("spread_b"):
        per = {str(d): {"n": int(len(g)), col: float(g[col].mean())} for d, g in x.groupby("date")}
        half = float((x["spread"] / 2 * 100).mean())
        stale = ~x[f"later_{horizon}"].astype(bool)
        out[str(b)] = {"by_session": per, "sessions_positive": int(sum(v[col] > 0 for v in per.values())),
                       "mean_half_spread_usd": half,
                       "capture_of_half_spread": float(x[col].mean()) / half if half else np.nan,
                       "share_no_newer_quote": float(stale.mean()),
                       f"{col}_no_newer_quote": float(x.loc[stale, col].mean()) if stale.any() else np.nan,
                       f"{col}_newer_quote": float(x.loc[~stale, col].mean()) if (~stale).any() else np.nan}
    return out


def diagnose(cfg=None) -> dict:
    cfg = cfg or load_config()
    F = store.load_derived("study10_fills", cfg)
    days = sorted(F["date"].unique())
    conv = {}
    for d in days:
        t = norm_trades(_read_pieces(cfg, "tcbbo", d))
        q = norm_quotes(_read_pieces(cfg, "cbbo-1m", d))
        conv[str(d)] = snapshot_convention(t, q)
    return {"note": "Study 10 rule-6 diagnostics (descriptive, in sample)", "snapshot_convention": conv,
            "stability_5min": stability(F, cfg["gates"]["study10_advance_horizon_min"]),
            "stability_15min": stability(F, cfg["gates"]["study10_confirm_horizon_min"])}


def run(cfg=None, save: bool = True) -> dict:
    cfg = cfg or load_config()
    days = draw_sessions(store.load_calendar(cfg), cfg)
    frames, ex_tot, checks = [], {}, {}
    for d in days:
        if not calm.in_sample(d, cfg):
            raise RuntimeError(f"{d} is not in sample")
        t, q = _read_pieces(cfg, "tcbbo", d), _read_pieces(cfg, "cbbo-1m", d)
        F, ex = session_fills(t, q, d, cfg)
        checks[str(d)] = sanity(t, q, F, ex)
        for k, v in ex.items():
            ex_tot[k] = ex_tot.get(k, 0) + v
        frames.append(F)
    F = pd.concat(frames, ignore_index=True)
    if save:
        store.save_derived(F.drop(columns=["ts_last"]), "study10_fills", cfg)
    rep = report(F, cfg, len(days), ex_tot)
    rep["sanity"] = checks
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price", action="store_true", help="quote one RTH session per parent and schema")
    ap.add_argument("--sessions-list", action="store_true", help="print the drawn pilot sessions")
    ap.add_argument("--pull", action="store_true", help="pull the pilot data (with --price-only: quote only)")
    ap.add_argument("--price-only", action="store_true")
    ap.add_argument("--approve-usd", type=float, default=None)
    ap.add_argument("--allow-past-total", action="store_true")
    ap.add_argument("--run", action="store_true", help="fills, marks and the report")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--diagnose", action="store_true", help="rule-6 checks on the saved fills and raw data")
    ap.add_argument("--parents", nargs="+", default=DEFAULT_PARENTS)
    ap.add_argument("--schemas", nargs="+", default=DEFAULT_SCHEMAS)
    ap.add_argument("--days", type=int, default=1, help="weekdays priced, evenly spaced over the in-sample period")
    ap.add_argument("--sessions", type=int, default=5, help="sessions the pilot would pull per parent")
    ap.add_argument("--chunk-min", type=int, default=30, help="minutes per get_cost request")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--attempts", type=int, default=2, help="tries per piece before it is reported as failed")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    from src.analysis import to_json
    if a.sessions_list or a.pull:
        days = draw_sessions(store.load_calendar(cfg), cfg)
        print("pilot sessions:", ", ".join(str(d) for d in days))
        if a.pull:
            budget = spend.Budget(cfg, a.approve_usd, a.allow_past_total)
            local = threading.local()

            def thread_client():          # one Databento client per worker thread, reused for every piece
                if not hasattr(local, "cl"):
                    local.cl = spend.client()
                return local.cl
            r = pull(cfg, thread_client, budget, days, a.price_only, workers=min(a.workers, 4))
            what = "quote (nothing pulled)" if a.price_only else "pulled"
            print(f"{what}: {r['pieces']} pieces, ${r['usd']:.2f}; already on disk {r['skipped']}; "
                  f"written {r['written']} ({r['empty']} empty); ledger ${spend.total_spent(cfg):.2f}")
        return
    if a.run:
        print(to_json(run(cfg)))
        return
    if a.diagnose:
        print(to_json(diagnose(cfg)))
        return
    if a.report_only:
        F = store.load_derived("study10_fills", cfg)
        print(to_json(report(F, cfg, F["date"].nunique())))
        return
    if not a.price:
        ap.print_help()
        return
    end = pd.Timestamp(cfg["sample"]["holdout_start"]) - pd.Timedelta(days=1)
    days = sample_days(cfg["sample"]["start"], end, a.days)
    t0 = time.time()
    df = price_table(spend.client(), cfg["data"]["opra_dataset"], a.parents, a.schemas, days,
                     cfg["market"]["rth_open"], cfg["market"]["rth_close"], a.sessions,
                     a.chunk_min, a.workers, a.attempts)
    pd.set_option("display.width", 200)
    print(f"Study 10 price quote (nothing pulled; ledger ${spend.total_spent(cfg):.2f}); days priced: "
          f"{', '.join(d.strftime('%Y-%m-%d') for d in days)}; {time.time() - t0:.0f} s")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))


if __name__ == "__main__":
    main()
