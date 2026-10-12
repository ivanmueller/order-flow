"""Study 10b: quoting conditions (IWM from parity, trailing volatility and acceleration, recent sweeps, gamma regime)."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from src import study10b

DAY = dt.date(2024, 3, 5)


def ts(hms):
    return pd.Timestamp(f"{DAY} {hms}", tz="America/New_York").tz_convert("UTC")


def sym(exp, right, k):
    return f"IWM   {exp:%y%m%d}{right}{int(round(k * 1000)):08d}"


def chain_rows(t, exp, S, strikes, tv=0.5):
    """Call and put mids consistent with forward S (C - P = S - K), each with a 2-cent spread."""
    rows = []
    for k in strikes:
        c = max(S - k, 0) + tv
        p = c - (S - k)
        for right, mid in (("C", c), ("P", p)):
            rows.append({"ts": t, "symbol": sym(exp, right, k), "bid": mid - 0.01, "ask": mid + 0.01})
    return rows


def test_parity_forward_from_the_chain():
    exp1, exp0 = dt.date(2024, 3, 8), DAY                               # same-day expiry is skipped
    rows = chain_rows(ts("10:00"), exp1, 200.30, [195, 199, 200, 201, 205])
    rows += chain_rows(ts("10:00"), exp0, 150.0, [150])                 # a wrong same-day chain, ignored
    rows += chain_rows(ts("10:01"), exp1, 200.50, [199, 200, 201])
    f = study10b.parity_prices(pd.DataFrame(rows), DAY, n_strikes=3)
    assert f.loc[ts("10:00")] == pytest.approx(200.30) and f.loc[ts("10:01")] == pytest.approx(200.50)


def test_vol_and_acceleration_by_hand():
    idx = pd.date_range(ts("10:00"), periods=7, freq="min")
    px = pd.Series([100.0, 100.0, 100.0, 101.0, 101.0, 102.0, 100.0], index=idx)
    f = study10b.vol_features(px, window=4, half=2)
    r = np.log(px).diff()
    # at 10:06 the last 4 returns are r[3..6]; last 2 = r[5], r[6]; previous 2 = r[3], r[4]
    rv = math.sqrt((r.iloc[3:7] ** 2).sum()) * 1e4
    assert f.loc[idx[6], "rv"] == pytest.approx(rv)
    last, prev = math.sqrt((r.iloc[5:7] ** 2).sum()), math.sqrt((r.iloc[3:5] ** 2).sum())
    assert f.loc[idx[6], "accel"] == pytest.approx(last / prev)
    assert np.isnan(f.loc[idx[3], "rv"])                                 # needs a full window of returns
    flat = study10b.vol_features(pd.Series(100.0, index=idx), window=4, half=2)
    assert flat.loc[idx[6], "rv"] == 0 and np.isnan(flat.loc[idx[6], "accel"])   # 0 / 0 is not acceleration


def test_features_use_only_completed_minutes():
    idx = pd.date_range(ts("10:00"), periods=3, freq="min")
    feat = pd.DataFrame({"rv": [1.0, 2.0, 3.0], "accel": [1.0, 1.5, 2.0]}, index=idx)
    fills = pd.DataFrame({"ts": [ts("10:01:30"), ts("10:02:00")]})
    m = study10b.attach_minute_features(fills, feat)
    assert list(m["rv"]) == [2.0, 3.0]                                    # 10:01:30 uses the 10:01 snapshot


def prints(rows):
    return pd.DataFrame([{"ts": ts(t), "symbol": s, "price": 1.0, "size": 1, "bid": 1.0, "ask": 1.02, "venue": v}
                         for t, s, v in rows])


def test_sweep_flag():
    A, B = sym(dt.date(2024, 3, 8), "C", 200), sym(dt.date(2024, 3, 8), "P", 200)
    tr = prints([("10:00:00.000", A, 1), ("10:00:00.004", A, 2),        # A swept across 2 venues
                 ("10:05:00.000", B, 1), ("10:05:00.004", B, 1)])        # B: one venue only, not a sweep
    sw = study10b.sweep_times(tr, sweep_ms=10, min_venues=2)
    assert list(sw["symbol"]) == [A]
    fills = pd.DataFrame({"ts": [ts("10:00:10"), ts("10:00:40"), ts("10:00:10"), ts("10:05:10"), ts("10:00:00")],
                          "symbol": [A, A, B, B, A]})
    flag = study10b.recent_sweep(fills, sw, lookback_s=30)
    assert list(flag) == [True, False, False, False, False]              # the sweep itself is not "recent"


def test_gamma_day_grades_use_prior_days_only():
    days = [DAY + dt.timedelta(days=k) for k in range(6)]
    net = [1e9, -2e9, 3e9, 0.5e9, -4e9, -1e9]
    gx = pd.DataFrame({"date": days, "net_gex": net}).set_index("date")
    g = study10b.gamma_grades(gx, ratio=1.0, min_days=3, lookback=252)
    assert pd.isna(g.loc[days[2], "grade"])                         # only 2 prior days: no grade
    # day 3: prior |net| = 1, 2, 3 bn -> median 2 bn; 0.5 / 2 < 1 -> weak positive
    assert g.loc[days[3], "scale"] == pytest.approx(2e9) and g.loc[days[3], "grade"] == "weak positive"
    # day 4: prior 1, 2, 3, 0.5 -> median 1.5; -4 / 1.5 -> deep negative
    assert g.loc[days[4], "grade"] == "deep negative"
    # day 5: prior 1, 2, 3, 0.5, 4 -> median 2; -1 / 2 -> weak negative
    assert g.loc[days[5], "grade"] == "weak negative"


def test_live_gamma_side_and_distance():
    # net gamma positive at S0 = 5000, flip 4980, EM 40: positive above the flip, negative below
    row = {"net_gex": 1e9, "s0": 5000.0, "flip": 4980.0, "em": 40.0}
    spx = np.array([5030.0, 4985.0, 4975.0, 4900.0, np.nan])
    lab, dist = study10b.live_gamma(spx, row, near_em=0.25)
    assert list(lab) == ["deep positive", "near-flip positive", "near-flip negative", "deep negative", None]
    assert dist[0] == pytest.approx(50 / 40) and dist[3] == pytest.approx(-80 / 40)
    # net gamma negative at S0 and S0 below the flip: negative below, positive above
    row2 = {"net_gex": -1e9, "s0": 4950.0, "flip": 4980.0, "em": 40.0}
    lab2, _ = study10b.live_gamma(np.array([4940.0, 4985.0]), row2, near_em=0.25)
    assert list(lab2) == ["deep negative", "near-flip positive"]
    # no flip on the grid: the day's sign everywhere, deep
    lab3, d3 = study10b.live_gamma(np.array([5000.0]), {"net_gex": -1e9, "s0": 5000.0, "flip": np.nan, "em": 40.0}, 0.25)
    assert list(lab3) == ["deep negative"] and np.isnan(d3[0])


def test_spx_from_completed_es_bars():
    bars = pd.DataFrame({"ts_open_utc": [ts("10:00"), ts("10:01"), ts("10:02")], "close": [5010.0, 5012.0, 5011.0],
                         "instrument_id": 7})
    fills = pd.DataFrame({"ts": [ts("10:01:30"), ts("10:02:00"), ts("09:59:00")]})
    spx = study10b.spx_at_fills(fills, bars, basis=10.0)
    # 10:01:30: last bar closed by then is the 10:00 bar (closes 10:01) -> 5010 - 10
    assert spx[0] == pytest.approx(5000.0) and spx[1] == pytest.approx(5002.0) and np.isnan(spx[2])


def _F(n, cleared_every, rs_good, rs_bad, rv_high_bad=True):
    rows = []
    for i in range(n):
        d = dt.date(2024, 1, 2 + i % 5)
        high = i % 3 == 0
        bad = high if rv_high_bad else (i % cleared_every == 0)
        rows.append({"date": d, "spread_b": "0.02-0.05", "rs_5": rs_bad if bad else rs_good,
                     "rs_15": rs_bad if bad else rs_good, "cleared": bad, "rv": 10.0 if high else 1.0,
                     "accel": 1.0, "sweep_recent": False, "gamma_day": "weak positive",
                     "gamma_live": "near-flip positive"})
    return pd.DataFrame(rows)


def test_filter_comparison(cfg):
    F = _F(300, 3, 1.0, -2.0)
    cut = study10b.cutpoints(F, cfg)
    assert cut["rv"] == pytest.approx(F["rv"].quantile(0.6667))
    r = study10b.compare(F, F["rv"] < cut["rv"] + 1e-12, cfg)
    assert r["share_kept"] == pytest.approx(200 / 300)
    assert r["rs_5_kept"] == pytest.approx(1.0) and r["rs_5_all"] == pytest.approx(0.0)
    assert r["diff_rs_5"] == pytest.approx(1.0) and r["diff_ci_lo"] > 0
    assert r["cleared_kept"] == 0.0 and r["cleared_all"] == pytest.approx(1 / 3)


def test_explore_end_to_end_on_synthetic_raw_pieces(cfg, tmp_path):
    """Raw pieces shaped like Databento's tables -> study10 fills -> study10b features and comparison."""
    from src import store, study10
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    exp = dt.date(2024, 3, 8)
    rng = np.random.default_rng(5)
    minutes = pd.date_range(ts("09:30"), ts("15:59"), freq="min")
    S = 200 + np.cumsum(rng.normal(0, 0.05, len(minutes)))
    snaps, prints_ = [], []
    for i, (t, s) in enumerate(zip(minutes, S)):
        for r in chain_rows(t, exp, float(s), [199, 200, 201]):
            snaps.append({"ts_recv": r["ts"], "symbol": r["symbol"], "bid_px_00": r["bid"], "ask_px_00": r["ask"]})
        if i % 2 == 0:                                   # a print at the 200 call's bid every other minute
            k = sym(exp, "C", 200)
            row = [x for x in chain_rows(t, exp, float(s), [200]) if x["symbol"] == k][0]
            for j, v in enumerate((1, 2) if i % 10 == 0 else (1,)):   # every 10th is a two-venue sweep
                prints_.append({"ts_recv": t + pd.Timedelta(seconds=20, milliseconds=3 * j), "symbol": k,
                                "price": row["bid"], "size": 1, "bid_px_00": row["bid"], "ask_px_00": row["ask"],
                                "publisher_id": v})
    snaps, prints_ = pd.DataFrame(snaps), pd.DataFrame(prints_)
    for sc, raw in (("cbbo-1m", snaps), ("tcbbo", prints_)):
        for s0, e0 in study10.chunks(DAY, "09:30", "16:00", 30):
            part = raw[(raw["ts_recv"] >= s0) & (raw["ts_recv"] < e0)]
            store.write(part.reset_index(drop=True), study10.piece_path(c, sc, DAY, s0))
    F, ex = study10.session_fills(study10._read_pieces(c, "tcbbo", DAY), study10._read_pieces(c, "cbbo-1m", DAY), DAY, c)
    assert len(F) > 100
    gx = pd.DataFrame({"date": [DAY], "net_gex": [1e9]}).set_index("date")
    G, sanity = study10b.session_features(c, DAY, F, gx, grades=None, bars=None)
    assert sanity["iwm_minutes"] == len(minutes)
    assert abs(sanity["iwm_min"] - S.min()) < 1e-6 and abs(sanity["iwm_max"] - S.max()) < 1e-6
    assert sanity["sweeps"] == 39 and G["sweep_recent"].sum() == 0      # every 10th of 390 minutes; next fill 2 min later > 30 s
    # 09:30 is the first price, so the 30th return lands at 10:00: NaN before, a value from the 10:00 snapshot on
    assert G.loc[G["ts"] < ts("10:00"), "rv"].isna().all() and G.loc[G["ts"] >= ts("10:00"), "rv"].notna().all()
    assert G["gamma_day"].isna().all() and G["gamma_live"].isna().all()    # no history and no ES bars here
    G["spread_b"] = "0.02-0.05"
    out = study10b.explore(G, c, {str(DAY): sanity})
    assert set(out["candidates"]) == {"C1_skip_top_vol", "C2_skip_top_accel", "C3_skip_after_sweep",
                                      "C4_skip_deep_negative_live_gamma", "C5_skip_if_C1_C2_or_C3",
                                      "C6_skip_if_C1_to_C4"}
    assert out["candidates"]["C4_skip_deep_negative_live_gamma"]["share_kept"] == 1.0


