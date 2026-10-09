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

## Study 3 pre-registration: session-level gamma regime (APPROVED by Matteo 2026-10-07 with the params; frozen before any run)
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

## 2026-10-07 Study 3 first run (commit 9c1a0f2 code, config as approved): 479 sessions, SUPERSEDED
- R1 fade high gamma: n=250, -0.34R (CI -0.46..-0.21); complement (fade low gamma) -0.16R; regime
  contrast -0.18R; permutation p=0.93. KILL. The fade is WORSE in high gamma, the opposite of H.
- R2 breakout low gamma: n=229, +0.12R (CI -0.03..+0.27); complement (breakout high gamma) +0.25R
  (CI +0.11..+0.41); contrast -0.13R; permutation p=0.86. KILL as a regime claim.
- R3 fade high gamma above flip: n=244, -0.32R. KILL.
- Contrast "breakout_all_sessions": n=479, win 42.8% vs a 33% driftless baseline, +0.19R
  (CI +0.08..+0.30), PF 1.32, market share +0.28R. This is a strong-looking result and rule 6
  applies. Checked so far: EM and gex_pct are D-1 values; O is the 09:30 bar on the session's own
  contract (no roll inside a session); one trade per session; holdout never loaded; the touch bar
  pays no target; a bar touching both barriers is a loss.
- BUG FOUND by that check (fill realism, SPEC rule 5): when price was already beyond the band at
  10:00 (crossed before band_start), the first eligible bar counted as the touch and the breakout
  entered at the band plus one tick although the market was already well past it: a free head start
  for breakouts and a handicap for fades, i.e. exactly the asymmetry seen. Fixed before any
  interpretation: a touch must start from inside the band (previous bar close inside); price already
  beyond a band when the window opens -> no trade that session; a breakout stop order fills at the
  bar's open when the bar opened through the band. Tests added. Re-run required; the numbers above
  are not a result.

## 2026-10-07 Study 3 second run (clean-touch and gapped-open rules), 397 sessions: KILL on all variants
- 82 sessions dropped as "already beyond the band at 10:00" (113 no-touch vs 31 before): those were
  the sessions inflating the first run's breakout number.
- R1 fade high gamma: n=207, -0.24R (CI -0.38..-0.09); complement -0.02R; regime contrast -0.21R
  (CI -0.44..+0.01); permutation p=0.95; market share -0.11R. KILL.
- R2 breakout low gamma: n=190, -0.06R (CI -0.22..+0.10); complement (breakout high gamma) +0.12R
  (CI -0.04..+0.28); contrast -0.18R; permutation p=0.91; market share +0.03R. INDICATIVE, fails.
- R3 fade high gamma above flip: n=204, -0.22R; complement +0.04R; contrast -0.26R (CI -0.53..-0.00);
  permutation p=0.95. KILL.
- Contrasts: fade all sessions -0.13R (market share -0.03R); breakout all sessions +0.03R
  (CI -0.08..+0.15, PF 1.05, market share +0.14R, win 37.8% vs 33% baseline). By tercile the fade
  goes +0.01 / -0.13 / -0.28 from low to high gamma and the breakout -0.06 / +0.06 / +0.10: at the
  trade level the regime runs the OPPOSITE way to H (a session that reaches 0.5 EM on a high-gamma
  day is a trend day; the compressed-range result of Stage 1 is unconditional, this is conditional on
  reaching the band).
- Rule 6 on the breakout: after the fill fix it is +0.03R with the interval straddling zero; not a
  result. Study 3 verdict vs rules: KILL on every variant. 12 of 20 variants used. No holdout.

## 2026-10-07 | review of studies 1-3 and Study 4 pre-registration DRAFT (not a run; awaiting Matteo)
- commit: 77f9ed6 reviewed; this entry committed on branch claude/affectionate-gauss-7nuqm8.
- change: code and log audit (REVIEW.md). No data on this machine; nothing re-run. pytest: 77 passed.
- config diff: none. Variant count unchanged at 12 of 20.
- result: every kill in studies 1-3 stands as scored; point-in-time, holdout seal, roll handling and
  fill conservatism verified in code; the 0.6-tick naive residual is the only positive signal and it
  is a quarter of the friction. Three report-only caveats on the regime variable (sign convention is
  an assumption; calendar-time clock overweights 0DTE ATM gamma; the percentile ranks raw dollar GEX,
  which drifts with S0^2) do not change any verdict but should be re-checked before anything
  conditions on the regime again.
- proposed next (REVIEW.md section 5), needs approval before any code runs: Study 4, regime-conditioned
  D-expiring SPXW ATM straddle at the D-1 17:00 close held to settlement (S4a short in high gamma,
  S4b long in low gamma, S4c iron fly with 1 EM wings), regime = gex_pct of session D-1 (point in
  time), entry at the quoted bid/ask, settlement = FRED SPX close, permutation placebo, Stage 1
  regression restated on straddle P&L. New params proposed: opt_cost_per_leg_usd 1.50 [3.00],
  s4_wing_em 1.0 [0.75, 1.5], s4_min_expectancy_em 0.03 (gate), s4_regime_lag 1 (fixed). Would bring
  the count to 15 of 20. Secondary: Study 5, last-30-minute hedging-flow trade (Baltussen et al.),
  two variants, 17 of 20. Not recommended: any further level/absorption/band variant, MBP-10 spend.

## Study 4 pre-registration: regime-conditioned 0DTE straddle at the D-1 close (APPROVED by Matteo 2026-10-07, "lets run a test on study 4"; frozen as drafted in REVIEW.md section 5 before any run)
- config diff (config.yaml): market.option_multiplier 100.0 (structural); params.opt_cost_per_leg_usd 1.50
  [nudge 3.00] (all-in per leg: commission, exchange, regulatory, settlement; placeholder until confirmed
  with the broker), params.s4_wing_em 1.0 [0.75, 1.5], params.s4_regime_lag 1 (fixed: point in time);
  gates.study4_min_expectancy_em 0.03. regime_threshold 0.5 reused, no nudge.
- hypothesis H4: conditional on the PRIOR session's gamma percentile, the D-expiring SPXW ATM straddle
  sold at the D-1 17:00 close and held to the SPX settlement pays after costs on high-gamma sessions
  (S4a), the long straddle pays on low-gamma sessions (S4b), and the defined-risk iron fly with wings
  one EM out pays on high-gamma sessions (S4c); each with the regime contrast positive and the
  permutation placebo beaten.
- build: K = strike nearest F with both legs valid (the engine's atm_straddle rule; the chain's
  straddle mid must equal gex_daily.em, mismatches counted); short sells at bid_C + bid_P, long buys at
  ask_C + ask_P, wings bought at the ask at the valid strikes nearest K +/- s4_wing_em EM (skip the fly
  when the nearest strike is more than 25% off the target width); settlement |S_T - K| at the FRED SPX
  close; fees per leg; P&L in EM units (pnl_pts / em), also points and dollars. Regime = gex_pct of
  session D-1 (its inputs were all published before the 17:00 D-1 entry); the D row's percentile uses
  OI published the morning of D and is reported as a non-tradeable diagnostic only. Eligible sessions:
  equity sessions, not half days, GEX row present, nearest expiry = the SPXW expiring on D, lagged
  percentile present, settlement present, a both-valid ATM strike. All three structures on every
  eligible session; the regime filter is applied in the report.
- placebo and contrasts: permutation of the lagged percentile across sessions (perm_draws 1000, seed
  bootstrap_seed); the complement regime; every structure on all sessions (variance premium baseline)
  and by percentile tercile; the Stage 1 regression restated with pnl_em as the outcome (ln VIX and
  ln EM/S0 controls, day-of-week dummies, Newey-West 5 lags).
- gates per variant: n >= 200; mean pnl_em >= 0.03; day-bootstrap 90% CI lower > 0; regime contrast
  CI lower > 0; permutation p < 0.05. Tail block reported, not gated: five worst and best days, their
  share of the total, mean without the best five. S4c carries the decision if S4a and S4c disagree.
- variants: S4a, S4b, S4c -> 15 of 20 once run.
- rule-6 checks pre-committed: every entry input stamped <= 17:00 D-1; settlement is the official close;
  holdout sessions never loaded (calendar is sealed; the holdout can serve this new hypothesis later);
  half days excluded; no session double counted; em_mismatch count must be 0.

