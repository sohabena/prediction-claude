# PHOENIX Remediation Stories

Source: forensic audit of `betfair-historical-importer` @ `59d77f4` (2026-11-02).
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
→ **101 passed in ~26 s** at commit `59d77f4`. Redis/Postgres may not be running.

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

## Parallelisation waves

Stories in the same wave own disjoint files and can run concurrently.

- **Wave 1 (parallel):** S01, S02, S03, S05, S08
- **Wave 2 (parallel):** S04 (needs S03 merged), S06 (needs S05 merged), S07 (needs S01+S02)
- **Wave 3:** S09 (needs S02, S03, S04) — S02 and S09 share a file, never run them together
- **Wave 4:** S10

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

## Out of scope (research backlog, no story yet)

- Real Betfair historical odds ingestion replacing synthetic prices (this branch's importer).
- Order-book depth / adverse-selection simulator.
- MaskablePPO / offline RL (IQL, CQL) on real ticks; CVaR objectives.
- Cross-match correlation-aware portfolio limits.
