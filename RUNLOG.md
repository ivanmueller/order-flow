# Run log

Every analysis run goes here, including failed and discarded variants: date, git commit, config hash,
what changed, config diff, and the headline result. Count of variants so far: 0
(if this passes 20, the NQ replication must also be positive before a Go).

Append with `from src.runlog import log_run`.

## 2026-10-05 | config: pilot overlay created (not a run)
- change: added `config.pilot.yaml` (extends config.yaml) for a 3-month pilot, Mar-May 2025, approved by Matteo.
- config diff vs main: sample.start 2025-03-03, sample.end 2025-05-30, data.derived_dir derived_pilot,
  gex_pct_min_periods 252 -> 20. Main config.yaml unchanged.
- result: n/a. Pilot results are a pipeline check and an early read, not gate decisions.

## 2026-10-04 | pilot: first GEX build on real data (Gate 0, partial)
- commit: 630b8c5  config: config.pilot.yaml (Mar-May 2025)
- change: first real-data run. ES bars Feb-May 2025 ($0.42), ThetaData EOD Feb 28-May 30 (free),
  OPRA OI Mar 3-Apr 16 ($0.56; pull died on a Databento 504 at Apr 17, retries added afterwards).
  Roll-basis pull not yet done (1 day, priced $0.00).
- config diff: none vs pilot overlay.
- result (33 days, Mar 3-Apr 16 2025): check 1 FAIL as written (36% of days with |S0 - SPX close| <= 5 pts,
  median 5.8 pts; suspected 16:00 cash close vs 16:15 option close timing, check 1b added to test that);
  check 2 PASS (put vol > call vol on 100% of days, smile smooth); check 4 PASS (corr EM/S0 vs VIX 0.97,
  ratio 0.75); check 3 (public chart comparison) not yet done. Net GEX negative on 32 of 33 days
  (-$2bn to -$77bn per 1%), flip missing on 36% of days (no zero crossing on the +/-5% grid in a deeply
  negative-gamma regime). Not a gate decision: partial pilot sample.

## 2026-10-05 | pilot: GEX build, 59 days (Gate 0, still partial)
- commit: 4c454dd  config: config.pilot.yaml
- change: OI pull resumed Apr 17-May 23 ($0.41), then crashed on 2025-05-26 (Memorial Day: ES traded,
  OPRA closed -> Databento 422 "no symbols"). Root cause: the ES calendar included ES-only holiday sessions.
  Fixed afterwards (equity_session flag from FRED SPX dates; prev_date and roll step over holidays).
- config diff: none.
- result (59 days, Mar 3-May 23 2025): checks 2 and 4 pass (put>call vol 100%, corr EM vs VIX 0.95).
  Check 1 fails as written (44% within 5 pts, median 5.4). Check 1b vs ES at 16:15 minus basis also fails
  (median |resid| 4.4, 22% within 2 pts), and the big residuals line up with known after-hours news
  (Apr 2 tariff announcement 16:25 ET: resid -170; May 16 Moody's downgrade ~16:50 ET: resid -25;
  Mar 4 Lutnick tariff-relief comments after the close: +17). Hypothesis: the ThetaData EOD quotes are the
  17:00 ET Cboe curb-session close, not 16:15. A quote-time scan (16:00/16:15/16:45/17:00) was added to
  --diagnose to test it. Net GEX negative on 81% of days, median -$24bn/1%, turning positive from
  May 12 (consistent with the post-tariff-pause regime); flip missing 27%. Not a gate decision.

## 2026-10-05 | pilot: GEX build complete, 63 days (Gate 0 checks 1, 2, 4)
- commit: c7ba2a7  config: config.pilot.yaml
- change: calendar rebuilt with equity sessions (2 ES-only holidays excluded: 2025-02-17, 2025-05-26);
  OI pull completed May 27-30 ($0.06). Total Databento spend so far ~$1.50.
