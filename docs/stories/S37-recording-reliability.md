# S37 — Record every real match, keep it clean, prove coverage

**Priority:** P0 · **Tier:** sonnet · **Depends on:** none · **Wave:** 10

## Story

As the strategy owner, I need every real live cricket match recorded with prices, state and result, kept separate from
synthetic and simulated data, so that live paper trading and future models have clean real data.

## Background (measured 2026-11-02 in the local DB)

- **Coverage is tiny:**
  - `odds_ticks` has 4,046 LotusBook rows across 17 matches since 2026-08-22, 3,563 of them from one match;
  - only 2 of 43 `match_results` rows have ticks;
  - `match_training_status` has 28,475 `scrape_approved` rows.
  So approval is not the bottleneck: the recorder is simply not running most of the time, or loses matches.
- **Recording is mixed up with training approval:** `scraper/manager.py:_on_events` L195-440 stores ticks only for
  `scrape_approved` matches (gate around L288), live (`is_live`) and past their scheduled time. Recording should not
  depend on a training decision.
- **Simulated matches are recorded as real:** ids like `Hobart Hurricanes SRL`, `New Zealand SRL` (Simulated Reality
  League) appear in both `odds_ticks` and `match_results`. They are computer-simulated games, not real cricket.
- **Synthetic rows share the real table:** `odds_ticks` holds `source='cricsheet_official'` rows (`cric_odi_*`) written by
  `scripts/cricsheet_harmonizer.py:437 insert_into_database`.
- **The historical table is missing:** `odds_ticks_historical` does not exist in this DB, so
  `scripts/export_recorded_corpus.py` (S16) exits 1. This item moved here from S23.

## Context pack

- `scraper/manager.py` (697 lines):
  - `ScraperManager.__init__` L43; `start` L65; `_load_scrape_approved` L133; `_load_completed_matches` L158;
  - `_listen_for_results` L173; `_on_events` L195-440 (redis auto-approve flag `config:scraper:auto_approve` L209);
  - `_publish_match_context` L443; `_store_odds_tick` L484-552; `_upsert_training_status` L553;
  - `stop` L654; `run_scraper` L674.
- `scraper/match_filter.py:68 MatchClassifier` (`qualifies` L82, `_is_blacklisted` L179): the place to reject simulated
  events.
- `scraper/lotus_result_detector.py:62 LotusResultDetector` (`check_completed` L135, `_infer_result` L200 infers results
  from odds, `_store_and_publish` L286). Results inferred from prices can be wrong; tag them `source="lotusbook_odds"`
  (already the default) and let consumers decide.
- `scripts/export_recorded_corpus.py` (S16): `main` reads `odds_ticks` and `odds_ticks_historical`.
- DB models: `backend/models/odds.py`, `backend/models/results.py`, `backend/models/match_status.py`. Do **not** run
  migrations against any DB; write the steps for the operator.

## Owned files

- `scraper/manager.py` (recording gate only)
- `scraper/match_filter.py`
- `scripts/export_recorded_corpus.py`
- New: `scripts/recording_health.py`
- New: `scripts/run_scraper_forever.py`, `scripts/install_scraper_task.ps1`
- New: `docs/runbooks/recording.md`
- New: `tests/unit/test_s37_recording.py`

## Tasks

1. **Decouple recording from training approval.** Store ticks for every match that passes the classifier and is live.
   Keep `scrape_approved` only for what the trading and training side consumes. Put it behind a setting
   (`SCRAPER_RECORD_ALL=1`, default on), read with `os.getenv`.
2. **Reject simulated events** in `MatchClassifier.qualifies`: token match on `SRL`, `Simulated`, `Virtual`, `E-Cricket`,
   case-insensitive, on team, competition and event names. Count them in `stats()`.
3. **Exporter:**
   - detect a missing `odds_ticks_historical` and continue with `odds_ticks` only;
   - exclude `source != 'lotusbook'` rows and simulated names;
   - exit 0 with a clear "no recorded matches" summary when nothing qualifies.
4. `scripts/recording_health.py` (read-only SQL) reports, per day:
   - matches seen vs matches recorded;
   - ticks per match and the median and p95 gap;
   - the fraction of matches with context and with a result;
   - simulated and synthetic rows present in `odds_ticks`.
   Exit 1 if any synthetic or simulated rows exist in `odds_ticks`.
5. **Automated scraping (operator request 2026-11-02).**
   - New `scripts/run_scraper_forever.py`: a watchdog that runs the scraper (`scraper.manager.run_scraper`) in a child
     process and restarts it with backoff on crash or when no tick has been stored for `SCRAPER_STALL_MINUTES`
     (default 10) while a match is live. It logs to `logs/scraper_watchdog.log` and writes a heartbeat file
     `logs/scraper_heartbeat.json` (last tick time, matches live, restarts today).
   - New `scripts/install_scraper_task.ps1`: registers a Windows Task Scheduler task "PHOENIX scraper" that starts
     the watchdog at logon and every morning, restarting on failure. **Do not run it**: the operator runs it once. It
     must be idempotent and print the uninstall command.
   - `recording_health.py` reads the heartbeat and flags a stale recorder.
6. `docs/runbooks/recording.md`:
   - how to keep the scraper running daily on Windows (Task Scheduler, with restart-on-failure);
   - how to apply the S08 migration that creates `odds_ticks_historical` (exact commands, for the operator to run);
   - how to move the existing `cricsheet_official` rows out of `odds_ticks` (a SQL the operator runs after a backup).

## Success criteria

- [ ] With approval unset, a live qualifying event is stored (test with a fake session).
- [ ] `SRL` and `Simulated` events are rejected and counted.
- [ ] The exporter exits 0 with no historical table and ignores `cricsheet_official` and SRL rows (fake-session test).
- [ ] `recording_health.py` flags synthetic rows (fake-session test).
- [ ] The watchdog restarts a child that exits, and one that stalls (test with a fake child process and a short stall timeout).
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s37_recording.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Running migrations or deleting rows (operator steps in the runbook).
- Betfair live stream (S41).
