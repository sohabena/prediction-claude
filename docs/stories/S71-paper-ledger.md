# S71 — Paper bet ledger, settlement and CLV

**Priority:** P0 · **Tier:** haiku · **Depends on:** none · **Wave:** 18A · **Plan:** docs/architecture/paper-trading.md

## Story

As the operator, I need every virtual bet recorded with what the model knew, the price asked and the price filled,
then settled on the real result with its CLV, so performance can be measured per strategy variant.

## Owned files

- New `backend/models/paper_bets.py`, `backend/db_init.py` (register the model only; table created by create_all),
  new `alembic/versions/008_paper_bets.py` (not run), new `virtual_trading/paper_ledger.py`,
  new `tests/unit/test_s71_paper_ledger.py`

## Tasks

1. Table `paper_bets` (plain table): `bet_id uuid pk`, `variant text`, `match_id text`, `decided_at timestamptz`,
   `filled_at timestamptz null`, `status text` (pending | filled | unfilled | settled | void), `side` BACK/LAY,
   `selection` home/away, `team text`, `price_limit float`, `price_filled float null`, `stake float`,
   `liability float`, `reason text`, `p_model float null`, `p_market float null`, `p_used float`, `edge float`,
   `source text` (prematch/inplay/market_only), `in_play bool`, `score_state jsonb null`, `book jsonb`,
   `pnl float null`, `settled_at timestamptz null`, `result text null`, `clv float null`, `closing_price float null`.
   Indexes on (variant, decided_at), (match_id).
2. `paper_ledger.py` (session injected, so tests use fakes):
   - `record_intents(session, variant, match, intents, context)` → pending rows;
   - `fill_pending(session, book_at)` → for pending rows older than 60 s, read the takeable price at
     decided_at + 60 s via `book_at(match_id, t)`; fill if it is at least as good as the limit, else `unfilled`;
   - `settle(session, results)` → filled rows of matches with a result: win/tie/no-result/abandoned/unresolved
     (void for tie/no-result/abandoned; unresolved waits), P&L per the exchange conventions;
   - `clv_for(row, reference_price)` via `shared.metrics.clv_prob`; reference = last price before the off for
     pre-match bets, same side 10 min after the fill for in-play bets;
   - `position(session, variant, match_id)` → outcome P&Ls of filled bets (for the 100,000 cap).
3. Tests with fakes: fill at the 60 s price; worse price → unfilled; settle back/lay wins/losses; void on tie; CLV
   sign for back and lay; position sums.

## Guardrails

No DB writes in tests; do not apply migrations; do not commit.
