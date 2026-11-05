# S53 — Harmonised match registry: competition, names, start times, one odds view

**Priority:** P1 · **Tier:** haiku · **Depends on:** S50 · **Wave:** 16B

## Story

As the person choosing training matches later, I want each match described once and consistently (teams,
competition, format, scheduled start in UTC, simulated or not), and one view that gives the best known back/lay for
any moment, whichever page it came from.

## Background (verified 2026-11-03)

- `competition` is empty in every `odds_ticks` and `match_training_status` row of this run. The listing extractor
  (`scraper/worker.py` JS ~L136-152) looks for `link.closest('.eventHeadName')`, which the current listing does not
  have. A real listing page is saved: `tests/fixtures/lotusbook/listing_page_20261103.html` (competition headings
  such as "Nepal Premier League", "Big Bash League, Women", "Caribbean Premier League SRL" are in the left menu; find
  how each match row maps to its competition on the listing, e.g. a section heading above the rows; if the listing has
  no mapping, take it from the match page breadcrumb/title in the S52 worker and say so in your report).
- Team name variants: "Hyderabad Kingsmen Srl" vs "... SRL"; names come from the href, the link text or the title.
- `match_start_time` is parsed from listing text like "Today at 10:00 PM" / "03 NOV 04:30"; check the timezone the
  browser context uses (`scraper/worker.py` `new_context`) and that stored times are UTC. Example to check: Toronto Kings
  v Montreal Warriors lb_76321652 shows "03 Nov | 04:30" on its page and is stored as 2026-11-03 17:30 UTC.
- Lays: for some live matches the listing has back only while `market_ticks` (`market_type='match_odds'`, one row
  per runner, `selection` = team name) has back and lay.

## Owned files

- `scraper/worker.py` (extractor: competition, start time), `scraper/parsers/lotusbook_parser.py`
- New `shared/naming.py` (`canonical_team_name`: trim, collapse spaces, upper-case a trailing `srl` token → `SRL`,
  keep everything else as shown)
- `backend/db_init.py`: a new `VIEW_STATEMENTS` list run after indexes, containing `CREATE OR REPLACE VIEW
  v_match_odds` (see task 4) and `v_matches` (registry + first/last tick, tick counts, result, is_simulated)
- New `tests/unit/test_s53_harmonisation.py`, `docs/stories/S53-report.md`

## Tasks

1. Competition: extract it for every listing row from the saved listing fixture; unit-test against the fixture with at
   least three matches and their competitions.
2. Names: apply `canonical_team_name` where the listing event is built (parser), so ticks, registry and results agree.
3. Start time: verify and fix the timezone so `match_start_time` is UTC; test the parse of "Today at 10:00 PM",
   "Tomorrow 12:15 PM", "03 NOV 04:30" with an explicit timezone.
4. `v_match_odds`: one row per (match_id, time) with `back_home, lay_home, back_away, lay_away, source` where
   `source` is `listing` or `match_page`; match-page rows are pivoted from `market_ticks` (runner = home/away by
   matching `selection` to the registry team names). Ordered by time; consumers pick the latest row at or before a
   timestamp.
5. Write the SQL so it runs on PostgreSQL 15 + TimescaleDB; test the SQL text structurally (no DB in tests). The
   orchestrator applies it.

## Guardrails

No DB writes in tests; do not apply the view. Do not edit `scraper/manager.py`, `scraper/market_pages.py`,
`scraper/parsers/lotusbook_markets.py`. Do not commit.
