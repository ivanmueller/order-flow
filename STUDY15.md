# Study 15: go long early in crypto volume / volatility surges, searched over every key setting

Status: APPROVED 2026-10-09 (Matteo: "Yes to everything. Testing all variations ... different entry points,
different thresholds, different filters, different trade costs and fills ... run on our data we already have to
start"). Research only: trading needs a venue legally open to a BC resident.

## Why, and the catch

Study 14 shorts after pumps lost 0.3-4.9% a trade, growing with the hold, while costs are ~0.3%: pumped coins kept
drifting up. The long side looks positive before funding and costs. But Study 14 already exposed these coins and
years (2020-01..2025-09) to us, so a search on them is partly fitting the known. The clean test is the sealed year
(2025-10..2026-09), untouched by any study: it decides.

## Data (on disk, $0)

Hourly klines and funding for every USDT perpetual (Study 14 download; 580 coins with data before 2025-10). The
search walks trades on hourly bars for every coin and every month. New: BTCUSDT hourly (~70 small files, free) for
the market filter. Finalists are re-walked on 5-minute bars (download of their months only), and the 1-minute check
is optional.

## Search grid

| Setting | Grid |
|---|---|
| surge window W | 1, 3, 6, 24 hours |
| price move P over W | +5%, +10%, +20%, +30% |
| surge kind K and level | volume 3x / 5x / 10x, or volatility (sum of hourly high-low ranges) 2x / 3x / 5x, vs the median W-hour value over the prior 30 days |
| early filter X | none; skip if the coin rose > 50% in the 7 days before the window |
| entry E | next hour; pullback (first red hour within 24 h, buy the next open); breakout (first hourly close above the signal bar's high within 24 h) |
| hold H | 1, 3, 7, 14 days |
| hard stop S | -10%, -20%, -30% below the entry fill |
| trailing stop TR | none, 15%, 30% below the highest high since entry |
| target T | none, +50%, +100% |
| funding filter F | any; last settled funding at entry <= 0.01% (normal); <= 0 (shorts crowded) |
| market filter B | any; BTC above its 20-day mean at entry |

4 x 4 x 6 x 2 = 192 signal sets x 3 entries x 108 exits x 6 filters = 373,248 combinations. One event per coin per
7 days per signal set; BTC and ETH not traded; 30 days of history needed; events dropped when a trade could cross
into the next period (signal + 16 days).

Trade walk (conservative, hourly): buy the next open 0.10% worse; inside a bar assume the low came first: a bar that
opens through the stop exits at its open, otherwise a stop exits at its level, 0.10% worse; the trailing level uses
the highest high of earlier bars only; stop before target in the same bar; a target fills only on a high above it,
at its price; time exits at the bar open 0.10% worse; 0.05% taker fee a side; actual funding paid by longs (received
when negative); a delisted coin exits at its last close; loss capped at the 1x collateral.

## Costs and fills (varied, reported, one gating stress)

Selection uses the base costs above (already the realistic taker cost). Picking the cheapest cost scenario would be
choosing an assumption, not a strategy, so cost and fill variants are reported for every finalist instead:
slippage 0.10% / 0.20% / 0.50%; maker fee 0.02% on entry (descriptive only, a resting order may not fill); 5-minute
re-walk (and 1-minute when downloaded) instead of hourly. Gating: positive at 2x slippage, and on the 5-minute re-walk.

## Protections (as Study 14)

1. Objectives: net return a trade; net return a month (all trades); t-stat. Ranked combinations need >= 100 trades on
   >= 20 weeks (discovery 2020-01..2023-12).
2. Noise test: the whole search rerun 100 times on placebo hours (same coin, same calendar month, >= 24 h from any
   event of the same signal set); the real best must beat 95% of placebo bests, per objective.
3. Validation 2024-01..2025-09: top 20 per objective, week-clustered 90% bootstrap.
4. Stability: >= 75% of eligible grid neighbours (W, P, level, H, S, TR, T, F one step) positive on discovery.
5. Frozen: <= 4 from objectives that passed (2), validation lower bound > 0 on >= 30 trades, (4) passed; ranked by
   validation return a month; identical trade lists counted once.
6. Diagnose (gating): own placebo p <= 0.05; positive without the best 5% of trades in both periods; positive at 2x
   slippage; positive on the 5-minute re-walk (>= 95% of trades covered). Reported: median trade, share of winners,
   worst trade / week / month, all cost variants.
7. Holdout 2025-10..2026-09, once, only on "run the holdout": PASS if >= 30 trades, mean > 0, week-bootstrap 90% lower
   bound > 0, and >= half the in-sample mean.

Risk report (descriptive): fixed risk per trade sized by the hard stop, at most 10 open, drawdowns, Kelly.

## Expected outcome, stated in advance

The average post-surge drift is probably real in-sample. Long-side funding, stops and fat right tails decide the
economics; most published crypto momentum weakened after 2021. A frozen result that fails the sealed year is the
most likely single outcome.
