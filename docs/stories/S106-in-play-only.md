# S106 — Model variants trade in play only; 30 bets is a ceiling, not a target

**Pillar:** 3 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S83, S90 · **Wave:** 24

## User story

As the operator, I want the model variants to stop opening risk before the off and to trade only in play, and I do
not need every match traded or 30 bets per match: bet only when there is an edge.

## Evidence (2026-11-05)

- R1 on 1,382 T20 matches (BBL file plus OddsPapi 2026, walk-forward out-of-sample): the pre-match model is worse
  than the closing market, log loss +0.047 (95% CI +0.033 to +0.061); backing it at the close loses 11 to 13%.
- Paper, current code (eras D to F): pre-match bets -127,200 (7 bets, 1 match); in-play bets +109,876 (6 matches).

## Acceptance criteria

1. `virtual_trading/paper_trader.py`: a constant `PREMATCH_NEW_RISK = False` (operator, 2026-11-05) and, in the
   per-variant loop, a strategy variant (not `market_only`, not a placebo) before the off gets no model probability,
   exactly as the S83 in-play-without-score path does: `model_edge.decide` receives `p_model=None`, so only reduce and
   green-up can trade (with no pre-match position there is nothing to reduce). Each such variant-match-cycle counts
   `guard_refusals["prematch_off"]`. The market-dynamics variants are unchanged (already in play only). The placebos
   copy the reference variant's intents, so they also stop pre-match by construction; verify it.
2. In-play decisions are unchanged (same probabilities, guards, caps, ticket budget; the budget is a ceiling: nothing
   forces a trade).
3. Docstrings: the module docstring step that describes S83 mentions S106.
4. Tests in `tests/unit/test_s106_in_play_only.py` (fakes only, pattern of the S83 tests: grep tests/unit for
   `inplay_without_state`): pre-match, a strategy variant with a strong model edge places nothing and counts
   `prematch_off`; in play with a fresh in-play score it still trades; market_only and the placebos behave as stated;
   setting the constant to True restores pre-match trading.
5. Replay before and after, pinned `--to 2026-11-05T00:00:00+00:00` (run the baseline first, before editing):
   pre-match bets per variant after the change must be 0; report bets, P&L and CLV per variant before and after.
