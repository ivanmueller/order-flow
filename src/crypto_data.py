"""Study 14 data: Binance USD-M futures public archive (data.binance.vision; free, no account, no key).

Downloads zips into data/raw/crypto/<type>/<SYMBOL>/ (idempotent: existing files are skipped, a 404 is recorded in
data/raw/crypto/_missing.txt and not asked again), parses them, and serves sealed per-coin tables. Holdout months
(on or after s14_holdout_start) are neither downloaded nor loaded unless --holdout is given with
GAMMA_EDGE_RUN_HOLDOUT=1 (said only on "run the holdout").

  python -m src.crypto_data --hourly            # 1-hour klines + funding, every USDT coin, every month (~0.7 GB)
  python -m src.crypto_data --event-data        # 5-min klines + daily metrics around events (after study14 --events)
  python -m src.crypto_data --minute            # 1-min klines for the frozen combinations' trades (after --discover)
Add --holdout (with GAMMA_EDGE_RUN_HOLDOUT=1) to fetch holdout months; --workers N parallel downloads (default 8).
Uses the step-0 inventory cache (data/raw/crypto/inventory_cache.json) for each coin's first and last month.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from src.calendar import HoldoutSealed, holdout_unsealed
from src.config import data_path, load_config, param

log = logging.getLogger("crypto_data")
BASE = "https://data.binance.vision/"
NS_PER_MS = 1_000_000


# ---- keys and months ------------------------------------------------------------------------------
def kline_key(sym: str, interval: str, month: str) -> str:
    return f"data/futures/um/monthly/klines/{sym}/{interval}/{sym}-{interval}-{month}.zip"


def funding_key(sym: str, month: str) -> str:
    return f"data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{month}.zip"


def metrics_key(sym: str, day: str) -> str:
    return f"data/futures/um/daily/metrics/{sym}/{sym}-metrics-{day}.zip"


def months(first: str, last: str, stop: str | None) -> list[str]:
    """Months first..last ('YYYY-MM'), keeping only months that end before `stop` (a date) when given."""
    out = [str(p) for p in pd.period_range(first, last, freq="M")]
    if stop is not None:
        s = pd.Period(pd.Timestamp(stop), freq="M")
        out = [m for m in out if pd.Period(m, freq="M") < s]
    return out


def study_symbols(inventory_cache: dict, cfg: dict) -> list[str]:
    """USDT-margined perpetuals in the archive (dated contracts, other quote currencies and exclusions dropped)."""
    quote, excl = param(cfg, "s14_quote"), set(param(cfg, "s14_exclude"))
    syms = inventory_cache.get("klines_1h", {})
    return sorted(s for s in syms if s.endswith(quote) and "_" not in s and s not in excl)


# ---- parsers --------------------------------------------------------------------------------------
def _to_ns(x) -> np.ndarray:
    """Binance times in ms (13 digits) or microseconds (16 digits, newer files) -> int64 ns."""
    a = np.asarray(x, dtype="int64")
    return np.where(a > 10**14, a * 1_000, a * NS_PER_MS).astype("int64")


def _csv(text: str, names: list[str]) -> pd.DataFrame:
    first = text.lstrip().split(",", 1)[0]
    has_header = not first.strip().lstrip("-").replace(".", "", 1).isdigit()
    return pd.read_csv(io.StringIO(text), header=0 if has_header else None, names=None if has_header else names)


KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
              "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def parse_klines(text: str) -> pd.DataFrame:
    d = _csv(text, KLINE_COLS)
    d.columns = KLINE_COLS[:len(d.columns)]
    out = pd.DataFrame({"ts": _to_ns(d["open_time"]), "open": d["open"].astype(float), "high": d["high"].astype(float),
                        "low": d["low"].astype(float), "close": d["close"].astype(float),
                        "qv": d["quote_volume"].astype(float)})
    return out


def parse_funding(text: str) -> pd.DataFrame:
    d = _csv(text, ["calc_time", "funding_interval_hours", "last_funding_rate"])
    return pd.DataFrame({"ts": _to_ns(d["calc_time"]), "rate": d["last_funding_rate"].astype(float)})


METRIC_COLS = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
               "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio", "count_long_short_ratio",
               "sum_taker_long_short_vol_ratio"]


def parse_metrics(text: str) -> pd.DataFrame:
    d = _csv(text, METRIC_COLS)
    t = d["create_time"]
    if pd.api.types.is_numeric_dtype(t):
        ts = _to_ns(t)
    else:
        ts = pd.to_datetime(t, utc=True).dt.tz_localize(None).to_numpy().astype("datetime64[ns]").astype("int64")
    return pd.DataFrame({"ts": np.asarray(ts, dtype="int64"),
                         "oi_value": pd.to_numeric(d["sum_open_interest_value"], errors="coerce"),
                         "top_ls": pd.to_numeric(d["sum_toptrader_long_short_ratio"], errors="coerce"),
                         "taker_ratio": pd.to_numeric(d["sum_taker_long_short_vol_ratio"], errors="coerce")})


def read_zip_text(path) -> str:
    with zipfile.ZipFile(path) as z:
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        return z.read(name).decode()


# ---- sealing and grid -----------------------------------------------------------------------------
def holdout_ns(cfg: dict) -> int:
    return pd.Timestamp(param(cfg, "s14_holdout_start"), tz="UTC").value


def seal(df: pd.DataFrame, cfg: dict, include_holdout: bool = False, col: str = "ts") -> pd.DataFrame:
    if include_holdout:
        if not holdout_unsealed():
            raise HoldoutSealed("Study 14 holdout requested without GAMMA_EDGE_RUN_HOLDOUT=1")
        return df
    return df[df[col].to_numpy() < holdout_ns(cfg)]


def grid(df: pd.DataFrame, step_ns: int) -> pd.DataFrame:
    """Complete bar grid first..last; a missing bar is flat at the previous close with zero volume (real=False)."""
    if df.empty:
        return df.assign(real=pd.Series(dtype=bool))
    d = df.sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")
    idx = np.arange(d.index[0], d.index[-1] + 1, step_ns, dtype="int64")
    g = d.reindex(idx)
    real = g["close"].notna().to_numpy()
    c = g["close"].ffill()
    for k in ("open", "high", "low"):
        g[k] = g[k].where(real, c)
    g["close"] = c
    g["qv"] = g["qv"].fillna(0.0)
    g["real"] = real
    return g.rename_axis("ts").reset_index()


# ---- downloads ------------------------------------------------------------------------------------
_local = threading.local()


def _http_fetch(key: str, attempts: int = 6, base_wait: float = 2.0) -> bytes | None:
    import time

    import requests
    for k in range(attempts):
        try:
            s = getattr(_local, "s", None) or requests.Session()
            _local.s = s
            r = s.get(BASE + key, timeout=120)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if (status is not None and status < 500 and status != 429) or k == attempts - 1:
                raise
            _local.s = None
            time.sleep(base_wait * 2 ** k)


def download(keys, root, fetch=_http_fetch, workers: int = 8) -> dict:
    """Fetch each key into root/<key's last two parts>; skip files on disk and keys recorded as missing."""
    root = Path(root)
    miss_path = root / "_missing.txt"
    missing = set(miss_path.read_text().split()) if miss_path.exists() else set()
    keys = list(dict.fromkeys(keys))
    todo, skipped = [], 0
    for k in keys:
        if local_path(root, k).exists() or k in missing:
            skipped += 1 if local_path(root, k).exists() else 0
            continue
        todo.append(k)
    counts = {"downloaded": 0, "missing": 0, "failed": 0}
    lock = threading.Lock()

    def one(k):
        try:
            b = fetch(k)
        except Exception as e:
            log.warning("%s: failed (%s)", k, type(e).__name__)
            with lock:
                counts["failed"] += 1
            return
        with lock:
            if b is None:
                counts["missing"] += 1
                missing.add(k)
                return
            p = local_path(root, k)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            tmp.write_bytes(b)
            tmp.replace(p)
            counts["downloaded"] += 1
            if counts["downloaded"] % 500 == 0:
                log.info("downloaded %d of %d", counts["downloaded"], len(todo))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, todo))
    root.mkdir(parents=True, exist_ok=True)
    miss_path.write_text("\n".join(sorted(missing)))
    recorded = sum(1 for k in keys if k in missing)
    return {"requested": len(keys), "skipped": skipped, "downloaded": counts["downloaded"], "missing": recorded,
            "failed": counts["failed"]}


