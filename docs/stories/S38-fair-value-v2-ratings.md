# S38 — Fair value v2: strength ratings, context features, walk-forward, judged against the real market

**Priority:** P0 · **Tier:** sonnet · **Depends on:** S24 (Elo, `shared/elo.py`) and S25 (venue and toss priors, `shared/venue_priors.py`) from the other set. The market comparison needs S35 (and S36 for in-play). S29 (player registry) is optional, as a v3 feature · **Wave:** 10
**Conflicts with:** none

## Story

As the strategy owner, I need a win-probability model that uses information the market may under-weight (team and
player strength by format, venue, toss, chasing), trained walk-forward, and measured against the Betfair implied
probability. That is how we find out whether thesis 1 (`docs/architecture/profit-architecture.md` §2) holds anywhere.

## Background

- `strategy/fair_value.py` (S14):
  - `family_of` L25; `tick_features(tick, family)` L35, which covers state only (balls fraction, wickets in hand, run
    rates, target-relative score, batting_is_home);
  - `_label` L52;
  - `FairValueModel(family, stride=3, calib_frac=0.2, seed)` L63 with `fit` L91, `predict_batch` L112, `predict_home`
    L121, `save`/`load` L125-135.
  It knows nothing about **who** is playing, so pre-match it is ~50/50 and it can only add value from state.
- On the S18 synthetic holdout its ECE was 0.12–0.14. That evaluation is meaningless because the market was biased, but
  it shows calibration has never been checked on real data.
- File naming (moved from S23): `models/fair_value/<family>.joblib` is overwritten by whichever key trains last.

## Context pack

- Cricsheet data: `data/raw/{ipl,odis,t20s,tests}/*.json` (about 11k files) and `data/incoming_cricsheet/`. Each JSON has
  `info` (dates, teams, venue, toss {winner, decision}, players {team: [names]}, gender, match_type, event, outcome) and
  `innings` → overs → deliveries (batter, bowler, runs, wickets).
- Real prices: `load_real_corpus(family)` (S35) and the aligned corpus `data/real/aligned` (S36). The pre-match price is
  the last tick with `in_play=False`.
- Evaluator hook: `scripts/train_format_specialists.py` `policy="value"` loads the fair model by family (S14 dispatch).
  Keep `FairValueModel.load(path)` compatible, and add a resolver `strategy.fair_value.model_path(family, key=None)` that
  the dispatch can be switched to later (do not edit that script here).
- Walk-forward: train on seasons < Y, calibrate on season Y−1, test on season Y, for every Y with enough data.

## Owned files

- `strategy/fair_value.py`
- New: `strategy/ratings.py` (adapter over `shared/elo.py`, `shared/venue_priors.py`)
- `scripts/train_fair_value.py`
- New: `scripts/eval_fair_value_vs_market.py`
- New: `tests/unit/test_s38_fair_value_v2.py`

## Tasks

1. `strategy/ratings.py` is a thin adapter. **Reuse** `shared/elo.py` (`CricketEloTracker`, S24) and `shared/venue_priors.py` (S25), and `shared/player_registry.py` (S29) when committed. Build only what they lack. Required behaviour of the combined ratings:
   - team Elo per (format family, gender), updated match by match in date order, with margin-of-victory weighting and a
     home-venue term;
   - player ratings: batting runs per ball above expectation and bowling runs and wickets per ball, exponentially
     decayed, aggregated to the XI.
   Everything is strictly **as of** the day before the match. Add a test that a match's own result never affects its
   own rating.
2. Features in `tick_features` (opt-in by model version):
   - pre-match: the Elo difference, the XI-strength difference, toss winner and decision, venue chase rate (as-of),
     and the competition tier;
   - in-play: those, plus the existing state.
   Keep the v1 feature set available as `version="v1"`.
3. Walk-forward training in `scripts/train_fair_value.py --family t20 --version v2`. Save
   `models/fair_value/<family>_<version>_<key or all>.joblib` plus a JSON report with per-season Brier, log loss, ECE
   and n.
4. `scripts/eval_fair_value_vs_market.py`, on the real corpus:
   - for each segment (family × {pre-match, powerplay, middle, death / session} × competition tier), compute model vs
     market Brier, log loss, ECE, n, and a paired bootstrap CI of the Brier difference;
   - output `docs/reports/fair_value_vs_market.json` and a short markdown table.
   **This is rung R1 of the validation ladder.**
5. Never use a holdout season for fitting, calibration or feature statistics (venue chase rate included).

## Success criteria

- [ ] Leak tests: a match's result does not change its own ratings or features; shuffling the holdout season's results
      does not change any prediction.
- [ ] Walk-forward runs on Cricsheet alone and reports per-season metrics (no market needed).
- [ ] On an S35 fixture corpus, the evaluator produces the segment table with paired CIs.
- [ ] The v1 model still loads and predicts identically.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s38_fair_value_v2.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Strategy rules and sizing (S40). Buying lineup data feeds.

## Integration notes from wave 10 (binding)

- `shared/elo.py` (S24): `CricketEloTracker`, `family_for_match_type`, `margin_multiplier`; query
  `rating(team, family, gender, as_of)` (`as_of` excludes matches on that date); `update()` must be called in date order
  (out-of-order raises). Pass `normalize_team=` instead of importing `scripts/cricsheet_harmonizer.py` into `strategy/`
  (it imports the DB and the synthetic market at import time). Associate teams can rate high from playing weak teams
  (Uganda is 4th in t20|male): use the Elo difference *and* a competition-tier feature, never Elo alone.
- `shared/venue_priors.py` (S25): `VenuePriors.build(raw_cricsheet_dicts)`, `get(venue, family, gender, as_of)`.
  `chase_win_pct` and `toss_winner_win_pct` are **percentages (0–100)**; metrics are `None` when there is no history
  before `as_of` (`fallback=True`, `n=0`). Handle `None` explicitly.
- Fix `strategy/fair_value.py:family_of` so `ODM` maps to `odi` (today it falls through to `multiday`); add a test.
