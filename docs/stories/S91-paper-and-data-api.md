# S91 — Read-only API for the paper trader and data health

**Area:** UI · **Priority:** P1 · **Tier:** haiku · **Depends on:** S76, S85, S88 · **Wave:** 21A
**Plan:** docs/architecture/ui-plan.md

## User story

As the operator's UI, I want a small read-only API over what the paper trader and the quality monitor already
compute, so the pages show the same numbers as the daily report without duplicating its logic.

## Acceptance criteria

- New router `backend/routers/paper.py` (prefix `/api/paper`) and `backend/routers/data.py` (prefix `/api/data`),
  registered in `backend/main.py`. Every query runs in a read-only transaction.
- `GET /api/paper/live`: real matches the trader considered in its last cycle (from the last line of
  `logs/paper_trader.log` and `paper_bets`): teams, in-play flag, current book, p_model, p_market, per variant the
  P&L if home wins / if away wins, worst case, tickets used of the budget, last 5 trades. Placebos labelled.
- `GET /api/paper/evidence`: the S88 headline (reuse `virtual_trading.paper_review` / `scripts.paper_report`
  functions, do not reimplement): tradable settled matches, per-variant clustered CLV and excess over placebo
  (pre-match / in-play), R1/R2/R3 progress in matches, in-play gate per format, fill realism.
- `GET /api/data/health`: the last record of `logs/data_quality.jsonl` (checks with status, value, detail).
- `GET /api/data/matches?days=3`: recorded matches with live / pre-match / finished, match page seen, score
  pipeline, start and actual start, result.
- `GET /api/data/missing-results`: `scripts.results_reconciliation.missing_results`.
- `POST /api/data/results` `{match_id, outcome: "winner"|"tie"|"no_result", winner?, margin?, url}`: the
  `scripts.set_result` logic (validation, refuse to overwrite a trusted result, settle the match's paper bets).
  No `force` from the UI.
- Tests with fakes (FastAPI TestClient, no real DB).
