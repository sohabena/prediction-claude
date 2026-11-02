# S03 — Environment execution realism

**Priority:** P0 · **Depends on:** none · **Wave:** 1
**Publishes:** the `step_info` contract in `docs/stories/README.md` (S04 consumes it)

## Story

As an RL researcher, I need `CricketBettingEnv` to account for bets the way an exchange
does (liability, spread, commission, delayed fills, random episode order), so that a
policy that is profitable in simulation has a chance of being profitable live.

## Background (`rl/environment.py`)

- **Sequential episodes:** `reset()` uses `self._data[self._episode_idx % len]` (line 165),
  so short training runs only ever see the first few dozen matches.
- **Exposure by stake, not liability:** `total_exposure += stake` (line 396) for LAY bets
  too; a 4 % lay at 10.0 actually risks 36 %.
- **Optimistic mark-to-market:** `_compute_unrealized_pnl` (636-684) values a BACK at the
  current *back* price; closing a back requires laying at the *lay* price (and vice versa).
- **Inverted "opportunity" rule:** lines 239-244 allow bets beyond budget when
  `|back_home − lay_home| > 0.04` — a wide spread is an illiquid, expensive market.
- **Hedge detection** (`_update_position`, 567-634) works on net *stake*, uses the worst
  entry price, and its `locked_pnl` formula does not equal the true green-up P&L.
- **Drawdown** uses realised balance, which only changes at episode end, so it is always 0
  mid-episode.
- **Commission** applied to the episode's positive net only (OK in principle) but not
  surfaced; no slippage; fills at the decision tick's price, while live fills happen
  5–15 s later.
- `placed_at = datetime.now()` instead of the tick timestamp.
- `_bets_this_hour` is just a per-episode counter.

## Context pack

- `rl/environment.py` (803 lines) map: `__init__` L52-132; `set_allowed_actions` L134; `reset` L138-200
  (sequential pick L165, meta extraction L174-194); `step` L202-351 (mask L217, budget/tolerance + spread rule
  L220-244, place + `_update_position` L255-280, mark-to-market L287-290, CLV L293, settle L296-305,
  step_info dict L314-342, reward L344); `_place_bet` L353-407; `_settle_bets` L409-486;
  `_determine_bet_outcome` L488-517; `_get_observation` L519-538; `_build_position_state` L550-565;
  `_update_position` L567-634; `_compute_unrealized_pnl` L636-684; `_compute_open_bets_clv` L686-721;
  `_get_odds_change` L723; `_tick_to_event` L736; `_tick_to_context` L764; `render` L793.
- Constants imported: `ACTION_SPACE_SIZE=9`, `OBSERVATION_SIZE=50`, `SMALL_STAKE_PERCENT=0.02`,
  `LARGE_STAKE_PERCENT=0.04` (`shared/constants.py` L45-51, owned by S05 — read only). Live uses 1 %/3 % (see
  `BettingAction` comments), so make the env's stake sizes constructor params with defaults 0.01/0.03.
- Reward call: `self.reward_fn.compute(action, step_info)`, `RewardFunction()` from `rl/reward.py` (S04 rewrites it).
  Today it reads `is_hedge, hedge_locked_pnl, bet_placed, unrealized_pnl_delta, bet_settled, profit_loss,
  net_exposure_home/away, bankroll, odds_change, clv_improvement, bets_last_hour, exposure_pct, current_drawdown,
  episode_done, episode_returns`. Keep emitting those legacy keys **in addition** to the new contract until S04
  lands (mark with `# TODO(S04): remove legacy keys`).
- **Constructor callers** (keep their keyword args working): `rl/trainer.py:49` (`data, initial_bankroll`) and
  `:253` (+ `settlement_mode, exchange_commission`) — trainer runs SB3 `check_env(env)` (obs float32 shape (50,),
  `reset` → `(obs, info)`); `backtesting/backtester.py:145,207,245`; `scripts/evaluate_model.py:29`;
  `scripts/analyze_training.py:71`; `scripts/train_format_specialists.py`; `scripts/weekly_cricsheet_sync_and_retrain.py:312`;
  `scripts/iterative_tuning_and_virtual_testing.py`; `scripts/benchmark_offline_improvements.py`.
  Private attrs read externally — keep the names: `_bets_this_episode`, `_portfolio`, `_settled_bets_history`,
  `_net_exposure_home/_away` (map to book P&L), `_current_episode_data`.
- `step_info` readers outside the env: `rl/callbacks.py:227-229,330-331` (`win_rate`, `roi`, `bankroll`);
  `backtesting/backtester.py:168-175,226-230,280+` (`profit_loss`, `bet_settled`, `bankroll`);
  legacy scripts read `is_hedge` (keep it = "this bet reduced worst-case loss").
- **Shared test file:** `tests/unit/test_budget_tolerance.py::test_env_bet_volume_tolerance` (L95+) tests the old
  spread-based env tolerance and is **owned by S05** (S05 deletes it). Don't edit it; re-test env tolerance in
  `tests/rl/test_s03_env_execution.py`. If it fails because of your change, list it as expected in your report.
- Fixture helper: `tests/rl/test_environment.py::_make_episode_data(n_steps=50)` (ticks 3 s apart,
  back_home≈1.85, lay_home≈1.90, back_away≈2.10, lay_away≈2.15, no `_meta` → use `settlement_mode="real"` with a
  `_meta` you add for deterministic tests).

