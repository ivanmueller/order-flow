# Thesis v2: trade against compelled flow, from the dealer's chair

Clean-slate review, 2026-10-08. Inputs: every study in this repository (RUNLOG.md, RESULTS.md, WRITEUP.md,
REVIEW.md), the two research briefs Matteo supplied ("Additional Intraday Edge Research for a Diversified,
Pre-Registered Backtest Program" and "First-Principles Alpha Research", both 2026-10-07), and primary sources
checked on 2026-10-08 (listed at the end). Nothing here is a backtest. No candidate below has been run, and the
ones marked for testing still need a frozen pre-registration and Matteo's approval.

## 1. Where we are, in one table

| Family | What it assumed about the people on the other side | Result |
|---|---|---|
| Gamma levels, tape at levels (studies 1-3) | Options dealers' hedging makes specific strikes hold or break | Levels no better than random prices; tape at a level anti-predictive; ~0.6 tick of signal vs ~2.3 ticks of cost |
| Gamma regime as a 0DTE straddle (study 4) | Dealer gamma sets the day's realized volatility | Right sign, contrast and placebo; 1.2 SE from zero; gone in defined-risk form |
| Close momentum, then the fade (5, 5f, NQ, NQ holdout) | Hedgers chase the day's move into the close | The opposite: the close reverses the day, strongest when dealer gamma is positive. ES holdout pass, NQ holdout fail (+0.0072). About one tick a trade |
| Pure aggressor flow (study 6) | Aggressive order flow tells you where price goes next | No information at 5-15 minutes (corr 0.01-0.02) |
| Momentum into settlement, CL/GC/ZN/6E (study 7) | Hedging flow into commodity, rate and FX settlements | Absent in CL, GC, 6E; real in ZN only on Fed days, 0.6 tick vs 2 ticks of cost |

Everything we tested was one of two things: a guess about which way dealers would push price inside the day,
or a pattern that dealers themselves already harvest. Both lose to the same arithmetic. The people who make
markets see their own inventory, are faster, and pay less to trade; any minute-scale pressure they create is
small and gone before a retail order can use it. The one durable result, the close fade, is exactly what a
long-gamma dealer book would produce, and it is worth about one tick: the dealers keep the rest.

## 2. The market-maker lens: what they do, and what they are forced to do

A market maker earns the spread and is paid for warehousing risk. It does not want direction; it wants to
turn inventory over and stay inside limits. So the useful question is not "where are dealers positioned?" but
"when is a dealer, or the customer a dealer serves, forced to trade at a predictable time or under a binding
constraint, and how much does it pay to get that done?" The first-principles brief states the same test:
who is compelled to transact at a predictable time, and can the induced price move be traded after costs?

| Participant | Obligations and constraints (verified where cited) | What that forces |
|---|---|---|
| Listed-options market makers (SPX/SPXW) | Cboe appointed market makers must quote continuously for 90% of the required time in 60% of the series in their classes (SEC filing 34-90482). Bona fide market making is exempt from the Reg SHO locate rule. | They must take the other side of customer option flow, then hedge delta in ES/SPY. Per Cboe, net 0DTE market-maker hedging is about 0.2% of SPX daily liquidity and customer 0DTE flow is "extremely balanced": small, which matches studies 1-3 and 6. |
| Treasury primary dealers | Under the NY Fed's dealer policy (superseded in 2016 by its counterparty policy), dealers were to bid in every auction for at least their pro-rata share at reasonable prices. Capital and leverage ratios bind at period ends. | They absorb new supply, then sell it down. NY Fed SR 1188 (2026): yields rise in the 3 hours before auctions and reverse in the 3 hours after; total 0.7-1.2 bp over 1991-2024, but less than half that after 2014. |
| FX dealer banks at the fixes | Absorb client orders for the WM/R 4 pm London and other fixes; hedge, then unwind | Krohn, Mueller and Whelan (JF): the dollar rises into the fixes and falls after, driven by large banks' inventory hedging and unwinding |
| Leveraged and inverse ETF sponsors | Prospectus mandate: reset leverage daily, near the close | "Perfectly predictable flows" (Barbon): end-of-day momentum, then reversal; LETF effects declining over time, option-hedging effects persistent |
| Pensions and balanced funds | Rebalance to target weights on a calendar or a drift threshold | Harvey, Mazzoleni and Melone (NBER 33554): when stocks are overweight, equity returns are about 17 bp lower over the next day; about $16 bn a year paid to front-runners |
| Bond index trackers and insurers | Match the index's month-end duration extension; window dressing | Hartley and Schwarz: coupon Treasuries earn about 20 bp at the 10-year in the last 3-5 trading days of the month, Sharpe about 1 after costs, also in Treasury futures |
| Banks at quarter and year end | Leverage ratio and G-SIB scores measured on period-end balances | ECB study: balance-sheet repo falls about 12.5% before quarter ends and up to 25% before year ends, more for G-SIBs and low-leverage banks |
| Commodity index funds | Published roll schedule (GSCI 5th-9th business day) | Mou: front-running the Goldman roll paid in the 2000s; likely competed away since |

The pattern across the table is the thesis: the obligations that make a market maker trade at a known time
are public, the trades are large, and the price concession is paid to whoever stands ready on the other side.
Market makers capture the minute-scale part. What is left for a slower trader is the part that lasts longer
than their balance sheet wants to hold: hours to days, around a deadline, when dealer capacity is tight.

## 3. Rules of the new thesis

1. Name the compelled actor and the rule or contract that compels it. No actor, no study.
2. Trade as the patient counterparty to the compelled flow, or after the dealer's unwind starts. Never race
   dealers to the first minute.
3. The expected gross concession must be at least three times our round-trip friction in the same unit
   (ES 0.58 points; NQ 0.70; ZN 0.035 points, about 2.25 ticks of 1/64; 6E about 1.2 bp). Every intraday idea
   so far failed this arithmetic before it failed any statistics.
4. Horizon long enough that one tick does not decide the result: hours to days, not minutes.
5. The clock comes from the mandate (auction time, fix time, month end, roll dates), never from the data.
6. Prefer effects with a published, post-publication out-of-sample period we can test, and keep our own
   untouched period (2026-01..09 is untouched for every market except ES and NQ).
7. A dealer-constraint variable (period end, auction size, volatility, balance-sheet stress) may be a
   pre-registered conditioning variable, because the mechanism predicts a bigger concession when dealers
   are constrained.

## 4. Our results, reread through this lens

- **Gamma levels (1-3) failed** because option dealers' net hedging is tiny and balanced (Cboe's 0.2%), so no
  strike carries enough forced flow to matter at a retail scale.
