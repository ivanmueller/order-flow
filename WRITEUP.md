# Gamma + Order Flow Edge Test: what was learned

Period: in-sample 2023-06-02 to 2025-12-31 (642 sessions with GEX, 502 with a lagged regime and a
0DTE straddle, ~620 ES and NQ close sessions, 110 sampled sessions with tick trades). Holdout 2026-01-01
to 2026-09-30: opened once, for the study 5f fade on ES only; untouched for every other test and market.
Data spend: about $85 of the $125 Databento credit (~$98 once the Study 7 bars land); ThetaData free tier.
Every run, config change and gate decision is in RUNLOG.md. The prompt for choosing the next study is
NEXT.md.

## Verdicts against the pre-registered rules

| gate | question | result | verdict |
|---|---|---|---|
| 0 | Is the GEX engine right? | S0 within 5 pts of ES at 17:00 minus basis on 96.7% of days; put vol > call vol 99.4%; EM vs VIX corr 0.87 | pass |
| 1 | Does dealer gamma predict a quieter session beyond VIX? | range-ratio beta -0.216, p=0.029, right sign; top vs bottom quintile gap 7.5% vs the 15% bar | kill |
| 2 | Do gamma levels hold more than random levels? | after the labeler fix: placebo 46.5%, gamma-only 45.4%, both 44.4%, structural 42.8%; gamma x regime interaction p=0.044 | not a kill; no group beats placebo |
| 3 | Does flow confirmation at a level pay after costs? | confirmed -0.33R on 446 trades, naive -0.29R, placebo -0.30R; all 44 nudges and every split negative | kill |
| study 2 | Do at-level tape features and at-level entries pay? | S2 -0.48R (254), S2r -0.31R (95), S2c -0.45R (77), S2h -0.54R (254); no variant above a driftless walk by 0.15R | kill |
| study 3 | Does the gamma regime pay as a session-scale band trade? | fade in high gamma -0.24R (207), breakout in low gamma -0.06R (190), fade above flip -0.22R (204); every regime contrast negative, permutation p > 0.9 | kill |
| study 4 | Does the gamma regime pay as a 0DTE straddle sold or bought at the prior close? | short straddle in high gamma +0.059 EM (266, CI -0.02..+0.14, contrast CI > 0, perm p 0.035); long straddle in low gamma +0.033 EM (236, CI fails); iron fly in high gamma -0.010 EM (265) | kill |
| study 5 | Does the rest-of-day move continue into the ES close (last 30 minutes)? | S5a -0.037 EM_V (CI -0.054..-0.020), S5b -0.031; timing contrast CI below zero: the close reverses the day (slope t -2.3) | kill |
| study 5f | Does fading that move pay (S5b reversed), out of sample? | in sample +0.008 EM_V; holdout 2026-01..09 +0.0145 (184 sessions, CI -0.006..+0.036, contrast CI > 0); nudges 7 of 8 positive; NQ +0.024 (CI +0.004..+0.044) | holdout gate pass; edge about one tick wide; paper trading next |
| study 6 | Does pure aggressor imbalance predict ES at 5-15 minutes? | net -0.49 to -1.19 points a trade on all four variants; gross at most +0.09 points; imbalance-return corr 0.009 / 0.020 | kill |
| study 7 | Does last-30-minute momentum into settlement pay in CL, GC, ZN, 6E? | running (RUNLOG) | pending |

## What held up

- Dealer gamma is a real regime variable. High-gamma sessions range less, with VIX in the model, and
  every kind of level, random ones included, holds more often on those days. The effect is about half
  the size that would have justified building on it alone.
- In negative gamma, gamma-tagged strikes break more often than random levels (40% vs 46% hold).
- The regime shows up in a tradeable payoff once, in study 4: the D-expiring straddle sold at the D-1
  close pays +0.059 EM per session when the prior session's gamma percentile is above 0.5 and loses
  -0.071 EM when it is below; the long straddle mirrors it (+0.033 vs -0.099). Both contrasts clear
  zero and beat a shuffled regime (p 0.035). Friction is 0.02 EM against a 0.13 EM contrast. It fails
  the gate on variance: 0.77 EM per-session scatter puts the mean 1.2 standard errors from zero, and
  about 466 high-gamma sessions would be needed. The effect is "low gamma is bad for short volatility"
  (terciles -0.10 / +0.08 / +0.01), not "high gamma is good", and the defined-risk iron fly has no edge.
- The 0DTE variance premium over the 23-hour hold is zero in this sample (short straddle on all
  sessions -0.002 EM).
