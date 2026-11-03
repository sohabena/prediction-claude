# S50 — Record every live match, SRL included, tagged as simulated

**Priority:** P0 · **Tier:** haiku · **Depends on:** S37, recording commit 9027ce6 · **Wave:** 16A

## Story

As the strategy owner I want every live LotusBook cricket match recorded, simulated (SRL) ones included, so the
decision on which matches to train on can be made later from the data. SRL must be clearly tagged and must never
reach live signals, approval or training unless someone opts in.

## Background (verified 2026-11-03)

- `scraper/manager.py::_on_events` has a hard **SIMULATED GATE** (`self._match_filter.reject_if_simulated(event)` →
  `continue`) before registration and recording. SRL is never registered or recorded.
- `scraper/market_pages.py::eligibility` returns `(False, "simulated")` for SRL, so no match page is opened.
- `scraper/match_filter.py::is_simulated_name` is the one classifier (tokens `srl`, `simulated`, `virtual`,
  `e-cricket`). Team names arrive as "Sydney Thunder SRL" and also "Hyderabad Kingsmen Srl" (mixed case).
- `match_training_status` is the match registry (PK `match_id`). It has no simulated flag.
- Readers of recorded data that must keep excluding SRL by default: `rl/data_loader.py`,
  `backend/repositories/training_repository.py`, `scripts/export_recorded_corpus.py`, `scripts/audit_clv.py`,
  `strategy/trust.py`. Several already use `is_simulated_name`; check each.
- `scripts/recording_health.py` treats simulated rows as contamination (exit 1). That is S55's file: do not edit it.

## Owned files

- `scraper/manager.py` (the simulated gate, `_upsert_training_status`, `_ensure_market_worker` eligibility call only)
- `scraper/market_pages.py` (`eligibility` only)
- `scraper/match_filter.py` (only if a helper is needed)
- `backend/models/match.py` or wherever `MatchTrainingStatus` lives (add `is_simulated` column)
- `backend/db_init.py` (one entry in `MIGRATION_STATEMENTS` adding `is_simulated BOOLEAN NOT NULL DEFAULT false` to
  `match_training_status`, plus a backfill `UPDATE ... SET is_simulated = true WHERE` the name matches the SRL tokens)
- `alembic/versions/004_match_is_simulated.py` (new; chained to `003_market_ticks`; do not run it)
- The five readers above, **only** to add or confirm a default SRL exclusion
- New `tests/unit/test_s50_record_srl.py`

## Tasks

1. Replace the simulated gate: simulated events are **recorded** (odds ticks, pre-match throttle, match context,
   match page) exactly like real ones, but they are never approved, never published to Redis
   (`CHANNEL_MATCH_EVENTS` / match context publish), and never auto-approved. Keep a stats counter
   `recorded_simulated`.
2. `_upsert_training_status` writes `is_simulated` for every match (true for SRL), and `scrape_status` for SRL is
   `simulated` (not `scrape_rejected`, not `scrape_approved`).
3. `eligibility` no longer rejects simulated matches (still requires `is_live`).
4. Model column + db_init statement + alembic migration 004 (not applied).
5. Each reader listed above excludes simulated matches by default, with an explicit opt-in parameter or flag
   (`include_simulated=False`). Do not change any other behaviour of those files.
6. Tests (fakes only, no DB, no browser, no Redis): SRL event is stored; SRL event is never published; SRL is never
   approved even with auto-approve on; registry row has `is_simulated=True` and `scrape_status="simulated"`; a real
   match is unchanged; market-page eligibility accepts a live SRL match; each reader excludes SRL by default.

## Success criteria

- `python -m pytest tests -q --ignore=tests/e2e` passes.
- No SRL event reaches Redis publish or approval in tests.

## Guardrails

No DB writes in tests. Do not run migrations. Do not touch `scripts/recording_health.py`, `scraper/worker.py`,
`scraper/parsers/*`, `docker-compose.yml`. Do not commit.
