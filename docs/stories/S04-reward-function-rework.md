# S04 — Reward function rework

**Priority:** P0 · **Depends on:** S03 merged (uses its `step_info` contract) · **Wave:** 2

## Story

As an RL researcher, I need a reward whose cumulative sum equals (a risk-adjusted
version of) the money the policy makes, so that PPO cannot farm bonuses that do not
correspond to profit.

## Background (`rl/reward.py`)

- **CLV bonus** (line 178): adds the *total* positive CLV of open bets on *every* step,
  ignores stake, never penalises adverse moves → one early bet on the eventual winner
  earns ≈ +0.6/step, dwarfing settlement (≈ +0.2).
- **Hedge bonus** (91-99): paid at 15× when a hedge is placed and the same profit is paid
  again at settlement (double counting).
- **Patience reward** (+0.01 per quiet HOLD) sums to more than trading P&L over a match.
- **Flat `bet_cost` 0.02** ≈ 10 % of a 2 % stake in P&L units — mis-scaled vs real costs.
- **Dead terms:** drawdown penalty never fires mid-episode (realised balance only);
  Sharpe bonus requires ≥ 5 settlements per episode but there is exactly one.

## Context pack

- `rl/reward.py` (182 lines): `RewardFunction.__init__(pnl_scale=10, hedge_bonus_scale=15, bet_cost=0.02,
  unrealized_scale=1, patience_reward=0.01, naked_exposure_penalty=0.01, overtrading_threshold=30,
  overtrading_penalty=0.05, exposure_threshold=0.5, exposure_penalty=0.1, drawdown_threshold=0.10,
  drawdown_penalty_scale=50, sharpe_bonus_scale=0.5, clv_bonus_scale=2.0)`; `compute(action, step_info)` L74-182.
- Only constructor callers: `rl/environment.py:106` (`RewardFunction()`, no kwargs) and
  `tests/unit/test_reward.py:15`. The kwargs can change freely; keep a no-arg constructor.
- The incremental drawdown penalty needs the previous step's value: keep `self._prev_dd_term` and reset it on
  the call after one with `episode_done=True`; also expose `reset()`.
- Bankroll denominator `B`: add `initial_bankroll: float = 100_000.0` to the constructor. The env does not pass it
  today; report "env should pass initial_bankroll to RewardFunction" as an orchestrator follow-up (one-line change
  in `rl/environment.py:106`, which you may make together with removing the TODO(S04) legacy keys — the only
  allowed edits outside your files).
- Inputs: only the README `step_info` contract keys.

## Owned files

- `rl/reward.py`
- `tests/unit/test_reward.py` (rewrite to the new design)
- New: `tests/unit/test_s04_reward_invariants.py`

## Design

Reward per step (all in bankroll-fraction units, `B = initial bankroll`):

```
r_t = equity_delta_t / B                       # dense, spread- and commission-aware (from S03)
      - λ_dd  * max(0, dd_t - dd_thr)^2 - (same for t-1)   # incremental drawdown penalty
      - λ_tail* max(0, -worst_case_pnl_t/B - tail_thr)     # tail-liability penalty, per step
      + λ_clv * clv_delta_t                    # optional, signed, stake-weighted; default 0
```

- Because S03's `equity` already includes realised P&L, commission and spread, the sum of
  `equity_delta / B` over an episode equals final net P&L / B **exactly**. No separate
  settlement term, no hedge bonus, no per-bet cost, no patience reward.
- The drawdown penalty is written as a difference of a potential so it does not
  accumulate when drawdown is flat.
- Default weights: `λ_dd=2.0, dd_thr=0.05, λ_tail=0.05, tail_thr=0.10, λ_clv=0.0`.
- Optional `reward_scale` (default 100) multiplies the total so typical per-episode returns
  are O(1) for PPO.

## Tasks

1. Replace `RewardFunction` with the design above; keep the class name and
   `compute(action, step_info) -> float` signature. Read only README-contract keys.
2. Expose all weights as constructor args; add `components(step_info) -> dict[str, float]`
   returning each term for logging (used by callbacks/TensorBoard).
3. Delete the hedge, patience, bet-cost, Sharpe, overtrading and exposure terms. If any
   other module imports their constructor kwargs, `grep` and report them (do not edit).
4. Remove S03's temporary adapter (`# TODO(S04)`) from `rl/environment.py` **only** if it
   exists — this is the single allowed edit outside owned files; mention it in the report.

## Success criteria

- [ ] Invariant test: on 20 random episodes (S03 env, random policy, `λ_dd=λ_tail=λ_clv=0`,
      `reward_scale=1`) `Σ r_t == final_net_pnl / B` within 1e-6.
- [ ] A HOLD-only policy gets total reward exactly 0.
- [ ] Placing a bet and immediately closing it at the same prices gives negative total
      reward (spread + slippage), never positive.
- [ ] Two bets with identical prices but stakes S and 2S produce CLV terms in ratio 1:2.
- [ ] No term is positive for adverse price moves.
- [ ] Drawdown penalty total is zero when drawdown stays below threshold, and does not
      grow while drawdown is constant above threshold (only changes when it changes).
- [ ] `components()` keys sum to `compute()` (test).

## Verification

```bash
python -m pytest tests/unit/test_reward.py tests/unit/test_s04_reward_invariants.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Hyper-parameter tuning of λ values (S09 experiments).
