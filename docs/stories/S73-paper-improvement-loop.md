# S73 — Improvement loop and paper-trading report

**Priority:** P1 · **Tier:** haiku · **Depends on:** S71, S72 · **Wave:** 18C · **Plan:** docs/architecture/paper-trading.md

## Story

As the operator, I want the system to learn which strategy settings work from its own settled paper bets, and a clear
daily report of how each variant is doing.

## Owned files

- New `virtual_trading/paper_review.py`, `scripts/paper_report.py`, `tests/unit/test_s73_paper_review.py`
- `scripts/data_quality_monitor.py` + its tests: one check `paper_trading` (trader fresh within 5 min while matches
  are live; no match with worst-case exposure beyond 100,000; unsettled filled bets older than 12 h after the result)

## Tasks

1. Per variant over a window: bets, matches, fill rate, mean CLV with bootstrap CI, realised P&L with CI, calibration
   of `p_used` (Brier vs results), max worst-case exposure per match.
2. Ranking: Thompson-style draw on the CLV posterior (normal approximation per variant); the variant with the best
   posterior mean whose CI lower bound is highest is `primary`; write `models/paper/primary.json` with the evidence.
   A variant whose daily loss passes the kill-switch rule is suspended (reuse `virtual_trading/kill_switch.py` logic
   or mirror its rule) until the next review.
3. The trader (S72) reads `models/paper/primary.json` only to label which variant is primary in logs; all variants keep
   trading so evidence keeps growing. Suspended variants stop opening new risk.
4. `scripts/paper_report.py`: markdown report `reports/paper/<YYYYMMDD>.md` (per variant table, per match summary,
   best/worst trades, how many settled bets until R3) and run it daily from the trader loop at 00:05 UTC.
5. Tests with synthetic ledgers.

## Guardrails

No DB writes in tests. Do not commit.
