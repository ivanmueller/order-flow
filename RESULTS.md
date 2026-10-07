# Results catalogue: every backtest, its hypothesis, how it was built, what it showed

One entry per run that produced a number. Each entry says what was being tested, what data and rules
built it, the headline result, the verdict against the pre-registered rule, and what happened next.
Full logs with commits and config diffs are in RUNLOG.md; the design is in SPEC.md; the lessons are in
WRITEUP.md. Nothing here touched the holdout (2026-01-01 onward).

## How everything is built (common to all entries)

- **Data.** SPX/SPXW end-of-day option quotes from ThetaData (free tier, the 17:00 ET Cboe curb close
  of session D-1). Start-of-day open interest from Databento OPRA (published before 09:30 ET on D).
  ES 1-minute bars and, for sampled days, ES tick trades with aggressor side, from Databento GLBX.
  SPX and VIX closes from FRED. Raw data is never modified; derived tables are rebuilt from it.
- **GEX engine (`src/gex.py`).** For each session D, from the D-1 quotes and the D open interest:
  put-call-parity forward and discount per expiry, Black-76 implied vols, sticky-strike gamma, dollar
  GEX per contract (calls +, puts -) summed by strike, a net-GEX curve on a +/-5% grid, the flip (zero
  crossing nearest spot), call and put walls (largest positive and negative strikes), top strikes, the
  expected move EM (nearest-expiry ATM straddle), and the GEX percentile against the prior 252
  sessions (minimum 126). Validated by four Gate 0 checks (forward vs ES, put skew, chart comparison,
  EM vs VIX).
- **Levels (`src/levels.py`).** Gamma levels (walls, flip, top strikes) and structural levels (prior-day
  high/low/close, overnight high/low, round numbers), merged within a tolerance, kept if inside the
  level window around the 09:30 price, tagged gamma_only / structural_only / both. Placebo levels:
  random prices inside one EM of the open, at least 4 ticks from any real level, same count rules.
- **Touches and labels (`src/touches.py`).** A touch is an approach from at least 0.10 EM away followed
  by a 1-minute bar within 2 ticks of the level, debounced. Label (Stage 2): success if price moves
  0.10 EM in favour before 0.05 EM against within 60 bars; adverse test from the touch bar, favourable
  test from the next bar (fixed 2026-10-06, see F3). Driftless baseline 33%.
