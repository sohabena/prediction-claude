# S02 — Honest, model-driven holdout evaluation

**Priority:** P0 · **Depends on:** none · **Wave:** 1
**Conflicts with:** S09 (same file — never run concurrently)

## Story

As the person deciding whether a specialist may trade, I need holdout metrics that
measure the trained PPO policy itself, computed from real simulated bets, so that the
dashboard and graduation gate reflect reality.

## Background

`scripts/train_format_specialists.py:332-485` `evaluate_specialist_holdout`:

- accepts `model: PPO` but **never calls it**; actions come from a hand-coded rule using
  `estimate_causal_in_play_prob` (lines 371-409);
- that heuristic has a units bug (`target = score + req_r * overs_rem / 6.0`, line 316);
  `TEAM_RATINGS` may have been fitted on the full corpus (lookahead);
- fabricates `hedged_in_match = int(bets * 0.40)` (line 420) and per-bet
  `confidence=0.85`, `closing_odds=1.03`, `clv=0.12`, `reward=0.48` (lines 442-449);
- persists those fabricated bets to the DB through `persist_virtual_bets` (line 597).
- Drawdown is computed on cumulative P&L across matches but divided by a single
  100k bankroll, producing 90–440% "drawdowns" that nobody acts on.

`models/specialists/metadata.json` therefore contains numbers produced by the heuristic.

## Context pack