- config diff: none.
- result (63 days, Mar 3-May 30 2025): quote-time scan of S0 vs ES-at-t minus basis, median |resid| in pts:
  16:00 5.60 (19% within 2) | 16:15 4.25 (23%) | 16:45 2.84 (37%) | 17:00 1.95 (53%, 97% within 5).
  The EOD quotes are the 17:00 ET Cboe curb close. Against that reference the forward fit passes the
  SPEC's check 1 standard (within 5 pts on 97% of days); the after-hours news outliers vanish
  (Apr 2 tariff day: -170 at 16:15 -> -1.2 at 17:00; May 16 Moody's: -25 -> -1.3).
  Checks 2 and 4 pass (put>call vol 100%; corr EM vs VIX 0.95). Net GEX negative 78% of days, median -$21bn.
  Proposed: market.quote_time 16:15 -> 17:00 (awaiting approval); check 1 now reads S0 against ES at
  quote_time minus basis (cash-close version kept as information). Check 3 (public chart) pending.

## 2026-10-05 | pilot: levels, touches, Stage 2 and Stage 1 reports (pipeline check, not gate calls)
- commit: 3d193f2  config: config.pilot.yaml (quote_time still 16:15; 17:00 proposal awaiting approval)
- change: first real-data run of levels -> touches -> analysis stage2 / stage1 on the 63-day pilot.
- config diff: none.
- levels: 708 (gamma_only 105, both 63, structural_only 225, placebo 315). touches: 1357, success 54% in
  every group (both 54.1, gamma_only 53.7, structural 53.8, placebo 55.4), timeouts 0%.
- Stage 2 (930 touches on the 43 days with a GEX percentile): no coefficient significant; G -0.20 (p .79),
  G:gex_pct +0.33 (p .72). No real level group's 90% CI beats the placebo rate. keep_gamma_tags=false,
  carry structural_only + both. Direction of the interaction (gamma levels hold more in high-GEX terciles:
  gamma_only 61% high vs 50% low) matches the hypothesis but is far from significant on this sample.
- Stage 1 (43 days, Mar 31-May 30): RR beta -0.76 (p 3e-5, R2 .37), Q1 vs Q5 gap 35%; ER beta -0.038
  (p .02); VR beta +0.07 (p .59, wrong sign) -> "KILL" by the literal rule. Rule-6 caveat: the sample is a
  single crash-and-recovery regime; the GEX percentile (20-48 days of history) is nearly a time trend and
  is confounded with April's realised-vs-implied blowout, so the RR p-value is not credible. Checked: no
  lookahead (percentile, EM, GEX from prior sessions; RR from D's bars), no roll day in the Stage 1 window,
  no holdout dates. Decision deferred to the full in-sample run.

## 2026-10-05 | config: three values set with approval (not a run)
- commit: caf6603 -> this commit. Approved by Matteo in chat.
- config diff (config.yaml, inherited by the pilot overlay except where it overrides):
  market.quote_time 16:15 -> 17:00 (EOD quotes are the Cboe curb close; pilot scan RUNLOG 2026-10-05);
  params.cost_rt_usd 5.00 -> 3.98 (broker all-in round trip), nudge 7.50 -> 5.97;
  params.gex_pct_min_periods 252 -> 126 (pilot overlay keeps 20).
- Stage 3 pilot pricing: 1357 touches -> 103 merged spans, 21,562 minutes, est $32.86 ($0.0015/min).
  The merged windows cover ~340 of 390 RTH minutes per day, so Stage 3 trades cost ~$0.52/day;
  the full in-sample (~645 days) would be ~$340, beyond the $125 credit. Sampling decision pending.

## 2026-10-05 | pilot: Stage 3 trades pulled and simulated (pipeline check, not a gate call)
- commit: 517005b  config: config.pilot.yaml (quote_time 17:00, cost_rt_usd 3.98)
- change: GEX rebuilt at quote_time 17:00 (Gate 0 check 1 now PASS: 96.8% within 5 pts); levels/touches
  rebuilt (708 levels, 1357 touches, unchanged counts); ES trades pulled for all touch windows ($34.09,
  103 spans; total Databento spend ~$35.60); flow features + tick simulator run.
- config diff: none.
- --strikes 2025-04-10: top |GEX| strikes are round numbers near spot (5600 +4.9bn, 5500 -3.5, 5450 -3.4,
  5550, 5575) plus the crash-low puts (5175, 5200, 4850, 4700); calls positive, puts negative. Sane.
- Stage 3, carried groups (structural_only + both), confirmed: n=218, win rate 39.9%, avg win 1.46R,
  avg loss 1.09R, expectancy -0.07R (90% CI -0.22..+0.08), PF 0.89, max DD 29R, 77 trades/month.
  Naive baseline same groups: -0.12R (CI -0.22..-0.03). Confirmed minus naive +0.045R (CI -0.11..+0.20).
  Verdict vs rules: KILL on this sample (pilot only). By group, confirmed expectancy: gamma_only +0.00R
  (n=88), structural_only -0.03R (162), both -0.19R (56), placebo -0.23R (243, CI -0.37..-0.11).
  Real levels beat placebo after confirmation (ALL_REAL -0.05 vs placebo -0.23) but none is positive.
  Confirmation rate 40% of touches (baseline volume lags the vol spike, so AbsRatio is easy to clear).
  Skips: risk_too_wide 189, no_fill 94. Exits confirmed: 333 stop / 196 target / 20 time.
- Secondary regression: tag_pd -0.76R (p 2e-8) is the only significant term; prior-day high/low trades
  were much worse. Rule-6 note: plausible in a 63-day trend regime where prior-day extremes get run
  through; not tuned on. To be re-checked on the full run's regime splits before believing it.
- Cost finding: merged windows cover ~340 of 390 RTH minutes/day -> ~$0.54/day of ES trades. Full
  in-sample (~582 remaining days) ~$315, holdout (~190 days) ~$103; credit remaining ~$89.

## 2026-10-06 | config: Stage 3 data budget (approved: "cheapest combo that gets adequate data")
- config diff (config.yaml): params.placebo_per_day 5 -> 2 (shrinks the merged trade windows ~20%);
  new stage3_sample {seed 20231101, in_sample_days 120, holdout_days 45}: Stage 3 runs on a fixed-seed
  random subset of days stratified by year, chosen before any full-run trade data exists. Stage 2 still
  uses every day. The pilot overlay keeps placebo 5 and sampling off so its logged results stay reproducible.
- expected cost: ~120 x $0.43 + 45 x $0.43 = ~$71 of the ~$77 credit remaining after the OI pull.
- Check 3 (public chart): gex-levels.com has no 2025 history; no other free dated source found.
  Proposed to accept Gate 0 on checks 1, 2, 4 plus the qualitative regime match (negative gamma
  Mar-Apr 2025, positive from mid-May). Awaiting the user's call.

## Gate 0 decision (2026-10-06): ACCEPTED by Matteo
- Checks 1, 2, 4 pass on the pilot's real data (RUNLOG 2026-10-05). Check 3 done qualitatively: no free
  dated public GEX source for 2025 was found; the engine's sign pattern (negative gamma Mar-Apr 2025,
  positive from mid-May) matches the reported regime of that period. Will be re-checked on the full run.

## Pre-registered Stage 3 variants (approved 2026-10-06, before any full-run trade data exists)
Each counts toward the 20-variant limit. V1-V4 are reversal (fade) trades under the frozen rules in
both directions (long at support, short at resistance); only the eligibility filter changes. They run
once, after the main Stage 3, on the same sampled days. Dealer-gamma logic: long-gamma dealers sell
rallies and buy dips, so fades should work in both directions when gamma is positive and fail when it
is negative; direction does not change with the regime, eligibility does.
- V1 regime filter: confirmed fades only on days with gex_pct >= 0.5.
- V2: V1 restricted to gamma-tagged levels (is_gamma).
- V3 flip filter: confirmed fades (both directions) only when the 09:30 price is above the flip; days
  with no flip excluded. (An earlier draft had resistance trades below the flip; that was backwards.)
- V4 wall alignment: shorts only at levels tagged gamma_call_wall, longs only at levels tagged
  gamma_put_wall (gamma_top and structural tags do not qualify).
- V5 negative-gamma continuation (made unconditional 2026-10-06 at Matteo's request; built, new param
  break_ticks 4 [nudges 3, 6]): on days with gex_pct < 0.5, or with the 09:30 price below the flip, trade
  WITH the break when absorption fails: no reclaim within reclaim_window, penetration >= break_ticks
  through the level inside that window, and net aggressive flow since t0 pointing through the level.
  Entry: first trade after t0 + reclaim_window, plus 1 tick against; stop stop_buffer (2) ticks back
  inside the level; target target_mult (1.5) R; max/min risk and time exit as the confirmed trade.
  Its complement (continuation in positive gamma) is reported as a contrast, not gate-scored.
Sample-size note: V1/V3 roughly halve the trade count, V2/V4 cut further; a variant under 200 trades
is reported against the gate as INDICATIVE. `python -m src.analysis variants`.
Variant count so far: 5 of 20.

## 2026-10-06 full in-sample data pull complete (main config)
- Commit: 2ea126d. Config: config.yaml (main), no changes.
- OPRA open interest: every in-sample session 2023-06-01..2025-12-31 has files (294/504/500 per year,
  none empty). Pulled with --workers 4 over several resumed runs (504 timeouts retried, none skipped).
  Databento flagged 2024-06-03 and 2025-10-22 as "degraded" days; kept, noted here for Stage 3 checks.
- ThetaData EOD quotes: the first `--what eod` run on the main config had written nothing (Theta
  Terminal was not up); re-run 2026-10-05 22:25-23:41 ET wrote 294/504/502 files per year, none empty,
  plus the 130 pilot files already on disk. ThetaData returns 403 before 2023-06-01 (free-tier history
  starts exactly at the SPEC start), so session 2023-06-01 has no D-1 report and the GEX table starts
  2023-06-02. No config change needed. Audit: "every in-sample session has files" for both datasets.
- Spend: ledger ~$48 of $125. ThetaData free.
- Next: `python -m src.gex` (Gate 0 block re-checked on ~645 days), levels, touches, Stage 1, Stage 2.

## 2026-10-06 full in-sample run: Gate 0 re-check, Stage 1, Stage 2 (main config)
- Commit: d372131 (data), report code unchanged since 2ea126d. Config: config.yaml, no diff.
- GEX: 642 sessions 2023-06-02..2025-12-31. 7 sessions dropped as "no usable GEX": 2023-06-01 (no
  D-1 report) and the six sessions whose D-1 is a half day (2024-07-05, 2024-12-02, 2024-12-26,
  2025-07-07, 2025-12-01, 2025-12-26); cause under investigation with `gex --why`. One session with
  no levels (2025-01-27). Flip missing on 3.0% of days (whole grid negative in Aug-2024 and
  Mar/Apr-2025 selloffs). net_gex negative on 38.8% of days, median +8.0bn.
- Gate 0 on 631 days: check 1 PASS (S0 vs ES at 17:00 minus basis: 96.7% within 5 pts, median 1.5;
  scan best 17:00, as configured); check 2 PASS (put vol > call vol 99.4%, smile 2nd diff 0.0007);
  check 4 PASS (corr EM vs VIX 0.87). Gate 0 acceptance stands.
- Suspicious EM values to check before Stage 3: 2024-12-03 em=112.9 (VIX ~13 that week), also
  2024-11-06 82.4 (post-election, plausible) and 2025-10-13 107.3 (post-tariff crash, plausible).
- Stage 1 (510 days from 2023-12-01, after the 126-session percentile warm-up), Newey-West OLS with
  ln VIX and DOW: RR beta on gex_pct -0.216, p=0.029 (right sign); VR beta -0.071, p=0.11 (same
  sign, not significant); ER beta -0.007, p=0.31. RR quintile means 1.66 (Q1) -> 1.54 (Q5), gap
  7.5% vs the 15% rule. Robustness: with ln(EM/S0) control RR beta -0.399, p=0.001; dropping 17
  event days -0.219, p=0.034; 0DTE-only percentile -0.079, p=0.38.
  Verdict vs rules: KILL (significant but effect under 15%).
- Stage 2 (7,284 touches, 511 days), day-clustered logit: gamma-tag G -0.104, p=0.28 (rule needs
  positive at p<0.10); G:gex_pct +0.130, p=0.41. gex_pct main effect +0.360, p=0.0003 (all levels,
  placebo included, hold more on high-GEX days). Success: placebo 61.2%, both 58.9%, gamma_only
  58.7%, structural_only 56.8%; no real group's 90% CI beats placebo. keep_gamma_tags false;
  carry_groups structural_only, both. Verdict vs rules: not a kill; carry structural levels only.
- Gate decisions: Matteo's. Next if Stage 3 proceeds: trades pull on the 120 sampled days (~$52,
  needs approval), then `python -m src.stage3`, `analysis stage3 --carry structural_only both`,
  then the pre-registered variants (exploratory given the Stage 1 verdict).

