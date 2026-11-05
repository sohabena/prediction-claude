# S49 — Soft-vs-sharp signals: LotusBook Bookmaker price vs its exchange-mirror match odds

**Priority:** P1 · **Tier:** haiku · **Depends on:** S47 (recorded bookmaker prices), S40/S43 (signal path), S46 (recorded backtest) · **Wave:** 16

## Story

As the person betting by hand, I need a signal when the site's in-house Bookmaker price on a team is better than the
fair price implied by its own exchange-mirror match odds, by more than a safety margin. This edge needs no prediction
model: it bets that the slower in-house line will move toward the sharp one.

## Background

- After S47, `market_ticks` holds `market_type='bookmaker'` back/lay per selection with timestamps and status;
  `odds_ticks` holds the match-odds mirror at the same cadence.
- Fair probability from match odds: de-vig the back/lay mid of both selections (normalise the implied probabilities to
  1). The Bookmaker market is often quoted in "rate" form (e.g. 45 means decimal 1.45). S47's discovery notes define
  the conversion; keep it in one function.
- Bookmaker markets usually charge no commission but have stake limits and suspend often. Signals must respect
  `status='open'`, the shown max stake, and the 60 s manual latency.

## Owned files

- New: `strategy/soft_sharp.py` (a `SoftSharpStrategy` implementing `strategy/base.py Strategy`)
- `scripts/run_real_backtest.py` (register strategy `soft_sharp`)
- New: `tests/unit/test_s49_soft_sharp.py`

## Tasks

1. Build ticks that join, as of each time, the latest bookmaker quote and the latest match-odds quote. Skip when
   either is older than 10 s or not open.
2. Edge for backing selection i at bookmaker decimal price `b`: `p_fair_i · b − 1`. Signal when it is ≥ `min_edge`
   (default 0.03) **and** still holds when re-checked against the match-odds price at the moment of the signal. Stake
   is fractional Kelly on `p_fair_i`, capped by the risk limits and the shown max stake.
3. Exit: optionally lay or hedge on match odds when the bookmaker price converges (reported, not required).
4. Backtest it on the recorded corpus (S46) with the manual preset; report P&L/match with CI, CLV (vs match-odds 600 s
   later), the missed rate and n. Report R0 for comparison.

## Success criteria

- [ ] A fixture where the bookmaker price is 2.30 and fair is 0.48 (edge +10.4%) signals; at 2.05 (edge −1.6%) it
      does not.
- [ ] Stale or suspended quotes never signal.
- [ ] The backtest runs end to end on a recorded fixture.
- [ ] Baseline suite passes.

## Verification

```bash
python -m pytest tests/unit/test_s49_soft_sharp.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Live wiring into the signal runner (small follow-up once the backtest shows an edge).
