# PHOENIX Remediation Stories

Source: forensic audit of `betfair-historical-importer` @ `59d77f4` (2026-11-02), S01–S10.
Second pass (strategy and data-flow review) @ `443c42e` (2026-11-02), S11–S18: see [strategy-review.md](strategy-review.md).
**Third pass, v2 architecture for real-money profit** @ `a902981` (2026-11-02), S35–S42: see [../architecture/profit-architecture.md](../architecture/profit-architecture.md). PPO on synthetic markets is shelved; S21 is superseded by S42; S22 and part of S23 are P2.
Each story is self-contained so that one sub-agent can execute it with no other context.

## How to run a story with a sub-agent

Preferred: `/story-pipeline run wave 1` (dispatches Sonnet executors in parallel), then
`/story-pipeline verify S01,S02,...` (Haiku checks), `/story-pipeline status` at any time.
Manual alternative:

Give the agent this prompt (replace `NN`):

> Read `docs/stories/README.md` (the "Rules for every agent" section) and then
> `docs/stories/SNN-*.md`. Implement that story only. Touch only the files listed under
> **Owned files**. When done, run the **Verification** commands, tick each success
> criterion in your report, and list anything you could not satisfy.

## Rules for every agent

1. **File ownership is strict.** Edit only the files listed under *Owned files* in your story.
   If a change elsewhere is unavoidable, stop and report it instead of editing.
2. **No fabricated numbers.** Never hard-code metrics, CLV, confidence, odds or P&L in
   anything that is persisted, logged as a result, or shown in the UI.
3. Match the surrounding code style (structlog `setup_logging`, type hints, docstrings).
4. Every behavioural change gets a unit test under `tests/unit/` or `tests/rl/`
   (new test files are always allowed, name them `test_sNN_*.py`).
5. Run `python -m pytest tests/unit tests/rl -q` before finishing; no new failures.
   Integration tests needing Redis/DB may be skipped if the services are not running — say so.
6. Do not commit. Leave changes in the working tree; the orchestrator reviews and commits.
7. Do not retrain the production specialists or overwrite anything in `models/`.
   Training runs for verification must write to a temp directory.

## Token budget rules (read first)

- Your story file + this README are your **complete** context. The *Context pack* in each
  story lists every signature, data shape and caller you need. Do **not** explore the repo
  broadly (no `ls -R`, no reading whole unrelated modules, no Explore agents).
- Read an owned file in full only when you are about to edit it. For anything else use the
  line ranges given in the context pack (`Read` with `offset`/`limit`) or a targeted `grep`.
- If the context pack is wrong or missing something, do one targeted grep, fix forward, and
  mention the gap in your report so the planner can improve the story.
- Report format (keep it under ~40 lines): files changed, success-criteria checklist with
  ✅/❌, test command output summary, open issues/items for the orchestrator.

## Shared context pack (applies to every story)

**Environment.** Windows 11, Python 3.11, run commands from repo root
`C:\Users\sohai\Downloads\prediction-claude`. Bash (Git Bash) and PowerShell both exist.
Installed: stable-baselines3, gymnasium, torch, numpy, scikit-learn, redis (asyncio),
SQLAlchemy async + asyncpg, alembic, pytest + pytest-asyncio (`asyncio_mode = "auto"`).
**Not installed:** `sb3_contrib` (no MaskablePPO). Baseline: `python -m pytest tests/unit tests/rl -q`
→ **101 passed in ~26 s** at commit `59d77f4`; **214 passed in ~20 s** at `443c42e` (baseline for S11+). Redis/Postgres may not be running.

**Conventions.** `from __future__ import annotations`; type hints everywhere; module logger via
`from shared.logging import setup_logging; logger = setup_logging("<name>")` and structured
calls `logger.info("event_name", key=value)`. Pydantic v2 models in `shared/schemas.py`.
Tests are plain pytest classes/functions; shared fixtures in `tests/conftest.py`
(`sample_odds_event`, `sample_match_context`, `sample_portfolio`, `sample_odds_history`).
Env tests build ticks with `_make_episode_data(n_steps)` in `tests/rl/test_environment.py`.

