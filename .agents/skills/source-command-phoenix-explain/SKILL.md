---
name: "source-command-phoenix-explain"
description: "Explain how PHOENIX works - scraping, training, lifecycle, and betting strategy"
---

# source-command-phoenix-explain

Use this skill when the user asks to run the migrated source command `phoenix-explain`.

## Command Template

Answer the user's question about how the PHOENIX system works using the information below. If they didn't ask a specific question, give a high-level overview and ask what they'd like to know more about.

---

## How the Scraper Detects Live Matches & Enforces Ingestion Governance

The scraper runs continuously in the background, combining automated discovery with strict user governance:

1. **Continuous In-Play Interception**: The scraper connects to `https://lotusbook.cc/cricket` via Chrome DevTools Protocol (CDP) WebSocket interception with Playwright DOM fallback (polling every 3–5 seconds).
2. **Multi-Layer Match Filtering (`scraper/match_filter.py`)**:
   - **Layer 0 Blacklist (Always Wins)**: Automatically checks competition, home team, and away team for `\bsrl\b`, `simulated`, `cyber`, `virtual`, and `esports`. Simulated Reality League (SRL) matches are strictly blocked from tick collection.
   - **Layer 1 Whitelist**: Validates ICC events and Tier-1 franchise tournaments (IPL, BBL, WBBL, CPL, PSL, SA20, The Hundred, MLC, etc.).
   - **Layer 2 Team Database**: Matches national and franchise squads in `ALL_KNOWN_TEAMS`, recognizing women's suffixes (`(w)`, `women`, `w.`).
3. **Two-Phase Approval Gating**:
   - **Scrape Gate**: Newly discovered matches default to `discovered` (Manual Approval Mode). Zero odds ticks are written to the database until explicitly approved by the user or verified as Tier-1 under auto-approval.
   - **Live & Schedule Gates**: Approved matches only record ticks when in-play (`is_live=True`) and within 10 minutes of scheduled start time. Completed matches automatically cease recording.
   - **Training Gate**: Post-match data quality validation ensures completeness $\ge 70\%$ and score $\ge 0.70$ before data enters the RL training corpus.
4. **Runtime Governance Controls**:
   - The user has dynamic toggles on the **Live Matches** dashboard page (`/matches`) to switch between **Manual Approval Mode** (100% manual control) and **Tier-1 Auto-Approval**, or toggle the **Strict International Filter** to instantly hide non-international matches.
5. **Real-Time Data Distribution**: Qualifying odds events stream to Redis (`match_events` channel) and write to TimescaleDB (`odds_ticks` table). Live odds (Back/Lay) and scorecards are cached in Redis `active_matches` with a 120s stale eviction timeout.

---

## The 12-Specialist Model Zoo & Automated Cricsheet Retraining

Rather than a single monolithic model that treats all cricket formats identically, PHOENIX utilizes a **12-Specialist Model Zoo** (`rl/model_zoo.py`) partitioned by format, league, and gender:
- **T20 Specialists:** `ipl`, `bbl_men`, `wbbl_women`, `t20i_men`, `t20i_women`, `t20_blast`, `franchise_t20`
- **ODI Specialists:** `odi_men`, `odi_women`, `list_a`
- **Multi-Day Specialists:** `test_men`, `first_class`

Each specialist is calibrated to format par rates (Women's T20: 136 rpo vs Men's: 168 rpo vs IPL: 188 rpo) and steeper wicket sensitivities in women's cricket.

### Automated Weekly Retraining
Every Monday at 03:00 UTC, the system downloads newly completed matches from Cricsheet (`recently_added_7_json.zip`), deduplicates them against canonical signatures, appends them to the specialist archives, and executes incremental PPO curriculum retraining (4,000 steps per affected specialist).

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
[LotusBook Live] <── Playwright CDP WebSocket & Polling
        │
  [Match Filter] ───► Layer 0 Blacklist (Blocks SRL & Cyber)
        │
   [Scrape Gate] ───► User Approval / Tier-1 Auto-Approval
        │
  [Redis PubSub] ────► [Frontend Dashboard] (Real-time odds, Back/Lay ladder)
        │
  [TimescaleDB]  ◄──── odds_ticks, match_context, virtual_bets
        │
  [Orchestrator] ────► 12 Specialist Model Zoo + Weekly Retraining
        │
  [Backend API]  ────► [Frontend Dashboard]
        │                     │
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
