# Runbook: recording real matches (S37)

Goal: every real live cricket match is recorded with LotusBook prices, match state and result,
with simulated (SRL / Virtual / E-Cricket) and synthetic (`cricsheet_official`) rows kept out of
`odds_ticks`. Recording no longer depends on training approval: `SCRAPER_RECORD_ALL` (default on)
stores ticks for every qualifying live match. Approval still decides which matches are published
to live consumers.

All steps below are for the operator. Nothing in the repo runs migrations, deletes rows or
installs the scheduled task on its own.

## 1. Keep the scraper running on Windows

The watchdog `scripts/run_scraper_forever.py` runs the scraper in a child process. It restarts the
child with backoff when it exits, and also when a match is live but no live event has been
processed for `SCRAPER_STALL_MINUTES` (default 10).

Install once, from the repo root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_scraper_task.ps1
```

This registers the task `PHOENIX scraper`, which starts the watchdog at logon and daily at 06:00
(override with `-DailyAt 05:30`). Running the installer again replaces the task.

Check it:

```powershell
python scripts\run_scraper_forever.py --status      # watchdog heartbeat (restarts today, matches live)
python scripts\recording_health.py --days 1          # per-day coverage and freshness
Get-Content logs\scraper_watchdog.log -Tail 20       # watchdog events
```

Uninstall:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_scraper_task.ps1 -Uninstall
# or: Unregister-ScheduledTask -TaskName "PHOENIX scraper" -Confirm:$false
```

Files written under `logs/`: `scraper_watchdog.log`, `scraper_heartbeat.json`, `scraper_child.log`
(scraper stdout and stderr) and `scraper_feed.json` (feed heartbeat from the scraper).

## 2. Apply the S08 migration that creates `odds_ticks_historical`

`scripts/export_recorded_corpus.py` reads `odds_ticks_historical` when it exists and falls back to
`odds_ticks` only when it does not. The full procedure, with backup and verification, is in
[db-migration-002.md](db-migration-002.md). Short form:

```bash
pg_dump -Fc -h localhost -U phoenix phoenix_betting > backup_pre_002.dump
# stop the scraper first (Ctrl+C in its window, or End the "PHOENIX scraper" task)
alembic stamp 001_initial          # existing database only
alembic upgrade head
python -m backend.db_init
```

The migration copies historical rows into `odds_ticks_historical`. It does not delete rows from
`odds_ticks`. Verify with the queries in that runbook, then restart the scraper.

## 3. Move synthetic rows out of `odds_ticks`

`odds_ticks` must hold only LotusBook rows. Rows with `source` in `cricsheet_official`,
`synthetic_cricsheet`, `synthetic_archetype` or `betfair_historical`, and `cric_*` match ids, are
synthetic or not LotusBook. They belong in `odds_ticks_historical` (step 2 copies them there).

Check first:

```sql
SELECT source, count(*) FROM odds_ticks
 WHERE source <> 'lotusbook' OR match_id LIKE 'cric\_%' GROUP BY source;
SELECT count(*) FROM odds_ticks_historical;   -- must cover the rows you are about to remove
```

After a fresh backup, delete them from the live table (operator runs this once, inside a
transaction so you can check the count before committing):

```sql
BEGIN;
DELETE FROM odds_ticks
 WHERE source IN ('cricsheet_official', 'synthetic_cricsheet', 'synthetic_archetype', 'betfair_historical')
    OR match_id LIKE 'cric\_%';
-- check the reported row count against the first query, then:
COMMIT;   -- or ROLLBACK;
```

Simulated matches (`... SRL`, `Simulated ...`, `Virtual ...`, `E-Cricket ...`) that were recorded
before S37 are real rows in `odds_ticks`. Find them with:

```sql
SELECT match_id, team_home, team_away, competition, count(*) FROM odds_ticks
 WHERE team_home ~* '\m(srl|simulated|virtual|e-?cricket)\M'
    OR team_away ~* '\m(srl|simulated|virtual|e-?cricket)\M'
    OR competition ~* '\m(srl|simulated|virtual|e-?cricket)\M'
 GROUP BY 1, 2, 3, 4;
```

Delete them only after the same backup, with the same transaction pattern. The exporter already
excludes them, so deleting is about table hygiene, not correctness.

## 4. Daily check

```powershell
python scripts\recording_health.py --days 7
```

Exit codes:

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Clean and the recorder is fresh | none |
| 1 | Synthetic or simulated rows exist in `odds_ticks` | run step 3 |
| 2 | Watchdog or feed heartbeat is stale or missing | run `--status`, check `logs\scraper_child.log` |