- **Flow features (`src/flow.py`).** From tick trades: absorption ratio (aggressive volume per tick of
  penetration vs the same half-hour's baseline), approach delta, exhaustion, reclaim, V5 break; study 2
  adds delta at the level, at-level share, big-lot share, tape speed, delta divergence.
- **Simulator (`src/sim.py`).** Tick-by-tick walk. Entry at the next print plus one tick of slippage
  (limit entries fill only on a print one tick through). Stop triggers when a print touches S and fills
  one tick beyond (or at the print if it gapped). Target fills only on a print one tick beyond T. Time
  exit at the next print minus one tick. Costs $3.98 per round trip subtracted. Risk floor 4 ticks,
  cap 0.15 EM, target 1.5R, time exit 30 minutes.
- **Statistics (`src/stats.py`).** Day-clustered logits, Newey-West OLS, Wilson intervals, day-bootstrap
  confidence intervals (5,000 draws, 90%), paired-by-day differences.
- **Fairness baseline.** For each trade the win rate a driftless walk would give its exact barriers,
  (R_k - tick) / (2.5 R_k + tick), applied to the observed average win and loss; "market share" is the
  observed expectancy minus that. Verified on a synthetic random walk.

## Pilot (config.pilot.yaml, Mar 3 to May 30 2025, 63 sessions; pipeline checks, not gate decisions)

### P1. Gate 0 on the pilot
- **Hypothesis.** The GEX engine reproduces the market: S0 near the ES-implied cash level, put vol
  above call vol, smooth smile, EM tracking VIX.
- **Result.** Check 1 failed at the 16:15 quote assumption (36% within 5 pts), passed at 17:00 (96.8%)
  after a quote-time scan showed the EOD report is the 17:00 curb close. Check 2 pass (100%), check 4
  pass (corr 0.97). Net GEX negative on 32 of 33 early-April days, flip missing on 36% of them.
- **Verdict.** Gate 0 accepted (2026-10-06) with quote_time set to 17:00.

### P2. Stage 1 on the pilot (43 days with a percentile)
- **Hypothesis.** Higher GEX percentile -> lower range ratio, variance ratio and efficiency ratio,
  with ln VIX and day-of-week in the model.
- **Result.** RR beta -0.76 (p 3e-5), quintile gap 35%; VR wrong sign. Not credible: one crash-and-
  recovery regime, percentile nearly a time trend.
- **Verdict.** Deferred to the full run.

### P3. Stage 2 on the pilot (930 touches)
- **Result.** Success 54% in every group including placebo; no coefficient significant.
- **Verdict.** Carry structural_only + both; deferred.

### P4. Stage 3 on the pilot (218 confirmed trades, $34.09 of tick data)
- **Hypothesis.** Absorption-confirmed fades at carried levels have positive expectancy after costs.
- **Result.** Confirmed -0.07R (CI -0.22..+0.08), naive -0.12R, placebo -0.23R; prior-day levels
  -0.76R. Real levels beat placebo after confirmation but nothing positive.
- **Verdict.** KILL on this sample; motivated the full run.

## Study 1, full in-sample (config.yaml, 2023-06-02 to 2025-12-31)

### F1. Gate 0 re-check (631 days)
- **Result.** S0 vs ES at 17:00 minus basis within 5 pts on 96.7% (median 1.5 pts); put vol > call
  vol 99.4%; EM vs VIX corr 0.87. Flip missing 3% (deep negative-gamma stretches). Six post-half-day
  sessions dropped (zero-bid EOD files); 2024-12-03 dropped from levels (zero-bid dailies).
- **Verdict.** Pass; acceptance stands.

### F2. Stage 1 (510 sessions, 2023-12-01 onward)
- **Hypothesis.** As P2.
- **Result.** RR beta -0.216, p=0.029 (right sign); quintile means 1.66 -> 1.54, gap 7.5%. VR
  -0.071, p=0.11. ER n.s. With ln(EM/S0) as control RR beta -0.399, p=0.001; dropping FOMC days
  unchanged; 0DTE-only percentile n.s.
- **Verdict.** KILL by the rule (gap under 15%), accepted: gamma dropped as a standalone claim, kept
  as a regime variable inside later tests.

### F3. Stage 2 (7,284 touches, 511 days)
- **Hypothesis.** Gamma-tagged levels hold more often than placebo, more so in high gamma.
- **Result, original label.** Placebo 61.2%, both 58.9%, gamma_only 58.7%, structural 56.8%; G
  -0.10 (p 0.28); gex_pct +0.36 (p 0.0003). Superseded: the touch bar's own range was counted as the
  reversal.
- **Result, fixed label.** Placebo 46.5%, gamma_only 45.4%, both 44.4%, structural 42.8% (driftless
  baseline 33%). G -0.18 (p 0.054), G x gex_pct +0.31 (p 0.044), gex_pct +0.29 (p 0.002). Gamma
  levels 40% hold in low gamma vs 51% in high; placebo 46% vs 49%.
- **Verdict.** Not a kill; no group beats placebo. Matteo's decision: score Stage 3 on all three real
  groups (deviation from the pre-registered carry rule, logged).

### F4. Stage 3 main (110 sampled sessions, 1,573 touches, $32.57 of tick data)
- **Hypothesis.** Absorption-confirmed fades (reclaim + abs_ratio >= 2) beat naive fades and have
  positive expectancy after costs.
- **Result.** Confirmed n=446: win 31.8%, avg win 1.41R, avg loss 1.13R, expectancy -0.33R (CI
  -0.42..-0.23), PF 0.58. Naive all real -0.29R (n=1,048). Placebo confirmed -0.30R, naive -0.29R.
  Confirmed minus naive -0.04R (CI -0.14..+0.07). By group, confirmed: structural -0.25R (223),
  gamma_only -0.42R (146), both -0.36R (77). Exits: 375 stop / 166 target / 26 time. Skips: 506
  risk_too_wide, 205 no_fill. Secondary regression: nothing significant.
- **Fairness.** Driftless win rate 35% at the 11-tick median risk vs 32% observed; drag -0.24R;
  market share -0.08R. Naive: 31% vs 34.9%, market share +0.10R (0.6 ticks).
- **Verdict.** KILL (expectancy, CI and confirmed-minus-naive all fail).

### F5. Pre-registered variants V1-V5 (same trades)
- **Hypotheses.** V1 fade in positive gamma; V2 V1 on gamma levels; V3 fade above the flip; V4 wall-
  aligned fades; V5 continuation with the break in negative gamma.
- **Result.** V1 -0.27R (171) vs complement -0.32R (175); V2 -0.24R (75); V3 -0.35R (253); V4 -0.32R
  (66); V5 -0.36R (124) vs complement -0.32R (61). Win rates 31-35%.
- **Verdict.** V3 KILL; others INDICATIVE (under 200); no regime gap.

### F6. Robustness nudges and splits
- **Result.** All 44 evaluated nudges negative (-0.29 to -0.42R), positive share 0. Every year, GEX
  tercile and time-of-day bucket negative with the whole 90% interval below zero.
- **Verdict.** Stage 3 kill robust. No holdout run.

### D1. Simulator fairness diagnostics (not a strategy)
- **Mirror trades** (opposite side of every naive fill): -0.35R vs its baseline -0.32R.
- **Synthetic random walk** through the simulator: observed matches the barrier-geometry baseline
  within sampling error. Conclusion: the losses are the market plus friction, not a simulator bug.

## Study 2, tape-footprint confirmation and at-level entries (same 110 sessions; 9 of 20 variants)

### S2. delta_at_level >= 150 + reclaim, limit entry one tick off the level
- **Hypothesis.** Flow absorbed at the level itself selects reversals; entering at the level cuts
  the entry cost.
- **Build.** New features from prints within 2 ticks of L in the 3-minute window; E1 limit live 10
  minutes, filled only on a print through it, stop under the lowest print to the fill.
- **Result.** n=254, win 27.6%, -0.48R (CI -0.59..-0.36); minus naive on the same touches -0.43R;
  market share -0.10R (-0.6 ticks). Placebo -0.40R with market share 0.00. 184 of 475 confirmed
  touches never came back to the level.
- **Verdict.** KILL; no pre-cost edge.

### S2r. S2 in positive gamma only
- **Result.** n=95, -0.31R, market share +0.07R (0.4 ticks), below the 0.15R threshold.
- **Verdict.** INDICATIVE; no pre-cost edge.

### S2c. Retest-fail continuation after a break, low gamma
- **Build.** After a V5 break, wait up to 15 minutes for a print back within 2 ticks of L; if the
  minute closes without reclaiming, enter with the break on the next print.
- **Result.** n=77, -0.45R, market share -0.09R. 174 no retest, 90 reclaimed.
- **Verdict.** INDICATIVE; no pre-cost edge.

### S2h. S2 with a 2.5R target and 60-minute exit
- **Result.** n=254, win 18.5%, -0.54R, market share -0.16R.
- **Verdict.** KILL.

Study 2 decision (Matteo, 2026-10-07): kill accepted; intraday level thesis closed.

## Planned

## Study 3, session-level gamma regime (approved 2026-10-07; 12 of 20 variants)

### How it is built
One trade per session on 1-minute bars (no tick data). O = the 09:30 open on the session's front
contract; bands at O +/- 0.50 EM (EM from the prior close's straddle). The first bar between 10:00
and 15:00 that reaches a band from inside it is the touch; a bar touching both bands, or price already
beyond a band at 10:00, skips the session. Fade (R1, R3): against the move, stop 0.25 EM beyond the
band, target the open (2:1). Breakout (R2): with the move, stop 0.25 EM back inside, target a further
0.50 EM (2:1); a stop order fills at the bar's open if the bar opened through the band. One tick of
slippage, costs, flat at 15:55. Regime split at the 0.5 GEX percentile; the complement is the
contrast; a 1,000-draw permutation of the percentile across sessions is the placebo.

### S3 first run (superseded)
- **Result.** R1 -0.34R (250), R2 +0.12R (229), R3 -0.32R (244); every regime contrast negative,
  permutation p > 0.8. The unconditioned breakout on all 479 sessions showed +0.19R, PF 1.32.
- **Rule 6 outcome.** The breakout number was inflated by a fill bug: sessions already beyond the
  band at 10:00 entered at the band price. Fixed (clean touches only, gapped opens fill at the open);
  re-run pending. Not a result.

### S3 second run (clean touches, gapped opens fill at the open; 397 sessions)
- **Result.** R1 fade high gamma -0.24R (207), R2 breakout low gamma -0.06R (190), R3 fade above
  flip -0.22R (204). Every regime contrast negative, permutation p > 0.9. Unconditioned breakout
  +0.03R (CI -0.08..+0.15). By tercile the fade worsens and the breakout improves as gamma rises:
  conditional on reaching the band, high-gamma sessions trend.
- **Verdict.** KILL on all three. 12 of 20 variants used; no holdout.

Study 3 decision: pending Matteo's call.

## Study 4, regime-conditioned 0DTE straddle at the D-1 close (approved 2026-10-07; build complete, run pending)

### How it is built
One position per session, entered at the D-1 17:00 ET EOD quotes (the same report the engine's EM
comes from) and held to the SPXW PM settlement (the SPX close on D, from FRED). K = the strike nearest
the nearest-expiry forward with both legs valid, so the straddle mid equals gex_daily.em. Short
straddle sells at the bids; long straddle buys at the asks; iron fly adds long wings bought at the ask
at the valid strikes nearest K +/- one EM. Fees per leg (opt_cost_per_leg_usd). P&L in EM units.
Regime = the PRIOR session's gex_pct (knowable at entry); the same-day percentile is a diagnostic
only. Permutation placebo, complement contrast, all-sessions baseline, terciles, and the Stage 1
regression restated on straddle P&L. Gates: n >= 200, mean >= 0.03 EM, CI lower > 0, contrast CI
lower > 0, permutation p < 0.05; tail block reported. `python -m src.study4`.

### S4a short straddle in high gamma / S4b long straddle in low gamma / S4c iron fly in high gamma
- **Result.** Not yet run on real data (built and tested 2026-10-07 in a container without data/).
- **Verdict.** Pending.
