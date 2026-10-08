# Next study: open-ended prompt for a fresh session

Paste the block below into a new session on this repository. It does not name the next hypothesis; it
asks the next system to inventory what exists, enumerate candidates, score them, and pre-register the
one it judges best, then stop for approval. A KILL is an acceptable outcome of whatever it picks.
Last updated 2026-10-08, after studies 1-7 and the 5f fade (holdout, robustness, NQ).

---

You are taking over a research programme with seven studies behind it and choosing what to test next.
Start by reading, in this order: CLAUDE.md (rules, status block), WRITEUP.md (verdicts, lessons, the
"Where everything is" index), RESULTS.md (every backtest with its build and verdict), REVIEW.md (the
audit of studies 1-3 and the regime-variable caveats in section 3b), CANDIDATES.md (the 2026-10-07 data
inventory and the 15 candidates scored before study 5; several are now spent), SPEC.md (formulas, fills,
gate rules), the RUNLOG.md entries from the Study 5 pre-registration onward, config.yaml and the market
overlays (config.nq/cl/gc/zn/6e.yaml). Then skim the docstrings of src/gex.py, src/flow.py, src/study4.py,
src/study5.py, src/study6.py, src/study7.py and src/price_menu.py so you know which quantities and tools
already exist as code. 

## What has been learned (short form; WRITEUP.md has the numbers)

- Intraday gamma levels and tape confirmation at levels (studies 1-3): gamma strikes are no better than
  random prices; tape absorption at a level is anti-predictive; friction (about 2.3 ES ticks a round
  trip with one tick of slippage each way and $3.98) is about four times the pre-cost signal. Closed.
- Dealer-gamma regime as a 0DTE straddle (study 4): right sign, contrast and placebo, but 1.2 standard
  errors from zero on 266 sessions, and the defined-risk iron fly has no edge. Closed as a standalone trade.
- Last-30-minute ES momentum (study 5): the opposite holds in 2023-25. The close REVERSES the rest of the
  day (slope t -2.3), consistent with 0DTE-era dampening of intraday momentum. Momentum closed.
- The fade (study 5f, the S5b stop trade reversed): in sample +0.008 EM_V, holdout 2026-01..09 +0.0145
  (gate PASS), nudge rule PASS (7 of 8), NQ replication +0.024 (CI above zero), NQ holdout 2026 +0.0072
  (gate FAIL: needed 0.0122; timing p 0.38). A $30,000 bankroll simulation (src/bankroll.py) shows micros roughly
  flat on the 2026 data and 1 NQ +17% with a 31% drawdown. But one extra tick of
  slippage or a 15:55 exit takes the ES edge to zero, and NQ shares four of its five best days with ES.
  Status: real timing, thin economics; SPEC "Partial" means paper trading next, not more variants. NQ's
  edge is larger after costs mainly because its tick is a smaller share of its range.
- Pure aggressor order flow on ES (study 6, $0 pilot on the 110 on-disk tick sessions): imbalance has no
  directional information at 5-15 minutes (corr 0.009 / 0.020, t < 1); best gross edge +0.09 points vs
  0.58 points of friction. Family closed (4 of 4 variants).
- Cross-market momentum into settlement (study 7, CL/GC/ZN/6E, unit EM_R): KILL on all four after costs.
  Timing is real only in ZN (p 0.001), led by FOMC days in the 14:30-15:00 window, and worth about 0.6 of a
  tick against two ticks of cost; the fade loses everywhere after costs. Family closed (4 of 4).
- The repeated lesson: the intraday ideas that found any pattern died on friction. Signals of a fraction
  of a tick per trade cannot pay 2-3 ticks of cost. The one survivor (the fade) holds a
  30-minute window once a day and is still only about one tick wide.

## Lessons from systematic traders who lasted (priors, not evidence)

Practitioner records are not controlled tests: they carry survivorship, are rarely audited, and many
published rules decayed after publication (McLean and Pontiff, JF 2016: anomaly returns fall by about a
third to a half after publication). Weigh them below peer-reviewed results. They are still a useful guide
to WHERE edges have survived costs, and they agree with what this project measured:

- Ed Seykota: early computerized trend following on diversified futures from the 1970s; let profits run,
  cut losses, size every position by risk. Edges at horizons of weeks, low win rates, fat right tails,
  costs negligible relative to the move.
- Richard Donchian and Richard Dennis (the Turtles): channel breakouts (the 4-week rule; 20- and 55-day
  breakouts), volatility-based sizing (N, the average true range), the same rules across 20+ uncorrelated
  futures. Diversification across markets, not refinement within one, carried the results.
- Richard Saidenberg (CTA; Stocks & Commodities interview, August 1997): systematic across horizons from
  day trades to long term; follow the system exactly and commit to it for a fixed period (six months to
  a year) before judging it; build your own rules rather than buying them; trading something less crowded
  gives better fills; the system must suit the trader. Matches this project's pre-registration discipline
  and its paper-trading step.
