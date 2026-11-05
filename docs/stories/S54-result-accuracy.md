# S54 — Accurate match results from recorded prices

**Priority:** P0 · **Tier:** haiku · **Depends on:** none · **Wave:** 16A

## Story

As the strategy owner I need a correct winner for every recorded match, because labels decide what the models learn.

## Background (verified 2026-11-03)

`match_results` for 2026-11-01..03 has 29 rows, all `source='lotusbook_odds'`. 13 are `result_type='no_result'` with
an **empty winner**, including real matches (Afghanistan v Zimbabwe lb_36255271 closing back_home 2.32; Dubai v
Sharjah lb_75747934 closing 1.85). Those matches simply left the listing; the detector called it "no result" from
mid-match prices. SRL wins are detected from 1.01 / 16.5 closing prices. Several real matches recorded live this run
(Oman v Netherlands lb_75878940, Melbourne Stars W v Perth Scorchers W lb_36262294) have no result row at all.

Detector: `scraper/lotus_result_detector.py` (`LotusResultDetector.update_tick`, `check_completed`, `_infer_result`,
`_store_and_publish`). It is fed from `scraper/manager.py::_on_events` (listing prices; score fields are None because
the listing has no score).

## Owned files

- `scraper/lotus_result_detector.py`
- New `tests/unit/test_s54_result_accuracy.py`

## Tasks

1. A match that disappears from the listing is **not** a result. Only infer a winner when the last recorded prices are
   decisive: one side's best back ≤ 1.05 (or the other side ≥ 15, or lay ≤ 1.06) within the last N minutes before
   it disappeared. Otherwise mark it `unresolved` (new `result_type`) with empty winner and keep it eligible for a
   later better source; never write `no_result` from price disappearance.
2. `no_result` is written only from an explicit source (see 3).
3. Add `update_scoreboard(match_id, comment, finished: bool)` so the match-page scoreboard (S51/S52) can supply the
   official comment ("X won by 25 runs", "Match tied", "No result"). A scoreboard result overrides a price inference
   and sets `source='lotusbook_scoreboard'`; a price inference never overrides a scoreboard result. Parse the comment
   with a small local regex (S51 will ship `parse_result_comment`; if `scraper/parsers/lotusbook_scoreboard.py`
   exists, import it, otherwise keep a local fallback with the same forms).
4. Winner names: copy the exact team string recorded in the ticks (`team_home` / `team_away`), so joins work.
5. Write `margin` when the comment has it.
6. Tests with fakes only: decisive home win; decisive away win via lay; disappearance mid-match → `unresolved`;
   scoreboard comment overrides; price never overrides scoreboard; tie and no-result comments.

## Guardrails

No DB in tests. Edit only the owned files. Do not commit.
