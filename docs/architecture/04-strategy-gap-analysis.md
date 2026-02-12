# PHOENIX Strategy Gap Analysis

## Overview

This document identifies potential gaps, risks, and areas for improvement in the PHOENIX cricket betting RL system. It covers the data pipeline, RL strategy, risk management, and operational concerns.

---

## 1. Data Pipeline Gaps

### 1.1 LotusBook DOM Structure (HIGH PRIORITY)
**Gap:** The scraper uses generic CSS selectors that may not match LotusBook's actual DOM structure.

**Impact:** Scraper may fail to extract any odds data.

**Mitigation:**
- Run E2E browser test (`tests/e2e/test_lotusbook_scraper.py`) to analyze actual DOM
- Manually inspect the site and update selectors in `scraper/worker.py`
- Add WebSocket interception as primary data source (lower latency, more reliable)

### 1.2 Cricbuzz API Stability (MEDIUM)
**Gap:** Cricbuzz doesn't have a documented public API. The enricher relies on undocumented endpoints.

**Impact:** API changes could break match context enrichment.

**Mitigation:**
- Add fallback data sources (ESPN Cricinfo, Cricsheet)
- Cache last known match context and gracefully degrade
- Monitor API response patterns and alert on changes

### 1.3 Match ID Mapping (MEDIUM)
**Gap:** LotusBook and Cricbuzz use different match identifiers. Fuzzy matching by team name may fail.

**Impact:** Match context won't be linked to odds data.

**Mitigation:**
- Build a manual mapping table for major tournaments
- Use multiple signals: team names + scheduled time + format
- Track mapping success rate as a metric

---

## 2. RL Strategy Gaps

### 2.1 Training Data Cold Start (HIGH)
**Gap:** The RL agent needs historical odds data to train, but we start with zero data.

**Impact:** Agent can't train offline until sufficient data is accumulated.

**Mitigation:**
- Run scraper for 2-4 weeks to accumulate data before starting RL training
- During accumulation phase, track and store all odds movements
- Consider synthetic data generation for initial exploration

### 2.2 Reward Sparsity (MEDIUM)
**Gap:** Bets are settled only when matches end (hours apart). Most steps have near-zero reward.

**Impact:** Slow learning, agent may struggle to associate actions with outcomes.

**Mitigation:**
- CLV-based intermediate rewards (compare entry odds to current odds)
- Shape patience reward to keep agent engaged during long HOLD stretches
- Use n-step returns with larger n to bridge temporal gaps

### 2.3 Market Regime Changes (MEDIUM)
**Gap:** Cricket betting markets have different characteristics across formats (T20 vs ODI), tournaments, and seasons.

**Impact:** Agent trained on one regime may fail in another.

**Mitigation:**
- Curriculum Stage 4 (Adversarial) injects noise to test robustness
- Track performance per tournament/format and retrain if metrics degrade
- Include temporal features so agent can learn time-of-year patterns

### 2.4 Simplified Settlement (LOW)
**Gap:** Current settlement in training uses random outcome based on implied probability, not actual match results.

**Impact:** Training signal may not accurately reflect real-world dynamics.

**Mitigation:**
- Phase 2 (Online Fine-Tuning) uses actual match outcomes
- Settlement engine supports multiple methods (match result, CLV, odds-based)
- Regularly compare virtual trading P&L with what would have happened live

---

## 3. Risk Management Gaps

### 3.1 Correlated Exposure (MEDIUM)
**Gap:** Multiple bets on different matches may still be correlated (e.g., same tournament, same team in multiple matches).

**Impact:** Portfolio drawdown could be larger than individual bet limits suggest.

**Mitigation:**
- Add per-tournament exposure limits
- Track team-level exposure (don't over-bet on India across matches)
- Implement correlation-aware position sizing

### 3.2 Bookmaker Detection (HIGH) — UPDATED
**Gap:** Anti-detection measures are implemented but not battle-tested.

**Impact:** Account could be limited or banned after going live.

**Mitigation (Multi-Account Strategy):**
- **Win rate cap REMOVED** — agent optimizes for maximum win rate
- Bets distributed across multiple accounts (1 recommendation per account)
- Accounts see a mix of wins and losses but net positive overall
- Random delays 5-15s before bet placement
- **Human-like stake rounding** — stakes rounded to multiples of 50/100/200/500/1000/2000/5000
- Stake noise +/-20% to avoid robotic consistency
- **Per-match budget of ₹1,00,000** with payout-aware headroom (1.5×)
- **Unlimited bets per match** — real users place many hedging bets
- 15s minimum cooldown between bets (bot pattern avoidance, not opportunity limiting)
- Test detection avoidance with a throwaway account first

### 3.3 Flash Crash Protection (LOW)
**Gap:** No specific handling for sudden extreme odds movements (site glitches, match suspensions).

**Impact:** Agent could place bets at anomalous odds.

**Mitigation:**
- Max single-tick change filter (reject odds changes > 50% in one tick)
- Sanity check: odds must be within historical range for that match
- Add circuit breaker for rapid consecutive losses

---

## 4. Operational Gaps

### 4.1 Monitoring & Alerting (MEDIUM)
**Gap:** Prometheus/Grafana configs exist but custom dashboards aren't built.

**Impact:** Issues may go undetected until manual inspection.

**Mitigation:**
- Create Grafana dashboards for: scraper health, data freshness, agent performance
- Set up alerts for: scraper down > 30s, data stale > 10s, agent win rate < 48%
- Add structured log alerting for error-level events

### 4.2 Model Rollback (LOW)
**Gap:** Model versioning exists but automated rollback is not implemented.

**Impact:** A bad model update could cause losses before manual intervention.

**Mitigation:**
- Implement A/B testing: run new model alongside current best
- Auto-rollback if new model's rolling Sharpe drops below threshold
- Keep last 5 model versions for quick rollback

### 4.3 Database Scaling (LOW -- for now)
**Gap:** TimescaleDB with single-node setup. Not horizontally scalable.

**Impact:** At high tick rates (many concurrent matches), DB writes may bottleneck.

**Mitigation:**
- Compression policy already enabled (7-day window)
- Retention policy at 90 days
- Can add read replicas if needed
- Batch inserts instead of per-tick for high-frequency scenarios

---

## 5. Recommended Priority Order

1. **DOM Selector Tuning** -- Without correct selectors, nothing works
2. **Data Accumulation** -- Run scraper for 2+ weeks before RL training
3. **CLV-Based Settlement** -- Better training signal than random outcomes
4. **Anti-Detection Testing** -- Validate before going live
5. **Monitoring Dashboards** -- Visibility into system health
6. **Correlation-Aware Risk** -- Better portfolio protection
7. **Model Rollback Automation** -- Safety net for live trading

---

## 6. Feature Additions for Future Iterations

| Feature | Complexity | Impact | Priority |
|---------|-----------|--------|----------|
| Multi-market support (Fancy, Over/Under) | High | High | v2.0 |
| Live betting execution | Medium | Critical | Post-graduation |
| Multi-site arbitrage | High | Medium | v3.0 |
| Ensemble of RL agents | Medium | Medium | v2.0 |
| Natural language match commentary features | Medium | Low | v3.0 |
| Transfer learning across formats | Medium | Medium | v2.0 |