- The ES close reverses the rest of the day in 2023-25 (study 5), the opposite of the published
  pre-2020 momentum and consistent with the 0DTE era. Fading it (study 5f) passed its holdout gate on
  ES, its nudges and an NQ replication. The timing is robust; the money is not: the edge is about one
  tick a trade on ES, gone with one more tick of slippage or a 15:55 exit. NQ looks better after costs
  mainly because its tick is a smaller share of its range, and it shares four of its five best days
  with ES, so it is a confirmation of the pattern, not a second sample.
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
- Pure aggressor imbalance carries no direction at 5 to 15 minutes (study 6): continuation, absorption
  and pressure-reversal readings all lose, and the deciles of imbalance are flat against the next move.
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

## Where the regime lead went (studies 3 and 4)

The session-scale version was tested: fade the half-expected-move band toward the open on high-gamma
sessions, trade the breakout on low-gamma sessions, one trade a day, 28 to 43 ticks of risk so
friction was about 0.1R. It failed in the direction opposite to the hypothesis. Conditional on
reaching the band, high-gamma sessions trend (fade -0.28R in the top tercile, breakout +0.10R) and
low-gamma sessions do not (fade +0.01R, breakout -0.06R). The unconditional Stage 1 finding, that
high-gamma sessions range less, is consistent with this: on most high-gamma days the band is never
reached, and the ones that reach it are the trend days.

Study 4 then expressed the regime as a volatility position instead of a direction (REVIEW.md section
5): sell the 0DTE straddle at the prior close on high-gamma sessions, buy it on low-gamma sessions, hold
to settlement. That is the first expression where the sign, the contrast and the placebo all line up,
and it still fails the pre-registered interval gate because the per-session scatter is twelve times
the mean. Nothing in the four studies converts the regime into a trade that clears its gate. True
absorption needs the order book (MBP-10), which was never bought; the tape-only results do not justify
buying it for the intraday level thesis.

## The common thread, and what practitioners would say

Every intraday idea that found any pattern lost it to friction: a fraction of a tick of edge against
2-3 ticks of cost. The systematic traders whose records lasted (Seykota, Donchian, Dennis and the
Turtles, Saidenberg across horizons, Larry Williams and Toby Crabel on daily bars) mostly traded at
horizons where the move is many times the cost, and spread one rule over many weakly correlated markets.
Those are priors, not evidence (survivorship, post-publication decay), but they point the same way as
the measurements here. NEXT.md asks the next study to say where it sits on horizon and diversification.

## Where everything is

| what | file |
|---|---|
| Design, formulas and gate rules for modules 1-3 and stages 1-3, the parameter registry | SPEC.md |
| Pre-registrations with formulas for studies 2-7 (tape features, band trades, straddles, close momentum and the fade, pure order flow, cross-market momentum), every run with config diffs, every gate decision, the per-family variant rule | RUNLOG.md |
| The prompt for choosing the next study (current findings, practitioner priors, data and budget) | NEXT.md |
| One entry per backtest: hypothesis, build, headline, verdict | RESULTS.md |
| Lessons and verdicts (this file) | WRITEUP.md |
| Code and log audit of studies 1-3, modeling caveats, the study 4 and study 5 proposals | REVIEW.md |
| Data inventory (what each table supports, its stamp, its gaps, cheap pulls), 15 candidate studies scored, the ranking behind study 5 | CANDIDATES.md |
| Every tunable value, with nudges | config.yaml |
| Market overlays: NQ replication, Study 7 markets (data root, symbol, tick, point value, settlement clock) | config.nq.yaml, config.cl/gc/zn/6e.yaml |
| Runbook, guard rails, decisions that needed sign-off | README.md |
| Working rules, data conventions, status block | CLAUDE.md |
| Formulas as implemented: module docstrings in src/gex.py, src/levels.py, src/touches.py, src/flow.py, src/sim.py, src/regime.py, src/study3.py, src/study4.py, src/study5.py, src/study6.py, src/study7.py, src/price_menu.py, src/stats.py | src/ |
| Hand-verified tests of every formula | tests/ |
| Data sources: ThetaData EOD quotes (free tier, 17:00 ET curb close), Databento OPRA open interest, Databento ES/NQ/CL/GC/ZN/6E bars and ES trades, FRED SPX and VIX closes; all under data/ and data_<mkt>/ (git-ignored), schemas in SPEC.md "Storage schemas"; spend ledger data/spend_ledger.csv | data/ |
