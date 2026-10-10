# Study 17: selling volatility through DEX liquidity provision, only when the fee premium beats forecast variance

Status: DRAFT 2026-10-10 (Matteo: "yes draft study 17"). Not approved; no config entries. Step 0 (a free inventory)
is built and can run now; the strategy below is fixed only after step 0 shows which pools have usable history.
Research only: providing liquidity on-chain is self-custody; check tax and legal treatment in BC before any live use.

## The idea in one inequality

A constant-product pool quotes its own price, so arbitrageurs (or better-informed traders) trade against it after
every outside move. For a full-range position worth V, the expected loss to them (loss-versus-rebalancing, LVR;
Milionis, Moallemi, Roughgarden and Zhang 2022) accrues at

    LVR rate = V * sigma^2 / 8      (sigma = volatility of the pair's relative price)

so a hedged liquidity position earns   fees - LVR - hedge costs - gas,   and an unhedged one adds the direction of a
continuously rebalanced 50/50 portfolio. Concentrating a range multiplies fees and LVR by the same factor, so range
placement does not change the sign; hedging removes direction but not LVR. The economics are a short-variance
position paid by fees. Each pool therefore has a fee-implied volatility

    implied sigma = sqrt(8 * annual fee yield)

and the trade is worth taking only when implied sigma exceeds the volatility that will actually be realized. The
quantitative system: forecast realized variance per pool, provide liquidity only when the fee premium clears it by a
margin, step aside before volatility jumps, hedge where a perpetual exists.

## Step 0: free inventory (built: `python -m src.defi_inventory`)

Source: DefiLlama's free API (no key): the yields pool list and each pool's daily history (TVL, base APY from
trading fees), and token price history for both tokens. Pools: two-token pools with impermanent-loss risk (DefiLlama
flags exposure = multi, ilRisk = yes), no stablecoin pairs, TVL >= a floor. History stops before 2025-10-01 (the
crypto holdout; rows on or after it are dropped before anything is saved).

Per pool and week it computes, descriptively:
- fees earned: sum of daily base APY / 365 (DefiLlama's fee yield for a full-range position);
- LVR incurred: quadratic variation of the pair's daily log price over the week / 8 (daily sampling understates
  intraday variance, so this is a LOWER bound on the true cost; noted in every output);
- net = fees - LVR, plus jump days (|daily move| > 20%) and fee-implied vs realized volatility.
Reported: how many pools and weeks exist per chain, venue and TVL bucket; the share of pool-weeks where fees beat
LVR; the median premium; whether the trailing month's premium predicts the next week's (a first look at whether the
idea has any signal); and how often jumps erase months of fees.

Decision after step 0 (Matteo's): if fees rarely beat even the understated LVR, the study stops at no cost. If a
premium exists somewhere, the strategy below is fixed to the pools and data that exist, priced, and sent for approval.

## Draft strategy (to be fixed after step 0)

- Universe: pools with >= 1 year of history, TVL >= floor, both token prices available; split by TVL bucket.
- Each week: forecast next-week variance (HAR on daily and, where available, hourly pair returns), forecast fee
  yield (trailing 7 days), enter when implied sigma >= (1 + margin) x forecast sigma; exit when it no longer is or
  when the forecast jumps (e.g. recent jump days, unlock calendars if obtainable).
- P&L per pool-week: realized fees - realized LVR (hourly prices where available) - gas for entry/exit (per chain)
  - hedge cost where a perpetual exists; reported hedged-equivalent and unhedged.
- Hedge options (each reported): none; short the perpetual where one exists; or the lending hedge -- deposit
  stablecoins on a lending market (Aave or similar), borrow the volatile token, pair it with stablecoins in the pool,
  and adjust the debt as the pool's token balance drifts. Its cost terms: borrow APR on the token minus supply APR on
  the collateral, a swap and gas per adjustment, and liquidation risk when the token rallies (the debt grows; the pool
  position usually cannot serve as collateral). Like the perp hedge it removes direction, not LVR: each adjustment
  buys after rises and sells after falls, which is how LVR is paid. It matters for tokens with a lending market but no
  perpetual, and costs most when borrow demand spikes (exactly when the token is hot). Borrow-rate history: the same
  DefiLlama pool list carries lending markets (borrow APY), added once step 0 says the premium exists.
- Grid (kept small, stated before running): margin [0, 25%, 50%], TVL floor [low, mid, high], forecast look-back
  [7, 30 days], hold [1, 4 weeks].
- Protections as Studies 14-16: placebo (the same number of random pool-weeks, and shuffled forecasts), 100+ reps;
  discovery 2022-2024, validation 2025-01..09, sealed year 2025-10..2026-09 once; positive without the best 5% of
  weeks; jump stress (the worst weeks repeated); at most 4 frozen.

## Expected outcome, stated in advance

Fees beat LVR in pockets (mid-size pools, calm periods, new pools with hype volume); most small-coin pools are
underpriced for their jump risk and informed flow. Whether a forecast can pick the good pool-weeks out of sample is
genuinely open.

## Step 0b result and what it did NOT test (2026-10-10)

Step 0b (RUNLOG 2026-10-10) measured the average pool-week, full-range-equivalent, fees only, unhedged, from the
pool's own prices. With close-to-close variance fees ~= LVR (52% of weeks positive, median ~0); a crude ex-ante
filter (calm and positive trailing 4 weeks, pre-2025) gave v2 +1.3%/yr median, +2.6%/yr winsorized. Matteo's
objection (accepted): that is an inventory, not the strategy. Missing: (1) a real forecast and selection; (2)
returns judged per unit of risk -- a concentrated range scales fees, LVR and the edge by the same factor, so a
small full-range edge with a high Sharpe is a large edge on capital; % of TVL vs T-bills was the wrong yardstick;
(3) the perpetual hedge with actual funding; (4) the LP's bounded loss (the var x V/8 formula let weeks "lose"
more than 100%); (5) incentive rewards (small on Uniswap mainnet, large elsewhere); (6) broken subgraph rows.

## Step 1 (PROPOSED, not approved): the strategy test

Universe: Uniswap v2/v3 Ethereum pools from step 0b whose volatile token(s) have a Binance USDT-M perpetual on disk
(WETH = ETH, WBTC = BTC), so the pool is hedgeable and the outside price is known. Broken rows out by rule, stated
before running: concentration outside [s17_conc_min, s17_conc_max], |daily pool-vs-Binance price gap| >
s17_max_gap, depeg/wrapped-pair symbols (renBTC, EURS, USDD...) listed in config.
Accounting (corrected 2026-10-10 after Matteo asked whether arbitrage is the right comparison): no sigma^2/8
approximation in the P&L. We simulate OUR position. A constant-product (or in-range v3) position's value depends only
on the current price, not the path, so intraday spikes that revert cost nothing and earn fees. Per day: our
position value from the price (v2: full range; v3: our own range, k x concentration, value capped when the price
leaves it), our fees = pool fees x our liquidity / (pool liquidity at the price + ours), and no fees on any day
whose high or low breaches our range (conservative). Unhedged P&L = position value change + fees (direction
included). Hedged P&L = that minus the perp hedge: short delta set at each daily rebalance, P&L -delta x price
change, actual Binance funding, taker fee on each adjustment. With daily hedging the residual is the exact daily
impermanent loss vs the hedge, sum of V x (sqrt(r) - 1)^2 / 2 for full range (~ V x x^2 / 8 for a daily move x):
LVR is its expected value, which is why the forecast question stays fees vs forecast variance. Prices: pool daily
close, checked against Binance (days with |gap| > s17_max_gap dropped as broken).
Forecast (fixed before running): HAR on daily realized variance from hourly Binance returns (1-day, 7-day, 28-day
terms) fitted on discovery data only, plus trailing 7-day fee yield as the fee forecast. Enter a pool for the next
week when fee-implied vol >= (1 + margin) x forecast vol; out otherwise.
P&L per pool-week, on capital: fees - LVR (capped) - gas (s17_gas_usd per entry or exit, at capital sizes
s17_capital_usd) - hedge cost. Hedge variants: none (adds the 50/50 portfolio's direction), or short perpetual
rebalanced daily to the pool's token delta: Binance funding actually paid/received, taker fee per rebalance.
Range: full range, and concentrated k x (s17_concentration [1, 2, 5]) reported as a scaling with its range-exit cap.
Rewards: none on Uniswap mainnet except short programs; reported as a known omission, not added.
Grid (small): margin [0, 0.25, 0.5]; forecast HAR vs trailing 28-day; hold [1, 4] weeks; TVL floor [1M, 10M];
hedge [none, perp] = 24 cells.
Judged on: mean weekly return on capital with a week-clustered 90% CI, Sharpe and t-stat (leverage-free); placebo
(same number of random pool-weeks, and forecasts shuffled across pools), 200 reps; positive without the best 5% of
weeks; both halves; gas break-even capital. Discovery 2020-05..2024-12, validation 2025-01..09 (never looked at ex
ante yet), sealed year 2025-10..2026-09 only on "run the holdout" (needs a second subgraph pull and Binance bars).
Gate per variant (Matteo's to approve): validation mean > 0 with 90% lower bound > 0 after gas and hedge, placebo
p <= 0.05, positive without the best 5% of weeks; <= 4 frozen.
Expected: the hedged, selected pocket earns a small positive Sharpe in discovery; validation decides. Cost $0
(subgraph cache + Binance data already on disk).
