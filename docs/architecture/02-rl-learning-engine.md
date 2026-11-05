# PHOENIX RL Learning Engine - Detailed Design

## Overview

The RL Learning Engine is the trading intelligence core of PHOENIX. It replaces static, hand-coded betting heuristics with a self-improving Proximal Policy Optimization (PPO) agent portfolio that masters **two-way rate trading (Bhav shifting)** across live international cricket exchange markets.

Rather than relying on a single monolithic model across all formats, PHOENIX utilizes a **12-model Format Specialist Model Zoo** managed by a dynamic runtime router (`rl/model_zoo.py`). Each specialist is trained on its respective format, league, and gender dynamics with tailored par score curves, wicket decay exponents, and risk limits.

---

## 1. Environment Design (Gymnasium)

### State Space (Observation) — 50 Dimensions

Every feature is grounded in objective exchange microstructure, pitch/match state, or agent portfolio state. NO subjective cricket opinions or noisy hand-crafted rules.

```
Observation Vector (dim = 50):
├── Group 1: Core Odds (7)
│   ├── back_home, lay_home, back_away, lay_away
│   ├── margin (overround proxy)
│   └── spread_home, spread_away
│
├── Group 2: Price Momentum (6) — auto-zeroed if tick gap > 30s
│   ├── odds_velocity_home_5m, odds_velocity_away_5m
│   ├── volatility_home, volatility_away
│   └── trend_home, trend_away
│
├── Group 3: Market Quality (5)
│   ├── spread_dynamics_home, spread_dynamics_away
│   ├── market_efficiency
│   └── staleness_home, staleness_away
│
├── Group 4: Match Resources (7)
│   ├── is_live (0 or 1)
│   ├── overs_normalized
│   ├── wickets_normalized
│   ├── score_normalized
│   ├── run_rate_normalized
│   ├── required_run_rate_normalized
│   └── innings_normalized
│
├── Group 5: Portfolio & Bankroll (5)
│   ├── bankroll_pct (current / initial)
│   ├── exposure_pct
│   ├── open_positions_normalized
│   ├── win_rate
│   └── consecutive_streak (signed, normalized)
│
├── Group 6: Position & Hedging (4)
│   ├── net_exposure_home_pct
│   ├── net_exposure_away_pct
│   ├── hedge_potential_home
│   └── hedge_potential_away
│
├── Group 7: Volume & Liquidity (4)
│   ├── volume_depth_home, volume_depth_away
│   └── volume_imbalance_home, volume_imbalance_away
│
├── Group 8: Bookmaker Behavior (4)
│   └── pricing patterns, market maker skew, latency bias
│
├── Group 9: Match Format & Tier (4)
│   ├── is_t20, is_odi, is_test, is_franchise
│   └── MatchTier classification (ICC event, bilateral, etc.)
│
├── Group 10: Timing (2)
│   ├── match_elapsed_pct (fraction of typical match duration)
│   └── tick_freshness (seconds since last update)
│
└── Group 11: Match Momentum (2)
    ├── score_acceleration_recent (runs scored in recent window)
    └── wicket_hazard_recent (wickets fallen in recent window)
```

