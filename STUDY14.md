# Study 14: crypto pump-fade shorts, searched over every key setting, with a squeeze-risk filter

Status: APPROVED 2026-10-09 as a search (Matteo: "leave all of the important aspects fluid ... find the optimal
setups"). Data fixed by the step-0 inventory.

## Idea

Low-cap coins with perpetual futures are pumped (coordinated buying, listings, unlock games) and usually give the
move back. A short in a perpetual needs no borrow; funding is often paid *to* shorts after a pump. The risk is the
squeeze: thin books and liquidation cascades. So the study measures two things: the fade itself, and whether
squeeze warning signs, known at entry, separate the shorts that get run over from the ones that work.

## Data (free; inventory run 2026-10-09, RUNLOG)

| Archive type | Coins | Delisted (not trading now) | From | To | Size | Use |
|---|---|---|---|---|---|---|
| 1-hour klines | 1,056 | 272 | 2020-01 | 2026-09 | 0.7 GB | events, all coins, all months |
| 1-minute klines | 1,056 | 272 | 2020-01 | 2026-09 | 32 GB | the trade walk: only months that hold an event |
| funding rate | 988 | 208 | 2020-01 | 2026-09 | 23 MB | actual funding, all coins |
| metrics (open interest, long/short ratios) | 1,034 | 245 | 2020-09 | 2026-10 | 6.7 GB | squeeze score: only the days around events |
| book depth | 1,004 | 215 | 2023-01 | 2026-10 | 237 GB | descriptive only (from 2023), days around events |
| liquidation snapshots | 0 | | | | | not available: dropped from the score |
| aggregate trades | 1,044 | 260 | 2020-01 | 2026-09 | 1.1 TB | not used |

Today 789 coins trade, so about a quarter of the archive is delisted coins (kept: no survivorship bias). Expected
download: ~0.7 GB hourly + funding, then a few GB of 1-minute and metrics files for event months only.
Research only. Trading needs a venue legally open to a BC resident; Binance is not one.

## Search design (Matteo 2026-10-09: every key setting fluid; find the combination that keeps trades high and risk low)

Every setting below is a grid; the search scores every combination on the discovery years, and only what survives
the noise test, validation and the stability check reaches the sealed holdout.

| Setting | Grid |
|---|---|
| pump window W | 6, 24, 72 hours |
| pump size P (return over W) | +30%, +50%, +100% |
| volume V (quote volume over W vs its median over the prior 30 days) | 3x, 5x, 10x |
| entry E | next hour; first red hour (<= 72 h); first red UTC day (<= 7 days); after a 10% / 20% drop from the post-pump high (<= 72 h) |
| hold H | 1, 3, 7, 14 days |
| stop S (above entry) | +15%, +30%, +50%, +100% |
| target T (below entry) | none, -20%, -40% |
| squeeze filter Q | none; skip the top half; skip the top third of the squeeze score |
| funding filter F | any; only when funding at entry >= 0 (shorts are paid) |

3 x 3 x 3 x 5 x 4 x 4 x 3 x 3 x 2 = 38,880 combinations. One event per coin per 7 days within each (W, P, V);
BTC and ETH excluded; 30 days of history needed.

Trade walk (conservative): entries at the open of the hour (or day) after the signal, sold 0.10% worse; stops walked
on 5-minute bars (stop before target in the same bar; a bar that opens above the stop exits at its open; stop fills
0.10% worse); targets fill only if the low trades below them; time exits at the bar open 0.10% worse; 0.05% fee a
side; actual funding received or paid while short; a coin delisted mid-trade exits at its last close. 1x notional.
Final candidates are re-walked on 1-minute bars before the holdout.

Squeeze score: a logistic model, fitted on training events only, of "price reaches +30% above entry within 7 days",
from features known at entry: open-interest change over 24 h, price change over 24 h, open interest over 24-h quote
volume, top-trader long/short ratio, taker buy/sell ratio, funding, days since listing. Events without metrics are
never skipped by Q (reported). Liquidations: not in the archive.

## Protections against fitting noise (as Study 13, plus a stability check)

1. Objectives, each with its own noise test: net return a trade; net return a month (all trades); consistency
   (mean / sd x sqrt(n)). Ranked combinations need >= 100 trades on >= 20 distinct weeks.
2. Noise test: the whole 38,880-combination search rerun on PLACEBO events (the same coins and months, random hours
   at least 72 h away from any real event), 100 times. This is also the key control: if shorting any altcoin hour is
   as good as shorting pumps (alts drift down), the pump condition adds nothing. The real best must beat 95% of
   placebo bests, per objective.
3. Validation (2024-01..2025-09): the 20 best combinations per objective, fixed, with a week-clustered 90% bootstrap.
4. Stability: at least 75% of a combination's grid neighbours (one setting one step away) must also be positive on
   the discovery years. A lone spike in the grid is noise.
5. Frozen: at most 4 combinations from objectives that passed the noise test, validation lower bound > 0 with >= 30
   trades, stability passed; ranked by validation net return a month.
6. Diagnose before the holdout: the same combination on placebo hours; without the best 5% of trades; worst trade,
   week and month; one-minute re-walk.
7. Holdout (2025-10..2026-09), once, only on "run the holdout": PASS if the net mean is > 0 with a week-clustered
   90% lower bound > 0, and at least half the in-sample mean.

Risk report for frozen combinations (descriptive): an account risking a fixed share of equity per trade (sized so a
stop costs that share), at most N trades open at once: return, max drawdown, worst month, losing streaks, and the
fractional-Kelly size that survives the worst observed week.

## Expected outcome, stated in advance

The fade itself is likely (post-pump reversals are well documented). Whether it survives the squeezes, funding and
realistic stops is genuinely uncertain. The squeeze score is the part most likely to matter.

## Parameters (proposed; added to config.yaml with the build)

s14_windows_h [6, 24, 72], s14_pump [0.3, 0.5, 1.0], s14_volume_mult [3, 5, 10], s14_entries [next_hour, red_hour,
red_day, drop_10, drop_20], s14_holds_d [1, 3, 7, 14], s14_stops [0.15, 0.3, 0.5, 1.0], s14_targets [null, 0.2, 0.4],
s14_squeeze_skip [null, 0.5, 0.6667], s14_funding_filter [any, nonneg], s14_volume_lookback_d 30, s14_cooldown_d 7,
s14_red_hour_window_h 72, s14_red_day_window_d 7, s14_drop_window_h 72, s14_fee 0.0005, s14_slippage 0.001,
s14_squeeze_label_rise 0.3, s14_squeeze_label_days 7, s14_train_end 2023-12-31, s14_validation_end 2025-09-30,
s14_holdout_start 2025-10-01, s14_null_reps 100, s14_top_k 20, s14_seed 20261014, s14_exclude [BTCUSDT, ETHUSDT],
s14_min_history_d 30; gates study14_min_trades 100, study14_min_weeks 20, study14_null_p 0.05, study14_val_min_trades 30,
study14_neighbour_share 0.75, study14_max_frozen 4.
