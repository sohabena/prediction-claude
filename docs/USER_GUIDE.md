# PHOENIX User Guide

## What is PHOENIX?

PHOENIX is an automated cricket betting analysis system that uses Reinforcement Learning (RL) to identify profitable betting opportunities. It scrapes live odds from LotusBook, trains an AI agent through virtual betting, and after proving consistent profitability, graduates to advisor mode where it recommends bets for you to place manually.

**Important:** PHOENIX never places real bets automatically. After graduation, it suggests bets with confidence scores. You decide whether to act on them.

---

## Dashboard Pages

### Main Dashboard (http://localhost:3000)

The home page shows a high-level overview:
- **Health Status Bar (NEW):** Shows real-time status of API, Redis, Database, and Scraper
- **Training Progress:** Total timesteps trained, episodes, recent win rate and Sharpe ratio
- **Virtual Trading:** Total virtual bets, win rate, P&L, average odds
- **System Status:** Agent version, ROI, wins/losses

The page auto-refreshes every 10 seconds.

### Training Monitor (/training)

Tracks the RL agent's training progress:
- **Summary Cards:** Policy loss, value loss, entropy, current model version
- **Charts:** Episode reward over time, win rate trend, ROI trend

### Virtual Trading (/trading)

Shows the agent's virtual betting performance:
- **Performance Cards:** Total bets, win rate, total P&L, average odds
- **Bet History Table:** Last 50 bets with timestamp, action, team, odds, stake, outcome, and P&L

### Graduation Progress (/graduation)

Monitors the agent's progress toward graduation:
- **Progress Bar:** Consecutive qualifying days out of 14 required
- **Criteria Cards:** Win rate, ROI, Sharpe ratio, max drawdown, profitable days, bet volume, average CLV
- Each criterion shows current value, threshold, and pass/fail status

### Live Matches (/matches)

Displays matches being tracked by the scraper with a **two-phase approval workflow**:

**Step 1: Scrape Approval** — Decide which discovered matches to scrape
- Approve Scraping / Skip buttons for each match
- Shows tick count as data accumulates

**Step 2: Training Approval** — Approve collected data for RL training
- **Validate button** — runs full data quality analysis
- Validation shows: quality score, recommendation (approve/review/reject), live vs pre-match ticks, completeness, odds range, missing data
- Approve / Reject buttons for training
- **📝 Result button (NEW):** Submit manual match result if Cricbuzz API fails

**Features:**
- Filter buttons: All, Discovered, Scraping, Skipped (Step 1) and All, Pending, Approved, Rejected (Step 2)
- WebSocket connection status indicator
- Summary pills showing counts per status

### Advisor (/advisor)

The most important page post-graduation:
- **Lifecycle State:** Shows current state (accumulating, training, virtual trading, graduated)
- **Drift Warning Banner:** Appears if performance is degrading (amber) or demotion is imminent (red)
- **Shadow Trading Performance:** Balance, P&L, win rate, ROI, Sharpe, drawdown
- **Confidence Calibration:** How accurate the agent's confidence scores are
- **Daily P&L Chart:** Last 30 days of shadow trading performance
- **Live Signals:** Active bet recommendations with action, confidence, and probability distribution
- **Manual Demotion:** Admin button to demote the agent back to virtual trading

---

## Agent Lifecycle

The agent progresses through 5 states automatically:

1. **Accumulating** -- Collecting odds data from live matches. Needs 10+ matches with 50+ ticks each.
2. **Offline Training** -- Training the PPO/DQN agent on historical data (500,000 timesteps).
3. **Online Training** -- Curriculum learning through 4 progressive difficulty stages.
4. **Virtual Trading** -- Agent places virtual bets on live matches. Must pass all graduation criteria for 14 consecutive days.
5. **Graduated (Advisor)** -- Agent generates bet signals. Shadow trading continues to validate performance.

If performance drifts after graduation (5 consecutive drift days), the agent automatically demotes back to Virtual Trading.

---

## Reading Bet Signals

When the agent is graduated, the Advisor page shows signals like:

- **Action:** BACK_HOME_SM (back home team, small stake) or LAY_AWAY_LG (lay away team, large stake)
- **Confidence:** How certain the agent is (e.g., 72%)
- **Action Probabilities:** Full distribution across all 9 actions

**Per-Match Budget:** Each match has a default budget of ₹1,00,000. SM actions use ~10% (₹10,000), LG actions use ~25% (₹25,000). Stakes are rounded to human-like amounts (100, 500, 1000, etc.).

**Multi-Account Strategy:** Distribute each recommendation to a different account. One account may profit, another may lose, but the portfolio stays net positive. This avoids bookmaker flagging.

**Guidelines:**
- Only act on signals with confidence > 50%
- Start with small stakes (SM actions) until you trust the system
- Place hedging bets on both sides to lock in profit during volatile moments
- The agent may recommend many bets per match during high-volatility events (wickets, sixes)

---

## FAQ

**Q: How long until the agent graduates?**
A: Depends on match availability. With 2-3 international matches per day, expect 2-4 weeks for data accumulation, 1-2 days for training, and 2-3 weeks for virtual trading validation.

**Q: What if the agent keeps failing graduation?**
A: The orchestrator automatically retrains nightly. If criteria are not met after extended periods, consider adjusting the configuration (lower thresholds or more training steps).

**Q: Does the agent bet real money?**
A: Never. PHOENIX only recommends bets. You must place them manually on LotusBook.

**Q: How should I approve matches for training?**
A: Go to /matches, click **Validate** on each match to see the quality report. Only approve matches with quality score > 85% and sufficient live ticks (30+). The system will block approval if quality is below 50%.

**Q: What is CLV?**
A: Closing Line Value measures if you got better odds than the market's final price. Positive CLV is the strongest indicator of genuine betting skill. PHOENIX now captures closing odds automatically for accurate CLV calculation.

**Q: What if Cricbuzz doesn't have the match result?**
A: Use the **📝 Result** button on the /matches page to submit the result manually. Select the winner, result type (win/tie/draw/no_result/abandoned), and optionally the margin.

**Q: How many accounts do I need?**
A: At least 3-5 accounts for distributing bets. Each recommendation goes to a different account so no single account shows consistently high win rates.
