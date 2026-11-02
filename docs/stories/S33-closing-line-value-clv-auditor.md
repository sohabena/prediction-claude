# S33 — Closing Line Value (CLV) and price efficiency auditor

**Priority:** P1 · **Tier:** sonnet · **Depends on:** none · **Wave:** 11
**Conflicts with:** none (strictly touches settlement, metrics, and CLV evaluation)

## Story

As the quantitative analyst, I need an automated Closing Line Value (CLV) auditing service that compares the odds of every placed bet against the market's final closing price and the price 60 seconds post-placement, providing mathematical proof of whether our system has genuine edge against market efficiency.

## Background

- In quantitative sports trading, **Closing Line Value (CLV)** is the gold-standard metric for separating true skill from luck.
- If you consistently bet a selection at 2.10 and the market closes at 1.90, you generated positive CLV:
  $$\text{CLV} = \frac{\text{Odds}_{\text{placed}}}{\text{Odds}_{\text{closing}}} - 1.0 = \frac{2.10}{1.90} - 1.0 = +10.5\%$$
- Academic literature and financial market microstructure research prove that a trader with positive CLV over 500+ trades is virtually guaranteed to be profitable long-term, regardless of short-term variance.
- Conversely, a trader with negative CLV who shows a profit is simply on a lucky run that will inevitably revert.
- PHOENIX already stores `clv` and `closing_odds` on [`VirtualBet`](file:///c:/Users/sohai/Downloads/prediction-claude/shared/schemas.py#L78-L82), but settlement currently lacks automated, systematic calculation of:
  1. **Match Closing CLV:** Placed price vs final pre-suspension price.
  2. **1-Minute Post-Placement CLV:** Placed price vs price 60 seconds later (measuring immediate price impact and information speed).

## Context Pack

- Settlement engine: `virtual_trading/settlement.py`
- Database: TimescaleDB `virtual_bets` table
- Metrics: `shared/metrics.py`
- Output report: `scripts/audit_clv.py`

## Owned Files

- `virtual_trading/settlement.py`
- `shared/metrics.py`
- New: `scripts/audit_clv.py`
- New: `tests/unit/test_s33_clv_auditor.py`

## Tasks

- [ ] In `virtual_trading/settlement.py`:
  - When settling bets at match conclusion, fetch the final valid pre-suspension tick for that market.
  - Compute `clv = (bet.odds / closing_odds) - 1.0` for BACK bets, and `clv = (closing_odds / bet.odds) - 1.0` for LAY bets.
  - Persist `closing_odds` and `clv` to TimescaleDB `virtual_bets`.
- [ ] In `shared/metrics.py`:
  - Add `calculate_portfolio_clv(bets: list[VirtualBet]) -> dict[str, float]`:
    - `mean_clv`, `median_clv`, `positive_clv_rate` (% of bets beating closing line), `clv_sharpe`.
- [ ] Implement `scripts/audit_clv.py`:
  - Standalone CLI tool that queries settled bets from TimescaleDB and prints a formatted statistical breakdown of CLV performance.
- [ ] Implement `tests/unit/test_s33_clv_auditor.py`:
  - Test CLV calculation formulas for both BACK and LAY bets.
  - Assert that beating the closing price produces positive CLV.

## Verification

```bash
# Run CLV unit tests
python -m pytest tests/unit/test_s33_clv_auditor.py -v

# Run CLV audit CLI
python -m scripts.audit_clv --limit 100
```