- Larry Williams: short-term rules on daily bars, notably the volatility breakout (enter beyond the open
  by a fraction of the prior day's range), day-of-week and trading-day-of-month tendencies, the "Oops"
  gap reversal, and COT commercial positioning as a filter. Testable on ES/NQ bars already on disk; many
  date from the 1980s-90s and the post-publication decay above applies.
- Toby Crabel: opening-range breakouts and range contraction (NR4/NR7 days) on daily bars. Also testable
  on the 1-minute bars on disk.
The common thread: the rules that survived trade at horizons where a move is many times the round-trip
cost, and they spread risk across many weakly correlated markets. Any candidate should state where it
sits on those two axes.

## What must not be reopened

Studies 1-3 (levels and tape at levels), study 4's regime straddle, study 5 momentum on ES, study 6 pure
aggressor flow, and any new variant of the 5f fade on ES or NQ (its next step is paper trading). The
original programme count is closed at 18 of 20. From here on (Matteo's rule, RUNLOG 2026-10-07): each new
study family gets at most 4 gated variants, frozen in its pre-registration, and needs its own
out-of-sample confirmation (an untouched holdout period or a fresh, independent market) before any Go.
A family that reuses the 2023-06..2025-12 ES sample says so.

The holdout is 2026-01-01..2026-09-30. For ES it has been read once, for the 5f fade, and for NQ once, for the same fade; both are spent for any
test involving the equity close. For every other market it is untouched. It opens only when Matteo says "run
the holdout".

## Data and budget as of this update

On disk: SPX/SPXW EOD chains and start-of-day OI (2023-06..2025-12), the GEX engine's tables, ES 1-minute
bars, ES tick trades on 110 sampled sessions (around level touches), FRED SPX/VIX, NQ 1-minute bars
(data_nq), and CL/GC/ZN/6E 1-minute bars 2023-04..2025-12 (data_<mkt>), with Study 7's per-market tables.
Price menu (RUNLOG 2026-10-07, quotes only): full ES RTH tick trades ~$0.41 a session (~$280 in sample);
1-minute bars ~$3.3 per market for 2023-06..2025-12; tick trades for CL/GC/ZN ~$36-43 RTH, 6E ~$16.
Databento credit: ~$99.4 of $125 spent; the $100 ask line is effectively reached,
so every further pull needs Matteo's approval. Cheaper routes priced or found: Databento Standard plan
($199/month; trades/MBP-1 for the last 12 months, OHLCV for 16+ years across CME, CBOT, NYMEX, COMEX),
Sierra Chart historical service (CME tick data with aggressor volume from about 2011-2013, from about
$26-56/month plus exchange fees), ThetaData Value ($40/month, option history from 2020). Daily OHLCV for
many futures over 10+ years is likely cheap on Databento; price it with src/price_menu.py (extend the
schema list) before proposing anything that needs it.

## Your task (four steps; no code before step 4 is approved)

1. Inventory what exists, from SPEC.md schemas, src/store.py, src/ingest_*.py and the overlays: what each
   table supports (horizon, point-in-time stamp, sample size, gaps), and what a small priced pull would
   add (get_cost first; ask before any spend).

2. Enumerate at least eight candidates, one paragraph each: mechanism, exact point-in-time inputs, the
   trade, sample size, the strongest prior for and against (published and practitioner, weighted as
   above), and why it is not a relabelling of a closed thesis. Cover at least: one longer-horizon
   (multi-day) rule on the markets already on disk; one cross-market diversified version of a single rule;
   one practitioner rule from the list above tested exactly as published; one option-surface idea that
   uses the SPX chains already on disk (skew, term structure, OI changes, settlement behaviour); and
   paper-trading the 5f fade as an operational step (not a variant).

3. Score each on five things, each argued in a sentence: strength of prior; whether the payoff uses the
   data's own prices (quotes, settlements, bars with SPEC rule-5 fills); expected signal-to-friction ratio
   using this project's measured costs; statistical power on the available sample, counted honestly
   (overlapping positions, tail days, how many years a trend rule needs to separate from zero); and
   independence from the closed theses. Rank them. Prefer the candidate with the best chance of a clean
   answer either way over the one with the most upside.

4. Pre-register the top candidate in RUNLOG.md in the form of studies 5-7: hypothesis, build rules stamped
   at the decision time, eligibility, variants (at most 4, counted against the new family's budget),
   contrasts, placebo, gates frozen before any run, the out-of-sample confirmation it will need, the
   rule-6 checks you commit to, the data cost, and every new parameter for config.yaml. State the
   expected outcome in advance. Then stop and wait for approval. After approval: tests first with
   hand-verified answers, then the module, then one run, then the report against the gates, then stop.
   The gate call is Matteo's.

Constraints throughout: point-in-time only (rule 1), nothing from the holdout unless Matteo says "run the
holdout" (rule 2), every tunable in config.yaml with approval (rule 3), every run logged (rule 4), fills
and costs never more favourable than SPEC.md (rule 5), suspect good results and say what you checked
(rule 6), no spending without asking (rule 7), gate decisions are not yours (rule 8). Treat a KILL as a
complete, useful result. Do not propose anything whose honest purpose is to rescue a closed thesis.
