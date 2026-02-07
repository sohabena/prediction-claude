# PHOENIX User Guide

> A guide for dashboard users who want to monitor the RL agent's progress, understand what's happening, and act on bet suggestions after graduation.

---

## Table of Contents

1. [What is PHOENIX?](#what-is-phoenix)
2. [Getting Started](#getting-started)
3. [Dashboard Overview](#dashboard-overview)
4. [Training Page](#training-page)
5. [Virtual Trading Page](#virtual-trading-page)
6. [Graduation Page](#graduation-page)
7. [Live Matches Page](#live-matches-page)
8. [Advisor Page](#advisor-page)
9. [Understanding the Agent Lifecycle](#understanding-the-agent-lifecycle)
10. [Reading Bet Signals](#reading-bet-signals)
11. [FAQ](#faq)

---

## What is PHOENIX?

PHOENIX is an automated cricket betting system powered by reinforcement learning (RL). It watches live cricket odds, learns patterns, and makes virtual bets to prove it can be profitable before ever touching real money.

**Key principles:**
- The agent learns entirely from data -- no hand-coded cricket rules or human intuition.
- It places virtual bets first. Only after meeting strict profit criteria for 14 consecutive days does it "graduate."
- Even after graduation, PHOENIX never places real bets automatically. It suggests bets to you, and you decide.

---

## Getting Started

Once the system is running, open your browser and go to:

```
http://localhost:3000
```

This is the main dashboard. The sidebar on the left lets you navigate to all pages:

| Page | What it shows |
|------|--------------|
| **Dashboard** | High-level overview of training and trading |
| **Training** | Detailed RL training metrics and charts |
| **Virtual Trading** | Virtual bet history and performance |
| **Graduation** | Progress toward live readiness |
| **Live Matches** | Currently tracked cricket matches |
| **Advisor** | Bet suggestions (active after graduation) |

All pages auto-refresh, so you can leave the dashboard open and it stays current.

---

## Dashboard Overview

The main dashboard (`/`) shows three sections:

### Training Progress
- **Total Timesteps** -- How many training steps the agent has completed. More steps = more experience.
- **Episodes** -- Number of complete match simulations the agent has trained on.
- **Win Rate (1h)** -- Percentage of winning bets in the last hour of training. Green (>55%) is great, yellow (>50%) is okay, red is below breakeven.
- **Sharpe (1h)** -- Risk-adjusted return metric. Above 1.5 is the graduation target. Higher is better.

### Virtual Trading
- **Total Bets** -- How many virtual bets the agent has placed.
- **Win Rate** -- Overall percentage of bets that were profitable.
- **Total P&L** -- Cumulative profit or loss from virtual betting. Green means profit.
- **Avg Odds** -- Average odds the agent bets on. Very low odds (near 1.0) mean it's being too conservative; very high odds mean it's gambling.

### System
- **Agent Version** -- Which model checkpoint is currently active.
- **ROI (1h)** -- Return on investment over the last hour.
- **Wins / Losses** -- Raw count of winning vs losing bets.

---

## Training Page

The training page (`/training`) dives deeper into how the RL agent is learning.

### Summary Cards
- **Policy Loss** -- How much the agent's decision-making policy is changing. Should decrease over time as the agent stabilizes.
- **Value Loss** -- How accurate the agent's value estimates are. Lower is better.
- **Entropy** -- How "random" the agent's actions are. High entropy early on (exploring), lower as it learns (exploiting).
- **Agent Version** -- Current model version identifier.

### Charts
- **Episode Reward** -- Shows the reward the agent earns per episode over time. An upward trend means the agent is improving.
- **Win Rate** -- Rolling win rate across episodes. Should trend upward toward and above 55%.
- **ROI** -- Return on investment per episode. Target is consistently above 8%.

**What to look for:** Steady upward trends in reward and win rate, with decreasing volatility. If reward suddenly drops, the agent may have encountered a difficult market condition or a training issue.

---

## Virtual Trading Page

The trading page (`/trading`) shows the agent's virtual betting performance.

### Performance Cards
- **Total Bets** -- Total virtual bets placed.
- **Win Rate** -- Percentage of profitable bets (green >55%, yellow otherwise).
- **Total P&L** -- Net profit/loss in virtual currency (green = profit, red = loss).
- **Avg Odds** -- Average odds the agent places bets at.

### Bet History Table
The table shows the most recent 50 virtual bets with:
- **Time** -- When the bet was placed.
- **Action** -- What the agent did (e.g., BACK_HOME_SM = back home team with small stake).
- **Team** -- Which team the bet is on.
- **Odds** -- The odds at the time of the bet.
- **Stake** -- How much virtual currency was wagered.
- **Outcome** -- Win (green), Loss (red), or Pending (gray).
- **P&L** -- Profit or loss from this individual bet.

### Understanding Actions
| Action | Meaning |
|--------|---------|
| HOLD | Do nothing (the agent is waiting for a better opportunity) |
| BACK_HOME_SM | Bet on home team winning, small stake (1% of bankroll) |
| BACK_HOME_LG | Bet on home team winning, large stake (3% of bankroll) |
| BACK_AWAY_SM | Bet on away team winning, small stake |
| BACK_AWAY_LG | Bet on away team winning, large stake |
| LAY_HOME_SM | Bet against home team, small stake |
| LAY_AWAY_SM | Bet against away team, small stake |

**HOLD should be the most common action (~90%).** A good agent is patient and only bets when it sees a clear opportunity.

---

## Graduation Page

The graduation page (`/graduation`) tracks whether the agent is ready for live trading.

### What is Graduation?
Graduation is the process of proving the agent is consistently profitable. All six criteria must be met simultaneously for **14 consecutive days**.

### Criteria

| Criterion | Target | What it Means |
|-----------|--------|---------------|
| **Win Rate** | > 55% | Over the last 200 bets, more than 55% were winners |
| **ROI** | > 8% | Return on investment over the last 200 bets exceeds 8% |
| **Sharpe Ratio** | > 1.5 | Risk-adjusted returns are strong (over 30 days) |
| **Max Drawdown** | < 15% | The worst peak-to-trough loss stayed below 15% |
| **Profitable Days** | > 10 of 14 | At least 10 of the last 14 days were profitable |
| **Bet Volume** | > 100 | The agent placed at least 100 bets in 30 days |

### Progress Display
- **Consecutive Days** -- Shows how many days in a row all criteria have been met (out of 14 required).
- **Each Criterion** -- Shows the current value, the threshold, and whether it's met (checkmark or X).

**If any criterion fails on any day, the consecutive counter resets to zero.** The agent must prove sustained performance.

---

## Live Matches Page

The matches page (`/matches`) shows cricket matches currently being tracked.

### Match Cards
Each active match shows:
- **Competition** -- The tournament or series name (e.g., "IPL 2026", "ICC World Cup").
- **Teams** -- Home team vs away team.
- **LIVE indicator** -- A red pulsing dot when the match is in-play.
- **Last update** -- How recently the scraper received odds data for this match.

### WebSocket Status
At the top of the page, you'll see the WebSocket connection status:
- **Connected** (green) -- Real-time updates are flowing.
- **Disconnected** (red) -- The connection to the backend is down. The page will try to reconnect automatically.

### Which Matches are Tracked?
PHOENIX only tracks **international and major franchise matches:**
- ICC events (World Cup, Champions Trophy, T20I, ODI, Test series)
- Major franchise leagues: IPL, BBL, PSL, CPL, The Hundred, SA20, MLC
- Simulated, virtual, or esports matches are automatically filtered out.

---

## Advisor Page

The advisor page (`/advisor`) is where the magic happens after graduation.

### Before Graduation
When the agent hasn't graduated yet, this page shows:
- **Lifecycle State** -- Which phase the system is in (Accumulating, Offline Training, Online Training, Virtual Trading).
- **Data Accumulation Progress** -- How many matches have been collected and how many more are needed before training starts.
- **Model Version** -- Current model checkpoint.
- **Curriculum Stage** -- Which training difficulty level the agent is on.

### After Graduation
Once the agent graduates, this page has three major sections:

#### 1. Drift Warning Banner (if applicable)
If the agent's shadow performance is degrading, you'll see a warning banner at the top:
- **Amber warning** -- Performance is below thresholds (shows day count, e.g. "Day 2/5"). Nightly retraining may fix it.
- **Red alert** -- Auto-demotion triggered. The agent has been reverted to virtual trading.

#### 2. Shadow Trading Performance
This section shows how the agent is performing in virtual bets placed alongside every recommendation:
- **Balance** -- Virtual shadow balance (green = above start, red = below).
- **Shadow P&L** -- Cumulative profit/loss from shadow bets.
- **Win Rate** -- Overall and recent-window (last 100 bets).
- **ROI** -- Return on investment.
- **Sharpe** -- Risk-adjusted return ratio.
- **Drawdown** -- Current drawdown from peak.
- **Confidence Calibration** -- Shows actual win rate by confidence bucket (e.g., bets with 70-80% confidence actually win X% of the time). This tells you how well-calibrated the agent's confidence scores are.
- **Daily P&L Chart** -- Visual bar chart of daily profit/loss over the last 30 days.

#### 3. Live Bet Signals
Each signal card displays:
- **Match** -- The teams and competition.
- **Recommended Action** -- What the agent suggests (e.g., "Back Home (Small)").
- **Confidence** -- A percentage showing how certain the agent is. Higher is better.
  - Green (>70%): High confidence.
  - Yellow (50-70%): Moderate confidence.
  - Red (<50%): Low confidence -- proceed with caution.
- **Action Distribution** -- Bar chart showing the probability the agent assigns to each possible action. This helps you see if the agent is torn between options or strongly favors one.

#### 4. Admin Controls
At the bottom, you can manually demote the agent if you've lost confidence:
- **Demote to Virtual Trading** -- Forces the agent back to proving itself. It must re-graduate (14 consecutive days) before signals resume.

### How to Use Signals
1. **Check the shadow performance first.** If the shadow P&L is trending down or the drift warning is showing, be more cautious with signals.
2. **Check the confidence.** Higher confidence signals are more reliable.
3. **Cross-check with calibration.** If the 70-80% confidence bucket has a 60% actual win rate, the agent is well-calibrated. If it's 40%, the agent is overconfident -- adjust your trust accordingly.
4. **Look at the action distribution.** If the recommended action has 60%+ and the next closest is under 15%, the agent is very sure. If two actions are close (e.g., 35% vs 30%), the agent is less certain.
5. **Consider the match context.** The signal is based on current odds and match data. Use your own judgment alongside it.
6. **You decide.** PHOENIX never places real bets. You execute manually on the betting site if you agree with the signal.

---

## Understanding the Agent Lifecycle

PHOENIX goes through five phases automatically, with the ability to cycle back if performance degrades:

```
1. ACCUMULATING  -->  2. OFFLINE TRAINING  -->  3. ONLINE TRAINING
        |                                              |
        |         (needs 30+ matches with 50+ ticks)   |
        |                                              v
                                              4. VIRTUAL TRADING
                                                       |
                                              (14 consecutive days
                                               meeting all criteria)
                                                       |
                                                       v
                                              5. GRADUATED (Advisor Mode)
                                                       |
                                              (continuous shadow trading
                                               + drift detection)
                                                       |
                                              [if performance drifts]
                                                       |
                                                       v
                                              4. VIRTUAL TRADING (re-prove)
```

### Phase 1: Accumulating (Weeks 1-2)
The scraper runs 24/7, collecting odds data from live international cricket matches. You'll see the accumulation progress on the Advisor page. Training starts automatically once 30+ qualifying matches have been collected with sufficient data.

### Phase 2: Offline Training (Auto-triggered)
The agent trains on historical match data. It goes through curriculum stages:
- **Stage 1 (Pattern Recognition):** Learns basic patterns with simplified actions.
- **Stage 2 (Full Actions):** All 7 betting actions are unlocked.

### Phase 3: Online Training (Auto-triggered)
The agent trains on live incoming data with more advanced curriculum stages:
- **Stage 3 (Live Simulation):** Handles real market dynamics with slippage.
- **Stage 4 (Adversarial):** Tested with noise injection for robustness.

### Phase 4: Virtual Trading (Continuous)
The agent runs in evaluation mode, placing virtual bets on live matches. Every bet is recorded. A graduation check runs daily at midnight UTC.

### Phase 5: Graduated / Advisor Mode + Shadow Trading
The agent has proven itself profitable. Three things now happen in parallel:

1. **Advisor signals** -- The agent generates bet recommendations that appear on the Advisor page. **You make all real betting decisions manually.**
2. **Shadow trading** -- The agent continues placing virtual bets on every signal it generates. This creates a live virtual P&L that proves the agent is still performing well.
3. **Drift detection** -- Every day, the system checks the shadow trading performance against floor thresholds. If performance degrades, warnings appear. If it degrades for 5 consecutive days, the agent is **automatically demoted** back to virtual trading.

### Post-Graduation Safety Net

The shadow trading system ensures graduation isn't a one-way door:

| Check | Threshold | What Happens |
|-------|-----------|-------------|
| Win rate floor | < 50% (rolling 100 bets) | Drift warning |
| ROI floor | < -2% (rolling 100 bets) | Drift warning |
| Sharpe ratio | < 0.50 | Drift warning |
| Drawdown | > 20% | Drift warning |
| 5 consecutive drift days | -- | **Auto-demotion** back to virtual trading |

After demotion, the agent must re-graduate (14 consecutive days meeting all criteria) before generating signals again. The nightly retraining continues to help the agent adapt to new market conditions.

### Nightly Schedule (runs automatically)
| Time (UTC) | Task |
|------------|------|
| 00:00 | Graduation criteria check |
| 01:00 | Refresh training dataset from database |
| 01:30 | Incremental retraining (50,000 steps) |
| 02:00 | Evaluation run (50 episodes) |
| 02:30 | Save model if improved, update dashboard |

**The nightly retraining runs in ALL phases** including after graduation, so the agent is always learning from the latest data.

---

## Reading Bet Signals

When the agent is graduated and showing signals, here's how to interpret them:

### Confidence Score
- **70-100%:** The agent strongly recommends this action. High alignment in its learned patterns.
- **50-70%:** The agent sees an opportunity but isn't fully certain. Consider additional factors.
- **30-50%:** The agent barely favors this over HOLD. Proceed with caution or skip.
- **Below 30%:** Signals below 30% confidence are automatically filtered out and won't appear.

### Action Probability Distribution
This shows what percentage the agent assigns to each of the 7 possible actions. Example:
- BACK_HOME_SM: **62%** (recommended)
- HOLD: 25%
- BACK_AWAY_SM: 8%
- Others: <5% combined

This means the agent is reasonably confident in backing the home team. The 25% HOLD probability suggests some uncertainty, but 62% is a solid signal.

### Tips
- **Don't bet on every signal.** Focus on high-confidence signals (>65%).
- **Check the match yourself.** The agent knows the odds data but you can see things it can't (e.g., weather delays, player injuries announced mid-match).
- **Track your results.** Keep a simple log of which signals you followed and outcomes. This helps you calibrate trust in the system over time.

---

## FAQ

**Q: How long until the agent graduates?**
A: Typically 2-3 months from first starting, depending on how many international matches are played during that period. The system needs match data to train on.

**Q: Can I speed up training?**
A: Training speed depends on the amount of real match data available. More live international matches = faster data accumulation = earlier training start.

**Q: What happens if the internet goes down?**
A: The scraper will reconnect automatically when the connection is restored. Any data gaps are handled gracefully -- the agent trains on whatever data is available.

**Q: Is my money at risk?**
A: No. PHOENIX never places real bets. It only suggests bets after graduation. You always make the final decision and execute manually.

**Q: What does "degraded" health status mean?**
A: It means one component (Redis or the database) is having issues but the system is partially working. Check the health page for details.

**Q: Why does the agent HOLD most of the time?**
A: By design. A good betting agent is patient. It only bets when it identifies a genuine edge in the odds. Holding ~90% of the time is expected and healthy behavior.

**Q: What cricket matches does PHOENIX track?**
A: International matches (ICC events, bilateral series) and major franchise leagues (IPL, BBL, PSL, CPL, The Hundred, SA20, MLC). Simulated, virtual, and esports matches are automatically excluded.

**Q: Does the agent stop learning after graduation?**
A: No. After graduation, the agent continues to learn through nightly retraining (50,000 steps every night at 01:30 UTC using the latest match data). It also continues placing virtual "shadow bets" on every signal it generates, so its post-graduation performance is continuously tracked.

**Q: What is shadow trading?**
A: Shadow trading is when the agent places virtual bets on every signal it recommends after graduation. This creates a parallel virtual P&L that validates whether the agent's recommendations are actually profitable. If shadow performance degrades, the system detects it and can automatically demote the agent.

**Q: What happens if the agent's performance degrades after graduation?**
A: The drift detection system monitors shadow trading performance daily. If rolling win rate drops below 50% or ROI drops below -2% for 5 consecutive days, the agent is automatically demoted back to virtual trading. It must re-prove itself for 14 consecutive days before generating signals again. You can also manually demote the agent at any time from the Advisor page.

**Q: Can I manually demote the agent?**
A: Yes. On the Advisor page, there's a "Demote to Virtual Trading" button in the Admin Controls section. This immediately stops signal generation and forces the agent to re-graduate.

**Q: What is confidence calibration?**
A: The calibration chart on the Advisor page shows how well the agent's confidence scores match reality. For example, if the agent says "70% confidence" on bets and those bets actually win 70% of the time, the agent is perfectly calibrated. If they only win 50% of the time, the agent is overconfident. Use this to adjust how much you trust different confidence levels.