Export the recorded corpus when there is enough data:

```powershell
python scripts\export_recorded_corpus.py --cadence decision
```

With no qualifying matches it prints "No recorded matches" and exits 0.

## 5. Environment settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCRAPER_RECORD_ALL` | `1` | Record every qualifying live match, regardless of approval. Set `0` to restore approval-gated recording. |
| `SCRAPER_STALL_MINUTES` | `10` | Restart the scraper when a live match has no processed event for this long. |
| `SCRAPER_MARKET_PAGES` | `1` | S47: open one read-only match page per live recorded match and store its Winner (match odds) best back/lay, plus Bookmaker and session markets, to `market_ticks`. Set `0` to turn off. |

## 6. Bookmaker and session markets (S47)

Table `market_ticks` (migration `003_market_ticks`, hypertable in `backend/db_init.py`). The migration is
not applied by the story. Apply it with the operator's usual step only after checking the table is empty
or the change is intended. Rows are written only when a market's content changes.

- `market_type`: `match_odds` (the Winner market: best back and best lay per runner), `bookmaker`,
  `session` (names like `session:6_over:India`), or `other`.
- `status`: `open`, `suspended`, or `ball_running`.
- `match_odds` selectors are VERIFIED on a live page (2026-11-03). This matters: for some live matches the
  listing page shows "-" for every lay while the match page shows real lays, so `odds_ticks` (from the
  listing) can be back-only and `market_ticks` holds the lays. Bookmaker and session selectors are still
  UNVERIFIED. See the header of `scraper/parsers/lotusbook_markets.py`.
