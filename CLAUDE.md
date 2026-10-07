# Gamma + Order Flow Edge Test

Batch research code that tests whether SPX gamma levels plus ES order flow confirmation produce positive expectancy after costs. Nothing here trades or runs live. The full design is in `SPEC.md`; read the relevant section before working on any module.

## Current status

Update this block at the end of every session.

- Stage: study 1 complete on the main config (RUNLOG.md 2026-10-06/07). Gate 0 passes; Stage 1 KILL
  (regime sign right, p=0.029, quintile gap 7.5% < 15%); Stage 2 after the labeler fix: no level group
  beats placebo, gamma x regime interaction p=0.044; Stage 3 KILL, robust to all nudges and splits
  (confirmed -0.33R; pre-cost naive-fade edge ~+0.10R = 0.6 ticks vs ~2.3 ticks of friction). No
  holdout run. Databento spend ~$81 of $125.
- Study 2 run 2026-10-07: KILL on every variant, accepted by Matteo; intraday level thesis closed
  (WRITEUP.md, RESULTS.md). 9 of 20 variants used.
- Study 3 run twice 2026-10-07 (first run superseded by a fill-realism fix): R1 -0.24R, R2 -0.06R,
  R3 -0.22R, regime contrasts negative, permutation p > 0.9. KILL on all; 12 of 20 variants used.
  Awaiting Matteo's call on study 3 and on the project. WRITEUP.md and RESULTS.md are current.
- Review 2026-10-07 (REVIEW.md, RUNLOG same date): studies 1-3 audited in code and logs, every kill
  stands; intraday level/flow thesis closed. Three report-only re-checks of the regime variable are
  recommended (sign convention, business-time clock, S0^2-normalised percentile), not yet run.
- Study 4 approved 2026-10-07 (regime-conditioned 0DTE straddle at the D-1 close: S4a short / S4b
  long / S4c iron fly; params opt_cost_per_leg_usd 1.50, s4_wing_em 1.0, s4_regime_lag 1, gate
  study4_min_expectancy_em 0.03 added to config.yaml). Built tests-first in `src/study4.py` (90 tests pass); run by
  Matteo 2026-10-07 (RUNLOG): S4a +0.059 EM (CI -0.02..+0.14, contrast CI > 0, perm p 0.035), S4b +0.033
  (CI fails), S4c iron fly -0.010. KILL on all three by the rules; 15 of 20 variants. Regime effect real
  but ~1.2 SE at 266 sessions; defined-risk version has no edge. Holdout still sealed.
- Next-study selection 2026-10-07 per NEXT.md (CANDIDATES.md; RUNLOG same date): data inventory, 15
  candidates scored; no candidate on disk can resolve its published effect after costs. Study 5
  pre-registered as a DRAFT awaiting Matteo's approval: last-30-minute ES momentum into the close,
  unconditional (S5a no stop, S5b 0.5 EM stop; would bring the count to 17 of 20), unit = VIX-implied EM,
  gamma only as a descriptive slope, verdict reported as existence (timing) and economics (after costs).
  Sample chosen at approval: A (2023-06..2025-12, on disk, default) or B (2019-01..2025-12, needs ES bars
  2019-01..2023-03, est $5.4, and first a code change freezing the stage-3 day set, which a calendar
  rebuild would otherwise redraw). Power at the published size in A: existence ~0.52, after costs ~0.18.
  Expected outcome stated in advance: KILL. Alternatives: the one-week premium (body and wings) with a $40
  ThetaData Value month (2020+), or write up. New params proposed, not yet in config.yaml: s5_sample,
  s5_decision_time, s5_exit_time, s5_stop_em, s5_em_vix_factor, s5_perm_block, s5_outlier_n,
  s5_sanity_em; gates study5_min_sessions, study5_min_expectancy_em, study5_perm_p, study5_tail_drop.
  Two independent reviews corrected the draft before freezing (RUNLOG). No code written for study 5.
- Known data facts: ThetaData free tier serves EOD from 2023-06-01; half-day EOD reports are all zero
  bids (six sessions have no GEX row); 2024-12-02 dailies zero-bid (levels skip 2024-12-03); EOD quotes
  are the 17:00 ET curb close. Approved: quote_time 17:00, cost_rt_usd 3.98, gex_pct_min_periods 126.
- Pilot: `config.pilot.yaml`; activate via .env. Pilot output is not a gate decision.
- Open issues: README decisions sign-off, then status.frozen in config.yaml.

## Non-negotiable rules

