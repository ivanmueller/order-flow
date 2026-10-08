"""Study 10 (DRAFT, RUNLOG 2026-10-08): can a priority-customer resting option order earn the spread?

Step 0, pricing only (free; nothing is downloaded). Calls Databento metadata.get_cost for one RTH session
(09:30-16:00 ET) of each OPRA parent and schema on a few evenly spaced weekdays, and prints the mean cost per
session and for the proposed number of sessions. Pricing touches no market data.

  python -m src.study10 --price
  python -m src.study10 --price --parents SPY.OPT XSP.OPT --sessions 10

Schemas: tcbbo = every trade with the consolidated NBBO at the trade (the fills); cbbo-1m = consolidated NBBO
sampled each minute (the marks at +1, +5, +15 minutes); cbbo-1s is priced for reference only.
The pilot itself is built after the draft is approved.
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import spend
from src.config import load_config

log = logging.getLogger("study10")

DEFAULT_PARENTS = ["SPY.OPT", "QQQ.OPT", "IWM.OPT", "XSP.OPT", "SPXW.OPT"]
DEFAULT_SCHEMAS = ["tcbbo", "cbbo-1m", "cbbo-1s"]
PILOT_SCHEMAS = {"tcbbo", "cbbo-1m"}          # what the pilot would pull; cbbo-1s is reference only


def cost_args(dataset: str, parent: str, schema: str, day: pd.Timestamp, rth_open: str, rth_close: str) -> dict:
    d = pd.Timestamp(day).strftime("%Y-%m-%d")
    s = pd.Timestamp(f"{d} {rth_open}", tz="America/New_York").tz_convert("UTC")
    e = pd.Timestamp(f"{d} {rth_close}", tz="America/New_York").tz_convert("UTC")
    return dict(dataset=dataset, symbols=[parent], stype_in="parent", schema=schema,
                start=s.isoformat(), end=e.isoformat())


def sample_days(start, end, n: int) -> list[pd.Timestamp]:
    days = list(pd.bdate_range(start, end))
    if n >= len(days):
        return days
    return [days[i] for i in np.linspace(0, len(days) - 1, n).round().astype(int)]


def price_table(cl, dataset, parents, schemas, days, rth_open, rth_close, sessions: int) -> pd.DataFrame:
    rows = []
    for p in parents:
        pilot_total, pilot_missing = 0.0, False
        for sc in schemas:
            costs, err = [], ""
            for d in days:
                try:
                    costs.append(float(spend.with_retries(
                        lambda: cl.metadata.get_cost(**cost_args(dataset, p, sc, d, rth_open, rth_close)),
                        f"get_cost {p} {sc}")))
                except Exception as e:          # an unavailable schema is reported, not fatal
                    err = str(e)[:120]
                    break
            per = float(np.mean(costs)) if costs and not err else np.nan
            if sc in PILOT_SCHEMAS:
                pilot_missing |= not np.isfinite(per)
                pilot_total += per if np.isfinite(per) else 0.0
            rows.append({"parent": p, "schema": sc, "usd_per_session": per, "usd_for_sessions": per * sessions,
                         "days_priced": len(costs), "error": err})
        tot = np.nan if pilot_missing else pilot_total
        rows.append({"parent": p, "schema": "total", "usd_per_session": tot,
                     "usd_for_sessions": tot * sessions, "days_priced": len(days),
                     "error": "pilot = tcbbo + cbbo-1m" + (" (a pilot schema is unavailable)" if pilot_missing else "")})
    return pd.DataFrame(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price", action="store_true", help="quote one RTH session per parent and schema")
    ap.add_argument("--parents", nargs="+", default=DEFAULT_PARENTS)
    ap.add_argument("--schemas", nargs="+", default=DEFAULT_SCHEMAS)
    ap.add_argument("--days", type=int, default=3, help="weekdays priced, evenly spaced over the in-sample period")
    ap.add_argument("--sessions", type=int, default=5, help="sessions the pilot would pull per parent")
    a = ap.parse_args(argv)
    if not a.price:
        ap.print_help()
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    end = pd.Timestamp(cfg["sample"]["holdout_start"]) - pd.Timedelta(days=1)
    days = sample_days(cfg["sample"]["start"], end, a.days)
    df = price_table(spend.client(), cfg["data"]["opra_dataset"], a.parents, a.schemas, days,
                     cfg["market"]["rth_open"], cfg["market"]["rth_close"], a.sessions)
    pd.set_option("display.width", 180)
    print(f"Study 10 price quote (nothing pulled; ledger ${spend.total_spent(cfg):.2f}); days priced: "
          f"{', '.join(d.strftime('%Y-%m-%d') for d in days)}")
    print(df.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))


if __name__ == "__main__":
    main()