## Owned files

- `rl/environment.py`
- `tests/rl/test_environment.py` (update existing tests to the new contract)
- New: `tests/rl/test_s03_env_execution.py`

## Tasks

1. **Constructor parameters** (keep existing ones, defaults shown):
   `small_stake_pct=0.01`, `large_stake_pct=0.03` (match live; read from `shared.constants`
   if S05 has added `SMALL_STAKE_PERCENT/LARGE_STAKE_PERCENT` updates — otherwise keep
   parameters), `max_liability_pct_per_bet=0.05`, `max_total_liability_pct=0.20`,
   `slippage_ticks=1`, `fill_delay_ticks=1`, `episode_sampling="random"`
   (`"sequential"` for evaluation), `bet_budget_tolerance_pct=0.20`.
2. **Episode sampling:** with `"random"`, `reset()` picks `self.np_random.integers(len(data))`;
   `options={"episode_index": i}` forces a specific episode. Evaluation code passes
   `episode_sampling="sequential"`.
3. **Fills:** decision at tick *t*, fill at tick `t + fill_delay_ticks` at that tick's price
   on the correct side (BACK→back price, LAY→lay price), then worsened by
   `slippage_ticks` Betfair price increments (implement `shared`-free helper
   `betfair_tick_size(price)` using the standard ladder: 1.01–2 step 0.01, 2–3 0.02,
   3–4 0.05, 4–6 0.1, 6–10 0.2, 10–20 0.5, 20–30 1, 30–50 2, 50–100 5, 100–1000 10).
   If the fill tick is past the end, the bet is not placed.
4. **Liability accounting:** LAY liability = stake·(odds−1). Size LAY bets so that
   **liability** equals the stake-percent budget (i.e. lay stake = budget/(odds−1)), cap
   every bet at `max_liability_pct_per_bet`, and reject bets that would push
   `worst_case_pnl` below `−max_total_liability_pct × bankroll`.
5. **Book model:** maintain the per-outcome book P&L
   (`pnl_if_home_wins`, `pnl_if_away_wins`) updated on every fill. Derive
   `worst_case_pnl`, `best_case_pnl`, and `total_liability` from it. Delete the
   `_best_*_entry_*` and `_net_exposure_*` hedge heuristics; positional features in the
   observation should be fed from the book (keep `_build_position_state` keys so the
   feature pipeline is unchanged; compute `net_home_exposure = pnl_if_home_wins`,
   `net_away_exposure = pnl_if_away_wins`, and best-entry fields as the stake-weighted
   average entry price per side).
6. **Mark-to-market (equity):** value the book at the price you could close at now:
   close a home BACK by laying home at `lay_home`, etc. Equity = bankroll + green-up value
   of the book at current prices, minus estimated commission on positive green-up.
   Emit `equity`, `equity_delta`, `peak_equity`, `current_drawdown` (on equity).
7. **Bet budget:** base cap `max_bets_per_episode`; the `+bet_budget_tolerance_pct` extra
   bets are allowed **only** for bets that strictly reduce `total_liability`. Remove the
   spread-based "opportunity" rule.
8. **Settlement:** at episode end settle from the book; apply commission on the net
   positive market P&L; void on `tie/no_result/draw/abandoned`. Emit `profit_loss`
   (net), `commission_paid`, `bet_settled`.
9. **CLV:** emit `clv_delta` = change since last step of
   `Σ stake_i·(1/entry_i − 1/close_price_now_i)` (signed, BACK; mirrored for LAY),
   divided by bankroll.
10. Use the tick timestamp for `placed_at`/`settled_at`. Remove `_bets_this_hour`.
11. Add `action_masks() -> np.ndarray[bool]` (HOLD always valid; bet actions invalid
    when the price is missing, budget exhausted, or the liability cap would be breached)
    for future MaskablePPO. Keep the old behaviour of converting invalid actions to HOLD.
12. Emit exactly the `step_info` keys in the README contract (plus extras if useful).

## Success criteria

- [ ] With `episode_sampling="random"` and 200 resets over 50 episodes, ≥ 40 distinct
      episodes are visited (seeded test).
- [ ] BACK 100 @ 2.0 then LAY to green up at 1.8 produces `worst_case_pnl ≈ best_case_pnl`
      (equal within 1 currency unit) and equity matches the analytic green-up value.
- [ ] A LAY at 10.0 never has liability above `max_liability_pct_per_bet × bankroll`.
- [ ] Opening a BACK and immediately marking it at the same tick yields
      `equity_delta < 0` (spread cost), never > 0.
- [ ] With `fill_delay_ticks=1` the fill price equals tick *t+1*'s price ± slippage.
- [ ] Bets beyond `max_bets_per_episode` are only accepted when they reduce liability.
- [ ] Mid-episode `current_drawdown` becomes > 0 when prices move against an open position.
- [ ] Commission: a winning episode with net +1000 reports `commission_paid == 20` at 2 %.
- [ ] `step_info` contains every key in the README contract (test iterates the table).
- [ ] Existing `tests/rl/test_environment.py` updated and green.

## Verification

```bash
python -m pytest tests/rl -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Reward weights and shaping (S04). Until S04 lands, keep `RewardFunction.compute`
  callable: if it raises on missing old keys, add a thin temporary adapter in the env that
  passes `0.0` for removed keys, marked `# TODO(S04): remove`.
- Feature pipeline changes.
