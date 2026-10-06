"""Ingest SPX/SPXW end-of-day quotes (ThetaData, free) and start-of-day open interest (Databento OPRA).

For each trading day D in [start, end]:
  * ThetaData EOD report dated D (closing NBBO) -> data/raw/options/eod/YYYYMMDD_{SPXW,SPX}.parquet
    (used as the D-1 "prior close" quote by session D+1)
  * OPRA statistics 00:00-09:30 ET on D, OPEN_INTEREST, last per symbol
    -> data/raw/options/oi/YYYYMMDD_{SPX,SPXW}.parquet  (as_of_date = D)

Idempotent: existing files are skipped. Empty days (holidays) are written as empty files.

  python -m src.ingest_options --start 2023-06-01 --end 2023-06-30 --what eod
  python -m src.ingest_options --start 2023-06-01 --end 2025-12-31 --what oi --price-only
  python -m src.ingest_options --start 2023-06-01 --end 2023-06-30 --what oi --approve-usd 3
  python -m src.ingest_options --probe 2023-06-01      # day-one check: raw columns from both sources
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import threading
import time

import numpy as np
import pandas as pd
import requests

from databento.common.error import BentoClientError

from src import calendar as calm
from src import spend, store
from src.config import load_config, param

log = logging.getLogger("ingest_options")

EOD_COLS = ["quote_date", "symbol", "expiration", "strike", "right", "bid", "ask", "close", "volume"]
OI_COLS = ["as_of_date", "symbol", "root", "expiration", "strike", "right", "open_interest"]


# ---------------------------------------------------------------------------
# ThetaData
# ---------------------------------------------------------------------------
def _norm_right(x) -> str:
    s = str(x).strip().upper()
    return {"CALL": "C", "PUT": "P"}.get(s, s[:1])


def _norm_date(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
    return pd.to_datetime(s, format="%Y%m%d").dt.date


def normalize_eod(raw: pd.DataFrame, symbol: str, quote_date: dt.date) -> pd.DataFrame:
    """Map a ThetaData EOD CSV to the options_eod schema.

    Tolerates v3 (strike in dollars, right CALL/PUT, ISO dates) and v2 (strike in 1/1000 dollars,
    right C/P, YYYYMMDD ints). Run --probe on day one and confirm the mapping.
    """
    cols = {c.lower(): c for c in raw.columns}

    def col(*names):
        for n in names:
            if n in cols:
                return raw[cols[n]]
        raise KeyError(f"ThetaData EOD: none of {names} in columns {list(raw.columns)}")

    strike = pd.to_numeric(col("strike"), errors="coerce").astype(float)
    if strike.median() > 100_000:  # v2 reports strike x 1000
        strike = strike / 1000.0
    out = pd.DataFrame({
        "quote_date": quote_date,
        "symbol": symbol,
        "expiration": _norm_date(col("expiration", "exp")),
        "strike": strike,
        "right": col("right").map(_norm_right),
        "bid": pd.to_numeric(col("bid", "bid_price"), errors="coerce"),
        "ask": pd.to_numeric(col("ask", "ask_price"), errors="coerce"),
        "close": pd.to_numeric(col("close"), errors="coerce"),
        "volume": pd.to_numeric(col("volume"), errors="coerce").fillna(0).astype("int64"),
    })
    return out[out["right"].isin(["C", "P"])][EOD_COLS].reset_index(drop=True)


def fetch_eod(cfg, symbol: str, day: dt.date) -> pd.DataFrame:
    url = f"{cfg['data']['thetadata_url']}/option/history/eod"
    params = {"symbol": symbol, "expiration": "*", "start_date": day.strftime("%Y%m%d"),
              "end_date": day.strftime("%Y%m%d"), "max_dte": param(cfg, "max_dte"), "format": "csv"}
    r = requests.get(url, params=params, timeout=300)
    if r.status_code in (204, 472) or not r.text.strip():  # 472 = ThetaData "no data"
        return pd.DataFrame(columns=EOD_COLS)
    r.raise_for_status()
    raw = pd.read_csv(io.StringIO(r.text))
    if raw.empty:
        return pd.DataFrame(columns=EOD_COLS)
    return normalize_eod(raw, symbol, day)


def ingest_eod(cfg, days):
    pause = cfg["data"]["thetadata_pause_s"]
    for day in days:
        for sym in cfg["data"]["thetadata_symbols"]:
            p = store.eod_path(cfg, day, sym)
            if p.exists():
                continue
            df = fetch_eod(cfg, sym, day)
            store.write(df, p)
            log.info("eod %s %s rows=%d", day, sym, len(df))
            time.sleep(pause)


# ---------------------------------------------------------------------------
# Databento OPRA open interest
# ---------------------------------------------------------------------------
def oi_args(cfg, day: dt.date, last_day: dt.date | None = None) -> dict:
    """Statistics from 00:00 ET on `day` to 09:30 ET on `last_day` (default: the same day).

    A multi-day range returns the in-between hours too (more data, a little more cost) but needs
    one server-side scan instead of one per day, which is what makes the per-day pull slow.
    """
    return dict(dataset=cfg["data"]["opra_dataset"], symbols=list(cfg["data"]["opra_parents"]),
                stype_in="parent", schema="statistics",
                start=calm.et_time(day, "00:00").isoformat(),
                end=calm.et_time(last_day or day, cfg["market"]["rth_open"]).isoformat())


def parse_osi(symbols: pd.Series) -> pd.DataFrame:
    """OSI symbol 'SPXW  230601C04200000' -> root, expiration, right, strike."""
    osi = symbols.astype(str).str.replace(" ", "", regex=False)
    parts = osi.str.extract(r"^(?P<root>[A-Z]+)(?P<exp>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")
    return pd.DataFrame({
        "root": parts["root"],
        "expiration": pd.to_datetime(parts["exp"], format="%y%m%d").dt.date,
        "right": parts["right"],
        "strike": parts["strike"].astype(float) / 1000.0,
    }, index=symbols.index)


def split_pre_open(stats: pd.DataFrame, cfg) -> dict:
    """Records published before the open, keyed by ET date, for a multi-day statistics pull."""
    if stats.empty or "ts_event" not in stats.columns:
        return {}
    ts = pd.to_datetime(stats["ts_event"], utc=True)
    mod = calm.minutes_of_day_et(ts)
    pre = stats[(mod < calm.hhmm_to_min(cfg["market"]["rth_open"])).to_numpy()]
    return {d: g for d, g in pre.groupby(ts[pre.index].dt.tz_convert(calm.ET).dt.date)}


def normalize_oi(stats: pd.DataFrame, day: dt.date, max_dte: int) -> pd.DataFrame:
    import databento as db
    s = stats[stats["stat_type"] == int(db.StatType.OPEN_INTEREST)]
    if "ts_event" in s.columns:
        s = s.sort_values("ts_event", kind="stable")
    oi = s.drop_duplicates("symbol", keep="last")[["symbol", "quantity"]].rename(
        columns={"quantity": "open_interest"})
    oi = oi.join(parse_osi(oi["symbol"])).dropna(subset=["root"])
    oi = oi[(oi["open_interest"] > 0) & (oi["expiration"] >= day)
            & (oi["expiration"] <= day + dt.timedelta(days=max_dte))]
    oi.insert(0, "as_of_date", day)
    return oi[OI_COLS].reset_index(drop=True)


def _chunks(days: list, n: int) -> list[list]:
    """Consecutive runs of up to n days (a gap of more than 4 calendar days starts a new chunk)."""
    out: list[list] = []
    for d in days:
        if out and len(out[-1]) < n and (d - out[-1][-1]).days <= 4:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def _write_day(cfg, roots, day, df, max_dte):
    oi = normalize_oi(df, day, max_dte) if not df.empty else pd.DataFrame(columns=OI_COLS)
    for r in roots:
        store.write(oi[oi["root"] == r], store.oi_path(cfg, day, r))
    return len(oi)


def ingest_oi_chunked(cfg, todo, budget: spend.Budget, price_only: bool, chunk_days: int, roots):
    cl = spend.client()
    chunks = _chunks(todo, chunk_days)
    if price_only:
        pick = chunks[: min(3, len(chunks))]
        costs = [budget.price(cl, oi_args(cfg, c[0], c[-1])) for c in pick]
        per_day = sum(costs) / max(1, sum(len(c) for c in pick))
        print(f"OPRA OI chunked ({chunk_days} days/request): priced {len(pick)} chunks, "
              f"${per_day:.4f}/day -> est ${per_day * len(todo):.2f} for {len(todo)} days in {len(chunks)} requests "
              f"(ledger so far ${spend.total_spent(cfg):.2f})")
        return
    for c in chunks:
        try:
            data, cost = budget.pull(cl, "oi", f"{c[0]}..{c[-1]}", oi_args(cfg, c[0], c[-1]))
        except BentoClientError as e:
            if not _no_symbols(e):
                raise
            log.warning("oi %s..%s: no OPRA symbols; writing empty files", c[0], c[-1])
            for day in c:
                _write_day(cfg, roots, day, pd.DataFrame(), param(cfg, "max_dte"))
            continue
        df = data.to_df()
        if "ts_event" not in df.columns:
            df = df.reset_index()
        by_day = split_pre_open(df, cfg)
        rows = {day: _write_day(cfg, roots, day, by_day.get(day, pd.DataFrame()), param(cfg, "max_dte")) for day in c}
        log.info("oi %s..%s days=%d rows=%s cost=$%.4f run=$%.2f", c[0], c[-1], len(c),
                 "/".join(str(v) for v in rows.values()), cost, budget.spent)


def _pull_one_day(cfg, roots, budget, day, client_factory):
    """One day's OI: price, pull, normalise, write. Returns (day, rows, cost). Thread-safe."""
    cl = client_factory()
    try:
        data, cost = budget.pull(cl, "oi", day.isoformat(), oi_args(cfg, day))
    except BentoClientError as e:
        if not _no_symbols(e):
            raise
        log.warning("oi %s: OPRA has no SPX/SPXW symbols that day (holiday?); writing empty files", day)
        _write_day(cfg, roots, day, pd.DataFrame(), param(cfg, "max_dte"))
        return day, 0, 0.0
    df = data.to_df()
    if "ts_event" not in df.columns:
        df = df.reset_index()
    return day, _write_day(cfg, roots, day, df, param(cfg, "max_dte")), cost


