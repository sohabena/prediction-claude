# PHOENIX RL Learning Engine - Detailed Design

## Overview

The RL Learning Engine is the brain of PHOENIX. It replaces hand-coded betting strategies with a self-improving agent that learns from thousands of virtual bets.

---

## 1. Environment Design (Gymnasium)

### State Space (Observation) -- DATA-ONLY PHILOSOPHY

Every feature is raw data from LotusBook/Cricbuzz, a mathematical transformation, or agent portfolio state. NO cricket heuristics.

```
Observation Vector (dim = 66):
├── Group 1: Raw Odds (12)
│   ├── back_home, lay_home, back_away, lay_away, back_draw, lay_draw
│   ├── implied_prob_home, implied_prob_away, implied_prob_draw
│   ├── overround, spread_home, spread_away
│
├── Group 2: Odds Momentum (16) -- math on price series
│   ├── odds_velocity_5s_home, odds_velocity_5s_away
│   ├── odds_velocity_30s_home, odds_velocity_30s_away
│   ├── odds_velocity_60s_home, odds_velocity_60s_away
│   ├── odds_acceleration_home, odds_acceleration_away
│   ├── volatility_30s_home, volatility_30s_away
│   ├── ema_crossover_home, ema_crossover_away
│   ├── momentum_score_home, momentum_score_away
│   ├── max_swing_60s_home, max_swing_60s_away
│
├── Group 3: Market Microstructure (8) -- derived from odds
│   ├── spread_width_home, spread_width_away
│   ├── spread_velocity_home, spread_velocity_away
│   ├── market_efficiency_score
│   ├── relative_price_level (where current odds sit in recent range)
│   ├── time_since_last_change_home, time_since_last_change_away
│
├── Group 4: Raw Match Statistics (8) -- direct from Cricbuzz
│   ├── is_live (0 or 1)
│   ├── overs_normalized (0.0 to 1.0)
│   ├── wickets_normalized (0.0 to 1.0)
│   ├── score_normalized
│   ├── run_rate_normalized
│   ├── required_run_rate_normalized
│   ├── innings (1 or 2, normalized)
│   ├── balls_remaining_normalized
│
├── Group 5: Temporal (6) -- cyclical time encoding
│   ├── hour_sin, hour_cos
│   ├── day_sin, day_cos
│   ├── minutes_since_match_start (normalized)
│   ├── seconds_since_last_tick (normalized)
│
├── Group 6: Portfolio State (8) -- agent's own state
│   ├── bankroll_pct (current / initial)
│   ├── session_pnl_pct
│   ├── open_positions_normalized
│   ├── exposure_pct
│   ├── recent_win_rate (last 20 bets)
│   ├── consecutive_streak (signed, normalized)
│   ├── daily_pnl_pct
│   ├── time_since_last_bet (normalized)
│
├── Group 7: Statistical Patterns (8) -- pure mathematical analysis
│   ├── mean_reversion_zscore_home, mean_reversion_zscore_away
│   ├── trend_strength_home, trend_strength_away
│   ├── volatility_percentile_home, volatility_percentile_away
│   ├── odds_autocorrelation_home, odds_autocorrelation_away

REMOVED (from old 72-feature design):
  - match_phase (heuristic -- agent learns from raw overs/wickets)
  - team_strength, venue_factor, toss_info, match_importance (subjective)
  - comeback_probability, blowout_probability (human-opinion-based)
  - similar_odds_win_rate, seasonality (requires labelled outcomes)
  - support/resistance proximity (TA-style heuristic)
```

### Action Space

```python
class BettingAction(IntEnum):
    HOLD = 0           # Do nothing
    BACK_HOME_SM = 1   # Back home team, small stake (1% bankroll)
    BACK_HOME_LG = 2   # Back home team, large stake (3% bankroll)
    BACK_AWAY_SM = 3   # Back away team, small stake (1% bankroll)
    BACK_AWAY_LG = 4   # Back away team, large stake (3% bankroll)
    LAY_HOME_SM = 5    # Lay home team, small stake (1% bankroll)
    LAY_AWAY_SM = 6    # Lay away team, small stake (1% bankroll)
```

**Design Rationale:**
- HOLD is the default action (most ticks should not trigger bets)
- Separate small/large stakes for position sizing decisions
- LAY only small stakes (higher risk, lower frequency)
- Draw market excluded initially (lower liquidity on LotusBook)