- `scripts/train_format_specialists.py` (726 lines) layout: `SPECIALISTS_DIR`/`CORPUS_DIR` L51-52;
  `ALL_SPECIALISTS_CONFIG` L54 (per key: `key, name, gender, max_overs, par_1, wicket_decay_power, div, gamma,
  ent_coef, lr`); `SPECIALIST_MAP` L201; `KEY_ALIASES` L204; `@dataclass SpecialistEvaluationMetrics` L214-231
  (key, format_name, matches_trained, matches_tested, timesteps, total_bets, avg_bets_per_match, hedged_bets,
  hedge_rate_pct, win_rate_pct, total_pnl, mean_roi_pct, sharpe_ratio, max_drawdown_dollars, max_drawdown_pct,
  profit_factor); `load_corpus(key)` L233; `TEAM_RATINGS` L254; `estimate_causal_in_play_prob` L281-330;
  `evaluate_specialist_holdout(model, specialist_key, format_name, test_episodes, max_bets_per_match=25,
  commission=0.02, starting_bankroll=100_000.0) -> (metrics, bet_records)` L332-485;
  `train_single_specialist` L489-595 (**S09's — do not edit**; calls the evaluator at L563);
  `persist_virtual_bets(bets)` L597-622 (raw `INSERT INTO virtual_bets ... ON CONFLICT DO NOTHING`, hard-codes
  `observation_hash='hash_spec'`); `update_redis_model_zoo_state(all_metrics)` L625; `main` L666.
- **Callers to keep working** (new params keyword-only with defaults): `train_format_specialists.py:563`;
  `scripts/weekly_cricsheet_sync_and_retrain.py:44,343` (keyword call, then `asdict(metrics)` → metrics must stay a
  dataclass; keep existing field names, add new ones with defaults) and `:45,435` (`persist_virtual_bets`).
- Env API in the loop: `env = CricketBettingEnv(data=[ep_ticks], initial_bankroll, settlement_mode="real",
  max_bets_per_episode, exchange_commission)`; `obs, info = env.reset()`; `obs, r, term, trunc, info = env.step(a)`;
  `info["step_info"]`; after the episode `env._portfolio.session_pnl`, `env._bets_this_episode`,
  `env._settled_bets_history: list[VirtualBet]`. If S03 has merged, pass `episode_sampling="sequential"` only when
  the constructor accepts it (`inspect.signature`).
- Probabilities: raw SB3 `PPO` → `model.policy.get_distribution(model.policy.obs_to_tensor(obs)[0]).distribution.probs`;
  `PhoenixAgent.get_action_distribution(obs)` exists at `rl/agent.py:110`.
- Reuse, don't duplicate: `virtual_trading/settlement.py:31 SettlementEngine.compute_clv(placement_odds,
  closing_odds)` (staticmethod); `backtesting/backtester.py` has hold/random baselines (`_run_hold_backtest` L187,
  `_run_random_backtest` L200) for reference.
- DB `virtual_bets` (ORM `backend/models/bets.py`): PK `(placed_at, match_id)`; columns action, team, odds, stake,
  confidence, settled_at, outcome, profit_loss, closing_odds, clv, agent_version, observation_hash, reward
  (+ `id BIGSERIAL` from `backend/db_init.py`). **No `source` column** → tag rows with
  `agent_version = f"holdout_eval:{key}:{version}"`. Because the PK includes `placed_at` and inserts use
  `ON CONFLICT DO NOTHING`, same-timestamp bets are silently dropped: use the tick timestamp plus a per-bet
  microsecond offset and log the inserted row count.

## Owned files

- `scripts/train_format_specialists.py` — **only** `evaluate_specialist_holdout`,
  `estimate_causal_in_play_prob`, `persist_virtual_bets`, `update_redis_model_zoo_state`,
  `SpecialistEvaluationMetrics` and new helpers. Do not touch training functions (S09).
- New: `backtesting/baselines.py`
- New: `tests/unit/test_s02_holdout_eval.py`

## Tasks

1. Rewrite `evaluate_specialist_holdout` so that every action comes from
   `model.predict(obs, deterministic=True)`. Add `policy: Literal["model","heuristic","random","hold"]="model"`
   so baselines can run through the **same** loop.
2. Move the heuristic into `backtesting/baselines.py` as `HeuristicPolicy`, fix the target
   bug (`target = score + req_r * overs_rem` with overs in overs, or better: carry the real
   target from the tick if present). Add `RandomPolicy` and `HoldPolicy`. Each exposes
   `predict(obs, tick) -> int`.
3. Compute every metric from `env._settled_bets_history` / env portfolio:
   - hedged bets = bets that reduced |net exposure| on the selection (count them, do not estimate),
   - per-bet `confidence` = `agent.get_action_distribution(obs)[action]` captured at placement,
   - `closing_odds` = the same-side price on the last tick of the episode,
   - `clv` = `1/entry − 1/closing` for BACK, `1/closing − 1/entry` for LAY,
   - `reward` = the env reward on the placement step.
   Persist these real values; if one is unavailable, persist `None`, never a constant.
4. Metrics: report per-match ROI on **turnover** (P&L / total stake) and on bankroll,
   win rate per **bet** and per **match**, profit factor, Sharpe of per-match P&L
   (no `sqrt(n)` scaling — report n separately), max drawdown of the cumulative P&L
   curve as a fraction of `starting_bankroll × matches` *and* in currency, average
   bets/match, commission paid, and 95% bootstrap CI for mean per-match P&L (1000 resamples, seeded).
5. Every evaluation call returns metrics for the model **and** the `random` and `hold`
   baselines on the same episodes; add `edge_vs_random` = model mean P&L − random mean P&L.
6. Add `evaluated_at`, `holdout_match_ids` (or a hash of them), `policy`, and
   `code_version` (git short SHA if available) to the metrics dataclass.
7. `persist_virtual_bets`: tag rows via `agent_version="holdout_eval:<key>:<version>"` (there is no `source` column; see context pack) so they can be filtered from live
   virtual trading in the UI. Make `placed_at` unique per bet and log the inserted row count.
   Do not change DB schema here.

## Success criteria

- [ ] `evaluate_specialist_holdout` calls `model.predict` on every step when `policy="model"`
      (unit test with a mock model that records calls and returns HOLD → zero bets, zero P&L).
- [ ] A mock model that always returns `BACK_HOME_SM` produces bets whose P&L matches a
      hand calculation on a 3-tick fixture episode, including 2% commission.
- [ ] `grep -nE "0\.85|1\.03|0\.12|0\.48|\* 0\.40" scripts/train_format_specialists.py`
      returns nothing inside evaluation or persistence code.
- [ ] Heuristic target bug fixed and covered by a test.
- [ ] Metrics include baseline comparisons and a bootstrap CI.
- [ ] Running the evaluator on 5 fixture episodes takes < 10 s.

## Verification

```bash
python -m pytest tests/unit/test_s02_holdout_eval.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Training changes, timesteps, splits (S09 / S07).
- Rewriting `metadata.json` — orchestrator re-evaluates after S01 data regeneration.
