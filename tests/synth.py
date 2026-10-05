"""Synthetic raw data in the exact on-disk layout, for the end-to-end plumbing test.

Not a market model: random-walk ES, flat-vol option chains, trades sprinkled inside each 1-minute bar.
It proves every stage runs and joins correctly; it says nothing about edge.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import calendar as calm
from src import gex, store
from src.config import DEFAULT_PATH

ET = "America/New_York"


def write_config(root: Path, holdout_start: str) -> Path:
    with open(DEFAULT_PATH) as f:
        cfg = yaml.safe_load(f)
    cfg["data"]["root"] = str(root / "data")
    cfg["sample"].update(start="2024-01-02", holdout_start=holdout_start, end="2024-03-29")
    cfg["params"]["gex_pct_lookback"]["value"] = 10
    cfg["params"]["gex_pct_min_periods"]["value"] = 5
    cfg["params"]["baseline_sessions"]["value"] = 3
    cfg["params"]["bootstrap_draws"]["value"] = 200
    p = root / "config.yaml"
    with open(p, "w") as f:
        yaml.safe_dump(cfg, f)
    return p


def sessions(start="2024-01-02", n=45) -> list[dt.date]:
    return [d.date() for d in pd.bdate_range(start, periods=n)]


def make_es(days, rng, roll_at: int) -> pd.DataFrame:
    out, px = [], 5000.0
    for i, d in enumerate(days):
        inst = 1 if i < roll_at else 2
        prev = d - dt.timedelta(days=1)   # Monday opens Sunday 18:00 ET
        t0 = pd.Timestamp(f"{prev} 18:00", tz=ET).tz_convert("UTC")
        n = 23 * 60
        steps = rng.normal(0, 0.6, n)
        # Mean-reverting pull toward the day's open so levels get revisited.
        closes = np.empty(n)
        anchor = px
        for k in range(n):
            px = px + steps[k] - 0.02 * (px - anchor)
            closes[k] = round(px * 4) / 4
        opens = np.concatenate([[closes[0]], closes[:-1]])
        ts = t0 + pd.to_timedelta(np.arange(n), unit="min")
        out.append(pd.DataFrame({"ts_open_utc": ts, "open": opens, "high": np.maximum(opens, closes) + 0.25,
                                 "low": np.minimum(opens, closes) - 0.25, "close": closes,
                                 "volume": rng.integers(200, 2000, n), "instrument_id": inst}))
    return pd.concat(out, ignore_index=True)


def make_chain(quote_date, day, F, cfg, rng):
    rows, oi = [], []
    strikes = np.arange(round(F / 5) * 5 - 300, round(F / 5) * 5 + 305, 5.0)
    t_q = calm.et_time(quote_date, cfg["market"]["quote_time"])
    exps = [day + dt.timedelta(days=k) for k in (0, 1, 2, 7, 14, 30)]
    for exp in exps:
        if exp.weekday() >= 5:
            continue
        T = gex.year_frac(t_q, gex.expiry_ts("SPXW", exp, cfg), cfg)
        for K in strikes:
            vol = 0.14 + 0.25 * max(0.0, (F - K) / F)   # simple put skew
            for right, is_call in (("C", True), ("P", False)):
                pxo = float(gex.black76_price(F, K, T, vol, 0.999, is_call))
                half = max(0.05, 0.02 * pxo)
                rows.append({"quote_date": quote_date, "symbol": "SPXW", "expiration": exp, "strike": K,
                             "right": right, "bid": max(0.0, pxo - half), "ask": pxo + half, "close": pxo,
                             "volume": 1})
                base = 3000 if K % 50 == 0 else 300
                oi.append({"as_of_date": day, "symbol": "x", "root": "SPXW", "expiration": exp, "strike": K,
                           "right": right, "open_interest": int(base * rng.uniform(0.5, 1.5))})
    return pd.DataFrame(rows), pd.DataFrame(oi)


def trades_from_bars(bars: pd.DataFrame, rng) -> pd.DataFrame:
    """Four prints per bar (open, high, low, close) with random aggressor sides."""
    rows = []
    for b in bars.itertuples():
        for k, p in enumerate((b.open, b.high, b.low, b.close)):
            rows.append((b.ts_open_utc + pd.Timedelta(seconds=5 + 14 * k), p, int(rng.integers(1, 30)),
                         int(rng.choice([-1, 1])), b.instrument_id))
    t = pd.DataFrame(rows, columns=["ts_event_utc", "price", "size", "side", "instrument_id"])
    t["side"] = t["side"].astype("int8")
    t["sequence"] = np.arange(len(t))
    return t


def build(root: Path, holdout_start="2024-02-26", seed=0):
    rng = np.random.default_rng(seed)
    cfg_path = write_config(root, holdout_start)
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    days = sessions()
    bars = make_es(days, rng, roll_at=20)
    for m, g in bars.groupby(bars["ts_open_utc"].dt.strftime("%Y-%m")):
        store.write(g, store.bars_path(cfg, m))
    cal = calm.build_calendar(bars, cfg)
    store.write(cal, store.derived_path(cfg, "calendar"))
    bars["date"] = calm.session_date(bars["ts_open_utc"])
    basis = 10.0
    daily = []
    for d, g in bars.groupby("date"):
        mod = calm.minutes_of_day_et(g["ts_open_utc"])
        last = g[mod < 16 * 60].iloc[-1]["close"]
        daily.append({"date": d, "spx_close": last - basis, "vix_close": float(rng.uniform(12, 20))})
    daily = pd.DataFrame(daily)
    store.write(daily, store.daily_path(cfg))
    # Roll day: D's contract at D-1 close = same synthetic price
    roll_day = cal.loc[cal["roll"], "date"].iloc[0]
    prev = cal.loc[cal["roll"], "prev_date"].iloc[0]
    store.write(pd.DataFrame({"close": [float(daily.set_index("date").loc[prev, "spx_close"] + basis)]}),
                store.roll_basis_path(cfg, roll_day))
    spx = daily.set_index("date")["spx_close"]
    for r in cal.itertuples():
        if pd.isna(r.prev_date):
            continue
        q, oi = make_chain(r.prev_date, r.date, float(spx[r.prev_date]), cfg, rng)
        store.write(q, store.eod_path(cfg, r.prev_date, "SPXW"))
        store.write(oi, store.oi_path(cfg, r.date, "SPXW"))
    return cfg_path, bars


def write_trades_for_touches(cfg: dict, bars: pd.DataFrame, touches: pd.DataFrame, rng):
    pre = pd.Timedelta(minutes=cfg["data"]["trades_pre_min"])
    post = pd.Timedelta(minutes=cfg["data"]["trades_post_min"])
    for day, g in touches.groupby("date"):
        s = pd.to_datetime(g["t0"], utc=True).min() - pre
        e = pd.to_datetime(g["t0"], utc=True).max() + post
        b = bars[(bars["date"] == day) & (bars["ts_open_utc"] >= s - pd.Timedelta(minutes=1)) & (bars["ts_open_utc"] < e)]
        t = trades_from_bars(b, rng)
        t = t[(t["ts_event_utc"] >= s) & (t["ts_event_utc"] < e)]
        d = Path(cfg["data"]["root"]) / "raw" / "es" / "trades" / store.ymd(day)
        store.write(t, d / f"{s.strftime('%Y%m%dT%H%M%S')}_{e.strftime('%Y%m%dT%H%M%S')}.parquet")
