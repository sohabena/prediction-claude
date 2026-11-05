# S97 — Market-dynamics model: trade the odds' own movement, with the scoreboard

**Pillars:** 2 and 3 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S74, S83, S84, S85, S90 · **Wave:** 22

## User story

As the operator, I want one model that looks only at how the odds are moving and at the live scoreboard (no team
ratings, no Cricsheet), and that bets on both sides as the odds change during a match, because the bookmaker's odds
already contain all the other information. The model should learn from every match it sees and improve over time.

## Thesis and honest limits

The price already holds the bookmaker's view of who wins. An edge can only come from **predictable price movement**:
over-reaction after wickets, boundaries and big overs, drift during quiet spells, slow repricing. The model therefore
predicts **the market probability 10 minutes from now**, not the winner. It never "ensures" profit; its value is
measured as CLV in excess of the placebo, match-clustered, like every other variant (Golden Rulebook section 7).

## Acceptance criteria

1. **Dataset** (`training/market_dynamics.py`, pure, plus a read-only builder script): for every real recorded match,
   in play, a snapshot every 30 s with features that use only data at or before the snapshot:
   - market: band market probability now; its change over the last 1, 3, 5 and 10 minutes; volatility (std of 30 s
     changes over 10 min); back-lay spread of both runners; distance from the price at the off; time since the last
     price change;
   - scoreboard (when the match has a live score; `livescore-v2`): innings, overs, wickets, runs, required rate,
     runs and wickets in the last 1, 3 and 5 overs (from the stored score rows), balls remaining;
   - a flag for "no scoreboard" so the same model serves score-less matches with market features only.
   Label: band market probability 10 minutes later minus now (and, for the fill check, 60 s later). Rows whose label
   window crosses the end of the match or a market suspension are dropped.
2. **Model** (`models/market_dynamics/<date>.joblib`): a small gradient-boosted regressor (scikit-learn
   `HistGradientBoostingRegressor`, pinned versions) predicting the 10-minute change. Evaluation is **leave-one-
   match-out** (few matches): mean absolute error vs the zero baseline, direction accuracy on moves larger than the
   spread, and a simulated CLV after the 60 s delay and the spread. A report in `reports/market_dynamics/<date>/`.
3. **Strategy variant** `mkt_dyn` (and `mkt_dyn_e06` with a 6% minimum edge) in the paper trader: the model's
   p_model = p_market now + predicted 10-minute change (clipped to the band), fed through the existing decision rules
   (`strategy/model_edge.py`: target position, ticket budget 30, both sides, guards, 100,000 budget and 120,000 hard
   limit, 60 s fills). Pre-match it does not trade (no scoreboard, little movement). It never uses team ratings.
   Its rows have `source = 'market_dynamics'`; placebos and reports treat it like any other variant.
4. **Learning over time:** a weekly step in `scripts/retrain_weekly.py` retrains on all settled recordings and
   replaces the model only when leave-one-match-out simulated CLV is not worse than the current one. The daily review
   ranks it with the others; replay (`scripts/replay_paper.py`) can evaluate any model file.
5. **First test now:** train on the recordings so far, replay the variant over all recorded matches with a pinned
   `--to`, and report: per match and in total, fills, CLV (clustered), excess over placebo, P&L; with the honest
   sample size.
6. Tests with synthetic paths (no real DB writes): no look-ahead in features or labels, label alignment, the
   no-scoreboard flag, the variant's decisions go through the same caps and guards, model loading failure means no
   new risk.