def local_path(root, key: str) -> Path:
    """data/futures/um/monthly/klines/AAA/5m/AAA-5m-2024-03.zip -> root/klines_5m/AAA/AAA-5m-2024-03.zip"""
    parts = key.split("/")
    if not key.startswith("data/futures/um/"):
        return Path(root) / key
    name, sym = parts[-1], (parts[5] if len(parts) > 5 else "x")
    kind = parts[4] if len(parts) > 4 else parts[0]
    if kind == "klines":
        kind = f"klines_{parts[6]}"
    return Path(root) / kind / sym / name


# ---- loaders (parsed once, cached by file list) ---------------------------------------------------
def raw_root(cfg) -> Path:
    return data_path(cfg, "raw", "crypto")


def _load_kind(cfg, sym: str, kind: str, parser) -> pd.DataFrame:
    folder = raw_root(cfg) / kind / sym
    files = sorted(p.name for p in folder.glob("*.zip")) if folder.exists() else []
    cache = data_path(cfg, "derived", "crypto", "cache", kind, f"{sym}.parquet")
    stamp = cache.with_suffix(".files")
    if cache.exists() and stamp.exists() and json.loads(stamp.read_text()) == files:
        return pd.read_parquet(cache)
    parts = []
    for f in files:
        try:
            parts.append(parser(read_zip_text(folder / f)))
        except Exception as e:                                   # a corrupt zip: report, continue
            log.warning("%s/%s unreadable (%s)", kind, f, e)
    df = (pd.concat(parts, ignore_index=True).sort_values("ts").drop_duplicates("ts", keep="last")
          .reset_index(drop=True)) if parts else pd.DataFrame(columns=["ts"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    stamp.write_text(json.dumps(files))
    return df


def load_bars(cfg, sym: str, interval: str = "1h", include_holdout: bool = False) -> pd.DataFrame:
    step = {"1h": 3_600, "5m": 300, "1m": 60}[interval] * 10**9
    d = _load_kind(cfg, sym, f"klines_{interval}", parse_klines)
    if d.empty:
        return grid(pd.DataFrame(columns=["ts", "open", "high", "low", "close", "qv"]), step)
    return grid(seal(d, cfg, include_holdout).astype({"ts": "int64"}), step)


def load_funding(cfg, sym: str, include_holdout: bool = False) -> pd.DataFrame:
    d = _load_kind(cfg, sym, "fundingRate", parse_funding)
    return seal(d, cfg, include_holdout) if not d.empty else pd.DataFrame({"ts": np.array([], "int64"), "rate": []})


def load_metrics(cfg, sym: str, include_holdout: bool = False) -> pd.DataFrame:
    d = _load_kind(cfg, sym, "metrics", parse_metrics)
    if d.empty:
        return pd.DataFrame({"ts": np.array([], "int64"), "oi_value": [], "top_ls": [], "taker_ratio": []})
    return seal(d, cfg, include_holdout)


def inventory_cache(cfg) -> dict:
    p = raw_root(cfg) / "inventory_cache.json"
    if not p.exists():
        raise SystemExit(f"{p} not found: run `python -m src.crypto_inventory` first (step 0)")
    return json.loads(p.read_text())


def needs_path(cfg) -> Path:
    return data_path(cfg, "derived", "crypto", "study14_needs.json")


def _stop(cfg, holdout: bool):
    if holdout and not holdout_unsealed():
        raise HoldoutSealed("--holdout needs GAMMA_EDGE_RUN_HOLDOUT=1")
    return None if holdout else param(cfg, "s14_holdout_start")


def hourly_keys(cfg, holdout: bool = False) -> list[str]:
    inv, stop = inventory_cache(cfg), _stop(cfg, holdout)
    keys = []
    for s in study_symbols(inv, cfg):
        r = inv["klines_1h"][s]
        if r.get("first"):
            keys += [kline_key(s, "1h", m) for m in months(r["first"], r["last"], stop)]
        f = inv.get("funding_rate", {}).get(s, {})
        if f.get("first"):
            keys += [funding_key(s, m) for m in months(f["first"], f["last"], stop)]
    return keys


def needed_keys(cfg, which: str, holdout: bool = False) -> list[str]:
    stop = _stop(cfg, holdout)
    p = needs_path(cfg)
    if not p.exists():
        raise SystemExit(f"{p} not found: run `python -m src.study14 --events` (or --discover for --minute) first")
    need = json.loads(p.read_text())
    hs = pd.Timestamp(stop) if stop else None
    keys = []
    if which == "event":
        keys += [kline_key(s, "5m", m) for s, m in need.get("m5", [])
                 if hs is None or pd.Period(m, freq="M") < pd.Period(hs, freq="M")]
        keys += [metrics_key(s, d) for s, d in need.get("metrics", []) if hs is None or pd.Timestamp(d) < hs]
    else:
        keys += [kline_key(s, "1m", m) for s, m in need.get("m1", [])
                 if hs is None or pd.Period(m, freq="M") < pd.Period(hs, freq="M")]
    return keys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hourly", action="store_true")
    g.add_argument("--event-data", action="store_true")
    g.add_argument("--minute", action="store_true")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    if a.hourly:
        keys = hourly_keys(cfg, a.holdout)
    else:
        keys = needed_keys(cfg, "event" if a.event_data else "minute", a.holdout)
    log.info("%d files requested", len(keys))
    r = download(keys, raw_root(cfg), workers=a.workers)
    from src.analysis import to_json
    print(to_json(r))
    if r["failed"]:
        print("Some files failed after retries; rerun the same command to fetch only those.")


if __name__ == "__main__":
    main()
