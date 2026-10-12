# Study 16: three structural crypto theses, market-neutral, tested at once; then variations of any that pass

Status: APPROVED 2026-10-09 (Matteo: "can we test all 3 of these at once and then explore variations of the most
profitable ones if there is one?"). Research only: these need short perpetual positions, and no venue open to a BC
resident offers them as far as we know.

## Why these, after Studies 14-15

Studies 14-15 timed single coins and mostly measured the market's direction (random hours did as well). Each thesis
here names who pays and why, and holds equal dollars long and short (or hedged with BTC), so the market's direction
cancels.

| Variant | Thesis | Base rule (fixed in advance) |
|---|---|---|
| C1 funding carry | leveraged longs pay shorts on crowded coins | each week rank by funding settled over the last 7 days; short the top fifth, long the bottom fifth |
| C2 momentum | coins that outperformed over weeks keep outperforming (published crypto factor) | rank by the 28-day return; long the top fifth, short the bottom fifth; weekly |
| C3 reversal | 1-week winners give it back (what Studies 14-15 saw) | rank by the 7-day return; long the bottom fifth, short the top fifth; weekly |
| C4 new-listing short | locked supply unlocks and early holders sell for months | short every coin in its first 90 days after listing (from day 1), equal weight, hedged long BTC; weekly |

## Data (on disk, $0)

Hourly klines and funding for every USDT perpetual (Study 14) plus BTCUSDT (Study 15). Daily panel: price at 01:00 UTC
(execution), close at 00:00 UTC (signals), prior-day quote volume, funding. Research span 2020-02-01..2025-09-30; the
year 2025-10..2026-09 stays sealed.

## Portfolio rules (all variants)

Signals use data up to 00:00 UTC; trades at the 01:00 UTC price. Universe at each rebalance: trading at both times,
>= 30 days of history, 30-day median daily quote volume >= $1M, BTC, ETH and index contracts excluded (C4: listing
age instead of history; volume over the days available). Legs: equal weight, 0.5 gross each (C4: short leg 0.5, BTC
long 0.5); at least 5 names a leg, else flat. Returns include funding (longs pay, shorts receive) and a coin delisted
during the hold exits at its last price; a short's loss is capped at its own notional (1x isolated margin).
Costs: 0.05% fee + 0.10% slippage on every unit of weight traded (turnover). Reported: 2x slippage, funding vs price.

## Stage A gate (per variant; 4 variants = the family budget)

1. Net mean weekly return > 0 with a week-clustered 90% bootstrap lower bound > 0.
2. Beats its placebo: rankings shuffled within the same universe (C4: the same number of random coins listed >= 365
   days earlier, same hedge), 200 reps; p <= 0.05.
3. Net mean > 0 in both 2020-02..2023-12 and 2024-01..2025-09.
4. Net mean > 0 without the best 5% of weeks.

## Stage B (only for variants that pass all of Stage A; a new family with its own budget)

Grid per variant: quantile [10%, 20%, 33%] x minimum volume [$0.25M, $1M, $5M] x weighting [equal, inverse
volatility] x the variant's own look-back and hold (C1 funding look-back [1,3,7,30] d x hold [1,3,7,14] d; C2
look-back [14,28,56,90] d x hold [7,14,28] d; C3 look-back [1,3,7] d x hold [1,3,7] d; C4 window [30,90,180] d x
delay [1,7,30] d x hold [7,14] d, no quantile). Discovery 2020-02..2023-12, objective net mean per week (>= 52 weeks
traded). Noise test: the whole grid on shuffled rankings, 100 reps, real best above 95% of placebo bests. Top 10 to
validation 2024-01..2025-09 (lower bound > 0), stability >= 75% of grid neighbours positive on discovery, 2x
slippage positive, positive without the best 5% of weeks; at most 4 frozen per study; holdout once on "run the
holdout": >= 26 weeks, mean > 0, lower bound > 0, >= half the in-sample mean.

## Expected outcome, stated in advance

C1 is the most likely to survive: funding carry is well documented, though much smaller after 2021. C2 and C3 are
published but weak recently. C4 is the least studied and the most exposed to squeezes on hot new listings.
