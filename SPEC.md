# Gamma + Order Flow Edge Test: MVP Build Spec

Oct 4, 2026 · @Matteo

## Scope and stage gates

The MVP answers one question for roughly $0 in data: does the gamma regime, a gamma level, or order flow confirmation at a level produce positive expectancy on ES after costs? It is batch research code only. Nothing runs live, and nothing is built for phase two until the gates below are passed.

Each stage has a pass rule and a kill rule, written here before any data is touched. The thresholds are suggested defaults; change them now if you disagree, then freeze them.

| Gate | Question | Pass | Kill |
| --- | --- | --- | --- |
| Stage 1: Regime | Does GEX predict intraday range and mean reversion beyond what VIX already explains? | GEX coefficient significant at p < 0.05 with VIX in the model, and top vs bottom GEX quintile range ratio differs by 15% or more | Not significant, or effect under 15%: drop the gamma framing, keep only the order flow project |
| Stage 2: Levels | Do gamma levels hold more often than matched control levels? | Gamma tag coefficient positive at p < 0.10 with day-clustered errors, ideally stronger in positive gamma | Not a kill: carry structural levels only into Stage 3 |
| Stage 3: Flow | Does absorption plus reclaim confirmation give positive expectancy after costs, above unconfirmed touches? | Confirmed expectancy at least +0.10R after costs, 90% day-bootstrap interval above 0, and above the unconfirmed baseline | Expectancy at or below 0, or indistinguishable from baseline: stop, no phase-two build |
| Holdout | Does the frozen rule survive data never looked at? | Same sign and at least half the in-sample expectancy, with parameter nudges not flipping the sign | Fails: stop, or restart from Stage 0 with a new hypothesis |

Out of scope for the MVP: real-time data, order book reconstruction, iceberg detection, dashboards, execution, and NQ as a primary market.

## Stack and project layout

Python 3.11+ on your own machine, Parquet files on disk, and Jupyter for analysis. No database server, cloud, or UI.

| Package | Used for |
| --- | --- |
| pandas, pyarrow | Tables and Parquet storage |
| numpy, scipy | Black-76 pricing, implied vol root-finding, normal CDF/PDF |
| statsmodels | OLS with Newey-West errors, logistic regression with clustered errors |
| databento | Official client for ES bars and trades |
| requests | Calls to the ThetaData Terminal running locally |
| pyyaml | Loading the frozen parameter file |
| matplotlib, jupyter | Inspection and notebooks |

```
gamma-edge/
  config.yaml              # every parameter from the registry section, frozen before runs
  RUNLOG.md                # every variant tried: date, change, result
  data/raw/options/        # ThetaData EOD quotes + open interest, one file per expiration
  data/raw/es/bars/        # Databento ohlcv-1m, one file per month
  data/raw/es/trades/      # Databento trades, one file per touch window
  data/raw/daily/          # SPX close, VIX close
  data/derived/            # gex_daily, gex_strikes, levels, touches, features, trades
  src/ingest_options.py
  src/ingest_futures.py
  src/ingest_daily.py
  src/gex.py
  src/levels.py
  src/touches.py
  src/flow.py
  src/sim.py
  src/stats.py
  src/calendar.py          # sessions, holidays, in_sample(date) holdout filter
  notebooks/01_regime.ipynb
  notebooks/02_levels.ipynb
  notebooks/03_flow.ipynb
  notebooks/04_holdout.ipynb
```

Two rules apply everywhere. Store timestamps in UTC and convert to America/New\_York only for session logic. Every query goes through one function, in\_sample(date), so holdout days can never leak into research by accident.

## Data providers and APIs

Total data cost for the MVP can be $0. Databento's $125 free credit covers SPX open interest and all ES data, and ThetaData's free tier covers the option quotes. If the credit runs short, the fallback is one month of ThetaData's Value tier, which also extends history back to 2020.