def ingest_oi_parallel(cfg, todo, budget: spend.Budget, roots, workers: int):
    """Per-day requests, `workers` at a time. Same cost as sequential; the server-side scan per
    request (~30 s) is what makes the pull slow, and the scans overlap. A 429 retries with backoff."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    local = threading.local()

    def client_factory():
        if not hasattr(local, "cl"):
            local.cl = spend.client()
        return local.cl

    done, gave_up = 0, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_pull_one_day, cfg, roots, budget, d, client_factory): d for d in todo}
        for fut in as_completed(futures):
            try:
                day, rows, cost = fut.result()   # SpendRefused or a non-transient error still stops the run
            except spend.GaveUp as e:
                gave_up.append(futures[fut])
                log.error("oi %s: skipped for now (%s)", futures[fut], e)
                continue
            done += 1
            log.info("oi %s rows=%d cost=$%.4f run=$%.2f (%d/%d)", day, rows, cost, budget.spent, done, len(todo))
    if gave_up:
        log.error("%d day(s) skipped after repeated gateway timeouts: %s. Re-run the same command to fetch them.",
                  len(gave_up), ", ".join(str(d) for d in sorted(gave_up)))


def ingest_oi(cfg, days, budget: spend.Budget, price_only: bool, sample: int, chunk_days: int = 1,
              workers: int = 1):
    cl = spend.client()
    roots = [p.split(".")[0] for p in cfg["data"]["opra_parents"]]
    todo = [d for d in days if not all(store.oi_path(cfg, d, r).exists() for r in roots)]
    if chunk_days > 1:
        return ingest_oi_chunked(cfg, todo, budget, price_only, chunk_days, roots)
    if workers > 1 and not price_only:
        return ingest_oi_parallel(cfg, todo, budget, roots, workers)
    if price_only:
        pick = todo if sample <= 0 else [todo[i] for i in np.linspace(0, len(todo) - 1, min(sample, len(todo))).astype(int)]
        costs = [c for d in pick if (c := _price_day(budget, cl, cfg, d)) is not None]
        per_day = float(np.mean(costs)) if costs else 0.0
        print(f"OPRA OI: priced {len(pick)} days, mean ${per_day:.4f}/day, "
              f"{len(todo)} days to pull -> est ${per_day * len(todo):.2f} "
              f"(ledger so far ${spend.total_spent(cfg):.2f})")
        return
    for day in todo:
        try:
            data, cost = budget.pull(cl, "oi", day.isoformat(), oi_args(cfg, day))
        except BentoClientError as e:
            if not _no_symbols(e):
                raise
            log.warning("oi %s: OPRA has no SPX/SPXW symbols that day (holiday?); writing empty files", day)
            for r in roots:
                store.write(pd.DataFrame(columns=OI_COLS), store.oi_path(cfg, day, r))
            continue
        df = data.to_df()
        if "ts_event" not in df.columns:
            df = df.reset_index()
        n = _write_day(cfg, roots, day, df, param(cfg, "max_dte"))
        log.info("oi %s rows=%d cost=$%.4f run=$%.2f", day, n, cost, budget.spent)


def _no_symbols(e: Exception) -> bool:
    return "symbology" in str(e).lower() or "could be resolved" in str(e).lower()


def _price_day(budget, cl, cfg, day):
    try:
        return budget.price(cl, oi_args(cfg, day))
    except BentoClientError as e:
        if not _no_symbols(e):
            raise
        log.warning("oi %s: no OPRA symbols (holiday?), skipped in pricing", day)
        return None


# ---------------------------------------------------------------------------
def trading_days(cfg, start, end) -> list[dt.date]:
    """ES calendar if it exists (run bars first, it's cheap), otherwise weekdays."""
    p = store.derived_path(cfg, "calendar")
    if p.exists():
        c = store.read(p)
        if "equity_session" in c:
            c = c[c["equity_session"]]
        days = pd.to_datetime(c["date"]).dt.date
        return [d for d in days if store.as_date(start) <= d <= store.as_date(end)]
    return calm.weekdays(start, end)


def probe(cfg, day: dt.date):
    print("== ThetaData EOD ==")
    for sym in cfg["data"]["thetadata_symbols"]:
        url = f"{cfg['data']['thetadata_url']}/option/history/eod"
        r = requests.get(url, params={"symbol": sym, "expiration": "*", "start_date": day.strftime("%Y%m%d"),
                                      "end_date": day.strftime("%Y%m%d"), "max_dte": param(cfg, "max_dte"),
                                      "format": "csv"}, timeout=300)
        print(sym, r.status_code)
        print("\n".join(r.text.splitlines()[:4]))
    print("== Databento OPRA OI (cost only) ==")
    cl = spend.client()
    print(f"cost for {day}: ${cl.metadata.get_cost(**oi_args(cfg, day)):.4f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--what", choices=["eod", "oi", "both"], default="both")
    ap.add_argument("--price-only", action="store_true")
    ap.add_argument("--sample", type=int, default=10, help="days to price in --price-only (0 = all)")
    ap.add_argument("--chunk-days", type=int, default=1,
                    help="OI: days per Databento request (1 = one per day; multi-day costs ~16x more per day)")
    ap.add_argument("--workers", type=int, default=1,
                    help="OI: concurrent per-day requests (same cost; 4 is a sensible start)")
    ap.add_argument("--approve-usd", type=float, default=None)
    ap.add_argument("--allow-past-total", action="store_true")
    ap.add_argument("--include-holdout", action="store_true",
                    help="also download holdout dates (raw files only; research loads stay sealed)")
    ap.add_argument("--probe")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    if a.probe:
        probe(cfg, store.as_date(a.probe))
        return
    start = a.start or cfg["sample"]["start"]
    end = a.end or cfg["sample"]["end"]
    days = trading_days(cfg, start, end)
    if not a.include_holdout:
        days = [d for d in days if calm.in_sample(d, cfg)]
    # Session D needs the EOD report of D-1, so EOD also covers the day before `start`.
    if a.what in ("eod", "both"):
        first = store.as_date(start)
        prior = [d for d in calm.weekdays(pd.Timestamp(first) - pd.Timedelta(days=7), first) if d < first][-1:]
        eod_days = prior + days
        ingest_eod(cfg, eod_days)
    if a.what in ("oi", "both"):
        ingest_oi(cfg, days, spend.Budget(cfg, a.approve_usd, a.allow_past_total), a.price_only, a.sample,
                  a.chunk_days, a.workers)


if __name__ == "__main__":
    main()
