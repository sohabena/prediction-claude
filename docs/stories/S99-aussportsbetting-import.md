# S99 — Import the aussportsbetting historical BBL odds file

**Pillar:** 1 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S98 · **Wave:** 23

## User story

As the operator, I want the free aussportsbetting.com historical Big Bash League file (results and odds) turned into
S98 records, so R1 can be tested on several BBL seasons.

## Facts

- The page is behind a Cloudflare check. **Nothing in this story downloads anything**: the operator saves the file by
  hand into `data/historical_odds/incoming/aussportsbetting/` (`.xlsx` or `.xls`; also accept `.csv`).
- The exact column layout is not known in advance. The aussportsbetting sport files usually have one or more note
  rows above a header row that contains `Home Team` and `Away Team`, then columns such as `Date`, a start time
  (`Kick-off (local)` or `Start Time`), `Home Score`, `Away Score`, `Home Odds`, `Away Odds`, `Home Odds Open`,
  `Home Odds Min`, `Home Odds Max`, `Home Odds Close` (same four for `Away`), `Bookmakers Surveyed`, `Notes`.

## Acceptance criteria

1. `scraper/importers/aussportsbetting.py` (pure, no network): `parse_workbook(path) -> ParseResult` with `records`
   (S98 `OddsRecord`), `notes` (text of every row above the header), `unknown_columns`, `skipped` (row number and
   reason) and counts.
   - Header row = the first row containing both `Home Team` and `Away Team` (case and whitespace insensitive).
     Columns are found by name, never by position.
   - Kinds: `... Odds Open` → `open`, `Min` → `min`, `Max` → `max`, `Close` → `close`; plain `Home Odds` /
     `Away Odds` → `average` (unless the notes define them otherwise; then record that in the manifest). One record
     per kind present with both prices valid.
   - `source="aussportsbetting"`, `bookmaker="aussportsbetting"`, `competition="Big Bash League"` (or from the file
     name for another competition: map known names, e.g. WBBL → female), `format="t20"`, gender from the competition.
   - `source_event_id` = `<date>_<home>_<away>` slug (stable across runs).
   - Start time: date plus the time column read as `Australia/Sydney` local time, converted to UTC; date only → 
     09:00 UTC on that date (Cricsheet links within one day). Excel serial dates and datetime cells both work.
   - Winner: from an explicit result/winner column when present; otherwise from `Home Score` / `Away Score` only when
     both are plain numbers and differ (count these as `winner_from_scores` in the counts: DLS can make this wrong,
     S102 checks against Cricsheet); otherwise None.
   - Team names through `training.historical_odds.normalise_team`.
2. `scripts/import_aussportsbetting.py [--incoming DIR] [--root DIR] [--dry-run]`: parses every file in the incoming
   folder, prints counts, unknown columns and the notes, and (unless `--dry-run`) writes the store with
   `write_store("aussportsbetting", ..., provenance=<notes + page URL>, inputs=[files])`. If the notes name Betfair as
   the odds source, `write_store` refuses: print that clearly and exit 2.
3. Tests `tests/unit/test_s99_aussportsbetting.py`, building small workbooks with openpyxl in `tmp_path`: note rows
   above the header; columns in a different order; missing Open/Min/Max; Excel serial dates; a time column; invalid
   prices skipped with a reason; winner from scores and from a result column; stable event ids; the script's dry run
   writes nothing; a Betfair note is refused.