- **The regime straddle (4) was the right idea in the wrong expression.** It measured the environment dealers
  create, not a trade against their constraint.
- **The close fade (5f) is a dealer footprint.** It is strongest when net gamma is positive, consistent with
  long-gamma books and LETF liquidity providers absorbing the day's move. Dealers already capture it. What is
  left is about one tick, which is what the holdouts measured.
- **Aggressor flow (6) has no information** because dealers absorb it at no predictable cost to themselves.
- **Study 7's only real signal (ZN on Fed days)** was a scheduled-event effect, and it was too small for ZN's
  tick.

## 5. The two briefs, through the same lens

The Additional Intraday Edge brief is careful and its statistical protocol (minimum detectable effect,
effective N, Holm across primaries) is sound. Three corrections from today's facts:

- **Treasury auctions:** SR 1188 puts the post-2014 total pressure at under half of 0.7-1.2 bp, so the reversal
  half is roughly 0.2-0.4 bp of yield. On ZN, at about $60-80 per bp per contract, that is about 1-2 ticks gross
  against 2.25 ticks of friction. It fails rule 3 on the prior alone; not worth a study at our costs.
- **The FX fix** passes the dealer-mechanism test, and its 6E data is already on disk, but its own source says
  the client-side strategy did not survive costs. Expected gross is a few bp against about 1.2 bp of friction,
  which is borderline for rule 3. Worth running only because it is free.
- **Its stale notes:** Study 7 has been run (KILL), and the 6E, CL and ZN bars are on disk.

The API-to-EIA drift, the natural-gas storage premium and the USDA continuation are information or
risk-premium effects, not dealer constraints. They are legitimate, but they belong to a different thesis. Crypto basis
and single-stock shocks need new data and venues. In the First-Principles brief, the settlement-distortion
idea overlaps study 7's windows. LETF net rebalancing is a dealer-adjacent mandate, but its ES expression
lives in the spent close window.

