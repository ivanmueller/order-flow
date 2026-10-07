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
