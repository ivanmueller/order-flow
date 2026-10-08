"""Study 10 (DRAFT, RUNLOG 2026-10-08): can a priority-customer resting option order earn the spread?

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
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from src import spend
from src.config import load_config

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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price", action="store_true", help="quote one RTH session per parent and schema")
    ap.add_argument("--parents", nargs="+", default=DEFAULT_PARENTS)
    ap.add_argument("--schemas", nargs="+", default=DEFAULT_SCHEMAS)
    ap.add_argument("--days", type=int, default=1, help="weekdays priced, evenly spaced over the in-sample period")
    ap.add_argument("--sessions", type=int, default=5, help="sessions the pilot would pull per parent")
    ap.add_argument("--chunk-min", type=int, default=30, help="minutes per get_cost request")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--attempts", type=int, default=2, help="tries per piece before it is reported as failed")
    a = ap.parse_args(argv)
    if not a.price:
        ap.print_help()
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
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