# ---- confirmation: frozen hypotheses -----------------------------------------------------------
def test_h1_skips_the_first_half_hour(cfg):
    F = pd.DataFrame({"ts": [ts("09:30:05"), ts("09:59:59"), ts("10:00:00"), ts("15:00")]})
    assert list(study10b.h1_keep(F, cfg)) == [False, False, True, True]


def test_h2_keeps_near_flip_only():
    F = pd.DataFrame({"gamma_live": ["near-flip positive", "near-flip negative", "deep positive", None]})
    assert list(study10b.h2_keep(F)) == [True, True, False, False]


def _hyp_frame(n_sess=5, per=300):
    rows = []
    for d in range(n_sess):
        for i in range(per):
            good = i % 3 != 0
            rows.append({"date": dt.date(2024, 1, 2 + d), "spread_b": "0.02-0.05", "rs_5": 1.0 if good else -2.0,
                         "cleared": not good, "k": good})
    return pd.DataFrame(rows)


def test_hypothesis_verdicts(cfg):
    F = _hyp_frame()
    assert study10b.hypothesis_verdict(F, F["k"].to_numpy(), cfg)["verdict"] == "PASS"
    worse = F["k"].to_numpy() == False                                   # noqa: E712  keeps only the bad fills
    assert study10b.hypothesis_verdict(F, worse, cfg)["verdict"] == "FAIL"
    few = np.zeros(len(F), bool)
    few[:100] = True                                                    # 100 kept fills, one session
    assert study10b.hypothesis_verdict(F, few, cfg)["verdict"] == "NOT_TESTABLE"