### Reward Function Design

```python
def compute_reward(self, action, step_info):
    """
    Multi-component reward function tuned for profitable betting.
    
    Key insight: We want the agent to learn PATIENCE (mostly HOLD)
    and PRECISION (bet only when edge exists).
    """
    reward = 0.0
    
    # === Component 1: Bet Outcome (dominant signal) ===
    if action != HOLD and step_info.get('bet_settled'):
        pnl = step_info['profit_loss']
        bankroll = step_info['bankroll']
        # Normalize P&L by bankroll for scale invariance
        reward += (pnl / bankroll) * 10.0
    
    # === Component 2: Patience Reward ===
    # Small positive reward for correctly NOT betting
    if action == HOLD:
        # Only reward if odds movement was unfavorable
        if abs(step_info['odds_change']) < 0.01:
            reward += 0.001  # Tiny reward for patience
    
    # === Component 3: Overtrading Penalty ===
    if step_info['bets_last_hour'] > 15:
        reward -= 0.01 * (step_info['bets_last_hour'] - 15)
    
    # === Component 4: Risk Management ===
    # Penalize exceeding risk limits
    if step_info['exposure_pct'] > 0.5:
        reward -= 0.1 * (step_info['exposure_pct'] - 0.5)
    
    # === Component 5: Drawdown Penalty (non-linear) ===
    drawdown = step_info['current_drawdown']
    if drawdown > 0.10:
        reward -= (drawdown - 0.10) ** 2 * 50  # Quadratic penalty
    
    # === Component 6: Sharpe Bonus (episodic) ===
    # Applied at end of episode (match)
    if step_info.get('episode_done'):
        returns = step_info['episode_returns']
        if len(returns) >= 5:
            sharpe = np.mean(returns) / (np.std(returns) + 1e-8)
            reward += max(0, sharpe) * 0.5
    
    return reward
```

---

## 2. Agent Architecture

### PPO Configuration

```python
from stable_baselines3 import PPO

model = PPO(
    policy="MlpPolicy",
    env=cricket_env,
    
    # Network architecture
    policy_kwargs={
        "net_arch": {
            "pi": [256, 256, 128],   # Policy network
            "vf": [256, 256, 128],   # Value network
        },
        "activation_fn": torch.nn.ReLU,
    },
    
    # PPO hyperparameters
    learning_rate=3e-4,
    n_steps=2048,          # Steps per rollout
    batch_size=64,
    n_epochs=10,           # PPO epochs per update
    gamma=0.99,            # Discount factor
    gae_lambda=0.95,       # GAE lambda
    clip_range=0.2,        # PPO clipping
    ent_coef=0.01,         # Entropy bonus (exploration)
    vf_coef=0.5,           # Value function coefficient
    max_grad_norm=0.5,     # Gradient clipping
    
    # Training
    verbose=1,
    tensorboard_log="./logs/ppo_cricket/",
)
```

### Why PPO Over Other Algorithms

| Algorithm | Pros | Cons | Verdict |
|-----------|------|------|---------|
| PPO | Stable, good exploration, handles discrete actions | Moderate sample efficiency | **Selected** |
| DQN | Good for discrete, experience replay | Poor for continuous, overestimation | Backup option |
| SAC | Excellent exploration via entropy | Designed for continuous actions | Not suitable |
| A2C | Simple, fast | High variance, unstable | Too risky for financial |

### Training Pipeline

```
Phase 1: Offline Pre-Training (Historical Data)
├── Load recorded odds sequences from TimescaleDB
├── Create replay environment (deterministic)
├── Train PPO for 500K+ timesteps
├── Evaluate on held-out matches
└── Save best model checkpoint

Phase 2: Online Fine-Tuning (Live Market)
├── Agent observes live odds in real-time
├── Places virtual bets against current market
├── Continues PPO training with live data
├── Model checkpoint every 1000 steps
└── Rollback if performance degrades

Phase 3: Evaluation & Graduation
├── Run agent on live data (virtual bets only)
├── Track rolling metrics (win rate, ROI, Sharpe)
├── Compare against baseline strategies
├── Graduate when all thresholds met
└── Human review and approval
```

---

## 3. Curriculum Learning

The agent learns progressively harder tasks:

