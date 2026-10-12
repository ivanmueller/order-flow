"""Study 11b: hedged market making with an exit that follows the far side of the quote. Hand-worked answers."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src import study11b

DAY = dt.date(2024, 3, 5)
C = "IWM   240315C00200000"
P = "IWM   240315P00200000"


def ts(hms):
    return pd.Timestamp(f"{DAY} {hms}", tz="America/New_York").tz_convert("UTC")


CLOSE = ts("16:00")


def trades(rows):
    """(time, symbol, price, size, bid, ask, bid_sz, ask_sz)"""
    return pd.DataFrame([{"ts": ts(t), "symbol": s, "price": p, "size": q, "bid": b, "ask": a, "bid_sz": bz,
                          "ask_sz": az} for t, s, p, q, b, a, bz, az in rows])


def quotes(rows):
    return pd.DataFrame([{"ts": ts(t), "symbol": s, "bid": b, "ask": a, "bid_sz": bz, "ask_sz": az}
                         for t, s, b, a, bz, az in rows])


def _case():
    E = pd.DataFrame([
        {"ts": ts("10:00:00"), "ts_last": ts("10:00:00"), "symbol": C, "s": 1, "price": 1.00, "bid": 1.00,
         "ask": 1.04, "bid_sz": 7.0, "ask_sz": 5.0},
        {"ts": ts("10:00:00"), "ts_last": ts("10:00:00"), "symbol": P, "s": -1, "price": 2.00, "bid": 1.96,
         "ask": 2.00, "bid_sz": 2.0, "ask_sz": 9.0}])
    tr = trades([("10:00:00", C, 1.00, 1, 1.00, 1.04, 7, 5),       # the entry print (not an exit)
                 ("10:00:10", C, 1.04, 3, 1.00, 1.04, 7, 8),       # at our 1.04: front fills; 3 of 5 ahead
                 ("10:00:30", C, 1.06, 4, 1.02, 1.06, 7, 4),       # after the re-peg to 1.06: 4 of 4 ahead
                 ("10:00:40", C, 1.06, 1, 1.02, 1.06, 7, 4),       # 5 > 4: back of the queue fills
                 ("10:00:50", C, 1.08, 1, 1.02, 1.06, 7, 4)])      # one tick through 1.06: conservative fills
    q = quotes([("10:00:05", C, 1.00, 1.04, 7, 8),                  # same price: no re-peg, still 5 ahead
                ("10:00:20", C, 1.02, 1.06, 7, 4),                  # far side moves: re-peg to 1.06, 4 ahead
                ("10:10:00", P, 1.90, 1.94, 3, 3)])                 # short: bid falls, re-peg down to 1.90
    return E, study11b.session_events(tr, q)


def test_repegged_exit_three_rules():
    E, ev = _case()
    R = study11b.simulate_entries(E, ev, max_hold_min=30, tick=0.01, slip_ticks=1, close=CLOSE, mult=100)
    long = R.loc[0]
    assert long["option_front"] == pytest.approx(4.0) and long["exit_ts_front"] == ts("10:00:10")
    assert long["repegs_front"] == 0
    assert long["option_queue"] == pytest.approx(6.0) and long["exit_ts_queue"] == ts("10:00:40")
    assert long["repegs_queue"] == 1
    assert long["option_through"] == pytest.approx(6.0) and long["exit_ts_through"] == ts("10:00:50")
    assert long["passive_through"] and long["hold_through"] == pytest.approx(50 / 60)


def test_no_fill_falls_back_after_max_hold():
    E, ev = _case()
    R = study11b.simulate_entries(E, ev, max_hold_min=30, tick=0.01, slip_ticks=1, close=CLOSE, mult=100)
    short = R.loc[1]
    # never filled at 1.96 or 1.90; at 10:30 buys at the newest ask 1.94 + 1 tick: -1 x (1.95 - 2.00) x 100 = +5
    for rule in ("through", "queue", "front"):
        assert short[f"option_{rule}"] == pytest.approx(5.0) and not short[f"passive_{rule}"]
        assert short[f"exit_ts_{rule}"] == ts("10:30:00") and short[f"repegs_{rule}"] == 1


def test_max_hold_and_close_clip():
    E, ev = _case()
    R = study11b.simulate_entries(E, ev, max_hold_min=0.5, tick=0.01, slip_ticks=1, close=CLOSE, mult=100)
    # 30 s: by 10:00:30 only the front fill (10:00:10) happened; queue and through fall back at the newest bid
    # 1.02 (the 10:00:30 print's quote) - 1 tick = 1.01 -> +1
    assert R.loc[0, "option_front"] == pytest.approx(4.0)
    assert R.loc[0, "option_queue"] == pytest.approx(1.0) and R.loc[0, "option_through"] == pytest.approx(1.0)


def test_hedge_at_iwm_bid_and_ask_after_latency():
    E = pd.DataFrame({"ts_last": [ts("10:00:00")], "s": [1]})
    iwm = pd.DataFrame({"ts": [ts("10:00:00"), ts("10:00:01"), ts("10:00:41")],
                        "bid": [200.00, 199.98, 200.10], "ask": [200.01, 199.99, 200.11]})
    h = study11b.hedge_pnl(E, np.array([0.5]), pd.Series([ts("10:00:40")]), iwm, latency_s=1, fee=0.0)
    # long call: sell 50 at the 10:00:01 bid 199.98, buy back at the 10:00:41 ask 200.11: -50 x 0.13 = -6.5
    assert h[0] == pytest.approx(-6.5)
    h2 = study11b.hedge_pnl(E.assign(s=-1), np.array([0.5]), pd.Series([ts("10:00:40")]), iwm, latency_s=1, fee=0.01)
    # short call: buy 50 at ask 199.99, sell at bid 200.10: +50 x 0.11 = 5.5, minus 0.01 x 50 x 2 = 1.0 -> 4.5
    assert h2[0] == pytest.approx(4.5)


def test_orders_per_day():
    R = pd.DataFrame({"repegs_queue": [0, 2, 4]})
    # entry + first exit placement + re-pegs: (2 + 4 + 6) / 3 = 4 option orders a round trip
    assert study11b.orders_per_round_trip(R, "queue") == pytest.approx(4.0)


def test_end_to_end_on_synthetic_pieces(cfg, tmp_path):
    """Study 11 entries -> Study 11b trips with a 1-second IWM file; the hedge offsets the option's IWM exposure."""
    from src import store, study10, study11
    from tests.test_study11 import chain_rows
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    c["params"] = {**cfg["params"], "bootstrap_draws": {"value": 200}}
    exp = dt.date(2024, 3, 8)
    rng = np.random.default_rng(5)
    minutes = pd.date_range(ts("09:30"), ts("15:59"), freq="min")
    S = 200 + np.cumsum(rng.normal(0, 0.05, len(minutes)))
    k = f"IWM   {exp:%y%m%d}C00200000"
    snaps, prints_ = [], []
    for i, (t, s) in enumerate(zip(minutes, S)):
        for r in chain_rows(t, exp, float(s), [199, 200, 201]):
            snaps.append({"ts_recv": r["ts"], "symbol": r["symbol"], "bid_px_00": r["bid"], "ask_px_00": r["ask"],
                          "bid_sz_00": 5, "ask_sz_00": 5})
        if i % 2 == 0:
            row = [x for x in chain_rows(t, exp, float(s), [200]) if x["symbol"] == k][0]
            q = {"bid_px_00": row["bid"], "ask_px_00": row["ask"], "bid_sz_00": 5, "ask_sz_00": 5, "symbol": k}
            prints_.append({**q, "ts_recv": t + pd.Timedelta(seconds=20), "price": row["bid"], "size": 1,
                            "publisher_id": 21})
            prints_.append({**q, "ts_recv": t + pd.Timedelta(seconds=40), "price": row["ask"], "size": 10,
                            "publisher_id": 24})
    for sc, raw in (("cbbo-1m", pd.DataFrame(snaps)), ("tcbbo", pd.DataFrame(prints_))):
        for s0, e0 in study10.chunks(DAY, "09:30", "16:00", 30):
            part = raw[(raw["ts_recv"] >= s0) & (raw["ts_recv"] < e0)]
            store.write(part.reset_index(drop=True), study10.piece_path(c, sc, DAY, s0))
    secs = pd.date_range(ts("09:30"), ts("16:00"), freq="s")
    Ss = np.interp(secs.astype("int64"), minutes.astype("int64"), S)
    store.write(pd.DataFrame({"ts_recv": secs, "symbol": "IWM", "bid_px_00": Ss - 0.005, "ask_px_00": Ss + 0.005}),
                study11.iwm_path(c, DAY))
    E, sanity = study11.session_entries(c, DAY)
    assert sanity["iwm_source"] == "iwm_1s" and sanity["iwm_1s_vs_parity_median_abs_bp"] < 1
    T = study11b.session_trips(c, DAY, E.assign(date=DAY).sort_values("ts").reset_index(drop=True))
    assert len(T) == len(E) and T["hedge_through"].notna().all()
    # each fill's hedge leg pays at least half the IWM spread a side; |q| <= 100 shares
    assert (T["hedge_through"].abs() < 100 * 1.0).all()
    for r in ("through", "queue", "front"):
        assert (T[f"repegs_{r}"] >= 0).all()
    out = study11b.explore(T, c)
    assert out["checks"]["exit_time_front_le_queue_le_through"] is True and out["checks"]["hedge_missing"] == 0
    assert set(out["orders_and_days"]) == {"through", "queue", "front"}
