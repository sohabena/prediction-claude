# S70 — Model-edge trading strategy (both sides, hedging, 100,000 per-match cap)

**Priority:** P0 · **Tier:** haiku · **Depends on:** S62 · **Wave:** 18A · **Plan:** docs/architecture/paper-trading.md

## Story

As the paper trader, I need a strategy that turns a probability and a book into trades on either side, adds, reduces
and hedges as the match moves, and never lets a match's worst case lose more than 100,000.

## Background

- `strategy/base.py`: `OrderIntent(side, selection, price_limit, stake, reason)`, `Position`, exchange conventions
  (BACK win S(o−1)/lose S; LAY win S/lose S(o−1); a draw settles both selections as losers).
- `strategy/staking.py`: `edge`, `edge_lower`, `shrink_factor`, `kelly_of_bankroll`, `round_stake`.
- `virtual_trading/portfolio.py`: `outcome_pnls`, `worst_loss`.
- LotusBook fill: BACK at the back price, LAY at the lay price.

## Owned files

- New `strategy/model_edge.py`, new `tests/unit/test_s70_model_edge.py`

## Tasks

1. `Variant` dataclass: name, shrink weight `w`, `min_edge`, `kelly_fraction` (default 0.25), `max_match_loss`
   (default 100_000), `bankroll` (default 5_000_000), `hedge_edge` (default 0.01), `max_trades_per_match` (default 40),
   `cooldown_s` (default 120). `market_only` control variant flag.
2. `Book` per outcome: net P&L if home wins, if away wins (start 0,0). `apply(bet)` updates it; `worst()` = −min.
3. `decide(variant, p_model, p_market, book_prices, position, *, now, last_trade_at) -> list[OrderIntent]` (pure):
   - `p = w·p_model + (1−w)·p_market` (market_only: p = p_market; no p_model: no new risk, only hedges).
   - Candidate trades: BACK/LAY × home/away at takeable prices; edge per unit from `strategy.staking.edge`.
   - Target: Kelly stake for the best candidate if its edge ≥ min_edge; skip during cooldown.
   - Reduce/flip: if the current position's favoured outcome now has edge ≤ −hedge_edge, trade the opposite side.
   - Green-up: if the locked profit (min outcome P&L) > 0 and edge < hedge_edge, trade to equalise outcome P&Ls.
   - Cap: shrink any trade so the worst outcome after it is ≥ −max_match_loss; drop if below the minimum stake.
   - Every intent carries a `reason` (enter, add, reduce, flip, green_up) and a `price_limit` = the decision price
     (worse price at fill → no fill).
4. Tests: no edge → nothing; positive edge → back at back price; negative edge on a held side → lay to reduce; green-up
   equalises; the cap binds exactly at −100,000 across several trades on both sides; lay maths hand-checked; cooldown.

## Guardrails

Pure module, no I/O. Do not commit.
