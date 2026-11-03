# S81 — Zero-downtime scraper deploys

**Pillar:** 1 · **Priority:** P2 · **Tier:** haiku · **Depends on:** S37, S52 · **Wave:** 19C
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the operator, I want to deploy a new scraper without losing recording time, because every redeploy today costs a
minute or more of every live match.

## Acceptance criteria

- `scripts/deploy_scraper.ps1` (and a Bash equivalent) builds the new image, starts it as `phoenix-scraper-next`,
  waits until it has written ticks for at least 60 s, then stops the old container and renames.
- While both run, duplicate rows are harmless: odds ticks dedupe by fingerprint per process, and market rows by
  per-record dedupe; the monitor's duplicates check stays green across a deploy (tested once live and documented).
- Runbook section on deploying.
