# Gamma + Order Flow Edge Test (MVP)

Batch research code that answers one question for about $0 in data: do SPX gamma regimes, gamma
levels, or order flow confirmation at a level give positive expectancy on ES after costs?
Nothing here trades or runs live. The design is in [`SPEC.md`](SPEC.md), the working rules in
[`CLAUDE.md`](CLAUDE.md), and every tunable number in [`config.yaml`](config.yaml).

| Gate | Module(s) | Report |
| --- | --- | --- |
| 0. GEX engine valid | `src/gex.py` | `python -m src.gex`, `notebooks/00_gex_validation.ipynb` |
| 1. Regime | `src/regime.py`, `src/analysis.py` | `python -m src.analysis stage1`, `01_regime.ipynb` |
| 2. Levels | `src/levels.py`, `src/touches.py` | `python -m src.analysis stage2`, `02_levels.ipynb` |
| 3. Flow | `src/flow.py`, `src/sim.py`, `src/stage3.py` | `python -m src.analysis stage3`, `03_flow.ipynb` |
| Holdout + robustness | `src/robustness.py` | `04_holdout.ipynb` |

Each report prints the numbers next to the frozen pass/kill rules (`verdict_vs_rules`). The gate call is yours.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env                   # then put your Databento key in .env (git-ignored, never committed)
# Install and start the Theta Terminal (free account); it serves http://127.0.0.1:25503/v3
pytest -q                              # 41 tests: hand-verified formulas + an end-to-end synthetic run
```

Windows shortcuts (repo root): `open_venv.bat` opens PowerShell as administrator in the repo with `.venv`
active; `open_venv_api.bat` does the same, then checks the Databento key in `.env` (never printed), makes one
free Databento call, and checks or starts the ThetaData Terminal (set `THETA_JAR` to its jar to auto-start).

## Pilot first (3 months, Mar-May 2025)

`config.pilot.yaml` overlays `config.yaml` for a cheap first pass on real data. Turn it on by adding
`GAMMA_EDGE_CONFIG=config.pilot.yaml` to `.env`; remove the line to switch back to the main config.
Raw downloads are shared (the main run reuses them, nothing is bought twice); pilot results go to
`data/derived_pilot/`. Every command below then defaults to the pilot dates.

## Runbook (Week 1 first: price everything before pulling anything at scale)

```bash
# 0. Day-one source check: SPX and SPXW both come back, and the EOD column names map correctly
python -m src.ingest_options --probe 2023-06-01

# 1. Free daily closes (SPX, VIX)
python -m src.ingest_daily

# 2. Price, then pull ES 1-minute bars (also builds the trading calendar from the bars)
python -m src.ingest_futures bars --price-only
python -m src.ingest_futures bars --approve-usd <amount you approve>
python -m src.ingest_futures roll-basis --price-only      # tiny: D's contract at D-1 close on roll days
python -m src.ingest_futures roll-basis --approve-usd 1

# 3. Option quotes (ThetaData, free) and open interest (Databento OPRA, priced first)
python -m src.ingest_options --what eod                    # overnight; pacing is in config.yaml
python -m src.ingest_options --what oi --price-only --sample 20
python -m src.ingest_options --what oi --approve-usd <amount>
#    If OI + bars + the Stage 3 reserve exceed the $125 credit: switch OI/quotes to ThetaData Value.

# 4. Gate 0
python -m src.gex                                          # builds gex_daily, prints the checks

# 5. Gate 1
python -m src.analysis stage1

# 6. Gate 2
python -m src.levels && python -m src.touches
python -m src.analysis stage2

# 7. Gate 3 (trades only around Stage 2 touches; overlapping windows are bought once)
python -m src.ingest_futures trades --price-only
python -m src.ingest_futures trades --approve-usd <amount>
python -m src.stage3
python -m src.analysis stage3 --carry gamma_only,both,structural_only

