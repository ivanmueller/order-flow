"""Ingest ES from Databento GLBX.MDP3.

  bars        ES.v.0 ohlcv-1m, one file per month -> data/raw/es/bars/YYYY-MM.parquet,
              then rebuilds the trading calendar -> data/derived/calendar.parquet
  roll-basis  On roll days D, D's front contract at the D-1 cash close (for the basis)
              -> data/raw/es/roll_basis/YYYYMMDD.parquet
  trades      Trades around each Stage 2 touch (t0 - pre .. t0 + post minutes). Overlapping windows
              on the same day are merged and only uncovered spans are pulled, so nothing is bought twice.
              -> data/raw/es/trades/YYYYMMDD/<startUTC>_<endUTC>.parquet

Every pull is priced first; use --price-only to see the bill, then --approve-usd to authorize it.

  python -m src.ingest_futures bars --start 2023-05-01 --end 2025-12-31 --price-only
  python -m src.ingest_futures bars --start 2023-05-01 --end 2025-12-31 --approve-usd 10
  python -m src.ingest_futures roll-basis --approve-usd 1
  python -m src.ingest_futures trades --price-only
  python -m src.ingest_futures trades --approve-usd 40
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from src import calendar as calm
from src import spend, store
from src.config import data_path, load_config

log = logging.getLogger("ingest_futures")

BAR_COLS = ["ts_open_utc", "open", "high", "low", "close", "volume", "instrument_id"]
TRADE_COLS = ["ts_event_utc", "price", "size", "side", "instrument_id", "sequence"]


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------
def bar_args(cfg, start, end) -> dict:
    return dict(dataset=cfg["data"]["es_dataset"], symbols=cfg["data"]["es_symbol"],
                stype_in="continuous", schema="ohlcv-1m", start=str(start), end=str(end))


def clean_bars(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Keep 18:00 ET (prior day) .. 17:00 ET: drop the daily maintenance hour."""
    if df.empty:
        return pd.DataFrame(columns=BAR_COLS)
    df = df.reset_index()
    ts = pd.to_datetime(df["ts_event"], utc=True)
    out = pd.DataFrame({"ts_open_utc": ts, **{c: df[c] for c in ("open", "high", "low", "close")},
                        "volume": df["volume"].astype("int64"), "instrument_id": df["instrument_id"].astype("int64")})
    mod = calm.minutes_of_day_et(out["ts_open_utc"])
    halt = (mod >= calm.hhmm_to_min(cfg["market"]["globex_close"])) & (mod < calm.hhmm_to_min(cfg["market"]["globex_open"]))
    return out[~halt].sort_values("ts_open_utc").reset_index(drop=True)


def months(start, end) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    out = []
    for m in pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M"):
        out.append((str(m), m.start_time, (m + 1).start_time))
    return out


def ingest_bars(cfg, start, end, budget: spend.Budget, price_only: bool):
    cl = spend.client()
    todo = [(m, s, e) for m, s, e in months(start, end) if not store.bars_path(cfg, m).exists()]
    if price_only:
        total = sum(budget.price(cl, bar_args(cfg, s.date(), e.date())) for _, s, e in todo)
        print(f"ES bars: {len(todo)} months to pull -> ${total:.2f} (ledger ${spend.total_spent(cfg):.2f})")
        return
    for m, s, e in todo:
        if e > pd.Timestamp.now().normalize():
            log.warning("skipping %s: month not finished", m)
            continue
        data, cost = budget.pull(cl, "bars", m, bar_args(cfg, s.date(), e.date()))
        store.write(clean_bars(data.to_df(), cfg), store.bars_path(cfg, m))
        log.info("bars %s cost=$%.4f", m, cost)
    rebuild_calendar(cfg)


def rebuild_calendar(cfg):
    files = sorted(data_path(cfg, "raw", "es", "bars").glob("*.parquet"))
    if not files:
        return
    bars = pd.concat([store.read(f) for f in files], ignore_index=True).drop_duplicates("ts_open_utc")
    c = calm.build_calendar(bars, cfg)
    store.save_derived(c, "calendar", cfg)
    log.info("calendar: %d sessions, %d roll days, %d half days", len(c), c["roll"].sum(), c["half_day"].sum())


