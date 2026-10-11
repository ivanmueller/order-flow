# Study 19: harvesting the index volatility premium the Sinclair & Mack way (weekly SPXW short volatility)

Status: DRAFT 2026-10-11 (Matteo: "lets start with option 1"). Not approved; no config entries. Source: Mack &
Sinclair, Retail Options Trading (2024), ch. 10 (volatility premium harvesting), ch. 14 (day-of-week), ch. 15
(straddle breakout). New study family for the SPX options data; the SPX holdout (2026-01-01..2026-09-30) is sealed
and unused by any option study, so it is available once at the end.

## Thesis

Index options are insurance; buyers pay a premium to avoid risk (82% of days positive, ~3.5 vol points in SPY, larger
in relative terms at low VIX; weekends over-compensated). This is a RISK PREMIUM, not a prediction: the edge is
real but paid for with rare, large losses. The study asks whether it survives honest fills and costs in SPXW weeklies
over our data, and whether two of the book's filters improve it. Prior evidence in this repo: Study 4's 0DTE short
straddle at the D-1 close earned +0.059 EM a session (CI -0.02..+0.14, underpowered).

## Data (no new spend in sample A)

- SPX/SPXW end-of-day chains from ThetaData (17:00 ET Cboe curb report, on disk 2023-06..2026-09, max 45 DTE), the
  same data and validity filter (max_rel_spread) as the GEX engine and Study 4.
- Settlement: SPX close on the expiry day (SPXW is PM-settled, cash; the book uses SPY; SPXW avoids assignment).
- VIX and VIX3M (FRED VIXCLS, VXVCLS; free) for the term-structure filter. VXVCLS is new to ingest_daily.

## Trades (all fills conservative, as Study 4)

Entry at the Friday 17:00 report (Thursday when Friday is a holiday); expiry the next week's Friday SPXW (or the
nearest SPXW 4-8 calendar days out). Sell at the bid, buy at the ask, opt_cost_per_leg_usd (1.50) per leg, held to
cash settlement, no adjustment (the book's examples do not adjust). Forward from put-call parity (gex.fit_forward);
deltas Black-76 from mid implied vol.
- Straddle: short call + put at the strike nearest the forward with both legs valid.
- Strangle: short 20-delta call and 20-delta put (nearest valid strikes).
- Iron condor (reported, not gated): the strangle plus long 2-delta wings.
- One-day (V4): short straddle on the SPXW expiring Tuesday, entered at Monday's 17:00 report (the book's "short vol
  Tuesday"); Thursday reported as the contrast.
P&L in SPX points, in expected-move units (EM = entry straddle mid), and in dollars per XSP contract (1/10 SPX,
the retail-size equivalent; XSP's wider spreads are covered by a stress at 2x costs).

## Variants: a fixed family of 4 (gated), chosen from the book before looking at results

- V1: weekly 20-delta short strangle, every Friday (the book's core harvest)
- V2: V1, skipped while VIX >= VIX3M at entry (term structure inverted; the book: "catching a falling knife")
- V3: weekly short ATM straddle with the V2 filter
- V4: one-day short straddle, Monday entry for Tuesday expiry
Reported only: iron condor; Thursday one-day; a "skip after 3 straight winning weeks" filter (ch. 15); VIX deciles;
worst weeks; drawdown and a stress test (each week's position revalued under the worst weekly SPX moves in the
sample) at a stated size.

## Rules (written before any result is seen)

Per variant, after costs, weekly (V4: per session) P&L in EM units: mean > 0 with a 90% bootstrap lower bound > 0;
positive in both halves of the sample; still positive with costs doubled; at least 50 trades. The mean WITHOUT the
best 5% is not a test here (short volatility's risk is in the worst weeks, not the best); instead the report shows
the result with the worst week counted twice. A variant that passes goes to the sealed 2026-01..09 holdout once
(same tests, >= 20 trades). Expected: V1-V3 point estimates positive; with sample A the lower bound is likely to
include zero (about 120 weekly trades; power roughly one in two at the book's premium size).

## Sample: Matteo's choice at approval

- A (default, $0): 2023-06..2025-12 on disk; ~120 weekly trades, ~125 Tuesday trades. Underpowered: a FAIL here may
  only mean too little data.
- B (~$40, one ThetaData Value month, priced first): weekly/daily EOD chains 2016..2023-05 added, ~500 weekly trades,
  power high; needs an ingest change for the paid tier and an extra validity check on older weeklies.

## Proposed parameters (to config.yaml only on approval)

s19_sample (A|B), s19_entry_weekday 4 (Friday), s19_min_days 4, s19_max_days 8, s19_delta 0.20, s19_wing_delta 0.02,
s19_skip_streak 3, s19_cost_stress 2.0, s19_seed; gates study19_min_trades 50, study19_holdout_min_trades 20.
Existing values reused: opt_cost_per_leg_usd 1.50, max_rel_spread, option_multiplier.

## Risk, stated plainly

Short index volatility makes money most weeks and loses several months of profit in a crash week. The study reports
the worst weeks and a stress test so the size can be chosen for survival, as the book insists ("evaluate based on
consequence, not probability"). Tradeable from BC through a Canadian broker offering US index options (XSP/SPX).