### Stage 1: Basic Pattern Recognition
- **Environment:** Historical data only, simplified actions (BACK/HOLD)
- **Goal:** Learn when odds are mispriced
- **Graduation:** > 52% win rate on validation set

### Stage 2: Full Action Space
- **Environment:** Historical data, all 7 actions
- **Goal:** Learn position sizing and LAY decisions
- **Graduation:** > 5% ROI on validation set

### Stage 3: Live Market Simulation
- **Environment:** Live odds feed, virtual execution with slippage
- **Goal:** Handle real-world market dynamics
- **Graduation:** > 55% win rate, > 8% ROI over 200+ bets

### Stage 4: Adversarial Testing
- **Environment:** Live market with adversarial noise injection
- **Goal:** Robustness to regime changes, outlier events
- **Graduation:** Maintains positive ROI under adversarial conditions

---

## 4. Experience Replay and Memory

### Episode Buffer Structure

```python
@dataclass
class Experience:
    observation: np.ndarray    # State at time t
    action: int                # Action taken
    reward: float              # Reward received
    next_observation: np.ndarray  # State at time t+1
    done: bool                 # Episode finished?
    info: Dict                 # Match context, odds, etc.
```

### Prioritized Experience Replay

- High-reward experiences (big wins/losses) replayed more often
- Rare events (wickets, suspensions) upweighted
- Recent experiences weighted higher than old

---

## 5. Model Persistence & Versioning

```
models/
├── checkpoints/
│   ├── ppo_cricket_v1_500k.zip
│   ├── ppo_cricket_v1_1M.zip
│   └── ppo_cricket_v2_best.zip
├── best_model.zip             # Currently deployed model
├── training_log.json          # Training history
└── eval_results/
    ├── v1_eval_200bets.json
    └── v2_eval_200bets.json
```

### Version Management

- Every model checkpoint saved with training metadata
- A/B testing: new model vs current best on live data
- Automatic rollback if new model underperforms

---

## 6. Monitoring & Debugging

### TensorBoard Metrics

- `rollout/ep_rew_mean` - Average episode reward
- `rollout/ep_len_mean` - Average episode length
- `train/policy_loss` - Policy network loss
- `train/value_loss` - Value network loss
- `train/entropy_loss` - Exploration entropy
- `custom/win_rate` - Betting win rate
- `custom/roi` - Return on investment
- `custom/sharpe_ratio` - Risk-adjusted returns
- `custom/action_distribution` - How often each action is selected
- `custom/hold_percentage` - % of steps with HOLD action

### Key Debugging Signals

1. **Agent always HOLDs:** Entropy too low, increase `ent_coef`
2. **Agent over-trades:** Reward function not penalizing enough, tune overtrading penalty
3. **Win rate high but ROI negative:** Agent winning small, losing big -- tune stake sizing
4. **Training diverges:** Learning rate too high, reduce or add warmup

---

## 7. Graduation System

```python
class GraduationEvaluator:
    """
    Evaluates if RL agent is ready for live trading.
    
    All criteria must be met for 14 consecutive days.
    """
    
    CRITERIA = {
        'win_rate': {'threshold': 0.55, 'window': 200},
        'roi': {'threshold': 0.08, 'window': 200},
        'sharpe_ratio': {'threshold': 1.5, 'window_days': 30},
        'max_drawdown': {'threshold': 0.15, 'window_days': 30},
        'profitable_days': {'threshold': 10, 'window_days': 14},
        'bet_volume': {'threshold': 100, 'window_days': 30},
    }
    
    def evaluate(self) -> GraduationResult:
        """Check all criteria against rolling windows."""
        results = {}
        all_passed = True
        
        for metric, config in self.CRITERIA.items():
            value = self.compute_metric(metric, config)
            passed = value >= config['threshold']
            results[metric] = {
                'value': value,
                'threshold': config['threshold'],
                'passed': passed,
            }
            if not passed:
                all_passed = False
        
        return GraduationResult(
            ready=all_passed,
            consecutive_days=self.consecutive_pass_days,
            required_days=14,
            metrics=results,
        )
```

### Post-Graduation Protocol

1. **Day 1-3:** Live execution at 25% normal stake
2. **Day 4-7:** Scale to 50% if metrics hold
3. **Day 8-14:** Scale to 75%
4. **Day 15+:** Full stake
5. **Continuous monitoring:** Auto-revert to virtual if metrics degrade
