# S95 — Remove the obsolete UI and run it with the recorder

**Area:** UI · **Priority:** P1 · **Tier:** haiku · **Depends on:** none · **Wave:** 21A
**Plan:** docs/architecture/ui-plan.md

## User story

As the operator, I want the UI to contain only pages that help me make profit, and to be running whenever the
recorder runs, so I can open it and trust what I see.

## Acceptance criteria

- Delete the RL-era pages: `app/training`, `app/graduation`, `app/advisor`, `app/trading`, `app/matches` (list and
  watch), and components only they use (`GraduationProgress`, `TrainingChart`, `SignalCard` if unused, …).
- Sidebar: Today (`/`), Evidence (`/evidence`), Data (`/data`), plus the health dot. Subtitle "Paper trading and
  data" instead of "Cricket Betting RL". Placeholder pages for Today/Evidence/Data until S92–S94 land.
- Backend routers stay in place (other code and tests use them); the UI no longer calls them.
- `docker-compose.yml`: `backend` and `frontend` also in the `recording` profile; the frontend reaches the backend at
  `http://localhost:${API_PORT:-8001}/api`. Check the backend starts with the recording services (no RL dependency
  at import); fix only what blocks start-up.
- `npm run build` passes.
