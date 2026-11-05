# S76 — Every bet settles: results reconciliation

**Pillars:** 1 and 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S54, S56, S68, S71 · **Wave:** 19A
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the operator, I want every finished match to end up with a correct result, and to be told which ones are missing,
so no paper bet stays unsettled and no training row lacks a label.

## Acceptance criteria

- Verify the result paths on real finished matches: the settled Winner market in the match page's JSON (runner status
  wording) and the score feed's `result`/`status` text. Capture both into fixtures from the first real finished
  `livescore-v2` match and make the parsers match them exactly.
- Daily reconciliation in the paper report and the monitor: real matches whose last tick is more than 3 h old with no
  `win`/`tie`/`no_result` result, with their teams, competition and start time.
- `scripts/set_result.py --match lb_… --winner "<team>" --margin "15 runs" --source public_scorecard [--url …]` writes
  a result (validates the winner against the registry teams, refuses to overwrite a trusted source without `--force`),
  then settles that match's paper bets.
- Settlement runs in the trader every cycle and after `set_result`.
- Tests with fixtures and fakes.