## 6. Candidates under the new thesis

Friction ratio = expected gross concession divided by our round-trip cost in the same unit (rule 3 needs at
least 3).

| # | Candidate | Compelled actor | Expected gross vs friction | N and out of sample | Data and cost | Verdict |
|---|---|---|---|---|---|---|
| M1 | Month-end Treasury demand: long ZN into the last 3-5 trading days of the month | Index trackers matching month-end duration extension; insurers; window dressing | ~20 bp of price (Hartley-Schwarz) ≈ 0.22 pts ≈ 14 ticks vs 2.25: **~6x** | ~31 month-ends in sample + 9 untouched (2026); a free existence check on decades of FRED Treasury yields, with 2020-2026 as post-publication out of sample (~80 months) | ZN bars on disk; FRED free; ZN 2026 bars ~$1 | **Run first** |
| M2 | Pension rebalancing: when stocks have outrun bonds month to date, short ES / long ZN into month end | Balanced funds and pensions with fixed targets | ~17 bp next day (Harvey et al.) ≈ 9-10 ES pts vs 0.58: **>10x** gross, but it is a slope, not every month | Monthly; small on 2.5 years. FRED SP500 and DGS10 give a free check over ~10 years | ES/ZN bars on disk; FRED free | **Run second**, same family as M1 (both month-end compelled flow) |
| M3 | FX fix inventory unwind: long 6E from 16:05 London to the US afternoon | FX dealer banks unwinding fix inventory | A few bp vs ~1.2 bp: **~1-3x** | ~640 days + ~185 untouched (2026) | 6E bars on disk; 2026 ~$1 | Free, so run, but expect a cost KILL |
| M4 | Treasury auction reversal | Primary dealers' bidding expectation | Post-2014 ~1-2 ticks vs 2.25: **<1x** | ~120 pooled auctions | ZN on disk | **Do not run** (fails rule 3 on the published size) |
| M5 | Quarter- and year-end balance-sheet stress | G-SIBs, low-leverage banks | Shows up in repo/funding, not cleanly in a futures price we can trade | 11 quarter ends | Funding futures data not on disk | Park; a conditioning variable for M1/M2 instead |
| M6 | Commodity index roll (GSCI/BCOM, 5th-9th business day) | Index funds' published roll | Calendar-spread concession likely decayed since Mou | ~31 rolls | CL second-month bars ~$3.5 | Later, if M1/M2 justify a commodity leg |
| M7 | OPEX pinning to large expiring strikes | Options market makers' delta hedging of expiring OI | Small per Cboe; overlaps the closed gamma-level family | ~31 monthly expiries | SPX OI on disk | Do not run (relabels a closed thesis) |
| M8 | LETF rebalance-size conditioning of the close | LETF sponsors' daily mandate | Declining (Barbon); ES close data already seen | n/a | n/a | Do not run (spent window, seen data) |

## 7. Recommendation

Open one new family, **"month-end compelled flow"** (budget of 4 gated variants, its own out-of-sample period),
with M1 as the primary and M2 as the second variant. Both put us on the patient side of trades that index
funds, insurers and pensions must make by a published deadline. Both have published effects many times our
costs, and both hold for days, not minutes. Run M3 alongside as a separate, free, one-variant check, with the
expectation of a cost KILL stated in advance.

Proposed order:
1. A free existence check with FRED Treasury yields and S&P 500 closes: is the month-end effect still there
   after its 2019 publication? This is a report, not a gate, and it uses no money.
2. Pre-register M1 and M2 on ZN and ES futures: 2023-06..2025-12 in sample, ZN's and ES's month-ends in 2026
   as out of sample (the ES 2026 data was used only for the close window, but we will state that). Conservative
   fills, one tick each way, $3.98 a round trip.
3. M3 on 6E with the 2026 6E data as its out-of-sample period.

Budget: the ledger is at about $99.40 of $125, past the $100 ask line, so every pull needs Matteo's yes.
Steps 1 and 2 need at most about $2 of 2026 bars; everything else is on disk or free.

Open questions for Matteo before any pre-registration:
- Is a multi-day hold (entering about 4-5 trading days before month end, exiting at the last close) acceptable for
  how you trade, including overnight margin on ZN and ES?
