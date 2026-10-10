"""Study 14 data layer: Binance archive parsers, month lists, sealing, bar grid, downloader."""
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from src import crypto_data as cd

H = 3_600_000          # one hour in ms
T0_MS = 1_700_000_000_000 - (1_700_000_000_000 % H)


def test_klines_with_and_without_header_and_microseconds():
    no_header = f"{T0_MS},1.0,1.2,0.9,1.1,100,{T0_MS + H - 1},110.0,5,50,55,0\n" \
                f"{T0_MS + H},1.1,1.3,1.0,1.2,10,{T0_MS + 2 * H - 1},12.0,5,5,6,0\n"
    header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume," \
             "taker_buy_quote_volume,ignore\n" + no_header.replace(str(T0_MS), str(T0_MS * 1000), 1)
    a, b = cd.parse_klines(no_header), cd.parse_klines(header)
    for k in (a, b):
        assert list(k.columns) == ["ts", "open", "high", "low", "close", "qv"]
        assert k["ts"].iloc[0] == T0_MS * 1_000_000 and k["qv"].tolist() == [110.0, 12.0]
        assert k["ts"].dtype == np.int64


def test_funding_and_metrics():
    f = cd.parse_funding("calc_time,funding_interval_hours,last_funding_rate\n"
                         f"{T0_MS},8,0.0001\n{T0_MS + 8 * H},8,-0.0025\n")
    assert f["rate"].tolist() == [0.0001, -0.0025] and f["ts"].iloc[1] == (T0_MS + 8 * H) * 1_000_000
    m = cd.parse_metrics("create_time,symbol,sum_open_interest,sum_open_interest_value,"
                         "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,"
                         "sum_taker_long_short_vol_ratio\n2024-03-05 00:05:00,AAAUSDT,10,250.5,1.1,1.4,0.9,0.8\n")
    assert m.iloc[0]["oi_value"] == 250.5 and m.iloc[0]["top_ls"] == 1.4 and m.iloc[0]["taker_ratio"] == 0.8
    assert m.iloc[0]["ts"] == pd.Timestamp("2024-03-05 00:05", tz="UTC").value


def test_zip_reader_reads_the_csv_inside(tmp_path):
    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("x.csv", "calc_time,funding_interval_hours,last_funding_rate\n1700000000000,8,0.0002\n")
    assert cd.parse_funding(cd.read_zip_text(p))["rate"].tolist() == [0.0002]


def test_months_stop_before_the_holdout():
    assert cd.months("2025-08", "2025-12", stop="2025-10-01") == ["2025-08", "2025-09"]
    assert cd.months("2025-08", "2025-12", stop=None) == ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
    assert cd.kline_key("AAAUSDT", "5m", "2024-03") == \
        "data/futures/um/monthly/klines/AAAUSDT/5m/AAAUSDT-5m-2024-03.zip"
    assert cd.metrics_key("AAAUSDT", "2024-03-05") == \
        "data/futures/um/daily/metrics/AAAUSDT/AAAUSDT-metrics-2024-03-05.zip"


def test_seal_drops_holdout_rows_unless_unsealed(cfg, monkeypatch):
    hs = pd.Timestamp("2025-10-01", tz="UTC").value
    df = pd.DataFrame({"ts": [hs - 1, hs, hs + 1]})
    assert len(cd.seal(df, cfg)) == 1
    with pytest.raises(Exception):
        cd.seal(df, cfg, include_holdout=True)
    monkeypatch.setenv("GAMMA_EDGE_RUN_HOLDOUT", "1")
    assert len(cd.seal(df, cfg, include_holdout=True)) == 3


def test_grid_fills_gaps_flat_with_zero_volume():
    ns = H * 1_000_000
    df = pd.DataFrame({"ts": [0, ns, 3 * ns], "open": [1.0, 2.0, 4.0], "high": [1.5, 2.5, 4.5],
                       "low": [0.5, 1.5, 3.5], "close": [1.2, 2.2, 4.2], "qv": [5.0, 6.0, 7.0]})
    g = cd.grid(df, ns)
    assert g["ts"].tolist() == [0, ns, 2 * ns, 3 * ns] and g["real"].tolist() == [True, True, False, True]
    assert g.iloc[2][["open", "high", "low", "close", "qv"]].tolist() == [2.2, 2.2, 2.2, 2.2, 0.0]


def test_download_skips_existing_and_records_missing(tmp_path):
    calls = []

    def fetch(key):
        calls.append(key)
        if "missing" in key:
            return None                                   # 404
        return b"PK-bytes"
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "have.zip").write_bytes(b"x")
    r = cd.download(["a/have.zip", "a/new.zip", "a/missing.zip"], tmp_path, fetch=fetch, workers=2)
    assert r == {"requested": 3, "skipped": 1, "downloaded": 1, "missing": 1, "failed": 0}
    assert sorted(calls) == ["a/missing.zip", "a/new.zip"] and (tmp_path / "a" / "new.zip").read_bytes() == b"PK-bytes"
    calls.clear()
    r2 = cd.download(["a/have.zip", "a/new.zip", "a/missing.zip"], tmp_path, fetch=fetch, workers=2)
    assert calls == [] and r2["missing"] == 1                # a recorded 404 is not asked again


def test_symbols_keep_usdt_perpetuals_only(cfg):
    inv = {"klines_1h": {"AAAUSDT": {}, "BTCUSDT": {}, "BBBUSDT_230331": {}, "CCCBUSD": {}, "DDDUSDC": {}}}
    assert cd.study_symbols(inv, cfg) == ["AAAUSDT"]
