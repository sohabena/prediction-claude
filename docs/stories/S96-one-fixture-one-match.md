# S96 — One fixture, one match: link twin event ids

**Pillar:** 1 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S56, S76, S86 · **Wave:** 22

## User story

As every consumer of match data (training, paper trading, results, monitor), I want one real fixture to count as one
match, because the site can list the same fixture under two event ids.

## Evidence (2026-11-03)

Otago Volts v Canterbury Kings (Ford Trophy, 21:30 UTC) was recorded as `lb_36266991` (score feed `livescore-v2`,
vanished from the listing at 23:53 and was marked `unresolved`) and `lb_75330686` (`legacy`, still live at 02:00).
Same teams, same competition, same actual start (21:30:00.74). No paper bets on either yet.

## Acceptance criteria

- Registry column `canonical_match_id` (nullable; migration in `backend/db_init.py`, ORM model). A non-canonical twin
  points at its canonical; the canonical and ordinary matches stay NULL.
- Twin rule (pure function, unit-tested): both `lb_` ids, same simulated flag, same teams after the existing name
  normalisation (case, whitespace; home/away may be swapped), same competition when both are known, and starts
  within 30 min (actual start when known, else scheduled). Canonical: the twin with the score feed
  (`score_pipeline = 'livescore-v2'`), else the one with more match-page rows, else the lower id.
- The hourly quality monitor's harmonise step links twins (UPDATE of `canonical_match_id` only) and copies a decided
  result (win, tie, no_result, abandoned) from one twin to the other when the other has none or only `unresolved`
  (`source = 'twin:<match_id>'`; never overwrite a decided or hand-entered result).
- New monitor check `duplicate_fixtures` (WARN): twins found and not linked, or twins with conflicting decided results.
- Consumers ignore non-canonical twins: the paper trader's candidate selection, the training dataset, the
  missing-results list and the `results` check. Recording continues on both ids (no data is dropped).
- A backfill (`--apply`, default dry run) links existing twins.
- Tests with fakes only (no real DB writes): the twin rule (swap, case, 30-min edge, SRL vs real never twins), the
  canonical choice, result copying rules, consumer filtering, the monitor check.
