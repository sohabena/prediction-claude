# S111 — Enforce in-play only: permanently disable pre-match new risk

**Pillar:** 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S106 · **Wave:** 25

## User story

As the operator, I want pre-match new risk to be permanently switched off across all model variants, aligning with
the R1 held-out evidence that pre-match ratings models are decisively inferior to the closing exchange market.

## Evidence (2026-11-05)

- R1 on 1,382 T20 matches (OddsPapi 2026 + BBL): pre-match model log loss is **+0.047 (95% CI +0.033 to +0.061)**
  worse than the closing market; flat backing at the close where the model differs by 3/6 points loses 11% to 13%.
- S106 previously set `PREMATCH_NEW_RISK = False`, but in Era H it was temporarily re-enabled as optional (`True`).
- In paper trading, pre-match bets lost 44,700 to 127,200 across variants.
- Permitting pre-match bets leaks capital into an efficient market before in-play information is available.

## Acceptance criteria

1. **Paper trader (`virtual_trading/paper_trader.py`):**
   - Set `PREMATCH_NEW_RISK = False` permanently.
   - Clarify the constant's docstring to state that pre-match risk is permanently prohibited following the R1 failure.
2. **Rulebook (`GOLDEN-RULEBOOK.md`):**
   - Update Rule 6: explicitly state that model variants trade in-play only with zero pre-match new risk.
3. **Tests (`tests/unit/test_s111_prematch_off.py`):**
   - Verify that `PREMATCH_NEW_RISK` is `False`.
   - Verify that no strategy variant generates pre-match new risk intents.