| Provider | Data | Tier and cost | History | Used in |
| --- | --- | --- | --- | --- |
| [Databento OPRA](https://databento.com/docs/examples/options/equity-open-interest) | SPX and SPXW start-of-day open interest (statistics schema) | Pay per GB from the $125 free credit | From 2023-03-28 | GEX engine |
| [ThetaData](https://thetadata.net/docs/operations/option_history_eod.html) | SPX and SPXW end-of-day quotes (closing NBBO, volume) | Free | From 2023-06-01 | GEX engine (implied vols), expected move |
| [Databento CME](https://databento.com/pricing) | ES 1-minute bars | Same credit | 15+ years | Stage 1, Stage 2, basis |
| Databento CME | ES trades with aggressor side, only around touches | Same credit | 15+ years | Stage 3 |
| FRED | SP500 and VIXCLS daily closes | Free CSV, no key | Full | Basis, VIX control |
| Fallback: [ThetaData Value](https://thetadata.net/docs/Articles/Getting-Started/Subscriptions.html) | Open interest and quotes | One month's subscription | From 2020-01-01 | Replaces the OPRA row if the credit runs short |

### Exact calls

ThetaData runs through the Theta Terminal on your machine, and on the free tier only the end-of-day call is needed. Request SPXW (PM-settled weeklies and dailies) and SPX (AM-settled monthlies), one date per call, capped at 45 days to expiry, and pace requests to stay under the free tier's rate limit.

```
GET http://127.0.0.1:25503/v3/option/history/eod?symbol=SPXW&expiration=*&start_date=YYYYMMDD&end_date=YYYYMMDD&max_dte=45&format=csv
(repeat with symbol=SPX)
```

Databento uses the official Python client. Request OPRA statistics from midnight to 09:30 ET each day, so only the start-of-day open interest is billed. Skip the definition schema: OPRA symbols follow OCC (OSI) format, so expiration, call or put, and strike parse straight from the symbol. Run get\_cost with the same arguments before every pull.

```python
import datetime as dt
from zoneinfo import ZoneInfo
import databento as db
import pandas as pd

client = db.Historical("YOUR_API_KEY")
ET = ZoneInfo("America/New_York")

def oi_args(day):  # start-of-day open interest for SPX + SPXW
    return dict(dataset="OPRA.PILLAR", symbols=["SPX.OPT", "SPXW.OPT"],
                stype_in="parent", schema="statistics",
                start=dt.datetime.combine(day, dt.time(0, 0), ET),
                end=dt.datetime.combine(day, dt.time(9, 30), ET))

day = dt.date(2023, 6, 1)
print(client.metadata.get_cost(**oi_args(day)))   # price one day, multiply by session count
stats = client.timeseries.get_range(**oi_args(day)).to_df()
oi = (stats[stats["stat_type"] == db.StatType.OPEN_INTEREST]
      .drop_duplicates("symbol", keep="last")[["symbol", "quantity"]]
      .rename(columns={"quantity": "open_interest"}))

# OSI symbol, e.g. "SPXW  230601C04200000": root, YYMMDD, C/P, strike x 1000
osi = oi["symbol"].str.replace(" ", "", regex=False)
parts = osi.str.extract(r"^(?P<root>[A-Z]+)(?P<exp>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")
oi = oi.join(parts)
oi["strike"] = oi["strike"].astype(int) / 1000
oi["expiration"] = pd.to_datetime(oi["exp"], format="%y%m%d")
# then keep expirations within 45 days

# ES bars for Stages 1-2
es = dict(dataset="GLBX.MDP3", symbols="ES.v.0", stype_in="continuous",
          schema="ohlcv-1m", start="2023-06-01", end="2023-07-01")
print(client.metadata.get_cost(**es))
bars = client.timeseries.get_range(**es).to_df()
# Stage 3: same call with schema="trades" and start/end set to each touch window
```

FRED needs no key: https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500 and ?id=VIXCLS.

### Point-in-time rules

1. OPRA publishes start-of-day open interest before the 09:30 ET open, reflecting the previous session's close, so it is known before the open of D. On the fallback, ThetaData's open interest dated D works the same way (published around 06:30 ET).
2. The ThetaData EOD report for D-1 is generated at 17:15 ET and carries the last NBBO at that time; use it as the prior-close quote for session D.
3. Session D therefore uses: quotes from D-1, open interest published the morning of D, and SPX and ES closes from D-1. Nothing from D's trading enters the levels.
4. In Databento trades, side A is a sell aggressor, B is a buy aggressor, and N is unknown; drop N.
5. ES.v.0 maps to the highest-volume contract each day and is not back-adjusted. Never let a touch window, basis, or bar series span a roll date.

### Credit budget

Price everything in Week 1, before downloading anything at scale.

1. OPRA open interest: get\_cost for one day, multiplied by the number of sessions from June 2023 to now.
2. ES 1-minute bars for the same span; these should be a small fraction of the total.
3. Stage 3 trade windows: price 20 sample windows and multiply by the expected touch count, then hold that amount in reserve.
4. If the three together exceed $125, switch open interest and quotes to the ThetaData Value fallback and keep the Databento credit for ES only.

On day one, confirm SPX and SPXW both come back from each source.

## Module 1: Data ingestion

Four batch jobs pull raw data once and save it as Parquet; everything downstream reads only from disk. Each job is idempotent: it skips files that already exist, so a crash just means rerunning it.

| Job | Loop | Output | Run time |
| --- | --- | --- | --- |
| ingest\_options.py | Each trading day from 2023-06-01: ThetaData EOD quotes for SPXW and SPX, then one Databento OPRA open interest call | data/raw/options/{eod,oi}/YYYYMMDD\_SYMBOL.parquet | A few hours, overnight |
| ingest\_futures.py bars | Each month, ES.v.0, ohlcv-1m, full Globex session | data/raw/es/bars/YYYY-MM.parquet | Minutes |
| ingest\_futures.py trades | Each touch from Stage 2, window from t0 minus 10 min to t0 plus 30 min | data/raw/es/trades/TOUCHID.parquet | Run only after Stage 2 |
| ingest\_daily.py | Two FRED CSVs | data/raw/daily/spx\_vix.parquet | Seconds |

### Storage schemas

| Table | Columns |
| --- | --- |
| options\_eod | quote\_date, symbol, expiration, strike, right, bid, ask, close, volume |
| options\_oi | as\_of\_date (open of this day), symbol, expiration, strike, right, open\_interest |
| es\_bars | ts\_open\_utc, open, high, low, close, volume, instrument\_id |
| es\_trades | ts\_event\_utc, price, size, side (+1 buy, -1 sell), instrument\_id |
| daily | date, spx\_close, vix\_close |

### Cleaning rules

1. Drop option quotes with bid at or below 0, ask below bid, or (ask - bid) / mid above 0.5. Keep their open interest; they get an interpolated vol later.
2. Drop open interest rows of 0.
3. Drop ES bars outside 18:00 ET (prior day) to 17:00 ET, and flag roll days where instrument\_id changes.
4. Build a trading calendar from the ES bars and use it for every "previous day" lookup, so holidays and half days are handled once.

## Module 2: GEX engine

The GEX engine turns prior-close quotes and morning open interest into one row per session: net GEX, its percentile, the flip, the walls, the top strikes, the expected move, and the ES basis. Every later stage reads this table, so validate it before moving on.

### Step 1: Time to expiry

Use two clocks per contract, measured in minutes and divided by 525,600. T\_q runs from the quote time (16:15 ET on D-1) to expiry, and is used to back out implied vol. T\_o runs from 09:30 ET on D to expiry, and is used to evaluate gamma for the session. SPXW expires at 16:00 ET; SPX monthlies settle at the open, so drop any SPX contract on its expiry day.

### Step 2: Forward and discount factor per expiry

For strikes within 2% of the prior SPX close that have valid call and put quotes, regress the call-minus-put mid on strike. The slope gives the discount factor and the intercept gives the forward, with no rate or dividend inputs needed.

```latex
C_{mid}(K) - P_{mid}(K) = D\,F - D\,K \quad\Rightarrow\quad D = -\text{slope}, \qquad F = \frac{\text{intercept}}{D}
```

### Step 3: Implied volatility (Black-76)

Solve each out-of-the-money option for sigma with Brent's method on \[0.01, 5\], using calls for K at or above F and puts below. Give the call and put at the same strike that one vol. Strikes without a valid quote get a vol linearly interpolated across strike within the same expiry, flat beyond the ends.

```latex
C = D\,[F\,N(d_1) - K\,N(d_2)], \qquad P = D\,[K\,N(-d_2) - F\,N(-d_1)]
```

```latex
d_1 = \frac{\ln(F/K) + \tfrac{1}{2}\sigma^2 T_q}{\sigma\sqrt{T_q}}, \qquad d_2 = d_1 - \sigma\sqrt{T_q}
```

### Step 4: Gamma at the open, then dollar GEX

Set the reference price S\_0 to the forward of the nearest expiry. Evaluate gamma at S\_0 with each contract's vol and T\_o (sticky strike). Discounting is ignored; at under 45 days its effect is under 1%.

```latex
\Gamma_i(S) = \frac{\varphi(d_1)}{S\,\sigma_i\sqrt{T_{o,i}}}, \qquad d_1 = \frac{\ln(S/K_i) + \tfrac{1}{2}\sigma_i^2 T_{o,i}}{\sigma_i\sqrt{T_{o,i}}}
```

Dollar GEX is the dollar change in dealer delta for a 1% move. The sign convention assumes dealers are long calls and short puts: calls count +1, puts count -1.

```latex
\text{GEX}_i(S) = s_i \cdot \Gamma_i(S) \cdot OI_i \cdot 100 \cdot S^2 \cdot 0.01, \qquad s_i = \begin{cases} +1 & \text{call} \\ -1 & \text{put} \end{cases}
```

```latex
\text{NetGEX}(S) = \sum_i \text{GEX}_i(S), \qquad \text{NetGEX}_{0DTE}(S) = \sum_{i:\,\text{exp}_i = D} \text{GEX}_i(S)
```

### Step 5: Strike profile, walls, and flip

```latex
G(K) = \sum_{i:\,K_i = K} \text{GEX}_i(S_0)
```

1. Call wall: the strike with the largest total call GEX.
2. Put wall: the strike with the largest absolute total put GEX.
3. Top strikes: the three strikes with the largest absolute G(K) within one expected move of S\_0.
4. Flip: recompute NetGEX(S) on a grid from 0.95 S\_0 to 1.05 S\_0 in 5-point steps, holding vols and T\_o fixed. The flip is the zero crossing nearest S\_0, linearly interpolated. No crossing means flip is blank for that day.

### Step 6: Expected move, percentile, and basis

The expected move is the prior-close at-the-money straddle on the nearest expiry on or after D, at the strike closest to F.

```latex
EM_D = C_{mid}(K_{atm}) + P_{mid}(K_{atm})
```

```latex
\text{GEXpct}_D = \text{percentile rank of } \text{NetGEX}_D \text{ among the previous 252 sessions}
```

```latex
B_D = ES_{16:00}(D-1) - SPX_{close}(D-1), \qquad L_{ES} = L_{SPX} + B_D
```

Use the ES contract that is front month on D for both sides of the basis.

### Output: gex\_daily

date, s0, em, net\_gex, net\_gex\_0dte, gex\_pct, flip, call\_wall, put\_wall, top1, top2, top3, basis. All levels are stored in SPX points and converted to ES points in Module 3.

### Validation before moving on

1. The forward F for the nearest expiry sits within a few points of the SPX close on 95% of days.
2. The vol smile per expiry is smooth, with put vols above call vols.
3. On 10 spot-check days, your net GEX sign and walls broadly match any public GEX chart for those dates.
4. EM divided by S\_0 tracks VIX divided by the square root of 252 over time.

## Module 3: Level builder

Each session gets one table of candidate ES levels, every level tagged with every reason it exists. Tags, not a single label, are what let Stage 2 separate a gamma effect from a round-number effect.

| Tag | Source | Definition |
| --- | --- | --- |
| gamma\_flip | gex\_daily | Flip level, if one exists |
| gamma\_call\_wall | gex\_daily | Call wall |
| gamma\_put\_wall | gex\_daily | Put wall |
| gamma\_top | gex\_daily | Each of the top three strikes |
| pd\_high, pd\_low | ES bars | Prior RTH session high and low (09:30 to 16:00 ET) |
| on\_high, on\_low | ES bars | Overnight high and low (18:00 ET on D-1 to 09:30 ET on D) |
| round | Arithmetic | Every SPX multiple of 50 within one expected move of S\_0 |

### Build rules

1. Convert gamma and round levels from SPX to ES by adding B\_D, then round to the nearest 0.25 tick.
2. Keep only levels within one expected move of the ES price at 09:30 ET.
3. Merge any levels within 2 ticks (0.5 points) of each other into one level at their mean price, keeping the union of tags.
4. Add a derived flag: is\_gamma is true if any gamma tag is present; is\_structural is true if any pd, on, or round tag is present.
5. Record the session regime from gex\_daily: gex\_pct, and whether the 09:30 price is above or below the flip.

Output: levels table with date, level\_es, tags, is\_gamma, is\_structural, gex\_pct, above\_flip.

## Stage 1: Regime test

Stage 1 asks whether high-GEX sessions are quieter and more mean-reverting than low-GEX sessions once VIX is accounted for. It needs only gex\_daily, ES 1-minute bars, and VIX, and should take about a week.

Main sample: June 2023 (the start of the free data) up to the holdout start. A 2020 to May 2023 robustness sample exists only on the ThetaData Value fallback.

### Daily outcome measures (ES, 09:30 to 16:00 ET)

Range ratio is the primary measure. Below 1 means the session moved less than options implied.

```latex
RR_D = \frac{H_D - L_D}{EM_D}
```

Variance ratio uses 5-minute log returns r\_t and overlapping 30-minute sums. Below 1 means mean reversion; above 1 means trending.

```latex
VR_D = \frac{\operatorname{Var}\left(\sum_{k=0}^{5} r_{t-k}\right)}{6\,\operatorname{Var}(r_t)}
```

Efficiency ratio is net move over total path. Low values mean choppy, two-way trade.

```latex
ER_D = \frac{|C_D - O_D|}{\sum_{j} |p_j - p_{j-1}|} \quad \text{(1-minute closes)}
```

### Model

Run one regression per outcome with Newey-West standard errors (5 lags) and day-of-week dummies.

```latex
Y_D = \alpha + \beta_1\,\text{GEXpct}_D + \beta_2 \ln \text{VIX}_{D-1} + \gamma^\top \text{DOW}_D + \varepsilon_D
```

The hypothesis is beta\_1 below 0 for all three outcomes. Also report the mean of each outcome by GEXpct quintile, with day-bootstrap 90% intervals.

### Decision

1. Pass: beta\_1 on range ratio is negative at p < 0.05, the top-quintile mean range ratio is at least 15% below the bottom quintile, and variance ratio moves the same direction.
2. Kill: beta\_1 is not significant, or the quintile gap is under 15%. Drop the gamma framing and decide whether to run Stage 3 on structural levels alone.

### Robustness (report, do not tune on)

1. Swap ln VIX for ln(EM / S\_0).
2. Drop FOMC, CPI, and NFP days.
3. Use the 0DTE-only GEX percentile in place of total.
4. On the ThetaData Value fallback only, run on the 2020 to May 2023 sample.

Expect few strongly negative-gamma days after mid-2022. That is why the model uses the continuous percentile rather than the sign alone.

## Stage 2: Level event study

Stage 2 asks whether price reverses more often at gamma levels than at structural or random levels, using only 1-minute bars. Expect one to two weeks, and a few thousand touches across the main sample.

### Placebo levels

Add 5 random levels per session, drawn uniformly within one expected move of the 09:30 price and at least 4 ticks from any real level. Tag them placebo. Their success rate is the base rate every real level type must beat.

### Touch definition (09:31 to 15:50 ET)

Direction d is +1 when price approaches from above (testing support) and -1 from below (testing resistance). With a = 0.10 EM and b = 2 ticks, bar t is a touch when:

```latex
d = +1:\; \text{low}_t \le L + b \;\text{and}\; \max_{t-30 \le k < t} \text{close}_k \ge L + a
```

```latex
d = -1:\; \text{high}_t \ge L - b \;\text{and}\; \min_{t-30 \le k < t} \text{close}_k \le L - a
```

Ignore a touch if the same level was touched in the previous 10 bars. Record touch\_n, the count of earlier touches of that level in the session.

### Outcome label

With R = 0.10 EM and F = 0.05 EM, scan forward for up to 60 bars from bar t. The adverse test (L - dF) starts on bar t itself; the favourable test (L + dR) starts on bar t+1, because the touch bar's favourable extreme is the approach before the touch, not a reversal after it (changed 2026-10-06, approved; the original inclusive scan labelled fast approaches as held on bar 0). Success means price reaches L + dR before L - dF. If both happen in the same bar, or neither happens within 60 bars, label it a failure; report timeouts separately.

Also record excursions over the next 30 bars, in expected-move units:

```latex
MFE = \max_{t \le k \le t+30} \frac{d\,(p_k - L)}{EM_D}, \qquad MAE = \max_{t \le k \le t+30} \frac{-d\,(p_k - L)}{EM_D}
```

### Model

Logistic regression of success on tags and context, with standard errors clustered by date.

```latex
\Pr(\text{success}) = \Lambda\big(\beta_0 + \beta_g G + \beta_{gx}\, G \cdot \text{GEXpct} + \beta_x \text{GEXpct} + \beta_r R_{nd} + \beta_p PD + \beta_o ON + \beta_f \text{First} + \beta_\delta \text{Dist} + \tau^\top \text{TOD} + \beta_s d\big)
```

G is is\_gamma; R\_nd, PD, and ON are the round, prior-day, and overnight tags; First is touch\_n = 0; Dist is the level's distance from the 09:30 price in EM units; TOD is three time buckets (09:30 to 10:30, 10:30 to 14:00, 14:00 to 16:00). Placebo touches are the omitted reference group.

Also report a plain table of success rates by level group (gamma only, structural only, both, placebo) and GEXpct tercile, with Wilson 90% intervals.

### Decision

1. Keep gamma tags if beta\_g or beta\_gx is positive at p < 0.10.
2. Otherwise drop gamma tags and carry structural levels only into Stage 3.
3. If no real level type beats placebo at all, Stage 3 is testing pure order flow at arbitrary prices. That is still worth running, but note it.

A success here is a price pattern, not a profitable trade. Profitability is only measured in Stage 3, after costs.

## Stage 3: Order flow features and trade simulation

Stage 3 asks whether waiting for absorption plus a reclaim turns level touches into positive expectancy after costs. It uses ES trades only in a window around each Stage 2 touch, so the data stays inside the free credit. Expect two to three weeks.

### Pull the data

For each touch, request trades from t0 minus 10 minutes to t0 plus 30 minutes. Price 20 windows with get\_cost first and multiply out before pulling the rest. Inside the window, reset t0 to the first trade within b of the level.

Each trade j has price p\_j, size q\_j, and aggressor sign s\_j (+1 buy, -1 sell). Signed volume:

```latex
v_j = s_j \, q_j
```

### Feature 1: Absorption ratio

Over the window W from t0 to t0 + 3 minutes, measure aggressive volume pushing into the level per tick of penetration through it, relative to normal.

```latex
\text{AggIn} = \sum_{j \in W,\; s_j = -d} q_j, \qquad \text{Pen} = \max\left(1,\; \frac{d\,(L - p^{ext}_W)}{0.25}\right)
```

p\_ext is the lowest trade in W for support and the highest for resistance. The baseline is the median, over the prior 20 sessions, of 3-minute ES bar volume per tick of range in the same half-hour slot. It counts both sides, so halve it.

```latex
\text{AbsRatio} = \frac{\text{AggIn} / \text{Pen}}{0.5 \cdot \text{Baseline}_{slot}}
```

### Feature 2: Approach delta

Net aggressive flow over the 10 minutes before t0, from -1 to +1. Selling into support shows as a negative value.

```latex
\Delta_A = \frac{\sum_{j \in [t_0 - 10m,\, t_0)} v_j}{\sum_{j \in [t_0 - 10m,\, t_0)} q_j}
```

### Feature 3: Exhaustion

The rate of into-level aggressive volume in the last 2 minutes of the approach, against the 8 minutes before that. Below 1 means the push is fading.

```latex
\text{Ex} = \frac{\text{AggIn}_{[t_0 - 2m,\, t_0)} / 2}{\text{AggIn}_{[t_0 - 10m,\, t_0 - 2m)} / 8}
```

### Feature 4: Reclaim

The reclaim is the first 1-minute bar k ending within 10 minutes after t0 where both of these hold. Its end time is t\_r.

```latex
d\,(\text{close}_k - L) \ge 2 \text{ ticks} \quad\text{and}\quad d \sum_{j \in [t_0,\, \text{end}_k]} v_j > 0
```

Confirmed touch: a reclaim exists and AbsRatio is at least 2.0. Exhaustion and approach delta are recorded but not used in the rule; they go into the secondary regression only.

### Trade rules: confirmed entry

1. Entry E is the first trade after t\_r, plus one tick against you: E = p + d x 0.25.
2. Stop S is the extreme trade between t0 and t\_r, plus two ticks beyond it: S = p\_ext - d x 0.50.
3. Risk is R\_k = d(E - S) in points. Skip the trade if R\_k exceeds 0.15 EM; widen the stop to 4 ticks if R\_k is smaller.
4. Target T = E + d x 1.5 R\_k. It fills only if a trade prints one tick beyond T.
5. Stop exits fill one tick beyond S.
6. Time exit at t\_r + 30 minutes or 15:55 ET, whichever comes first, at the next trade minus one tick.
7. Walk the trade tick by tick, so there is no same-bar ambiguity.

### Trade rules: naive baseline

The same touches traded without waiting: a limit at L that fills only if a trade prints one tick through it, a stop at L - d x 0.05 EM, a target at 1.5 times that distance, and the same time exit.

### Profit and loss in R

C is your all-in round-trip commission and fees per contract in dollars; PV is $50 per point for ES or $5 for MES.

```latex
\text{PnL}_R = \frac{d\,(X - E)}{R_k} - \frac{C}{R_k \cdot PV}
```

```latex
\mathbb{E}[\text{PnL}_R] = \bar{w}\,p - \bar{l}\,(1 - p)
```

p is win rate, w-bar is average win in R, and l-bar is average loss in R (costs included).

### The comparison

| Level group | Naive baseline | Confirmed |
| --- | --- | --- |
| Gamma-tagged | N, win rate, expectancy R, 90% CI | N, win rate, expectancy R, 90% CI |
| Structural only | Same | Same |
| Placebo | Same | Same |

Compute every interval by resampling whole days 5,000 times. Also report the confirmed minus naive difference with its own interval, plus trades per month, profit factor, max drawdown in R, and longest losing streak.

Secondary analysis: regress PnL\_R on AbsRatio, approach delta, exhaustion, and the level tags, clustered by date. This shows which ingredient carries the result.

### Decision

1. Pass: in the level group carried from Stage 2, at least 200 confirmed trades, expectancy at least +0.10R after costs, a 90% interval lower bound above 0, and the confirmed minus naive interval above 0.
2. Kill: anything less. Stop before buying full order book data.

## Holdout, robustness, and the go/no-go decision

The holdout is the most recent 9 months of data at the time of the first pull, fixed in config.yaml before Stage 1 runs. in\_sample(date) excludes it from every query until the final run.

### Final run

1. Freeze config.yaml and record the code version and date in RUNLOG.md.
2. Run Modules 2 and 3 and Stages 2 and 3 on the holdout, once, with no changes.
3. The holdout passes if confirmed expectancy has the same sign and at least half the in-sample value. With roughly 9 months of trades the interval will be wide, so significance is not required on the holdout alone.

### Robustness checks (on in-sample data, report only)

1. Nudge each parameter in the registry one notch up and one notch down, one at a time. Expectancy must stay positive in at least 80% of nudges.
2. Split results by calendar year, GEXpct tercile, and time-of-day bucket. One period carrying the whole result is a warning.
3. Replicate on NQ with the frozen rules: NDX and NDXP open interest from Databento OPRA and quotes from ThetaData, NQ.v.0 from Databento, and the FRED NASDAQ100 close for the basis.

### Multiple testing

Every variant you run goes in RUNLOG.md, including the ones that failed. If the count passes 20, also require the NQ replication to be positive before calling it a go.

### Decision

| Outcome | Condition | Next step |
| --- | --- | --- |
| Go | Stage 3 pass, holdout pass, robustness pass | Phase two: buy full order book data and build the live system |
| Partial | Positive expectancy but below one threshold | Paper trade the frozen rules live for 3 months, logging every touch, before spending |
| No-go | Holdout fails or Stage 3 kill | Stop, write up what was learned, keep the pipeline for the next idea |

## Parameter registry

Every tunable number lives in config.yaml with the defaults below. Edit them before Stage 1 if you disagree, then freeze; the nudge column is what the robustness check uses, never what you tune on.

| Parameter | Default | Nudges | Used in |
| --- | --- | --- | --- |
| max\_dte | 45 days | none | Ingestion, GEX |
| max\_rel\_spread | 0.5 of mid | 0.3, 0.7 | IV cleaning |
| parity\_band | 2% of SPX close | none | Forward fit |
| flip\_grid | plus or minus 5%, 5-point steps | none | Flip |
| gex\_pct\_lookback | 252 sessions | 126, 504 | Regime |
| round\_step | 50 SPX points | 25, 100 | Levels |
| level\_window | 1.0 EM | 0.75, 1.25 | Levels |
| merge\_tol | 2 ticks | 1, 4 | Levels |
| approach\_a | 0.10 EM | 0.075, 0.15 | Touches |
| proximity\_b | 2 ticks | 1, 3 | Touches |
| approach\_lookback | 30 bars | 20, 45 | Touches |
| debounce | 10 bars | 5, 15 | Touches |
| success\_R | 0.10 EM | 0.075, 0.15 | Stage 2 label |
| fail\_F | 0.05 EM | 0.04, 0.075 | Stage 2 label |
| label\_horizon | 60 bars | 45, 90 | Stage 2 label |
| placebo\_per\_day | 5 | none | Stage 2 |
| abs\_window | 3 minutes | 2, 5 | Stage 3 |
| abs\_threshold | 2.0 | 1.5, 3.0 | Stage 3 |
| baseline\_sessions | 20 | 10, 40 | Stage 3 |
| reclaim\_window | 10 minutes | 5, 15 | Stage 3 |
| reclaim\_ticks | 2 | 1, 3 | Stage 3 |
| entry\_slippage | 1 tick | 2 | Simulation |
| stop\_buffer | 2 ticks | 1, 4 | Simulation |
| max\_risk | 0.15 EM | 0.10, 0.20 | Simulation |
| min\_risk | 4 ticks | none | Simulation |
| target\_mult | 1.5 R | 1.0, 2.0 | Simulation |
| time\_exit | 30 minutes | 20, 45 | Simulation |
| cost\_rt\_usd | Your broker's all-in round trip | plus 50% | Simulation |
| bootstrap\_draws | 5,000 day resamples | none | Statistics |
| holdout | Most recent 9 months at first pull | none | All stages |

## Build order and what is deferred

The build takes about seven weeks part-time, and each phase starts only after the gate before it passes.

&#91;embedded content: build order · 5 phases, 5 gates\]

Read top to bottom: a failed gate either stops the project or narrows it, and only Gate 3 plus the holdout can release phase-two spending.

### Build checklist

- [ ] Open free ThetaData and Databento accounts; install the Theta Terminal
- [ ] Write config.yaml from the parameter registry and set the holdout dates
- [ ] Build ingest\_daily.py, ingest\_options.py, and the bars half of ingest\_futures.py
- [ ] Price every Databento pull with get\_cost; if the total exceeds the $125 credit, switch to the ThetaData Value fallback
- [ ] Build gex.py and pass its four validation checks (Gate 0)
- [ ] Run the Stage 1 notebook and record the Gate 1 call in RUNLOG.md
- [ ] Build levels.py and touches.py, run Stage 2, record the Gate 2 call
- [ ] Build the trades half of ingest\_futures.py, flow.py, and sim.py; run Stage 3 and record the Gate 3 call
- [ ] Run the holdout, robustness checks, and NQ replication; record the decision

### Deferred to phase two

Full order book data and book reconstruction; iceberg, refill, and pulled-liquidity features; intraday GEX from trade-level options data to capture same-day 0DTE positioning; a real-time pipeline; dashboards; and execution. Each phase-two feature must beat the Stage 3 version on the same frozen test before it is built.