**Domain model (`shared/schemas.py`).**

```python
class BettingAction(IntEnum):   # 9 discrete actions
    HOLD=0; BACK_HOME_SM=1; BACK_HOME_LG=2; BACK_AWAY_SM=3; BACK_AWAY_LG=4
    LAY_HOME_SM=5; LAY_AWAY_SM=6; LAY_HOME_LG=7; LAY_AWAY_LG=8   # note: LAY order differs from BACK
class OddsEvent(BaseModel):     # one scraped tick
    match_id; timestamp; team_home; team_away; competition=""
    back_home/lay_home/back_draw/lay_draw/back_away/lay_away: Optional[float] (>1.0)
    volume_back_home/volume_lay_home/volume_back_away/volume_lay_away: Optional[float]
    is_live=False; scheduled_time; score_text=""; source="lotusbook"; scrape_method; scrape_latency_ms
    @classmethod from_redis_data(data) -> OddsEvent | None
class MatchContext(BaseModel):  # score state
    match_id; timestamp; is_live; score; wickets; overs; run_rate; required_run_rate; innings
    balls_remaining; max_overs=20.0; max_balls=120; batting_team; bowling_team; status; match_format="T20"
class VirtualBet(BaseModel):
    id: Optional[str]; match_id; placed_at; action: BettingAction; team; odds (>1); stake (>0)
    confidence; settled_at; outcome: BetOutcome=PENDING; profit_loss=0.0; closing_odds; clv
    agent_version=""; observation_hash=""; reward=0.0
class PortfolioState(BaseModel):
    initial_balance; current_balance; open_positions; total_exposure; session_pnl; daily_pnl
    total_bets; total_wins; consecutive_streak; time_since_last_bet
    win_rate / roi / exposure_pct  (properties)
```

**Exchange maths.** BACK stake S at odds o: win → +S(o−1), lose → −S. LAY stake S at o:
win (selection loses) → +S, lose → −S(o−1); *liability* = S(o−1). Back price < lay price on
the same selection. Green-up of back S@o₁ by laying at o₂: lay stake S·o₁/o₂, locked P&L
S(o₁/o₂ − 1) on both outcomes. Commission (2 %) is charged on net positive market winnings.

**Training tick dict (corpus / env input).** Keys: `match_id, time|timestamp, team_home,
team_away, competition, match_format, back_home, lay_home, back_away, lay_away,
implied_prob_home, implied_prob_away, overround, is_live, score, wickets, overs, run_rate,
req_run_rate, innings, balls_remaining, batting_team, bowling_team, status,
volume_{back,lay}_{home,away}, source`, and `_meta: {winner, loser, winner_is_home, gender}`
(settlement only). Corpus file `data/specialists/corpus_<key>.json.gz` = list of match dicts
`{match_id, team_home, team_away, competition, match_format, gender, specialist_key, winner,
loser, start_time, total_ticks, ticks: [...]}`. ≈106 ticks/match on average.

**Feature pipeline (do not modify).** `FeaturePipeline().compute(match_id, event: OddsEvent,
context: MatchContext|None, portfolio: PortfolioState, position_state: dict) -> np.ndarray[50]`.
`position_state` keys read by `features/extractors/position_features.py`: `bankroll,
net_home_exposure, net_away_exposure, back_home_odds, lay_home_odds, back_away_odds,
lay_away_odds, best_entry_home, best_entry_away, best_entry_lay_home, best_entry_lay_away`.
The pipeline is stateful per `match_id` (120 s lookback).

**Specialist keys.** `t20i_men, t20i_women, odi_men, odi_women, test_men, ipl, bbl_men,
wbbl_women, t20_blast, franchise_t20, first_class, list_a` (+ legacy `t20i, odi, test`).
Model files `models/specialists/<key>_v1.zip`, registry `models/specialists/metadata.json`
via `rl/model_zoo.py: ModelZoo.register_specialist(key, model_path, format_name,
matches_count, metrics)`.

