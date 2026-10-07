# Review of studies 1 to 3, and the next trade expression

2026-10-07. Code and log audit of the repository at commit 77f9ed6 (branch
`claude/affectionate-gauss-7nuqm8`). The `data/` directory is not in the repository, so nothing was
re-run on real data; every number below is quoted from RUNLOG.md, RESULTS.md and WRITEUP.md. The test
suite was run here: 77 passed.

## 1. Verdict

The intraday level and flow thesis is closed. The three studies are internally consistent, the
simulator is conservative in every place I checked, and the one positive residual (naive fades beat
a driftless walk by about 0.6 ticks) is below any taker's friction. No bug I found would reverse a
kill. The modeling caveats in section 3 bear on how good the *regime variable* is, not on whether a
level trade was wrongly killed; if anything they mean the regime effect is understated.

What survived is a session-scale volatility fact, not an intraday price fact: high open-interest
gamma predicts a smaller session range beyond VIX (range-ratio beta -0.216, p = 0.029; -0.399,
p = 0.001 with ln(EM/S0) as the control). Studies 1 to 3 tried to monetise that fact with
directional ES trades whose risk was 6 to 43 ticks, at prices that did not exist until the day had
already moved. Section 5 proposes the direct expression instead: a regime-conditioned 0DTE
straddle sold at the prior close and held to settlement, where the regime effect is several times
the friction rather than a quarter of it.

## 2. What was checked

Code read in full: `gex.py`, `levels.py`, `touches.py`, `flow.py`, `sim.py`, `stage3.py`,
`study3.py`, `analysis.py`, `stats.py`, `robustness.py`, `regime.py`, `calendar.py`, `store.py`,
`ingest_futures.py`, the tests, and the notebooks (which only call `src/`).

Point in time. Every input to a session D level or trade is from D-1 quotes, OI published before
09:30 on D, or D-1 closes; `gex_pct` ranks against prior sessions only; the Stage 3 volume baseline
uses prior sessions only; the reclaim decision waits for the absorption window (`t_dec`), so no
future volume enters the entry; the Stage 2 favourable scan starts on bar t+1. Holdout: every load
goes through `calendar.seal()`, and holdout tables are written under separate names. No roll spans a
window: sessions are filtered by `instrument_id`, prior-day levels are dropped on roll days.

Fills and costs. Entry pays a tick of slippage, stops fill a tick beyond (or at the print if it
gapped), targets need a print a tick beyond, time exits give up a tick, $3.98 round trip is always
subtracted, and same-bar ambiguity is a loss. The driftless-walk baseline for these barriers,
(R_k - tick) / ((1 + m) R_k + tick), was verified against a synthetic tick walk in
`test_simulator_is_fair_on_a_driftless_tick_walk`, and the mirror diagnostic (opposite side of every
naive fill) comes out at about twice the drag, which is what a fair simulator gives.

Selection. Levels, placebo levels, touch windows and the Stage 3 day sample are all fixed by seeds
chosen before the trade data existed. Stage 3 skipped 506 confirmed touches as `risk_too_wide` versus
446 taken; the `max_risk` nudge to 0.20 EM made expectancy worse (-0.40R), so the skip is not hiding
a profitable tail.

Verdicts versus the pre-registered rules. Every KILL in RESULTS.md is correctly scored against the
rule that was written before the run. Two deviations are logged, both Matteo's: scoring Stage 3 on
all three real level groups, and running V1 to V5 after a Stage 1 kill. Neither created a pass.

## 3. Scrutiny findings

### 3a. Sound (no action)

- Stage 2 labeler fix (favourable move from bar t+1) is the conservative direction; it moved hold
  rates from 57-61% to 43-47% against a 33% driftless baseline and left the placebo comparison
  intact.
- Study 3 first-run breakout number (+0.19R) was correctly withdrawn: price already beyond the band
  at 10:00 had been entered at the band price. The second run's +0.03R with an interval straddling
  zero is the honest number.
- The 0.6-tick residual at touched levels is the right size for liquidity resting at round and prior
  extreme prices; the literature on round-number support and resistance finds the same small bounce
  (e.g. Osler 2003 on FX). It is real and it is not tradeable by a taker.
