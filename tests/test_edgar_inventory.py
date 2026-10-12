"""Study 21 step 0: EDGAR companyfacts -> point-in-time firm-years and Piotroski F-score signals, by hand.

Synthetic company, December year-ends. FY2020 row (10-K A20 filed 2021-03-01), hand-computed:
  ROA = 10/120 = 0.0833 > 0                                   -> 1
  CFO/A = 15/120 = 0.125 > 0                                  -> 1
  dROA: 0.0833 vs 6/100 = 0.06 (2019 NI AS FIRST REPORTED; the restated 12 would give 0.12 and flip it to 0) -> 1
  ACCRUAL: 0.125 > 0.0833                                     -> 1
  dLEVER: 25/avg(125,120)=0.2041 < 30/avg(120,100)=0.2727     -> 1
  dLIQUID: 70/40=1.75 > 60/40=1.5                             -> 1
  EQ_OFFER: weighted shares 10 <= 10                          -> 1
  dMARGIN: 40/120=0.333 > 30/100=0.30                         -> 1
  dTURN: 120/120=1.00 vs 100/100=1.00, not higher             -> 0
  F = 8; book-to-float = 50/40 = 1.25
"""
import numpy as np
import pandas as pd
import pytest

from src import edgar_inventory as ei


def _f(val, end, accn, filed, start=None, form="10-K", fp="FY"):
    d = {"end": end, "val": val, "accn": accn, "filed": filed, "form": form, "fp": fp}
    if start:
        d["start"] = start
    return d


A18, A19, A20 = ("A18", "2019-03-01"), ("A19", "2020-03-01"), ("A20", "2021-03-01")
Y = {2018: ("2018-01-01", "2018-12-31"), 2019: ("2019-01-01", "2019-12-31"), 2020: ("2020-01-01", "2020-12-31")}


def _flow(v19, v20, extra_19=None):
    """2019 value first reported in A19, 2020 in A20 (with the 2019 comparative repeated, possibly restated)."""
    s19, e19 = Y[2019]
    s20, e20 = Y[2020]
    return [_f(v19, e19, *A19, start=s19), _f(v19 if extra_19 is None else extra_19, e19, *A20, start=s19),
            _f(v20, e20, *A20, start=s20)]


def _inst(v18, v19, v20):
    return [_f(v18, "2018-12-31", *A18), _f(v18, "2018-12-31", *A19), _f(v19, "2019-12-31", *A19),
            _f(v19, "2019-12-31", *A20), _f(v20, "2020-12-31", *A20)]


def company():
    g = {
        "NetIncomeLoss": {"USD": _flow(6, 10, extra_19=12)},          # 2019 restated to 12 in the FY2020 10-K
        "NetCashProvidedByUsedInOperatingActivities": {"USD": _flow(4, 15)},
        "Assets": {"USD": [_f(100, "2017-12-31", *A18)] + _inst(100, 120, 125)},
        "LongTermDebtNoncurrent": {"USD": _inst(20, 30, 25)},
        "AssetsCurrent": {"USD": _inst(50, 60, 70)},
        "LiabilitiesCurrent": {"USD": _inst(40, 40, 40)},
        "Revenues": {"USD": _flow(100, 120)},
        "GrossProfit": {"USD": _flow(30, 40)},
        "WeightedAverageNumberOfSharesOutstandingBasic": {"shares": _flow(10, 10)},
        "StockholdersEquity": {"USD": _inst(40, 45, 50)},
    }
    # a later 10-K/A restating FY2020 net income must not change the FY2020 row (filed after it)
    g["NetIncomeLoss"]["USD"].append(_f(-50, "2020-12-31", "A20A", "2021-09-01", start="2020-01-01", form="10-K/A"))
    # a 10-Q fact is ignored
    g["Revenues"]["USD"].append(_f(999, "2020-06-30", "Q2", "2020-08-01", start="2020-01-01", form="10-Q", fp="Q2"))
    dei = {"EntityPublicFloat": {"USD": [_f(40, "2020-06-30", *A20), _f(30, "2019-06-30", *A19)]}}
    return {"cik": 123, "entityName": "Synthetic Co",
            "facts": {"us-gaap": {k: {"units": v} for k, v in g.items()},
                      "dei": {k: {"units": v} for k, v in dei.items()}}}