# 7b. Study 4 (RUNLOG 2026-10-07): regime-conditioned 0DTE straddle at the D-1 close; needs only
#     gex_daily, the EOD quote files and the FRED closes already on disk (no spend)
python -m src.study4

# 7c. Study 5 close momentum, then the 5f fade (holdout steps only when you say "run the holdout")
python -m src.study5
python -m src.study5 --fade-reference
python -m src.study5 --holdout-check
GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.study5 --fade-holdout <step-1 mean_em>
python -m src.study5 --fade-robustness
#     NQ replication: GAMMA_EDGE_CONFIG=config.nq.yaml, ingest_daily, bars (price, approve), then
python -m src.study5 --fade-replication

# 7d. Price menu (quotes only, nothing pulled)
python -m src.price_menu --start 2023-06-01 --end 2025-12-31

# 7e. Study 6 pure order flow pilot on the on-disk ticks (no spend)
python -m src.study6 --count
python -m src.study6

# 7f. Study 7 cross-market momentum: per market with GAMMA_EDGE_CONFIG=config.<cl|gc|zn|6e>.yaml,
#     ingest_daily, bars 2023-04-01..2025-12-31 (price, approve), then
python -m src.study7 --market
#     then with no overlay set:
python -m src.study7 --cross config.cl.yaml config.gc.yaml config.zn.yaml config.6e.yaml
python -m src.study7 --bridge

# 7g. Hypothetical $30,000 bankroll of the 5f fade (descriptive; config.yaml bankroll section)
#     NQ: GAMMA_EDGE_CONFIG=config.nq.yaml; --holdout needs GAMMA_EDGE_RUN_HOLDOUT=1 after study5 --fade-holdout
python -m src.bankroll
python -m src.bankroll --holdout

# 7h. Study 9 passive-fill measurements on the on-disk ticks and touches (no spend)
python -m src.study9 --realized-spread
python -m src.study9 --level-reversion

# 7i. Study 8 month-end compelled flow (no overlay set; loads config.yaml and config.zn.yaml itself; no spend)
python -m src.study8 --count        # tradable month-ends per variant, no P&L: run first
python -m src.study8 --e0           # FRED existence check (downloads DGS10 and SP500 once), in sample only
python -m src.study8                # M1 (ZN) and M2 (ES); --report-only re-reads the saved tables

# 7j. Study 10 step 0: price OPRA tcbbo + cbbo-1m for the option-liquidity pilot (quotes only; needs the API key)
python -m src.study10 --price
#     the pilot (IWM, zero commission): sessions, exact quote, pull (after the yes), run
python -m src.study10 --sessions-list
python -m src.study10 --pull --price-only
python -m src.study10 --pull --approve-usd <quote + margin> --allow-past-total
python -m src.study10 --run
python -m src.study10 --diagnose
python -m src.study10 --bankroll
#     Study 10b: quoting conditions, step 1 exploration on the pilot fills (free)
python -m src.study10b --explore
#     Study 10 confirmation on 5 fresh sessions (V1 replication + frozen H1, H2)
python -m src.study10 --confirm --sessions-list
python -m src.study10 --confirm --pull --price-only
python -m src.study10 --confirm --pull --approve-usd <quote + margin> --allow-past-total
python -m src.study10 --confirm --run
python -m src.study10b --confirm

