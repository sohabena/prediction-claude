# S80 — Toss-aware pre-match model

**Pillar:** 2 · **Priority:** P1 · **Tier:** haiku · **Depends on:** S60, S68, S69 · **Wave:** 19B
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the pre-match model, I want to use who won the toss and what they chose, because it moves T20 win probability,
we now record it live, and the market may be slow to reprice after it.

## Acceptance criteria

- Features from Cricsheet `info.toss` (winner is home, decision bat/field) and their interaction with venue chase bias
  and day/night where available.
- Walk-forward exactly as S60; report the gain over v3 per family, gender and tier. The toss model becomes the default
  after the toss only if it beats v3 on the pooled walk-forward.
- The probability service uses the toss model once `match_training_status.toss_winner` is known, the pre-toss model
  before.
- Replay (S74) report: CLV of variants before and after using the toss model.
- Tests: toss orientation when the away side wins the toss; no toss leakage before it is known.