def test_firm_year_fscore_uses_first_reported_values():
    fy = ei.firm_years(company())
    r = fy.set_index("accn").loc["A20"]
    assert r["fye"] == pd.Timestamp("2020-12-31") and r["filed"] == pd.Timestamp("2021-03-01")
    expect = {"s_roa": 1, "s_cfo": 1, "s_droa": 1, "s_accrual": 1, "s_dlever": 1, "s_dliquid": 1, "s_eq": 1,
              "s_dmargin": 1, "s_dturn": 0}
    for k, v in expect.items():
        assert r[k] == v, k
    assert r["n_signals"] == 9 and r["fscore"] == 8
    assert r["float_usd"] == 40 and r["bm"] == pytest.approx(1.25)
    assert r["ni"] == 10                                         # not the later 10-K/A's -50


def test_value_filed_after_the_filing_is_not_used():
    c = company()
    ca = c["facts"]["us-gaap"]["AssetsCurrent"]["units"]["USD"]
    # drop 2020 current assets from A20; it appears only in a filing made a year later
    ca[:] = [x for x in ca if not (x["accn"] == "A20" and x["end"] == "2020-12-31")]
    ca.append(_f(70, "2020-12-31", "A21", "2022-03-01"))
    r = ei.firm_years(c).set_index("accn").loc["A20"]
    assert np.isnan(r["s_dliquid"]) and r["n_signals"] == 8 and np.isnan(r["fscore"])


def test_missing_debt_tag_is_zero_debt_and_flagged():
    c = company()
    del c["facts"]["us-gaap"]["LongTermDebtNoncurrent"]
    r = ei.firm_years(c).set_index("accn").loc["A20"]
    assert r["ltd_imputed"] and r["s_dlever"] == 0               # 0 -> 0 leverage did not fall


def test_earlier_years_without_prior_data_have_fewer_signals():
    fy = ei.firm_years(company()).set_index("accn")
    assert "A19" in fy.index and fy.loc["A19", "n_signals"] < 9  # no 2018 flows on file
    assert np.isnan(fy.loc["A19", "fscore"])


def test_summary_counts_and_size_buckets():
    fy = ei.firm_years(company())
    fy["cik"] = 123
    fy["sic"] = 3571
    s = ei.summarize(fy, last_filed_cut=pd.Timestamp("2024-01-01"), buckets=[50e6, 300e6, 2e9])
    y = s["by_filed_year"]["2021"]
    assert y["firm_years"] == 1 and y["all_9_signals"] == 1 and y["float_lt_50m"] == 1
    assert s["companies"]["stopped_filing_before_cut"] == 1     # last 10-K filed 2021 -> dead firms are kept


def test_zero_denominators_give_missing_signals_not_a_crash():
    c = company()
    cl = c["facts"]["us-gaap"]["LiabilitiesCurrent"]["units"]["USD"]
    for x in cl:
        if x["end"] == "2020-12-31":
            x["val"] = 0                                         # no current liabilities in 2020
    rv = c["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    for x in rv:
        if x.get("end") == "2019-12-31":
            x["val"] = 0                                         # pre-revenue in 2019
    r = ei.firm_years(c, cik=777).set_index("accn").loc["A20"]
    assert np.isnan(r["s_dliquid"]) and np.isnan(r["s_dmargin"])
    assert r["s_dturn"] == 1                                     # 120/120 vs 0/100: zero revenue is a valid turnover
    assert r["cik"] == 777 and r["n_signals"] == 7


def test_cik_comes_from_the_file_name_when_the_json_has_none():
    c = company()
    del c["cik"]
    assert (ei.firm_years(c, cik=42)["cik"] == 42).all()


def test_byte_prefilter_only_drops_files_that_cannot_score():
    import json as _j
    raw = _j.dumps(company()).encode()
    assert ei.relevant(raw)
    no10k = raw.replace(b'"10-K', b'"20-F')
    assert not ei.relevant(no10k) and len(ei.firm_years(_j.loads(no10k))) == 0