#     Study 11: execution design, exploration on the 10 seen sessions (round trip with a passive exit, stale
#     quotes, contract selection, inventory and hedging). Free; the IWM 1-second pull is optional and priced first.
python -m src.study11 --iwm-pull --price-only
python -m src.study11 --iwm-pull --approve-usd X --allow-past-total     # only after Matteo approves the quote
python -m src.study11 --explore
python -m src.study11 --report-only
#     Study 11b: hedged market making (needs the 1-second IWM pull above and study11_entries)
python -m src.study11b --explore
#     Study 12: the Study 1 level fade in SPY shares ($0 commission); price first, pull only with approval
python -m src.study12 --count
python -m src.study12 --pull --price-only
python -m src.study12 --pull --approve-usd X --allow-past-total
python -m src.study12 --run
#     Study 13: order-flow pattern discovery on the on-disk ES ticks (free); --test runs once, after review
python -m src.study13 --build
python -m src.study13 --discover
python -m src.study13 --test
#     Study 14: crypto pump-fade short search (Binance public archive, free; research only). In order:
python -m src.study14 --all                   # one command for the next four steps (resumes; parallel)
python -m src.crypto_inventory                 # step 0 (done): what the archive holds; writes the resume cache
python -m src.crypto_data --hourly             # 1-hour klines + funding, all USDT coins (~0.7 GB; resumable)
python -m src.study14 --events                 # pump events per (W, P, V); lists the 5-min / metrics files needed
python -m src.crypto_data --event-data         # 5-min klines + daily metrics for event months only
python -m src.study14 --discover               # 38,880 combinations + 100 placebo searches, validation, freeze
python -m src.crypto_data --minute             # 1-minute klines for the frozen combinations' trades
python -m src.study14 --diagnose               # placebo, tails, worst periods, 1-minute re-walk
python -m src.study14 --risk                   # descriptive bankroll of the frozen combinations
#     holdout, once, only on "run the holdout": GAMMA_EDGE_RUN_HOLDOUT=1 with crypto_data --hourly --holdout,
#     study14 --events --holdout, crypto_data --event-data --holdout, then study14 --holdout
#     Study 15: long early in crypto surges (hourly data already on disk; STUDY15.md). In order:
python -m src.study15 --all                    # BTC hourly (tiny) -> events -> discover (373,248 combos + 100 placebo)
python -m src.crypto_data --event-data --study 15   # 5-minute bars for the frozen combinations' trades
python -m src.study15 --diagnose               # placebo, tails, cost variants, 2x slippage, 5-minute re-walk
python -m src.study15 --risk
#     holdout, once, only on "run the holdout": GAMMA_EDGE_RUN_HOLDOUT=1 with crypto_data --hourly --holdout,
#     crypto_data --btc --holdout, study15 --events --holdout, crypto_data --event-data --study 15 --holdout,
#     then study15 --holdout

