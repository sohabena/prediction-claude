# PHOENIX training strategy: beat the LotusBook price, prove it, then signal

Status: plan, 2026-11-03. Builds on `profit-architecture.md` (validation ladder §4, Betfair constraint §7) and the
recording pipeline (S50–S57). Stories S58–S66 implement it.

## 1. The problem, stated exactly

A signal makes money only if our probability is better than **the price we can actually take**, by more than the
margin, at the moment a person can place the bet (60 s after the signal). So the target is not "predict the winner"
but "predict the winner better than the de-vigged LotusBook price, net of its margin, 60 s later".

What we have, measured on 2026-11-03:

| Asset | Size | Use |
|---|---|---|
| Cricsheet ball-by-ball | ~11.2k matches (t20s 5,845, odis 3,226, ipl 1,243, tests 921) | Strength, venue, player and state models. Labels only; **no prices** |
| LotusBook recordings | ~1 day: 15 real/SRL matches, ~1k listing ticks, ~9.6k match-page rows | The only real prices. Grows every day the recorder runs |
| Current fair model | v2 GBM + isotonic, walk-forward Brier 0.157 (T20 2026 fold), ODI 0.185 | Never compared with a market price |
| Live inputs | Prices (listing + match-page JSON), competition, start time | **No live score** (scoreboard blocked in headless) |

Consequences that shape everything below:

1. **Pre-match is the main arena.** It needs no score, tolerates a 60 s manual delay, and is where a strength model
   built on 20 years of data can know something the price does not.
2. **The market is the baseline, not the enemy.** The de-vigged LotusBook price is already a strong forecaster. The
   best design learns *where it is wrong* and otherwise defers to it. Any model that does not beat the market's own
   probability on log loss, on held-out real matches, does not get to emit signals.
3. **Real-price evidence is scarce and slow.** Every component that touches prices must shrink towards the market
   when data is thin, report uncertainty, and refuse to claim an edge it cannot show with confidence intervals.
4. **In-play needs a different model.** Without a live score, in-play work is price dynamics only, at horizons a
   person can act on (≥ 2 min).

## 2. Architecture

```
                 ┌────────────────────────── offline, Cricsheet (labels, no prices) ─────────────────────────┐
                 │  S60 pre-match strength model v3                                                           │
                 │   Elo (team, format, gender) + expected-XI player strength + venue/toss priors + tier      │
                 │   → p_model(home wins)   walk-forward by season, stacked GBM + logistic, calibrated        │
                 └──────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                            │ p_model per recorded match (as of the match date)
LotusBook recordings ─► S58 training dataset ──────────────┼──► S61 market-blend model
 v_match_odds, market_ticks,   one row per match × snapshot │     logit p = a + b·logit p_mkt + c·(logit p_model − logit p_mkt)
 registry, results             (T-6h, T-60m, T-10m, off,    │              + segment terms, Bayesian shrinkage to (a=0,b=1,c=0)
                               in-play every 2 min)         │     → p_final, with posterior sd
                                        │                   │
                                        └─► S59 market module (de-vig: multiplicative / power / Shin, margin,
                                                 favourite–longshot curve, mid vs takeable price)
                                                            │
                         S62 bet selection & staking ◄──────┘
                          EV at the takeable price 60 s later, minus margin; min edge per segment;
                          Bayesian (uncertainty-shrunk) fractional Kelly; caps per match/day
                                                            │
                         S63 evaluation harness ────────────┤  walk-forward by date on recordings; CLV primary,
                          (gates every model and policy)    │  log loss vs market, P&L with bootstrap CIs; pre-registered
                                                            │  segments; writes the gate report the trust registry reads
                         S64 in-play price-dynamics model ──┤  price-only features → 10-min-ahead move; research track
                         S66 SRL feasibility (separate) ────┘  calibration + margin + lag on SRL; no model unless an edge shows
                         S65 model registry & retraining: versioned artefacts, manifests, champion/challenger,
                          weekly retrain in Docker, drift and calibration checks into the quality monitor
```

## 3. Components

**S58 training dataset (the foundation).** One builder turns recordings into a tidy, versioned dataset:
`match` table (registry + result + Cricsheet link) and `snapshot` table (match × time: de-vigged prices from
`v_match_odds`, best takeable back/lay, the price 60 s and 10 min later, time to start, in-play flag). Entity
resolution links each recorded match to its Cricsheet match (team canonicalisation + date ±1 day) so `p_model` and
the official result can be attached. Results come from `match_results`; Cricsheet's result is a cross-check.
Quarantined rows are excluded by construction (they are no longer in `odds_ticks`). SRL rows carry `is_simulated`.

**S59 market module.** Pure functions: implied probability from back/lay (mid and takeable side), overround, three
de-vig methods (multiplicative, power, Shin) with a test that they agree on fair books, and a fitted
favourite–longshot curve. Every model consumes market probabilities through this module, so "the market's opinion"
means one thing everywhere.

**S60 pre-match strength model v3.** A pre-match-only model (no state features) because that is the arena. Inputs:
team Elo by format/gender with home term, expected-XI player strength (last N appearances, decayed, shrunk),
venue par and chase bias, competition tier, days since last match. Two learners (regularised logistic on a few
strong features; GBM on all), stacked and isotonic-calibrated. Walk-forward by season exactly as S38. Reports
against the v2 pre-match fold and against a pure-Elo baseline, segmented by format, gender and tier.