## 2026-10-07 | Study 4 built tests-first; real-data run PENDING (this session's container has no data/)
- commit: see git log (branch claude/affectionate-gauss-7nuqm8). Tests: tests/test_study4.py (hand-
  verified ATM selection, short/long straddle and iron-fly payoffs incl. wing caps and tolerance,
  regime lag, eligibility rules, generalised permutation test, report on synthetic frames) and a
  synthetic end-to-end run in tests/test_pipeline.py (holdout excluded, em_mismatch 0, fly loss bounded
  by max_loss, regime equals the prior session's percentile). src/study3.permutation_test gained
  value/pct arguments (defaults unchanged).
- config diff: the Study 4 entries above. No other change.
- result: no real-data numbers yet. Run on the data machine: `python -m src.study4` (reads gex_daily,
  the D-1 EOD files and data/raw/daily; writes data/derived/straddle_trades.parquet; prints the report
  with verdict_vs_rules per variant). Zero Databento spend. Then log the headline here.

## 2026-10-07 | Study 4 first run on real data (Matteo's machine, commit f89b0e1, config as approved): KILL on all three variants
- run: `python -m src.study4`, 502 sessions (2023-12 .. 2025-12). Skipped: half_day 8, no_gex_row 47
  (the ~40 calendar sessions before 2023-06-02 from the two-month bar warm-up plus the 7 known), no_prev_regime
  130 (the 126-session percentile warm-up), nearest_not_spxw_0dte 2. em_mismatch_sessions 0 (rule-6 check passes).
- S4a short straddle, gex_pct(D-1) >= 0.5: n=266, win 62.8%, +0.059 EM (+2.4 pts, +$238 per straddle),
  90% CI -0.021..+0.135 (FAILS ci_lower>0); complement -0.071 EM; regime contrast +0.130 (CI +0.012..+0.247);
  permutation p=0.035; PF 1.22; max DD 7.4 EM; worst day 2025-10-10 -5.3 EM (-154 pts); mean ex-best-5
  +0.041, ex-worst-5 +0.119. 4 of 5 gates pass. Verdict vs rules: KILL.
- S4b long straddle, gex_pct(D-1) < 0.5: n=236, win 47.0%, +0.033 EM (+1.9 pts), CI -0.050..+0.121 (FAILS);
  complement -0.099; contrast +0.132 (CI +0.013..+0.248); permutation p=0.036; mean ex-best-5 -0.031 (the long
  side leans on its best days). KILL.
- S4c iron fly, high gamma, 1 EM wings: n=265, -0.010 EM, CI -0.046..+0.025; contrast +0.046 (CI -0.006..+0.098);
  permutation p=0.063. Wings cost 0.33 EM of the 0.98 EM credit and double the spread. KILL on 4 of 5 gates.
  Pre-registered tie-break: S4c carries the decision when S4a and S4c disagree -> KILL.
- contrasts: short straddle on all sessions -0.002 EM (the 0DTE variance premium is about zero in this sample,
  as REVIEW.md expected); terciles low/mid/high: short -0.096 / +0.078 / +0.011, long +0.057 / -0.116 / -0.052,
  fly -0.058 / -0.011 / -0.027. NOT monotonic: the effect is "low gamma is bad for the short straddle", not
  "high gamma is good"; the top tercile is about zero.
- Stage 1 restated on straddle P&L: beta on the lagged percentile +0.19, p=0.17 (ln VIX), +0.18, p=0.18 (ln EM/S0):
  not significant. Same-day percentile (OI published after entry, NOT tradeable): +0.35, p=0.001; S4a/S4b under it
  +0.060 / +0.034 with contrasts CI > 0 and permutation p 0.03 / 0.027: the lag costs little, so the point-in-time
  version is a fair test of the tradeable thing.
- reading: friction is not the problem (half-spread 0.019 EM + fees 0.001 EM vs a 0.13 EM regime contrast);
  variance is. Per-session sd ~0.77 EM, SE ~0.047 on 266 sessions, so the +0.059 mean is 1.2 SE from zero. At this
  mean a CI lower bound above zero needs ~460 high-gamma sessions (about 3.5 more years). Pooling S4a and S4b into
  one switching strategy (not pre-registered, reported for information only) gives +0.047 EM on 502 sessions,
  SE ~0.035, 90% lower bound about -0.01: still a fail. The defined-risk version has no edge at all.
- rule-6 notes: point in time by construction (lagged regime, D-1 quotes, official settlement); holdout untouched;
  the permutation test treats sessions as exchangeable although the percentile is persistent, so its p is
  anti-conservative (a block permutation would be stricter), which only strengthens the kill.
- Variant count: 15 of 20. Decision is Matteo's. No holdout run.
- PowerShell note: the "NativeCommandError" in the console is PowerShell treating the module's stderr log line as
  an error under `2>&1`; the run completed normally.

## 2026-10-07 | Study 4 code hardening after a six-lens review (no run; numbers above are from commit f89b0e1)
- change: s4_regime_lag < 1 now raises (a lag of 0 would silently gate on the same-day, non-tradeable
  percentile); sessions whose quotes imply a riskless structure (iron-fly credit >= its narrower wing, or a
  non-positive premium) are dropped and counted as quote_sanity; sessions where no admissible wing exists are
  counted (fly_no_wing) so S4c's sample size is visible; friction_em_mean (half-spread + fees) reported next
  to the full spread; tail block reports the sums and only forms shares when the total is positive; a
  21-session block permutation is reported as a diagnostic beside the pre-registered session permutation
  (the lagged percentile is persistent, so the session shuffle is anti-conservative); --report-only recomputes
  the em_mismatch check from the saved table; permutation p is (count + 1) / (draws + 1), never exactly 0.
  Gates, formulas and variants unchanged. Tests added; 90 pass.
- config diff: none.
- effect on the logged result: none of the changes touch a gate or a payoff; a re-run would add the diagnostic
  fields (block_permutation p, friction_em_mean, fly_no_wing, quote_sanity counts) to the same verdicts.

## 2026-10-07 | next-study selection per NEXT.md (not a run): inventory, 15 candidates, scoring (CANDIDATES.md)
- commit: branch claude/next-study-prereg from main afbe3af (see git log). No data on this machine (data/
  is git-ignored) and no Databento key, so nothing was re-run and nothing was priced with get_cost; counts
  are quoted from this log and RESULTS.md, prices are estimates from the ledger's own rates. pytest: 90 passed.
- config diff: none. Variant count unchanged at 15 of 20.
- result: CANDIDATES.md inventories every table (what it supports, its stamp, its gaps), enumerates 15
  candidates (REVIEW's study 5, the term-structure-timed premium from the earlier NEXT.md draft in commit
  4146b21, the three section-3b re-checks, ten new ones) and scores them on prior, own-price payoff, signal
  to friction, independent decisions per month, power and independence. Ranking: (1) last-30-minute
  momentum into the close; (2) the one-week premium split into body and wings, not resolvable in sample
  (MDE 0.13 to 0.19 of premium) but testable with a $40 ThetaData Value month (2020 onward); the rest not
  recommended, each with its reason. No candidate on disk can resolve its published effect after costs;
  study 5 comes closest (about even odds of detecting the published effect before costs, about one in five
  after).
- Findings from reading: the draft term-structure study cannot meet its own sample gate (about 64 weekly and
  15 monthly non-overlapping positions in regime); of REVIEW 3b.1's three sign conventions, two are mirror
  images and the third is the unsigned measure divided by S0^2.
- Literature checked (sources in CANDIDATES.md): the overnight drift has averaged near zero since 2021 (NY
  Fed, July 2026); last-30-minute momentum measured flat on 1,085 SPX sessions of the 0DTE era and damped by
  0DTE positioning (Adams et al. 2025); one-week SPX ATM short straddles flat after costs over 2010-2019
  (Miller and Li 2026); little weekend effect in S&P 500 options (Jones and Shemesh 2018).
- Independent review before freezing (a separate agent read the draft against the code and logs; 15
  findings): the published Gao et al. effect was overstated about two-fold (a long-or-cash summary figure,
  6.3% against -0.5%, read as long-short; the paper's long-short strategy earns 6.67% a year, Sharpe 1.08),
  which had put study 5's MDE below the published effect when it is above it; the S5b stop was off the tick
  grid and a gapped stop could fill a tick better than rule 5; the EM eligibility rule admitted an 18-day EM
  (2024-12-03); two "contrasts" were really further trades (alternative predictors, the gamma split); gate
  4 restated gate 3; the session-permutation claim held only for constant drift; post-half-day sessions were
  handled inconsistently; several gate numbers were hard-coded; the 16:00 exit's departure from flat_time was
  unstated; arithmetic slips. All corrected in the pre-registration below and in CANDIDATES.md.
- Second review of the corrected draft (same agent; 11 findings): sample B's economics power was overstated
  because gate 2, not gate 3, binds at B's sample size; pulling the 2019 bars rebuilds the calendar and
  would redraw the stage-3 day sample (only 13 of the current 120 days survive on a proxy calendar), so
  studies 1 and 2 would stop being reproducible; under B nothing checked that existence holds in 2023-2025;
  gate 4 and the session permutation are the same test; the 2.65 bp reading of Gao et al. implies a Sharpe of
  1.56 in this window and is an upper bracket, not the published size; the block length deciding gate 5 was
  a module constant; the 0.75 EM/VIX factor is the pilot's, not the full sample's; NQ replication under B
  would cost about $8 to $10. Corrected below. Consequence: sample A is now the default and B an option
  (more existence power if the effect is constant, less economics power, a code prerequisite). The ranking
  survives; the case for study 5 is narrow: in sample A it has about even odds of detecting the published
  effect before costs and about one in five after.

## Study 5 pre-registration: last-30-minute momentum into the close (APPROVED by Matteo 2026-10-07 with sample A, "We'll do sample A to start"; frozen before any run)
Revised from REVIEW.md section 5, which conditioned both legs on the gamma regime (with the move in low
gamma, against it in high gamma). NEXT.md closes the regime as a trade and forbids new variants of it, so
here the hypothesis is unconditional and gamma appears only as a descriptive statistic that cannot produce
a Go (open PR #3, unmerged, records the same instruction: gamma is optional). Holdout sealed.

Hypothesis H5. On ES, the return from 15:30 to 16:00 ET continues the rest-of-day return from the prior
session's close to 15:30: one contract taken at 15:30 in the direction of that return and held to 16:00 (i)
times the closing window better than a random direction with the same long and short mix (existence), and
(ii) has positive after-cost expectancy (economics), on all eligible sessions, with no regime filter.
Expected outcome, stated before the run: KILL (CANDIDATES.md C1). Power is in the paragraph after the gates;
in short, at the published effect size the existence half is a coin flip in sample A and the economics half
cannot be resolved on either sample.

Sample, chosen by Matteo at approval, before any data is read:
- A (default): 2023-06-02 to 2025-12-31, data on disk, zero spend.
- B (option): 2019-01-03 to 2025-12-31 (the first session with a prior close and a prior VIX on disk), after
  an ES 1-minute bar pull for 2019-01 to 2023-03 (estimated $5.4 at the pilot's $0.105 a month, just over
  the $5 ask line; price with `python -m src.ingest_futures bars --start 2019-01-01 --end 2023-03-31
  --price-only` and approve before pulling). FRED SPX and VIX from 2019 are already on disk, so the
  calendar's equity-session flags cover B. Prerequisite before that pull: the pull rebuilds
  data/derived/calendar.parquet, and calendar.stage3_days draws its stratified sample from the whole
  calendar, so the stage-3 day set used by studies 1 and 2 (and by `robustness nudges` and `ingest_futures
  trades`) would change. The current in-sample day set is written to disk first and stage3_days is made to
  read it when present, with a test that the set is identical before and after a calendar rebuild (a code
  change, logged, no result changes). Under B the NQ replication after a pass needs NQ bars from 2019,
  estimated $8 to $10 (over the ask line, total spend near $95).
Everything below applies to both samples; gates 7a and 7b apply to B only.

Build rules, every input stamped at or before the 15:30:00 ET decision on session D (bars are keyed by open
time; a bar is known at its close):
- P_prev = close of D-1's last RTH bar (`gex.es_close_at_spx_close`), on D's instrument_id.
- P_dec = close of D's bar opening one minute before s5_decision_time (the 15:29 bar, closing at 15:30:00).
- Unit: EM_V = s5_em_vix_factor x (VIX close of D-1 / 100) / sqrt(252) x P_prev, a VIX-implied expected
  move. One unit for every session in both samples, because option EMs do not exist before 2023-06 and
  gex_daily.em is not always the 0DTE straddle (2024-12-03 is an 18-day straddle). The factor 0.75 is a
  convention taken from the pilot's EM / S0 to VIX / sqrt(252) ratio (33 days of March-April 2025); the
  full-sample ratio from `python -m src.gex --validate-only` is logged before the run for information, and
  the factor is not changed after this registration (it scales the unit, the stop and gate 2 together,
  so freezing it adds no bias). Where gex_daily has a row whose nearest expiry is the SPXW expiring on D,
  results are also reported in that option EM as a cross-check.
- r_ROD = (P_dec - P_prev) / EM_V; trade direction d = sign(r_ROD); r_ROD = 0 means no trade (counted).
- Entry E = open of D's bar opening at s5_decision_time (15:30) + d x entry_slippage ticks. The decision bar
  and the entry bar are different bars by construction.
- S5a exit: X = open of D's bar opening at s5_exit_time (16:00) - d x 1 tick (rule-5 time exit). That bar
  lies outside study 3's RTH frame (which stops at 15:59) and is read explicitly; a session without it is
  skipped and counted, never filled at a later price. The 16:00 exit departs from market.flat_time (15:55,
  the hard exit for studies 1 to 3) and from REVIEW's draft: the hypothesis is about the window that ends
  at the 16:00 close, and the closing auction and the 15:50 imbalance publication fall in its last minutes.
- S5b exit (defined risk): stop S = E - d x s5_stop_em x EM_V, rounded to the tick grid away from the entry
  (down for a long, up for a short): S_g. A bar whose adverse extreme reaches S_g fills at the worse of its
  open and S_g - d x 1 tick (a long fills at min(open, S_g - tick)); on the entry bar, whose open is the
  entry, that is S_g - d x tick. Otherwise the S5a time exit. No target.
- pnl_pts = d (X - E) - cost_rt_usd / point_value; pnl_em = pnl_pts / EM_V (gate unit); also pnl_bp =
  1e4 pnl_pts / E and pnl_usd = pnl_pts x point_value.
- For descriptive statistics only (not fills): r_L30 = (open of the 16:00 bar - open of the 15:30 bar) /
  EM_V.
Eligibility: in-sample equity session in the chosen sample; not a half day; previous equity session not a
half day (its last ES bar is the early close, not 16:00); not a roll day (calendar.roll: the predictor
would span two contracts); VIX close of D-1 present; bars present for D-1's last RTH bar and D's 15:29,
15:30 and 16:00 bars, all on D's instrument_id; r_ROD != 0. Both variants on every eligible session (one
sample). Expected n: about 620 (A), about 1,690 (B); about 20 independent decisions a month.

Variants (count toward the 20; 15 used):
- S5a: momentum, no stop. [16]
- S5b: S5a with the s5_stop_em stop (the defined-risk version). [17]
No other rule is scored, traded or given a p-value. Count after this study: 17 of 20.

Descriptive statistics (reported without trade P&L intervals or p-values, not variants; none of them may
later be pre-registered as a trade on any data used here, only on new data such as the holdout):
1. Drift: the always-long and always-short 15:30-16:00 means with the same fills (they also enter gate 4);
   S5a's long and short legs separately.
2. Slope of r_L30 on r_ROD, Newey-West nw_lags, with and without the s5_outlier_n largest |r_ROD| sessions.
3. S5a mean by tercile of |r_ROD| and by tercile of EM_V / P_prev; by year; FOMC days (static/events.csv)
   against the rest.
4. Slopes of r_L30 on two other predictors (prior close to 10:00, Gao et al.; 09:30 to 15:30, comparable to
   the 0DTE-era measurement), with standard errors only.
5. Mechanism (Baltussen et al.): the slope of r_L30 on r_ROD separately by the sign of the D row's net_gex
   (< 0 = dealers short gamma under the SPEC convention), 2023-06 onward, with standard errors only. The D
   row uses OI published before 09:30 D, so it is point in time at 15:30.
6. The gross move in the trade direction in 15:30-15:50 and 15:50-16:00 (bar opens).
7. Overlap with study 3's trend days: S5a on sessions where price reached O +/- band_a x EM_V (O the 09:30
   open) before 15:30, computed from the bars for every session, against the rest.
8. Friction: mean friction in EM_V and points; S5b's share of stopped trades; the driftless baseline for these
   fills (minus the friction, a little worse for stopped trades).

Placebo. Session permutation of the trade direction d across eligible sessions (perm_draws, seed
bootstrap_seed) and a block permutation of d in blocks of s5_perm_block (21) sessions. Each session's long
and short outcomes are computed once, so S5b's stop path is exact under either shuffle. p = (count + 1) /
(draws + 1) over permuted means at or above the observed. The session shuffle keeps the long and short
counts fixed, which removes a constant drift; drift that varies with the share of up days over weeks
survives it, which is what the block shuffle is for. Both enter gate 5.

Gates per variant (frozen before any run). Existence: 4, 5 and, under B, 7a. Economics: 2, 3, 6 and, under
B, 7b. Gate 4 and the session half of gate 5 are the same test in two forms: under the fixed-count shuffle
the expected permuted mean is exactly gate 4's random-direction benchmark, so both measure the observed mean
minus that benchmark; gate 4 is kept for its interval, and the block half of gate 5 is the independent
check.
1. n >= study5_min_sessions (400).
2. mean pnl_em >= study5_min_expectancy_em (0.02 EM after costs, about 0.85 points at an EM of 42). In A
   gate 3 binds first under the planning standard deviation (it needs about 0.024 EM); in B gate 2 binds
   (gate 3 needs only about 0.015 EM there).
3. day-bootstrap 90% interval of mean pnl_em has a lower bound > 0 (bootstrap_draws, bootstrap_seed,
   ci_level).
4. timing contrast: mean pnl_em minus the mean of the same long and short mix taken in random directions
   (the strategy's long share times the always-long mean plus its short share times the always-short mean,
   the legs computed on the identical session set with the same fills and costs), day-bootstrap 90%
   interval lower bound > 0; every bootstrap resample recomputes the long share and both leg means. For
   S5a friction and constant drift cancel exactly; for S5b they cancel approximately (the extra tick on a
   stopped trade depends on each direction's path).
5. both permutation p-values (session and block) < study5_perm_p (0.05).
6. mean pnl_em without the study5_tail_drop (5) best sessions > 0. Reported with it: the five worst and best
   sessions and their sums, the share of P&L from FOMC days and from March 2020 and April 2025 where in
   sample, max drawdown, longest losing streak.
7a. (B only) era, existence: the 2023-06 to 2025-12 timing contrast (gate 4's statistic) has the same sign
   as the full-sample contrast and at least half its size.
7b. (B only) era, economics: the 2023-06 to 2025-12 mean of pnl_em has the same sign as the full-sample mean
   and at least half its size (the holdout rule's form).
Decision: pass means every gate on the deciding variant. S5b carries the decision when S5a and S5b disagree
(study 4's tie-break). The report states existence and economics separately, so "the effect exists but does
not pay" is a possible, complete answer. Kill: anything less; stop, write up, no nudges, no new predictors,
no holdout. Pass: nudges and splits, the NQ replication (NQ bars priced first: about $3 to $4 under A, $8 to
$10 under B), then the holdout once on Matteo's word.

Power, stated before the run (CANDIDATES.md C1). Planning standard deviation of pnl_em 0.36 EM (an
assumption no gate uses); ES friction 0.58 points, about 0.014 EM in A and about 0.016 on average in B
(friction in EM terms is larger when the index was lower). Published size: Gao et al.'s long-short timing
strategy earned 6.67% a year, Sharpe 1.08 (SPY 1993-2013); rescaled to this window's volatility that is about
0.025 EM gross (0.011 net in A). An upper bracket reads the 2.65 bp a session directly as basis points,
0.035 EM gross, which implies a Sharpe of about 1.56 in this window and is optimistic.
- A (n about 620; SE 0.0145): gate 3 needs about 0.024 EM net (1.0 point, 1.8 bp); MDE 0.036. At the
  published size: existence 0.52, economics 0.18 (upper bracket 0.79 and 0.43). If the 0DTE-era measurement
  is the truth (about 0.005 EM gross), existence passes with 0.10, and the 90% upper bound excludes the
  published size with probability 0.38 (0.67 for the upper bracket).
- B (n about 1,690; SE 0.009), with gates 7a and 7b, by simulation: if the published size held throughout,
  existence 0.76 and economics 0.08 (upper bracket 0.92 and 0.41); if it held before 2023-06 and fell to the
  0DTE-era size after, existence passes with 0.29, so the era gate catches the decay about seven times in
  ten; with no effect anywhere, existence passes with 0.04. B buys existence power if the effect is
  constant, and a decay pattern to describe if it is not; it does not buy economics power.
These figures are for single gates (B's include the era gates); the joint decision has somewhat lower power
because gates 3 to 5 test nearly the same thing at the same level. Independent decisions: about 20 a month.

Rule-6 checks committed:
- Point in time: the direction uses bars that close at or before 15:30:00; entry is the next bar's open; the
  unit uses the VIX close of D-1; the mechanism statistic uses the D row (OI before 09:30 D). A unit test
  gives a session whose 15:30 bar alone carries the move and requires the direction to ignore it.
- No series spans a roll: roll days are ineligible and instrument_id is checked on D-1's close and on every
  bar used on D.
- The holdout is never loaded (a sealed-holdout refusal test); half days and post-half-day sessions are
  excluded; one trade per session per variant; no session counted twice.
- The 16:00 exit bar must exist; sessions without it are skipped and counted.
- Fill realism: on the sampled tick sessions whose windows cover 15:29:00 to 16:01:00, the bar-rule entry and
  exit are compared with the first tick print after 15:30:00 and after 16:00:00 (one tick against); the mean
  and worst differences in ticks are reported, and any average advantage of bar fills is flagged against
  rule 5.
- A synthetic driftless walk through the module returns minus the friction within sampling error.
- Data sanity: missing minutes between 15:29 and 16:00, duplicate bars, and sessions with |r_L30| >
  s5_sanity_em (3 EM_V) are counted and listed, not dropped.
- If anything looks strong: the drift legs, both permutations, the FOMC and crash-month shares, the overlap
  with trend days (statistic 7), and the option-EM cross-check are re-examined, and the report says what was
  checked.

New parameters (config.yaml; rule 3, need approval):
- params.s5_sample "A" or "B": chosen at approval, no nudge.
- params.s5_decision_time "15:30" (nudges "15:25", "15:35") and params.s5_exit_time "16:00" (nudges
  "15:55", "16:05"): robustness only.
- params.s5_stop_em 0.50 (nudges 0.35, 0.75): REVIEW proposed 0.25 EM as "rarely hit", but with a
  closing-window standard deviation near 0.36 EM a 0.25 EM stop is reached on about half of sessions (twice
  the normal tail beyond 0.7 standard deviations) and would make it a different trade; 0.5 EM, about 1.4
  standard deviations, is reached on about 16% and still caps the tail.
- params.s5_em_vix_factor 0.75 (a unit convention from the pilot's EM / S0 to VIX / sqrt(252) ratio; no
  nudge).
- params.s5_perm_block 21 (sessions per block in gate 5's block shuffle; no nudge: it decides a gate).
- params.s5_outlier_n 5 and params.s5_sanity_em 3.0: reporting constants.
- gates.study5_min_sessions 400, gates.study5_min_expectancy_em 0.02, gates.study5_perm_p 0.05,
  gates.study5_tail_drop 5: gates, no nudges.
Reused: entry_slippage (1 tick), cost_rt_usd (3.98, nudge 5.97), band_a (0.50, statistic 7 only),
perm_draws, bootstrap_draws, bootstrap_seed, ci_level, nw_lags, market.tick, market.point_value.

Tests first after approval, each with a hand-verified answer: the predictor (prior close on the same
instrument; roll-day and post-half-day exclusion); the direction ignores the 15:30 bar; EM_V from a VIX
close; entry and exit fills; a missing 16:00 bar skips the session; the stop (rounded to the grid away from
entry; reached on the entry bar; gapped through, filling at the worse of the open and one tick beyond;
reached on the 15:59 bar against the time exit); P&L units and costs; the timing contrast against a
hand-computed random-direction mean; both permutations with precomputed outcomes and p never zero; the
Newey-West slope on synthetic data with a known beta; both era gates; (B only) the frozen stage-3 day set
surviving a calendar rebuild; stop rounding with a small tolerance so floating-point noise never moves the
stop a tick; the sealed-holdout refusal; a synthetic
end-to-end run with injected momentum (existence passes) and without (minus the friction, existence
fails). Then `src/study5.py`, one run (`python -m src.study5`), the report against the gates, and stop. The
gate call is Matteo's.

## 2026-10-07 | Study 5 approved with sample A; config entries added (not a run)
- Approval: Matteo, "We'll do sample A to start". The pre-registration above is frozen as written; sample A
  (2023-06-02 to 2025-12-31, data on disk, zero spend). Sample B is not approved and the module refuses it
  (it would first need the stage-3 day-set freeze).
- config diff (config.yaml), as registered: params s5_sample "A", s5_decision_time "15:30" [15:25, 15:35],
  s5_exit_time "16:00" [15:55, 16:05], s5_stop_em 0.50 [0.35, 0.75], s5_em_vix_factor 0.75,
  s5_perm_block 21, s5_outlier_n 5, s5_sanity_em 3.0; gates study5_min_sessions 400,
  study5_min_expectancy_em 0.02, study5_perm_p 0.05, study5_tail_drop 5; sample.s5_starts {A 2023-06-02,
  B 2019-01-03}. Two structural market times for registered descriptive statistics only (no gate uses
  them): market.moc_imbalance_time "15:50" (statistic 6), market.first_half_hour_end "10:00" (statistic 4).
- Variant count: 17 of 20 once run.

## 2026-10-07 | Study 5 built tests-first; real-data run PENDING (this session's container has no data/)
- code: src/study5.py; tests/test_study5.py (25 tests, hand-verified on one constructed session: EM_V,
  tick-grid stops, touched, gapped, entry-bar and last-bar stops, both directions, skips, eligibility,
  descriptive fields, timing contrast, both permutations, era check, Newey-West slope, tie-break, a
  driftless walk returning minus the friction, injected momentum passing existence and none failing it,
  sample B refused) and test_study5_end_to_end in tests/test_pipeline.py (synthetic store, roll day skipped,
  no holdout date, holdout request refused). Mutation check: four planted bugs (direction from the entry
  bar, stop filled at the stop, gapped stop at the better price, time exit a tick favourable) each failed
  the tests. pytest: 116 passed.
- config diff: none beyond the approval entry above.
- Run on the data machine: `python -m src.study5` (reads the calendar, ES bars, FRED VIX and gex_daily for
  descriptive fields; writes data/derived/close_momentum_trades.parquet; prints the report with
  verdict_vs_rules per variant, the existence and economics halves, and decision_vs_rules). Zero spend.
  Then log the headline here.

## 2026-10-07 | Study 5 first run on real data (Matteo's machine, commit c7c2c68, config as approved, sample A): KILL on both variants
- run: `python -m src.study5`; 618 sessions 2023-06-02..2025-12-31. Skipped: roll_day 11, half_day 8,
  prev_half_day 8, flat_predictor 3. Sanity: 0 sessions with missing minutes in 15:29-16:00, 0 duplicate
  bars, no |r_L30| > 3 EM_V. pytest on that machine: all passed.
- S5a momentum, no stop: mean -0.037 EM_V (-1.66 pts, -3.0 bp, -$83/contract), 90% CI -0.054..-0.020;
  win 44.3%, PF 0.67; timing contrast -0.024 (CI -0.041..-0.006); session perm p 0.994, block p 0.974;
  mean without best 5 -0.044. Existence FAIL, economics FAIL, KILL.
- S5b with 0.5 EM_V stop (stopped 6.6%): mean -0.031 (CI -0.046..-0.015); timing contrast -0.020 (CI
  -0.036..-0.003); perm p 0.985 / block 0.962. Existence FAIL, economics FAIL, KILL. Decision (S5b
  carries it): KILL. Variant count 17 of 20.
- Reading: the published momentum is excluded, and the sign is the opposite. The timing contrast's whole
  interval is below zero: going with the rest-of-day move does worse than random directions with the same
  long/short mix. Descriptive slope of r_L30 on r_ROD -0.028 (t -2.3; -0.037, t -3.5 without the 5
  largest |r_ROD|); negative in every year (2023 -0.029, 2024 -0.050, 2025 -0.030 EM); strongest in the
  top |r_ROD| tercile (-0.074); both other predictors negative (Gao first half-hour t -1.4, open-to-15:30
  t -2.0). Gamma split (descriptive only): reversal slope -0.042 (t -2.8, n 374) when net_gex >= 0, -0.013
  (t -0.7, n 244) when < 0, the direction the dealer-hedging mechanism predicts (long gamma damps moves),
  consistent with Adams et al. 2025. Most of it is 15:30-15:50 (-0.021) rather than 15:50-16:00 (-0.002).
  Always-long -0.009, always-short -0.020 (friction 0.014 EM_V a trade).
- The mirror (fade the rest-of-day move) would net about +0.009 EM_V a session (each session's two
  directions sum to minus twice the friction, so the mirror mean is +0.037 - 2 x 0.014), with an interval
  that includes zero. It was not pre-registered, and by the registration no trade suggested by this data may
  be registered on it; testing it would need new data (the holdout or sample B) and a variant.
- rule-6 checks on the strong (negative) result: sign logic verified by the hand tests (long when the day is
  up, pnl = X - E) and by the injected-momentum test, which passes with a positive mean; friction cancels in
  the timing contrast, so fills cannot create it; the decision bar (15:29 close) and entry (15:30 open) are
  adjacent prints, and a quarter-point bounce is small against a 0.36 EM_V (about 15 points) window; same
  sign in every year, in option-EM units (S5a -0.047, S5b -0.040 option EM on 616 sessions) and in both
  predictor variants; no holdout date loaded (last 2025-12-31).
- Unit note: median option EM / EM_V = 0.757, so the frozen 0.75 factor (from the 33-day pilot) makes EM_V
  about 1.32 option EMs. A pure scale: it changes no sign, interval or p-value; it makes the 0.5 EM_V stop
  about 0.66 option EM and gate 2 about 0.026 option EM. No verdict depends on it.
- Decision is Matteo's. No holdout run.

## Study 5f pre-registration: fade the rest-of-day move into the close, holdout only (APPROVED by Matteo 2026-10-07, "Yes"; frozen before any run)
Origin: the study 5 run (above) found the close reverses the day in 2023-06..2025-12 (timing contrast -0.024
EM_V, CI below zero; slope t -2.3). By study 5's own registration, a trade suggested by that data may not be
tested on it, so this is tested once on the sealed holdout (2026-01-01 to sample.end 2026-09-30) and nowhere
else. Matteo chose this route 2026-10-07.

Hypothesis H5f. The study 5 trade in the opposite direction (d_f = -sign(r_ROD)) with the S5b stop has
positive after-cost expectancy out of sample.

Build: identical to study 5 (same decision, entry, exit, stop, unit, costs, eligibility, config values),
except the direction is reversed. No new parameter. Because every session's long and short outcomes are
already computed, the fade is the other side of the same table: pnl_em_fade = pnl_em_momentum_stop of the
side not chosen by study 5.

Variant: S5f, fade with the 0.5 EM_V stop (the defined-risk version only; the no-stop fade is not run).
Count: 18 of 20.

Steps, in order, none of which reads a holdout date until step 4:
1. In-sample reference: from the saved study 5 table (2023-06-02..2025-12-31), the S5f mean pnl_em, its
   CI and timing contrast, computed with `--report-only` on in-sample data and logged here. If that mean is
   not positive, stop: the holdout is not opened for this hypothesis.
2. Check that holdout inputs exist without loading them: ES bar files 2026-01 to 2026-09 and a FRED file
   whose last date is at least 2026-09-30 (file names and the file's max date only). If bars are missing,
   price the pull first (about 9 months x $0.105 = about $1).
3. Matteo says "run the holdout".
4. One run with GAMMA_EDGE_RUN_HOLDOUT=1 on holdout sessions only; report; stop.

Holdout gates (SPEC holdout rule, as in "Holdout, robustness, and the go/no-go decision"):
1. mean pnl_em of S5f > 0 and at least holdout_min_fraction (0.5) times the in-sample S5f mean.
2. Reported, not gated: the 90% interval, the timing contrast and its interval, both permutation p-values,
   the tail block, the descriptive gamma split, and the momentum direction's mean (which should be negative).
Pass: the fade survives one out-of-sample look; the next step would be the SPEC robustness nudges and an NQ
replication, and only then any thought of trading it. Kill: write up.

Power, stated before the run. About 180 eligible holdout sessions; at the planning sd of 0.36 EM_V the
standard error is about 0.027 EM_V. The in-sample fade nets about +0.009 EM_V, so gate 1 needs only about
+0.0045 in the holdout. If the true edge is zero, the holdout passes gate 1 by chance about 43% of the time;
if it is +0.009, about 57%. This holdout can kill a fade that has turned clearly negative; it cannot confirm
an edge this small. A pass is a reason to keep watching (paper trading, or the 2019 extension), not to trade.
Using the holdout here also spends its freshness for any later ES close-window hypothesis.

## 2026-10-07 | Study 5f approved ("Yes") and built tests-first; nothing run on real data
- code: src/study5.py gains fade(), fade_gate(), fade_report(), holdout_check() and run_fade_holdout(), and
  CLI flags --fade-reference (step 1, in-sample, from the saved table), --holdout-check (step 2, file names
  and the daily file's last date only) and --fade-holdout MEAN (step 4, refused without
  GAMMA_EDGE_RUN_HOLDOUT=1, and returns VOID without opening the holdout if MEAN is not positive). Holdout
  trades go to close_momentum_trades_holdout, never over the in-sample table.
- tests: fade side swap and gate (hand values, including exactly half, and VOID), a fade report on injected
  momentum (negative), and a synthetic holdout path (refused without the flag, holdout dates only with it,
  VOID with a negative reference). pytest: all pass.
- config diff: none (reuses study 5's values and gates.holdout_min_fraction 0.5). Variant count 18 of 20.
- Next, on Matteo's machine: step 1 `python -m src.study5 --fade-reference`, then step 2
  `python -m src.study5 --holdout-check`; log both here. Step 4 only after "run the holdout".

## 2026-10-07 | Study 5f steps 1 and 2 (Matteo's machine, commit edd8698; in-sample only, holdout not opened)
- pytest: all passed.
- Step 1, in-sample reference (`--fade-reference`, 618 sessions 2023-06-02..2025-12-31): S5f mean +0.00796
  EM_V (CI -0.0093..+0.0246), win 50.0%, PF 1.09, max DD 3.9 EM_V; timing contrast +0.0199 (CI
  +0.0030..+0.0356); session perm p 0.016, block perm p 0.039; mean without the best 5 sessions -0.0015
  (the best five, 2024-05-01, 2024-05-31, 2024-07-25, 2024-02-13, 2024-07-12, sum +5.8 EM_V). Reference is
  positive, so the holdout may be opened. Holdout gate: mean > 0 and >= 0.5 x 0.00796 = 0.00398 EM_V.
- Reading of the reference (not a gate): the timing is real in sample, but the after-cost edge rests on a
  handful of large reversal days; without them it is about zero.
- Step 2 (`--holdout-check`): bar months 2026-01..2026-09 all present, daily file ends 2026-10-02, ok.
- Next: step 4 only on Matteo's "run the holdout":
  GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.study5 --fade-holdout 0.007964284240146915

## 2026-10-07 | Study 5f holdout run (Matteo's machine, commit edd8698, GAMMA_EDGE_RUN_HOLDOUT=1, run by Matteo): gate PASS
- 184 holdout sessions 2026-01-02..2026-09-30 (skipped: roll_day 3). One run; the holdout is now spent for
  the closing-window fade.
- S5f fade with the 0.5 EM_V stop: mean +0.0145 EM_V (CI -0.0061..+0.0357), win 52.7%, PF 1.25, max DD 1.5
  EM_V; timing contrast +0.0233 (CI +0.0018..+0.0450); session perm p 0.037, block perm p 0.077; mean
  without the best 5 +0.0022. Momentum direction -0.032 (in sample -0.031).
- Gate (SPEC holdout rule, frozen): mean > 0 and >= 0.5 x 0.00796 = 0.00398 -> PASS.
- Reading: the reversal replicated out of sample with the same size (timing contrast +0.023 vs +0.020 in
  sample; momentum side -0.032 vs -0.031). The after-cost edge is still small and unresolved: the interval
  includes zero in both samples. Pooled (802 sessions) the fade nets about +0.0095 EM_V, roughly 0.4-0.5
  points or $20-25 per ES contract per trade, against in-sample drawdowns near 4 EM_V. As pre-registered,
  a pass is a reason to keep watching, not to trade.
- rule-6 checks: dates all in the holdout window; no in-sample session re-used (separate table
  close_momentum_trades_holdout); the frozen reference was logged before the run; skips as expected for
  Jan-Sep 2026 (three rolls, no half days); the gamma statistic is empty because gex_daily was never built
  for the holdout, as intended (descriptive only).
- Per SPEC after a pass: robustness nudges and splits on the in-sample data, the NQ replication (bars to be
  priced), and paper trading before any spend. Decision is Matteo's. Variant count 18 of 20.

## Study 5f robustness and NQ replication pre-registration (APPROVED by Matteo 2026-10-07, "Okay lets do 1 and 2"; frozen before any run)
Per SPEC "Robustness checks" after the holdout pass. No new variant: the frozen S5f rule is re-run, not
changed. In-sample data only (2023-06-02..2025-12-31); the holdout is not reopened.
1. Nudges (in-sample, ES): each registered nudge one at a time, everything else frozen:
   s5_decision_time 15:25 / 15:35, s5_exit_time 15:55 / 16:05, s5_stop_em 0.35 / 0.75, entry_slippage 2,
   cost_rt_usd 5.97 (eight runs). Rule (SPEC; gates.robustness_min_positive_share 0.80): the S5f mean
   pnl_em must stay positive in at least 80% of the nudges, i.e. at least 7 of 8. Reported per nudge: n,
   mean, timing contrast.
2. Splits (in-sample, ES, the base run): S5f mean, n and day-bootstrap 90% interval by calendar year, by
   tercile of EM_V / price, by the sign of the D row's net_gex, and by weekday. Report only; a warning is
   recorded if removing any single year turns the mean non-positive ("one period carrying the whole result").
3. NQ replication (SPEC robustness 3): the same frozen rules on NQ.v.0 1-minute bars over the same in-sample
   window, with the same VIX-based unit (EM_V on NQ's prior close; NQ's higher volatility makes the 0.5
   EM_V stop relatively tighter, accepted as part of "frozen rules"), NQ point value $20, the same tick,
   slippage and $3.98 round trip. Rule: S5f mean pnl_em > 0 and timing contrast > 0 on NQ (point
   estimates); intervals and both permutations reported. Data: NQ bars 2023-04..2025-12 only (no holdout
   months), pulled under a separate overlay (config.nq.yaml, data root data_nq) so ES tables and the ES
   calendar are untouched; the spend ledger stays the single data/spend_ledger.csv. Priced with get_cost
   first (estimate $3 to $4); Matteo approves the amount.
Outcome language: passes on all three -> "robust in sample and on NQ"; the next step would be paper trading
(SPEC "Partial": three months of logged live signals before any spend). Any failure is reported as such.

## 2026-10-07 | Study 5f robustness and NQ replication built tests-first (nothing run on real data)
- code: study5.fade_robustness() (BASE plus the eight registered nudges on one in-sample load; splits by
  year, EM_V tercile, net_gex sign, weekday; single-year carry warning), study5.fade_replication() (frozen
  rules on the active config's market, replication gate), CLI --fade-robustness and --fade-replication.
  config.nq.yaml overlay (data root data_nq, NQ.v.0, point value $20, shared ledger data/spend_ledger.csv);
  spend.ledger_path honours data.ledger so the $100 guard counts NQ spend; data_nq/ git-ignored.
- tests: the registered nudge list, replication gate, carry warning (hand frames), overlay values, shared
  ledger path, robustness on the synthetic store. pytest: all pass.
- config diff: config.nq.yaml added (overlay only); config.yaml unchanged.
- Next on Matteo's machine: `python -m src.study5 --fade-robustness`; then under the NQ overlay:
  ingest_daily (free), price NQ bars 2023-04..2025-12 with --price-only, approve, then --fade-replication.

## 2026-10-07 | Study 5f robustness run and NQ pricing (Matteo's machine, commit 3fa6e57; in sample only)
- Base run reproduces the reference (618 sessions, +0.00796 EM_V). Costs throughout: entry_slippage 1 tick on
  entry, one tick on the time exit (stops one tick beyond), $3.98 round trip = 0.5796 points a trade.
- Nudges (S5f mean pnl_em / timing contrast): decision 15:25 +0.0032 / +0.015; decision 15:35 -0.0065 /
  +0.008; exit 15:55 +0.00001 / +0.014; exit 16:05 +0.0189 / +0.031; stop 0.35 +0.0062 / +0.020; stop 0.75
  +0.0075 / +0.022; entry slippage 2 ticks +0.0004 / +0.019; cost $5.97 +0.0070 / +0.020. Positive in 7 of 8
  (0.875 >= 0.80): nudge rule PASS. The timing contrast is positive in all 8.
- Splits (S5f mean, 90% CI): 2023 -0.0072 (n 139), 2024 +0.0156 (240), 2025 +0.0091 (239); EM_V tercile
  low +0.0034, mid -0.0085, high +0.0290 (CI +0.001..+0.058); net_gex < 0 +0.0072, >= 0 +0.0085; Friday
  +0.0271, other weekdays -0.0004..+0.0057. No single-year carry warning.
- Reading: the timing is robust (positive in every nudge); the after-cost edge is not: one extra tick of
  entry slippage takes it to about zero (+0.0004), and exiting at 15:55 instead of 16:00 does the same. The
  edge is about one tick wide and leans on high-volatility sessions.
- NQ pricing under config.nq.yaml: ingest_daily wrote data_nq/raw/daily (free); NQ bars 2023-04..2025-12,
  33 months, get_cost $3.55 (shared ledger $81.05 before). Under the $5 ask line; awaiting Matteo's approval.

## 2026-10-07 | Decision (Matteo): multiple-testing count per study family from here on (not a run)
- Rule change, recorded before any further result: the programme-wide count stays closed at 18 of 20
  (studies 1-5f, unchanged history; NQ replication of 5f is running, so the old over-20 rule is met for
  5f either way). Each NEW study family (e.g. pure order flow, commodity settlement momentum) gets its own
  budget of at most 4 gated variants, frozen in its pre-registration, and needs its own out-of-sample
  confirmation (an untouched holdout or a replication market) before any Go. Families that reuse the
  2023-06..2025-12 ES sample must say so and count against both their own budget and a note in this log.
- Rationale: false positives scale with the number of tests on the same data; new families will bring new
  data (fresh ticks, other markets) and their own holdouts, which is the stronger guard.
- Also noted: NQ bar pull under config.nq.yaml in progress (~$0.10-0.12 a month, quote $3.55).

## 2026-10-07 | Study 5f NQ replication run (Matteo's machine, config.nq.yaml; in sample only)
- Data: NQ.v.0 1-minute bars 2023-04..2025-12, 33 months, actual $3.55 (approved; shared ledger about
  $84.60 of $125). Databento flagged degraded days 2024-09-18, 2025-09-17, 2025-09-24, 2025-11-28 (the last
  is a skipped half day); not inspected. Calendar: 709 sessions, 690 equity, 11 rolls, 27 half days.
- Rules frozen as for ES 5f (S5b stop 0.5 EM_V, 15:30 decision, 16:00 exit, 1-tick fills, $3.98 a round
  trip, NQ tick 0.25 at $20 = 0.699 points a trade). EM_V = 0.75 x VIX(D-1) on the NQ price, unchanged.
- Result: n 621 (skipped roll 11, half day 8, prev half day 8), 2023-06-02..2025-12-31. Fade mean +0.0243
  EM_V (90% CI +0.0038..+0.0443), win 53.1%, PF 1.24, max DD 5.31 EM_V, longest losing streak 9. Without the
  best 5: +0.0126. Timing contrast +0.0269 (CI +0.0075..+0.0459); session perm p 0.012, 21-block p 0.008.
  Momentum direction -0.0285. Gamma slopes n 0 (no SPX GEX table under data_nq; descriptive only).
- Replication gate (fade mean > 0 and timing contrast > 0): PASS.
- Checks (rule 6): last session 2025-12-31, no holdout dates; rolls skipped; worst trades -0.506 = the 0.5
  stop plus friction, as built. Four of the five best NQ days (2024-05-01, 05-31, 07-12, 07-25) are also
  ES's best five, so NQ is not independent evidence (ES/NQ ~0.9 correlated): it shows the effect is not an
  ES quirk, not a second sample.
- Reading: friction is about 0.005 EM_V on NQ versus about 0.014 on ES (NQ's tick is smaller relative to
  its range), so the pre-cost fades are similar (~+0.02-0.03 EM_V) and NQ's better after-cost number is
  mostly cheaper friction. Rough size: ~3.5 NQ points (~$70) a contract a trade, before overnight/margin
  considerations. Programme count unchanged at 18 of 20; this run satisfies the over-20 NQ rule for 5f.
- Gate call is Matteo's. Options: paper trading per SPEC "Partial" (ES and/or NQ), an RTY check under the
  same frozen rules, or move on to pricing the pure order-flow family.

## 2026-10-07 | Pricing tool for the pure order-flow family and other-market fades (built; nothing pulled)
- Request (Matteo): price the pure order-flow study first, then test the fade on uncorrelated markets.
- code: src/price_menu.py, quotes only via metadata.get_cost (free), never calls timeseries. Per continuous
  symbol: trades 24h (exact, per month), trades RTH 09:30-16:00 ET (even weekday sample, extrapolated, errs
  high over holidays), ohlcv-1m 24h (exact). Default symbols ES, NQ, CL, GC, ZN, 6E. Pricing reads no market
  data, so quoting 2026 dates does not touch the holdout. tests/test_price_menu.py (fake client): pass; full
  suite passes. config diff: none.
- Prior from the ledger: Stage 3 ES trades cost ~$0.54 per ~340 RTH minutes, so full RTH is ~$0.60/session
  and the 2023-06..2025-12 sample (~640 sessions) ~$380 usage-based, well past the ~$40 of credit left.
- Alternatives found (2026-10-07, to verify): Databento Standard CME plan $199/month includes L1 schemas
  (trades, TBBO, MBP-1) for the last 12 months and OHLCV for 16+ years across CME/CBOT/NYMEX/COMEX; Sierra
  Chart historical service (packages from $26-56/month) carries CME tick data with aggressor bid/ask volume
  from about 2011-2013, exchange fees separate.
- Next on Matteo's machine: run the two price menus below, then choose the data route.

## 2026-10-07 | Price menu run (Matteo's machine, commit 5bb4849; quotes only, nothing pulled; ledger $84.61)
- 2023-06-01..2025-12-31 (675 weekdays), USD: trades 24h / trades RTH (24-day sample, extrapolated) / bars 24h
  ES 352.18 / 279.67 / 3.34; NQ 307.03 / 224.26 / 3.34; CL 80.10 / 42.85 / 3.29; GC 80.39 / 35.80 / 3.31;
  ZN 80.19 / 41.01 / 3.12; 6E 39.59 / 15.89 / 3.28.
- 2025-10-01..2026-09-30 (261 weekdays): ES 153.27 / 137.93 / 1.29; NQ 121.23 / 95.94 / 1.29; CL 35.13 /
  19.99 / 1.28; GC 36.54 / 16.02 / 1.28; ZN 30.66 / 18.01 / 1.18; 6E 11.47 / 6.22 / 1.26.
- Reading: ES RTH trades ~$0.41/session in sample (~$0.53 in the last 12 months), lower than the $0.60
  prior. A full in-sample ES trades set (~$280) is out of reach on credit. Bars for the four non-equity
  fade markets cost ~$13.0 in sample (ledger would reach ~$97.6, under the $100 line).
- Decision (Matteo): pure order flow starts as a $0 pilot on the 110 ES sessions already on disk; only if
  the pilot finds an edge, a confirmation run on freshly drawn random sessions (priced then).

## 2026-10-07 | Study 6 pre-registration DRAFT: pure order flow pilot (ES, on-disk ticks) -- awaiting approval
Family: pure order flow (new family; budget <= 4 gated variants; Go needs its own out-of-sample run).
Reuse note: the pilot reuses the Stage 3 trade windows (110 stratified sessions 2023-06..2025-12, seed in
config stage3_sample, windows t0-10..t0+45 min around Stage 2 touches). Flow features were studied there
AT LEVELS only; nothing here conditions on a level, but the sessions are not fresh and the windows are not
random clock times. Hence: the pilot can only KILL or ADVANCE, never Go.

Signal (point in time, ticks only): at decision time t, I_L(t) = sum(side x size) / sum(size) over ES
trades in [t - L, t) (side B +1, A -1, N dropped). Decision times on a clock grid every H minutes, t >= 09:40
and t + H <= 15:50 ET (keeps clear of the open and of the 5f close window). A slot counts only if [t - L,
t + H] lies inside one downloaded span with one instrument_id (rolls excluded by the Stage 3 day set).
Threshold: trade when |I_L(t)| >= the 80th percentile of |I_L| over all eligible slots of the previous 20
pilot sessions (strictly earlier dates; the first 10 sessions are warm-up and not traded).

Trades (time exit only, no stop, so no same-bar ambiguity): entry at the first trade print at or after t,
one tick adverse; exit at the first print at or after t + H, one tick adverse; $3.98 a round trip. Friction
0.5796 points a trade. PnL in points (ticks and $ per contract reported).
Variants (4; F1/F2 and F3/F4 are mirror images, so at most one of each pair can pass after costs):
  F1 continuation L = H = 5 min (trade with the aggressor)    F2 fade L = H = 5 min
  F3 continuation L = H = 15 min                              F4 fade L = H = 15 min

Pilot gates, per variant (all must hold to ADVANCE):
  1. n trades >= 300.
  2. Existence: gross (pre-cost) mean > 0 with within-session permutation p < 0.05 (shuffle the signal
     across a session's eligible slots, 1000 draws).
  3. Economics: after-cost mean >= 0.25 points (one tick) a trade, and 90% session-bootstrap CI lower bound > 0.
  4. Tail: after-cost mean without the best 5 sessions > 0.
Reported, not gated: slope of forward H-min return on I_L (session-clustered), year splits, deciles of I_L,
long/short split, mean |I_L| by time of day.

Confirmation (only for a variant that ADVANCES; registered now, run later): fresh sessions drawn uniformly
at random (seed 20261007) from in-sample 2023-06..2025-12 sessions outside the Stage 3 day set, excluding
roll and half days; RTH ES trades priced with get_cost first (~$0.41 a session); thresholds and rules frozen
from the pilot. Pass: after-cost mean > 0 and gross permutation p < 0.05 on the fresh set. Sample size fixed
when it is priced, before any pull.

Power (rough): ES 5-min SD ~5 pts, so a top-quintile trade needs a signal-return correlation near 0.08 to
clear friction at H = 5 (near 0.04 at H = 15); published intraday trade-imbalance predictability in index
futures is ~0.01-0.03 at these horizons. Pilot MDE roughly 0.3-0.4 pts at H = 5 and ~1.5-2 pts at H = 15
(slot coverage to be counted first). Expected outcome stated in advance: KILL on all four.

Build order: tests first (signal, threshold point-in-time, fills, permutation on synthetic ticks with
hand-checked answers); step 0 prints eligible slot counts from timestamps only; then the run.
Proposed config entries (not yet added): s6_grid_start "09:40", s6_grid_end "15:50", s6_horizons [5, 15],
s6_threshold_pct 0.80, s6_threshold_lookback 20, s6_warmup_sessions 10, s6_perm_draws 1000, s6_conf_seed
20261007; gates study6_min_trades 300, study6_min_expectancy_pts 0.25, study6_perm_p 0.05, study6_tail_drop 5.
Budget note: ledger $84.61. Other-market fade bars ~$13 (to ~$97.6); a confirmation run would then cross the
$100 line (~$0.41 a session; the $40.39 of credit left buys ~65 sessions after those bars).

## 2026-10-07 | Study 6 APPROVED (Matteo: "whatever you think has the strongest potential edge", config "okay", pilot first)
- Variant set revised from the draft before any data was read: the draft spent its four variants on two
  mirror pairs (a mirror pair is one two-sided test). Final set, each one-directional with its own thesis:
  F1 continuation L = H = 5 (order splitting / informed flow persists; Chordia-Subrahmanyam);
  F2 absorption fade L = H = 15 (top-quintile |I| while price did not move with the aggressor, dP x sign(I)
     <= 0: a passive participant is absorbing; trade against the aggressor);
  F3 pressure reversal L = H = 15 (top-quintile |I| and dP x sign(I) >= 1 x trailing median |dP|: transient
     price impact reverts; consistent with the intraday reversal found in 5/5f; trade against the aggressor);
  F4 absorption fade L = H = 5.
  Prior note: Stage 3's absorption-at-level confirmation lost (-0.33R), so the absorption prior is weak.
- Decisions run on a 1-minute grid with one position at a time (next decision at or after the exit time),
  not on an H-minute clock grid, so short holds get enough trades; everything else as drafted.
- Existence gate restated as the gross mean's 90% session-bootstrap CI lower bound > 0 (one-sided 5%),
  replacing the slot-shuffle permutation, which is ill-defined once positions are sequential.
- config diff (approved): params s6_grid_start "09:40", s6_grid_end "15:50", s6_flow_pct 0.80,
  s6_lookback_sessions 20, s6_warmup_sessions 10, s6_pressure_mult 1.0, s6_conf_seed 20261007, s6_variants
  {F1..F4 as above}; gates study6_min_trades 300, study6_min_expectancy_pts 0.25, study6_tail_drop 5.
  Existing params reused: entry_slippage 1, cost_rt_usd 3.98, bootstrap_draws 5000, ci_level 0.90.

## 2026-10-07 | Study 6 built tests-first (nothing run on real data)
- code: src/study6.py (slot_table, thresholds, select, price, pilot_days, build_slots, trades, summarize,
  verdict, descriptive, count, report, run); CLI --count (signal-only counts, outcome column blanked),
  default run (saves flow_trades), --report-only. Asserts on run: no holdout date, no duplicate trades,
  entry at or after the decision minute.
- tests: tests/test_study6.py (hand tape: six slots with I, dP, entry and exit prints; span start and grid
  end limits; contract change; thresholds from earlier sessions only with warm-up; selection for each kind;
  one position at a time; fills and costs; gates incl. tail) and a synthetic end-to-end test in
  test_pipeline.py. Full suite passes.
- Next on Matteo's machine: `python -m src.study6 --count`, then `python -m src.study6`.

## 2026-10-07 | Study 6 pilot run (Matteo's machine, commit 18b7632; in sample, on-disk ticks; $0)
- Count (signal only): 106 pilot sessions; slots L5/H5 27,868, L15/H15 24,453; trades F1 1,778, F2 94,
  F3 523, F4 212. Run: 96 sessions traded after the 10-session warm-up.
- Results (points a trade; gross = before slippage and costs; 90% session-bootstrap CI):
  F1 continuation 5/5:   n 1778, gross +0.063 (-0.104..+0.233), net -0.517 (-0.684..-0.347), win 40.6%, PF 0.67
  F2 absorption 15/15:   n 94,   gross -0.606 (-1.887..+0.663), net -1.186 (-2.467..+0.083)
  F3 pressure rev 15/15: n 523,  gross +0.093 (-0.400..+0.596), net -0.487 (-0.980..+0.017), PF 0.81
  F4 absorption 5/5:     n 212,  gross -0.104 (-0.467..+0.250), net -0.683 (-1.047..-0.330)
  Gates: F1 and F3 pass n only; F2 and F4 fail n; no variant passes existence, economics or tail. KILL on all
  four. Every year negative for F1, F3, F4; F2 positive only in 2023 (n 20).
- Descriptive (all eligible slots after warm-up): forward return on I_L, slope t 0.84 (5 min, corr 0.009) and
  0.91 (15 min, corr 0.020). Decile means show no monotone pattern: the extreme deciles of either sign have
  lower forward returns than the middle ones (sample drift sits in the middle), so there is no directional
  information in aggressor imbalance at these horizons. 80th percentile |I_L| is 0.089 (5 min), 0.060 (15 min).
- Reading: the raw signal is about 0.06-0.09 points a trade before friction against 0.58 points of friction,
  the same order as Stage 3 (0.6 ticks vs 2.3). Pure aggressor imbalance does not predict ES at 5-15 minutes
  in this sample. The confirmation run is not triggered; no spend. Family budget: 4 of 4 gated variants used.
- Checks: no holdout dates (run asserts); roll and half days excluded; duplicate trades asserted absent; entries
  at or after the decision minute (asserted); thresholds from earlier sessions only (unit-tested). Nothing
  looked strong, so no further rule-6 search.
- Gate call is Matteo's. Ledger unchanged at $84.61.

## 2026-10-07 | Study 7 pre-registration DRAFT: the 5f fade on four non-equity markets -- awaiting approval
Family: cross-market fade (new family; 4 gated variants = 4 markets; own out-of-sample before any Go).
Purpose (Matteo): find whether the close reversal exists outside equity indices, so independent copies could
be combined. CL, GC, ZN, 6E are weakly correlated with ES and with each other, unlike NQ.

Prior, stated before any data: Baltussen, Da, Lammers and Martens (JFE 2021) report intraday MOMENTUM, not
reversal, in the last 30 minutes across 60+ futures (equities, bonds, commodities, currencies, 1974-2020),
tied to gamma hedging. Our ES/NQ reversal in 2023-25 fits the 0DTE-era dampening that is specific to equity
index options. So the fade is not expected to generalize; expected outcome KILL on all four. The momentum
direction is reported as a descriptive number (momentum_direction_mean_em), not gated.

Rules: Study 5f frozen rules with the clock anchored to each market's settlement instead of the 16:00 equity
close (P_prev = D-1 settlement-minute close; decision 30 minutes before settlement; time exit at settlement;
stop 0.5 EM; entry and exit one tick adverse; $3.98 a round trip, nudge $5.97; roll, half-day and
after-half-day sessions skipped):
  X1 CL.v.0 settle 14:30 ET (decision 14:00)   tick 0.01,     $1,000/pt   friction ~0.024 pts
  X2 GC.v.0 settle 13:30 ET (decision 13:00)   tick 0.10,     $100/pt     friction ~0.24 pts
  X3 ZN.v.0 settle 15:00 ET (decision 14:30)   tick 1/64,     $1,000/pt   friction ~0.035 pts
  X4 6E.v.0 settle 15:00 ET (decision 14:30)   tick 0.00005,  $125,000/pt friction ~0.00013
Unit: VIX does not describe these markets, and two of their own vol indexes (TYVIX, EVZ) were discontinued,
so all four use one rule: EM_R = s5_rv_factor x SD of the last s5_rv_sessions daily log returns of the
settlement-minute closes (strictly before D) x P_prev, factor 1.0, 20 sessions (point in time; EM_V on ES
is about one daily SD, so the units are comparable). Bridge check (descriptive): ES 5f rerun with EM_R.
Friction in EM units is ES-like for CL, GC, 6E (~0.01-0.025) but ~0.07 for ZN, so ZN starts handicapped.

Gates per market (the Study 5 in-sample gates, unchanged): sessions >= 400; fade mean >= 0.02 EM and 90%
session-bootstrap CI lower bound > 0; timing contrast CI lower bound > 0 with session and 21-block
permutation p < 0.05; mean without the best 5 sessions > 0. Reported, not gated: the NQ-style replication
read (mean > 0 and contrast > 0), years, pooled equal-weight fade across the four markets, cross-market
correlation of daily fade PnL. A market that passes may go to its own holdout, 2026-01..09 (never looked at
for these markets), only after Matteo says "run the holdout"; holdout rule as 5f (positive and >= half the
in-sample mean).
Power: the ES fade had SE ~0.010 EM at ~620 sessions; passing needs a true effect near 0.03 EM or more.

Data and cost: 1-minute bars 2023-04-01..2025-12-31 per market (April-May for the 20-session vol warm-up),
~$3.4 each from the price menu, ~$13.7 in four pulls of under $5 each, each priced with get_cost first;
ledger $84.61 -> ~$98.3, under the $100 line. A later holdout pull (~$1 a market) would cross the line, so ask.
Free inputs: FRED SPX/VIX daily under each overlay (only for the equity-session calendar).

Proposed config (needs approval): params s5_em_unit {value: "vix"} (overlays set "realized"),
s5_rv_sessions {value: 20}, s5_rv_factor {value: 1.0}; overlays config.cl.yaml, config.gc.yaml, config.zn.yaml,
config.6e.yaml (data root data_<mkt>, symbol, tick, point_value, rth_close = settlement time, s5_decision_time
and s5_exit_time as above, s5_em_unit "realized", shared ledger). config.yaml values otherwise unchanged.
Build: tests first (settlement anchors per overlay, EM_R by hand, tick grids for 1/64 and 0.00005, the ES
VIX path unchanged), then price, approve, pull, run --fade-replication per market plus a cross-market report.

## 2026-10-07 | Study 7 revised to MOMENTUM and APPROVED (Matteo: "ok go"), before any data
- Direction changed from the draft's fade to Study 5's momentum, on the prior alone (no CL/GC/ZN/6E data has
  been read): Baltussen et al. report last-30-minute momentum across bonds, commodities and currencies; the
  ES/NQ reversal is attributed to equity-index 0DTE hedging, absent at that scale in these markets.
- Gated rule per market: S5a (no stop, hold to settlement), as in the literature. Not gated: S5b, the fade
  mirror. Expected outcome revised: existence possible in some markets; KILL after costs most likely.
- Everything else as in the draft (settlement anchors, EM_R, Study 5 gates, holdout only on Matteo's word).
- config diff (approved): config.yaml params s5_em_unit "vix", s5_rv_sessions 20, s5_rv_factor 1.0 (ES and NQ
  unchanged); new overlays config.cl.yaml, config.gc.yaml, config.zn.yaml, config.6e.yaml (data roots
  data_<mkt>, git-ignored; shared ledger).

## 2026-10-07 | Study 7 built tests-first (nothing run on real data)
- code: study5.session_trade takes sigma and builds EM_R when s5_em_unit is "realized" (skip reason no_vol);
  study5.run computes sigma per session via study7.sigma_by_session. src/study7.py: realized_sigma,
  sigma_by_session, market_verdict, momentum_market (--market), pooled_daily and cross_report (--cross),
  bridge (--bridge: ES under EM_R, save=False).
- tests: tests/test_study7.py (overlay values for all four, ES unit unchanged, sigma by hand 0.115857, roll
  returns skipped, sigma strictly before D, 1/64 and 0.00005 stop grids, a CL session by hand, no_vol, VIX path
  unchanged, gated variant S5a, pooled equal weight) and a synthetic EM_R end-to-end test. Full suite passes.
- Bug caught by the tests: YAML read the 6E tick "5e-05" as a string; written as 0.00005.
- Next on Matteo's machine, per market: ingest_daily (free), bars 2023-04-01..2025-12-31 --price-only, then
  --approve-usd 4.00 (each ~$3.4; ledger stays under $100), then study7 --market; then --cross and --bridge.

## 2026-10-07 | Study 7 run (Matteo's machine, commit 2cb2fb0; in sample 2023-06-02..2025-12-31)
- Pulls (each priced with get_cost first, --approve-usd 4.00 each): CL $3.50, GC $3.52, ZN $3.32, 6E $3.49 for
  1-minute bars 2023-04..2025-12; ledger $84.61 -> ~$98.43 (under the $100 line). Degraded days flagged by
  Databento as before (2024-09-18, 2025-09-17, 2025-09-24, 2025-11-28). Calendars: CL 33 rolls, GC 13, ZN 11
  (26 early-close days, SIFMA), 6E 11.
- S5a momentum (gated), EM_R units, 90% CI; timing contrast (CI); session / block permutation p; pre-cost
  = mean + friction:
  CL n 610: -0.0131 (-0.0256..-0.0007); contrast +0.006 (-0.006..+0.019); p 0.18/0.19; slope t -0.08; friction 0.019; pre-cost +0.006
  GC n 626: -0.0109 (-0.0203..-0.0017); contrast +0.001 (-0.008..+0.011); p 0.40/0.39; slope t +0.03; friction 0.011; pre-cost 0.000
  ZN n 624: -0.0657 (-0.0772..-0.0542); contrast +0.027 (+0.015..+0.038); p 0.001/0.001; slope t +3.18; friction 0.092; pre-cost +0.027
  6E n 618: -0.0200 (-0.0305..-0.0088); contrast +0.010 (-0.001..+0.021); p 0.073/0.062; slope t +0.81; friction 0.030; pre-cost +0.010
  Gates: KILL on all four (economics fails everywhere; mean ex-best-5 negative everywhere). Existence PASSES in
  ZN only. Not gated: S5b KILL in all four; fade mirror after costs negative in all four (CL -0.025, GC
  -0.012, ZN -0.119, 6E -0.039); years negative in every market and year.
- Cross-market (descriptive): pooled equal-weight momentum -0.027 (CI -0.033..-0.021); daily PnL correlations
  near zero except ZN-6E +0.20. BUG in the descriptive report: "pooled_fade_mirror_mean_em +0.027" was minus the
  momentum mean, which ignores that the fade pays its own costs; the per-market fades after costs are all
  negative. Fixed (study7.direction_frames; cross_report now reports per-market and pooled fades after costs);
  no gate used it.
- ES bridge (EM_R instead of EM_V, descriptive): S5a -0.034 (EM_V -0.037); 5f fade +0.0066 (CI -0.011..+0.024),
  timing contrast +0.021 (CI +0.003..+0.037); median EM_R / price 0.0075. The unit change does not move ES.
- Rule-6 checks: in sample only (last 2025-12-31); roll, half-day and after-half-day sessions skipped; no
  duplicate bars; no |r_L30| above 3 EM. ZN's existence pass deserves suspicion of a narrow cause: its five best
  days (2025-09-17, 2024-12-18, 2025-10-29, 2025-07-30, 2023-07-26) are all FOMC days, and the ZN/6E window
  (14:30-15:00 ET) holds the Fed chair's press conference. 6E's best and worst days are FOMC days too, hence
  the ZN-6E correlation. Added a descriptive FOMC split (fomc_split: event-day mean, and mean, contrast and
  slope without the 21 FOMC days) to --market; free to rerun from the bars on disk.
- Reading: the literature's momentum shows up only in ZN, and possibly only on Fed days; it is worth about
  0.6 of a 1/64 tick before costs against two ticks of cost. Commodities and FX show nothing. The fade does not
  generalize either. Family closed: 4 of 4 used.
- Gate call is Matteo's.

## 2026-10-08 | Study 7 KILL accepted; hypothetical bankroll of the 5f fade built (nothing run on real data)
- Matteo accepted Study 7's KILL and asked for the inverse in other markets (answered from the run: the fade
  is <= 0 before costs in CL/GC/ZN/6E, so no test). Then asked for a $30,000 hypothetical bankroll of the 5f
  fade on NQ over the in-sample period already on disk, knowing it is not out-of-sample evidence.
- Choices (Matteo, via questions): show all sizing schemes; risk-based at both 1% and 2%; micro cost $3.98 a
  round trip until the broker confirms (never lower than real, rule 5).
- code: src/bankroll.py (fade_legs from the saved Study 5 table, simulate per scheme with compounding for
  micro_risk, stress rows with the registered entry_slippage nudge on entry and exit, summary with CAGR,
  max drawdown, months, Sharpe, max notional / equity). Descriptive only, never a gate.
- tests: tests/test_bankroll.py (three hand sessions: legs, 1 NQ, 1 MNQ, 1% risk in whole micros with
  compounding, stress, too-small-to-size) and a synthetic end-to-end test. Full suite passes.
- config diff (approved): new bankroll section (start_usd 30000, full_contracts 1, micro_point_value 5.0 for
  MES with config.nq.yaml setting 2.0 for MNQ, micro_cost_rt_usd 3.98, risk_pcts [0.01, 0.02]).
- Next on Matteo's machine: python -m src.bankroll under config.nq.yaml (and under config.yaml for ES).

## 2026-10-08 | Hypothetical bankroll run of the 5f fade (Matteo's machine, commit af109ff; in sample, descriptive)
- $30,000 start, 2023-06-02..2025-12-31, one fade trade a session, SPEC rule-5 fills, $3.98 a round trip on
  every contract including micros (pessimistic for micros until the broker confirms).
- NQ (621 sessions): 1 NQ fixed $30,000 -> $81,948 (+173%, CAGR 47.6%), max DD $14,127 (27.7%), worst day
  -$4,274, worst month -$8,341, 61% positive months, Sharpe 1.37, losing streak 9, notional up to 12x equity;
  stress (+1 tick each side) $75,738. 1 MNQ fixed $32,970 (+9.9%, DD 5.3%). Risk 1% in MNQ $32,511 (+8.4%,
  DD 10.1%, at most 3 micros); risk 2% $35,518 (+18.4%, DD 20.3%, at most 6). Stress keeps every NQ scheme
  positive.
- ES (618 sessions): 1 ES fixed $30,000 -> $43,490 (+45%, CAGR 15.5%), DD $11,591 (27.9%), Sharpe 0.68; stress
  $28,040 (-6.5%, DD 45%). Every MES scheme loses (-2.9% to -19.5%), because the ES edge (~$25.8 gross a contract,
  ~$2.58 an MES) is smaller than $3.98.
- Reading: per full contract the NQ fade made about $84 a trade net ($87.6 gross); the in-sample 90% CI of the
  NQ mean (+0.0038..+0.0443 EM_V) maps to roughly $13..$152 a trade, i.e. about $8k..$95k over the period on
  one NQ. The five best sessions carry about half the profit (mean without them +0.0126 vs +0.0243). Micro
  results are dominated by the assumed $3.98 micro cost (about 45% of the MNQ gross edge). All of this is the
  sample the fade was found on (ES) or a ~0.9-correlated twin (NQ): it describes the ride, not the expectation.
- No gate, no spend, no holdout read.

## 2026-10-08 | NQ holdout for the 5f fade: pre-registration (Matteo: "lets run it on that new data"), before any read
- Authorization: Matteo asked to run the fade on the new data (the NQ holdout, 2026-01-02..2026-09-30), which no
  analysis has touched for NQ. Recorded here as his "run the holdout" for this one NQ run.
- Rule (frozen, unchanged from 5f): S5b reversed, 15:30 ET decision, 16:00 exit, 0.5 EM_V stop, EM_V from VIX
  (NQ overlay), entry and exit one tick adverse, $3.98 a round trip. Reference = the NQ in-sample fade mean
  +0.0243149640941187 EM_V (RUNLOG Study 5f NQ replication). Gate (the 5f holdout rule): holdout mean > 0 and
  >= holdout_min_fraction (0.5) x reference = 0.01216 EM_V. Reported: CI, timing contrast, permutations, tail.
- Data: NQ 1-minute bars 2026-01..2026-09, priced with get_cost first (~$0.97 expected from the price menu),
  --approve-usd 1.50; ledger ~$98.43 -> ~$99.4, under the $100 line.
- Bankroll on the holdout (descriptive, fresh $30,000 on the first holdout session): src/bankroll.py --holdout
  added (refused without GAMMA_EDGE_RUN_HOLDOUT=1; reads close_momentum_trades_holdout). In-sample bankroll
  rerun at the broker micro rate as well.
- config diff (approved): bankroll.micro_cost_rt_usd 3.98 -> 1.18 (Matteo's broker, all-in micro round trip).
- tests: broker-rate micro case by hand (8.82, -21.68, 2.82), holdout refused without the flag. Full suite passes.

## 2026-10-08 | NQ holdout attempt FAILED before any holdout read (Matteo's machine, commit be52e3f)
- Bars: NQ 2026-01..09 priced $0.97, pulled (each month logged; Databento flagged 2026-01-31, 03-15, 03-16,
  03-21, 04-10, 05-24, 08-29 as degraded, mostly weekends). Ledger ~$99.40.
- Then the calendar rebuild ran out of memory (numpy MemoryError allocating 37.8 MiB; the next command failed
  importing statsmodels with MemoryError too): the Windows machine was short of free RAM. The calendar kept no
  2026 sessions, so `study5 --fade-holdout` found 0 sessions and crashed on an empty table (KeyError 'date'),
  and `bankroll --holdout` found no holdout table. No holdout session was computed or printed: the one-shot
  NQ holdout run is NOT spent and the pre-registration stands unchanged.
- In-sample bankroll at the broker micro rate ($1.18) did run (descriptive): 1 MNQ $30,000 -> $34,709 (+15.7%,
  DD 4.6%, Sharpe 1.18; stress $34,088); risk 1% -> $35,582 (+18.6%, DD 9.4%, max 3 micros); risk 2% ->
  $46,685 (+55.6%, DD 17.6%, max 7 micros; stress $44,160); 1 NQ unchanged at $81,948.
- Fixes: rebuild_calendar reads only ts_open_utc and instrument_id (about half the memory); run_fade_holdout
  returns gate NO_DATA instead of crashing when the calendar has no holdout sessions, and says the run is not
  spent. Tests for both; full suite passes.
- Next: free memory (close other programs, fresh PowerShell), `python -m src.ingest_futures calendar` under the
  NQ overlay (no spend: bars are on disk), holdout-check, then the pre-registered holdout run.

## 2026-10-08 | NQ holdout run of the 5f fade (Matteo's machine, commit 7e2c52c; one shot, as pre-registered)
- Calendar rebuilt (902 sessions, 877 equity, 14 rolls); holdout-check ok (2026-01..09 bars present, daily to
  2026-10-06). 184 holdout sessions 2026-01-02..2026-09-30 (3 roll days skipped).
- Fade (S5b reversed), EM_V: mean +0.0072 (90% CI -0.022..+0.037), win 49.5%, PF 1.08, max DD 3.06 EM_V, losing
  streak 5; mean without the best 5 sessions -0.010; timing contrast +0.006 (CI -0.025..+0.036); session
  permutation p 0.38, block p 0.49; momentum direction -0.0035.
- Gate (frozen): holdout mean > 0 and >= 0.5 x 0.02431 = 0.01216. +0.0072 < 0.01216: FAIL.
- Bankroll on the holdout (descriptive, fresh $30,000, micros at $1.18): 1 NQ -> $34,968 (+16.6%) but max DD
  $14,534 (31.2%), 44% positive months, Sharpe 0.68, stress $33,128; 1 MNQ -> $30,353 (+1.2%); risk 1% ->
  $30,280 (+0.9%); risk 2% -> $31,634 (+5.4%, DD 8.5%). In-sample bankroll rerun identical to the previous entry.
- Reading: in 2026 the NQ fade kept its sign but not its size (about 0.3x in sample), and neither the mean nor
  the timing is distinguishable from zero; the 1-NQ profit sits on a handful of days (ex-best-5 negative). Across
  the four looks at the fade: ES in sample +0.008, ES holdout +0.0145 (pass), NQ in sample +0.024, NQ holdout
  +0.0072 (fail). Consistent with a small positive effect near +0.01 EM_V that nine months cannot resolve.
- Checks: holdout read once with the env flag and Matteo's instruction; same frozen rules; no parameter changed
  after the result. Ledger ~$99.40. Gate call is Matteo's.

## 2026-10-08 | Thesis v2: clean-slate review from the market maker's side (not a run)
- Matteo asked for a clean-slate review of the repository plus two research briefs (Additional Intraday Edge
  Research; First-Principles Alpha Research) and a change of thesis: think from the market makers' side
  (obligations, constraints, regulations) to find exploitable, compelled behaviour.
- Written to THESIS.md: the obligations and constraints of options market makers, Treasury primary dealers,
  FX fixing banks, LETF sponsors, pensions, index trackers and banks at period ends, checked against primary
  sources; seven rules for the new thesis (named compelled actor; gross concession >= 3x our friction; horizon
  of hours to days; clock from the mandate); our studies reread through that lens; candidates scored.
- Key fact checks: NY Fed SR 1188 puts post-2014 Treasury auction pressure at under half of 0.7-1.2 bp, so the
  auction reversal is about 1-2 ZN ticks gross against 2.25 of friction (not worth a study). Hartley-Schwarz
  month-end Treasury returns are about 20 bp at the 10-year, Sharpe ~1 after costs, present in futures.
  Harvey et al. rebalancing: about -17 bp next-day equity return when stocks are overweight.
- Recommendation (Matteo's call): new family "month-end compelled flow" (M1 month-end Treasury demand in ZN,
  M2 pension rebalancing in ES/ZN), preceded by a free FRED existence check; M3 FX fix in 6E as a separate free
  check with a cost KILL expected. No spend, no pre-registration yet.

## 2026-10-08 | Study 8 pre-registration DRAFT: month-end compelled flow (Track A) -- awaiting approval
Matteo: on board with multi-day holds if the edge is there; also wants a fast small-edge track (Track B below).
Family: month-end compelled flow (new; at most 4 gated variants; own out-of-sample before any Go). Reuses the
2023-06..2025-12 ES and ZN bars already on disk; the ES 2026 data was read only for the 15:30-16:00 close window,
and ZN 2026 is untouched. Both are stated here as the family's out-of-sample period, to be opened once.

Step E0, existence check (free, descriptive, no gate). FRED daily series DGS10 (10-year CMT yield, from 1990)
and SP500 (about 10 years on FRED), via the existing FRED loader. Reported, per calendar month: the 10-year yield
change over the last k trading days, k = 1..5, its mean in bp with Newey-West t, split 1990-2019 (the paper's
era) and 2020-2026-09 (post-publication); and the next-day S&P return on the last trading day regressed on
month-to-date (S&P return minus 10-year price-return proxy). The period after the paper (2020 on) is reported
first. This decides whether M1 and M2 are worth building; it is not a gate.

M1, month-end Treasury demand (gated). Long 1 ZN at the 15:00 ET settlement-minute bar of the 5th-to-last
trading day of each month (open + 1 tick); exit at the same bar on the last trading day (open - 1 tick);
$3.98 a round trip. Roll rule: the position is on the front contract at entry; if ZN rolls inside the window
(late Feb/May/Aug/Nov), the session is skipped unless the back-month bars are bought (priced separately).
Unit: ZN ticks (1/64) and EM_R (20-day realized vol x price, from the Study 7 tooling).
M2, rebalancing pressure (gated). At the ES 16:00 close two trading days before month end, compute
R = month-to-date ES return minus month-to-date ZN return. If R > 0 (stocks overweight), short 1 ES; if R < 0,
long 1 ES. Exit at the 16:00 close of the last trading day. Entry and exit one tick adverse, $3.98.
Variants: M1 and M2 only (2 of 4); no threshold, no window search.
Gates per variant (proposed): n >= 25 in sample (month-ends); after-cost mean > 3 x friction (M1 > 6.75 ZN
ticks, M2 > 1.75 ES points); 90% month-block bootstrap lower bound > 0; same sign in the 2026 out-of-sample
months; mean without the best 3 months > 0. With ~31 in-sample months the study is underpowered by design:
it can only find an effect near the published size (~0.2-0.25 sigma needs ~100+ events), so E0's longer history
carries the existence question and our futures sample carries the costs question. Expected outcome: M1 positive
before costs (published, in futures), M2 uncertain.
Proposed config (needs approval): s8_m1_entry_days_before_end 4 (enter at the close of T-4, i.e. the 5th-to-last
day), s8_m1_entry_time "15:00", s8_m2_entry_days_before_end 2, s8_fred_series [DGS10, SP500], s8_e0_k_max 5,
gates study8_min_events 25, study8_min_friction_multiple 3, study8_tail_drop 3.

## 2026-10-08 | Track B design note: passive liquidity in compelled windows (not pre-registered)
- Arithmetic: of ES's ~2.3-tick round-trip cost, ~2 ticks are the spread paid in and out; measured gross edges
  at minute scale are 0.25-0.6 tick. Repetition cannot fix a negative after-cost edge; only not paying the spread
  can. Passive (maker) execution earns it, but takes adverse selection.
- Hypothesis for later: resting orders filled during compelled-flow windows (close auction lead-in, month-end,
  fixes) suffer less adverse selection than at random times, because the counterparty is forced, not informed.
- Needs L1 quotes (MBP-1/TBBO) for the chosen windows; to be priced with get_cost before any proposal
  (Databento Standard plan alternative: $199/month, 12 months of L1). Conservative fills: SPEC rule 5 (fill only
  on a print one tick through) as the primary scenario, queue estimate as secondary.

## 2026-10-08 | Study 9 APPROVED and built tests-first: can a passive ES trader earn the spread? (report-only)
Matteo: "lets do study 9 ... realized-spread study", plus the ES level reversion redone with $0 commission and
perfect resting-order execution at the level, at holds of 1, 3, 5, 10, 15, 30, 45 and 60 minutes.
Both parts are measurements, not gated variants; their advance rules decide only whether to price L1 quote data
for a proper queue study. In sample only.

Part A, realized spread (110 on-disk Stage 3 tick sessions). Quote inferred from the tape (ES one tick wide: buy
aggressor at the ask, mid = p - 1/2 tick; sell aggressor at the bid, mid = p + 1/2 tick), validated by the share of
opposite-side consecutive prints exactly one tick apart. RS_i(D) = -s_i (mid(t_i + D) - p_i)/tick at D = 5, 30, 60 s,
contract-weighted, session-bootstrap CI. Classes: clearing (last print of a same-price same-side run after which the
next print goes through the level: the back-of-queue fill) vs other. Splits: time of day (09:30-10:00, 10:00-11:30,
11:30-14:00, 14:00-15:30, 15:30-16:00) and trade size (1, 2-9, 10-49, 50+). Fees per side: $0, $2.79 and $3.98 a
round trip ($2.79 = $0 commission: CME exchange fee $1.386 + regulatory $0.011 per side, IBKR schedule 2026-10-08).
ADVANCE (to pricing L1 data) if clearing fills in some time bucket have a 60 s CI lower bound and a 30 s mean above
the $2.79 per-side fee (0.112 tick). Otherwise KILL passive ES market-making at a retail queue position.

Part B, level reversion with a resting order (all in-sample Stage 2 touches, all groups incl. placebo). Order at L
from the touch bar for 10 bars; long at support, short at resistance. perfect: filled at L when a bar trades at L,
exit at the close of fill bar + H, no slippage (Matteo's explicit upper-bound scenario; it departs from SPEC rule 5
on purpose and can never ADVANCE alone). conservative: filled only when a bar trades through L by a tick, exit one
tick worse (rule 5). Holds past 15:59 close at the last RTH bar (share reported). Fees $0 / $2.79 / $3.98.
ADVANCE if conservative, real levels, net of $2.79: 90% CI lower bound > 0 and real minus placebo > 0 at some hold.
Expected: the perfect case looks positive at short holds (it counts touches that bounce without trading through,
which a queued order rarely gets); the gap between perfect and conservative is the adverse-selection cost.

config diff (approved): params s9_rs_horizons_sec [5,30,60], s9_tod_edges, s9_size_edges [1,2,10,50],
s9_lr_horizons_min [1,3,5,10,15,30,45,60], s9_fill_window 10, s9_fee_scenarios_usd [0,2.79,3.98]; gate
study9_advance_fee_usd 2.79.
Build: src/study9.py; tests/test_study9.py (inferred mid, RS by hand at 30/60 s, span-end drop, clearing flag,
spread check, buckets, resting fills perfect vs trade-through, reversion P&L long and short with truncation, fee
points, bootstrap) and a synthetic end-to-end test (same fill bar: conservative = perfect - 1 tick). Full suite passes.

## 2026-10-08 | Study 9 run (Matteo's machine, commit aa86bdf; in sample; $0)
Part A, realized spread (106 sessions with ticks, 89.4 M contracts):
- Quote check: of 7.94 M consecutive opposite-side print pairs, 37.2% are one tick apart and 62.4% at the same
  price (level flips); only ~0.4% imply a wider spread, so the one-tick inferred quote holds.
- Passive result per fill, ticks, contract-weighted (90% session CI), at 5 / 30 / 60 s:
  all fills       +0.002 (-0.005..+0.009) / -0.008 (-0.021..+0.005) / -0.020 (-0.040..-0.001)
  clearing fills  -0.574 (-0.617..-0.531) / -0.605 (-0.671..-0.540) / -0.633 (-0.725..-0.543)   [back of queue]
  other fills     +0.062 (+0.058..+0.067) / +0.054 (+0.044..+0.065) / +0.044 (+0.029..+0.060)   [front of queue]
  Fee per side: 0.112 tick at $2.79 a round trip, 0.159 at $3.98. Clearing fills lose 0.47-0.68 tick before fees
  in every time bucket; other fills earn under half the $0-commission fee. Only the passive side of 1-lot
  aggressor trades (non-clearing) clears the $2.79 fee (+0.14 tick, net +0.03), and a resting order cannot choose
  its counterparty. Advance rule (clearing, 60 s CI lb and 30 s mean > fee in some bucket): no bucket. KILL.
Part B, level reversion with a resting order (9,113 touches: 6,888 real-level, 2,225 placebo):
- perfect (fill at the level on touch, exit at the close, no slippage), real levels, gross points per trade by hold
  1/3/5/10/15/30/45/60 min: +0.081 / +0.033 / -0.033 / -0.088 / -0.123 / -0.119 / -0.282 / -0.667; net of $2.79
  +0.025 at 1 min (CI -0.157..+0.170), negative from 3 min on, CI below zero at 60 min. Placebo levels beat real
  ones at every hold (1 min +0.19). Fill rate 91%.
- conservative (fill only on a one-tick trade-through, exit one tick worse), real levels: -0.42 / -0.48 / -0.55 /
  -0.59 / -0.59 / -0.60 / -0.75 / -1.18 points gross; every CI below zero; real minus placebo negative at every
  hold. Fill rate 86%. The ~0.5-point gap between perfect and conservative (one tick of exit slippage plus about one
  tick of adverse selection on trade-through fills) is the cost of not being at the front of the queue.
- Advance rule (conservative, real, net of $2.79, CI lb > 0 and real > placebo at some hold): none. KILL.
- Not acted on (64 cells, no correction, data seen): the "both" group (gamma and structural levels together, n
  ~1,120) is positive in the perfect case at most holds and +0.48 conservative at 30 min. Recorded only; it could
  be revisited only as a new pre-registered test on data not yet seen.
Reading: the average passive ES fill earns about zero within a minute: the half-tick effective spread is matched
by about half a tick of adverse selection. The front of the queue keeps a few hundredths of a tick; the back of
the queue, where a retail resting order sits, pays about 0.6 tick per fill. Even with perfect fills and $0
commission, level reversion is worth at most about a third of a tick at a 1-minute hold, and real levels do worse
than random prices. Track B on ES at a retail queue position is closed.

## 2026-10-08 | Study 8 APPROVED and built tests-first: month-end compelled flow (Track A); no run yet
Matteo: "let's do track A". Taken as approval of the Study 8 draft above as written (E0, M1, M2; 2 of 4
variants in the new family). Build: src/study8.py; tests/test_study8.py (15 tests: month-end calendar with
incomplete months, yield changes by hand, par-bond return against a cash-flow sum, rebalancing frame by hand,
month-to-date return across a roll, hold P&L long and short with roll-in-window skip, M2 direction, friction,
all four gate checks, synthetic M1/M2 pipelines, E0 end to end with 2026 rows on disk sealed out, report).
Full suite passes.
Changes from the draft (both conservative, flagged for Matteo):
- E0 stops at 2025-12-31, not 2026-09. Month-end 10-year yield changes in 2026 are, in effect, M1's
  out-of-sample result (ZN is a 10-year future), and the S&P last-day returns of 2026 are M2's. Reading them in
  E0 would open the family's out-of-sample period early. Rule 2 applies; E0 goes through the seal.
- E0's history start and era split are config entries (s8_e0_start 1990-01-02, s8_e0_split 2020-01-01), the
  values stated in the draft. The 10-year price proxy is a par bond repriced at DGS10 (10 years, semiannual,
  calendar-day carry): structural, no tunable duration.
Implementation choices (stated before any number is seen):
- Month-end = the last session of each complete month in the market's own calendar; entry = the session k before
  it (M1 k=4: 5th-to-last; M2 k=2: 3rd-to-last). M1 holds 4 sessions, M2 2.
- M1 stays on the entry contract; when ZN.v.0 switches contract inside the window the month is skipped
  (roll_in_window). ZN's volume roll sits in late Feb/May/Aug/Nov, close to the T-4 entry, so up to ~10 of ~31
  in-sample months could be skipped and M1 could fail n >= 25 mechanically. `--count` reports tradable months
  and skip reasons without any P&L, so the fix (back-month bars for those windows, priced with get_cost) can be
  decided before results exist.
- M2 decides on the 16:00 ES close (bar closing 16:00) and the 15:00 ZN settlement close of the entry day, enters
  at the open of the ES 16:00 bar one tick adverse (Study 5's clock), exits at the open of the 16:00 bar on the
  last session one tick adverse. Month-to-date returns are chained daily log returns on one contract from the
  previous month's last close; a return across a contract change is skipped and counted (mtd_returns_skipped).
- Gate thresholds computed from friction = 2 ticks + $3.98: M1 3 x 2.255 = 6.76 ZN ticks; M2 3 x 0.580 = 1.74
  ES points (the draft's rounded 6.75 and 1.75).
- Descriptive only: each variant's by-year means, EM_R units, win rate; the same hold from every session (drift
  reference: is it month-end or just 2023-25 drift?); M2's long-side return regressed on R (Harvey et al.
  predict a negative slope).
Out-of-sample step (ZN 2026 bars not on disk; ES 2026 16:00 bars on disk): built only if a variant passes in
sample, and run only on "run the holdout".
Run order for Matteo (no overlay set, $0): python -m src.study8 --count; python -m src.study8 --e0;
python -m src.study8.
config diff (approved with the draft): params s8_m1_entry_days_before_end 4, s8_m1_entry_time "15:00",
s8_m2_entry_days_before_end 2, s8_fred_series [DGS10, SP500], s8_e0_k_max 5, s8_e0_start "1990-01-02",
s8_e0_split "2020-01-01"; gates study8_min_events 25, study8_min_friction_multiple 3, study8_tail_drop 3.

## 2026-10-08 | Venue question (no study, no spend)
Matteo asked whether crypto or prediction markets hold more edge, and how a high-frequency small edge could be
reached in markets at all. Answer given: the ES edge is the residual to queue priority (Study 9), so look where
priority or payment does not go by speed. Ranked: (1) US equity/index options as a priority customer (priority over
market makers at the same price on many exchanges, usually no exchange fee, up to 390 orders a day on average;
ISE filing SR 34-62152); risk = stale quotes picked off; needs intraday option quotes, to be priced; (2) CME
pro-rata products (SR3 outrights: top order, then pro rata with a 2-lot minimum; ZT; grains; per Databento's CME
matching summary), capital-heavy; (3) prediction-market making (Polymarket: makers pay no fee and get 15-25% of
taker fees back; Kalshi taker 0.07 p(1-p), maker 0 in most series), outside scope. Crypto is not more edge by
default: Hyperliquid retail maker 0.015% / taker 0.045% versus ES ~0.001% of notional per round trip; rebates start
at 0.5% of exchange maker volume. Nothing scoped; a Study 10 desk scope of option 1 offered.

## 2026-10-08 | Study 8 run (Matteo's machine, commit 491dcb3; in sample; $0; FRED downloaded once)
Matteo ran --count, --e0 and the M1/M2 run in one go, so the back-month decision for M1 was not taken separately;
M1 runs on the months the on-disk ZN.v.0 bars allow.
Count: M1 21 tradable month-ends of 33 (2023-04..2025-12): roll_in_window 9 (of 11 roll months), no_entry_bar 3
(the T-4 entry fell on a half day: Christmas Eve, the day after Thanksgiving). M2 30 of 33: no_mtd 1 (first
month, no previous month-end close), no_exit_bar 2 (last session of November was a half day).
E0 (FRED, 1990-01-02..2025-12-31, sealed at holdout_start), month-end 10-year yield change over the last k days:
  2020-2025 (72 months), bp (NW t) vs the all-days mean of the same window:
    k1 -0.96 (-1.5) vs +0.15; k2 -1.51 (-2.0) vs +0.30; k3 -2.35 (-2.2) vs +0.45; k4 -2.57 (-2.2) vs +0.60;
    k5 -2.62 (-1.8) vs +0.75
  1990-2019 (360 months): k1 -1.39 (-4.9); k2 -2.03 (-5.2); k3 -2.39 (-4.7); k4 -2.24 (-4.1); k5 -2.44 (-4.1);
    all-days -0.08 to -0.41
  Rebalancing (S&P last-day return on MTD stocks minus a 10-year par bond): slope t -0.24 (2020-25, 72 months),
  -0.88 (2016-19, 38). Last-day mean -10.5 bp vs +5.9 all days (2020-25), about 1 SE; -15.7 bp when stocks led,
  -1.4 bp when bonds led. No reliable Harvey et al. effect in the last-day return.
M1 (long ZN T-4 settlement to T0 settlement), ZN ticks after costs: n 21, mean -0.30 (90% month CI -12.7..+12.5),
  gross -0.05, win 48%; without the best 3 months -10.5; by year 2023 -3.7, 2024 -2.7, 2025 +5.5 (7 each).
  Threshold 3 x 2.255 = 6.76 ticks. Checks: n FAIL (21 < 25), friction FAIL, CI FAIL, tail FAIL. KILL.
  Descriptive: the same 4-session long from every session averaged -6.6 ticks (the 2023-25 bond sell-off), so the
  month-end windows were ~6 ticks better than any window; not gated, not significant (SE ~7.7 ticks).
M2 (fade month-to-date stock-bond outperformance, T-2 close to T0 close), ES points after costs: n 30, mean +8.73
  (CI -6.6..+24.5), gross +8.81, win 53%, short 67% of months; without the best 3 months -1.93; by year 2023 -4.3
  (8), 2024 +26.8 (11), 2025 +0.2 (11). Threshold 1.74. Checks: n PASS, friction PASS, CI FAIL, tail FAIL. KILL.
  Long-side return on R: slope t -0.35 (no relation). Any-day 2-session long +6.0 points (drift).
  Rule 6 on the positive mean: inputs close at or before 16:00:00, entry at the 16:00 bar's open; month-ends come
  from the exchange calendar (known in advance); one row per month; last month 2025-12 (no holdout). The mean is
  three months of 2024; the regression and E0 both show no relation to R.
Reading: the Treasury month-end effect exists and has not decayed: 10-year yields fall ~2.5 bp over the last 4
sessions of the month in 2020-25 (t -2.2), the same size as 1990-2019 (t -4.1), against a +0.6 bp drift. At
roughly 4 ZN ticks per bp (DV01 ~$60-70, depends on the cheapest-to-deliver) that is ~10 ticks gross against
2.25 ticks of friction, about the published Sharpe (~0.7 a year from 12 trades). Our 21 ZN month-ends cannot
resolve it (SE ~7.7 ticks; ~90 month-ends are needed for t = 2 at that size). Rebalancing (M2): no evidence in
E0 or in futures. Both variants KILL by the rules; 2 of 4 used in the family.

## 2026-10-08 | Study 8 KILL accepted; Study 10 pre-registration DRAFT: priority-customer option liquidity (pilot)
Matteo: "Accept the KILL but I want to move on to a small test of the options idea." Study 8 family closed at
2 of 4 (M1 effect noted as real in yields, unproven in futures; not pursued).

Study 10 question: does a non-professional ("priority customer") limit order resting at the NBBO of a US option
earn the half-spread net of adverse selection and retail fees? On most US options exchanges a priority customer
(<= 390 orders a day on average in a month) is filled ahead of market makers at the same price and pays no or low
exchange fees, so on its exchange it sits at the front of the queue, which Study 9 found is where the ES residual
went. Pilot = a measurement, like Study 9 Part A: it can only KILL or ADVANCE to a pre-registered strategy test.

Fee arithmetic (IBKR Pro tiered, US options, checked 2026-10-08; 390 one-lot orders x 21 days = 8,190 contracts a
month, so the <= 10,000 tier): commission $0.65 a contract at premium >= $0.10, $0.50 at $0.05-0.10, $0.25 below
$0.05; minimum $1.00 an order; OCC clearing $0.025; plus ORF, CAT ($0.0003) and SEC (sells). Proposed "other" =
$0.05 a contract a side (conservative; ORF to be checked before the run). Gate fee a side = premium-tiered
commission + $0.05 (orders of 2+ contracts); a one-lot order pays the $1.00 minimum + $0.05 = $1.05, reported
beside it. Index options on Cboe (SPXW, XSP) carry customer exchange fees that must be added before the run if
chosen. A penny-wide series offers $0.50 of half-spread a contract against ~$0.70 of fee: dead before adverse
selection. The test is about series quoted $0.05 or wider.

Data (OPRA.PILLAR, schemas exist from 2023-03-28): tcbbo (every trade with the consolidated NBBO at the trade, and
the venue in publisher_id; OPRA never disseminates the aggressor side) and cbbo-1m (consolidated NBBO each
minute) for the chosen parent(s), RTH 09:30-16:00 ET, s10_sessions random in-sample sessions (seeded; no half
days). Step 0 prices this first: python -m src.study10 --price (free). Parents chosen at approval within the
remaining credit ($125 - ~$99.40 = ~$25.60; any pull now needs Matteo's yes).
Fills: trades exactly at the NBBO bid (the passive side bought, s = +1) or ask (passive side sold, s = -1), with
0 < bid < ask. Excluded and counted: trades inside the spread (auctions, price improvement), outside it, locked
or crossed markets, zero bids.
Value per fill, $ a contract: RS_D = s x (mid(t + D) - p) x 100, D = 1, 5, 15 min, mid from the last cbbo-1m
snapshot at or before t + D (and at or after t); dropped when t + D passes 16:00 or the option's expiry.
Net = RS_D - gate fee. Session-bootstrap CI (the session is the cluster).
Buckets (all predeclared): quoted spread $0.01 / 0.02-0.04 / 0.05-0.09 / 0.10-0.24 / 0.25+; days to expiry 0 /
1-7 / 8-30 / 31+; premium < 0.10 / 0.10-0.99 / 1-4.99 / 5+; Study 9's time-of-day buckets; trade size 1 / 2-9 /
10+; clearing proxy (the next trade in the contract within 60 s prints through the fill price: the fill a
back-of-queue order would also have got) vs other. Descriptive: by venue (publisher_id).
ADVANCE if, in some spread bucket of $0.05 or wider with >= 1,000 fills present in every session, the 5-minute
mean net (gate fee) has a 90% session-bootstrap lower bound > 0 and the 15-minute mean net is > 0. Otherwise KILL.
An ADVANCE leads only to a strategy test with a quote-level fill model on fresh sessions (cbbo-1s or cmbp-1,
priced then). With 5 sessions the CI is coarse; it is a filter, not a verdict.
Known optimism (stated in advance): counting every at-NBBO print as a fill assumes front-of-queue on the venue
that printed, which a priority customer has only on its own exchange and behind earlier customers. Known
pessimism: marking at mid ignores a passive exit (earning a second half-spread).
Expected outcome: KILL in penny-wide series by arithmetic; nickel-and-wider series uncertain, with a prior against
(wide quotes are wide because those series are hard to hedge or rarely trade).
Proposed config (needs approval): s10_parents (chosen after pricing), s10_sessions 5, s10_seed 20261008,
s10_horizons_min [1, 5, 15], s10_spread_edges [0.01, 0.02, 0.05, 0.10, 0.25], s10_dte_edges [0, 1, 8, 31],
s10_premium_edges [0.10, 1.0, 5.0], s10_commission_tiers {0.05: 0.25, 0.10: 0.50, else: 0.65}, s10_order_min_usd
1.00, s10_other_fee_usd 0.05, s10_clearing_window_s 60; gates study10_min_fills 1000, study10_min_spread 0.05.
Build so far: src/study10.py --price (quotes only) with tests/test_study10.py (3 tests, fake client).

## 2026-10-08 | Study 10 draft amended: $0-commission fee scenario (Matteo: Wealthsimple)
Matteo: Wealthsimple removed options commissions, so a $0-commission world exists. Checked 2026-10-08: Wealthsimple's
options page states no commission and no per-contract fee on US-listed stock and ETF options (its table also lists
Questrade at $0/$0 and IBKR at $0.15-0.65 with a $1 minimum); regulatory/clearing pass-through is not stated. Options
orders go through two routing brokers (Wealthsimple smart-order-routing note); US-listed options execute only on
exchanges, so a resting customer limit order still rests on an exchange with priority-customer status, but the venue
is not the customer's choice. No official trading API (only unofficial wrappers), so hundreds of orders a day could
not be automated there. USD premium from a CAD account pays 1.5% FX each way (USD account: $10/month on Core).
Amendment (proposed; needs approval): three fee scenarios a contract a side, all reported:
  zero_commission  $0.05 (OCC $0.025 + ORF + CAT, conservative pass-through; the gate scenario if Matteo chooses it)
  ibkr_multi_lot   premium-tiered $0.25/0.50/0.65 + $0.05
  ibkr_one_lot     $1.00 minimum + $0.05
With the zero-commission fee a penny-wide series offers $0.50 of half-spread a contract against $0.05, so the
spread restriction is lifted: the advance rule applies to every spread bucket, $0.01 included (study10_min_spread
dropped). Everything else in the draft is unchanged. FX is assumed zero (USD account) and stated as such.

## 2026-10-08 | Study 10 step 0 price quote: first attempt timed out (nothing priced, nothing spent)
Matteo ran python -m src.study10 --price: get_cost on whole option chains (parent symbology) over a full RTH
session returned 504 gateway timeouts after ~2 minutes each (QQQ.OPT tcbbo, IWM.OPT cbbo-1m), with up to 7 retries.
Fix (commit 2203f6d): each session is priced in 30-minute pieces, in parallel (6 workers), and summed; 2 tries per
piece, then the piece is reported as failed and that row shows no price. Defaults now 1 day (the middle in-sample
weekday, 2024-09-16) and the two pilot schemas. 5 tests pass.

## 2026-10-08 | Transfer tooling (no analysis run)
Matteo is moving to a laptop via a USB drive (E:). Added backup_to_usb.bat + scripts/backup_to_usb.ps1 (robocopy of
the repo minus .venv and caches; asks before copying .env; free-space and FAT32 4 GB checks; file-by-file size check;
TRANSFER_NOTE.txt) and restore_from_usb.bat + scripts/restore_from_usb.ps1 (copy to C:\order-flow, check, build
.venv, quick pytest). Shared check in scripts/transfer_common.ps1. All three parse under PowerShell 7.4; the check was
tested on a synthetic tree (passes a good copy, catches a missing and a resized file, skips .venv, caches, .pyc, .env).
Not runnable here: robocopy and drive checks (Windows only). The data stay where they are; nothing is changed in data/.

## 2026-10-08 | Study 10 step 0 price quote (commit cb6b249; nothing pulled; ledger $99.40)
One RTH session priced (2024-09-16, the middle in-sample weekday), 30-minute pieces; 343 s. USD per session
(tcbbo + cbbo-1m = pilot) and for 5 sessions:
  SPY.OPT  13.23 + 0.77 = 14.00   (5: 70.02)
  QQQ.OPT   7.31 + 0.56 =  7.87   (5: 39.33)
  IWM.OPT   1.65 + 0.38 =  2.02   (5: 10.12)
  XSP.OPT   0.15 + 1.10 =  1.25   (5:  6.26)
  SPXW.OPT  tcbbo unpriced (1 of 13 pieces kept timing out) + cbbo-1m 1.27
Remaining credit ~$25.60. SPY and QQQ whole chains are out of reach; IWM and XSP fit. One priced day only, so
session costs may differ by tens of percent (volume varies by day and grew over 2023-25); the pull prices every
session exactly with get_cost first. Wealthsimple lists options on US stocks and ETFs only, so the index options
(XSP, SPXW) would be IBKR trades with Cboe customer index fees on top; IWM is the zero-commission candidate.

## 2026-10-08 | Study 10 APPROVED (IWM, zero commission) and built tests-first; no data pulled yet
Matteo: "Lets build the IWM test ... test 1 variation with completely $0 commission since this is an execution
heavy strategy". New family "option liquidity provision": 1 gated variant (V1: IWM options, every at-NBBO fill,
gate fee $0.00 a contract a side). Sessions: 5, drawn with seed 20261008 from in-sample equity sessions from
2023-06-01 (no half days); printed by --sessions-list before any data exists.
Rules as in the draft (2026-10-08 entries) with these settled at approval:
- Gate fee $0.00 (Matteo). Reported beside it, not gated: pass-through $0.05, IBKR tiered + $0.05, IBKR one-lot
  $1.05, and whether the verdict would also be ADVANCE at the $0.05 pass-through.
- No spread restriction: the advance rule looks at every quoted-spread bucket ($0.01 / 0.02-0.04 / 0.05-0.09 /
  0.10-0.24 / 0.25+). Five buckets are looked at, so an ADVANCE is a lead for fresh sessions, not a result.
Implementation choices made before any data (flagged for Matteo):
- One fill opportunity per sweep: prints of one contract, side and price, each within 10 ms of the previous
  (s10_sweep_ms), count once, because a single resting order is filled once by a multi-venue sweep. Each
  opportunity has equal weight (a resting one-lot gets one contract), not contract weight.
- Marks: the newest valid quote at or before t + D from cbbo-1m snapshots and the pre-trade quotes of later
  prints in the contract (the draft said "the last cbbo-1m snapshot at or after t"; the newest quote of either
  kind is the better estimate). When no quote newer than the fill is seen, the quote is taken as unchanged; the
  share of such marks is reported (quote_seen_after_fill_D).
- Time-of-day buckets reuse s9_tod_edges. Timestamps: ts_recv for prints and snapshots; the sanity block reports
  the share of snapshots on whole minutes (whether cbbo-1m stamps the interval end).
Pull: tcbbo + cbbo-1m, IWM.OPT parent, 5 sessions x 13 thirty-minute pieces x 2 schemas = 130 requests, each
priced with get_cost (spend.Budget) before it is pulled, written to data/raw/opra/iwm/<schema>/<date>/<HHMM>.parquet,
idempotent. Estimate from the price quote: ~$2.02 a session, ~$10 in all; the exact quote is --pull --price-only.
The ledger is at $99.40, so the pull needs Matteo's yes on the exact figure and --allow-past-total.
Stated in advance: the tape measure may well be positive at $0 (market makers earn the spread on average). What
it cannot show is whether a retail order gets those fills (queue position among customers on one exchange,
cancels when the stock moves); an ADVANCE leads to a quote-level fill model on fresh sessions, not to trading.
config diff (approved): params s10_parent "IWM.OPT", s10_sessions 5, s10_seed 20261008, s10_chunk_min 30,
s10_horizons_min [1, 5, 15], s10_spread_edges [0.01, 0.02, 0.05, 0.10, 0.25], s10_dte_edges [0, 1, 8, 31],
s10_premium_edges [0.10, 1.0, 5.0], s10_size_edges [1, 2, 10], s10_clearing_window_s 60, s10_sweep_ms 10,
s10_gate_fee_usd 0.0, s10_other_fee_usd 0.05, s10_commission_tiers [[0.05, 0.25], [0.10, 0.50], [1e6, 0.65]],
s10_order_min_usd 1.00; gates study10_min_fills 1000, study10_advance_horizon_min 5, study10_confirm_horizon_min 15.
Build: src/study10.py (draw_sessions, pull, classify, dedupe_sweeps, clearing_flags, mark_mids, realized_spread_usd,
bucket, fee_table, session_fills, verdict, report, sanity, run); tests/test_study10.py 15 tests (pricing 5; fills and
exclusions, sweep merge, clearing, marks with staleness and the close, RS by hand, buckets, fees, session draw,
verdict cases, pull quote/write/idempotent with a fake budget, one synthetic session end to end).

## 2026-10-08 | Study 10 pilot: sessions drawn and exact quote (commit b961f55; nothing pulled)
Sessions (seed 20261008): 2023-08-09, 2023-09-27, 2023-10-10, 2023-12-14, 2025-12-02. --pull --price-only: 130
pieces (5 x 13 x tcbbo, cbbo-1m), $8.19 exact (get_cost per piece; three 504s retried). Ledger $99.40 -> $107.59
if pulled. Fix: the pull now reuses one Databento client per worker thread (it opened one per piece).

## 2026-10-08 | Study 10 pilot run (Matteo's laptop, commit 59aa87a; in sample; pull $8.19, ledger $107.58)
Pull: 130 pieces written (0 empty); 504s each succeeded on the first retry.
Data: 413,571 RTH prints on 5 sessions (40.5k-150.2k a session; 1,317-2,053 contracts traded); prices in dollars
(median print $0.47-1.95); cbbo-1m 1.5-2.0 M snapshots a session, 100% on whole minutes (every listed contract,
every minute). Prints at the bid or ask 61-83% by session; excluded: inside 104,805 (auctions, improvement),
outside 5,218, locked/crossed 2,500, no quote 3,160. 215,533 fill opportunities after the 10 ms sweep merge
(43.1k a session). A quote newer than the fill was seen for 98-99% of 5-minute marks.
RS ($ a contract, all fills, gate fee $0), 5-minute mean (90% session CI), 15-minute mean, by quoted spread:
  0.01-0.02  n 138,749  +0.11 (-0.06..+0.21)  15m +0.18   cleared 12%: -2.18; not cleared +0.43
  0.02-0.05  n  62,036  +0.50 (+0.34..+0.76)  15m +0.64   cleared 14%: -3.08; not cleared +1.08   ADVANCE
  0.05-0.10  n   8,520  +0.78 (+0.44..+1.36)  15m +1.23   cleared 15%: -6.60; not cleared +2.06   ADVANCE
  0.10-0.25  n   5,403  -0.16 (-1.07..+2.82)  15m +1.01   cleared 15%: -16.2; not cleared +2.69
  0.25+      n     825  +14.5 (-4.82..+17.2)  15m +18.9
Verdict by the pre-registered rule: ADVANCE (buckets 0.02-0.05 and 0.05-0.10); also ADVANCE at the $0.05
pass-through. At IBKR fees only 0.05-0.10 stays positive (+0.08 tiered); one-lot negative everywhere but 0.25+.
Descriptive: positive at 5 min in every DTE, premium, time-of-day and size bucket; by venue from -0.95 to +1.22.
Rule 6 (result looks strong; assume a bug): checked from the output: units, every session in every bucket, no
holdout date (latest 2025-12-02), marks almost always on a newer quote, snapshots on whole minutes. Not yet
checked: (1) whether one session carries the advance, (2) whether a cbbo-1m snapshot stamped T describes T or
T + 60 s (marks up to a minute late; outcome side only), (3) the share of the half-spread kept. Added --diagnose
(tests first, 17 tests): snapshot_convention (snapshot vs the market state at T - 60 s, T, T + 60 s read from
pre-trade quotes of prints within 1 s) and stability (by session, sessions positive, capture of half-spread,
marks with no newer quote). Gate decision is Matteo's; no next stage started.
What the tape cannot show (stated in the draft): whether a retail order gets these fills. Cleared fills (the
level traded through within 60 s; 12-15% of fills) lose $2-7 a contract; a resting order that does not move with
IWM is hit exactly then, and would also create pick-off fills absent from this tape. Exiting by crossing the
spread costs a half-spread ($1-2.50 in these buckets), more than the measured RS: the edge exists only if the exit
is passive too.

## 2026-10-09 | Study 10 rule-6 diagnostics (Matteo's laptop, commit cc57f91; in sample; $0)
- Snapshot clock: a cbbo-1m snapshot stamped T matches the market state at T for 54-66% of checks (pre-trade
  quotes of prints within 1 s), against 8-17% for T - 60 s and 9-16% for T + 60 s, every session. Snapshots
  describe their stamp time: the marks look no later than t + D. (Matches are below 100% because the state is
  read from the next print's pre-trade quote, which can change within the second.)
- Stability at 5 min (sessions positive / capture of the half-spread / marks without a newer quote):
  0.01-0.02 4 of 5 / 22% / 1.6%; 0.02-0.05 5 of 5 (+0.11 to +1.09 by session) / 42% / 2.1%;
  0.05-0.10 4 of 5 (2023-09-27 -0.32) / 25% / 2.0%; 0.10-0.25 4 of 5 (2023-12-14, n 3,792, -1.33, carries the
  bucket) / -2%; 0.25+ 3 of 5 (2023-12-14 has 653 of 825 fills). At 15 min 0.02-0.05 is positive 4 of 5
  (2025-12-02 -0.24), 0.05-0.10 4 of 5.
- Reading: no single session carries the advancing buckets; no clock lookahead; the marks almost always use a
  newer quote. The rule-6 checks found no bug. The open question remains fill quality for a retail order.

## 2026-10-09 | Study 10 bankroll built (descriptive; Matteo: "run the bankroll simulation over the 3 fill scenarios
as well as 1-5 contract positions with the assumed frequency of these per day")
Resampled from the pilot's 70,556 fills in the advancing buckets (0.02-0.05 and 0.05-0.10), 5-minute marks, $0
commission. Each simulated day draws one pilot session, then N fills from it; a k-lot order gets min(k, print
size) contracts per fill (bigger prints come with bigger orders and kept less). Fill quality: as in the data,
20% or 30% break-through fills (cleared within 60 s), drawn from that session's cleared and other fills (a session
lacking a kind borrows it from all sessions; a test caught that an empty pool had added $0). $30k start, 252
days, 500 paths, seed 20261009; a path at zero stops (ruin). Grid 3 x 5 x 3 = 45 rows: mean and SD of the day,
losing days, median / 5th / 95th percentile final equity, median return, median and 95th percentile max
drawdown, ruin share. Tests: partial fills, extremes and mean of the day simulation, path stats by hand, the
empty-pool borrow, the grid. Assumes what the pilot assumes (front-of-queue fills, a passive exit marked at mid,
no pick-off fills, no capital limit) and adds that days and fills are independent draws from 5 sessions.
config diff (approved by the request): bankroll.s10_buckets [0.02-0.05, 0.05-0.10], s10_fills_per_day
[100, 200, 300], s10_break_through [null, 0.20, 0.30], s10_contracts [1, 2, 3, 4, 5], s10_paths 500, s10_days 252,
s10_seed 20261009. Command: python -m src.study10 --bankroll (saves study10_bankroll).

## 2026-10-09 | Study 10 bankroll run 1 DISCARDED (commit 900df4e): every money column NaN
Fills within 5 minutes of the close have no 5-minute mark (net_5 NaN by design); the pools kept them, so any day
that drew one summed to NaN, and losing-day shares counted NaN days as not losing. Fix: pools drop unmarked fills;
the run now stops if a simulated day is not finite; test added (23 Study 10 tests pass). Numbers from run 1 are void.

## 2026-10-09 | Study 10 bankroll run 2 (Matteo's laptop, commit 764866c; descriptive, in sample; $0)
$30k, 252 days, 500 paths, resampled fills from the advancing buckets, $0 commission, 5-minute marks.
Median final equity / median max drawdown / ruin, 1-lot and 5-lot, 100 and 300 fills a day:
  as in data      1-lot: 100 $44.6k (+49%) DD 1.9%; 300 $74.5k (+148%) DD 2.0%   (mean day $58 / $176, SD $138 / $256)
                  5-lot: 100 $68.9k (+130%) DD 6.4%; 300 $150.4k (+401%) DD 6.1%  (mean day $155 / $474)
  20% break-thru  1-lot: 100 $38.0k (+27%) DD 2.9%;  300 $54.5k (+82%) DD 3.5%
                  5-lot: 100 $51.6k (+72%) DD 10.1%; 300 $95.0k (+217%) DD 11.1%
  30% break-thru  1-lot: 100 $28.4k (-5%) DD 10.4%;  300 $24.8k (-17%) DD 23.9%
                  5-lot: 100 $24.5k (-18%) DD 34.1%; 300 $10.4k (-65%) DD 80.7%, ruin 30.8%
Losing days 23-34% (as in data), 33-41% (20%), ~50% (30%). Size scales P&L less than linearly (1 -> 5 lots:
x2.7 at 100 fills a day, prints are small) and drawdown more than linearly.
Reading: the outcome turns on fill quality alone, as before: as in the data or at 20% break-through every row is
positive; at 30% every row loses and large size with many fills can ruin the account. Implied annual Sharpe at
"as in data" (~6-7) is a market maker's at the tape level; for a retail order it says the assumptions (front of
the queue, passive exit at mid, no pick-off fills) carry the result. Days are independent draws from 5 sessions,
so regime risk (volatility spikes, gaps) is understated and drawdowns are optimistic.

## 2026-10-09 | Study 10b APPROVED and built: quoting conditions (step 1 exploration; no run yet)
Matteo: "lets do that exactly as you layed it out" (trailing volatility of the tested instrument and its
acceleration, a recent-sweep flag, the day's gamma regime), the success rule agreed, and "run it on what we have
so far right now ... and then after test consistency with 5 fresh days".
Plan (fixed now):
1. Explore on the pilot fills ($0, src/study10b.py --explore). Exploration only: it chooses, it does not prove.
2. Freeze at most 3 filters (Study 10 family variants 2-4 of 4), with their numeric cut points from step 1.
3. Confirm on 5 fresh in-sample sessions (new seed, pilot days excluded; ~$8, Matteo's yes on the exact quote),
   which also replicates the unfiltered V1 result.
Success rule per frozen filter (agreed): on the fresh sessions, in the advancing buckets (0.02-0.05, 0.05-0.10),
the filtered fills keep more and are run through less than all fills: 5-minute mean RS kept minus all with a 90%
session-bootstrap lower bound > 0, and break-through share kept < all.
Features, each from data stamped at or before the fill:
- IWM per cbbo-1m minute from put-call parity, nearest expiry after the session date, F = K + C_mid - P_mid,
  median over the 3 strikes with the smallest |C - P| (no IWM equity data needed; Matteo: "accurately calculated on
  whatever instrument we're testing").
- rv: sqrt(sum of the last 30 squared 1-minute log returns) in bp (none before 10:00); accel: rv of the last 15
  minutes / the previous 15 (> 1 = expanding).
- sweep_recent: the same contract swept (prints within 10 ms on >= 2 venues) in the 30 s before the fill.
- gamma: sign of SPX net gamma for the day (gex_daily; point in time: OI before 09:30, quotes of the day before).
  A market-wide proxy (no IWM open interest on disk); with 5 or 10 days it can only be descriptive.
Candidates stated before any number: C1 skip the top volatility tercile, C2 skip the top acceleration tercile, C3
skip after a recent sweep, C4 positive-gamma days only, C5 skip if C1, C2 or C3. Reported at 5 and 15 minutes, with
by-level tables, the penny bucket under C5, and an IWM sanity row per session (minutes, min/max, median 1-minute
move, sweeps, gamma, share of fills with a volatility value).
config diff (approved by the request): params s10b_vol_window_min 30, s10b_accel_half_min 15,
s10b_sweep_lookback_s 30, s10b_sweep_min_venues 2, s10b_parity_strikes 3, s10b_top_quantile 0.6667.
Build: src/study10b.py; tests/test_study10b.py 7 tests (parity forward with a same-day chain ignored, volatility
and acceleration by hand with the full-window rule and 0/0, features from completed snapshots only, sweeps and the
strictly-before rule, gamma labels, the kept-vs-all comparison with its CI, and an end-to-end run on synthetic raw
pieces shaped like Databento's). Three of my expected values were wrong (sweep count, a window boundary) and were
corrected against hand counts; the code was right each time.

## 2026-10-09 | Study 10b amended before any run: graded and live gamma (Matteo)
Matteo: "I don't want to do just positive or negative. There's a difference between a deep positive or a deep
negative gamma number. Some days it's a weak negative and flips positive." The sign-only gamma is replaced by two
features (no study 10b number had been seen):
- gamma_day: z = net_gex / median |net_gex| of up to gex_pct_lookback (252) previous sessions (at least 20);
  deep if |z| >= 1.0, else weak; with the sign: deep/weak positive/negative. Prior sessions only.
- gamma_live, per fill: SPX = close of the last ES bar completed by the fill (front contract) minus the day's
  ES-SPX basis (gex_daily, known before the open); the side of the day's flip (S0's side carries the sign of net
  gamma), near-flip if within 0.25 expected moves of the flip, else deep. A weak negative day that trades through
  its flip reads positive from that minute on. No flip on the grid: the day's sign, deep. flip_dist_em reported.
Candidates (restated before any number): C1 skip top volatility tercile, C2 skip top acceleration tercile, C3 skip
after a recent sweep, C4 skip deep-negative live gamma, C5 skip if C1-C3, C6 skip if C1-C4. The report shows every
fill by day grade and by live label (n, sessions, 5- and 15-minute RS, break-through share). With 5 sessions the
day grade is descriptive only; the live label varies within days and has more information.
config diff (approved by the request): s10b_gamma_deep_ratio 1.0, s10b_gamma_min_days 20, s10b_flip_near_em 0.25.
Tests: day grades from prior days only (by hand), live labels on both sides of the flip and with no flip, SPX from
completed ES bars only; 32 Study 10/10b tests pass.

## 2026-10-09 | Study 10b step 1 exploration run (Matteo's laptop, commit e6cead3; pilot fills, in sample; $0)
IWM from parity: 389 minutes a session; ranges 190.96-192.90 (2023-08-09), 174.61-176.97 (09-27), 174.49-177.03
(10-10), 196.58-200.05 (12-14), 245.27-247.26 (2025-12-02), in line with IWM's prices on those days; median
1-minute move 2.3-4.0 bp. Sweeps 2,706-12,972 a session. Gamma: day grades deep negative (08-09, 09-27), weak
negative (10-10, the only day that crossed its flip: live mix 10.4k near-flip positive, 9.0k deep positive, 6.2k deep
negative, 4.5k near-flip negative), deep positive (12-14), weak positive (2025-12-02).
Advancing buckets, 70,556 fills; all: 5-min RS +0.534, 15-min +0.713, break-through 14.0%. Cut points: rv 32.2 bp
(30 min), accel 1.070.
Candidates, kept minus all (90% session CI), break-through kept vs all, share kept:
  C1 skip top vol      5m -0.058 (-0.181..+0.010)  15m -0.143 (-0.222..+0.017)  13.2% vs 14.0%  74%
  C2 skip top accel    5m -0.037 (-0.086..+0.076)  15m +0.102 (+0.047..+0.212)  14.7% vs 14.0%  75%
  C3 skip after sweep  5m -0.012 (-0.114..+0.051)  15m -0.023                   13.6% vs 14.0%  75%
  C4 skip deep-neg live 5m +0.020 (-0.069..+0.300) 15m +0.041                   14.7% vs 14.0%  72%
  C5 C1|C2|C3          5m +0.032 (-0.106..+0.093)  15m +0.163 (-0.135..+0.352)  13.7% vs 14.0%  42%
  C6 C1..C4            5m +0.023 (-0.113..+0.209)  15m +0.327 (+0.051..+0.613)  14.7% vs 14.0%  27%
By level: top volatility tercile earns more (5m +0.70, 15m +1.11) with more break-throughs (16.2% vs 10.4%);
first half hour (no volatility value yet) is the weakest at 5 min (+0.33) with the most break-throughs (18.8%) but
+0.93 at 15 min; recent-sweep fills are no worse (+0.57 vs +0.52). Gamma: weak days (weak negative +1.07, weak
positive +0.81) beat deep days (+0.33, +0.39), and near-flip live fills beat deep ones (near-flip negative +2.13,
near-flip positive +1.00, break-through 7-9%), but each grade is one or two sessions and near-flip is 6% of fills,
mostly from 2023-10-10: indistinguishable from session identity. Penny bucket under C5: +0.023 (-0.003..+0.052).
Reading against the agreed rule (kept RS up with 90% lb > 0 AND fewer break-throughs): no candidate meets it at
5 minutes; C2 and C6 meet the RS half at 15 minutes but have more break-throughs. Break-through share stays at
13-15% under every filter: these minute-scale conditions do not separate the fills that get run over (a
seconds-scale effect). Skipping volatility removes the best fills. Many comparisons were looked at (6 candidates,
2 horizons, 5 groupings), so the 15-minute "wins" are what chance alone would produce. Nothing frozen yet:
Matteo's call.

## 2026-10-09 | Study 10 confirmation PRE-REGISTERED and built (Matteo: "Okay lets do that"); no fresh data yet
Family "option liquidity provision": V1 (unfiltered, $0) replication + H1 + H2 = 3 of 4 gated variants.
Sessions: 5 fresh in-sample sessions, seed 20261010, the 5 pilot sessions excluded, no half days
(python -m src.study10 --confirm --sessions-list prints them before any data exists). Pull ~$8 (5 x 26 pieces);
exact quote with --confirm --pull --price-only; ledger $107.58 -> ~$116, needs Matteo's yes and --allow-past-total.
V1 replication rule: the pilot's advance rule unchanged (some spread bucket with >= 1,000 fills present in every
session, 5-minute net mean 90% session-bootstrap lower bound > 0, 15-minute net mean > 0, fee $0), and it must hold
in at least one of the pilot's advancing buckets (0.02-0.05 or 0.05-0.10) to count as replicated.
Frozen hypotheses (each judged in those two buckets with the agreed rule: kept minus all 5-minute RS with a 90%
session-bootstrap lower bound > 0 AND a lower break-through share; NOT_TESTABLE if fewer than 500 kept fills or
kept fills in fewer than 2 sessions):
  H1 skip the first 30 minutes after the open (no fills before 10:00 ET).
  H2 quote only while live gamma is near the flip (within 0.25 expected moves, either side).
Not carried forward (pilot evidence against or none): skip high volatility (removes the best fills), skip after a
sweep, skip deep-negative live gamma, the combinations.
config diff (approved): params s10_confirm_seed 20261010, s10_confirm_sessions 5, s10b_skip_open_min 30,
s10b_min_kept_fills 500, s10b_min_kept_sessions 2.
Build: study10.draw_sessions gains seed/n/exclude, confirm_sessions, run(confirm=True) saves study10_fills_confirm,
CLI --confirm; study10b.features_table, h1_keep, h2_keep, hypothesis_verdict, confirm (--confirm). Tests: fresh draw
excludes the pilot, H1 and H2 masks, PASS/FAIL/NOT_TESTABLE; 36 Study 10/10b tests pass.
