# Study 14 (DRAFT, not approved): crypto pump-fade shorts with a squeeze-risk filter

Status: draft for Matteo's approval. Nothing below is in `config.yaml` yet. Step 0 (the archive inventory) decides
which parts are feasible; any part whose data is missing is dropped before approval, not patched afterwards.

## Idea

Low-cap coins with perpetual futures are pumped (coordinated buying, listings, unlock games) and usually give the
move back. A short in a perpetual needs no borrow; funding is often paid *to* shorts after a pump. The risk is the
squeeze: thin books and liquidation cascades. So the study measures two things: the fade itself, and whether
squeeze warning signs, known at entry, separate the shorts that get run over from the ones that work.

## Data (free)

- Binance USD-M perpetuals from the public archive (data.binance.vision), every symbol including delisted ones
  (no survivorship bias), 2020-01 to the latest month: 1-hour klines for event detection, 1-minute klines for the
  trade walk, monthly funding rates, daily metrics (open interest and long/short ratios) where the archive has them.
- Step 0: `python -m src.crypto_inventory` lists what exists (symbols, first/last month, size, delisted count).
- Research only. Trading needs a venue legally open to a BC resident; Binance is not one. Prices on Binance stand
  in for the market; a live venue's fills and funding would have to be checked before any money.

## Events (point in time, hourly bars, UTC)

- A coin's 24-hour return >= P (proposed 40%) and its 24-hour quote volume >= V x (proposed 5x) its median daily
  volume over the previous 30 days (needs 30 days of history; BTC and ETH excluded).
- One event per coin per 7 days (cooldown), so one pump is not counted many times.

## Variants (family budget 4, fixed now)

| | Entry | Hold | Filter |
|---|---|---|---|
| V1 | open of the next hour after the event | 7 days | none |
| V2 | first hour that closes red after the event (within 72 h) | 7 days | none |
| V3 | first UTC day that closes red after the event (within 7 days) | 7 days | none |
| V4 | as V2 | 7 days | skip if the squeeze score is in its top third (cut from training years) |

Holds of 1, 3 and 14 days are reported for every variant, not gated.

## Fills and costs (conservative)

- Entry and exit: taker, 0.05% fee a side plus 0.10% slippage a side.
- Stop: 50% above entry (proposed), walked on 1-minute bars. A minute whose high reaches the stop exits at the
  stop plus slippage, or at that minute's high if it opened above the stop (a jump through it). If the stop and
  the exit time fall in the same minute, it counts as stopped.
- Funding: the actual 8-hour rates while short (received or paid).
- Size: 1x notional (no leverage), so exchange liquidation is not modelled; leverage is a sizing question afterwards.

## Squeeze-risk score (V4, and reported for all)

Known at entry: open interest change over 24 h vs the price change (shorts piling in), funding rate, open interest
over 24-h volume, top-trader long/short ratio, days since listing, and liquidations if the archive has them. A
logistic model of "the stop is hit within the hold" fitted on training years only; validation reports whether it
ranks the losers.

## Split and holdout

Training 2020-2023, validation 2024, holdout = the last 12 months, sealed until Matteo says "run the holdout".

## Pass rule (per variant, on training + validation)

1. >= 200 trades.
2. Net mean return a trade >= +2% of notional with a 90% bootstrap lower bound > 0, resampling whole weeks (pumps
   cluster in time).
3. Net mean > 0 without the best 5% of trades.

Reported beside it: win rate, worst trade, worst week, longest losing run, funding share of P&L, and a
fractional-Kelly size that keeps the worst observed week survivable.

Holdout: same sign and at least half the in-sample mean.

## Expected outcome, stated in advance

The fade itself is likely (post-pump reversals are well documented). Whether it survives the squeezes, funding and
realistic stops is genuinely uncertain. The squeeze score is the part most likely to matter.

## Proposed parameters (not in config.yaml until approved)

s14_pump_return 0.40, s14_volume_mult 5, s14_volume_lookback_days 30, s14_cooldown_days 7, s14_hold_days 7,
s14_report_holds [1, 3, 14], s14_red_hour_window_h 72, s14_red_day_window_d 7, s14_stop 0.50, s14_fee 0.0005,
s14_slippage 0.0010, s14_squeeze_cut 0.6667, s14_train_end 2023-12-31, s14_validation_end 2024-12-31;
gates study14_min_trades 200, study14_min_mean 0.02, study14_tail_drop 0.05.
