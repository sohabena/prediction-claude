# S94 — Data page

**Area:** UI · **Priority:** P1 · **Tier:** haiku · **Depends on:** S91, S95 · **Wave:** 21B
**Plan:** docs/architecture/ui-plan.md

## User story

As the operator, I want to see whether the recorded data is trustworthy and to enter a missing result in one place,
so every paper bet settles and every training row has a label.

## Acceptance criteria

- `frontend/app/data/page.tsx`.
- Quality monitor: the latest run's checks (OK / WARN / FAIL with detail), time of the run; refresh every minute.
- Missing results: the list from `/api/data/missing-results`, each with an "Enter result" form: winner (one of the
  two registry teams), tie or no result, margin, scorecard URL (required). Submits to `POST /api/data/results`,
  shows what was written and how many paper bets settled, or the refusal reason. Reuse
  `components/ManualResultModal.tsx` if it fits, simplified.
- Recorded matches (last 3 days): teams, competition, real/SRL, state (pre-match, live, finished), match page seen,
  score pipeline, start and actual start, result. Filter real / SRL / all.
- No delete buttons, no scraping approval toggles.
