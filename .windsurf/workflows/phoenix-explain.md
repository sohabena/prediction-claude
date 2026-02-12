---
description: Explain how PHOENIX works - scraping, training, lifecycle, and betting strategy
---
Answer the user's question about how the PHOENIX system works using the information below. If they didn't ask a specific question, give a high-level overview and ask what they'd like to know more about.

---

## How the Scraper Detects Live Matches

The scraper is **fully automatic and polling-based**. No manual intervention is needed.

1. **Continuous Polling**: The scraper polls `https://lotusbook.site/cricket` every 3 seconds (configurable via `SCRAPER_POLL_INTERVAL` in `shared/config.py`).
2. **DOM Parsing**: Each cycle, Playwright loads the page and extracts ALL visible match cards -- team names, current odds (back/lay), and live/upcoming status.
3. **Live Detection**: The scraper checks for keywords like "live", "in-play" in the DOM to determine if a match is currently in progress.
4. **Match Filtering**: The `MatchClassifier` (in `scraper/match_filter.py`) filters events to keep only:
   - **International matches**: National teams (e.g., India vs Australia, England vs South Africa)
   - **Major franchise leagues**: IPL, BBL, CPL, PSL, SA20, The Hundred, MLC, ILT20, LPL, BPL
   - Domestic/club/U19/women's matches are excluded by default (configurable)
5. **Data Storage**: Qualifying odds events are stored in TimescaleDB (`odds_ticks` table) and published to Redis (`match_events` channel) for real-time consumption.
6. **Active Match Tracking**: Redis key `active_matches` is continuously updated with currently live matches.

**Key point**: The scraper runs 24/7 as a background service. It automatically discovers new matches when they appear on the site and stops tracking them when they finish.

---

## How the Model Trains (The Orchestrator Lifecycle)

Training is **fully autonomous** -- managed by the Orchestrator (`rl/orchestrator.py`). The lifecycle has 5 states:

### State 1: ACCUMULATING (Data Collection)
- **What happens**: The orchestrator checks TimescaleDB every 5 minutes for sufficient training data.
- **Trigger to advance**: When 10+ matches have 50+ odds ticks each (configurable in `shared/config.py`).
- **No manual action needed** -- the scraper feeds data continuously.

### State 2: OFFLINE_TRAINING (Initial Model Training)
- **What happens**: The `MatchDataLoader` loads historical episodes from the DB. A PPO agent trains for 500,000 steps on this data.
- **Data used**: 74-feature vectors per timestep, including odds movements, momentum indicators, market microstructure, and portfolio state.
- **Trigger to advance**: Training completes successfully.
- **Duration**: Hours to days depending on data volume and hardware.

### State 3: ONLINE_TRAINING (Curriculum Learning)
- **What happens**: The agent progresses through 4 curriculum stages of increasing difficulty:
  1. **Pattern Recognition** -- learns to read odds patterns
  2. **Full Actions** -- learns when to back, lay, or hold
  3. **Live Simulation** -- realistic market conditions with slippage
  4. **Adversarial** -- robustness against noisy/adversarial data
- **Auto-advancing**: Stages advance automatically when performance thresholds are met.
- **Trigger to advance**: All 4 stages passed.

### State 4: VIRTUAL_TRADING (Paper Trading Validation)
- **What happens**: The agent places virtual bets on LIVE matches using real-time odds data.
- **Graduation check**: Runs nightly at 00:00 UTC. Criteria include:
  - 14 consecutive profitable days
  - Win rate above threshold
  - Positive ROI
  - Sharpe ratio above minimum
  - Maximum drawdown within limits
- **Trigger to advance**: All graduation criteria met for 14 consecutive days.

### State 5: GRADUATED (Advisor Mode + Shadow Trading)
- **What happens**:
  - The agent generates **bet signals** (recommended action, confidence score) for each live match.
  - Signals appear on the Advisor dashboard page for the user to act on manually.
  - **Shadow trading** continues: every signal is also placed as a virtual bet to continuously validate performance.
  - **Nightly retraining**: At 01:30 UTC, the agent trains for 50,000 additional steps on new data.
- **Drift detection**: If shadow trading performance degrades for 5 consecutive days (win rate < 50% or ROI < -2%), the agent is automatically **demoted back to VIRTUAL_TRADING** for retraining.
- **Manual demotion**: Admins can also demote the agent via the dashboard or API (`POST /api/advisor/demote`).

---

## Architecture Overview

```
[LotusBook.site] <-- Playwright polling every 3s
        |
    [Scraper] -- filters international matches
        |
   [Redis PubSub] -----> [Frontend Dashboard] (real-time updates)
        |
   [TimescaleDB] <------ odds_ticks, match_context, virtual_bets
        |
   [Orchestrator] -- manages lifecycle, trains PPO agent
        |
   [Backend API] -------> [Frontend Dashboard]
        |                     |
   [Advisor Signals]     [Shadow Trading Metrics]
```

All services run locally via `.\phoenix.ps1 start` or Docker Compose.

---

## Quick Reference

| Question | Answer |
|----------|--------|
| Does the scraper need manual start? | No, it's started automatically by `phoenix.ps1 start` |
| Does training need manual triggering? | No, the orchestrator handles everything automatically |
| When does the agent start giving bet recommendations? | After graduating (States 1-4 complete, could take weeks) |
| Does the agent auto-bet real money? | No, it only suggests bets. The user must approve and place manually |
| What if the agent's performance drops? | Drift detection auto-demotes it for retraining |
| How do I check current state? | `/phoenix-orch` or visit http://localhost:3000/advisor |
| Can I speed up training? | Reduce `min_matches_to_train` in config (not recommended for quality) |
