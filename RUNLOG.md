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

## Pre-registered Stage 3 variants (proposed 2026-10-06, before any full-run trade data; awaiting approval)
Each counts toward the 20-variant limit. All are reversal trades under the frozen rules; only the
eligibility filter changes. Run once, after the main Stage 3, on the same sampled days.
- V1 regime filter: confirmed trades only on days with gex_pct >= 0.5.
- V2: V1 restricted to gamma-tagged levels (is_gamma).
- V3 side-of-flip alignment: support trades (d=+1) only when the 09:30 price is above the flip,
  resistance trades (d=-1) only when below; days with no flip excluded.