- Applied to the live database on 2026-11-03 (table created from migration 003's DDL, then made a hypertable).
- Simulated (SRL) matches never get a match page. At most 4 match pages are open at once.

Health query to add to `scripts/recording_health.py` (not edited by S47):

```sql
SELECT match_id, market_type, COUNT(*) AS rows, MAX(time) AS last_seen
FROM market_ticks
WHERE time > NOW() - INTERVAL '1 day'
GROUP BY match_id, market_type
ORDER BY last_seen DESC;
```

### Pre-match match pages (S77)

- A real match gets its read-only match page from `PREMATCH_PAGE_LEAD_MIN` minutes before its start (default
  75, so the T-60m dataset snapshot finds a page row under 15 min old), not only when live. The page is the same one the live recording uses, so its rows go through the same
  dedupe and land in `market_ticks` before the start. SRL matches still open a page only while live.
- Slots: the cap (`SCRAPER_MARKET_WORKER_CAP`, default 12) goes to live matches first. Pre-match pages fill the
  free slots, earliest start first. A live match that finds every slot taken closes the pre-match page with the
  latest start. Live pages are never closed for a pre-match one.
- A pre-match page closes when its match leaves the listing for 45 min, when its start moves more than 30 min
  outside the lead window, or when the match completes. It stays open while the match is not live yet.
- Selection is `plan_market_workers` in `scraper/market_pages.py` (pure, unit-tested in
  `tests/unit/test_s77_prematch_pages.py`).
- Monitor: `scripts/data_quality_monitor.py` check `prematch_pages` warns below 90% of real matches started
  since `PREMATCH_PAGES_SINCE` (set it to the deploy time before the first run that should count) that have
  match-page rows before their start. Matches first listed later than the lead are not scored.
- Dataset (S58, `training/dataset.py`): snapshots read the as-of book from `v_match_odds`, which merges listing
  and match-page rows. A value counts only from a row at or before the snapshot and at most 15 min old. Match-page
  rows are written on change, so with the default lead the page opens about at T-60m and the T-60m snapshot gets
  no match-page lays. A quiet market can also read as empty at T-10m or T-0. A longer lead gives T-60m rows only
  when prices moved within the 15 min before it.

## 7. 30-day recording run in Docker (2026-11-02)

**What runs:** `docker compose --profile recording up -d scraper` starts `phoenix-scraper` (with `phoenix-timescaledb`
and `phoenix-redis`). The container runs the S37 watchdog, which restarts the scraper on a crash or a stall. Docker
restarts the container (`restart: unless-stopped`) whenever Docker Desktop is running.

**What is recorded:** every real match on the LotusBook cricket page (simulated SRL matches are always rejected):

- live matches: prices plus score state (`match_context`) on every change;
- upcoming matches: pre-match prices, only when a price changes (at most every 30 s per match, plus a 15-minute heartbeat).

`SCRAPER_INTERNATIONAL_ONLY` is false for this run. The Redis override `config:scraper:international_only` must also
be `false`: `docker exec phoenix-redis redis-cli SET config:scraper:international_only false`.

**Database state after setup:** migration 002 was applied by hand (alembic is not stamped on this DB):

- `odds_ticks.tick_seq` is a sequence default, because hypertables cannot take an identity column;
- `odds_ticks_historical` exists;
- the `virtual_bets` key is `(placed_at, bet_id)`.

**No retention policy** on `odds_ticks`: recorded LotusBook ticks are the only real price history. `db_init` adds
retention only with `ODDS_TICKS_RETENTION=on`.

Pre-run backup: `data/backups/phoenix_pre_recording_20261102.dump`. Restore with `pg_restore`, as in the 002 runbook.

**Keeping it running for 30 days:**

1. Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**: on. Without it, a reboot stops
   recording until Docker is opened.
2. Sleep: no program can record while the PC is asleep.
   - Idle sleep is already "Never" in this PC's power plan (checked 2026-11-02).
   - Keep the lid open, or set the lid action to "Do nothing" when plugged in:
     `powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0` then `powercfg /setactive SCHEME_CURRENT`.
   - Optional safety net that does not change settings:
     `powershell -ExecutionPolicy Bypass -File scripts\install_keep_awake_task.ps1`. It keeps Windows from idle-sleeping
     only while `phoenix-scraper` runs.
3. Check daily:
   - `docker ps` (phoenix-scraper Up);
   - `python scripts/recording_health.py`;
   - the tick count: `docker exec phoenix-timescaledb psql -U phoenix -d phoenix_betting -c "SELECT date(time), is_live,
     count(*), count(distinct match_id) FROM odds_ticks WHERE source='lotusbook' GROUP BY 1,2 ORDER BY 1 DESC LIMIT 6"`.

## 8. Data quality report (S55)

`scripts/data_quality_monitor.py` runs ten read-only checks over the last 24 h and writes the result to
`logs/data_quality.jsonl` (one JSON line per run). The `quality-monitor` service (profile `recording`) runs it
every hour. Run it once by hand with `python scripts/data_quality_monitor.py`. Exit codes: 0 all ok, 2 warnings
only, 1 at least one failure (or the database could not be read).

| Check | Fails when | Warns when |
|-------|------------|------------|
| `freshness` | a match is live and the newest row is over 15 min old | |
| `price_sanity` | a price is stored as 0, or a book is crossed (back > lay) | a price is outside (1.01, 1000] |
| `duplicates` | over 1 % of `market_ticks` rows repeat the previous row | any repeated row |
| `match_page_coverage` | | under 90 % of live matches have match-page rows |
| `lay_completeness` | over 10 % of live listing ticks have no lay that the match page can fill | over 1 % |
| `score_coverage` | over 10 % of matches with match-page rows have no score state | any |
| `registry` | a recorded match has no registry row, or competition is empty | team-name variants for one match id |
| `results` | a match's last tick is over 6 h old with no result, or a result has an empty winner | |
| `results_missing` (S76) | | a real match's last tick is over 3 h old with no win, tie, no_result or abandoned result |
| `start_time_consistency` (S86) | | a real match's listing-derived and JSON-derived starts differ by over 15 min, or its `actual_start_time` is over 60 min after `match_start_time` |
| `gaps` | | a gap over 5 min inside one live match |
| `srl` | never | never (count only) |

`srl` counts simulated (SRL) matches. They are recorded on purpose, so they never fail a check. `is_simulated`
counts are shown in the `registry` detail once that column exists.

The report is read-only: it runs in a READ ONLY transaction and only executes `SELECT`. It does not repair data.
Each `fail` or `warn` line names the matches to look at. Fix the cause in the scraper or the registry, not in the
report.

## 9. Quarantine, harmonisation and known limits (S57, 2026-11-03)

- The `quality-monitor` container runs `scripts/data_quality_monitor.py --interval 3600 --quarantine`. Before each
  report it moves rows that are not prices of their match from `odds_ticks` to `odds_ticks_quarantine` (kept, with
  `reason`): `post_settlement` (prices after a decided match), `cross_match_copy` (two matches, same time, same book),
  `placeholder_book` (LotusBook's default book, e.g. 1.92/1.99/2.02/2.10, seen on 3+ matches), `after_result` (ticks
  after a trusted result). It then fills missing `competition` values on recent ticks from the registry.
- Dry run any time: `docker exec phoenix-quality-monitor python scripts/quarantine_ticks.py` (add `--apply` to move).
- Match-page workers seed their dedupe state from the last stored rows, so restarts do not rewrite markets.
- Live listing ticks are re-stored every 5 min when unchanged (heartbeat), so a gap means the recorder was down.
- **Known limit: no live score.** The Sportradar scoreboard does not render in headless Chromium and the page's
  score feed returns empty `stats` (S51 report). `score_coverage` therefore warns; it never fails.
- Results come from decisive prices, the market's settled winner, or (filled by hand, `source='public_scorecard'`)
  public scorecards. Matches that vanish without a decisive price are `unresolved`.
- Ticks and matches recorded before match-page recording began (2026-11-03 ~02:26 UTC) cannot have match-page lays;
  the monitor reports them apart instead of scoring them.

## 10. Results: verified wording, missing results, hand-set results (S76)

**Verified wording (2026-11-03).** The first finished `livescore-v2` match is `lb_36267578` (Biratnagar Kings v
Janakpur Bolts). The scraper logged the feed's finished text as `Biratnagar Kings won by 2 runs 🏆` (parsed=true,
`logs/scraper_child.log`, 10:20:55Z), and the match's last `match_context` row is `status = completed`. The parsers
(`scraper/parsers/lotusbook_scoreboard.py`, `scraper/lotus_result_detector.py`) read that text as a win by 2 runs.
Fixture: `tests/fixtures/s76/finished_livescore_v2_lb_36267578.json`. The raw score/v3 body was not logged, so the
field that carried the text (`stats.result` or `stats.status`) is still unknown.