## 2026-10-06 data-quality findings on the full GEX table (gex --why, --validate-only)
- Six post-half-day sessions (2024-07-05, 2024-12-02, 2024-12-26, 2025-07-07, 2025-12-01, 2025-12-26)
  have no GEX row: the ThetaData EOD report for a half day is all zero bids (snapshot taken after the
  13:00 close), so no surface fits. Not recoverable from the free EOD endpoint; <1% of sessions, dropped.
- 2024-12-03: the D-1 report (2024-12-02) has zero bids on every SPXW daily Dec 3-18, so S0/EM came
  from the Dec 20 expiry (em 112.9 = an 18-day straddle). `--validate-only` now counts such days
  (nearest_exp_beyond_3d_n): 1 of 642. Levels now skip sessions whose nearest expiry is more than
  MAX_NEAREST_DTE_DAYS (3) out (src/levels.py, data-quality constant, not a tunable). Levels, touches
  and Stage 2 to be re-run; the change touches one session.
- 2025-01-27 "no levels": price opened ~2% below every level (gap day), nothing inside the window.
  Correct behaviour. 2025-10-13 em 107 and S0 40 pts below the cash close: real post-crash 0DTE.

## 2026-10-06 Stage 2 re-run after the stale-expiry guard (commit 7e0a3f4 code)
- Levels: 2024-12-03 skipped (13 levels fewer: 579/1303/1280/1831 by group). Touches and Stage 2
  identical to the first run (7,284 touches, 511 days, same coefficients): that session had produced
  no touches because its inflated EM made the approach distance unreachable on a 20-pt day.
  Stage 2 verdict unchanged: G -0.104 p=0.28, no real group beats placebo, carry structural_only + both.

