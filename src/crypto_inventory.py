"""Study 14 step 0 (free, no account, no key): what Binance's public USD-M futures archive actually holds.

Lists data.binance.vision (an S3 bucket) and reports, per data type: how many symbols, the first and last month,
total size, and how many symbols are no longer trading (i.e. delisted coins are kept, so the study can avoid
survivorship bias). Types checked: monthly 1h and 1m klines, monthly fundingRate, daily metrics (open interest and
long/short ratios), daily liquidationSnapshot and bookDepth, monthly aggTrades (size only, sampled). Current
listings come from the public exchangeInfo endpoint; if it is unreachable from your network, that column is blank.

  python -m src.crypto_inventory                       # full inventory (a few minutes; ~1-2k listing requests)
  python -m src.crypto_inventory --sample 20           # quick check on 20 symbols per type
"""
from __future__ import annotations

import argparse
import logging
import re
import xml.etree.ElementTree as ET

log = logging.getLogger("crypto_inventory")
BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
# (label, prefix of the per-symbol folders, sub-folder under the symbol or "")
TYPES = [
    ("klines_1h", "data/futures/um/monthly/klines/", "1h/"),
    ("klines_1m", "data/futures/um/monthly/klines/", "1m/"),
    ("funding_rate", "data/futures/um/monthly/fundingRate/", ""),
    ("metrics_oi_longshort", "data/futures/um/daily/metrics/", ""),
    ("liquidation_snapshot", "data/futures/um/daily/liquidationSnapshot/", ""),
    ("book_depth", "data/futures/um/daily/bookDepth/", ""),
    ("agg_trades", "data/futures/um/monthly/aggTrades/", ""),
]
DATE = re.compile(r"(\d{4}-\d{2})(?:-\d{2})?\.zip$")


def _http_fetch(params: dict) -> str:
    import requests
    r = requests.get(BUCKET, params=params, timeout=60)
    r.raise_for_status()
    return r.text


def list_all(prefix: str, fetch=_http_fetch) -> tuple[list[str], list[tuple[str, int]]]:
    """(sub-folder names, [(key, size)]) under prefix, following S3 pagination."""
    folders, keys, marker = [], [], None
    while True:
        params = {"delimiter": "/", "prefix": prefix}
        if marker:
            params["marker"] = marker
        root = ET.fromstring(fetch(params))
        for p in root.findall("s3:CommonPrefixes/s3:Prefix", NS):
            folders.append(p.text[len(prefix):].strip("/") if p.text.startswith(prefix) else p.text)
        for c in root.findall("s3:Contents", NS):
            keys.append((c.find("s3:Key", NS).text, int(c.find("s3:Size", NS).text)))
        trunc = (root.findtext("s3:IsTruncated", default="false", namespaces=NS) or "").lower() == "true"
        if not trunc:
            break
        marker = root.findtext("s3:NextMarker", namespaces=NS) or (keys[-1][0] if keys else folders and prefix + folders[-1] + "/")
        if not marker:
            break
    return folders, keys


def file_range(keys) -> dict:
    zips = [(k, s) for k, s in keys if k.endswith(".zip")]
    dates = sorted(m.group(1) for k, _ in zips if (m := DATE.search(k)))
    return {"files": len(zips), "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "mb": round(sum(s for _, s in zips) / 1e6, 3)}


def summarize(per_symbol: dict, trading: set | None) -> dict:
    firsts = [v["first"] for v in per_symbol.values() if v.get("first")]
    lasts = [v["last"] for v in per_symbol.values() if v.get("last")]
    return {"symbols": len(per_symbol), "first": min(firsts) if firsts else None, "last": max(lasts) if lasts else None,
            "total_mb": round(sum(v.get("mb", 0) for v in per_symbol.values()), 1),
            "not_trading_now": None if trading is None else sum(1 for s in per_symbol if s not in trading)}


def trading_symbols() -> set | None:
    try:
        import requests
        r = requests.get(EXCHANGE_INFO, timeout=30)
        r.raise_for_status()
        return {s["symbol"] for s in r.json()["symbols"] if s.get("status") == "TRADING"}
    except Exception as e:                                        # geo-blocked or offline: report, don't fail
        log.warning("exchangeInfo unreachable (%s); delisted count left blank", e)
        return None


def inventory(sample: int | None = None, fetch=_http_fetch) -> dict:
    trading = trading_symbols()
    out = {"current_trading_symbols": None if trading is None else len(trading), "types": {}}
    for label, prefix, sub in TYPES:
        symbols, _ = list_all(prefix, fetch)
        symbols = [s for s in symbols if s]
        if sample:
            symbols = symbols[:sample]
        per = {}
        for i, s in enumerate(symbols):
            _, keys = list_all(f"{prefix}{s}/{sub}", fetch)
            per[s] = file_range(keys)
            if i and i % 100 == 0:
                log.info("%s: %d of %d symbols", label, i, len(symbols))
        out["types"][label] = summarize(per, trading)
        if sample:
            out["types"][label]["sampled"] = sample
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=None, help="only the first N symbols of each type")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from src.analysis import to_json
    print(to_json(inventory(a.sample)))


if __name__ == "__main__":
    main()
