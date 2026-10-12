"""Price-only menu for data the next studies may need. Never downloads anything.

Calls Databento metadata.get_cost (free) for continuous front-month symbols:
  trades    24h  : exact, one call per calendar month (Globex day incl. overnight)
  trades    RTH  : rth_open..rth_close ET on an even sample of weekdays, extrapolated to all weekdays
                   (exchange holidays are counted as weekdays, so the estimate errs high)
  ohlcv-1m  24h  : exact, one call per month (bars for the fade replication in other markets)

Pricing touches no market data, so it may quote any date range, holdout dates included.

  python -m src.price_menu --start 2023-06-01 --end 2025-12-31
  python -m src.price_menu --start 2025-10-01 --end 2026-09-30 --symbols ES.v.0
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from src import spend
from src.config import load_config

log = logging.getLogger("price_menu")

DEFAULT_SYMBOLS = ["ES.v.0", "NQ.v.0", "CL.v.0", "GC.v.0", "ZN.v.0", "6E.v.0"]


def rth_window_utc(day: pd.Timestamp, open_hhmm: str, close_hhmm: str):
    d = pd.Timestamp(day).strftime("%Y-%m-%d")
    s = pd.Timestamp(f"{d} {open_hhmm}", tz="America/New_York").tz_convert("UTC")
    e = pd.Timestamp(f"{d} {close_hhmm}", tz="America/New_York").tz_convert("UTC")
    return s, e


def month_spans(start, end) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[s, e) per calendar month, clipped to [start, end + 1 day)."""
    lo, hi = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    out = []
    for m in pd.period_range(lo, hi - pd.Timedelta(days=1), freq="M"):
        out.append((max(lo, m.start_time), min(hi, (m + 1).start_time)))
    return out


def weekdays(start, end) -> list[pd.Timestamp]:
    return list(pd.bdate_range(start, end))


def even_sample(days: list, n: int) -> list:
    if n >= len(days):
        return list(days)
    idx = np.linspace(0, len(days) - 1, n).round().astype(int)
    return [days[i] for i in idx]


def _cost(cl, dataset, symbol, schema, s, e) -> float:
    args = dict(dataset=dataset, symbols=symbol, stype_in="continuous", schema=schema,
                start=pd.Timestamp(s).isoformat(), end=pd.Timestamp(e).isoformat())
    return float(spend.with_retries(lambda: cl.metadata.get_cost(**args), f"get_cost {symbol} {schema}"))


def price_24h(cl, dataset, symbol, schema, start, end) -> float:
    return sum(_cost(cl, dataset, symbol, schema, s, e) for s, e in month_spans(start, end))


def price_rth(cl, dataset, symbol, schema, start, end, rth_open, rth_close, sample):
    days = weekdays(start, end)
    picked = even_sample(days, sample)
    costs = [_cost(cl, dataset, symbol, schema, *rth_window_utc(d, rth_open, rth_close)) for d in picked]
    return (float(np.mean(costs)) * len(days) if costs else 0.0), len(days), len(picked)


def _row(cl, dataset, symbol, schema, window, start, end, rth_open, rth_close, rth_sample):
    if window == "24h":
        usd, n_days, n_priced = price_24h(cl, dataset, symbol, schema, start, end), len(weekdays(start, end)), None
    else:
        usd, n_days, n_priced = price_rth(cl, dataset, symbol, schema, start, end, rth_open, rth_close, rth_sample)
    return dict(symbol=symbol, schema=schema, window=window, weekdays=n_days, rth_days_priced=n_priced,
                usd=usd, usd_per_weekday=usd / max(1, n_days))


def menu(cl, dataset, symbols, start, end, rth_open, rth_close, rth_sample, workers: int = 1) -> pd.DataFrame:
    jobs = []
    for sym in symbols:
        jobs += [(sym, "trades", "24h"), (sym, "trades", "RTH"), (sym, "ohlcv-1m", "24h")]
    run = lambda j: _row(cl, dataset, *j, start, end, rth_open, rth_close, rth_sample)  # noqa: E731
    if workers > 1:
        with ThreadPoolExecutor(workers) as ex:
            rows = list(ex.map(run, jobs))
    else:
        rows = [run(j) for j in jobs]
    return pd.DataFrame(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--rth-sample", type=int, default=24, help="weekdays priced for the RTH estimate")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    df = menu(spend.client(), cfg["data"]["es_dataset"], a.symbols, a.start, a.end,
              cfg["market"]["rth_open"], cfg["market"]["rth_close"], a.rth_sample, a.workers)
    pd.set_option("display.width", 160)
    print(f"Price menu {a.start}..{a.end} (quotes only, nothing pulled; ledger ${spend.total_spent(cfg):.2f})")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))


if __name__ == "__main__":
    main()
