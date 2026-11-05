# S89 in-play model vs LotusBook in-play price

Generated 2026-11-03T12:14:16.479321+00:00. Real `livescore-v2` matches with a decided result. Model and market are scored on the same recorded in-play states; a state counts only when the in-play model (source `inplay`) priced it. Log loss and Brier: lower is better; the difference is model minus market (negative favours the model). Match-clustered 95% CIs, 2000 seeded draws, each match weighted equally.

Gate rule: `inplay_model_allowed = matches >= 15 and logloss_diff <= 0 and ci_lower < 0 (logloss_diff = model minus market, final-result log loss, match-clustered 95% bootstrap CI)`.

## Coverage

- registry: 2
- not_decided: 1
- decided: 1
- matches_evaluated: 1
- states_evaluated: 257
- skipped states: no_later_price 14, no_market 8

## Per format (final result)

| format | n matches / states | log loss model | log loss market | diff [95% CI] | Brier diff [95% CI] | gate |
|---|---|---|---|---|---|---|
| t20 | 1 / 257 | 0.3785 | 0.3943 | -0.0158 [n/a, n/a] (too few matches) | -0.0058 [n/a, n/a] (too few matches) | closed (too few matches) |
| odi | 0 / 0 | n/a | n/a | n/a | n/a | closed |
| multiday | 0 / 0 | n/a | n/a | n/a | n/a | closed |

## Per format (soft target: market 10 min later)

| format | n matches / states | log loss diff [95% CI] | Brier diff [95% CI] |
|---|---|---|---|
| t20 | 1 / 243 | 0.0132 [n/a, n/a] (too few matches) | 0.0042 [n/a, n/a] (too few matches) |
| odi | 0 / 0 | n/a | n/a |
| multiday | 0 / 0 | n/a | n/a |

## Gate file

```json
{
  "t20": {
    "inplay_model_allowed": false,
    "matches": 1,
    "logloss_diff": -0.015789073364449416,
    "ci": [
      null,
      null
    ],
    "evaluated_at": "2026-11-03T12:14:16.479321+00:00"
  },
  "odi": {
    "inplay_model_allowed": false,
    "matches": 0,
    "logloss_diff": null,
    "ci": [
      null,
      null
    ],
    "evaluated_at": "2026-11-03T12:14:16.479321+00:00"
  },
  "multiday": {
    "inplay_model_allowed": false,
    "matches": 0,
    "logloss_diff": null,
    "ci": [
      null,
      null
    ],
    "evaluated_at": "2026-11-03T12:14:16.479321+00:00"
  }
}
```