- The quote-time finding (ThetaData's EOD snapshot tracks ES at 17:00, not 16:15) matches Cboe's
  curb session, which runs 16:15 to 17:00 ET for SPX and SPXW only. The forward fit at that reference
  passes check 1 on 96.7% of days.

### 3b. Modeling caveats that affect the regime variable (report-only re-checks recommended)

These do not change any verdict. They matter for Study 4, which conditions on the regime.

1. **The dealer sign convention is an assumption, not an observation.** Calls +1, puts -1 assumes
   dealers are long the calls customers overwrote and short the puts customers bought. Public
   critiques say exactly that: it is an inventory convention, not option mathematics, and it can be
   wrong by strike and by day. Barbon and Buraschi sign their gamma imbalance from Cboe open-close
   customer flow, not from a convention. 0DTE flow is, by Cboe's own account, roughly balanced
   between buying and selling, so the put sign on 0DTE strikes is a coin flip. The engine's 0DTE-only
   percentile was not significant in Stage 1, which is what a mis-signed component would look like.
   Re-check, report only: Stage 1 under three alternative definitions of net GEX (all contracts long
   gamma for dealers; calls and puts both negative; OI-weighted absolute gamma with no sign). If the
   range-ratio beta is stable, the convention does not matter here; if one definition is much
   stronger, Study 4 should use it, chosen before the run.

2. **Calendar-time clock.** T_q runs 17:00 D-1 to 16:00 D (1,380 minutes) and T_o runs 09:30 to
   16:00 (390 minutes), both in calendar minutes. Overnight carries far less variance per minute than
   RTH, so the 0DTE vol backed out at 17:00 is a 23-hour vol, and evaluating gamma at T_o = 28% of
   calendar time understates the remaining variance at the open by roughly two to three times. The
   0DTE ATM strikes therefore get too much gamma weight and far strikes too little. The fix is a
   business-time clock (overnight weighted at about a third); it changes the 0DTE and one-day
   components most. Not a verdict changer; worth doing before Study 4 because the EM straddle itself
   is unaffected (it is a quoted price), only the gamma profile is.

3. **The percentile ranks raw dollar GEX, which drifts with S^2 and with OI growth.** SPX went from
   about 4,300 to about 6,800 in the sample, so the S^2 term in dollar GEX rose about 2.5 times, about
   44% per 252-session window. The percentile against a trailing year partly measures where the
   index is relative to its own past year, and Stage 1 has no trend control. Re-check, report only:
   percentile of net GEX divided by S0^2 (gamma notional in index units) and Stage 1 with year
   dummies. Expect the sign to hold; the size may move either way.

4. **Open-interest gamma misses intraday 0DTE positioning.** SPEC defers this to phase two, and the
   literature now says the morning-OI 0DTE gamma does not propagate volatility (Dim, Eraker, Vilkov).
   This is a reason to keep the regime at the session scale and to condition at the prior close,
   where the positioning is what the OI says it is, rather than at a band touch three hours into the
   day, when it is not. Study 3's finding that "high-gamma sessions that reach the band are trend
   days" is consistent with the morning-OI regime being stale by then.

### 3c. Small asymmetries (none reverse a result)

- Study 3 fade entries fill when a bar's high merely equals the band (`hi >= B`), then pay a tick.
  SPEC rule 5 applied strictly would require a print a tick through the band for a resting limit.
  This is slightly generous to the fade, which lost anyway.
- Study 3 drops 82 sessions already beyond the band at 10:00. Those are the strongest trend days,
  excluded from the breakout variant. A variant that enters on the cross whenever it happens was not
  pre-registered and would need to be, but the regime contrast was negative either way.
- Stage 2's G x gex_pct interaction (p = 0.044) appeared after the labeler fix; it is a marginal,
  post-fix interaction and should be read as exploratory. Nothing was built on it.

## 4. Why the level thesis is closed: signal versus friction

| study | trade | risk per trade | pre-cost signal vs driftless | friction | after cost |
|---|---|---|---|---|---|
| 1 (Stage 3) | confirmed fade after reclaim | 11 ticks median | -0.08R (-0.9 ticks) | 0.24R | -0.33R (n 446) |
| 1 (naive) | limit at the level | 6 ticks | +0.10R (+0.6 ticks) | 0.38R | -0.29R (n 1,048) |
| 2 (S2) | absorbed-flow fade, limit at level | ~6 ticks | -0.10R (-0.6 ticks) | 0.38R | -0.48R (n 254) |
| 3 (R1) | 0.5 EM band fade, high gamma | 28-43 ticks | -0.11R | 0.07R | -0.24R (n 207) |
| 3 (R2) | 0.5 EM band breakout, low gamma | 28-43 ticks | +0.03R | 0.07R | -0.06R (n 190) |

Two things are true at once. At tick scale the only signal is the 0.6-tick bounce, a quarter of the
friction. At session scale the directional expression (study 3) has no signal at all, because
reaching the band selects the trend days, so the regime filter selects against itself. Every nudge
(44 of 44), year, tercile and time bucket is negative. More variants of the same family (other
thresholds, other entries, MBP-10 absorption) would be fitting noise, and would push the count past
20, which SPEC says then requires an NQ replication. Do not spend on the order book for this thesis.

## 5. Where the regime signal lives, and the next trade expression

The Stage 1 result is a statement about realised range versus the option-implied move: high-gamma
sessions realise less of the EM. That is a volatility statement. For a driftless path at implied
vol, expected range over the RTH window divided by the straddle is about 2.0 times the square root of
the RTH share of quote-to-settlement variance, which at a 60-70% share is 1.55 to 1.67. The observed
mean range ratio of 1.54 to 1.66 says realised is close to implied in this sample: the unconditional
0DTE straddle is roughly fairly priced here, which matches the recent literature that the 0DTE
variance premium is real but modest and eaten by costs. So the unconditional short straddle is not
the trade. The regime contrast is the trade, if anything is.

The direct expression is to sell the thing whose price is the EM, on the sessions where the EM is
too high, and buy it where the EM is too low, without a directional view, a level, or an intraday
entry. Everything needed is on disk.

### Study 4 (proposed, not run): regime-conditioned 0DTE straddle at the prior close

**Hypothesis H4.** Conditional on the prior session's gamma regime, the D-expiring SPXW ATM
straddle sold at the D-1 close and held to settlement has positive after-cost expectancy on
high-gamma sessions, and the long straddle has positive expectancy on low-gamma sessions, with the
high-minus-low contrast positive and larger than a shuffled regime produces.

**Why it is different from studies 1 to 3.** No level, no entry timing, no intraday path dependence,
no ES execution. The payoff is |settle - K| against premium, which is what "tighter range" and "range
expansion" cash out to. Friction is a few tenths of an SPX point against a regime effect of one to
three points (table below), the inverse of the tick-scale ratio.

**Data, all on disk, no spend.**
- Entry price: the D-1 17:00 ET EOD report for the D-expiring SPXW, strike nearest the forward F
  (the same contract the engine already uses for EM). Short straddle sells at bid_C + bid_P; long
  straddle buys at ask_C + ask_P. The spread is observed, not assumed.
- Settlement: SPXW is PM-settled to the SPX close on D; use the FRED SPX close already in
  `data/raw/daily`. Drop SPX AM-settled monthlies (already dropped), sessions with no GEX row,
  half-day sessions, and the one stale-expiry session.
- Regime: `gex_pct` of session **D-1**, not D. The D row uses OI published the morning of D, which
  is not knowable at 17:00 on D-1. The percentile is slow-moving, so the lag costs little; report the
  D-row version as a non-tradeable diagnostic only.
- Costs: a per-leg commission plus fees parameter (`opt_cost_per_leg_usd`, value to confirm with the
  broker; $1.50 placeholder) charged on entry legs only (cash settlement has no exit commission), plus
  the quoted half-spread already paid by trading at the bid or ask.

**Units.** P&L per straddle in SPX points, divided by EM for the session (so each day is in
expected-move units), and also in dollars at $100 per point. Gate on the EM-unit expectancy.

**Variants (3 of the remaining 8; count would go to 15 of 20).**
- S4a short straddle, sessions with gex_pct(D-1) >= 0.5 (reuse `regime_threshold`).
- S4b long straddle, sessions with gex_pct(D-1) < 0.5.
- S4c iron fly, S4a with long wings one EM either side of K (defined risk; wing prices from the same
  report, bought at the ask). This is the version anyone would actually hold overnight.

**Contrasts and placebo.** The same trade on all sessions (the variance premium baseline) and on the
complement regime; a 1,000-draw permutation of gex_pct across sessions as in study 3; and the Stage 1
regression re-run with the straddle P&L in EM units as the outcome, ln VIX and day-of-week as
controls (the tradeable restatement of the range-ratio result).

**Gates (per variant, frozen before the run).** n >= 200 sessions in regime; after-cost expectancy
>= `s4_min_expectancy_em` (proposed 0.03 EM, about one SPX point, two to four times the friction);
day-bootstrap 90% CI lower bound > 0; regime contrast (variant minus complement) CI lower > 0;
permutation p < 0.05. Plus a tail report that is not a gate but must be in the write-up: the five
worst days, their share of total P&L, max drawdown, and the result with the five best days removed.
With 2.5 years of data the tail is under-sampled; S4c is the variant that should carry the decision.

**Rule-6 checks pre-committed.** EM, bid, ask and gex_pct(D-1) are all stamped before 17:00 on D-1;
the strike is chosen from the D-1 report; the settlement is the official close, never an intraday
print; no holdout session is loaded (the holdout, 2026-01-01 onward, is still pristine and can serve
as the holdout for this new hypothesis); half days excluded; check that no session is double counted
when SPX and SPXW both expire.

**Expected economics (assumptions to be replaced by the data).**

| quantity | value | basis |
|---|---|---|
| ATM 0DTE straddle at the D-1 close | 30 to 45 SPX points | EM/S0 is 0.75 x VIX/100/sqrt(252) in Gate 0 |
| regime effect on realised move | about 7.5% of the straddle | Stage 1 quintile gap, applied to |move| |
| that effect in points, top vs bottom quintile | 2 to 3 points per straddle | 7.5% of 30 to 45 |
| half-spread, two legs, curb session | 0.2 to 0.5 points | to be read from the EOD bid and ask |
| commissions and fees, two legs | 0.03 to 0.04 points | $3 to $4 per straddle at $100 per point |

The ratio of effect to friction is roughly four to ten, versus a quarter for the level trades. That
is the whole reason to run it. Note the honest risk: Stage 1 measured range, the straddle pays on
the close-to-close move, and the variance ratio and efficiency ratio were not significant, so a
compressed range need not mean a compressed terminal move. The test is informative either way.

**New parameters needing approval (CLAUDE.md rule 3).** `opt_cost_per_leg_usd` (1.50, nudge 3.00),
`s4_wing_em` (1.0, nudges 0.75, 1.5), `s4_min_expectancy_em` (0.03, no nudge: gate), `s4_regime_lag`
(1 session, no nudge: point in time). `regime_threshold` 0.5 reused.

**Tests first.** Straddle P&L on a hand-built chain (settle inside, at, beyond the strike; both
sides; wings); strike selection nearest F; the D-1 regime lag; the EM-unit conversion; the
permutation test on synthetic frames (already exists); a sealed-holdout refusal.

**Kill rule.** No variant passes, or S4a passes but S4c does not and the five worst days carry the
sign: stop, write up, keep the regime finding as a description. Pass: nudges and splits, then the
holdout once.

### Study 5 (secondary, also zero spend): the hedging-flow trade into the close

The published directional expression of the same mechanism is not a level fade. Baltussen, Da,
Lammers and Martens (JFE 2021) show that the last 30 minutes' return is predicted by the day-so-far
return, and that the effect is driven by gamma hedging demand: dealers short gamma must buy into a
rally and sell into a decline near the close. Barbon and Buraschi find intraday momentum when the
dealer gamma imbalance is negative and reversal when it is positive. Neither sample includes the 0DTE
era, so a replication on 2023 to 2025 with this project's regime variable is a fair, cheap test:
at 15:30 ET go with the 09:30-to-15:30 ES move on low-gamma sessions (gex_pct < 0.5 or open below
the flip) and against it on high-gamma sessions, exit at 15:55, risk in EM units with a 0.25 EM stop
that is rarely hit in 25 minutes, so friction is well under 0.1R. One trade a session, 1-minute bars,
the study 3 permutation placebo. Two variants, which would bring the count to 17 of 20. Its weakness
is that the published effect may have decayed, and that Dim, Eraker and Vilkov find 0DTE gamma does
not propagate volatility; that is exactly what the test would say.

### Not recommended

- Any further variant of a level touch, reclaim, absorption or band trade, at any threshold.
- Buying MBP-10 order-book data for absorption; the tape results do not justify it.
- Tuning `regime_threshold`, `band_a`, or any Stage 3 parameter on the in-sample data.
- Opening the holdout for anything from studies 1 to 3.

## 6. Order of work if Study 4 is approved

1. Report-only re-checks of the regime variable (section 3b items 1 to 3): three sign conventions,
   business-time clock, S0^2-normalised percentile, year dummies. One RUNLOG entry, no decisions.
2. Freeze the Study 4 pre-registration with the chosen regime definition and the four new
   parameters, then write the tests, then `src/study4.py`, then run once.
3. Report against the gates and stop. The gate call is Matteo's.

## Sources

- Baltussen, Da, Lammers, Martens, "Hedging Demand and Market Intraday Momentum", JFE 2021:
  https://www3.nd.edu/~zda/intramom.pdf and https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/
- Barbon, Buraschi, "Gamma Fragility": https://abarbon.com/papers/gamma-fragility
- Dim, Eraker, Vilkov, "0DTEs: Trading, Gamma Risk and Volatility Propagation" (summary):
  https://harbourfrontquant.substack.com/p/impact-of-zero-dte-options-on-the
- Beckmeyer, Branger, Gayda, "Retail Traders Love 0DTE Options... But Should They?" (summary):
  https://www.cxoadvisory.com/individual-investing/retail-0dte-option-trader-performance
- On the calls-positive, puts-negative convention being an inventory assumption and on flow-signed
  alternatives: https://flashalpha.com/articles/flow-signed-gex-polarity-dealers-long-or-short-gamma
  and https://spotgamma.com/gamma-exposure-gex/
- 0DTE flow roughly balanced between buyers and sellers (Cboe research, as summarised):
  https://www.luxalgo.com/library/concept/0dte-flow-effects/
- 0DTE straddle overpricing is modest and stable across regimes (193 SPY sessions, 2022-2026):
  https://flashalpha.com/articles/are-0dte-straddles-overpriced-193-spy-sessions-data-study
- Cboe curb session 16:15 to 17:00 ET, SPX and SPXW only: https://www.sec.gov/file/34-93819