# ---------------------------------------------------------------------------
# Roll-day basis
# ---------------------------------------------------------------------------
def ingest_roll_basis(cfg, budget: spend.Budget, price_only: bool):
    cl = spend.client()
    cal = store.read(store.derived_path(cfg, "calendar"))
    cal["date"] = pd.to_datetime(cal["date"]).dt.date
    cal["prev_date"] = pd.to_datetime(cal["prev_date"]).dt.date
    rolls = cal[cal["roll"] & cal["prev_date"].notna()]
    total = 0.0
    for r in rolls.itertuples():
        p = store.roll_basis_path(cfg, r.date)
        if p.exists():
            continue
        close_hhmm = cfg["market"]["half_day_close"] if cal.loc[cal["date"] == r.prev_date, "half_day"].any() else cfg["market"]["rth_close"]
        end = calm.et_time(r.prev_date, close_hhmm)
        args = dict(dataset=cfg["data"]["es_dataset"], symbols=[int(r.instrument_id)], stype_in="instrument_id",
                    schema="ohlcv-1m", start=(end - pd.Timedelta(minutes=10)).isoformat(), end=end.isoformat())
        if price_only:
            total += budget.price(cl, args)
            continue
        data, _ = budget.pull(cl, "roll_basis", str(r.date), args)
        store.write(clean_bars(data.to_df(), cfg), p)
    if price_only:
        print(f"roll basis: {len(rolls)} roll days -> ${total:.2f}")


# ---------------------------------------------------------------------------
# Trades around touches
# ---------------------------------------------------------------------------
def merge_intervals(iv: list[tuple[pd.Timestamp, pd.Timestamp]]):
    iv = sorted(iv)
    out = []
    for s, e in iv:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def subtract_intervals(want, have):
    """Parts of `want` not covered by `have` (both lists of (start, end))."""
    out = []
    for s, e in want:
        cur = s
        for hs, he in sorted(have):
            if he <= cur or hs >= e:
                continue
            if hs > cur:
                out.append((cur, hs))
            cur = max(cur, he)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def _day_dir(cfg, day):
    return data_path(cfg, "raw", "es", "trades", store.ymd(day))


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y%m%dT%H%M%S")


def covered(cfg, day) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    d = _day_dir(cfg, day)
    out = []
    for f in d.glob("*.parquet") if d.exists() else []:
        s, e = f.stem.split("_")
        out.append((pd.Timestamp(s, tz="UTC"), pd.Timestamp(e, tz="UTC")))
    return merge_intervals(out)


def touch_windows(touches: pd.DataFrame, cfg) -> pd.DataFrame:
    pre = pd.Timedelta(minutes=cfg["data"]["trades_pre_min"])
    post = pd.Timedelta(minutes=cfg["data"]["trades_post_min"])
    t0 = pd.to_datetime(touches["t0"], utc=True)
    return pd.DataFrame({"date": touches["date"].values, "start": (t0 - pre).values, "end": (t0 + post).values})


def needed_spans(cfg, windows: pd.DataFrame):
    spans = []
    for day, g in windows.groupby("date"):
        want = merge_intervals(list(zip(pd.to_datetime(g["start"], utc=True), pd.to_datetime(g["end"], utc=True))))
        for s, e in subtract_intervals(want, covered(cfg, day)):
            spans.append((day, s, e))
    return spans


def clean_trades(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=TRADE_COLS)
    df = df.reset_index()
    side = df["side"].astype(str).map({"B": 1, "A": -1})  # N (unknown aggressor) -> NaN -> dropped
    out = pd.DataFrame({"ts_event_utc": pd.to_datetime(df["ts_event"], utc=True), "price": df["price"].astype(float),
                        "size": df["size"].astype("int64"), "side": side,
                        "instrument_id": df["instrument_id"].astype("int64"),
                        "sequence": df["sequence"].astype("int64") if "sequence" in df else np.arange(len(df))})
    out = out.dropna(subset=["side"])
    out["side"] = out["side"].astype("int8")
    return out.sort_values(["ts_event_utc", "sequence"], kind="stable").reset_index(drop=True)


