"""Study 11: execution design (round trip with a passive exit, stale quotes, contract selection, inventory/hedge).
Every expected number below is worked by hand in the comments."""
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest
from scipy.special import ndtr

from src import gex, study10, study11
from src.config import param

DAY = dt.date(2024, 3, 5)
C = "IWM   240315C00200000"
P = "IWM   240315P00200000"
D = "IWM   240315C00201000"


def ts(hms):
    return pd.Timestamp(f"{DAY} {hms}", tz="America/New_York").tz_convert("UTC")


CLOSE = ts("16:00")


def entry(t, sym, s, price, bid, ask, bid_sz=np.nan, ask_sz=np.nan, t_last=None):
    return {"ts": ts(t), "ts_last": ts(t_last or t), "symbol": sym, "s": s, "price": price, "bid": bid, "ask": ask,
            "bid_sz": bid_sz, "ask_sz": ask_sz}


def trades(rows):
    """rows: (time, symbol, price, size, bid, ask)."""
    return pd.DataFrame([{"ts": ts(t), "symbol": s, "price": p, "size": q, "bid": b, "ask": a}
                         for t, s, p, q, b, a in rows])


def quotes(rows):
    return pd.DataFrame([{"ts": ts(t), "symbol": s, "bid": b, "ask": a} for t, s, b, a in rows])


# ---- exit fill rules ---------------------------------------------------------------------------
def _exit_case():
    E = pd.DataFrame([entry("10:00:00", C, 1, 1.00, 1.00, 1.04, bid_sz=7, ask_sz=5),     # long: offer at 1.04
                      entry("10:00:00", P, -1, 2.00, 1.96, 2.00, bid_sz=2, ask_sz=9),    # short: bid at 1.96
                      entry("10:00:00", D, 1, 1.00, 1.00, 1.04)])                        # no sizes on the quote
    tr = trades([("10:00:00", C, 1.00, 1, 1.00, 1.04),      # the entry print itself (at ts_last): not an exit
                 ("10:00:10", C, 1.04, 3, 1.00, 1.04),      # at our price: front fills; 3 of the 5 ahead traded
                 ("10:00:20", C, 1.04, 3, 1.00, 1.04),      # 6 > 5 ahead: back of the queue fills
                 ("10:00:40", C, 1.05, 1, 1.01, 1.05),      # one tick through: the conservative rule fills
                 ("10:00:15", P, 1.96, 1, 1.96, 2.00),      # at our bid: front; 1 of 2 ahead
                 ("10:00:10", D, 1.04, 50, 1.00, 1.04),     # at our price, size unknown ahead: only front
                 ("10:00:50", D, 1.05, 1, 1.01, 1.05)])
    snaps = quotes([("10:00:30", P, 1.94, 1.95)])           # ask 1.95 <= 1.96 - 1 tick: a seller would hit our bid
    obs = pd.concat([snaps, tr[["ts", "symbol", "bid", "ask"]]], ignore_index=True)
    return E, tr, obs


def test_exit_times_three_rules():
    E, tr, obs = _exit_case()
    T = study11.exit_times(E, tr, obs, horizon_min=5, tick=0.01, close=CLOSE)
    assert list(T.loc[0]) == [ts("10:00:40"), ts("10:00:20"), ts("10:00:10")]     # through, queue, front
    assert list(T.loc[1]) == [ts("10:00:30"), ts("10:00:30"), ts("10:00:15")]     # quote through; queue = through
    assert list(T.loc[2]) == [ts("10:00:50"), ts("10:00:50"), ts("10:00:10")]     # no size: queue waits for through
    assert list(T.columns) == list(study11.MODELS)


