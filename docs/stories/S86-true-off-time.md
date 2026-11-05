# S86 — True off time

**Pillar:** 1 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S52, S56, S77 · **Wave:** 20A
**Plan:** docs/architecture/value-focused-plan.md

## User story

As every consumer of "in play" (the trader, CLV, the dataset), I want the time the match actually started, not the
scheduled time, because the listing and the match page can disagree by hours (5.5 h for West Indies A v England Lions
on 2026-11-03) and matches start late.

## Acceptance criteria

- New registry columns `actual_start_time` and `actual_start_source` (migration, nullable). Set once, by the first
  of: the match JSON's market turning in-play (find the field in the stored fixtures; passive capture only), the
  listing's live badge, the score feed's first ball.
- Consumers: the paper trader's in-play flag and the pre-match CLV reference ("last price before the off") use
  `actual_start_time` when known, else `match_start_time`; the dataset's T-60m/T-10m/T-0 snapshots likewise.
- Monitor check `start_time_consistency` (WARN): real matches whose listing and JSON starts differ by more than
  15 min, or whose `actual_start_time` is more than 60 min after `match_start_time`.
- Backfill for recorded matches from the first live listing tick (reads the DB, writes only the new columns), with a
  dry-run mode; the operator runs the write.
- Tests: precedence of sources, set once, consumers' fallback.