## Gate decisions (2026-10-06, Matteo)
- Stage 2: DEVIATION from the pre-registered carry rule, at Matteo's instruction. The Stage 3 gate is
  scored on all three real level groups (structural_only, gamma_only, both) rather than structural
  levels only, so each level type is studied independently in the order-flow test. The per-group
  table in `analysis stage3` reports each group on its own; the pooled gate uses all three.
  Rationale recorded: "measure everything"; the Stage 2 placebo result is noted, not overridden.
- Stage 3 trades pull approved: 110 sampled days, 1,573 touches, 201 spans, est $30.09, cap $34.
- Variants V1-V5 run after the main Stage 3, labelled exploratory given the Stage 1 verdict.
- Stage 1 call: pending (verdict vs rules KILL on the 15% size bar; sign and significance hold).

## 2026-10-06 Stage 3 trades pull and first Stage 3 run (main config)
- Pull: 110 sampled days, 201 spans, actual $32.57 (est $30.09, cap $34). Ledger ~$81 of $125.
  Databento flagged 2025-09-17 and 2025-11-28 as degraded days (kept).
- `src.stage3` run summary (mean pnl_r by group and mode): confirmed structural_only -0.25 (223),
  gamma_only -0.42 (146), both -0.36 (77), placebo -0.30 (121); naive -0.27/-0.35/-0.23/-0.29;
  continuation -0.40/-0.54/+0.02/-0.22.