def test_exit_window_and_close():
    E = pd.DataFrame([entry("10:00:00", C, 1, 1.00, 1.00, 1.04), entry("15:58:00", C, 1, 1.00, 1.00, 1.04)])
    tr = trades([("10:01:30", C, 1.05, 1, 1.01, 1.05), ("16:00:30", C, 1.05, 1, 1.01, 1.05)])
    obs = tr[["ts", "symbol", "bid", "ask"]]
    T1 = study11.exit_times(E, tr, obs, horizon_min=1, tick=0.01, close=CLOSE)
    T5 = study11.exit_times(E, tr, obs, horizon_min=5, tick=0.01, close=CLOSE)
    assert pd.isna(T1.loc[0, "through"]) and T5.loc[0, "through"] == ts("10:01:30")
    assert pd.isna(T5.loc[1, "through"])                     # 16:00:30 is after the close


def test_exit_ignores_the_entry_sweep():
    # the entry is a sweep 10:00:00 -> 10:00:00.008; a print at our exit price inside it is not an exit
    E = pd.DataFrame([entry("10:00:00", C, 1, 1.00, 1.00, 1.04, ask_sz=0, t_last="10:00:00.008")])
    tr = trades([("10:00:00.005", C, 1.05, 1, 1.00, 1.04)])
    T = study11.exit_times(E, tr, tr[["ts", "symbol", "bid", "ask"]], horizon_min=5, tick=0.01, close=CLOSE)
    assert T.isna().all(axis=None)


# ---- round trip --------------------------------------------------------------------------------
def test_round_trip_passive_and_fallback(cfg):
    E = pd.DataFrame([entry("10:00:00", C, 1, 1.00, 1.00, 1.04),          # exits passively at 10:00:40
                      entry("10:00:00", D, 1, 1.00, 1.00, 1.04),          # never exits passively
                      entry("10:00:00", P, -1, 1.04, 1.00, 1.04)])        # short, never exits passively
    tr = trades([("10:00:40", C, 1.05, 1, 1.01, 1.05)])
    snaps = quotes([("10:00:00", D, 1.00, 1.04), ("10:03:00", D, 0.97, 1.01),
                    ("10:00:00", P, 1.00, 1.04), ("10:03:00", P, 0.97, 1.01)])
    obs = pd.concat([snaps, tr[["ts", "symbol", "bid", "ask"]]], ignore_index=True)
    T = study11.exit_times(E, tr, obs, horizon_min=5, tick=0.01, close=CLOSE)
    R = study11.round_trips(E, T, obs, [1, 5], tick=0.01, slip_ticks=1, close=CLOSE, mult=100)
    # C: sold at the 1.04 offer 40 s later: (1.04 - 1.00) x 100 = +4
    assert R.loc[0, "gross_through_1"] == pytest.approx(4.0) and R.loc[0, "passive_through_1"]
    assert R.loc[0, "hold_through_1"] == pytest.approx(40 / 60)
    # D, 1 min: newest quote by 10:01 is the 10:00 one, bid 1.00, sold one tick under: 0.99 -> -1
    assert R.loc[1, "gross_through_1"] == pytest.approx(-1.0) and not R.loc[1, "passive_through_1"]
    # D, 5 min: newest quote by 10:05 is 10:03, bid 0.97 -> 0.96 -> -4
    assert R.loc[1, "gross_through_5"] == pytest.approx(-4.0) and R.loc[1, "hold_through_5"] == pytest.approx(5)
    # P short at 1.04, 5 min: buys back at ask 1.01 + 1 tick = 1.02 -> -1 x (1.02 - 1.04) x 100 = +2
    assert R.loc[2, "gross_through_5"] == pytest.approx(2.0)
    E["mid0"] = 1.02
    fees = study11.fee_net(R.assign(mid0=1.02), "gross_through_5", cfg)
    assert fees.loc[1, "pass_through"] == pytest.approx(-4.0 - 2 * 0.05)    # a fee on the way in and out
    assert fees.loc[1, "zero_commission"] == pytest.approx(-4.0)
    assert fees.loc[1, "ibkr_tiered"] == pytest.approx(-4.0 - 2 * (0.65 + 0.05))


