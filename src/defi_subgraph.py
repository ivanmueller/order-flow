"""Study 17 step 0b: do Uniswap fees beat the EXACT loss to arbitrage? (The Graph, free key; $0)

Fixes the three biases of step 0 (RUNLOG 2026-10-10):
- concentration: LVR is computed from the liquidity actually at the price, LVR_day = var_day x V_virtual / 8 with
  V_virtual = 2 x L x sqrt(P) in USD (v3); for v2 the virtual value is the reserves;
- survivorship: pools ranked by ALL-TIME volume, so pools that later died are included;
- sampling: daily variance = max(Parkinson high-low estimate, close-to-close squared return) when the day's range
  is usable (v3), close-to-close otherwise; the larger estimate keeps the cost conservative.
Rows on or after s14_holdout_start are never requested (date filter in the query) and never saved.

  python -m src.defi_subgraph --probe                 # checks the key and both subgraphs (3 small queries)
  python -m src.defi_subgraph --max-pools 50          # quick run
  python -m src.defi_subgraph                         # default: top 1,000 v3 pools and top 500 v2 pairs by volume
  python -m src.defi_subgraph --close-only            # same pools from the local cache, close-to-close variance
  python -m src.defi_subgraph --chain all --probe     # Study 18: check the Arbitrum and Base subgraphs
  python -m src.defi_subgraph --chain all             # Study 18: cache top 1,000 v3 pools per chain (free)
  python -m src.defi_subgraph --diagnose              # tails, robust stats, ex-ante look (weeks before 2025 only)
Needs THEGRAPH_API_KEY in .env. Subgraph ids are options (--v3-id / --v2-id) in case Uniswap moves them.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import data_path, load_config, load_env_file, param

log = logging.getLogger("defi_subgraph")
GATEWAY = "https://gateway.thegraph.com/api/{key}/subgraphs/id/{sid}"
V3_ID = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"     # Uniswap v3, Ethereum mainnet
V2_ID = "A3Np3RQbaBA6oKJgiwDJeo5T3zrYfGHPWFYayMwtNDum"     # Uniswap v2, Ethereum mainnet
Q96 = 2.0 ** 96
JUMP = 0.20
STABLES = {"USDC", "USDT", "DAI", "FRAX", "LUSD", "USDE", "PYUSD", "TUSD", "BUSD", "GUSD", "USDP", "SUSD", "CRVUSD",
           "GHO", "USDS", "FDUSD", "USD0", "RLUSD", "USDC.E", "MIM", "ALUSD", "DOLA"}


# ---- pure arithmetic (tested) -------------------------------------------------------------------------------
def is_stable_pair(s0: str, s1: str) -> bool:
    return str(s0).upper() in STABLES and str(s1).upper() in STABLES


def v3_virtual_usd(liquidity, sqrt_price_x96, dec1, t1_usd):
    """Full-range-equivalent value of the liquidity at the current price: 2 x L x sqrt(P) token1, in USD."""
    y = np.asarray(liquidity, float) * (np.asarray(sqrt_price_x96, float) / Q96) / 10.0 ** int(dec1)
    return 2.0 * y * np.asarray(t1_usd, float)


def daily_variance(df: pd.DataFrame, use_range: bool = True) -> np.ndarray:
    """max(Parkinson, close-to-close^2) when the day's high-low range is usable, close-to-close^2 otherwise;
    NaN on a day without a prior close. use_range=False: close-to-close only (diagnostic: intraday wicks such as
    sandwiches move the pool's high-low without costing the LP anything)."""
    c = df["close"].to_numpy(float)
    c2c = np.full(len(c), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ok = (c[1:] > 0) & (c[:-1] > 0)
        c2c[1:] = np.where(ok, np.log(c[1:] / c[:-1]) ** 2, np.nan)
        if use_range and "high" in df and "low" in df:
            h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
            valid = (l > 0) & (h >= l) & (h / l < 5) & (c >= l * 0.99) & (c <= h * 1.01)
            park = np.where(valid, np.log(h / l) ** 2 / (4 * math.log(2)), np.nan)
            out = np.where(np.isfinite(park), np.fmax(park, c2c), c2c)
        else:
            out = c2c
    return np.where(np.isfinite(c2c), out, np.nan)


def _jump(df) -> np.ndarray:
    c = df["close"].to_numpy(float)
    j = np.zeros(len(c), bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        j[1:] = np.abs(np.log(c[1:] / c[:-1])) > JUMP
    return j


def v3_daily(days: pd.DataFrame, dec1: int, t1_usd: pd.Series, fee_tier: int, use_range: bool = True) -> pd.DataFrame:
    d = days.sort_values("date").reset_index(drop=True)
    day = pd.to_datetime(d["date"].astype("int64"), unit="s")
    t1 = t1_usd.reindex(day).to_numpy(float)
    virt = v3_virtual_usd(d["liquidity"].astype(float), d["sqrtPrice"].astype(float), dec1, t1)
    var = daily_variance(d, use_range)
    fees = pd.to_numeric(d["feesUSD"], errors="coerce").to_numpy(float)
    fees = np.where(np.isfinite(fees), fees, pd.to_numeric(d["volumeUSD"], errors="coerce") * fee_tier / 1e6)
    tvl = pd.to_numeric(d["tvlUSD"], errors="coerce").to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        conc = virt / tvl
    return pd.DataFrame({"day": day.to_numpy(), "fees_usd": fees, "tvl_usd": tvl, "lvr_usd": var * virt / 8,
                         "var": var, "jump": _jump(d), "concentration": conc})


def v2_daily(days: pd.DataFrame, fee: float) -> pd.DataFrame:
    d = days.sort_values("date").reset_index(drop=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        d["close"] = d["reserve1"].astype(float) / d["reserve0"].astype(float)
    var = daily_variance(d[["close"]])
    tvl = d["reserveUSD"].astype(float).to_numpy()
    return pd.DataFrame({"day": pd.to_datetime(d["date"].astype("int64"), unit="s").to_numpy(),
                         "fees_usd": fee * d["dailyVolumeUSD"].astype(float).to_numpy(), "tvl_usd": tvl,
                         "lvr_usd": var * tvl / 8, "var": var, "jump": _jump(d), "concentration": 1.0})


def weekly(d: pd.DataFrame, holdout: pd.Timestamp, min_days: int = 5) -> pd.DataFrame:
    """Monday weeks wholly before the holdout: fees, LVR and net as shares of mean TVL; fee-implied and realized vol."""
    d = d[(d["day"] < holdout) & d["lvr_usd"].notna() & d["fees_usd"].notna() & (d["tvl_usd"] > 0)].copy()
    cols = ["week", "fees", "lvr", "net", "jump_days", "implied_vol", "realized_vol", "tvl", "concentration", "days"]
    if d.empty:
        return pd.DataFrame(columns=cols)
    d["week"] = d["day"] - pd.to_timedelta(d["day"].dt.weekday, unit="D")
    d = d[d["week"] + pd.Timedelta(days=7) <= holdout]
    d["virt"] = d["concentration"] * d["tvl_usd"]
    g = d.groupby("week")
    tvl, n = g["tvl_usd"].mean(), g.size()
    fees, lvr = g["fees_usd"].sum() / tvl, g["lvr_usd"].sum() / tvl
    with np.errstate(invalid="ignore", divide="ignore"):
        implied = np.sqrt(8 * (g["fees_usd"].sum() / n * 365) / g["virt"].mean())
    out = pd.DataFrame({"fees": fees, "lvr": lvr, "net": fees - lvr, "jump_days": g["jump"].sum().astype(int),
                        "implied_vol": implied, "realized_vol": np.sqrt(g["var"].mean() * 365), "tvl": tvl,
                        "concentration": g["concentration"].median(), "days": n})
    return out[out["days"] >= min_days].rename_axis("week").reset_index()[cols]


# ---- fetching ------------------------------------------------------------------------------------------------
class Graph:
    def __init__(self, key: str, sid: str, cache: Path, pause: float, offline: bool = False):
        self.url, self.cache, self.pause, self.sid = GATEWAY.format(key=key, sid=sid), cache, pause, sid
        self.offline = offline

    def q(self, name: str, query: str, variables: dict) -> dict:
        p = self.cache / f"{name}.json"
        if p.exists():
            return json.loads(p.read_text())
        if self.offline:
            raise RuntimeError(f"not in the local cache: {name}")
        import requests
        last = None
        for k in range(5):
            try:
                r = requests.post(self.url, json={"query": query, "variables": variables}, timeout=90)
                r.raise_for_status()
                js = r.json()
                if js.get("errors"):
                    raise RuntimeError(str(js["errors"])[:300])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(js["data"]))
                time.sleep(self.pause)
                return js["data"]
            except Exception as e:                                   # never log the URL: it holds the key
                last = e
                time.sleep(2 * 2 ** k)
        raise RuntimeError(f"subgraph {self.sid[:6]}...: {type(last).__name__}: {str(last)[:200]}")

    def paged(self, name: str, query: str, field: str, variables: dict, cursor: str, cap: int = 100) -> list:
        out, last = [], variables.get(cursor + "_gt", 0)
        for i in range(cap):
            rows = self.q(f"{name}_{i}", query, {**variables, cursor + "_gt": last})[field]
            out += rows
            if len(rows) < 1000:
                break
            last = rows[-1][cursor]
        return out


V3_POOLS = """query($skip:Int!){ pools(first:1000, skip:$skip, orderBy:volumeUSD, orderDirection:desc){
  id feeTier volumeUSD token0{id symbol decimals} token1{id symbol decimals} } }"""
V3_DAYS = """query($pool:String!, $date_gt:Int!, $end:Int!){ poolDayDatas(first:1000, orderBy:date, orderDirection:asc,
  where:{pool:$pool, date_gt:$date_gt, date_lt:$end}){ date liquidity sqrtPrice feesUSD volumeUSD tvlUSD high low close } }"""
TOKEN_DAYS = """query($token:String!, $date_gt:Int!, $end:Int!){ tokenDayDatas(first:1000, orderBy:date,
  orderDirection:asc, where:{token:$token, date_gt:$date_gt, date_lt:$end}){ date priceUSD } }"""
V2_PAIRS = """query($skip:Int!){ pairs(first:1000, skip:$skip, orderBy:volumeUSD, orderDirection:desc){
  id volumeUSD token0{symbol} token1{symbol} } }"""
V2_DAYS = """query($pair:String!, $date_gt:Int!, $end:Int!){ pairDayDatas(first:1000, orderBy:date, orderDirection:asc,
  where:{pairAddress:$pair, date_gt:$date_gt, date_lt:$end}){ date reserve0 reserve1 reserveUSD dailyVolumeUSD } }"""


def _key() -> str:
    load_env_file()
    k = os.environ.get("THEGRAPH_API_KEY", "").strip()
    if not k:
        raise SystemExit("THEGRAPH_API_KEY is not set in .env")
    return k


def probe(v3_id: str, v2_id: str, root: Path) -> dict:
    key = _key()
    out = {}
    for name, sid, q, field in (("v3", v3_id, V3_POOLS, "pools"), ("v2", v2_id, V2_PAIRS, "pairs")):
        try:
            g = Graph(key, sid, root / "probe" / str(int(time.time())), 0)
            rows = g.q(name, q.replace("first:1000", "first:3"), {"skip": 0})[field]
            out[name] = {"ok": True, "top": [f"{r['token0']['symbol']}-{r['token1']['symbol']}" for r in rows]}
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)[:300]}
    return out


CHAINS = {   # candidate subgraph ids per chain (The Graph Explorer); the probe keeps the first with the Uniswap v3 schema
    "arbitrum": ["FbCGRftH4a3yZugY7TnbYgPJVEv2LvMT6oF1fxPe9aJM", "FQ6JYszEKApsBpAmiHesRsd9Ygc6mzmpNRANeVQFYoVX"],
    "base": ["43Hwfi3dJSoGpyas9VwNoDAv55yjgGrPpNSmbQZArzMG", "HMuAwufqZ1YCRmzL2SfHTVkzZovC9VL2UAKhjvRqKiR1",
             "BEt2Ug4pbDaBuBxP9Y7qYk5zxk5MbCcDto6U2wD2uGQg", "FUbEPQw1oMghy39fwWBFY5fE6MXPXZQtjncQy2cXdrNS"]}
NEED = {"date", "liquidity", "sqrtPrice", "feesUSD", "tvlUSD", "high", "low", "close"}


def _chosen(root: Path, chain: str) -> Path:
    return root / chain / "subgraph_id.txt"


def probe_chains(chains: dict, root: Path) -> dict:
    """Per chain, try each candidate id: top pools by volume and one day row with every field Study 18 needs.
    The first id that passes is saved (raw/defi_subgraph/<chain>/subgraph_id.txt) and used by the fetch."""
    key, out = _key(), {}
    for name, ids in chains.items():
        out[name] = {}
        for sid in ids:
            try:
                g = Graph(key, sid, root / "probe" / f"{name}_{sid[:6]}_{int(time.time())}", 0)
                rows = g.q("pools", V3_POOLS.replace("first:1000", "first:3"), {"skip": 0})["pools"]
                day = g.q("day", V3_DAYS.replace("first:1000", "first:1"), {"pool": rows[0]["id"], "date_gt": 0,
                                                                             "end": 2_000_000_000})["poolDayDatas"]
                ok = bool(day) and NEED <= set(day[0])
                out[name][sid[:8]] = {"ok": ok, "top": [f"{r['token0']['symbol']}-{r['token1']['symbol']}" for r in rows],
                                      "first_day": str(pd.Timestamp(int(day[0]["date"]), unit="s").date()) if day else None}
            except Exception as e:
                out[name][sid[:8]] = {"ok": False, "error": str(e)[-160:]}
                continue
            if ok:
                f = _chosen(root, name)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(sid)
                out[name]["chosen"] = sid
                break
        out[name].setdefault("chosen", None)
    return out


def fetch_chain(cfg, chain: str, n: int, pause: float) -> dict:
    """Cache the top-n v3 pools by all-time volume and their daily rows (before the holdout) for one chain.
    Free (The Graph key); idempotent (cached queries are not repeated); stablecoin pairs skipped."""
    f = _chosen(data_path(cfg, "raw", "defi_subgraph"), chain)
    if not f.exists():
        raise SystemExit(f"no working subgraph id saved for {chain}: run --chain {chain} --probe first")
    sid = f.read_text().strip()
    key, root = _key(), data_path(cfg, "raw", "defi_subgraph", chain)
    end = int(pd.Timestamp(param(cfg, "s14_holdout_start"), tz="UTC").timestamp())
    g = Graph(key, sid, root / "v3", pause)
    pools = []
    for s in range(0, n, 1000):
        pools += g.q(f"pools_{s}", V3_POOLS, {"skip": s})["pools"]
    counts = {"pools": 0, "stable": 0, "fetched": 0, "errors": 0}
    for i, p in enumerate(pools[:n]):
        counts["pools"] += 1
        if is_stable_pair(p["token0"]["symbol"], p["token1"]["symbol"]):
            counts["stable"] += 1
            continue
        try:
            g.paged(f"days_{p['id']}", V3_DAYS, "poolDayDatas", {"pool": p["id"], "date_gt": 0, "end": end}, "date")
            counts["fetched"] += 1
        except Exception as e:
            counts["errors"] += 1
            log.warning("%s %s: %s", chain, p.get("id"), str(e)[:200])
        if i % 100 == 0:
            log.info("%s: %d of %d pools", chain, i, min(n, len(pools)))
    return {chain: counts}


def run(cfg, n_v3: int, n_v2: int, v3_id: str, v2_id: str, pause: float, min_median_tvl: float,
        close_only: bool = False) -> dict:
    """close_only=True: rebuild from the local cache only (no queries) with close-to-close variance; outputs get a
    _c2c suffix."""
    from src.defi_inventory import summarize, trailing_premium
    key, root = _key(), data_path(cfg, "raw", "defi_subgraph")
    holdout = pd.Timestamp(param(cfg, "s14_holdout_start"))
    end = int(pd.Timestamp(holdout, tz="UTC").timestamp())
    rows, skipped = [], {"stable": 0, "thin_or_bad": 0, "no_token_price": 0, "errors": 0}
    v3 = Graph(key, v3_id, root / "v3", pause, offline=close_only)
    pools = []
    for s in range(0, n_v3, 1000):
        pools += v3.q(f"pools_{s}", V3_POOLS, {"skip": s})["pools"]
    pools = pools[:n_v3]
    tok_cache = {}
    for i, p in enumerate(pools):
        try:
            s0, s1 = p["token0"]["symbol"], p["token1"]["symbol"]
            if is_stable_pair(s0, s1):
                skipped["stable"] += 1
                continue
            days = pd.DataFrame(v3.paged(f"days_{p['id']}", V3_DAYS, "poolDayDatas",
                                         {"pool": p["id"], "date_gt": 0, "end": end}, "date"))
            if len(days) < 30:
                skipped["thin_or_bad"] += 1
                continue
            t1 = p["token1"]["id"]
            if t1 not in tok_cache:
                td = pd.DataFrame(v3.paged(f"tok_{t1}", TOKEN_DAYS, "tokenDayDatas",
                                           {"token": t1, "date_gt": 0, "end": end}, "date"))
                tok_cache[t1] = (pd.Series(td["priceUSD"].astype(float).to_numpy(),
                                           index=pd.to_datetime(td["date"].astype("int64"), unit="s"))
                                 if len(td) else pd.Series(dtype=float))
            if tok_cache[t1].empty:
                skipped["no_token_price"] += 1
                continue
            d = v3_daily(days, int(p["token1"]["decimals"]), tok_cache[t1], int(p["feeTier"]), use_range=not close_only)
            d = d[(d["tvl_usd"] > 1000) & (d["fees_usd"] <= d["tvl_usd"])]          # spam / broken rows
            if d["tvl_usd"].median() < min_median_tvl:
                skipped["thin_or_bad"] += 1
                continue
            w = weekly(d, holdout)
            if len(w):
                w["trailing"] = trailing_premium(w)
                w["pool"], w["chain"], w["project"] = p["id"], "Ethereum", f"uniswap-v3 ({int(p['feeTier']) / 1e4:.2f}%)"
                w["symbol"] = f"{s0}-{s1}"
                rows.append(w)
        except Exception as e:
            skipped["errors"] += 1
            log.warning("v3 %s: %s", p.get("id"), str(e)[:200])
        if i % 50 == 0:
            log.info("v3: %d of %d pools", i, len(pools))
    if n_v2:
        v2 = Graph(key, v2_id, root / "v2", pause, offline=close_only)
        pairs = []
        for s in range(0, n_v2, 1000):
            pairs += v2.q(f"pairs_{s}", V2_PAIRS, {"skip": s})["pairs"]
        for i, p in enumerate(pairs[:n_v2]):
            try:
                s0, s1 = p["token0"]["symbol"], p["token1"]["symbol"]
                if is_stable_pair(s0, s1):
                    skipped["stable"] += 1
                    continue
                days = pd.DataFrame(v2.paged(f"days_{p['id']}", V2_DAYS, "pairDayDatas",
                                             {"pair": p["id"], "date_gt": 0, "end": end}, "date"))
                if len(days) < 30:
                    skipped["thin_or_bad"] += 1
                    continue
                d = v2_daily(days, 0.003)
                d = d[(d["tvl_usd"] > 1000) & (d["fees_usd"] <= d["tvl_usd"])]
                if d["tvl_usd"].median() < min_median_tvl:
                    skipped["thin_or_bad"] += 1
                    continue
                w = weekly(d, holdout)
                if len(w):
                    w["trailing"] = trailing_premium(w)
                    w["pool"], w["chain"], w["project"], w["symbol"] = p["id"], "Ethereum", "uniswap-v2", f"{s0}-{s1}"
                    rows.append(w)
            except Exception as e:
                skipped["errors"] += 1
                log.warning("v2 %s: %s", p.get("id"), str(e)[:200])
            if i % 50 == 0:
                log.info("v2: %d of %d pairs", i, min(n_v2, len(pairs)))
    W = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    out_dir = data_path(cfg, "derived", "defi")
    out_dir.mkdir(parents=True, exist_ok=True)
    if len(W):
        W.to_parquet(out_dir / f"study17_subgraph_pool_weeks{'_c2c' if close_only else ''}.parquet")
    res = {"study": 17, "step": "0b exact LVR (Uniswap subgraphs, dead pools included)", "skipped": skipped,
           "pools_with_usable_history": int(W["pool"].nunique()) if len(W) else 0,
           "first_week": str(W["week"].min().date()) if len(W) else None,
           "last_week": str(W["week"].max().date()) if len(W) else None, **summarize(W)}
    if len(W):
        res["median_concentration_v3"] = float(W.loc[W["project"].str.startswith("uniswap-v3"), "concentration"].median())
        res["by_realized_vol"] = {str(k): {"pool_weeks": int(len(x)), "share_fees_beat_lvr": round(float((x["net"] > 0).mean()), 3),
                                           "mean_net_annual": round(float(x["net"].mean() * 52), 3)}
                                  for k, x in W.groupby(pd.cut(W["realized_vol"], [0, 0.3, 0.6, 1.0, 2.0, np.inf]), observed=True)}
    res["note"] = ("Pool-level: fees vs LVR from liquidity at the price (v3) or reserves (v2), as shares of mean TVL. "
                   "Variance max(Parkinson, close-to-close). Ethereum mainnet only; no gas, hedge or selection yet.")
    (out_dir / f"study17_subgraph{'_c2c' if close_only else ''}.json").write_text(json.dumps(res, indent=1, default=str))
    return res


EX_ANTE_END = "2025-01-01"          # diagnose looks ex ante only before this; 2025-01..09 stays for validation
VOL_BINS = [0, 0.3, 0.6, 1.0, 2.0, np.inf]


def trailing_vol(W: pd.DataFrame, weeks: int = 4) -> pd.Series:
    """Mean realized vol of the pool's previous `weeks` rows (never the week itself)."""
    W = W.sort_values(["pool", "week"])
    return W.groupby("pool")["realized_vol"].transform(
        lambda x: x.shift(1).rolling(weeks, min_periods=weeks).mean()).reindex(W.index)


def robust(x: pd.Series) -> dict:
    """Weekly net (share of TVL) summarised so a few broken weeks cannot dominate: median, 1/99% winsorized mean."""
    x = pd.Series(x, dtype=float).dropna()
    if x.empty:
        return {"n": 0}
    lo, hi = x.quantile([0.01, 0.99])
    return {"n": int(len(x)), "share_positive": round(float((x > 0).mean()), 3),
            "median_annual": round(float(x.median() * 52), 4),
            "winsorized_mean_annual": round(float(x.clip(lo, hi).mean() * 52), 4),
            "mean_annual": round(float(x.mean() * 52), 3), "weeks_losing_over_100pct": int((x < -1).sum())}


def _by(W: pd.DataFrame, key) -> dict:
    return {str(k): robust(g["net"]) for k, g in W.groupby(key, observed=True)}


def diagnose(cfg) -> dict:
    """Reads the saved pool-weeks only (no queries): tails, robust stats, close-only variance, and an EX-ANTE look
    (trailing vol and trailing premium from earlier weeks) on weeks before EX_ANTE_END."""
    d = data_path(cfg, "derived", "defi")
    W = pd.read_parquet(d / "study17_subgraph_pool_weeks.parquet")
    W["week"] = pd.to_datetime(W["week"])
    W["tvol"] = trailing_vol(W)
    cols = ["symbol", "project", "week", "net", "fees", "lvr", "concentration", "realized_vol", "tvl", "jump_days"]
    fmt = lambda t: [{k: (str(v.date()) if isinstance(v, pd.Timestamp) else (round(float(v), 4) if isinstance(v, (float, np.floating)) else v))
                      for k, v in r.items()} for r in t[cols].to_dict("records")]
    v3 = W["project"].str.startswith("uniswap-v3")
    out = {"all": robust(W["net"]), "by_project": _by(W, "project"),
           "by_tvl": _by(W, pd.cut(W["tvl"], [0, 1e6, 1e7, 1e8, np.inf], labels=["<1M", "1-10M", "10-100M", ">100M"])),
           "v3_concentration_quantiles": {str(q): round(float(W.loc[v3, "concentration"].quantile(q)), 3)
                                          for q in (0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99)},
           "worst_weeks": fmt(W.nsmallest(15, "net")), "best_weeks": fmt(W.nlargest(10, "net"))}
    c = d / "study17_subgraph_pool_weeks_c2c.parquet"
    if c.exists():
        C = pd.read_parquet(c)
        out["close_only_variance"] = {"all": robust(C["net"]), "by_project": _by(C, "project")}
    E = W[W["week"] < pd.Timestamp(EX_ANTE_END)]
    calm, pos = E["tvol"] < 0.6, E["trailing"] > 0
    out["ex_ante_before_2025"] = {
        "note": "Buckets use only earlier weeks of the same pool (trailing 4-week realized vol, trailing 4-week net).",
        "by_trailing_vol": _by(E, pd.cut(E["tvol"], VOL_BINS)),
        "by_trailing_premium_sign": _by(E.dropna(subset=["trailing"]), np.where(E["trailing"].dropna() > 0, "positive", "negative")),
        "calm_and_positive": robust(E.loc[calm & pos, "net"]),
        "calm_and_positive_by_project": _by(E[calm & pos], "project"),
        "calm_and_positive_tvl_over_10M": robust(E.loc[calm & pos & (E["tvl"] > 1e7), "net"]),
        "ex_post_same_week_vol_for_contrast": _by(E, pd.cut(E["realized_vol"], VOL_BINS))}
    (d / "study17_subgraph_diagnose.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--chain", choices=["arbitrum", "base", "all"], help="Study 18: probe or fetch Uniswap v3 on an L2")
    ap.add_argument("--close-only", action="store_true", help="rebuild from the local cache, close-to-close variance")
    ap.add_argument("--diagnose", action="store_true", help="tails, robust stats, ex-ante look (saved weeks only)")
    ap.add_argument("--max-pools", type=int, default=1000, help="v3 pools by all-time volume")
    ap.add_argument("--max-pairs", type=int, default=500, help="v2 pairs by all-time volume (0 = skip v2)")
    ap.add_argument("--min-median-tvl", type=float, default=100_000)
    ap.add_argument("--v3-id", default=V3_ID)
    ap.add_argument("--v2-id", default=V2_ID)
    ap.add_argument("--pause", type=float, default=0.05)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    if a.chain:
        chains = CHAINS if a.chain == "all" else {a.chain: CHAINS[a.chain]}
        if a.probe:
            r = probe_chains(chains, data_path(cfg, "raw", "defi_subgraph"))
        else:
            r = {}
            for name in chains:
                r.update(fetch_chain(cfg, name, a.max_pools, a.pause))
    elif a.diagnose:
        r = diagnose(cfg)
    elif a.probe:
        r = probe(a.v3_id, a.v2_id, data_path(cfg, "raw", "defi_subgraph"))
    else:
        n2 = a.max_pairs if a.max_pools >= 1000 else min(a.max_pairs, a.max_pools)
        r = run(cfg, a.max_pools, n2, a.v3_id, a.v2_id, a.pause, a.min_median_tvl, close_only=a.close_only)
    print(json.dumps(r, indent=1, default=str))


if __name__ == "__main__":
    main()