- `analysis stage3` crashed (KeyError is_gamma): the secondary regression merged sim_trades with
  features, both carrying is_gamma, giving _x/_y suffixes. Fixed; --carry now accepts space- or
  comma-separated groups. The synthetic test env yields no confirmed trades, so a unit test with
  fabricated frames now covers that path. Full report to be re-run.
- `analysis variants` (carry all three real groups): V1 -0.27R (n=171, CI -0.39..-0.14), V2 -0.24R
  (75), V3 -0.35R (253, KILL), V4 -0.32R (66), V5 -0.36R (124); complements V1c -0.32R (175),
  V5c -0.32R (61). Win rates 31-35%. No variant or contrast is positive; regime split shows no gap.

## 2026-10-06 Stage 3 full report and scrutiny before any Stage 3 decision (commit after 7e0a3f4)
- `analysis stage3 --carry structural_only gamma_only both` (file stage3_all.json): confirmed n=446,
  win 31.8%, avg win 1.41R, avg loss 1.13R, expectancy -0.33R (CI -0.42..-0.23), profit factor 0.58;
  naive ALL_REAL n=1048 -0.29R; placebo naive -0.29R, confirmed -0.30R. conf-minus-naive -0.04R
  (CI -0.14..+0.07). Verdict vs rules: KILL (n ok; expectancy, CI and conf-naive all fail).
  Exit reasons: confirmed stop 375 / target 166 / time 26; naive 889/475/4. Skips: risk_too_wide 506,
  no_fill 205, too_late 16. Secondary regression: nothing significant (abs_ratio p=0.50).
- Scrutiny (rule 6 applied to a negative result as well): 
  (a) Simulator arithmetic is consistent: avg win 1.41R = 1.5R target minus costs; avg loss 1.13R =
      1R plus one tick beyond, slippage and costs, implying a typical R_k near 10 ticks.
  (b) Driftless-walk baseline for these barriers (stop at touch of S, target one tick beyond T) is
      about 38% wins -> about -0.15R from fills and costs alone. Observed 32-35% wins, -0.29/-0.33R:
      roughly half the loss is drag, half is a win-rate deficit that hits placebo levels too.
  (c) LABEL FLAW FOUND in Stage 2: SPEC scans "from bar t (inclusive)", so the touch bar's own
      high/low counts toward the R=0.10 EM reversal. That bar's extreme is mostly the approach before
      the touch (approach_a is also 0.10 EM), so fast approaches are labelled "held" on bar 0. Stage 2
      hold rates of 57-61% sit far above the driftless baseline F/(R+F)=33%, while the trades (which
      use tick data after the touch) win 32-35%. Stage 2's placebo comparison is unaffected (all groups
      inflated alike); its absolute hold rates and possibly the gex_pct term are. Diagnostic added to
      `analysis stage2` (label_check). Proposed fix, pending Matteo's sign-off because it changes a
      SPEC rule: favourable excursion counted from bar t+1, adverse from bar t.
- Added, diagnostics only (not variants): `mirror` trades (opposite side of every naive fill, same
  barriers/fills/costs) and a `fairness` block in the Stage 3 report (observed vs driftless win rate
  and expectancy per mode; naive+mirror sum vs twice the drag). Requires `python -m src.stage3` re-run.

## 2026-10-06 Stage 2 labeler fix (approved by Matteo, SPEC rule changed)
- touches.label: favourable excursion counted from bar t+1, adverse from bar t. SPEC.md "Labels"
  paragraph updated with the reason. Test written first (test_touch_bar_approach_does_not_count_as_reversal).