## Story list

Model tier = suggested executor. **Sonnet** implements every story (each needs design judgement or exchange maths).
**Haiku** is cheap enough for the post-story check: run Verification, diff owned-vs-touched files, tick criteria.

| ID | Title | Priority | Tier | Depends on | Owned files (summary) |
|----|-------|----------|------|-----------|------------------------|
| [S01](S01-remove-outcome-leakage.md) | Remove outcome leakage from synthetic odds | P0 | Sonnet | — | `scripts/cricsheet_harmonizer.py`, `scripts/prepare_specialist_datasets.py`, `scripts/generate_realistic_historical_corpus.py` |
| [S02](S02-honest-holdout-evaluation.md) | Honest model-driven holdout evaluation | P0 | Sonnet | — | `scripts/train_format_specialists.py` (evaluation + persistence functions only) |
| [S03](S03-environment-execution-realism.md) | Environment execution realism | P0 | Sonnet | — | `rl/environment.py` |
| [S04](S04-reward-function-rework.md) | Reward function rework | P0 | Sonnet | S03 (step_info contract) | `rl/reward.py` |
| [S05](S05-risk-and-sizing.md) | Risk limits, liability accounting and sizing | P0 | Sonnet | — | `virtual_trading/engine.py`, `virtual_trading/risk_manager.py`, `virtual_trading/portfolio.py`, `shared/constants.py` |
| [S06](S06-live-execution-path.md) | Live execution path correctness | P1 | Sonnet | S05 | `virtual_trading/live_trading_loop.py`, `scraper/manager.py`, `rl/model_zoo.py` |
| [S07](S07-retraining-and-promotion.md) | Retraining pipeline + promotion gate | P1 | Sonnet | S01, S02 | `scripts/weekly_cricsheet_sync_and_retrain.py`, `rl/graduation.py` |
| [S08](S08-timescaledb-hardening.md) | TimescaleDB retention, chunks, keys, pools | P0 (retention) / P1 | Sonnet | — | `backend/db_init.py`, `alembic/versions/002_*.py`, `backend/models/{odds,bets}.py`, `shared/db.py`, `shared/config.py` |
| [S09](S09-training-regime.md) | Training regime: scale, sampling, normalisation | P1 | Sonnet | S02, S03, S04 | `scripts/train_format_specialists.py` (training functions only), `rl/agent.py` |
| [S10](S10-redis-streams-migration.md) | Durable tick bus: Redis Streams | P2 | Sonnet | S06 | `shared/redis_client.py`, `features/feature_cache_consumer.py` + consumer wiring |
| [S11](S11-feature-train-serve-parity.md) | Feature pipeline train/serve parity | P0 | Sonnet | — | `features/pipeline.py`, `features/normalizer.py` |
| [S12](S12-live-decision-cadence.md) | Live decision cadence matches training | P0 | Sonnet | — (S11 first is nicer) | `virtual_trading/live_trading_loop.py` (handler), new `virtual_trading/decision_gate.py` |
| [S13](S13-synthetic-market-null-test.md) | Synthetic market without free edge + null test | P0 | Sonnet | — | `scripts/prepare_specialist_datasets.py`, `scripts/cricsheet_harmonizer.py`, new `scripts/synthetic_market.py`, `scripts/audit_synthetic_edge.py` |
| [S14](S14-fair-value-model-and-value-policy.md) | Calibrated fair-value model + value policy | P1 | Sonnet | (S13 format) | new `strategy/*`, `scripts/train_fair_value.py`, evaluator dispatch in `scripts/train_format_specialists.py` |
| [S15](S15-metrics-and-graduation-correctness.md) | One CLV/Sharpe/drawdown; honest graduation | P0 | Sonnet | — | new `shared/metrics.py`, `virtual_trading/settlement.py`, `rl/graduation.py`, `rl/lifecycle/graduation_handler.py`, `backtesting/{backtester,report}.py`, `shared/constants.py` (GRADUATION_CRITERIA), `backend/services/agent_service.py` (CLV) |
| [S16](S16-recorded-real-odds-corpus.md) | Recorded real-odds corpus export + replay | P0 | Sonnet | — | new `scripts/export_recorded_corpus.py`, `data/recorded/README.md` |
| [S17](S17-model-provenance-gate.md) | Model zoo provenance gate | P0 | Sonnet | — | `rl/model_zoo.py` |
| [S18](S18-rebuild-and-honest-report.md) | Rebuild everything + honest strategy report | P0 (ops) | Sonnet | S11, S13–S17 | new `scripts/rebuild_all.py`, `docs/reports/`, new model/data dirs |
| [S19](S19-optional-confidence-in-engine.md) | Engine accepts "no confidence"; stop persisting 0.5 | P1 | Sonnet | S12 | `virtual_trading/engine.py`, `virtual_trading/live_trading_loop.py` (one call), `virtual_trading/bet_persister.py` |
| [S20](S20-graduation-wiring-and-sharpe-cleanup.md) | Graduation per live model; last `sqrt(252)` | P1 | Sonnet | S15 | `rl/orchestrator.py` (nightly check), `virtual_trading/shadow_trader.py` (Sharpe) |
| [S21](S21-graduation-streak-per-version.md) | Graduation streak tracked per model version | P1 | Sonnet | S20 | `rl/graduation.py`, `rl/lifecycle/graduation_handler.py` |
| [S22](S22-calibrated-null-market-and-positive-control.md) | Calibrated null market, calibration audit, positive-control market | P0 | Sonnet | S13, S14, S18 | `scripts/synthetic_market.py`, `scripts/prepare_specialist_datasets.py`, `scripts/cricsheet_harmonizer.py`, new `scripts/audit_market_calibration.py` |
| [S23](S23-s18-followups-hygiene.md) | S18 follow-ups: test_men audit, historical table, warnings, fair-value naming | P1 | Sonnet | S18, after S22 | `scripts/audit_outcome_leakage.py`, `features/pipeline.py`, `scripts/export_recorded_corpus.py`, `scripts/train_fair_value.py`, `scripts/rebuild_all.py`, `strategy/fair_value.py` |
| [S35](S35-real-price-corpus-from-betfair-files.md) | Real-price corpus from Betfair historical files | P0 | Sonnet | — (operator downloads files to run) | `scraper/parsers/betfair_historical.py`, `scripts/import_betfair_data.py`, new `scripts/build_real_price_corpus.py` |
| [S36](S36-conservative-ball-time-alignment.md) | Conservative Cricsheet ↔ price alignment | P0 | Sonnet | S35 | new `scripts/align_ball_by_ball.py` |
| [S37](S37-recording-reliability.md) | Record every real match; exclude SRL/synthetic; coverage report | P0 | Sonnet | — | `scraper/manager.py` (recording gate), `scraper/match_filter.py`, `scripts/export_recorded_corpus.py`, new `scripts/recording_health.py`, `docs/runbooks/recording.md` |
| [S38](S38-fair-value-v2-ratings.md) | Fair value v2: ratings, context, walk-forward, vs market | P0 | Sonnet | — (S35/S36 for the market comparison) | `strategy/fair_value.py`, new `strategy/ratings.py`, `scripts/train_fair_value.py`, new `scripts/eval_fair_value_vs_market.py` |
| [S39](S39-real-price-backtester.md) | Real-price event-driven backtester, conservative execution | P0 | Sonnet | S35 | new `backtesting/real_backtester.py`, `backtesting/execution.py`, `strategy/base.py`, `scripts/run_real_backtest.py` |
| [S40](S40-strategy-engine-v2-live.md) | Signal engine: manual-bet signals from the backtested strategy (PPO shadow) | P1 | Sonnet | S39, S38 | `strategy/value_policy.py`, new `strategy/runner.py`, `virtual_trading/live_trading_loop.py` (decision), `shared/constants.py` (STRATEGY/EXECUTION) |
| [S41](S41-betfair-exchange-adapter.md) | Betfair market-data adapter (read-only), closing price | P1 | Sonnet | S40 | `scraper/parsers/betfair_parser.py`, new `scraper/betfair_stream.py`, `scraper/betfair_store.py`, `scraper/match_mapper.py` |
| [S42](S42-go-live-gate-and-kill-switch.md) | Signal trust gate (CLV vs Betfair close) + signal kill switch | P1 | Sonnet | S40 (S41) | `rl/graduation.py`, `rl/lifecycle/graduation_handler.py`, `shared/constants.py` (GRADUATION/KILL), new `virtual_trading/kill_switch.py`, `virtual_trading/risk_manager.py` |
| [S24](S24-in-house-cricsheet-elo-engine.md) | In-house chronological Cricket Elo engine from Cricsheet | P1 | Sonnet | — | scripts/compute_cricsheet_elo.py, shared/elo.py, 	ests/unit/test_s24_cricsheet_elo.py |
| [S25](S25-venue-and-toss-priors-engine.md) | In-house venue par and toss prior engine from Cricsheet | P1 | Sonnet | — | scripts/compute_venue_priors.py, shared/venue_priors.py, 	ests/unit/test_s25_venue_priors.py |
| [S26](S26-observation-vector-expansion.md) | Observation vector expansion: incorporate Elo, venue, and toss priors | P1 | Sonnet | S24, S25 | shared/constants.py, eatures/pipeline.py, eatures/extractors/prior_features.py, 	ests/unit/test_s11_feature_parity.py, 	ests/unit/test_features.py |
| [S27](S27-fair-value-ev-gating-in-live-engine.md) | Fair-value probability integration and EV value gating in live trading loop | P0 | Sonnet | S14, S26 | irtual_trading/live_trading_loop.py, irtual_trading/engine.py, shared/constants.py, 	ests/unit/test_s27_ev_gating.py |
| [S28](S28-sanctioned-betfair-historical-importer.md) | Sanctioned Betfair historic data pipeline and TimescaleDB ingestion | P2 | Sonnet | — | scripts/import_betfair_data.py, 	ests/unit/test_s28_betfair_importer.py |
| [S29](S29-player-at-crease-resource-tracking.md) | Player-at-crease impact and top-order resource tracking from Cricsheet | P1 | Sonnet | S24 | scripts/compute_player_roles.py, shared/player_registry.py, eatures/extractors/match_stats_features.py, 	ests/unit/test_s29_player_resources.py |
| [S30](S30-specialist-zoo-retraining-and-benchmark.md) | Specialist Zoo retraining and comparative holdout evaluation on expanded features | P1 | Sonnet | S26, S29 | scripts/train_format_specialists.py, scripts/benchmark_offline_improvements.py, 	ests/unit/test_s30_retraining_pipeline.py |
| [S31](S31-adverse-selection-and-placement-delay.md) | Real-world bet placement delay and adverse selection simulator | P0 | Sonnet | — | irtual_trading/live_trading_loop.py, irtual_trading/engine.py, 
l/environment.py, 	ests/unit/test_s31_adverse_selection.py |
| [S32](S32-cross-match-portfolio-risk-limits.md) | Cross-match portfolio exposure limits and concurrent event circuit breakers | P0 | Sonnet | — | irtual_trading/risk_manager.py, irtual_trading/portfolio.py, 	ests/unit/test_s32_portfolio_risk.py |
| [S33](S33-closing-line-value-clv-auditor.md) | Closing Line Value (CLV) and price efficiency auditor | P1 | Sonnet | — | irtual_trading/settlement.py, shared/metrics.py, scripts/audit_clv.py, 	ests/unit/test_s33_clv_auditor.py |
| [S34](S34-frontend-ui-ratings-and-ev-transparency.md) | Frontend UI transparency: Display Team Elo, Venue Priors, and Real-Time EV Breakdown | P2 | Sonnet | S26, S27 | ackend/routers/match_router.py, ackend/routers/trading_router.py, rontend/app/matches/page.tsx, rontend/app/trading/page.tsx, 	ests/unit/test_s34_ui_endpoints.py |

