"""Study 17 step 0 (free, no key): do DEX liquidity fees ever beat the loss to arbitrage (LVR)?

From DefiLlama's free API: the yields pool list, each selected pool's daily history (TVL, base APY = trading-fee
yield) and both tokens' daily prices. Per pool-week: fees (sum of daily fee yield / 365), LVR (quadratic variation of
the pair's daily log price / 8 -- daily sampling understates intraday variance, so LVR here is a LOWER bound), net,
jump days (|daily move| > 20%), fee-implied vs realized volatility. Holdout rows (on or after s14_holdout_start) are
dropped before anything is saved. Requests are cached under data/raw/defi/ so a rerun resumes.

  python -m src.defi_inventory                    # all qualifying pools (TVL >= $250k)
  python -m src.defi_inventory --max-pools 50     # quick check
  python -m src.defi_inventory --min-tvl 1000000 --pause 0.2
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import data_path, load_config, param

log = logging.getLogger("defi_inventory")
POOLS_URLS = ["https://yields.llama.fi/pools", "https://api.llama.fi/pools"]
POOL_CHART_URLS = ["https://yields.llama.fi/chart/{pool}", "https://api.llama.fi/chart/{pool}"]
PRICE_CHART_URLS = ["https://coins.llama.fi/chart/{coins}", "https://api.llama.fi/chart/{coins}"]
JUMP = 0.20                                    # descriptive only: a daily pair move larger than this is a "jump day"


# ---- parsing --------------------------------------------------------------------------------------------------
def select_pools(pools_json: dict, min_tvl: float) -> list[dict]:
    """Two-token pools with impermanent-loss risk, not stablecoin pairs, TVL >= min_tvl."""
    out = []
    for p in pools_json.get("data", []):
        toks = p.get("underlyingTokens") or []
        if (p.get("exposure") == "multi" and p.get("ilRisk") == "yes" and not p.get("stablecoin")
                and (p.get("tvlUsd") or 0) >= min_tvl and len(toks) == 2 and all(toks)):
            chain = str(p.get("chain", "")).lower()
            out.append({**p, "coins": [f"{chain}:{t}" for t in toks]})
    return out


def parse_pool_chart(js: dict, holdout: pd.Timestamp) -> pd.DataFrame:
    d = pd.DataFrame(js.get("data", []))
    if d.empty:
        return pd.DataFrame(columns=["day", "tvl", "fee_yield"])
    ts = pd.to_datetime(d["timestamp"], utc=True)
    d = d[ts < holdout]
    ts = ts[ts < holdout]
    return pd.DataFrame({"day": ts.dt.tz_localize(None).dt.normalize().to_numpy(),
                         "tvl": pd.to_numeric(d.get("tvlUsd"), errors="coerce").to_numpy(),
                         "fee_yield": pd.to_numeric(d.get("apyBase"), errors="coerce").to_numpy() / 100.0}
                        ).drop_duplicates("day", keep="last").reset_index(drop=True)


def parse_price_chart(js: dict, coin: str, holdout: pd.Timestamp) -> pd.Series:
    pts = js.get("coins", {}).get(coin, {}).get("prices", [])
    if not pts:
        return pd.Series(dtype=float)
    d = pd.DataFrame(pts)
    ts = pd.to_datetime(d["timestamp"], unit="s", utc=True)
    keep = ts < holdout
    s = pd.Series(d["price"].to_numpy(float)[keep], index=ts[keep].dt.tz_localize(None).dt.normalize().to_numpy())
    return s[~s.index.duplicated(keep="last")].sort_index()


# ---- arithmetic -----------------------------------------------------------------------------------------------
def weekly_lp(pool: pd.DataFrame, p0: pd.Series, p1: pd.Series, min_days: int = 5) -> pd.DataFrame:
    """Per Monday-start week: fees, LVR lower bound, net, jump days, implied and realized volatility."""
    pair = (p0 / p1).replace([np.inf, -np.inf], np.nan).dropna()
    pair = pair[pair > 0]
    r = np.log(pair).diff()
    d = pool.set_index("day").join(r.rename("r"), how="inner")
    d = d[d["fee_yield"].notna() & d["r"].notna()] if len(d) else d
    if d.empty:
        return pd.DataFrame(columns=["week", "fees", "lvr", "net", "jump_days", "implied_vol", "realized_vol", "tvl", "days"])
    wk = d.index - pd.to_timedelta(d.index.weekday, unit="D")
    g = d.groupby(wk)
    out = pd.DataFrame({"fees": g["fee_yield"].sum() / 365, "lvr": g["r"].apply(lambda x: float((x ** 2).sum())) / 8,
                        "jump_days": g["r"].apply(lambda x: int((x.abs() > JUMP).sum())),
                        "implied_vol": np.sqrt(8 * g["fee_yield"].mean().clip(lower=0)),
                        "realized_vol": np.sqrt(g["r"].apply(lambda x: float((x ** 2).mean())) * 365),
                        "tvl": g["tvl"].median(), "days": g.size()})
    out["net"] = out["fees"] - out["lvr"]
    out = out[out["days"] >= min_days].rename_axis("week").reset_index()
    return out[["week", "fees", "lvr", "net", "jump_days", "implied_vol", "realized_vol", "tvl", "days"]]


def trailing_premium(w: pd.DataFrame, weeks: int = 4) -> pd.Series:
    """Mean net over the previous `weeks` weeks (strictly before each week)."""
    return w["net"].rolling(weeks, min_periods=weeks).mean().shift(1)


def summarize(W: pd.DataFrame) -> dict:
    if W.empty:
        return {"pool_weeks": 0}
    W = W.copy()
    W["tvl_bucket"] = pd.cut(W["tvl"], [0, 1e6, 1e7, 1e8, np.inf], labels=["<1M", "1-10M", "10-100M", ">100M"])

    def block(x):
        ann = 52
        return {"pools": int(x["pool"].nunique()), "pool_weeks": int(len(x)),
                "share_weeks_fees_beat_lvr": round(float((x["net"] > 0).mean()), 3),
                "median_fee_yield_annual": round(float((x["fees"] * ann).median()), 3),
                "median_lvr_annual": round(float((x["lvr"] * ann).median()), 3),
                "mean_net_annual": round(float(x["net"].mean() * ann), 3),
                "median_net_annual": round(float(x["net"].median() * ann), 3),
                "median_implied_vol": round(float(x["implied_vol"].median()), 3),
                "median_realized_vol": round(float(x["realized_vol"].median()), 3),
                "share_weeks_with_jump": round(float((x["jump_days"] > 0).mean()), 3)}
    out = {"all": block(W),
           "by_tvl": {str(k): block(x) for k, x in W.groupby("tvl_bucket", observed=True)},
           "by_chain": {k: block(x) for k, x in W.groupby("chain") if x["pool"].nunique() >= 5},
           "by_project": {k: block(x) for k, x in W.groupby("project") if x["pool"].nunique() >= 5},
           "by_year": {str(k): block(x) for k, x in W.groupby(W["week"].dt.year)}}
    # first look at signal: does last month's premium predict next week's?
    t = W.dropna(subset=["trailing"])
    if len(t) > 100:
        hi = t["trailing"] > 0
        out["signal_check"] = {"next_week_net_annual_if_trailing_positive": round(float(t.loc[hi, "net"].mean() * 52), 3),
                               "next_week_net_annual_if_trailing_negative": round(float(t.loc[~hi, "net"].mean() * 52), 3),
                               "share_trailing_positive": round(float(hi.mean()), 3),
                               "rank_correlation": round(float(t["trailing"].corr(t["net"], method="spearman")), 3)}
    out["note"] = ("LVR from daily prices is a LOWER bound on the true cost (intraday variance is missed); fees are "
                   "DefiLlama's base APY for the pool. Descriptive only: no gas, no hedge, no selection out of sample.")
    return out


# ---- fetching -------------------------------------------------------------------------------------------------
def _get(urls, cache: Path, pause: float, params=None):
    if cache.exists():
        return json.loads(cache.read_text())
    import requests
    last = None
    for u in urls:
        for k in range(4):
            try:
                r = requests.get(u, params=params, timeout=60)
                if r.status_code == 404:
                    break
                r.raise_for_status()
                js = r.json()
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(js))
                time.sleep(pause)
                return js
            except Exception as e:                                         # rate limits, resets: back off
                last = e
                time.sleep(2 * 2 ** k)
    raise RuntimeError(f"all endpoints failed ({last})")


def _prices(coin: str, start: pd.Timestamp, end: pd.Timestamp, root: Path, pause: float, holdout) -> pd.Series:
    parts, t = [], start
    while t < end:
        span = 300
        js = _get([u.format(coins=coin) for u in PRICE_CHART_URLS],
                  root / "prices" / f"{coin.replace(':', '_')}_{t.date()}.json", pause,
                  params={"start": int(t.timestamp()), "span": span, "period": "1d"})
        parts.append(parse_price_chart(js, coin, holdout))
        t += pd.Timedelta(days=span)
    s = pd.concat(parts) if parts else pd.Series(dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def inventory(cfg, min_tvl: float, max_pools: int | None, pause: float) -> dict:
    holdout = pd.Timestamp(param(cfg, "s14_holdout_start"), tz="UTC")
    root = data_path(cfg, "raw", "defi")
    pools = select_pools(_get(POOLS_URLS, root / "pools.json", pause), min_tvl)
    pools.sort(key=lambda p: -(p.get("tvlUsd") or 0))
    if max_pools:
        pools = pools[:max_pools]
    log.info("%d pools selected (TVL >= %.0f)", len(pools), min_tvl)
    rows, errors = [], []
    for i, p in enumerate(pools):
        try:
            pc = parse_pool_chart(_get([u.format(pool=p["pool"]) for u in POOL_CHART_URLS],
                                       root / "pool_charts" / f"{p['pool']}.json", pause), holdout)
            if len(pc) < 30:
                continue
            start = pd.Timestamp(pc["day"].min(), tz="UTC") - pd.Timedelta(days=2)
            end = min(pd.Timestamp(pc["day"].max(), tz="UTC") + pd.Timedelta(days=1), holdout)
            p0 = _prices(p["coins"][0], start, end, root, pause, holdout)
            p1 = _prices(p["coins"][1], start, end, root, pause, holdout)
            if len(p0) < 30 or len(p1) < 30:
                continue
            w = weekly_lp(pc, p0, p1)
            if w.empty:
                continue
            w["trailing"] = trailing_premium(w)
            w["pool"], w["chain"], w["project"], w["symbol"] = p["pool"], p.get("chain"), p.get("project"), p.get("symbol")
            rows.append(w)
        except Exception as e:
            errors.append(p["pool"])
            log.warning("%s: %s", p.get("symbol"), e)
        if i % 50 == 0:
            log.info("%d of %d pools", i, len(pools))
    W = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    out_dir = data_path(cfg, "derived", "defi")
    out_dir.mkdir(parents=True, exist_ok=True)
    if len(W):
        W.to_parquet(out_dir / "study17_pool_weeks.parquet")
    res = {"study": 17, "step": "0 inventory (free, descriptive)", "pools_selected": len(pools),
           "pools_with_usable_history": int(W["pool"].nunique()) if len(W) else 0, "errors": len(errors),
           "first_week": str(W["week"].min().date()) if len(W) else None,
           "last_week": str(W["week"].max().date()) if len(W) else None, **summarize(W)}
    (out_dir / "study17_inventory.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-tvl", type=float, default=250_000, help="pool TVL floor today (USD)")
    ap.add_argument("--max-pools", type=int, default=None)
    ap.add_argument("--pause", type=float, default=0.1, help="seconds between requests")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(inventory(load_config(), a.min_tvl, a.max_pools, a.pause), indent=1, default=str))


if __name__ == "__main__":
    main()
