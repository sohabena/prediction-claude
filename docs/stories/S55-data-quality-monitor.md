# S55 — Data quality monitor for the recording run

**Priority:** P0 · **Tier:** haiku · **Depends on:** none (reads S50's `is_simulated` column if present) · **Wave:** 16A

## Story

As the operator of a 30-day unattended recording run I want an automatic, repeatable check that the scraped data is
clean, harmonised and complete, with a clear daily report and a non-zero exit when something is wrong.

## Background (verified 2026-11-03)

- DB (Docker `phoenix-timescaledb`, db `phoenix_betting`, user `phoenix`): `odds_ticks` (listing prices, change-only),
  `market_ticks` (match-page markets; `market_type` match_odds/bookmaker/session/other; status open/suspended/
  ball_running), `match_context` (score state; only real scores), `match_results`, `match_training_status` (registry).
- `scripts/recording_health.py` (S37) exists and treats simulated rows as contamination (exit 1). From S50 on, SRL is
  recorded on purpose and tagged; that check must become a count, not a failure.
- Known defects to detect: empty `competition` on every row; listing ticks with all lays null while
  `market_ticks` has lays; matches live in `odds_ticks` with no `market_ticks` rows (no match page); results
  `no_result`/`unresolved` with empty winner; live matches with no result 6 h after the last tick; pre-match matches
  rejected thousands of times for "Both back prices are None" (log spam in `logs/scraper_child.log`).

## Owned files

- `scripts/recording_health.py`
- New `scripts/data_quality_monitor.py` (loop wrapper; writes `logs/data_quality.jsonl`, one JSON line per run)
- `docker-compose.yml`: **only** a new `quality-monitor` service in profile `recording` (same image as the scraper,
  `command: python scripts/data_quality_monitor.py --interval 3600`, `restart: unless-stopped`, `./logs:/app/logs`,
  same POSTGRES_* env as the scraper). If the scraper image does not contain `scripts/recording_health.py`, add the
  needed `COPY` lines to `Dockerfile.scraper`.
- `Dockerfile.scraper` (COPY lines only, if needed)
- `docs/runbooks/recording.md` (a short section 8: how to read the report)
- New `tests/unit/test_s55_data_quality.py`

## Tasks

1. Checks (each returns `{name, status: ok|warn|fail, value, detail}`), over a window (default last 24 h):
   - **freshness**: newest `odds_ticks` and `market_ticks` row age; fail if > 15 min while any match is live.
   - **price sanity**: odds outside (1.01, 1000]; crossed book (back > lay on the same side) in either table; zero
     prices stored as numbers (must be NULL).
   - **duplicates**: identical consecutive rows per match in `market_ticks` (dedupe broken).
   - **match-page coverage**: share of live matches in `odds_ticks` that have `market_ticks` rows (warn < 90 %).
   - **lay completeness**: share of live listing ticks with null lays where `market_ticks` has a lay within ±10 s.
   - **score coverage**: live matches with `market_ticks` rows but no `match_context` rows.
   - **registry**: every `match_id` in the tick tables has a `match_training_status` row; empty `competition` count;
     team-name variants for the same match id; `is_simulated` counts if the column exists.
   - **results**: matches whose last tick is > 6 h old with no `match_results` row, or with empty winner.
   - **gaps**: per live match, longest gap between consecutive rows across both tables (warn > 5 min).
   - **SRL**: counted and reported, never a failure.
2. Output: human table to stdout and one JSON line to `logs/data_quality.jsonl`. Exit 0 ok, 1 any fail, 2 warn only.
3. The SQL must be read-only (`SELECT` only) and use parameters, never string-built values.
4. Tests: feed rows through fakes or an injectable query function; no DB. Cover each check's ok/warn/fail path.

## Guardrails

Read-only against the DB. Do not edit scraper code. Do not start containers. Do not commit.