- Should the family trade ZN only, or ZN and ES together as one rebalancing basket?

## 8. Two tracks (Matteo, 2026-10-08)

Matteo's premise: dealers are not perfect, they leave money on the table that is too small for them to chase,
and an educated retail quant can collect it. Two tracks follow from that.

**Track A, slow and large: compelled flow over days.** Month-end Treasury demand and pension rebalancing (M1,
M2). The concession is many times our costs, and dealers leave it because holding inventory over days around
a deadline is what their balance sheets are not built for. Pre-registration draft: RUNLOG, Study 8.

**Track B, fast and small: the blackjack idea, done honestly.** A card counter wins because the edge per hand
is larger than the house edge, and repetition turns a small positive edge into a reliable total. In trading,
the house edge is the spread plus fees, paid on every hand. Repetition multiplies whatever is left after costs,
negative or positive; it cannot turn a negative into a positive. Our measured numbers:

| Measured gross edge per trade (ES) | Cost per round trip | Net |
|---|---|---|
| Touch reversion at levels: ~0.6 tick | ~2.3 ticks (one tick each way + $3.98) | negative |
| Aggressor-flow continuation (study 6): ~0.25 tick | ~2.3 ticks | negative |
| Close fade (5f): ~2-3 ticks | ~2.3 ticks | about +1 tick in sample, ~0 in stress |

So Track B is a cost problem before it is a signal problem. Of the 2.3 ticks, about 2 are the spread we cross on
entry and exit; the $3.98 is under a third of a tick on ES. Contract size does not help: on MES the $1.18 fee
alone is about 0.94 tick. The only way to change the table's rules is to stop paying the spread and start
earning it, which means passive limit orders: being the market maker for a moment. That opens the real
risk of the maker's business, adverse selection. A resting order is filled most often when the price is
about to move through it. HFT market makers win the queue race and the fills an outsider gets are the ones they
decline.

The niche where a slow maker can win combines the two tracks: rest passive orders only when the counterparty
is compelled rather than informed (benchmark windows, month-end, index and ETF rebalancing, scheduled fixes).
The forced trader pays the spread for reasons unrelated to the next minute's price, so adverse selection
should be lower exactly then.

Testing it honestly needs quotes (the L1 order book), not bars. Fills follow the SPEC rule-5 rule (a resting
order fills only on a print one tick through), with a queue-position estimate from the L1 order book
as a secondary, less conservative scenario. Data: ES MBP-1 or TBBO for chosen windows only (to be priced;
the Databento Standard plan includes 12 months of L1). Design: RUNLOG, Track B note.

## Sources (checked 2026-10-08)

- Cboe market-maker quoting obligations: SEC release 34-90482, https://www.sec.gov/files/rules/sro/cboe/2020/34-90482.pdf
- NY Fed primary dealer operating policy (superseded 2016): https://www.newyorkfed.org/markets/pridealers_policies.html
- Fleming, Liu, Nguyen, "Intraday Price Pressure and Order Flow Around U.S. Treasury Auctions," NY Fed SR 1188: https://www.newyorkfed.org/research/staff_reports/sr1188.html
- Hartley, Schwarz, "Predictable End-of-Month Treasury Returns": https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2019/12/17-19.Schwarz.pdf
- Harvey, Mazzoleni, Melone, "The Unintended Consequences of Rebalancing," NBER 33554: https://www.nber.org/papers/w33554
- Krohn, Mueller, Whelan, "Foreign Exchange Fixings and Returns Around the Clock": https://www.bankofcanada.ca/2021/10/staff-working-paper-2021-48/
- Barbon, "Liquidity Provision to Leveraged ETFs and Equity Options Rebalancing Flows": https://abarbon.com/papers/liquidity-provision-to-leveraged-etfs-and-equity-options-rebalancing-flows
- Cboe, "0DTEs Decoded": https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact
- ECB/SUERF, "Window dressing of regulatory metrics: evidence from repo markets": https://www.suerf.org/suerf-policy-brief/65559/window-dressing-of-regulatory-metrics-evidence-from-repo-markets
- Mou, "Limits to Arbitrage and Commodity Index Investment: Front-Running the Goldman Roll": https://papers.ssrn.com/abstract=1716841