**Winner (match odds) market at the end: NOT VERIFIED.** No stored `market_ticks` row has a WINNER, WON, LOSER or
LOST runner status. Real finished matches end with the Winner market still `open` (lb_36267578, fixture
`winner_market_end_of_real_match_lb_36267578.json`) or `suspended` (lb_76227860). The only `closed` Winner rows are
one real match closed at 05:10Z (runners `suspended`) and SRL matches. So `lotusbook_json.settlement_of` returns no
winner on the data seen so far, and the settled-winner path has never produced a result.

- Settled-market capture: since 2026-11-03 11:31 UTC an open match page stays `MARKET_COMPLETED_LINGER_S` (30 min)
  after its match's result instead of closing at once (before, the Biratnagar page closed 6 s after the score said
  completed). After the next real finish, check the Winner runner statuses in `market_ticks`; until then the
  WINNER/WON wording stays unverified and the scoreboard text is the source of record.
- Tie and no-result wording (`Match tied`, `No result`) is not in any stored feed text yet. Both parsers accept the
  usual forms; that is unverified too.

**Missing results.** `python scripts/results_reconciliation.py` lists real matches (SRL excluded) whose last tick
(odds or market) is over 3 h old with no win, tie, no_result or abandoned result, over the last 7 days. Exit 2 when
any is listed. The paper report has a "Results missing" section with the same list, and the monitor check
`results_missing` warns (its window is the monitor's 24 h). Rows with an `unresolved` result but no tick in the
database (for example lb_36246097, lb_36266985, lb_76292634, lb_76064830) are outside the last-tick rule and are not
listed; decide them by hand.

**Writing a result by hand.** Use this only when a public scorecard gives the result and the scraper did not:

```powershell
python scripts\set_result.py --match lb_<match id> --winner "Melbourne Stars W" --margin "15 runs" --source public_scorecard --url <scorecard url> --dry-run
python scripts\set_result.py --match lb_<match id> --winner "Melbourne Stars W" --margin "15 runs" --source public_scorecard --url <scorecard url>
python scripts\set_result.py --match lb_<match id> --tie        # or --no-result
```

- The winner must match the registry's `team_home` or `team_away` (case and spacing ignored). A trusted result (win,
  tie, no_result, abandoned) is refused unless `--force`, and `--force` is refused when paper bets on the match are
  already settled against it. Each write appends a line to `logs\set_result.jsonl`.
- The write settles the match's filled paper bets at once (`paper_ledger.settle`, the same function the trader runs
  every cycle). Tie and no_result void the bets.
- The detector can still write a row for the same match later (it merges on `match_id`). Check
  `scripts\results_reconciliation.py` after a manual write.

## 11. True off time (S86)

`match_training_status.actual_start_time` and `actual_start_source` record when a match actually started. They are set
once, by the first of: the match JSON's `inPlay` flag, the listing's live badge, the score feed's first ball
(`scraper/actual_start.py`). Migration: `backend/db_init.py` (`ADD COLUMN IF NOT EXISTS`, nullable); the scraper does
not apply it. Backfill of recorded matches (dry run by default; the operator runs `--apply`):

```powershell
python scriptsackfill_actual_start.py          # dry run: table and summary, writes nothing
python scriptsackfill_actual_start.py --apply  # writes the two columns where actual_start_time is NULL
```

