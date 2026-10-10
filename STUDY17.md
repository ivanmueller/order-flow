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
- Grid (kept small, stated before running): margin [0, 25%, 50%], TVL floor [low, mid, high], forecast look-back
  [7, 30 days], hold [1, 4 weeks].
- Protections as Studies 14-16: placebo (the same number of random pool-weeks, and shuffled forecasts), 100+ reps;
  discovery 2022-2024, validation 2025-01..09, sealed year 2025-10..2026-09 once; positive without the best 5% of
  weeks; jump stress (the worst weeks repeated); at most 4 frozen.

## Expected outcome, stated in advance

Fees beat LVR in pockets (mid-size pools, calm periods, new pools with hype volume); most small-coin pools are
underpriced for their jump risk and informed flow. Whether a forecast can pick the good pool-weeks out of sample is
genuinely open.