def ingest_trades(cfg, budget: spend.Budget, price_only: bool, sample: int, holdout: bool = False):
    cl = spend.client()
    # Sealed: in-sample touches only, unless this is the final holdout run (env flag required).
    touches = store.load_derived("touches_holdout" if holdout else "touches", cfg, include_holdout=holdout)
    cal = store.load_calendar(cfg, include_holdout=holdout).set_index("date")
    spans = needed_spans(cfg, touch_windows(touches, cfg))

    def args(day, s, e):
        return dict(dataset=cfg["data"]["es_dataset"], symbols=[int(cal.loc[day, "instrument_id"])],
                    stype_in="instrument_id", schema="trades", start=s.isoformat(), end=e.isoformat())

    if price_only:
        idx = np.linspace(0, len(spans) - 1, min(sample, len(spans))).astype(int) if spans else []
        costs = [budget.price(cl, args(*spans[i])) for i in idx]
        mins = np.array([(e - s).total_seconds() / 60 for _, s, e in spans])
        per_min = sum(costs) / max(1e-9, mins[idx].sum()) if len(idx) else 0
        print(f"trades: {len(touches)} touches -> {len(spans)} spans, {mins.sum():.0f} minutes; "
              f"priced {len(idx)} spans -> est ${per_min * mins.sum():.2f} (ledger ${spend.total_spent(cfg):.2f})")
        return
    for day, s, e in spans:
        data, cost = budget.pull(cl, "trades", f"{day}_{_fmt(s)}", args(day, s, e))
        store.write(clean_trades(data.to_df()), _day_dir(cfg, day) / f"{_fmt(s)}_{_fmt(e)}.parquet")
        log.info("trades %s %s-%s cost=$%.4f run=$%.2f", day, s.time(), e.time(), cost, budget.spent)


def load_trades(cfg, day, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
    """Trades in [start, end) on session `day`. None if the span isn't fully downloaded."""
    if subtract_intervals([(start, end)], covered(cfg, day)):
        return None
    parts = []
    for f in sorted(_day_dir(cfg, day).glob("*.parquet")):
        s, e = (pd.Timestamp(x, tz="UTC") for x in f.stem.split("_"))
        if e > start and s < end:
            parts.append(store.read(f))
    t = pd.concat(parts, ignore_index=True).drop_duplicates(["ts_event_utc", "sequence", "price", "size"])
    t = t[(t["ts_event_utc"] >= start) & (t["ts_event_utc"] < end)]
    return t.sort_values(["ts_event_utc", "sequence"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", choices=["bars", "roll-basis", "trades", "calendar"])
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--schema", default="ohlcv-1m", help="kept for the CLAUDE.md command form")
    ap.add_argument("--price-only", action="store_true")
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--approve-usd", type=float, default=None)
    ap.add_argument("--allow-past-total", action="store_true")
    ap.add_argument("--holdout", action="store_true", help="trades for holdout touches (final run only)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    budget = spend.Budget(cfg, a.approve_usd, a.allow_past_total)
    if a.job == "bars":
        # Start two months early: the first session needs a prior day, a basis, and
        # baseline_sessions of history for the Stage 3 volume baseline.
        start = a.start or (pd.Timestamp(cfg["sample"]["start"]) - pd.offsets.MonthBegin(2)).date()
        ingest_bars(cfg, start, a.end or cfg["sample"]["end"], budget, a.price_only)
    elif a.job == "roll-basis":
        ingest_roll_basis(cfg, budget, a.price_only)
    elif a.job == "trades":
        ingest_trades(cfg, budget, a.price_only, a.sample, a.holdout)
    else:
        rebuild_calendar(cfg)


if __name__ == "__main__":
    main()