- Touches and Stage 2 must be rebuilt (`python -m src.touches`, `python -m src.analysis stage2`);
  Stage 3 trades do not use labels, but `python -m src.stage3` is re-run anyway for the mirror /
  fairness diagnostics. All free.

## 2026-10-06 Stage 2 after the labeler fix, and the simulator fairness result
- Stage 2 rebuilt (7,284 touches, 511 days): hold rates fell from 57-61% to 43-47% (placebo 46.5%,
  gamma_only 45.4%, both 44.4%, structural_only 42.8%); success_on_touch_bar_share now 0.0.
  Driftless baseline F/(R+F) = 33%, so a real but level-agnostic mean reversion of ~12 points remains.
  Logit: G -0.181 (p=0.054), G:gex_pct +0.305 (p=0.044), gex_pct +0.292 (p=0.002). keep_gamma_tags
  flips to TRUE on the interaction term. Terciles: gamma_only 39.9% (low) -> 50.8% (high); placebo
  45.7% -> 49.4%. Gamma levels break MORE than random levels in low gamma and match them in high
  gamma. No real group's 90% CI beats placebo overall. 
- Stage 3 fairness block (first version) showed naive+mirror = -0.64R vs twice-drag -0.38R. Replayed
  the simulator on a synthetic driftless tick walk: the same shortfall appears, so it is the
  barrier geometry, not the market: every entry print sits one tick inside E (naive fill one tick
  through L; slippage elsewhere), so the true driftless win rate is (R_k - tick)/(2.5 R_k + tick):
  31% at the naive/continuation R_k of 6 ticks, 35% at the confirmed 11 ticks. fairness() corrected;
  test_simulator_is_fair_on_a_driftless_tick_walk added.
- Reading the Stage 3 numbers against the corrected baseline (sim_trades unchanged):
  naive win 34.9% vs 31% baseline (+4), exp -0.29R vs -0.38R drag => market +0.10R;
  mirror 30.8% vs 33% (-2), -0.35 vs -0.32 => -0.03R;
  confirmed 32.1% vs 35% (-3), -0.32 vs -0.24 => -0.08R;
  continuation 34.2% vs 31% (+3), -0.34 vs -0.38 => +0.05R.
  Conclusion: a small pre-cost mean-reversion edge exists at touched levels (naive +0.10R over a
  random walk) but the SPEC trade rules cost 0.24-0.38R per trade at 6-11 ticks of risk (a tick of
  slippage, a tick through on the stop, a tick beyond on the target, $3.98 RT). Absorption
  confirmation does not add to it (confirmed is below its baseline). The Stage 3 KILL stands as
  pre-registered; the signal is too small for these execution rules.
- Next, all pre-registered and free: `python -m src.robustness nudges` (includes max_risk, stop_buffer,
  target_mult, time_exit nudges) and `python -m src.robustness splits`.

## 2026-10-07 robustness nudges and splits on the Stage 3 result (main config, sim_trades of 2026-10-06)
- `robustness nudges`: BASE -0.326R (446 confirmed). All 44 evaluated nudges negative (positive share
  0.00, pass=False); range -0.286 (debounce 5) to -0.422 (entry_slippage 2). max_rel_spread nudges
  skipped (need a GEX rebuild). Execution nudges: stop_buffer 1/4 -0.318/-0.316, max_risk 0.10/0.20
  -0.399/-0.319, target_mult 1.0/2.0 -0.323/-0.319, time_exit 20/45 -0.342/-0.326, cost 5.97 -0.344.
  No execution setting lets the pre-cost signal through.
- `robustness splits`: negative in every year (2023 -0.44, 2024 -0.24, 2025 -0.34), every GEX tercile
  (low -0.32, mid -0.27, high -0.29) and every time-of-day bucket (open -0.38, mid -0.30, close -0.35);
  every 90% interval below zero.
- Stage 3 verdict vs rules: KILL, robust to nudges and splits. Per SPEC: no holdout run on a Stage 3
  kill; write up what was learned. Decision is Matteo's.

## Study 2 pre-registration: tape-footprint confirmation and at-level entries (APPROVED by Matteo 2026-10-07, params approved; frozen before any run)
Status of study 1: Stage 3 KILL, robust. Pre-cost finding that motivates study 2: naive fades at
touched levels beat a driftless walk by ~+0.10R (win 34.9% vs 31%), i.e. about 0.6 ticks per trade at
a 6-tick risk, against ~2.3 ticks of friction (1 tick slippage, 1 tick stop-through, $3.98 RT).
Tape-only absorption (abs_ratio) did not improve on that. Study 2 asks whether finer tape features and
entries at the level raise the pre-cost edge enough to clear the friction. Same data (110 sampled days,
existing trades), no spend. Same holdout, sealed.

