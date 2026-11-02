# S14 — Calibrated fair-value model and a value-betting benchmark policy

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S13 (uses its corpora format; can start on current corpora) · **Wave:** 5
**Conflicts with:** none (edits one dispatch block in `scripts/train_format_specialists.py`, which no other wave-5 story touches)

## Story

As the strategy owner, I need an explicit, calibrated win-probability model and a simple
"bet when price beats fair value by more than costs, size by fractional Kelly" policy. That policy
is the bar the RL agent must beat on the same episodes, and it gives the live engine the
`win_prob` it already accepts for Kelly sizing.

## Background (why this is the better profit strategy)

- Today the only alpha source is PPO acting on 50 features. Nothing estimates fair probability explicitly.
  So we cannot tell whether a profit comes from better probability estimates (real, transferable edge)
  or from exploiting quirks of the simulator (see S13).
- In-play cricket edge, in order of how realistic it is for this system:
  1. **A better fair price than a slow or soft book.** The live target is LotusBook, a bookmaker-style
     market that can lag the exchange.
  2. **Disciplined trading out (green-up) after a favourable move.**
  3. Microstructure or latency. This is **not** available: there is a 3 s scrape plus a deliberate
     5–15 s placement delay (`shared/constants.py:115-116`).
  A calibrated model directly serves (1) and gives (2) its entry signal.
- `virtual_trading/engine.py:139-165` `_target_liability(..., win_prob)` already implements fractional Kelly
  when `win_prob` is given (back edge L153, lay edge L155). But `live_trading_loop.py:406` calls
  `propose(action, event, confidence=...)` without `win_prob`, so live sizing ignores edge entirely.
- `backtesting/baselines.py:19 estimate_in_play_prob` is a hand-tuned heuristic with static ratings. It is not
  fitted and not calibrated.

## Context pack

- Training data: `data/specialists/corpus_<key>.json.gz`, loaded with `scripts/train_format_specialists.py:load_corpus(key)`
  (L233), a list of episodes, each a list of tick dicts. Tick fields: `score, wickets, overs, innings,
  balls_remaining, run_rate, req_run_rate, batting_team, team_home, team_away, match_format, gender, back_home,
  lay_home, back_away, lay_away, time, match_id`. The result is only in `SETTLEMENT_ONLY_KEYS = ("_meta", "winner",
  "loser", "winner_is_home")` (`scripts/prepare_specialist_datasets.py:31`). It is allowed as a **label** for
  supervised training and never as a feature.
- Split: use the same match-level chronological split as S09. Grep `def chronological_split\|def split_episodes`
  in `scripts/train_format_specialists.py`; import it and do not re-implement it. **The fair model must never see
  holdout matches.**
- Evaluator: `evaluate_specialist_holdout(..., policy: Literal["model","heuristic","random","hold"])`
  `scripts/train_format_specialists.py:566`. Dispatch is in `run(pol)` L583-603. Baseline objects expose
  `predict(obs, tick, env) -> int` and `reset()`. Add `"value"` here (the only edit to that file).
- Action ids: `shared/schemas.py:22 BettingAction`. HOLD=0, BACK_HOME_SM=1, BACK_HOME_LG=2, BACK_AWAY_SM=3,
  BACK_AWAY_LG=4, LAY_HOME_SM=5, LAY_AWAY_SM=6, LAY_HOME_LG=7, LAY_AWAY_LG=8.
- Exchange maths (README shared pack): back EV per unit = `p·o − 1`; lay EV per unit stake = `1 − p·o`. Commission
  is 2% on net market winnings. The required edge must exceed commission plus half the spread.
- sklearn is installed. sb3_contrib is not.
- Gotcha: on synthetic corpora the market price *is* a hand model. "Edge" there measures model disagreement, not
  real alpha. Real validation needs recorded odds (S16). State this in the report output.

## Owned files

- New: `strategy/__init__.py`, `strategy/fair_value.py`, `strategy/value_policy.py`
- New: `scripts/train_fair_value.py`
- `scripts/train_format_specialists.py`: **only** the `run(pol)` dispatch inside `evaluate_specialist_holdout`
  and the `policy` Literal
- New: `tests/unit/test_s14_fair_value.py`

## Tasks

1. `strategy/fair_value.py`:
   - `FairValueModel` with `fit(episodes)`, `predict_home(tick) -> float`, `save(path)` and `load(path)`.
   - Features: format, innings, balls remaining fraction, wickets in hand, run rate, required run rate,
     target-relative score, batting-is-home. Fit **per format family** (T20 / ODI / multi-day). Use gradient boosting
     or logistic regression on state features.
   - Wrap it in isotonic calibration fitted on a validation slice of the train split.
   - Never read `winner*` / `_meta` from a tick inside `predict_home`. Add a test that strips these keys and gets the
     same output.
2. `scripts/train_fair_value.py --key <specialist>`:
   - fits on the train split;
   - reports Brier score, log loss and ECE (10 bins) on the holdout, plus the same metrics for the
     market-implied probability (de-vigged mid) as a reference;
   - saves to `models/fair_value/<family>.joblib` plus a JSON report, with `code_version` and the split hash.
3. `strategy/value_policy.py`:
   - `ValuePolicy(fair_model, min_edge=0.02, commission=0.02, max_bets=25, green_up_at=0.5)` with
     `predict(obs, tick, env) -> int`.
   - Back the selection when `p·back − 1 > min_edge + commission·(p·back−1)⁺`, and lay when `1 − p·lay > min_edge`.
   - Use the LG size when the edge is above 2× `min_edge`.
   - Close the position (opposite side) once the equity gain reaches `green_up_at × initial edge`. Use
     `env._pnl_home`/`env._pnl_away` if `env` is given.
   - Expose `win_prob(tick, action) -> float` so the live engine can be fed later.
4. Add `"value"` to the evaluator dispatch. It loads the fair model for the specialist's family and raises a clear
   error if none is trained.
5. Report, in the script output and the JSON, the per-match P&L of `value` against `model`, `random` and `hold` on the
   same holdout.

## Success criteria

- [ ] A fitted model's calibration ECE on the holdout is ≤ 0.05 for the T20 family on the current corpus.
      If it is not met, report the number; do not tune on the holdout.
- [ ] A leak test shows that removing the settlement-only keys does not change predictions, and that holdout match
      ids are not in the training set.
- [ ] `evaluate_specialist_holdout(..., policy="value")` runs end to end on a 5-episode fixture.
- [ ] On a fixture where the fair prob is 0.6 and the back price is 2.0, ValuePolicy backs. At a price of 1.6, it holds.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s14_fair_value.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Feeding `win_prob` into live `engine.propose` (backlog, after S16 validation shows the model beats the market).
- Adding fair-value edge as an observation feature (changes `OBSERVATION_SIZE`; backlog).
