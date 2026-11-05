# S109 — Odds validity corridor: ban new-risk LAY below 1.35 and BACK above 4.00 for model variants

**Pillar:** 3 · **Priority:** P0 · **Tier:** sonnet · **Depends on:** S75, S90, S107 · **Wave:** 25

## User story

As the operator, I want the standard model variants to stop placing new-risk bets at extreme odds (no new-risk LAY
below 1.35, and no new-risk BACK at 4.00 or longer), so that model calibration compression in the tails and Kelly
liability explosions do not incinerate capital on impossible comebacks or insurmountable leaders.

## Evidence (2026-11-05 forensic audit of 1,380 settled strategy bets)

A forensic breakdown of all settled paper bets across 11 matches revealed two severe structural losses:
1. **Laying heavy favorites (< 1.50):** 149 bets, win rate 0.7% (1 win in 149!), losing **`-1,731,243`**. At 1.05
   decimal odds, liability is only 0.05 per unit of stake, so the Kelly formula demands massive stakes (up to 380,000)
   for a 1-2% model-market disagreement, which loses almost every single time.
2. **Backing long-shots (>= 4.00):** 146 bets, win rate < 10% (0.0% on >= 5.00, 128 of 128 lost!), losing
   **`-1,950,593`**. Isotonic regression and logistic loss flatten in the tails (e.g. predicting 0.07 when true
   empirical chance is 0.005), which Kelly staking misinterprets as an astronomical edge.
3. **The Core is Profitable:** Within the competitive corridor ($1.35 \le \text{odds} \le 4.00$), the standard model
   variants produced **`+3,270,820` net profit (+9.8% ROI)** across 1,103 bets.

## Acceptance criteria

1. **Trade guards (`strategy/trade_guards.py`):**
   - Add `MIN_NEW_RISK_LAY_ODDS = 1.35` and `MAX_NEW_RISK_BACK_ODDS = 4.00` to `GuardConfig` (customizable).
   - In `intent_refusal`:
     - If `intent.reason in NEW_RISK_REASONS`:
       - If `intent.side == LAY` and the takeable lay price (or `intent.price_limit`) is $< \text{MIN\_NEW\_RISK\_LAY\_ODDS}$: refuse with `"lay_odds_too_low"`.
       - If `intent.side == BACK` and `intent.reason != "tail"` and the takeable back price (or `intent.price_limit`) is $\ge \text{MAX\_NEW\_RISK\_BACK\_ODDS}$: refuse with `"back_odds_too_high"`.
     - `NEW_RISK_REFUSALS` and `REFUSAL_REASONS` updated to include `"lay_odds_too_low"` and `"back_odds_too_high"`.
     - `reduce` and `green_up` intents are NEVER refused by these guards (hedging/closing can execute at any price).
     - The `tail` variant (reason `"tail"`) is exempted from `back_odds_too_high` (it operates on its own 10.0+ chase base rate model with micro-stakes).
2. **Model edge strategy (`strategy/model_edge.py`):**
   - In `_candidates`: for standard variants, do not generate candidate trades that violate the corridor ($1.35 \le \text{odds} < 4.00$) so the Kelly target position does not seek un-takeable boundary states.
3. **Tests (`tests/unit/test_s109_odds_corridor.py`):**
   - Verify `intent_refusal` rejects new-risk LAY at 1.05..1.34 with `"lay_odds_too_low"`.
   - Verify `intent_refusal` rejects new-risk BACK at >= 4.00 with `"back_odds_too_high"`.
   - Verify `reduce` and `green_up` pass even at 1.05 or 10.0.
   - Verify `tail` variant BACK intents pass at 10.0+.
   - Verify `model_edge.decide` respects the odds limits.
