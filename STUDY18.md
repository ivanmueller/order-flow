# Study 18: forecast-selected, perp-hedged Uniswap liquidity on cheap-gas chains (Arbitrum, Base)

Status: APPROVED 2026-10-11 ("Approved as drafted"); config s18_* and study18_* added; src/study18.py built. The free
data step (probe + fetch) is built and can run now; the study module is built after approval. Research only: the
hedge assumes Binance perpetuals and funding, which are not open to a BC resident; funding elsewhere differs.

## Why this, and why not a tweak of Study 17

Study 17 (Ethereum mainnet) found a real liquidity edge in 2020-21 (fees beat the hedged impermanent loss by
~0.4%/week, and the forecast picked better pools than random), ~0 from 2022, and a FAIL in 2025 validation. The
pattern fits a market that became competitive. Re-tuning Study 17 after seeing 2025 would be fitting to 2025.
The hypothesis here is different and stated in advance: the edge MOVED to younger, less professional venues, where
(a) gas is cents, so small positions are not eaten by costs, and (b) liquidity in mid-size pools is thinner.

## Data (free)

- Uniswap v3 on Arbitrum (from 2021-09) and Base (from 2023-08), The Graph subgraphs (same schema as mainnet v3):
  top 1,000 pools per chain by ALL-TIME volume (dead pools included), daily rows before 2025-10-01 only.
- Binance USD-M hourly closes and funding (on disk) for marks and the hedge; a pool is used only if each volatile
  token has a perp (WETH -> ETH, WBTC -> BTC, else SYMBOL or 1000SYMBOL), exactly as Study 17.
- Same broken-data rules as Study 17: pool price vs Binance gap <= 5%, liquidity/TVL in [0.05, 50].

## Accounting (unchanged from Study 17, tested code reused)

Own position simulated daily from the price: amounts follow the pool close, marked at Binance; fee share = pool fees
x our liquidity / (pool liquidity + ours); daily perp hedge with actual funding and 0.05% taker on every change;
entry/exit gas and a 0.30% swap on half the capital; weekly fresh position, costs by continuity. Market-path check
and component breakdown (fees, hedged loss, funding, costs) reported for every variant.

## Variants: a small family fixed now (no grid search), all full range, perp-hedged, hold 4 weeks

Chosen from Study 17's discovery behaviour BEFORE looking at any L2 data:
- V1: margin 0.25, HAR forecast, TVL >= $1M (Study 17's middle frozen cell)
- V2: margin 0.5, HAR forecast, TVL >= $1M (stricter selection)
- V3: as V1 with TVL >= $250k (the "thinner, less professional pools" part of the thesis)
- V4: as V1, only pools younger than 26 weeks at the decision (the "new pools" part of the thesis)
Concentrated ranges (2x, 5x), unhedged, and capital $10k/$100k reported, never gated.

## Proposed parameters (to config.yaml only on approval)

s18_chains [arbitrum, base]; s18_gas_usd 0.50 per mint or burn (conservative: L2 transactions cost cents);
s18_capital_main 25,000 per position (reported at 10,000 and 100,000); s18_tvl_floor_small 250,000;
s18_new_pool_weeks 26; s18_discovery_end 2024-12-31; HAR refit on the L2 discovery weeks only. Everything else
reuses the s17_* values (swap 0.30%, perp 0.05%, gap 5%, concentration bounds, token map, fee/variance windows).

## Rules, written before any L2 data is seen

Stage A (discovery: Arbitrum 2021-09..2024-12, Base 2023-08..2024-12, pooled; per variant):
net mean > 0 with 90% lower bound > 0 WITH funding AND WITHOUT funding; selection placebo p <= 0.05 (random pool-
weeks from the same week's universe, 200 reps, on gross results); both halves of discovery positive WITHOUT funding;
positive without the best 5% of weeks; >= 26 active weeks. Also reported: each chain separately, by year.
Validation (2025-01..09, once, only variants passing Stage A): the same tests, >= 13 active weeks.
Sealed year (2025-10..2026-09): only on "run the holdout", only for validation passes; needs a new fetch.
Family: 4 gated variants (V1-V4); nothing added after seeing results.

## Expected outcome, stated in advance

Most likely: Stage A fails WITHOUT funding (the L2s were already competitive by 2023-24, and the Arbitrum 2021-22
period resembles mainnet's decline). A pass, if any, most likely in V3/V4 (thin or new pools), with small capacity.

## Steps

1. `python -m src.defi_subgraph --chain all --probe` (checks both subgraph ids and the fields; 4 small queries)
2. `python -m src.defi_subgraph --chain all` (caches ~2,000 pools' daily rows; a few thousand queries; resumable)
3. After approval: `python -m src.study18 --build`, `--stage-a`; validation once, only if something passes.

## Result (2026-10-11): Stage A FAIL on all four
Without funding, after costs: V1 -0.024%/wk (lb -0.035), V2 -0.010%, V3 -0.034%; V4 had 2 active weeks (untestable as
designed: young pools rarely have >= $1M TVL, a perp, and 28 days of history). With zero transaction costs the
liquidity part is still ~0 (+0.003 to +0.016%/wk): fees about equal the hedged impermanent loss. Selection beats
random pools only by losing less. Validation not run; holdout untouched.
