# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 16 matches considered; 5298 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 120, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 23 | 11 | 10 | 0 | +0.0178 [n/a, n/a] (1 match / 10 bets; too few matches) | +4326 [n/a, n/a] (1 match / 10 bets; too few matches) | 25,000 | 5.50 | 1 (9.1%) |
| w03_e06 | 14 | 11 | 11 | 0 | +0.0480 [n/a, n/a] (1 match / 11 bets; too few matches) | +8002 [n/a, n/a] (1 match / 11 bets; too few matches) | 0 | 11.00 | 1 (9.1%) |
| w05_e03 | 28 | 15 | 10 | 0 | +0.0359 [n/a, n/a] (1 match / 10 bets; too few matches) | +16103 [n/a, n/a] (1 match / 10 bets; too few matches) | 48,200 | 7.50 | 1 (6.7%) |
| w05_e06 | 18 | 12 | 10 | 0 | +0.0405 [n/a, n/a] (1 match / 10 bets; too few matches) | +24908 [n/a, n/a] (1 match / 10 bets; too few matches) | 50,000 | 6.00 | 0 (0.0%) |
| w07_e03 | 32 | 17 | 11 | 0 | +0.0101 [n/a, n/a] (1 match / 11 bets; too few matches) | +4180 [n/a, n/a] (1 match / 11 bets; too few matches) | 39,250 | 8.50 | 1 (5.9%) |
| w07_e06 | 24 | 13 | 11 | 0 | +0.0060 [n/a, n/a] (1 match / 11 bets; too few matches) | +15509 [n/a, n/a] (1 match / 11 bets; too few matches) | 50,000 | 6.50 | 1 (7.7%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 23 | 11 | 12 | 47.8% | 56.2% | 0.0% (0) | 0 | 0.0% (0) | -0.0491 / -1.9% |
| w03_e06 | 14 | 11 | 3 | 78.6% | 90.3% | 0.0% (0) | 0 | 0.0% (0) | -0.0782 / -3.1% |
| w05_e03 | 27 | 15 | 12 | 55.6% | 59.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0353 / -1.9% |
| w05_e06 | 17 | 12 | 5 | 70.6% | 64.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0392 / -1.9% |
| w07_e03 | 31 | 17 | 14 | 54.8% | 45.4% | 0.0% (0) | 0 | 0.0% (0) | -0.0253 / -1.1% |
| w07_e06 | 23 | 13 | 10 | 56.5% | 67.4% | 0.0% (0) | 0 | 0.0% (0) | -0.0262 / -1.4% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 4 | +0.0091 [n/a, n/a] (1 match / 3 bets; too few matches) | +0.0088 [n/a, n/a] (1 match / 3 bets; too few matches) | -26000 [n/a, n/a] (1 match / 3 bets; too few matches) |
| w03_e03 | in-play | 7 | +0.0215 [n/a, n/a] (1 match / 7 bets; too few matches) | +0.0659 [n/a, n/a] (1 match / 7 bets; too few matches) | +30326 [n/a, n/a] (1 match / 7 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 11 | +0.0480 [n/a, n/a] (1 match / 11 bets; too few matches) | +0.0252 [n/a, n/a] (1 match / 11 bets; too few matches) | +8002 [n/a, n/a] (1 match / 11 bets; too few matches) |
| w05_e03 | pre-match | 10 | +0.0108 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0169 [n/a, n/a] (1 match / 5 bets; too few matches) | -34340 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w05_e03 | in-play | 5 | +0.0610 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0812 [n/a, n/a] (1 match / 5 bets; too few matches) | +50443 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w05_e06 | pre-match | 4 | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | in-play | 8 | +0.0384 [n/a, n/a] (1 match / 8 bets; too few matches) | +0.0601 [n/a, n/a] (1 match / 8 bets; too few matches) | +74908 [n/a, n/a] (1 match / 8 bets; too few matches) |
| w07_e03 | pre-match | 15 | -0.0010 [n/a, n/a] (1 match / 9 bets; too few matches) | -0.0012 [n/a, n/a] (1 match / 9 bets; too few matches) | -36820 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | in-play | 2 | +0.0601 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0864 [n/a, n/a] (1 match / 2 bets; too few matches) | +41000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w07_e06 | pre-match | 4 | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w07_e06 | in-play | 9 | -0.0035 [n/a, n/a] (1 match / 9 bets; too few matches) | +0.0453 [n/a, n/a] (1 match / 9 bets; too few matches) | +65509 [n/a, n/a] (1 match / 9 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0178 [-0.0189, +0.0524] | n/a [-10868.66, +12425.02] |
| w03_e06 | +0.0480 [+0.0162, +0.0839] | n/a [-11728.81, +14546.19] |
| w05_e03 | +0.0359 [+0.0089, +0.0676] | n/a [-10507.45, +16399.24] |
| w05_e06 | +0.0405 [+0.0084, +0.0706] | n/a [-14864.98, +22980.82] |
| w07_e03 | +0.0101 [-0.0105, +0.0320] | n/a [-9257.77, +11581.64] |
| w07_e06 | +0.0060 [-0.0346, +0.0426] | n/a [-11316.09, +17856.03] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 6, BACK_home 1, LAY_away 12, LAY_home 4
- w03_e06: BACK_away 4, BACK_home 3, LAY_away 6, LAY_home 1
- w05_e03: BACK_away 9, BACK_home 6, LAY_away 5, LAY_home 8
- w05_e06: BACK_away 7, BACK_home 3, LAY_away 5, LAY_home 3
- w07_e03: BACK_away 16, BACK_home 6, LAY_away 8, LAY_home 2
- w07_e06: BACK_away 6, BACK_home 2, LAY_away 6, LAY_home 10
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 748}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 91, 'inplay_without_state': 26444, 'no_band': 41, 'spread_too_wide': 2, 'trade_rate': 19}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 10 / 0 | 11 / 0 | 10 / 0 | 10 / 0 | 11 / 0 | 11 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 1 / 25,000 | - | 5 / 48,200 | 2 / 50,000 | 6 / 39,250 | 2 / 50,000 | - |