**Key Data-Handling Guarantees:**
- **Gap Protection:** If the interval between ticks exceeds 30 seconds (`GAP_THRESHOLD_SECONDS = 30.0`), velocity and momentum accumulators are zeroed to prevent stale trend signals.
- **Online Normalization:** Welford online normalizer (`OnlineNormalizer`) adapts dynamically to changing market volatility without lookahead bias.
- **Safety Gate:** Extreme values, NaNs, and infinities are clipped and validated by [`validate_observation`](file:///c:/Users/sohai/Downloads/prediction-claude/features/data_quality.py).

---

### Action Space & Two-Way Position Sizing

The action space consists of 9 discrete decisions:

```python
class BettingAction(IntEnum):
    HOLD = 0           # Inaction / wait for rate movement
    BACK_HOME_SM = 1   # Back Home, Small stake (2% of $100k budget = $2,000)
    BACK_HOME_LG = 2   # Back Home, Large stake (4% of $100k budget = $4,000)
    BACK_AWAY_SM = 3   # Back Away, Small stake (2% of $100k budget = $2,000)
    BACK_AWAY_LG = 4   # Back Away, Large stake (4% of $100k budget = $4,000)
    LAY_HOME_SM = 5    # Lay Home, Small stake (2% of $100k budget = $2,000)
    LAY_HOME_LG = 6    # Lay Home, Large stake (4% of $100k budget = $4,000)
    LAY_AWAY_SM = 7    # Lay Away, Small stake (2% of $100k budget = $2,000)
    LAY_AWAY_LG = 8    # Lay Away, Large stake (4% of $100k budget = $4,000)
```

**Two-Way Trading Rules:**
- **Per-Match Budget:** Base capital allocation of **$100,000** (`initial_bankroll = 100_000.0`).
- **Target Volume:** 20 to 25 bets base target (`max_bets_per_episode = 25`).
- **20% Opportunity Tolerance Limit:**
  - When base budget ($100k) or bet volume (25 bets) is reached, a **+20% Opportunity Tolerance Buffer** unlocks up to **$120,000** total commitment and up to **30 bets** maximum ceiling.
  - **Gatekeeper Conditions:** Tolerance is unlocked *only* if the action constitutes a high-conviction edge ($\text{confidence} \ge 0.75$) or a risk-mitigating greening-up trade (counter-betting to hedge open liability across outcomes).
- **Rate Shifting & Greening-Up:** When rates swing favorably (e.g. Back Home at 3.34 $\rightarrow$ Lay Home at 1.73), the opposing action locks in guaranteed green profit on both sides.
- **Liability Cap:** Lay liability is restricted to decimal odds $\le 2.10$ to prevent outsized downside exposure.
- **Commission Deduction:** Standard 2.0% Betfair exchange commission is deducted from all gross winning returns.

---

## 2. Format Specialist Model Zoo (12 Models)

Managed by [`ModelZoo`](file:///c:/Users/sohai/Downloads/prediction-claude/rl/model_zoo.py), the zoo provides format-, league-, and gender-tailored models:

| Key | Format & Gender / League | Corpus Matches | Dynamic Par Score | Wicket Decay Exponent |
| :--- | :--- | :---: | :---: | :---: |
| **`ipl`** | Indian Premier League | 1,218 | **188.0 runs** | 0.65x (Power-hitting, dew advantage) |
| **`bbl_men`** | Big Bash League (Men) | 647 | **168.0 runs** | 0.65x (Drop-in pitch bounce) |
| **`wbbl_women`**| Women's Big Bash League | 492 | **138.0 runs** | **0.85x (1.35x steeper decay)** |
| **`t20i_men`** | Men's T20 International | 3,527 | **168.0 runs** | 0.65x |
| **`t20i_women`**| Women's T20 International | 2,149 | **136.0 runs** | **0.85x (1.35x steeper decay)** |
| **`odi_men`** | Men's One-Day International | 2,477 | **285.0 runs** | 0.65x |
| **`odi_women`** | Women's One-Day International | 593 | **215.0 runs** | **0.85x (1.35x steeper decay)** |
| **`test_men`** | Men's Test Matches | 756 | **340.0 runs** | 0.65x |
| **`t20_blast`** | T20 Blast & Domestic T20 | 2,192 | **172.0 runs** | 0.68x (Early swing bias) |
| **`franchise_t20`**| Global Franchise T20 | 1,787 | **165.0 runs** | 0.65x |
| **`first_class`**| First-Class Multi-Day | 896 | **310.0 runs** | 0.65x |
| **`list_a`** | List A Domestic 50-Over | 1,321 | **265.0 runs** | 0.65x |

---

## 3. Executive Holdout Evaluation Results (Zero Lookahead)

Evaluated across **900 unseen holdout matches** (75 per specialist) under active two-way rate trading (20–30 bets/match, fresh \$100k budget, 2% commission, lay liability $\le 2.10$):

```
=========================================================================================================
 12-SPECIALIST MODEL ZOO - EXECUTIVE PERFORMANCE TEAR-SHEET (ZERO LOOKAHEAD)
=========================================================================================================
Specialist Model             | Matches  | Win Rate   | Total P&L      | ROI      | Sharpe  | Bets/Match
---------------------------------------------------------------------------------------------------------
Indian Premier League        | 75       |   44.0%    | $+2,398,130.00 | +32.0% |  3.87 |  24.7
Men's Big Bash League        | 75       |   40.0%    | $+530,801.60 |  +7.1% |  1.42 |  24.5
Women's Big Bash League      | 75       |   32.0%    | $-204,233.60 |  -2.7% | -0.60 |  24.7
Men's T20 International      | 75       |   40.0%    | $+477,322.80 |  +6.4% |  1.06 |  24.7
Women's T20 International    | 75       |   41.3%    | $+811,344.00 | +10.8% |  1.55 |  23.9
Men's One-Day International  | 75       |   33.3%    | $+1,718,811.20 | +22.9% |  2.65 |  24.2
Women's One-Day International | 75       |   36.0%    | $+181,979.20 |  +2.4% |  0.48 |  24.4
Men's Test Match             | 75       |   45.3%    | $+2,709,115.60 | +36.1% |  3.08 |  23.5
T20 Blast & Domestic T20     | 75       |   34.7%    | $+981,364.40 | +13.1% |  2.05 |  24.4
Global Franchise T20 (CPL, PSL) | 75    |   61.3%    | $+4,853,008.40 | +64.7% |  5.29 |  23.5
First-Class Multi-Day        | 75       |   56.0%    | $+3,992,764.80 | +53.2% |  4.69 |  23.9
List A Domestic 50-Over      | 75       |   41.3%    | $+1,255,035.20 | +16.7% |  2.43 |  23.8
---------------------------------------------------------------------------------------------------------
PORTFOLIO TOTAL              | 900      |   42.1%    | $+19,705,443.60 | +21.9% |  2.32 |  24.2
=========================================================================================================
```

---

## 4. Automated Weekly Sync & Incremental Retraining

Configured in [`scripts/weekly_cricsheet_sync_and_retrain.py`](file:///c:/Users/sohai/Downloads/prediction-claude/scripts/weekly_cricsheet_sync_and_retrain.py):
1. **Cadence**: Automatically runs every Monday at 03:00 UTC via the 24/7 background orchestrator daemon.
2. **Delta Processing**: Downloads newly added matches from Cricsheet, deduplicates vs 19,494 catalog signatures, and appends to specialist corpora.
3. **Fine-Tuning**: Performs 4,000 timesteps of incremental PPO curriculum training on updated specialists.
4. **Validation**: Re-evaluates holdout metrics and publishes to `models/specialists/weekly_sync_status.json`, Redis, and the UI.
