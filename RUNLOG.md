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
