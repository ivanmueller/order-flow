# Next study: data inventory, candidates, scoring

2026-10-07. Written from NEXT.md (steps 1 to 3); the pre-registration that step 4 asks for is in RUNLOG.md
under the same date. Read in NEXT.md's order: CLAUDE.md, WRITEUP.md, RESULTS.md, REVIEW.md, SPEC.md, the
last six RUNLOG entries, config.yaml, and the code (`store.py`, `ingest_*.py`, `calendar.py`, `gex.py`,
`flow.py`, `study3.py`, `study4.py`). The `data/` directory is not in the repository and this container has
no Databento key, so nothing was re-run on real data and `get_cost` could not be called; every count below
is quoted from RUNLOG/RESULTS or read from the code, and every price is an estimate from the rates the
spend ledger already recorded. The test suite was run here: 90 passed.

Open PR #3 adds two standing criteria to NEXT.md (the GEX regime is optional; score decision frequency and
net edge times root N). Both are compatible with the version of NEXT.md this session was given, so they
are included as extra columns rather than as overrides.

Planning arithmetic (minimum detectable effects, published sizes against this sample) was run in code;
the assumptions are stated where the numbers are used. MDE below means the after-cost mean a variant
needs for an 80% chance that its 90% interval clears zero: 2.49 standard errors. Clearing zero at all
needs 1.645 standard errors.

## 1. Data inventory

All research loads go through `calendar.seal()`; nothing at or after 2026-01-01 is read. Raw files are
shared across configs; derived tables are per config (`data/derived/` for the main config).

| table (path) | what it holds | point-in-time stamp | in-sample size | can support | cannot support, known gaps |
|---|---|---|---|---|---|
| `options_eod` (`raw/options/eod/YYYYMMDD_{SPXW,SPX}.parquet`) | ThetaData free-tier EOD report per quote date: quote_date, symbol, expiration, strike, right, bid, ask, close (last trade), volume (daily, unsigned) | the 17:00 ET Cboe curb close of the quote date (RUNLOG 2026-10-05 scan); used as the prior-close quote by the next session | 294 / 504 / 502 files for 2023 / 2024 / 2025, i.e. every quote date 2023-06-01 to 2025-12-30, two roots | any option position entered at a 17:00 bid or ask and held to an SPXW PM settlement within 45 days, or closed at a later 17:00 bid or ask; per-expiry forwards, ATM vols, skew and term structure out to 45 days | expiries beyond 45 calendar days (no 2-3 month vol); intraday option prices (no entry or exit between 17:00 reports); signed or open/close flow; SPX AM-settled monthlies cannot be settled (no SOQ on disk); history before 2023-06-01 (403); half-day reports are all zero bids (six sessions have no surface); 2024-12-02's dailies are zero-bid |
| `options_oi` (`raw/options/oi/YYYYMMDD_{SPX,SPXW}.parquet`) | Databento OPRA start-of-day open interest, expirations D to D+45, OI > 0 | published before 09:30 ET on D, reflecting positions after D-1's trading | every in-sample session 2023-06-01 to 2025-12-31; 2024-06-03 and 2025-10-22 flagged degraded by Databento | strike-level positioning known at the D open; day-over-day change OI(D) - OI(D-1) as an unsigned flow proxy for D-1 | anything at the 17:00 D-1 entry used by study 4 (the D file arrives after it); who bought or sold; positions opened and closed the same day (0DTE intraday flow never appears) |
| `gex_daily` (derived) | per session: s0 (nearest-expiry forward), em (D-1 17:00 ATM straddle mid, nearest expiry), net_gex, net_gex_0dte, gex_pct, gex_pct_0dte, flip, walls, top strikes, basis, nearest_root/exp | D-1 17:00 quotes plus OI published before 09:30 D: the D row is knowable at 09:30 D, not at 17:00 D-1 | 642 sessions 2023-06-02 to 2025-12-31; gex_pct from 2023-12-01 (126-session warm-up), 510 sessions; net_gex < 0 on 38.8% of days; flip missing 3% | a vol scale (em) for every session; a regime state that is point in time for any decision after 09:30 D | a regime knowable at 17:00 D-1 without a one-session lag (study 4 had to lag it); the sign convention is an assumption (REVIEW 3b); 7 sessions missing (2023-06-01 and the six post-half-day sessions); 2024-12-03 has an 18-day em (levels skip it) |
| `gex_vols` (derived) | per session, root, expiry within 45 days: OTM implied vol per valid strike and the forward F | D-1 17:00 report | 642 sessions | ATM vol by expiry (term structure 0 to 45 days), skew at fixed moneyness, implied variance for any horizon to 45 days | the calendar-minute clock (T_q from 17:00) treats overnight as normal time, so short-dated vols are 23-hour vols (REVIEW 3b.2); the `gex.py` docstring still says T_q starts at 16:15, the code uses `quote_time` 17:00 |
| `gex_strikes` (derived) | per session and strike: call GEX, put GEX, total, at S0 | as gex_daily | 642 sessions | strike profile, largest-OI and largest-gamma strikes | as gex_daily |
| `es_bars` (`raw/es/bars/YYYY-MM.parquet`) | Databento ES.v.0 1-minute OHLCV, 18:00 to 17:00 ET, instrument_id; not back-adjusted | bar open time in UTC; a bar is known at its close | every minute from 2023-04 (two months before the sample, for warm-ups) to 2025-12 in sample; about 640 equity sessions | any ES trade with SPEC rule-5 bar fills; returns and realised variance at any horizon from one minute to a session; overnight versus day session; time-of-day structure; delta hedges for option positions | intra-minute ordering (same-bar ambiguity counts as a loss); aggressor side; any series across a roll (about 11 roll days in sample; `calendar.roll`); 8 half days end at 13:00 |
| `calendar` (derived) | date, prev_date, instrument_id, roll, half_day, n_rth_bars, equity_session | from the bars and FRED dates | ES-only holidays excluded from equity sessions | every previous-session lookup, roll and half-day handling | n/a |
| `es_trades` (`raw/es/trades/YYYYMMDD/*.parquet`) | ES tick trades with aggressor side (B +1, A -1, N dropped), sequence | event time | 110 sampled sessions (stratified by year, seed 20231101), 201 merged windows from t0 - 10 to t0 + 45 minutes around Stage 2 touches between 09:31 and 15:50; $32.57 | tick-level fill checks and tape features inside those windows | whole-session flow, and any one-decision-per-day test: 110 sessions give a standard error about 2.4 times that of 640 (for the study 5 trade below, an MDE near 0.09 EM); the 15:30 to 16:00 window is covered only on sessions with a late touch |
| `roll_basis` (`raw/es/roll_basis/`) | D's front contract at the D-1 cash close, roll days only | D-1 16:00 | about 11 days | the basis on roll days | anything else |
| `daily` (`raw/daily/spx_vix.parquet`) | FRED SP500 and VIXCLS closes from 2019-01-01 | the official 16:00 SPX close (SPXW PM settlement value) and the VIX close of each date | every date since 2019 | settlement of SPXW positions; VIX controls; close-to-close realised vol back to 2019 | open, high, low; VIX term structure (VIX9D, VIX3M are not pulled; VXVCLS is free on FRED if ever needed) |
| study outputs (`touches`, `features`, `sim_trades`, `band_trades`, `straddle_trades`) | per-touch labels, tape features and simulated trades (studies 1 to 3); band trades (study 3); straddle, long straddle and iron fly per session (study 4, 502 sessions) | as built | as logged | contrasts and overlap checks for a new study | new tests: their outcomes have been looked at; reusing them needs a fresh pre-registration and counts against the 20 |