# ---- delta, forward, stale quotes, hedge -------------------------------------------------------
def test_forward_by_expiry():
    rows = []
    for exp, S in ((dt.date(2024, 3, 8), 200.30), (dt.date(2024, 3, 15), 200.80)):
        for k in (199, 200, 201):
            c = max(S - k, 0) + 0.5
            for right, mid in (("C", c), ("P", c - (S - k))):
                rows.append({"ts": ts("10:00"), "symbol": f"IWM   {exp:%y%m%d}{right}{k * 1000:08d}",
                             "bid": mid - 0.01, "ask": mid + 0.01})
    f = study11.forward_by_expiry(pd.DataFrame(rows), n_strikes=3)
    got = f.set_index("expiration")["F"]
    assert got[dt.date(2024, 3, 8)] == pytest.approx(200.30) and got[dt.date(2024, 3, 15)] == pytest.approx(200.80)


def test_delta_from_implied_vol():
    exp = dt.date(2024, 3, 15)
    T = (pd.Timestamp(f"{exp} 16:00", tz="America/New_York") - pd.Timestamp(f"{DAY} 10:00", tz="America/New_York")
         ).total_seconds() / (365 * 86400)
    c = float(gex.black76_price(200.0, 200.0, T, 0.2, 1.0, True))
    p = float(gex.black76_price(200.0, 200.0, T, 0.2, 1.0, False))
    E = pd.DataFrame({"ts": [ts("10:00")] * 4, "expiration": [exp] * 4, "right": ["C", "P", "C", "C"],
                      "strike": [200.0, 200.0, 150.0, 260.0], "mid0": [c, p, 49.0, 0.0]})
    fwd = pd.DataFrame({"ts": [ts("09:59")], "expiration": [exp], "F": [200.0]})
    iv, delta = study11.option_greeks(E, fwd, pd.Series(dtype=float))
    d1 = (math.log(1.0) + 0.5 * 0.04 * T) / (0.2 * math.sqrt(T))
    assert iv[0] == pytest.approx(0.2, abs=1e-6) and iv[1] == pytest.approx(0.2, abs=1e-6)
    assert delta[0] == pytest.approx(ndtr(d1), abs=1e-6) and delta[1] == pytest.approx(ndtr(d1) - 1, abs=1e-6)
    # below intrinsic (no implied vol): deep ITM call -> delta 1; worthless OTM -> 0
    assert np.isnan(iv[2]) and delta[2] == 1.0 and np.isnan(iv[3]) and delta[3] == 0.0


def test_stale_ratio_sign_and_point_in_time():
    S = pd.Series([200.0, 199.95, 199.90, 190.0],
                  index=[ts("09:59:00"), ts("09:59:45"), ts("10:00:00"), ts("10:00:05")])
    E = pd.DataFrame({"ts": [ts("10:00:00")] * 3, "s": [1, 1, -1], "spread": [0.04] * 3})
    delta = np.array([0.5, -0.5, 0.5])                      # long call, long put, short call
    X = study11.stale_features(E, delta, S, [10, 60], source="iwm_1s")
    # IWM 200.00 -> 199.90 over the last 60 s (the 10:00:05 print is after the fill and unused)
    # long call: -(+1)(0.5)(-0.10)(100) = +5 adverse; half-spread $2 -> ratio 2.5
    assert X.loc[0, "adverse_60"] == pytest.approx(5.0) and X.loc[0, "stale_60"] == pytest.approx(2.5)
    assert X.loc[1, "stale_60"] == pytest.approx(-2.5)     # long put gains when IWM falls
    assert X.loc[2, "stale_60"] == pytest.approx(-2.5)     # short call gains too
    assert X.loc[0, "stale_10"] == pytest.approx(1.25)     # 10 s: from 199.95 (09:59:45, newest by 09:59:50): +2.5
    Xm = study11.stale_features(E, delta, S, [10, 60], source="parity_1m")
    assert Xm["stale_10"].isna().all() and Xm.loc[0, "stale_60"] == pytest.approx(2.5)   # minute IWM can't see 10 s