1. **Point-in-time only.** Levels for session D may use only: option quotes from the D-1 end-of-day report, open interest published the morning of D (before 09:30 ET), and SPX and ES closes from D-1. Nothing timestamped after the decision time may enter a feature, level, or entry. If you are unsure whether a value was knowable at that moment, stop and ask.
2. **The holdout is sealed.** Every data load goes through `in_sample(date)` in `src/calendar.py`, which excludes dates on or after `holdout_start` in `config.yaml`. Never read, plot, summarize, or test on holdout dates unless I say "run the holdout".
3. **Parameters live in `config.yaml`.** Never hardcode a tunable number. Never change a value in `config.yaml` without my approval; propose the change and explain why.
4. **Log every run.** Append an entry to `RUNLOG.md` for every analysis run, including failed and discarded ones: date, git commit, what changed, config diff, and the headline result.
5. **Fills stay conservative.** Never make fill or cost assumptions more favorable than `SPEC.md`. Same-bar ambiguity counts as a failure, targets fill only on a print one tick beyond, stops exit one tick beyond, and costs are always subtracted.
6. **Suspect good results.** If a result looks strong, assume a bug before celebrating: check for lookahead, roll contamination, duplicate events, and holdout leakage, and tell me what you checked.
7. **No spending without asking.** Call `get_cost` before every Databento pull and report the cost. Ask before any pull over $5, or any pull that would take total spend past $100 of the $125 credit.
8. **Gate decisions are mine.** Report the numbers against the pass and kill rules in `SPEC.md`, then stop. Do not move to the next stage on your own.

## Data conventions

- Store timestamps in UTC; convert to America/New_York only for session logic. Build the trading calendar from ES bars.
- ES uses Databento continuous symbol `ES.v.0`, which is not back-adjusted. No window, basis, or bar series may span a roll date (watch `instrument_id`).
- Databento trades `side`: `B` is a buy aggressor (+1), `A` is a sell aggressor (-1), `N` is dropped.
- Open interest comes from Databento `OPRA.PILLAR`, schema `statistics`, parents `SPX.OPT` and `SPXW.OPT`, requested 00:00 to 09:30 ET, filtered to `StatType.OPEN_INTEREST`, keeping the last record per symbol. Parse root, expiration, call or put, and strike from the OSI symbol; skip the definition schema.
- Option quotes come from the ThetaData Terminal at `http://127.0.0.1:25503/v3`, free tier, end-of-day endpoint only.
- Drop SPX (AM-settled) contracts on their expiration day.
- ES prices are in points; one tick is 0.25.

## Code conventions

- Python 3.11+, with pandas, pyarrow, numpy, scipy, and statsmodels. Raw data in `data/raw/`, derived tables in `data/derived/`, all Parquet.
- Ingestion jobs are idempotent: skip files that already exist.
- Notebooks only call functions from `src/`; no logic lives in notebooks.
- Read `DATABENTO_API_KEY` from the environment. Never write keys into code, config, or logs. `data/` and `.env` stay in `.gitignore`.
- Write the test first, using synthetic data with hand-verified answers, for: Black-76 price, implied vol, and gamma; the forward fit; the flip finder; the touch detector; the outcome labeler; every feature in `src/flow.py`; and the trade simulator.

## How to work

1. Before writing a module, read its section in `SPEC.md` and restate the formulas you are about to implement.
2. After writing it, run the tests, then run it on 5 real sessions and show the sanity checks from `SPEC.md` (for the GEX engine, the four Gate 0 checks).
3. Stop and ask when `SPEC.md` is ambiguous, when a data source behaves differently from `SPEC.md`, or when a gate decision is due.
4. At the end of a session, update the status block above and commit.

## Commands

See README.md for the full runbook in order.

- Tests: `pytest -q`
- Day-one probe: `python -m src.ingest_options --probe 2023-06-01`
- Daily closes: `python -m src.ingest_daily`
- Ingest options: `python -m src.ingest_options --start YYYY-MM-DD --end YYYY-MM-DD --what eod|oi [--price-only | --approve-usd X]`
- Ingest ES bars: `python -m src.ingest_futures bars --start YYYY-MM-DD --end YYYY-MM-DD [--price-only | --approve-usd X]`
- Roll-day basis: `python -m src.ingest_futures roll-basis [--price-only | --approve-usd X]`
- Build GEX table: `python -m src.gex --start YYYY-MM-DD --end YYYY-MM-DD`
- Levels and touches: `python -m src.levels`, `python -m src.touches`
- Stage 3 trades: `python -m src.ingest_futures trades [--price-only | --approve-usd X]`, then `python -m src.stage3`
- Gate reports: `python -m src.analysis stage1|stage2|stage3|variants|study2 [--carry ...]`
- Study 3 band trades: `python -m src.study3 [--report-only]`
- Study 4 straddles: `python -m src.study4 [--report-only]`
- Robustness: `python -m src.robustness nudges|splits`
- Holdout (only when told "run the holdout"): `GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.robustness holdout-prep|holdout`
