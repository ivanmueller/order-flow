# Gamma + Order Flow Edge Test: what was learned

Period: in-sample 2023-06-02 to 2025-12-31 (642 sessions with GEX, 110 sampled sessions with tick
trades). Holdout 2026-01-01 to 2026-09-30 never opened. Data spend: about $81 of the $125 Databento
credit; ThetaData free tier. Every run, config change and gate decision is in RUNLOG.md.

## Verdicts against the pre-registered rules

| gate | question | result | verdict |
|---|---|---|---|
| 0 | Is the GEX engine right? | S0 within 5 pts of ES at 17:00 minus basis on 96.7% of days; put vol > call vol 99.4%; EM vs VIX corr 0.87 | pass |
| 1 | Does dealer gamma predict a quieter session beyond VIX? | range-ratio beta -0.216, p=0.029, right sign; top vs bottom quintile gap 7.5% vs the 15% bar | kill |
| 2 | Do gamma levels hold more than random levels? | after the labeler fix: placebo 46.5%, gamma-only 45.4%, both 44.4%, structural 42.8%; gamma x regime interaction p=0.044 | not a kill; no group beats placebo |
| 3 | Does flow confirmation at a level pay after costs? | confirmed -0.33R on 446 trades, naive -0.29R, placebo -0.30R; all 44 nudges and every split negative | kill |
| study 2 | Do at-level tape features and at-level entries pay? | S2 -0.48R (254), S2r -0.31R (95), S2c -0.45R (77), S2h -0.54R (254); no variant above a driftless walk by 0.15R | kill |

## What held up

- Dealer gamma is a real regime variable. High-gamma sessions range less, with VIX in the model, and
  every kind of level, random ones included, holds more often on those days. The effect is about half
  the size that would have justified building on it alone.
- In negative gamma, gamma-tagged strikes break more often than random levels (40% vs 46% hold).
- Touched prices revert a little at the one-minute scale: hold rates of 43 to 47% against a 33%
  driftless baseline, and naive fades win 35% against a 31% baseline. That is worth about 0.6 ticks
  per trade.

## What did not

- Gamma strikes are not better levels than random prices near the open.
- Tape-only absorption (aggressive volume per tick of penetration, delta at the level, big lots,
  tape speed) does not select reversals. Confirmed fades do worse than unconfirmed ones; at-level
  absorption selection does worse than a random walk by about 0.6 ticks. On this tape, heavy flow into
  a level is followed by continuation more often than reversal within 30 to 60 minutes.
- Entering at the level instead of after the reclaim, a longer horizon, and the retest-fail
  continuation in low gamma do not change that.
- Execution dominates at this size. With 6 to 11 ticks of risk, a tick of slippage, a tick through on
  the stop, a tick beyond on the target and a $3.98 round trip cost 0.24 to 0.38R per trade, four
  times the pre-cost signal.

## Bugs found and fixed on the way

- Stage 2 label counted the touch bar's own range as the reversal (SPEC said scan from bar t
  inclusive); hold rates were 57-61% before the fix, 43-47% after. The placebo comparison was
  unaffected.
- ThetaData half-day end-of-day reports are all zero bids (six sessions have no GEX row); one normal
  day (2024-12-02) had zero-bid dailies, which inflated the next session's expected move; levels now
  skip such sessions.
- A column collision crashed the Stage 3 secondary regression; the synthetic test environment produced
  no confirmed trades, so that path was untested. Fixed and covered.
- My first simulator-fairness baseline ignored that every entry print sits one tick inside the entry
  price; the corrected formula is verified on a synthetic driftless walk.

## Where the surviving lead points

The regime effect is daily, not intraday: it says how much a session will range and whether levels
of any kind hold, not where to enter. A study built on that would trade session-level range or
volatility conditioned on dealer gamma, at a horizon where 2 ticks of friction do not matter. It is a
different hypothesis and would need its own pre-registration. True absorption needs the order book
(MBP-10), which this study never bought; the tape-only result does not justify buying it for the
intraday level thesis.
