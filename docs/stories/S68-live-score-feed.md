# S68 — Record the live score from the page's own score feed

**Priority:** P0 · **Tier:** haiku · **Depends on:** S52, S54, S56 · **Wave:** 17D

## Story

As the person training in-play models and labelling results, I want the live score, toss and result recorded for
every real match whose page carries them, from the data the page already receives.

## Background (verified 2026-11-03)

- A match page polls `GET https://b2b.max247.co/api/public/match-center/score/v3/4/<event_id>` every few seconds.
- For SRL matches and some real matches it returns `{"result":{"stats":{},"scorePipeline":"legacy"}}` (no score).
- For real matches on the `livescore-v2` pipeline it returns full stats. Real fixture:
  `tests/fixtures/lotusbook/score_v3_real_live_36267578_20261103.json` (Biratnagar Kings v Janakpur Bolts, NPL):
  `stats.battingTeam{name, score "2", wickets "1", overs "1.0", balls "6", runRate, requiredRunRate, requiredRuns,
  target, partnerShipScore/Balls}`, `currentInning` 1, `oversPerInning` "20.0", `totalBalls` 120, `remainingBalls`
  114, `innings[]{number, teamName, score, wickets, overs, balls, isLive}`, `tossWinner{name, decision "opt to Bowl"}`,
  `batsmen[]`, `bowler`, `lastOver.balls[]` (values like "0","1","W"), `isPowerplay`, `matchFormat` "T20",
  `session` "In Play", `result` null, `status` null, `updatedAt` (ISO, the feed's own time), `source` "livescore-v2".
- `match_context` columns: time, match_id, is_live, score, wickets, overs, run_rate, req_run_rate, innings,
  balls_remaining, batting_team, bowling_team, status, match_format. Rows are stored only when a real score was read
  (no placeholders).
- The market-page worker (`scraper/market_pages.py`) already listens passively for `sma-event` responses (S56);
  `scraper/manager.py` stores match context via `_publish_match_context(..., publish=False, store=True)` and results via
  `LotusResultDetector.update_scoreboard(match_id, comment, finished)` / `update_settled`.

## Rules

Passive only: read the page's own `match-center/score/v3` responses with `page.on("response")`. No own requests, no
header or user-agent changes, no clicks.

## Owned files

- New `scraper/parsers/lotusbook_score.py` (pure: score JSON → `ScoreState` or None)
- `scraper/market_pages.py` (subscribe to the score responses of its own event; dedupe by `updatedAt` + content)
- `scraper/manager.py` (store `match_context` rows from score states; registry toss fields; results)
- `backend/models/match_status.py` + `backend/db_init.py` (nullable `toss_winner TEXT`, `toss_decision TEXT` on
  `match_training_status`), new `alembic/versions/007_toss_fields.py` (not run)
- `scripts/data_quality_monitor.py` + its tests: `score_coverage` counts only matches whose feed is `livescore-v2`
  (record the pipeline per match in memory/logs or derive from `match_context` presence) and may **fail** when such a
  match has no context rows; legacy-pipeline matches are reported apart
- New `tests/unit/test_s68_score_feed.py`

## Tasks

1. Parser: `parse_score_v3(payload) -> ScoreState | None` (None for legacy/empty stats). Fields: batting team, runs,
   wickets, overs (cricket notation), balls, innings, target, run rate, required rate, remaining balls, total balls,
   format, powerplay, toss winner/decision, `updated_at`, `result`/`status` text, last-over ball values.
   `to_context_row(state, match_id, team_home, team_away)` maps to `match_context` columns (bowling team = the other
   side).
2. Worker: listen to its own event's score responses; hand changed states (by `updated_at` and score content) to a
   callback; log `score_pipeline_legacy` once per match when the feed is legacy.
3. Manager: store a `match_context` row per changed state (never a placeholder); write toss winner/decision to the
   registry once known; when `result`/`status` reads as finished, pass it to `update_scoreboard(..., finished=True)`
   using `scraper.parsers.lotusbook_scoreboard.parse_result_comment` forms; log the raw text so the wording can be
   verified.
4. Tests from the fixture: parsed values equal the fixture; legacy payload → None; dedupe; context row mapping;
   toss written once; no request is ever issued (fake page).

## Guardrails

No DB writes in tests. Do not apply migrations, do not touch Docker, do not commit.
