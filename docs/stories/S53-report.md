# S53 report: harmonised match registry

Date: 2026-11-03. Read-only against the live DB (SELECT inside a READ ONLY transaction). No DDL applied, no writes.

## Files changed
- `shared/naming.py` (new): `canonical_team_name`.
- `scraper/parsers/lotusbook_parser.py`: `SITE_TZ`, `COMPETITION_BY_TEAM`, `competition_for_teams`, `parse_site_time` (module function with an explicit reference clock); `_parse_time`, `_clean_team_name`, `_infer_competition_if_empty` updated.
- `scraper/worker.py`: competition block in `LOTUSBOOK_EXTRACT_JS` (the `.eventHeadName` lookup removed; a `data-competition` attribute is used if present); stale docstrings.
- `backend/db_init.py`: `VIEW_STATEMENTS` (`v_match_odds`, `v_matches`), run after the indexes.
- `tests/unit/test_s53_harmonisation.py` (new, 34 tests).

## Competition
The listing has no per-row competition. The left-menu names are not linked to rows, the only section headings are "In Play" and "Upcoming Events", and the match-page captures have no breadcrumb either. So competition is derived from team names, for teams that play in exactly one listed competition:
- Biratnagar Kings v Janakpur Bolts: Nepal Premier League (row "Today at 6:45 AM")
- Hobart Hurricanes W v Melbourne Renegades W: Big Bash League, Women (row "Tomorrow 8:10 AM")
- St Lucia Kings SRL v Trinbago Knight Riders SRL: Caribbean Premier League SRL (In Play row)
- Toronto Kings v Montreal Warriors: stays empty (neither team is mapped, nothing in the capture names it).

The table is a short curated list (15 team names). It needs extending as new competitions appear. Verified with the real extractor JS in headless Chromium on the fixture: 18 anchors, 14 events emitted. The St Lucia row is suspended (all prices 0) so the parser drops it, as before.

## Names
`canonical_team_name` (trim, collapse spaces, trailing `srl` becomes `SRL`) is applied in the parser's `_clean_team_name`. The view compares team names case- and whitespace-insensitively, so old "Srl" rows still match.

## Timezone finding
- Listing and match-page times are India Standard Time. Evidence: Biratnagar "Today at 6:45 AM" is stored as 01:15 UTC; Toronto's page start "03 Nov 04:30" equals 23:00 UTC on 02 Nov, and its `is_live` flips to true at 23:27 UTC on 02 Nov.
- The parser's "today/tomorrow/dd-mm-yyyy" paths already convert IST to UTC. The "03 NOV 04:30" form was not parsed at all (returned None). It is now parsed in IST, as are naive ISO strings (previously read as UTC).
- Not fixed, flagged: the registry value for Toronto (lb_76321652) is 2026-11-03 17:30 UTC. The page text gives 2026-11-02 23:00 UTC, which fits the live flip. The 17:30 value is in the future while the match is live, so it is wrong. It was not produced by any path in the current parser. It needs a registry correction by the orchestrator.
- Not changed (manager.py is S52's): `new_context` sets no `timezone_id`. The parser no longer depends on the browser timezone, but pinning `timezone_id="Asia/Kolkata"` in `new_context` would make the site rendering explicit. Recommended.

## View SQL
- `v_match_odds`: listing rows from `odds_ticks` (`source = 'lotusbook'`) and match-page rows pivoted from `market_ticks` (`market_type = 'match_odds'`, runner matched to the registry team names). One row per (match_id, time), listing preferred on a tie, ordered by match_id, time. Columns: `match_id, time, back_home, lay_home, back_away, lay_away, source`.
- `v_matches`: registry columns (`competition, match_start_time, is_simulated, training_status, scrape_status`), listing and match-page tick counts, first and last tick, and result (`result_winner, result_type, result_completed_at`).
- Both views are plain SELECTs, compatible with PostgreSQL 15 and TimescaleDB. Neither is applied; `db_init` creates them at startup.

Read-only probe of both SELECT bodies (wrapped in `BEGIN READ ONLY` ... `ROLLBACK`):
- `v_match_odds` for the example matches: the latest lb_76321652 rows (02:30 UTC) show a listing row with back only (1.52 / 2.3, no lay) and a match-page row with lays (back 1.52, lay 1.77; away back 2.3, lay 2.92). Totals across all matches: 4675 listing rows, 25 match-page rows.
- `v_matches`: lb_36267578 has 81 listing ticks and 0 match-page ticks; lb_76108412 has 7 listing and 14 match-page ticks.

## Tests
- `tests/unit/test_s53_harmonisation.py`: 34 passed.
- Full suite (`python -m pytest tests -q -p no:cacheprovider --ignore=tests/e2e`): 910 passed, exit 0, S52's in-progress tests included.

## Not done
- No DB writes, no view applied, no commit.
- Registry competition for existing rows stays empty until the parser runs on new listings.