**S61 market-blend model.** The edge model. A Bayesian logistic regression on recorded snapshots:
`logit p_final = a + b·logit p_mkt + c·Δ + Σ segment terms`, with `Δ = logit p_model − logit p_mkt`, priors centred
on "trust the market" (a=0, b=1, c=0). With little data it returns the market; as matches accrue it learns how much
of `p_model`'s disagreement to keep, and whether the market is miscalibrated in a segment (favourite–longshot,
women's, associate, low tier, far-from-start). Fit by Laplace approximation (numpy/scipy only) so the posterior sd is
available to staking. Walk-forward by date; never fitted on a match it is evaluated on.

**S62 bet selection and staking.** Extends `strategy/value_policy.py`. Edge is computed at the **takeable price 60 s
after the signal** (from S58's look-ahead in backtests, from the live book in production). Bet only when the
posterior lower bound of EV clears a per-segment minimum. Stake = fractional Kelly on the posterior-mean edge,
shrunk by posterior uncertainty, capped per match and per day. Emits the existing `Signal` objects.

**S63 evaluation harness.** The referee. Walk-forward by date over S58 snapshots, purged by match. Metrics: CLV
(binding definition, `shared.metrics.clv_prob`) as primary; log loss and Brier of `p_final` vs `p_mkt` (paired
bootstrap CI); simulated P&L under the 60 s fill rule; calibration (ECE, reliability bins). Segments are
pre-registered in a file before evaluation, so the harness cannot be tuned into finding an edge. Output: a gate report
(R1/R2 per segment: pass, fail, insufficient data with n needed) consumed by the trust registry.

**S64 in-play price dynamics.** Research model on `market_ticks` match odds and over-runs markets: after a large
price move, does the price continue or revert over the next 10 min? Price-only features (size of move, time since,
volatility, spread, time in match by elapsed minutes). Strictly evaluated by S63; produces no signals until it passes.

**S65 model registry and retraining.** Artefacts under `models/registry/<name>/<version>/` with a manifest (data
window, row counts, git sha, metrics, gate status). Champion/challenger: a challenger replaces the champion only when
S63 shows it better on the same held-out window. Weekly retrain job in Docker (Cricsheet sync → S60 → dataset S58 →
S61 → S63). Drift checks (feature distributions, calibration on the last 7 days) added to the quality monitor.

**S66 SRL feasibility.** Separate track on SRL recordings: closing-price calibration against results, margin, and
whether prices lag. A model is only proposed if a bias larger than the margin shows with a confidence interval.

## 4. Validation (unchanged ladder, sharper tests)

| Rung | Pass criterion on held-out LotusBook recordings |
|---|---|
| R1 model | `p_final` log loss < `p_mkt` log loss, paired bootstrap 95% CI excludes 0, in a pre-registered segment with ≥ 200 matches |
| R2 strategy | Mean CLV per bet > 0 (95% CI) and simulated P&L CI > 0 after margin and 60 s latency, ≥ 150 held-out matches |
| R3 paper live | ≥ 300 settled paper bets, CLV CI lower bound > 0 |
| R4 money | The operator's decision |

Until R1 passes, the blend model returns the market and the system emits **no** signals (it says so plainly).
This is the expected state for the first weeks.

## 5. Order of work

- Wave A (independent): S58 dataset, S59 market module, S60 pre-match model v3.
- Wave B (needs A): S61 blend, S62 staking, S63 evaluation, S64 in-play dynamics, S66 SRL feasibility.
- Wave C: S65 registry and weekly retraining.

## 5a. Status after the first build (2026-11-03)

- Built: S58 dataset, S59 market, S60 pre-match v3, S61 blend, S62 staking, S63 evaluation and gates, S64 in-play
  research, S65 registry and weekly trainer container (first run Monday 03:00 UTC), S66 SRL feasibility, S67 Cricsheet
  coverage code, S68 live score.
- Measured: pre-match v3 matches pure Elo within noise on Cricsheet walk-forward (T20 log loss 0.6285 vs 0.6279,
  ODI 0.6170 vs 0.6135). Team strength carries almost all pre-match information; extra player and venue features add
  little. v3 selects Elo when Elo wins.
- **Live score found:** the page's own `match-center/score/v3` feed carries score, toss, batsmen and bowler for real
  matches on the `livescore-v2` pipeline (S68 records it into `match_context`, toss into the registry). SRL and some
  real matches are `legacy` with no score. This re-opens a state-aware in-play model for `livescore-v2` matches once
  enough of them are recorded; the existing v1 state features (`strategy/fair_value.py`) are the starting point.
- Gates: every rung is `insufficient` (7 settled recorded matches, 1 with a pre-match price). Signals stay off.
- Biggest lever now: Cricsheet coverage. Only 2 of 16 recorded real matches can be rated with the local Cricsheet set
  (internationals and IPL only; no Afghanistan T20Is). Downloading Cricsheet's `all_json.zip` (S67 code ready) is the
  operator's approval.

## 6. What this plan will not do

- Train on synthetic markets or treat Cricsheet-only accuracy as an edge (S22 lesson).
- Use score features live before a live score source exists.
- Lower the R1/R2 thresholds to make a result appear.