def test_hedge_uses_the_first_price_after_the_fill():
    S = pd.Series([200.0, 200.2, 201.2], index=[ts("10:00:00"), ts("10:00:01"), ts("10:05:00")])
    E = pd.DataFrame({"ts": [ts("10:00:00")] * 2, "s": [1, -1]})
    exit_ts = pd.Series([ts("10:05:00")] * 2)
    h = study11.hedge_pnl(E, np.array([0.5, -0.4]), exit_ts, S, cost=0.005)
    # long call: 50 shares short at 200.2 (not 200.0 at the fill), bought back 201.2: -50 - 2 x 0.005 x 50 = -50.5
    assert h[0] == pytest.approx(-50.5)
    # short put = +40 delta, hedged by selling 40 shares: -40 x 1.0 - 0.4 = -40.4
    assert h[1] == pytest.approx(-40.4)


# ---- inventory ---------------------------------------------------------------------------------
def _day():
    m = lambda hm: ts(hm)                                                       # noqa: E731
    return pd.DataFrame({"ts": [m("10:00"), m("10:01"), m("10:02"), m("10:06")],
                         "exit_ts": [m("10:10"), m("10:05"), m("10:20"), m("10:08")],
                         "s": [1, 1, 1, -1], "delta": [0.5, 0.5, 0.6, 0.5], "gross": [2.0, 3.0, -1.0, 4.0],
                         "hedge": [-1.0, 0.0, 0.5, 0.0], "price": [1.0, 1.0, 1.0, 2.0]})


def test_inventory_limits_by_hand():
    rng = np.random.default_rng(0)
    r = study11.inventory_day(_day(), 10, rng, max_open=2, max_delta=None, long_only=False)
    # e2 is skipped (two open at 10:02); e1 is closed by 10:06, so e3 fits
    assert r["pnl"] == pytest.approx(9.0) and r["pnl_hedged"] == pytest.approx(8.0)
    assert r["taken"] == 3 and r["skipped"] == 1 and r["peak_open"] == 2
    assert r["peak_delta"] == pytest.approx(100) and r["peak_premium"] == pytest.approx(200)
    r = study11.inventory_day(_day(), 10, rng, max_open=None, max_delta=60, long_only=False)
    # e1 (to 100 shares) and e2 (110) breach 60; e3 cuts net delta 50 -> 0 and is taken
    assert r["pnl"] == pytest.approx(6.0) and r["skipped"] == 2
    r = study11.inventory_day(_day(), 10, rng, max_open=None, max_delta=None, long_only=True)
    assert r["pnl"] == pytest.approx(4.0) and r["taken"] == 3                   # the short is not an option


def test_delta_limit_from_flat():
    d = _day().iloc[[0, 3]].reset_index(drop=True)
    d.loc[1, "ts"], d.loc[1, "exit_ts"] = ts("10:01"), ts("10:03")
    r = study11.inventory_day(d, 10, np.random.default_rng(0), max_open=None, max_delta=10, long_only=False)
    # from flat, e0 alone is +50 > 10 -> skipped; then the short alone is -50 -> skipped too
    assert r["taken"] == 0
    r = study11.inventory_day(d, 10, np.random.default_rng(0), max_open=None, max_delta=50, long_only=False)
    assert r["taken"] == 2 and r["peak_delta"] == pytest.approx(50)


# ---- IWM 1-second pull -------------------------------------------------------------------------
class FakeBudget:
    def __init__(self, cost):
        self.cost, self.pulled = cost, []

    def price(self, cl, args):
        return self.cost

    def pull(self, cl, job, what, args):
        self.pulled.append(args)

        class Dd:
            def to_df(self_inner):
                return pd.DataFrame({"ts_recv": [pd.Timestamp(args["start"])], "symbol": ["IWM"],
                                     "bid_px_00": [200.0], "ask_px_00": [200.01]}).set_index("ts_recv")
        return Dd(), self.cost


