# Next study: open-ended prompt for a fresh session

Paste the block below into a new session on this repository. It does not name the next hypothesis; it
asks the next system to inventory what exists, enumerate candidates, score them, and pre-register the
one it judges best, then stop for approval. A KILL is an acceptable outcome of whatever it picks.

---

You are taking over a closed research programme and choosing what to test next. Start by reading, in
this order: CLAUDE.md (rules, status), WRITEUP.md (verdicts, lessons, and the "Where everything is"
index), RESULTS.md (every backtest with its build and verdict), REVIEW.md (the audit of studies 1-3,
the modeling caveats on the regime variable in section 3b, and two unrun proposals in section 5),
SPEC.md (formulas and gate rules), the last six RUNLOG.md entries, and config.yaml. Then skim the
docstrings of src/gex.py, src/flow.py, src/study3.py and src/study4.py so you know exactly which
quantities already exist as code.

What is settled and must not be reopened: the intraday level and flow thesis (studies 1-3: gamma
levels are not better than random prices, tape absorption is anti-predictive, friction is four times
the signal), and the dealer-gamma regime as a standalone trade (study 4: real, both-sided, p 0.035 on
the placebo, but 1.2 standard errors from zero on 266 sessions and absent in defined-risk form). Do
not add variants to any of them; 15 of 20 pre-registered variants are used, and passing 20 requires
an NQ replication before any Go. The holdout (2026-01-01 onward) is sealed and stays sealed.

Two standing instructions from Matteo that override any attachment to the project's history:

- The GEX regime is not required. It was the founding idea, and it is weak. If a candidate with
  stronger quantitative reasoning does not use dealer gamma at all, choose it and say plainly that the
  gamma framing is dropped. Keep the GEX engine only if a candidate genuinely needs one of its
  outputs (forward, implied vols, expected move, strike-level open interest); do not include gamma as
  a conditioning variable out of habit.
- Trade frequency matters. A small edge is only worth having if it can be taken often enough for the
  law of large numbers to do the work, as in blackjack: the per-decision expectancy is tiny, the
  decision count is what makes the variance survivable and the edge bankable. So prefer, all else
  equal, hypotheses that produce many independent decisions per month over ones that produce a few
  large ones. But apply the project's own lesson honestly: frequency only helps when the edge per
  decision survives the friction per decision. The tick-scale fades had thousands of decisions and
  lost because 0.6 ticks of edge met 2.3 ticks of cost; the straddle had one decision a day and a
  favourable cost ratio but too few decisions to resolve. The target is the product: net edge per
  decision after costs, times the square root of the number of genuinely independent decisions in the
  sample, which is what a t-statistic is. Say for every candidate how many independent decisions per
  month it offers, what counts as independent (overlapping positions and same-day trades are not),
  and what the net edge per decision would have to be for the gates to resolve on this sample.

Your task has four steps. Do not write code before step 4 is approved.

1. Inventory the data as it actually exists on disk, from the schemas in SPEC.md and the loaders in
   src/store.py and src/ingest_*.py: SPX and SPXW end-of-day chains for every session (bid, ask, close,
   volume, up to 45 days to expiry, stamped at the 17:00 ET curb close), start-of-day open interest per
   contract, the engine's derived tables (per-expiry forwards and implied vols, per-strike GEX, daily
   GEX summary with flip, walls, expected move, percentile), ES 1-minute bars for the whole period, ES
   tick trades with aggressor side on 110 sampled sessions around level touches, FRED SPX and VIX
   closes, the trading calendar with rolls and half days. State what each table can and cannot
   support (horizon, point-in-time stamp, sample size, known gaps such as the zero-bid half-day
   reports), and what a cheap additional pull would add, priced with get_cost before anything is
   bought (the Databento credit has about $44 left; ask before any pull over $5).

2. Enumerate at least eight candidate hypotheses that this data can test, each in one paragraph:
   the mechanism, the exact point-in-time inputs, the trade or payoff that expresses it, the sample
   size it would have, the strongest published prior for or against it, and why it is not a
   relabelling of a closed thesis. Include the two unrun proposals in REVIEW.md (the last-30-minute
   hedging-flow trade; the medium-horizon variance premium timed by the term-structure slope) and the
   three report-only re-checks of the regime variable in REVIEW.md section 3b, but go beyond them:
   think about the option surface itself (skew, term structure, day-over-day open-interest changes as
   a flow proxy, settlement-day behaviour near large strikes), the ES path at horizons other than the
   ones already tested (overnight versus day session, time-of-day structure, variance ratios at longer
   lags, systematic intraday seasonality that can be traded every session), and cross-signals between
   the two (implied versus realised at matched horizons). Candidates that reuse study 4's volatility
   expression are allowed if the conditioning variable is new; candidates with no gamma input at all
   are welcome. Candidates that need data not on disk are allowed if the pull is priced and small; say
   what the 110 sessions of tick data can and cannot support at the decision frequency you propose.

3. Score the candidates on six things, each argued in a sentence, not a number pulled from the air:
   strength of the prior from published evidence and from what this project has already measured;
   whether the payoff can be expressed with the data's own prices (quoted bid and ask, official
   settlement, bars with SPEC rule-5 fills) rather than a model; expected signal-to-friction ratio,
   using this project's measured costs (half-spread plus fees in option premium, 2.3 ticks per ES
   round trip); decision frequency, counted as independent decisions per month after removing overlap
   and same-day clustering; statistical power on the available sample, which is the net edge per
   decision times the square root of the independent decision count, with tail days counted honestly;
   and independence from the closed theses. Rank them. Say which you would run and which you would not,
   and why. Prefer the candidate with the best chance of a clean answer either way over the one with
   the most upside if it works, and prefer many small independent decisions over few large ones when
   the net edge per decision is comparable.

4. Pre-register the top candidate in RUNLOG.md in the same form as studies 3 and 4: hypothesis,
   build rules stamped at the decision time, eligibility, variants (count them against the 20),
   contrasts, placebo (permutation or block permutation as the persistence of the conditioning
   variable requires), gates frozen before any run (sample size, after-cost expectancy in a stated
   unit, bootstrap interval, contrast interval, placebo p, a tail block, and a defined-risk version
   where the payoff is unbounded), the rule-6 checks you commit to, and every new parameter with
   nudges for config.yaml. Then stop and wait for approval. After approval: tests first with
   hand-verified answers, then the module, then one run, then the report against the gates, then stop.
   The gate call is Matteo's.

Constraints throughout: point-in-time only (rule 1), nothing from the holdout (rule 2), every tunable
in config.yaml with approval (rule 3), every run logged (rule 4), fills and costs never more favourable
than SPEC.md (rule 5), suspect good results and say what you checked (rule 6), no spending without
asking (rule 7), gate decisions are not yours (rule 8). Treat a KILL as a complete, useful result. Do
not propose anything whose honest purpose is to rescue a closed thesis.
