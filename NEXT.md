# Next study: prompt for a fresh session

Paste the block below into a new session on this repository. It is a pivot away from the gamma
regime and the level thesis (both closed, see WRITEUP.md) to the one option anomaly with the strongest
documented prior that this data can test at zero cost: the variance risk premium at the one-week to
one-month horizon, timed by the front-end term-structure slope. A KILL is an acceptable outcome; the
prompt forbids adding variants to rescue it.

---

Read CLAUDE.md, WRITEUP.md ("Where everything is"), RESULTS.md, REVIEW.md section 3b and the last four
RUNLOG.md entries before doing anything. Studies 1 to 4 are closed: the intraday level and flow thesis
has no edge, and the dealer-gamma regime is real but too weak to clear a gate. Do not reopen them, do
not add variants to them, and do not touch the holdout.

Pre-register Study 6 (study 5, the hedging-flow trade, stays an unrun draft) and stop for my approval
before writing code. The study uses only data already on disk: the D-1 EOD option chains (SPXW and
SPX, up to 45 days to expiry, 17:00 ET curb-close quotes), the engine's per-expiry forwards and implied
vols in data/derived/gex_vols, the FRED SPX and VIX closes, and gex_daily for the secondary split. Zero
Databento spend.

Hypothesis H6. Short-dated SPX variance beyond the 0DTE horizon is overpriced on average, and the
front-end term-structure slope tells you when: the ATM straddle with about 7 and about 30 calendar
days to expiry, sold at the D-1 close and held to settlement, has positive after-cost expectancy when
the slope ln(IV_30 / IV_0) is in contango (above its trailing-252-session median, point in time) and
non-positive when the front end is inverted. Study 4 found the 0DTE premium is zero in this sample;
this tests whether it exists one step out the curve, which is where the literature puts it.

Build rules, all stamped at 17:00 ET on D-1:
- IV_tau for tau in {0, 7, 30} days: the engine's ATM implied vol (strike nearest the forward) on the
  expiry nearest tau, linearly interpolated in total variance between the two bracketing expiries;
  the slope is ln(IV_30 / IV_0). Record the raw expiries used.
- Positions, one per session: the straddle on the SPXW expiry nearest 7 days (W7) and nearest 30
  days (M30), strike nearest the forward with both legs valid; short at the bids, long at the asks;
  SPX AM-settled monthlies may be used for M30 only with settlement at the SPX open print of the
  expiration day, otherwise SPXW PM-settled at the FRED close. Per-leg fees opt_cost_per_leg_usd.
  Defined-risk variant: iron fly with wings at one implied move of the position's own horizon.
- P&L in units of the straddle premium (so horizons are comparable) and in SPX points and dollars.
  Positions overlap (a new one every session with 7 or 30 days to run), so every interval uses a
  block bootstrap with blocks of at least the holding horizon, and one non-overlapping ladder
  (entries every 7th or 30th session) is reported alongside as the honest sample size.
- Regime of the slope: contango if the slope is above its trailing-252-session median (minimum 126),
  computed from prior sessions only. The dealer-gamma percentile of D-1 is a secondary split, reported,
  not gated.

Variants (count them; 15 of 20 are used): S6a short W7 straddle in contango; S6b short M30 straddle in
contango; S6c the defined-risk iron fly version of whichever of S6a or S6b is better on the
pre-registered primary metric (declare the rule now, not after). Contrasts, not gated: each position on
all sessions (the unconditional premium), in inversion, by slope tercile, and by gamma tercile.
Placebo: a block permutation of the slope regime across sessions (block of 21), 1,000 draws.

Gates per variant, frozen before the run: at least 150 non-overlapping positions in regime; after-cost
expectancy at least 0.10 of the premium; block-bootstrap 90% interval lower bound above zero; regime
contrast interval lower bound above zero; permutation p below 0.05. Tail block as in study 4 (five
worst and best positions, the mean without the best five, max drawdown); the iron fly must also be
positive for a pass. Kill: anything less. No nudging, no new variants, no holdout unless I say so.

Rule-6 checks to pre-commit: every input stamped on or before 17:00 D-1; settlement is the official
print, never an intraday price; no overlapping-position double counting in the non-overlapping ladder;
expiries within 45 days only; the six zero-bid half-day reports and 2024-12-02 skipped; quote sanity
as in study 4 (credit never above the structure's maximum); the slope median uses prior sessions only.

New parameters to propose for config.yaml with nudges, for my approval: horizons 7 and 30 (nudges 5/10
and 21/45), slope lookback 252 (126, 504), wing width one implied move (0.75, 1.5), the
expectancy gate 0.10 of premium, block length 21. Reuse opt_cost_per_leg_usd, perm_draws,
bootstrap_draws, bootstrap_seed, ci_level.

Work order: pre-registration in RUNLOG.md and stop; after approval, tests first with hand-verified
answers (total-variance interpolation, slope, the non-overlapping ladder, the block bootstrap, both
settlement conventions, the premium-unit P&L), then src/study6.py, then one run, then the report against
the gates and stop. The gate call is mine.
