# S74 — Replay the paper trader over recorded history

**Pillar:** 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S58, S69–S73 · **Wave:** 19A
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the strategy owner, I want to run the exact paper-trading code over every match we have already recorded, as if it
were live, so I can judge any change to the models or strategy in minutes instead of waiting weeks for live matches.

## Acceptance criteria

- `scripts/replay_paper.py --from 2026-11-01 [--to …] [--variants …] [--out reports/replay/<name>]` replays every real
  recorded match (SRL with a flag) through the same `run_cycle` logic: decisions every 15 s of recorded time, books
  as-of each decision (S58 merge rule), fills at the takeable price 60 s later, settlement on `match_results`, CLV per
  the binding definition.
- It never reads anything recorded after the decision time (a look-ahead test proves it).
- It writes to its own ledger (in memory or a `paper_bets_replay` table), never to `paper_bets`.
- Output per variant: bets, fills, settled bets, CLV mean with CI, P&L with CI, max worst case per match, churn
  (trades per match, share of tiny trades), and a per-match table.
- Determinism: two runs with the same inputs give identical output.
- Parity: replaying a window the live trader also traded gives the same intents for the same inputs (documented
  exceptions only: data that arrived late).
- Runs over all recorded matches in under 10 minutes on the host.

## Notes

The live trader reads its clock from `now`; replay drives it with recorded time and a DB view limited to rows at or
before that time. Reuse `virtual_trading/paper_trader.py` and `paper_ledger.py` rather than copying them.
