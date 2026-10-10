"""Study 14 step 0: inventory of Binance's public futures archive (listing parser, pagination, summary)."""
from src import crypto_inventory as ci

PAGE1 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Name>data.binance.vision</Name><Prefix>data/futures/um/monthly/klines/</Prefix><IsTruncated>true</IsTruncated>
<NextMarker>data/futures/um/monthly/klines/BBB/</NextMarker>
<CommonPrefixes><Prefix>data/futures/um/monthly/klines/AAAUSDT/</Prefix></CommonPrefixes>
<CommonPrefixes><Prefix>data/futures/um/monthly/klines/BBBUSDT/</Prefix></CommonPrefixes>
</ListBucketResult>"""
PAGE2 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<IsTruncated>false</IsTruncated>
<CommonPrefixes><Prefix>data/futures/um/monthly/klines/CCCUSDT/</Prefix></CommonPrefixes>
</ListBucketResult>"""
FILES = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
<Contents><Key>data/futures/um/monthly/klines/AAAUSDT/1h/AAAUSDT-1h-2021-03.zip</Key><Size>1000</Size></Contents>
<Contents><Key>data/futures/um/monthly/klines/AAAUSDT/1h/AAAUSDT-1h-2021-03.zip.CHECKSUM</Key><Size>90</Size></Contents>
<Contents><Key>data/futures/um/monthly/klines/AAAUSDT/1h/AAAUSDT-1h-2023-11.zip</Key><Size>3000</Size></Contents>
</ListBucketResult>"""


def test_listing_follows_pagination():
    calls = []

    def fetch(params):
        calls.append(params)
        return PAGE1 if "marker" not in params else PAGE2
    pre, keys = ci.list_all("data/futures/um/monthly/klines/", fetch)
    assert pre == ["AAAUSDT", "BBBUSDT", "CCCUSDT"] and keys == []
    assert calls[1]["marker"] == "data/futures/um/monthly/klines/BBB/"


def test_file_range_and_size_ignore_checksums():
    _, keys = ci.list_all("x", lambda p: FILES)
    r = ci.file_range(keys)
    assert r == {"files": 2, "first": "2021-03", "last": "2023-11", "mb": 0.004}


def test_summary_counts_delisted():
    per_symbol = {"AAAUSDT": {"first": "2021-03", "last": "2023-11", "files": 2, "mb": 1.0},
                  "BBBUSDT": {"first": "2020-01", "last": "2025-12", "files": 72, "mb": 2.0}}
    s = ci.summarize(per_symbol, trading={"BBBUSDT"})
    assert s["symbols"] == 2 and s["not_trading_now"] == 1 and s["first"] == "2020-01" and s["last"] == "2025-12"
    assert s["total_mb"] == 3.0
    assert ci.summarize(per_symbol, trading=None)["not_trading_now"] is None    # exchange info unreachable


def test_retries_after_resets_then_succeeds():
    calls, waits = [], []

    def flaky(params):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError("reset")
        return PAGE2
    f = ci.with_retries(flaky, attempts=4, base_wait=2.0, sleep=waits.append)
    assert "CCCUSDT" in f({}) and waits == [2.0, 4.0]


def test_failed_symbol_is_recorded_and_run_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "TYPES", [("klines_1h", "p/", "1h/")])
    monkeypatch.setattr(ci, "trading_symbols", lambda: {"AAAUSDT"})
    top = """<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
    <CommonPrefixes><Prefix>p/AAAUSDT/</Prefix></CommonPrefixes><CommonPrefixes><Prefix>p/BBBUSDT/</Prefix></CommonPrefixes>
    </ListBucketResult>"""
    state = {"fail_b": True}

    def fetch(params):
        if params["prefix"] == "p/":
            return top
        if "BBB" in params["prefix"] and state["fail_b"]:
            raise ConnectionResetError("reset")
        return FILES
    cache = tmp_path / "cache.json"
    r = ci.inventory(fetch=fetch, cache_path=cache, pause=0)
    assert r["types"]["klines_1h"]["errors"] == ["BBBUSDT"] and r["types"]["klines_1h"]["symbols"] == 1
    state["fail_b"] = False
    calls = []

    def counting(params):
        calls.append(params["prefix"])
        return fetch(params)
    r2 = ci.inventory(fetch=counting, cache_path=cache, pause=0)
    assert r2["types"]["klines_1h"]["symbols"] == 2 and r2["types"]["klines_1h"]["not_trading_now"] == 1
    assert "p/AAAUSDT/1h/" not in calls                                  # resumed from the cache
