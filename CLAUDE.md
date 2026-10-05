# Gamma + Order Flow Edge Test

Batch research code that tests whether SPX gamma levels plus ES order flow confirmation produce positive expectancy after costs. Nothing here trades or runs live. The full design is in `SPEC.md`; read the relevant section before working on any module.

## Current status

Update this block at the end of every session.

- Stage: Week 1. Full pipeline code exists (ingestion, GEX, levels, touches, flow, sim, gate reports,
  robustness, holdout) and passes 41 tests, including a synthetic end-to-end run. No real data has been pulled yet.
- Pilot: `config.pilot.yaml` (Mar-May 2025, gex_pct_min_periods 20) approved; activate via .env. Pilot output is not a gate decision.
- Gates passed: none
- Open issues: sign off the decisions listed in README.md ("Decisions that need your sign-off"),
  especially holdout_start, cost_rt_usd, gex_pct_min_periods; then set status.frozen in config.yaml.
  Run `python -m src.ingest_options --probe 2023-06-01` and confirm the ThetaData v3 EOD columns map correctly.

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
- Gate reports: `python -m src.analysis stage1|stage2|stage3 [--carry ...]`
- Robustness: `python -m src.robustness nudges|splits`
- Holdout (only when told "run the holdout"): `GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.robustness holdout-prep|holdout`