def test_iwm_pull_prices_then_writes(cfg, tmp_path):
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    b = FakeBudget(0.02)
    q = study11.iwm_pull(c, None, b, [DAY], price_only=True)
    assert q["pieces"] == 1 and q["usd"] == pytest.approx(0.02) and b.pulled == []
    a = study11.iwm_args(c, DAY)
    assert a["schema"] == "bbo-1s" and a["symbols"] == ["IWM"] and a["stype_in"] == "raw_symbol"
    assert a["dataset"] == "XNAS.ITCH"
    r = study11.iwm_pull(c, None, b, [DAY], price_only=False)
    assert r["written"] == 1
    assert study11.iwm_pull(c, None, b, [DAY], price_only=False)["skipped"] == 1    # idempotent
    s = study11.load_iwm(c, DAY)
    assert s.iloc[0] == pytest.approx(200.005)


# ---- end to end on synthetic raw pieces --------------------------------------------------------
def chain_rows(t, exp, S, strikes, tv=0.5):
    rows = []
    for k in strikes:
        c = max(S - k, 0) + tv
        for right, mid in (("C", c), ("P", c - (S - k))):
            rows.append({"ts": t, "symbol": f"IWM   {exp:%y%m%d}{right}{int(k * 1000):08d}",
                         "bid": round(mid - 0.01, 2), "ask": round(mid + 0.01, 2)})
    return rows


def test_session_end_to_end(cfg, tmp_path):
    from src import store
    c = dict(cfg)
    c["data"] = {**cfg["data"], "root": str(tmp_path)}
    c["params"] = {**cfg["params"], "s11_inventory_draws": {"value": 5}, "bootstrap_draws": {"value": 200}}
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
            # a buyer lifts the 1.04-type offer 20 s later with 10 contracts (more than the 5 displayed)
            prints_.append({**q, "ts_recv": t + pd.Timedelta(seconds=40), "price": row["ask"], "size": 10,
                            "publisher_id": 24})
    snaps, prints_ = pd.DataFrame(snaps), pd.DataFrame(prints_)
    for sc, raw in (("cbbo-1m", snaps), ("tcbbo", prints_)):
        for s0, e0 in study10.chunks(DAY, "09:30", "16:00", 30):
            part = raw[(raw["ts_recv"] >= s0) & (raw["ts_recv"] < e0)]
            store.write(part.reset_index(drop=True), study10.piece_path(c, sc, DAY, s0))
    E, sanity = study11.session_entries(c, DAY)
    assert sanity["iwm_source"] == "parity_1m" and len(E) > 300
    longs = E[E["s"] == 1]
    assert longs["passive_front_1"].all() and longs["passive_queue_1"].all()        # 10 > 5 ahead, 20 s later
    for p in (1, 5, 15):
        f, q, t = (E[f"gross_{m}_{p}"].mean() for m in ("front", "queue", "through"))
        assert f >= q - 1e-9 and q >= t - 1e-9
    assert E["delta"].notna().all() and E["delta_b"].notna().all()
    assert np.isfinite(E["hedge"]).all()
    out = study11.explore(E, c, {str(DAY): sanity})
    assert set(out) >= {"round_trip", "checks", "stale_quotes", "contract_selection", "inventory"}
    assert out["checks"]["front_ge_queue_ge_through"] is True
    assert {"K1_skip_stale_0.5_60s", "K3_skip_dte_1-8"} <= set(out["stale_quotes"]["candidates"]) | set(
        out["contract_selection"]["candidates"])
    assert len(out["inventory"]) == 3 * len(param(c, "s11_limits")) * 2