## Parallelisation waves

Stories in the same wave own disjoint files and can run concurrently.

- **Wave 1 (parallel):** S01, S02, S03, S05, S08
- **Wave 2 (parallel):** S04 (needs S03 merged), S06 (needs S05 merged), S07 (needs S01+S02)
- **Wave 3:** S09 (needs S02, S03, S04) — S02 and S09 share a file, never run them together
- **Wave 4:** S10
- **Wave 5 (parallel):** S11, S12, S13, S14, S15, S16, S17. These are disjoint files. S14 edits only the policy dispatch in
  `scripts/train_format_specialists.py`, and no other wave-5 story touches that file.
- **Wave 6:** S18 (long-running rebuild and report; needs wave 5 committed)
- **v2 waves (real-money architecture):**
  - **Wave 10 (parallel):** S35, S37, S38
  - **Wave 11 (parallel):** S36 (needs S35), S39 (needs S35)
  - **Wave 12 (parallel):** S40 (needs S38, S39), S41 (needs S40's runner only at integration; can start with S40)
  - **Wave 13:** S42 (shares `shared/constants.py` with S40)
  - Operator steps between waves: download Betfair historical files before running S35/S36/S39 for real; keep the recorder running after S37; provide a Betfair app key before S41 live paper trading.
- **Operator decisions 2026-11-02:** no automated betting (the app emits signals; bets are placed by hand, so backtests use 60 s manual latency); scraping is automated (S37 watchdog + scheduled task); the operator downloads Betfair historical files. **Executor tier for the merged plan: Haiku** (operator request).
- **Merged plan (v2 stories S35–S42 + the other set's S24–S34), recommended order. This supersedes the v2 waves above and the other set's Waves 10–11:**
  - **Wave 10 (parallel):** S35 real corpus, S37 recording, S24 Elo, S25 venue/toss priors
  - **Wave 11 (parallel):** S36 alignment, S38 fair value v2 (needs S24, S25), S39 real backtester, S32 portfolio risk, S33 CLV auditor
  - **Wave 12 (parallel):** S40 strategy engine, S41 Betfair adapter, S29 player resources (as S38 v3 features)
  - **Wave 13:** S42 go-live gate + kill switch (after S32)
  - **Later / P2:** S34 UI, S28 (Betfair → DB ingestion, after S35), S22, S23
  - **Shelved:** S26 (observation expansion), S30 (zoo retraining). Both feed PPO trained on the synthetic market, which pays a no-skill policy (S18 report, S22 evidence).
  - **Superseded:** S27 by S40 (same goal; S40 runs the backtested strategy object). S31 by S39's execution model plus S40's latency-matched placement. Both also own `live_trading_loop.py` and `engine.py`, which S40 owns.

| Other-set story | Verdict | Reason |
|---|---|---|
| S24 Elo engine | Keep, P0, wave 10 | Strength ratings are thesis 1; S38 consumes them |
| S25 venue/toss priors | Keep, P0, wave 10 | Same; S38 consumes them |
| S26 observation 50→54 | Shelve | Only feeds PPO observations; the synthetic training market is not a null market |
| S27 EV gating in live loop | Superseded by S40 | Same intent; S40 shares the backtested strategy interface and owns the same files |
| S28 Betfair → TimescaleDB | P2, after S35 | S35 builds the file corpus the backtests need; DB ingestion is optional. Owns `scripts/import_betfair_data.py` (S35 no longer edits it) |
| S29 player-at-crease | Keep, P1, wave 12 | Useful fair-value features (S38 v3), not PPO features |
| S30 zoo retraining | Shelve | Synthetic-market RL; results cannot show skill |
| S31 adverse selection / delay | Fold into S39 + S40 | S39 models latency, bet delay and adverse fills; S40 matches live placement delay to it |
| S32 cross-match portfolio risk | Keep, P1, wave 11 | Needed before any real money; run before S42 |
| S33 CLV auditor | Keep, P1, wave 11 | Must use `shared.metrics.clv_prob` and prefer the Betfair close (S41) |
| S34 UI transparency | Keep, P2 | After S38/S40 so it shows the real strategy's numbers |
- **Wave 9 (P2 after v2 plan):** S22, then S23 (they share files, so sequential), then rerun S18. S22 is the blocker for any conclusion about edge.
- **Wave 8:** S21 (any time before a real-money step; independent of S18).
- **Wave 7 (done, S19 and S20):** S19, S20. Disjoint from each other and from S18. S19 changes sizing only for callers that relied on the old 0.5 default, so land it before S18's rebuild.
- **Wave 10 (Authentic Open-Source Intelligence & Value Gating):**
  - **Phase 10A (parallel):** S24 (Elo Engine), S25 (Venue/Toss Priors), S28 (Sanctioned Betfair Importer) own disjoint files and execute simultaneously.
  - **Phase 10B:** S26 (Observation Expansion 50→54) follows S24 + S25.
  - **Phase 10C:** S27 (Fair-Value EV Gating & Kelly Sizing) integrates the calibrated probability model and expanded observations into the live trading loop.
- **Wave 11 (Execution Realism, Player Intelligence, Risk & UI Transparency):**
  - **Phase 11A (parallel):** S29 (Player Resource Tracking), S31 (Adverse Selection Simulator), S32 (Cross-Match Portfolio Risk), S33 (CLV Auditor) own disjoint files and can execute concurrently.
  - **Phase 11B:** S30 (Specialist Zoo Retraining & Benchmark Report) runs across the model zoo using candidate staging directories.
  - **Phase 11C:** S34 (Frontend UI Transparency) surfaces Elo badges, venue par run-rates, and EV edge breakdown in the Next.js UI.

After each wave: run the full unit suite, review diffs, commit one commit per story.

## Shared contract: `step_info` (owned by S03, consumed by S04)

S03 must emit, and S04 may rely on, these keys from `CricketBettingEnv.step()`:

| Key | Type | Meaning |
|-----|------|---------|
| `action` | int | Action actually executed (after masking) |
| `bet_placed` | bool | A bet was filled this step |
| `bet_stake` | float | Stake of the filled bet (0 if none) |
| `bet_liability` | float | Max loss of the filled bet: stake for BACK, stake·(odds−1) for LAY |
| `bankroll` | float | Realised cash balance |
| `equity` | float | bankroll + spread-aware mark-to-market of open bets |
| `equity_delta` | float | equity change since previous step (after commission estimate) |
| `peak_equity` | float | running max of equity this episode |
| `current_drawdown` | float | 1 − equity/peak_equity |
| `total_liability` | float | **net** worst-case loss of the open book: `max(0, -worst_case_pnl)` (as implemented by S03) |
| `gross_liability` | float | sum of per-bet liabilities of open bets (added by S03) |
| `worst_case_pnl` | float | min over outcomes (home wins / away wins) of book P&L |
| `best_case_pnl` | float | max over outcomes of book P&L |
| `clv_delta` | float | stake-weighted change in CLV of open bets since previous step, divided by bankroll; **positive = favourable** (BACK `stake·(1/close − 1/entry)`, LAY `stake·(1/entry − 1/close)`) |
| `profit_loss` | float | realised P&L settled this step, net of commission |
| `bet_settled` | bool | any settlement this step |
| `commission_paid` | float | commission charged this step |
| `bets_this_episode` | int | count of filled bets |
| `episode_done` | bool | terminal step |
| `win_rate` | float | portfolio win rate (kept: read by `rl/callbacks.py:228`) |
| `roi` | float | portfolio ROI (kept: read by `rl/callbacks.py:229`) |

Existing readers of `info["step_info"]` that must keep working: `rl/callbacks.py:227-229,330-331`
(`win_rate`, `roi`, `bankroll`), `backtesting/backtester.py:168-175,226-230,280+`
(`profit_loss`, `bet_settled`, `bankroll`).

Keys removed from the old contract: `hedge_locked_pnl`, `is_hedge`, `clv_improvement`,
`unrealized_pnl_delta`, `bets_last_hour`. S04 must not read them.

Notes from S03 (committed `0b7e921`):
- `rl/environment.py` still emits those legacy keys (plus `net_exposure_*`, `odds_change`,
  `exposure_pct`, `episode_returns`) marked `# TODO(S04)`. S04 does not own the env, so it
  just stops reading them; removing them is a follow-up.
- On the terminal step the book is cleared after settlement, so `worst_case_pnl`,
  `best_case_pnl` and `total_liability` read 0 there.
- `exchange_commission` defaults to 0.0; realistic training must pass 0.02.
- Default `episode_sampling` is `"random"`; deterministic evaluation passes `"sequential"`.

## Contract: decision tick (owned by S12, used by S11/S16)

A live tick is a *decision tick* when any of these hold:
- it is the first tick of the match;
- `innings` changed;
- `wickets` increased;
- `floor(overs)` changed;
- `score` rose by ≥ 4 since the last decision tick;
- there has been no decision tick for 180 s.

Without context, only the time rule applies. Only decision ticks are fed to `FeaturePipeline.compute` and to the policy. Every tick still updates `_latest_events` for fills.

## Contract: real tick (owned by S35, used by S36, S38, S39)

One dict per price update: `time`, `market_id`, `match_id` (`bf_<market_id>`), `team_home`, `team_away`, `competition`, `match_format`, `family`, `in_play`, `ltp_home`, `ltp_away`, `ltp_draw`, `back_home`, `lay_home`, `back_away`, `lay_away` (None when the file tier has no ladder; never filled from ltp), `traded_volume_home/away`, `source`, `data_tier`, `market_model="real"`. Result only in `_meta` on the first tick. S36 adds state fields (`score`, `wickets`, `overs`, `innings`, `target`, `balls_remaining`, `run_rate`, `req_run_rate`) as of the conservatively released time.

## Contract: strategy interface (owned by S39, implemented by S40)

`strategy/base.py`: `Strategy.on_tick(tick, position) -> list[OrderIntent]`, `Strategy.reset(meta_without_result)`; `OrderIntent(side, selection, price_limit, stake, reason)`. The backtester and the live runner call strategies only through this interface.

## Out of scope (research backlog, no story yet)

- Feed S14 `win_prob` into live `engine.propose` for Kelly sizing (after S18 shows the fair model beats market-implied on recorded odds).
- Fair-value edge as an observation feature (changes `OBSERVATION_SIZE`).
- Collapse 15 specialists into 3 format families if S18 confirms they generalise better.
- Weekly `export_recorded_corpus.py` in the S07 sync job; keep recorded ticks beyond the 90-day `odds_ticks` retention.
- Sharpe graduation threshold (0.10/day) is a placeholder; calibrate from the S18 report.
- Soft-book vs sharp-line value (needs a genuinely independent bookmaker price; LotusBook match odds mirror the exchange).
- Session/"fancy" runs markets (would need a new scraper target and a runs model).
- Timestamped ball-by-ball feed (would replace S25 estimation).
- `BettingAction` docstrings say 1%/3% but `SMALL/LARGE_STAKE_PERCENT` are 2%/4% (`shared/schemas.py:26-33`).

- Real Betfair historical odds ingestion replacing synthetic prices (this branch's importer).
- Order-book depth / adverse-selection simulator.
- MaskablePPO / offline RL (IQL, CQL) on real ticks; CVaR objectives.
- Cross-match correlation-aware portfolio limits.