# 8. Robustness (in-sample) and the one-shot holdout -- only when you say "run the holdout"
python -m src.robustness nudges
python -m src.robustness splits
GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.robustness holdout-prep
GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.ingest_futures trades --holdout --price-only
GAMMA_EDGE_RUN_HOLDOUT=1 python -m src.robustness holdout --in-sample-expectancy <from step 7>
```

Log every run in `RUNLOG.md` (`from src.runlog import log_run`).

## Guard rails built in

- **Holdout seal.** Every research load goes through `calendar.seal()` / `in_sample()`. Asking for
  holdout rows raises `HoldoutSealed` unless `GAMMA_EDGE_RUN_HOLDOUT=1` is set. Holdout outputs
  are written to separate `*_holdout` tables, so they never overwrite in-sample results.
- **Spending.** Every Databento pull is priced with `get_cost` first and written to
  `data/spend_ledger.csv`. A run may spend at most `--approve-usd` (default $5, the "ask first"
  line), and nothing goes past the $100 ledger line without `--allow-past-total`.
- **Rolls.** Every session uses only bars from that session's front contract (`instrument_id`).
  Prior-day levels are dropped on roll days; the basis on a roll day comes from the `roll-basis` pull.
- **Point-in-time.** Session D uses D-1 EOD quotes, OI published before 09:30 on D, and D-1
  closes. GEX percentile, VIX, and the flow baseline use only prior sessions.
- **Idempotent ingestion.** Existing files are skipped; writes are atomic.

## Decisions that need your sign-off before freezing `config.yaml`

These are places where `SPEC.md` was silent, ambiguous, or would leak future data. Each is a
config value or a documented rule you can change now; after Stage 1 starts, changes go in RUNLOG.md.

1. **Holdout start = 2026-01-01** (≈ the last 9 months, Jan to Sep 2026). In-sample is 2023-06-01 to 2025-12-31.
2. **`cost_rt_usd` = $3.98**, the user's broker all-in ES round trip (set 2026-10-05).
3. **`gex_pct_min_periods` = 126** (decided 2026-10-05; spec-literal was 252). The GEX percentile needs
   six months of history, so Stage 1 starts around December 2023.
4. **Stage 3 trade window is t0-10m to t0+45m, not +30m.** A reclaim can arrive at t0+10m and the
   time exit is 30 minutes later, so a +30m window would cut trades off early.
5. **Lookahead fix in the confirmation rule.** AbsRatio needs trades through t0+3m, but a reclaim
   can finish sooner. Entry waits for `t_dec = max(t_r, t0 + abs_window)`, and the stop extreme and
   the time exit are measured from `t_dec`. Without this, fast reclaims would use future volume.
6. **Naive baseline limit** stays live for `reclaim_window` (10 min) after t0. The spec doesn't say how long.
7. **Stop fills** at one tick beyond S, or at the print itself if price gapped further (never better).
8. **Stage 2 labels stop at the 16:00 RTH close**, so late touches have shorter horizons and count as timeouts.
9. **Touch rules:** the debounce skips a touch if any of the previous 10 bars came within b of L;
   d comes from which side of L the previous close sits on.
10. **Stage 1 excludes half days** (no full 09:30 to 16:00 session).
11. **`quote_time` = 17:00 ET** (decided 2026-10-05; spec said 16:15). The pilot showed the ThetaData EOD
    NBBO tracks ES at 17:00, when Cboe's SPX curb session ends. It sets the implied-vol clock and the
    reference for Gate 0 check 1. Still before the open of D, so point-in-time holds.
12. **`static/events.csv` has FOMC days for 2023 to 2025 only.** Add CPI and NFP dates from the BLS
    calendar before running the "drop event days" robustness check.

## Layout

```
config.yaml   SPEC.md   CLAUDE.md   RUNLOG.md   static/events.csv
src/  config, calendar (sessions + holdout seal), store (parquet I/O), spend (Databento guard)
      ingest_daily, ingest_options, ingest_futures
      gex (Module 2), levels (Module 3), regime (Stage 1 measures), touches (Stage 2)
      flow, sim, stage3 (Stage 3), study3, study4, stats, analysis (gate reports), robustness, runlog, plots
notebooks/ 00_gex_validation  01_regime  02_levels  03_flow  04_holdout
tests/      formula tests with hand-checked answers + synthetic end-to-end pipeline
data/       raw/ and derived/ Parquet (git-ignored)
```

## Moving to another computer (USB)

On the current PC (after `git pull`), plug in the drive and double-click `backup_to_usb.bat` (default E:; for
another letter run `backup_to_usb.bat F:`). It copies the whole project to `E:\order-flow`: code, docs, configs,
git history, every data folder (data, data_nq, data_cl, data_gc, data_zn, data_6e) and the spend ledger
(`data\spend_ledger.csv`). It skips `.venv` and caches, asks before copying `.env` (your Databento key), checks
every file by size, and writes `TRANSFER_NOTE.txt`. Running it again copies only what changed.

On the laptop: install Python 3.11+ (tick "Add python.exe to PATH") and Git, then double-click
`E:\order-flow\restore_from_usb.bat`. It copies the project to `C:\order-flow` (or the folder you name), checks
every file, builds `.venv` from requirements.txt and runs a quick test. Then use `open_venv.bat` /
`open_venv_api.bat` as before. Without a copied `.env`, create one from `.env.example` with your key. The
ThetaData Terminal is needed only for new end-of-day option quotes, not for anything on disk.
