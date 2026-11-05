# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 16 matches considered; 5298 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 23 | 14 | 11 | 0 | +0.0140 [n/a, n/a] (1 match / 11 bets; too few matches) | +7942 [n/a, n/a] (1 match / 11 bets; too few matches) | 25,460 | 7.00 | 1 (7.1%) |
| w03_e06 | 23 | 13 | 13 | 0 | +0.0040 [n/a, n/a] (1 match / 13 bets; too few matches) | -13550 [n/a, n/a] (1 match / 13 bets; too few matches) | 13,550 | 13.00 | 1 (7.7%) |
| w05_e03 | 24 | 11 | 8 | 0 | +0.0043 [n/a, n/a] (1 match / 8 bets; too few matches) | +6755 [n/a, n/a] (1 match / 8 bets; too few matches) | 44,100 | 5.50 | 0 (0.0%) |
| w05_e06 | 26 | 14 | 11 | 0 | +0.0135 [n/a, n/a] (1 match / 11 bets; too few matches) | +10176 [n/a, n/a] (1 match / 11 bets; too few matches) | 44,100 | 7.00 | 1 (7.1%) |
| w07_e03 | 32 | 18 | 11 | 0 | +0.0163 [n/a, n/a] (1 match / 11 bets; too few matches) | +4620 [n/a, n/a] (1 match / 11 bets; too few matches) | 50,000 | 9.00 | 1 (5.6%) |
| w07_e06 | 23 | 15 | 13 | 0 | +0.0373 [n/a, n/a] (1 match / 13 bets; too few matches) | +9554 [n/a, n/a] (1 match / 13 bets; too few matches) | 50,000 | 7.50 | 1 (6.7%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 23 | 14 | 9 | 60.9% | 64.4% | 0.0% (0) | 0 | 0.0% (0) | -0.0679 / -2.5% |
| w03_e06 | 23 | 13 | 10 | 56.5% | 49.2% | 0.0% (0) | 0 | 0.0% (0) | -0.1446 / -3.6% |
| w05_e03 | 23 | 11 | 12 | 47.8% | 42.6% | 0.0% (0) | 0 | 0.0% (0) | -0.0055 / -0.4% |
| w05_e06 | 26 | 14 | 12 | 53.8% | 67.4% | 0.0% (0) | 0 | 0.0% (0) | -0.0264 / -1.2% |
| w07_e03 | 32 | 18 | 14 | 56.2% | 39.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0133 / -0.6% |
| w07_e06 | 23 | 15 | 8 | 65.2% | 56.9% | 0.0% (0) | 0 | 0.0% (0) | -0.0040 / -0.2% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 4 | +0.0507 [n/a, n/a] (1 match / 1 bet; too few matches) | +0.0507 [n/a, n/a] (1 match / 1 bet; too few matches) | -25000 [n/a, n/a] (1 match / 1 bet; too few matches) |
| w03_e03 | in-play | 10 | +0.0104 [n/a, n/a] (1 match / 10 bets; too few matches) | +0.0309 [n/a, n/a] (1 match / 10 bets; too few matches) | +32942 [n/a, n/a] (1 match / 10 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 13 | +0.0040 [n/a, n/a] (1 match / 13 bets; too few matches) | +0.0171 [n/a, n/a] (1 match / 13 bets; too few matches) | -13550 [n/a, n/a] (1 match / 13 bets; too few matches) |
| w05_e03 | pre-match | 9 | +0.0105 [n/a, n/a] (1 match / 6 bets; too few matches) | +0.0135 [n/a, n/a] (1 match / 6 bets; too few matches) | -33860 [n/a, n/a] (1 match / 6 bets; too few matches) |
| w05_e03 | in-play | 2 | -0.0144 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0490 [n/a, n/a] (1 match / 2 bets; too few matches) | +40615 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | pre-match | 5 | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | in-play | 9 | +0.0057 [n/a, n/a] (1 match / 9 bets; too few matches) | +0.0344 [n/a, n/a] (1 match / 9 bets; too few matches) | +60176 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | pre-match | 16 | -0.0041 [n/a, n/a] (1 match / 9 bets; too few matches) | -0.0018 [n/a, n/a] (1 match / 9 bets; too few matches) | -36880 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | in-play | 2 | +0.1081 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0977 [n/a, n/a] (1 match / 2 bets; too few matches) | +41500 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w07_e06 | pre-match | 4 | +0.0344 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0344 [n/a, n/a] (1 match / 2 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w07_e06 | in-play | 11 | +0.0379 [n/a, n/a] (1 match / 11 bets; too few matches) | +0.0412 [n/a, n/a] (1 match / 11 bets; too few matches) | +59554 [n/a, n/a] (1 match / 11 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0140 [-0.0177, +0.0425] | n/a [-10538.64, +12268.35] |
| w03_e06 | +0.0040 [-0.0238, +0.0325] | n/a [-10000.10, +7300.00] |
| w05_e03 | +0.0043 [-0.0292, +0.0343] | n/a [-12047.97, +16789.06] |
| w05_e06 | +0.0135 [-0.0163, +0.0380] | n/a [-13237.71, +17929.18] |
| w07_e03 | +0.0163 [-0.0107, +0.0466] | n/a [-9194.73, +11577.45] |
| w07_e06 | +0.0373 [+0.0197, +0.0541] | n/a [-14046.69, +16732.67] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 10, BACK_home 2, LAY_away 11
- w03_e06: BACK_away 5, BACK_home 5, LAY_away 13
- w05_e03: BACK_away 12, BACK_home 6, LAY_away 3, LAY_home 3
- w05_e06: BACK_away 7, BACK_home 3, LAY_away 11, LAY_home 5
- w07_e03: BACK_away 14, BACK_home 10, LAY_away 6, LAY_home 2
- w07_e06: BACK_away 8, BACK_home 2, LAY_away 11, LAY_home 2
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 748}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 86, 'inplay_without_state': 26448, 'no_band': 41, 'spread_too_wide': 2, 'trade_rate': 60}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 11 / 0 | 13 / 13,550 | 8 / 0 | 11 / 0 | 11 / 0 | 13 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 3 / 25,460 | - | 3 / 44,100 | 3 / 44,100 | 7 / 50,000 | 2 / 50,000 | - |