Hypotheses (all point-in-time, decision data ends at t_dec):
- H1 selection: touches where aggressive flow AT the level is absorbed carry a larger reversal edge.
  New features over the abs_window, prints within proximity_b ticks of L only:
    delta_at_level = -d * sum(side*size) at-level (positive = flow into the level being absorbed);
    at_level_share = at-level volume / window volume;
    big_lot_share  = volume in prints >= q_big / window volume (q_big new param, 50 contracts);
    tape_speed     = prints per minute in the window / baseline prints per minute (same slot, prior
                     baseline_sessions);
    delta_div      = approach_delta * d < 0 (flow against the level on the approach) while
                     break_pen < break_ticks (price did not give) -> boolean.
  Pre-registered confirmation rule S2: delta_at_level >= s2_delta_min (new param, 150 contracts) AND
  reclaim as in study 1. No other combinations will be scored.
- H2 execution: enter at the level instead of after the reclaim close. E1: at t_dec place a limit at
  L + d*entry_offset ticks (new param, 1 tick), live for fill_window (new param, 10 min), filled only
  by a print one tick through it (SPEC rule 5); stop p_ext - d*stop_buffer; target target_mult R;
  time exit time_exit from fill. No fill -> no trade (reported as a skip).
- H3 continuation in low gamma (gex_pct < 0.5 or below flip): E2 retest-fail entry. After a V5 break,
  wait for a print back within proximity_b of L from the far side within retest_window (new param,
  15 min); enter with the break direction on the first print after a 1-minute close that fails to
  reclaim L; stop L + d*stop_buffer back on the original side; target target_mult R; time exit.
Variants scored (each counts toward the 20 limit; study 1 used 5):
  S2   = S2 rule + E1, all real level groups (per-group table reported as in study 1)     [6]
  S2r  = S2 restricted to gex_pct >= 0.5                                                  [7]
  S2c  = E2 continuation on low-gamma days                                                [8]
  S2h  = S2 with target_mult 2.5 and time_exit 60 (longer horizon)                        [9]
Gates (unchanged from SPEC Stage 3): n >= 200, after-cost expectancy >= 0.10R, bootstrap CI lower > 0,
and variant minus its naive baseline > 0. Pre-cost diagnostic reported alongside: market share vs the
driftless baseline from the fairness block, in R and in ticks. A variant with market share below +0.15R
is recorded as "no pre-cost edge" even if n is short. Placebo levels run through every variant.
Kill: no variant passes -> stop; write-up. Pass -> robustness nudges/splits, then the holdout on the
passing variant only (one shot).
New config params proposed (need approval before any code runs): q_big 50, s2_delta_min 150,
entry_offset 1 tick, fill_window 10 min, retest_window 15 min. Nudges: q_big [25, 100],
s2_delta_min [100, 250], fill_window [5, 15], retest_window [10, 20].
Tests first: every new feature and both entries on synthetic prints with hand-verified answers.

Specification corrections made before any run (2026-10-07): tape_speed cannot use prior sessions
(trade data exists only for the sampled windows), so it is prints per minute in the abs_window over
prints per minute in the 10-minute approach window (point-in-time). big_lot_share and tape_speed are
measured over the whole abs_window; delta_at_level and at_level_share use prints within proximity_b
of L. E1 fills at the limit price (no slippage on a resting order; the one-tick-through rule stays).
The E1 stop extreme covers [t0, fill]. E2 evaluates the first clock minute that ends after the retest
print; a close that reclaims L means no trade (skip "reclaimed").

## 2026-10-07 Study 2 first and only run (commit d36d29e, config as approved, same 110 sampled days)
- S2 (delta_at_level >= 150 + reclaim, E1 limit entry): n=254, win 27.6%, -0.48R (CI -0.59..-0.36),
  minus naive on the same touches -0.43R; driftless baseline -0.38R -> market share -0.10R (-0.6 ticks).
  By group: structural -0.49, gamma_only -0.51, both -0.39, placebo -0.40 (placebo market share 0.00).
  Verdict KILL; no pre-cost edge.
- S2r (S2, gex_pct >= 0.5): n=95, -0.31R, market share +0.07R (+0.4 ticks) < 0.15R threshold.
  INDICATIVE; no pre-cost edge. (both group +0.46R on n=13, placebo +0.21R on n=29: noise.)
- S2c (E2 retest continuation, low gamma): n=77, -0.45R, market share -0.09R. INDICATIVE; no edge.
- S2h (S2 with 2.5R target, 60 min): n=254, win 18.5%, -0.54R, market share -0.16R. KILL.
- Fills: E1 limit filled on 254 of 475 S2-confirmed touches (184 never came back to the level
  inside 10 min, 33 risk_too_wide); E2: 174 no retest, 90 reclaimed, 177 trades.
