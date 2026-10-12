# Study 21: Piotroski F-score in neglected US microcaps

Status: DRAFT 2026-10-12 (Matteo: "okay lets do the fundamental microcaps"). Step 0 (free inventory) BUILT in
`src/edgar_inventory.py`. The strategy below is a proposal; nothing in it is frozen until Matteo approves it after
step 0, and no config entries are added before then.

## Thesis

Small, cheap companies that nobody covers react slowly to what their own filings say. Piotroski (2000): among
high book-to-market stocks, buying those with strong fundamentals (F-score 8-9) and avoiding the weak (0-1) earned
~7.5%/yr over the cheap-stock average, almost entirely in small, thinly traded firms with no analyst following.
The edge we would hold is capacity: positions too small for funds. Unlike Studies 1-20 this is not a price pattern.

Expected outcome, stated in advance: the high-minus-low F-score spread is still positive gross in microcaps, but
the long-only after-cost excess over the cheap-microcap average is small (0-4%/yr) and may not clear the gate;
a pass is plausible but not likely. Known risks: decay since publication, costs (1-5% round trip), dilution and
fraud, survivorship (handled by using every filer), delisting losses.

## Step 0: inventory (free, no prices)

`python -m src.edgar_inventory --limit 500` then `python -m src.edgar_inventory`. SEC bulk files (companyfacts,
submissions), cached in data/raw/edgar. A firm-year = one original 10-K; every input is the FIRST-filed value of
that period among facts filed on or before that 10-K (restatements later are ignored; values filed later are not
used). Dead companies included. Rows filed on or after holdout_start (2026-01-01) are dropped. Reports per filed
year: firm-years, financials (SIC 6000-6999), firm-years with all nine signals, public-float size buckets
(< $50M, $50-300M, $300M-2B, >= $2B), F-score histogram of microcaps, signal coverage, companies that stopped
filing, companies with no current ticker (= the price-matching problem).

Signals (all as in Piotroski 2000, NI = net income, A = total assets at year end):
ROA = NI_t / A_{t-1} > 0; CFO_t / A_{t-1} > 0; ROA_t > ROA_{t-1}; CFO/A > ROA (accrual); LTD / average assets fell;
current ratio rose; weighted basic shares did not rise (no equity issue); gross margin rose; sales / A_{t-1} rose.

Choices flagged for approval (made in the step 0 code, changeable):
1. Net income is NetIncomeLoss (Piotroski used income before extraordinary items; few microcaps tag that).
2. No long-term debt tag ever filed -> debt = 0 in both years (leverage "did not fall" = 0); share reported.
3. Equity issue = weighted basic shares rose (splits would count as issues; Piotroski used the equity-issued flag).
4. Gross profit = GrossProfit, else revenue minus cost of revenue.
5. Size from dei:EntityPublicFloat on the 10-K cover (market value held by non-affiliates at Q2 end): understates
   market cap where insiders own a lot; replaced by price x shares once prices are bought.

## Proposed strategy (for approval after step 0)

- Universe: non-financial US filers, float or market cap $5M-$300M, price >= $1, median daily dollar volume
  >= $50k (so a $30k account can trade), positive book equity, top tercile of book-to-market within the universe.
- Formation: monthly; a firm is eligible from the trading day after its 10-K filing date (+1 day) for 12 months.
- Portfolios: long F >= 8 equal weight; reported beside it the F <= 1 group and the cheap-microcap average.
  Hold 12 months; monthly overlapping cohorts.
- Costs: half the quoted or estimated spread each way by dollar-volume bucket (to be set from the price data,
  at least 1% round trip), plus a delisting return of -30% for performance delistings (Shumway 1997) when no
  delisting return is in the data.
- Gate (per variant, <= 4 variants): mean monthly excess over the cheap-microcap average after costs > 0 with the
  90% block-bootstrap lower bound > 0; both halves positive; positive without the best 5% of months; and
  the F >= 8 minus F <= 1 spread positive. Discovery: formations 2010-04..2018-12. Validation once:
  2019-01..2022-12. Holdout (sealed, "run the holdout"): 2023-01..2024-12 (returns to 2025-12, before the
  project seal of 2026-01-01).
- Prices: a survivorship-free daily US price file with delisted tickers (Sharadar via Nasdaq Data Link or
  QuantRocket, one month, priced first; EODHD only if it confirms delisted coverage). Not bought without approval.
- Live, only if validation and holdout pass: EDGAR feed -> weekly rescore -> candidate list -> IBKR limit orders
  with manual approval.
