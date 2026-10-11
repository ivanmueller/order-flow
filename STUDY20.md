# Study 20: short VIX futures after the curve normalises (Mack & Sinclair ch. 10 and 15)

Status: DRAFT 2026-10-11 (Matteo: "yes do that"). Not approved; no config entries. The free data step is built
(`src/vix_futures.py`). Research only until a variant passes validation; the tradeable form is a long position in an
inverse VIX-futures ETF (SVXY -0.5x, SVIX -1x) at a Canadian broker, no shorting needed.

## Thesis

Short-term VIX futures usually trade above spot VIX (contango) and roll down toward it as they expire: holders of
volatility insurance pay that roll. After a spike the curve inverts (backwardation); the book's rule is to wait until
it starts to normalise, then sell, because volatility mean-reverts and the roll is richest then (ch. 10: selling
into backwardation is "catching a falling knife"; ch. 15: the short UVXY mean-reversion trade). This is a risk
premium paid for crash exposure. Study 19's 5 inverted weeks (+0.20 EM) point the same way but prove nothing.

## Data (free)

- Cboe VX settlements per monthly contract, 2004-03 onward (`python -m src.vix_futures`); prices before 2007-03-26
  divided by 10 (VBI era). Panel: the two nearest contracts not expiring that day.
- VIX and VIX3M from FRED (on disk; VIX3M starts 2007-12).

## The traded series

A constant-maturity short-term VIX futures index rebuilt from the panel (S&P VIX Short-Term Futures method: daily
return = previous day's weights x contract returns, weights moving linearly from the front to the second contract over
each roll period, in business days). The products simulated are daily-rebalanced -0.5x and -1x of that index minus
the ETF's annual fee (SVXY 0.95%, SVIX 1.35%), no T-bill income (conservative). Signals use closes of day t; the
position changes at the close of day t+1 (one-day lag, conservative); 0.10% of each unit traded as spread/slippage,
$0 commission.

## Variants: a fixed family of 4 (gated)

- X1 (reference): always -0.5x (the plain roll harvest, SVXY-like)
- X2: -0.5x only while VX1 < VX2 at the signal close (curve in contango; Simon & Campasano's basis rule)
- X3 (the book): after VIX >= VIX3M on any of the previous 10 business days, enter -0.5x when VIX < VIX3M again; hold
  20 business days (re-entries extend); flat otherwise
- X4: X2 at -1x (SVIX-like) with a circuit breaker: flat from the next close after any day VIX rises >= 20%
Reported only: -1x versions of X1/X3, VIX3M/VIX ratio buckets, per-year returns, the worst days (2008-10, 2018-02-05,
2020-03, 2024-08-05, 2025-04), time in the market, and the Kelly-style fraction implied by each.

## Rules, written before any result is seen

Periods: discovery 2008-01..2019-12; validation 2020-01..2025-09 (run once, only for variants passing discovery);
sealed year 2025-10..2026-09 (only on "run the holdout", only for validation passes).
A variant passes a period if: mean daily return > 0 with a 90% lower bound > 0 (bootstrap resampling whole months);
compounded (geometric) return > 0; both halves of the period positive; maximum drawdown <= s20_max_dd (proposed 50%:
a strategy that halves the account is not one a person can hold). Validation additionally needs >= 250 days in the
market.

## Expected outcome, stated in advance

X1 positive on average but fails the drawdown rule (2008, 2018, 2020); X2 smaller drawdowns, likely still > 50% in
2018/2020; X3 positive with low time in the market (the best chance); X4 is wiped out on 2018-02-05 (the breaker
cannot act before the jump day) and fails. Any pass will be smaller and lumpier than ETF marketing suggests.

## Proposed parameters (to config.yaml only on approval)

s20_discovery [2008-01-01, 2019-12-31], s20_validation [2020-01-01, 2025-09-30], s20_holdout_start 2025-10-01,
s20_leverage_half 0.5, s20_leverage_full 1.0, s20_fee_half 0.0095, s20_fee_full 0.0135, s20_slippage 0.001,
s20_inversion_lookback_d 10, s20_hold_d 20, s20_breaker_vix_jump 0.20, s20_max_dd 0.50, s20_seed;
gates study20_min_days_in_market 250.

## Steps

1. `python -m src.vix_futures --probe` (checks both Cboe URL patterns; a few requests)
2. `python -m src.vix_futures` (fetches ~275 contract files, builds the panel; free; resumable)
3. After approval: `python -m src.study20 --discover`, then once `--validate`.