- Reading: selecting on heavy absorbed flow at the level makes fades WORSE than a random walk (-0.6
  ticks), the same direction as study 1's confirmed-vs-naive gap. On this tape, heavy aggressive flow
  into a level is followed by continuation more often than reversal within 30-60 minutes; the
  at-level entry does not change that. The retest-fail continuation in low gamma is also no better
  than random. Study 2 verdict vs rules: KILL on every variant. Variant count 9 of 20.
- Per SPEC no-go: stop, write up (WRITEUP.md), keep the pipeline. Decision is Matteo's.

## Decision (2026-10-07, Matteo): Study 2 KILL accepted. Intraday level thesis closed (no-go, no holdout).
Rationale recorded: no variant beat a driftless walk by the pre-registered 0.15R; selection on absorbed
flow is anti-predictive at 30-60 minutes; execution friction is 4x the pre-cost signal. Not worth
adjusting: the nudges already span the execution space and every cell is negative.

## Study 3 pre-registration: session-level gamma regime (DRAFT 2026-10-07, awaiting Matteo's approval; no run before approval)
Motivation (what survived studies 1-2): high dealer gamma predicts a smaller session range beyond VIX
(Stage 1 RR beta -0.216, p=0.029; -0.399, p=0.001 with ln EM/S0), and every level type holds more on
high-gamma days (Stage 2 gex_pct +0.29, p=0.002). The effect is a property of the session, so the
trade should be a session-scale range bet in ES, at a risk size where 2-3 ticks of friction are noise.
Data: already on disk, no spend. gex_daily (EM, gex_pct, flip, S0) for 642 sessions, ES 1-minute bars
for the whole period, VIX. No tick data needed: risk is 20-40 ticks, so 1-minute bars with SPEC rule-5
fills (stop fills one tick beyond S when a bar's low/high touches it; target fills only when a bar
prints one tick beyond T; a bar that touches both is a loss) are conservative and sufficient.
Sample: the ~510 sessions with a GEX percentile (2023-12-01..2025-12-31), in-sample only. Holdout sealed.

Hypothesis H: on high-gamma sessions, price that reaches the expected-move band returns toward the
open more often than on low-gamma sessions, by enough to pay after costs.
Setup (one trade per session, the first qualifying band touch, either side):
  band:  B_up = O + a*EM, B_dn = O - a*EM, O = the 09:30 ES open (first RTH bar open), EM from gex_daily
         (D-1 close straddle, point-in-time). a = band_a (0.50 EM).
  touch: first RTH bar from band_start (10:00) to band_end (15:00) whose high >= B_up (or low <= B_dn).
  R1 range fade (high gamma, gex_pct >= 0.5): enter against the move at the band price plus one tick of
         slippage; stop s*EM beyond the band (band_stop_s 0.25 EM); target the open O (reward a*EM,
         so 2:1); time exit flat_time (15:55) at the next bar's open minus one tick.
  R2 band breakout (low gamma, gex_pct < 0.5): enter with the move at the band plus one tick; stop
         s*EM back inside; target a further a*EM beyond the band; same time exit.
  R3 = R1 restricted to sessions with the 09:30 open above the flip (the Stage 2 interaction's regime).
  Costs cost_rt_usd; R_k = s*EM (typically 7-10 pts = 28-40 ticks, friction ~0.07R).
Placebo for a regime (not a level) hypothesis: a permutation test. gex_pct is shuffled across sessions
1000 times (seed bootstrap_seed); the observed high-minus-low-gamma expectancy gap must exceed the 95th
percentile of the shuffled gaps. Also reported: the same trade on ALL sessions (no regime filter) and on
the complementary regime, as contrasts.
Gates (SPEC Stage 3 rules reused, per variant): n >= 200 trades, after-cost expectancy >= 0.10R, day-
bootstrap 90% CI lower > 0, AND the regime contrast (variant minus its complement) CI lower > 0, AND the
permutation p < 0.05. Pass -> robustness nudges and year/tercile/time splits, then the holdout once.
Kill -> stop; the regime effect is documented in WRITEUP.md but not tradable at this horizon either.
Variant count: R1, R2, R3 -> 12 of 20.
New config params proposed (need approval): band_a 0.50 EM [nudges 0.40, 0.60], band_stop_s 0.25 EM
[0.20, 0.35], band_start "10:00", band_end "15:00", regime_threshold 0.5 (gex_pct) [no nudge: it is
the pre-registered split, nudging it would be fitting].
Tests first: band detection, both entries with hand-verified fills on synthetic bars, the permutation
test on synthetic frames.
Rule-6 checks pre-committed: EM and gex_pct are D-1 quantities (point-in-time); no roll day inside a
session; the open O is the first RTH bar, not a later bar; one trade per session; holdout dates never
loaded; the permutation test guards against a time-trend in gex_pct masquerading as regime.