What a cheap additional pull would add. None of these has been priced with `get_cost` (not possible
from this container); the estimates use the rates in RUNLOG and the `--price-only` command prices each
exactly. Databento credit left is about $44 (ledger about $81 of $125); the $100 ledger line leaves about
$19 of headroom without `--allow-past-total`.

| pull | estimate | what it adds | needed for the recommended study? |
|---|---|---|---|
| ES 1-minute bars 2019-01 to 2023-03 (`python -m src.ingest_futures bars --start 2019-01-01 --end 2023-03-31 --price-only`) | about $5.4 at the pilot's $0.105 per month (51 months), just over the $5 ask line; about $16 back to 2010-06 | about 1,000 more ES sessions (FRED SPX and VIX from 2019 are already on disk, so the calendar's equity flags and a VIX-implied EM cover them) | optional (study 5's sample B). It raises study 5's existence power from 0.52 to 0.76 if the effect has been constant since 2019, and shows a decay pattern if it has not, but it lowers the after-cost power (0.18 to 0.08) because older years carry more friction in EM terms. Prerequisite: the pull rebuilds the calendar, and `calendar.stage3_days` would then redraw the stage-3 day sample of studies 1 and 2, so that day set must be frozen to disk first (a small logged code change). An NQ replication would then need NQ from 2019 too (about $8 to $10). Back to 2010 is not worth it: ES friction in EM terms was two to four times today's when the index was at 1,100 to 2,000. |
| NQ.v.0 1-minute bars 2023-04 to 2025-12 (same job with `es_symbol` NQ.v.0) | about $3 to $4 (NQ files are smaller than ES) | the SPEC's NQ replication of any ES-only result (SPEC "Robustness checks" 3) | only after a pass; required if the variant count ever passes 20 |
| ES trades 15:25 to 16:05 on every in-sample session | at least $38 at $0.0015 per minute (40 minutes x about 640 sessions); that rate is an RTH average and Databento bills by volume, so the heavier closing window would cost more, possibly more than the credit left | tick-level fills for a closing-window trade | no: SPEC rule-5 bar fills are conservative for a fixed-time entry and exit; the 110 sampled sessions give a fill check where their windows cover the close |
| ThetaData Value, one month ($40, not Databento credit) | $40 | SPX and SPXW EOD, 1-minute quotes and open interest from 2020-01-01: about 850 more sessions (2.3 times the option sample), and intraday option entries | not for study 5; it is the condition under which the option candidates below become testable. Caveat: Cboe's curb session started 2022-04-25, so EOD reports before it are stamped 16:15, not 17:00, and Gate 0's quote-time scan would have to be re-run on the new years |

## 2. Candidates and 3. scoring

Fifteen candidates: the two unrun proposals (REVIEW.md section 5's study 5; the term-structure-timed
variance premium, which was drafted in an earlier NEXT.md in commit 4146b21 rather than in REVIEW.md),
the three report-only re-checks of REVIEW.md section 3b, and ten new ones. Each has the paragraph step 2
asks for, then the six scores step 3 asks for (five from NEXT.md, plus decision frequency from PR #3),
each argued in a sentence.

Common numbers used in the scores. Friction: an ES round trip with one tick of slippage in, one tick out
and $3.98 is 0.58 points, 2.3 ticks, about 1.0 bp at an average ES level of 5,600; for the 0DTE straddle
at the curb close study 4 measured half-spread plus fees at 0.02 EM. Scatter: the 0DTE straddle's P&L has
a per-session standard deviation of 0.77 EM (study 4); the iron fly's, backed out of its interval, 0.35 EM.
Planning assumption for the ES closing window: the 15:30 to 16:00 return carries about 8.5% of the
17:00-to-16:00 variance (about 12% of RTH, RTH about 70% of the 23 hours), so its standard deviation is
about 0.36 EM, or 27 bp. That is an assumption for power only; no gate uses it.

### C1. Last-30-minute momentum into the close (REVIEW.md section 5 "study 5", revised)

Mechanism: demand that must trade in the direction of the day's move concentrates near the close.
Leveraged and inverse ETFs rebalance in proportion to the day's return, option dealers who are short
gamma hedge with the move, and closing-auction flow follows. Inputs at 15:30:00 ET on D: the ES close of
D-1 and the 15:29 bar close on D on the same contract (the rest-of-day return r_ROD), the VIX-implied
expected move from D-1's VIX close as the scale and the stop unit (one unit for every year, since option
EMs exist only from 2023-06), and, for a descriptive mechanism statistic only, the sign of the D row's net
GEX (open interest published before 09:30 D, so known at 15:30). Trade: one ES contract at the 15:30 bar's
open plus a tick, in the direction of r_ROD, out at the 16:00 bar's open minus a tick; a defined-risk
version with a stop 0.5 EM from entry. Sample: about 620 sessions (2023-06-02 to 2025-12-31 less half
days, post-half-day sessions and roll days), or about 1,690 from 2019 with the ES bar pull below, one
decision each; no gamma warm-up needed. Prior for: Gao, Han, Li and Zhou (JFE 2018; SPY 1993 to 2013),
where the first half-hour return predicts the last half-hour (slope 6.94 x 10^-2, t 4.08, R-squared 1.6%)
and the long-short timing strategy earned 6.67% a year (standard deviation 6.19%, Sharpe 1.08) against
-1.11% for always holding the last half-hour, 6.52% after costs from 2005; Baltussen, Da, Lammers and
Martens (JFE 2021; 60-plus futures 1974 to 2020),
where the rest-of-day return predicts the last 30 minutes with asset-class Sharpe ratios of 0.87 to 1.73,
significant only on negative-gamma days. Prior against: a replication of Gao et al. on SPY for 2015 to 2020
had a Sharpe of -0.63; Adams, Dim, Eraker, Fontaine, Ornthanalai and Vilkov (October 2025) find that 0DTE
positioning dampens intraday momentum; a practitioner measurement on 1,085 SPX sessions from 2022-04 to
2026-08 finds a slope of +0.006 +/- 0.009 overall and no effect using the public open-interest book (a
blog, not peer reviewed, but measured on this exact era). Why it is not a relabelling: no level, no tape,
no band trigger and no regime filter; study 3's breakout was a band-triggered trend trade opened between
10:00 and 15:00, this is a fixed-time closing-flow trade taken every session, and gamma appears only as a
descriptive slope that cannot produce a Go.

- Prior: two JFE papers for, but the most recent evidence (2025 paper, the 0DTE-era measurement, the
  2015 to 2020 replication) says the effect has faded, and this project's open-interest gamma is the
  version the 0DTE-era measurement found useless.
- Own prices: fully, with ES bars and SPEC rule-5 fills (entry at the next bar's open plus a tick, time
  exit at the exit bar's open minus a tick, stops one tick beyond); no model.
- Signal to friction: thin. Gao et al.'s Sharpe of 1.08, rescaled to this window's volatility, is about
  0.025 EM (1.8 bp) a session gross, 1.8 times the 1.0 bp (0.014 EM) of ES friction, leaving about 0.011 EM
  net. Reading their 2.65 bp directly as basis points gives 0.035 EM gross (2.6 times friction), but that
  implies a Sharpe of about 1.56 in this window and is an optimistic upper bracket. At the 0DTE-era slope
  (about 0.4 bp) the ratio is 0.4.
- Frequency: about 20 decisions a month, each independent (one 30-minute position per session, no
  overlap, consecutive closing windows nearly uncorrelated).
- Power: SE about 0.0145 EM on 620 sessions, so clearing zero needs about 0.024 EM after costs (1.0 point,
  1.8 bp) and the MDE is 0.036 EM, about 1.5 times the published effect before costs and 3.4 times after.
  Split the question in two. Existence (does the direction time the window better than chance; friction
  cancels): power 0.52 at the published size (0.79 at the upper bracket). Economics (does it pay after
  costs): 0.18 (0.43). If the 0DTE-era measurement is the truth, the 90% upper bound excludes the published
  size with probability 0.38 (0.67). With the 2019 ES bar extension and era gates on both halves
  (simulated): existence 0.76 and economics 0.08 if the published effect held throughout (gate 2 binds at
  that sample size and older years carry more friction in EM terms); if it held only before 2023-06, the
  era gate rejects existence about seven times in ten.
- Independence: high for the unconditional trade; the gamma split is the published mechanism and is
  reported as a descriptive slope only.

### C2. Medium-horizon variance premium timed by the term-structure slope (earlier NEXT.md draft, commit 4146b21)

Mechanism: short-dated SPX variance is overpriced on average, more so when the curve is in contango.
Inputs at 17:00 D-1: ATM vols at 0, 7 and 30 days from `gex_vols` (total-variance interpolation), the slope
ln(IV30 / IV0) against its trailing 252-session median. Trade: short the SPXW straddle nearest 7 and nearest
30 days at the bids when the slope is above its median, held to PM settlement; iron fly as defined risk.
Sample: an entry every session, but about 128 non-overlapping weekly and about 30 non-overlapping monthly
positions in the whole sample, half of each in regime. Prior for: the variance premium (Carr and Wu 2009;
Bollerslev, Tauchen and Zhou 2009) and its concentration at short maturities (Dew-Becker, Giglio, Le and
Rodriguez, JFE 2017); slope timing (Johnson, JFQA 2017, 1996 to 2013: a low slope predicts higher returns to
long variance, so contango favours sellers). Prior against: Miller and Li (May 2026; one-week SPX options
2010 to 2019) find short ATM straddles insignificantly different from zero after spreads and margin
(5%-out-of-the-money strangles earned about 0.52% a week), and both negative in 2020 to 2021; study 4 put
the one-day premium at -0.002 EM. Not a relabelling: no gamma, a new horizon.

- Prior: strong for a premium somewhere on the short end, weak for the ATM straddle specifically.
- Own prices: yes for SPXW (bid in, official settlement); the SPX AM monthlies cannot be settled from
  disk, so M30 must use SPXW.
- Signal to friction: favourable; a weekly ATM straddle is about 2.2 EM, so the curb half-spread is a few
  percent of premium against a premium of 5 to 15% if it exists.
- Frequency: about 4 independent weekly decisions a month and 1 monthly, halved by the regime filter.
- Power: in regime, the weekly MDE is about 0.26 of premium and the monthly about 0.55; the draft's own
  gate of 150 non-overlapping positions in regime cannot be met (about 64 weekly, 15 monthly).
- Independence: high.

### C3. Re-check: dealer sign convention (REVIEW.md 3b.1), report only

Mechanism: the calls-plus, puts-minus convention may mis-sign dealer gamma, especially on 0DTE strikes.
Inputs: the same quotes and OI. Output: Stage 1 re-estimated under alternative sign definitions; no trade.
Sample: 510 sessions. Note from reading the proposal against the formula: its three alternatives are not
three. "All contracts long gamma for dealers" is the unsigned dollar total, sum |GEX_i|; "calls and puts
both negative" is its exact negative, whose percentile is one minus the first, so its beta is the same
number with the sign flipped; "OI-weighted absolute gamma with no sign" is the first without the daily
100 S0^2 0.01 dollar scaling, i.e. the first normalised as in C5. The real comparison is the convention
against unsigned total gamma, in dollars or in index units; unsigned gamma mostly measures how much open
interest sits near the money and trends with OI growth.

- Prior: the convention is an assumption (REVIEW 3b.1 cites the critiques); the 0DTE-only percentile was not
  significant in Stage 1, which is what mis-signing would look like.
- Own prices: not a trade.
- Signal to friction: not applicable.
- Frequency: not applicable.
- Power: two regressions on 510 sessions; informative only if the betas differ by more than about one
  standard error.
- Independence: none; it describes the closed regime variable.

### C4. Re-check: business-time clock (REVIEW.md 3b.2), report only

Mechanism: weighting overnight minutes at about a third would re-weight 0DTE and one-day gamma.
Output: gex rebuilt with a business-time T_q and T_o, Stage 1 restated; no trade. Sample 510.

- Prior: a modelling improvement, not a prediction; it changes the gamma profile, not EM (a quoted price).
- Own prices: not a trade.
- Signal to friction: not applicable.
- Frequency: not applicable.
- Power: as C3.
- Independence: none.

### C5. Re-check: S0-squared-normalised percentile and year dummies (REVIEW.md 3b.3), report only

Mechanism: dollar GEX scales with S0 squared (about 2.5 times over the sample), so the trailing percentile
partly ranks the index level. Output: the percentile of net_gex / S0^2 and Stage 1 with year dummies.

- Prior: the most likely of the three to change the WRITEUP's description of the regime, since a trend
  in the regressor can masquerade as an effect.
- Own prices: not a trade.
- Signal to friction: not applicable.
- Frequency: not applicable.
- Power: as C3.
- Independence: none. The sign of net_gex, which C1 uses as its mechanism split, is unaffected by this
  normalisation (dividing by a positive number keeps the sign).

### C6. One-week variance premium, unconditional, split into body and wings

Mechanism: as C2 without timing, and with the short straddle decomposed into a short iron fly (the body
within one weekly expected move) plus a short strangle (the wings), because the short-dated literature puts
the premium in the left tail (Andersen, Fusari and Todorov, JF 2017; Bondarenko on puts). Inputs: the D-1
17:00 chain for the SPXW expiring five sessions ahead. Trades: short ATM straddle, iron fly with wings at
the strikes nearest K +/- one weekly EM, and the strangle, all at bid or ask to settlement; a daily delta
hedge with ES at 17:00 is possible for the straddle. Sample: about 600 overlapping entries, about 128
non-overlapping. Prior: as C2 (Miller and Li find the ATM straddle flat over ten years and the strangle
positive; their 2020 to 2021 holdout was negative for both). Not a relabelling: new horizon; study 4's
one-day premium becomes the baseline.

- Prior: moderate for the wings, weak for the ATM body; tail-dominated either way.
- Own prices: yes; the delta-hedged version adds a model hedge ratio (Black-76 at the 17:00 surface),
  though every fill is still a real price.
- Signal to friction: favourable for the straddle and fly (premium in the tens of points against a
  half-spread of a point or two); the strangle's small premium makes its spread proportionally larger.
- Frequency: about 4 to 6 effective independent decisions a month (overlapping daily entries recover about
  half again over the non-overlapping count).
- Power: in sample, the straddle's MDE is 0.19 of premium on the non-overlapping ladder, 0.16 using every
  overlapping entry and 0.13 delta-hedged (a simulation with GARCH and fat tails, scaled to study 4's
  measured one-day scatter), and the fly's is 0.065 weekly EM; with the 2020 to 2025 extension (ThetaData
  Value, $40) the overlapping straddle, hedged straddle and fly fall to 0.10, 0.085 and 0.041.
- Independence: high.

### C7. 0DTE straddle timed by implied versus realised variance

Mechanism: when the 0DTE implied variance is high relative to recent realised variance, sellers are paid
more than the day delivers; when it is low, realised catches up. Inputs at 17:00 D-1: EM-implied
variance against trailing 5- and 20-session realised variance (FRED closes, or ES 5-minute returns up to
17:00). Trade: study 4's short straddle in the top half, long in the bottom half. Sample: about 630
sessions. Prior for: Goyal and Saretto (JFE 2009) in the single-stock cross-section; Johnson (2017) ranks
implied-minus-expected variance among the strongest variance-premium predictors. No direct evidence found
for the index 0DTE time series. Not a relabelling: a new conditioning variable on study 4's expression,
which NEXT.md allows.

- Prior: good in the cross-section of stocks, untested for 0DTE index options, where pricing is far more
  efficient.
- Own prices: yes (study 4's machinery).
- Signal to friction: friction 0.02 EM against whatever contrast exists; study 4's best contrast was 0.13 EM.
- Frequency: about 20 decisions a month, independent (one-session holds).
- Power: the half-minus-half contrast has an MDE of 0.15 EM, slightly above study 4's 0.13; the most
  likely outcome is study 4's again: right sign, about 1.2 standard errors.
- Independence: moderate; same expression as a closed study with a new variable.

### C8. Non-trading-period (weekend) 0DTE straddle

Mechanism: options decay in calendar time while variance accrues mostly in trading time; a
Friday-close price for the Monday expiry that charges too much weekend variance would pay sellers.
Inputs: the Friday 17:00 report for the Monday SPXW. Trade: study 4's short straddle on sessions whose
hold spans non-trading days. Sample: about 125 Mondays plus about 10 post-holiday sessions. Prior for:
Jones and Shemesh (JF 2018): equity option returns average -0.62% from Friday to Monday. Prior against:
the same paper finds little evidence of a weekend effect in S&P 500 index options. Not a relabelling.

- Prior: weak for SPX specifically, by the paper that documents the effect.
- Own prices: yes, and nearly free (study 4's table already has every Monday; it would need a fresh
  pre-registration because that table has been examined).
- Signal to friction: favourable if a large effect existed.
- Frequency: about 4 decisions a month.
- Power: MDE 0.17 EM; only a gross mispricing would show.
- Independence: moderate (study 4's expression).

### C9. Settlement-day pinning at the largest open-interest strike

Mechanism: dealers long gamma at a heavy strike sell above it and buy below it, pulling the close toward
it (Ni, Pearson and Poteshman 2005 for stocks; Golez and Jackwerth, JFE 2012, for S&P 500 futures on
expiration days). Inputs: at a 17:00 D-1 entry the newest open interest is the morning-of-D-1 file, so the
pin strike is one session stale. Trade: a 0DTE butterfly centred on that strike against one centred at the
money as the placebo. Sample: about 630. Prior against: Stage 2 found gamma-tagged prices hold no better
than random prices (45.4% against 46.5%); the 0DTE positions that could pin are opened intraday and never
reach open interest; Golez and Jackwerth's sample predates daily expiries.

- Prior: weak here.
- Own prices: yes, but four legs at the curb close.
- Signal to friction: poor; a butterfly's spread is a large share of its cost.
- Frequency: about 20 a month.
- Power: low; a butterfly loses its debit on most days and its P&L scatter relative to cost is well above
  the straddle's.
- Independence: low; "the gamma strike attracts price" is a restatement of the closed level thesis.

### C10. Day-over-day open-interest change as a flow proxy

Mechanism: open interest built on D-1 in near-dated options reveals hedging or speculative demand that
moves D. Inputs: OI(D) - OI(D-1) by moneyness and expiry, published before 09:30 D. Trade: ES from 09:30 to
16:00 in the indicated direction (options could only be entered at the 17:00 D quote, a session late).
Sample: about 640. Prior: signed open-buy volume predicts returns (Pan and Poteshman, RFS 2006), but
unsigned OI cannot separate buyers from writers, and same-day 0DTE flow never appears in it; I found no
evidence for unsigned index OI.

- Prior: weak.
- Own prices: yes (ES bars).
- Signal to friction: unknown and probably small; the RTH return's scatter is about 1.05 EM.
- Frequency: about 20 a month.
- Power: MDE about 0.10 EM (about 8 bp) on a session-long hold.
- Independence: low; dealer positioning by another name.

### C11. Overnight drift (overnight versus day session)

Mechanism: compensation for absorbing end-of-day order imbalances, realised around the European open.
Inputs: ES bars. Trade: long ES from 02:00 to 03:00 ET, or 16:00 to 09:30, optionally after down days.
Sample: about 640 nights. Prior for: Boyarchenko, Larsen and Whelan (RFS 2023): from 1998 to 2020 the 02:00
to 03:00 window earned about 3.7% a year, more than 60% of the E-mini's 5.9% close-to-close return. Prior
against: the New York Fed's Liberty Street Economics (July 2026) reports the window has averaged close to
zero since January 2021, attributed to a halving of closing-imbalance dispersion; the NightShares overnight
ETFs closed fourteen months after launch.

- Prior: strong historically, gone in the sample period.
- Own prices: yes.
- Signal to friction: the historical 1.5 bp a night against 1.0 bp of friction was thin even before it
  disappeared.
- Frequency: about 20 a month.
- Power: MDE about 1.0 bp net on the one-hour window; the historical size does not clear it after costs.
- Independence: high.

### C12. Index serial dependence (variance ratios at longer lags, daily and weekly reversal)

Mechanism: index arbitrage and ETF creation make index returns mean-revert at short lags. Inputs: ES daily
closes on one contract (rolls skipped). Trade: fade the prior session's or week's return. Prior:
Baltussen, van Bekkum and Da (JFE 2019): the S&P 500's one-week autocorrelation is -0.087 after index
futures, the cross-index one-day figure -0.005, and the MAC(5) strategy's Sharpe about 0.4.

- Prior: moderate at the weekly lag, near zero at the daily.
- Own prices: yes.
- Signal to friction: fine per trade (friction is 1 bp against a daily scatter of about 94 bp), but the
  edge is tiny relative to the scatter.
- Frequency: about 20 a month (daily) or 4 (weekly).
- Power: at a Sharpe of 0.4 the daily version needs about 10,000 sessions; this sample has 640.
- Independence: high.

### C13. Put-skew steepness and next-period variance or return

Mechanism: a steep put skew prices left-tail risk, and the tail premium predicts returns (Bollerslev,
Todorov and Xu, JFE 2015; Kozhan, Neuberger and Schneider, RFS 2013). Inputs: `gex_vols` put vol at a fixed
moneyness minus ATM at 7 and 30 days. Trade: a risk reversal or put spread at bid and ask, or skew as a
conditioning variable for C6.

- Prior: documented at monthly to quarterly horizons, weak at weekly.
- Own prices: yes, with four legs.
- Signal to friction: OTM spreads at the curb are proportionally wide.
- Frequency: about 1 independent decision a month at the documented horizon.
- Power: about 30 independent observations; hopeless.
- Independence: high.

### C14. Opening-gap fade

Mechanism: moves made overnight on thin liquidity partly reverse when the cash session opens. Inputs: ES
16:00 close on D-1 and the 09:30 open on D. Trade: fade the gap from 09:30 to 10:30 with a stop. Sample:
about 640. Prior: Grant, Wolf and Yu (JBF 2005) study intraday reversals after large opening moves in S&P
500 futures over fifteen years; I could not read the abstract from here and recall, without having
verified it, that the reversals did not survive costs; otherwise practitioner lore.

- Prior: weak and unverified.
- Own prices: yes.
- Signal to friction: unknown.
- Frequency: about 20 a month.
- Power: similar to C1 (MDE about 0.04 EM).
- Independence: moderate; the prior close was a structural level in studies 1 and 2, and touched-price
  mean reversion was measured there at 0.6 ticks.

### C15. Event-day 0DTE straddle (FOMC, CPI, NFP)

Mechanism: the implied move into a scheduled announcement against the realised move. Inputs: the 17:00
report on the eve of each event; `static/events.csv` holds FOMC days only (README note 12). Sample: about
80 sessions. Prior: the announcement-day equity premium (Savor and Wilson 2013); option-side evidence mixed.

- Prior: mixed.
- Own prices: yes.
- Signal to friction: favourable if an effect exists.
- Frequency: about 3 a month.
- Power: MDE 0.21 EM.
- Independence: moderate (study 4's expression).

## Ranking and recommendation

| rank | candidate | run? | the deciding reason |
|---|---|---|---|
| 1 | C1 last-30-minute momentum | yes | closest to resolvable of anything with a published prior: its MDE is about 1.5 times the published effect before costs (3.4 after), while the option candidates either need several times the recent estimates (C6) or have no published effect for this market at all (C7); zero spend; about 20 independent decisions a month. Even so, its existence half is about a coin flip at the published size |
| 2 | C6 one-week premium, body and wings | not now | in sample the MDE (0.13 to 0.19 of premium) is above every recent estimate for the ATM straddle, so the likely outcome is study 4's: positive and unresolved; becomes testable with the $40 ThetaData Value extension (fly MDE 0.041) |
| 3 | C7 0DTE timed by IV/RV | no | contrast MDE 0.15 EM against study 4's best contrast of 0.13: built to repeat study 4's failure mode |
| 4 | C14 opening-gap fade | no | powered like C1 but with no verified prior, and adjacent to the closed level thesis |
| 5 | C8 weekend straddle | no | the paper that documents the effect finds little of it in S&P 500 options; MDE 0.17 EM |
| 6 | C15 event days | no | 80 sessions, MDE 0.21 EM |
| 7 | C2 slope-timed premium (draft) | no | its own sample-size gate cannot be met; the timing halves an already thin sample. The slope survives as a reported contrast in C6 |
| 8 | C11 overnight drift | no | the New York Fed reports it has averaged near zero since 2021 |
| 9 | C12 serial dependence | no | needs about fifteen times the sample |
| 10 | C13 skew | no | about 30 independent observations |
| 11 | C10 OI change | no | unsigned, stale for options, dealer positioning by another name |
| 12 | C9 pinning | no | restates the closed level thesis with a stale strike |
| - | C3, C4, C5 re-checks | not now | not trades; they matter only if a future study conditions on gamma. C5 is the one worth doing for the record (the percentile may partly rank the index level) and costs an afternoon; of C3's three definitions, two are mirror images and the third is the unsigned measure divided by S0^2 |

No candidate on the data on disk can resolve its published effect after costs. That is the main finding
of this exercise, and it is a property of 2.5 years of daily decisions rather than of any one idea. The
option-premium candidates have a per-decision scatter of about 0.8 of premium and need 0.13 to 0.19 of
premium to clear a gate, while the recent evidence puts the ATM premium near zero at one day (study 4) and
one week (Miller and Li); they would most likely repeat study 4's result, a positive estimate inside an
interval that includes zero. The ES-path candidates have tiny scatter per decision but published effects
that are one to three times their friction.

Why C1 first anyway, given NEXT.md's rule to prefer a clean answer either way over upside. It is the
nearest to clean, though not clean. Before costs its MDE is about 1.5 times the published effect, so the
existence question (does the rest-of-day direction still time the close better than chance?) has about even
odds (0.52) of detecting the published size in sample, and if the 0DTE-era measurement is right the upper
bound excludes the published size with probability 0.38. The economic question cannot be settled at the
published size on any sample on offer: Gao et al.'s effect keeps less than half of itself after ES friction
(about 0.011 EM net of 0.025 gross), so after-cost power is about 0.18 in sample and lower with the 2019
extension. The pre-registration therefore reports existence and economics separately, so "it exists but
does not pay" is a complete answer rather than an ambiguous kill. C1 also has the most independent decisions
of anything with a published prior, which is what PR #3 asks to prefer, and it costs nothing.

The honest expectation is a KILL, most likely an uninformative one in the narrow sense: a point estimate near
zero whose interval does not quite exclude the published size. The 2025 paper and the 0DTE-era measurement
both say the effect is gone, and this project's open-interest gamma is the version that measurement found
useless. What a run would add is this project's own measured bound under its own fill rules, on the last
unrun expression of the founding mechanism (dealer and rebalancing flow moving the index), for an
afternoon of work and no spend. The 2019 extension ($5.4 plus a small code change to freeze the stage-3 day
set) adds existence power (0.76 if the effect is constant) and a decay pattern, not economics power.

The alternatives are real and Matteo's to weigh: C6 with the 2020-to-2025 extension ($40 outside the
Databento credit, a quote-time re-check for the pre-curb years, a fly MDE of 0.041 weekly EM that resolves a
modest body premium, a straddle MDE of 0.10 that still does not), or writing the programme up here on the
finding above, that no candidate on this data can resolve its published effect after costs.

## Sources

- Gao, Han, Li, Zhou, "Market intraday momentum", JFE 2018:
  [working paper](https://c.mql5.com/forextsd/forum/173/intraday_momentum_-_the_first_half-hour_return_predicts_the_last_half-hour_return.pdf)
  (timing strategy, Table 4; costs, Table 10); summaries at
  [CXO Advisory](https://cxoadvisory.com/calendar-effects/first-and-last-half-hours-of-trading-linked) (whose
  6.3% / -0.5% figures describe a long-or-cash version) and
  [Harbourfront Quant](https://harbourfrontquant.substack.com/p/does-intraday-momentum-exist-in-stock)
- Replication of Gao et al. 2015-2020: [QuantConnect](https://www.quantconnect.com/research/15348/intraday-etf-momentum/)
- Baltussen, Da, Lammers, Martens, "Hedging demand and market intraday momentum", JFE 2021:
  [Alpha Architect summary](https://alphaarchitect.com/hot-topic-does-gamma-hedging-actually-affect-stock-prices/),
  [EUR repository](https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/)
- 0DTE-era measurement, 1,085 SPX sessions: [firmtape on DEV](https://dev.to/firmtape/intraday-momentum-is-dead-in-the-0dte-era-we-measured-it-on-1085-spx-sessions-43g0)
- Adams, Dim, Eraker, Fontaine, Ornthanalai, Vilkov, "Do S&P500 options increase market volatility? Evidence
  from 0DTEs", 2025: [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974)
- Boyarchenko, Larsen, Whelan, "The overnight drift": [NY Fed staff report](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr917.pdf);
  "The disappearing overnight drift", [Liberty Street Economics, July 2026](https://libertystreeteconomics.newyorkfed.org/2026/07/the-disappearing-overnight-drift/)
- Baltussen, van Bekkum, Da, "Indexing and stock market serial dependence around the world", JFE 2019:
  [CXO Advisory](https://www.cxoadvisory.com/?p=28672)
- Johnson, "Risk premia and the VIX term structure", JFQA 2017:
  [CXO Advisory](https://www.cxoadvisory.com/volatility-effects/vix-term-structure-slope-and-variance-asset-future-returns/)
- Dew-Becker, Giglio, Le, Rodriguez, "The price of variance risk", JFE 2017, and its term-structure summary:
  [Review of Finance blog](https://revfin.org/the-term-structure-of-the-price-of-variance-risk/)
- Andersen, Fusari, Todorov, weekly options: [NBER w21491](https://www.nber.org/papers/w21491)
- Miller, Li, "Strangling time decay profits from short-dated index options", May 2026:
  [UNLV](https://oasis.library.unlv.edu/gaming_institute/2026/May28/30)
- Jones, Shemesh, "Option mispricing around nontrading periods", JF 2018: [CXO Advisory](https://www.cxoadvisory.com/?p=5800)
- Vilkov, "0DTE trading rules": [Hull Tactical summary](https://hulltactical.com/2026/07/08/0dte-options-tiny-edge-huge-distribution/)
- Rhoads, one-day SPX ATM straddles 2022-2023: [Substack](https://russellrhoads.substack.com/p/digging-into-1-day-spx-at-the-money)
- Golez, Jackwerth, "Pinning in the S&P 500 futures", JFE 2012: [RePEc](https://ideas.repec.org/a/eee/jfinec/v106y2012i3p566-585.html)
- Grant, Wolf, Yu, "Intraday price reversals in the US stock index futures market", JBF 2005:
  [RePEc](https://ideas.repec.org/a/eee/jbfina/v29y2005i5p1311-1327.html)
- ThetaData tiers and history: [docs](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html),
  [pricing](https://thetadata.net/pricing); Cboe curb session from 2022-04-25:
  [Benzinga](https://benzinga.com/news/23/02/30897312/cboe-extends-options-trading-hours-to-fit-traders-of-all-time-zones)
