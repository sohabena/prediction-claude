# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T12:30:00+00:00; 18 matches considered; 5672 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 24 | 19 | 9 | 0 | +0.0252 [+0.0232, +0.0276] (2 matches / 19 bets; too few matches) | +5879 [n/a, n/a] (1 match / 9 bets; too few matches) | 9,300 | 9.50 | 2 (10.5%) |
| w03_e06 | 29 | 21 | 11 | 0 | +0.0105 [+0.0094, +0.0118] (2 matches / 20 bets; too few matches) | +6700 [n/a, n/a] (1 match / 11 bets; too few matches) | 17,061 | 10.50 | 2 (9.5%) |
| w05_e03 | 36 | 20 | 8 | 0 | +0.0136 [+0.0043, +0.0197] (2 matches / 20 bets; too few matches) | +6755 [n/a, n/a] (1 match / 8 bets; too few matches) | 13,400 | 10.00 | 0 (0.0%) |
| w05_e06 | 29 | 22 | 8 | 0 | +0.0277 [+0.0064, +0.0399] (2 matches / 22 bets; too few matches) | +12033 [n/a, n/a] (1 match / 8 bets; too few matches) | 16,760 | 11.00 | 1 (4.5%) |
| w07_e03 | 42 | 23 | 10 | 0 | +0.0049 [+0.0037, +0.0058] (2 matches / 23 bets; too few matches) | +3120 [n/a, n/a] (1 match / 10 bets; too few matches) | 17,250 | 11.50 | 1 (4.3%) |
| w07_e06 | 30 | 21 | 10 | 0 | +0.0269 [+0.0172, +0.0375] (2 matches / 21 bets; too few matches) | +16313 [n/a, n/a] (1 match / 10 bets; too few matches) | 23,300 | 10.50 | 2 (9.5%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 24 | 19 | 5 | 79.2% | 72.3% | 0.0% (0) | 0 | 0.0% (0) | -0.0537 / -1.9% |
| w03_e06 | 29 | 21 | 8 | 72.4% | 73.9% | 0.0% (0) | 0 | 0.0% (0) | -0.0390 / -1.5% |
| w05_e03 | 36 | 20 | 16 | 55.6% | 49.8% | 0.0% (0) | 0 | 0.0% (0) | -0.0230 / -0.9% |
| w05_e06 | 29 | 22 | 7 | 75.9% | 67.7% | 0.0% (0) | 0 | 0.0% (0) | -0.0482 / -1.8% |
| w07_e03 | 42 | 23 | 19 | 54.8% | 46.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0239 / -0.8% |
| w07_e06 | 30 | 21 | 9 | 70.0% | 65.9% | 0.0% (0) | 0 | 0.0% (0) | -0.0348 / -1.2% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 9 | +0.0120 [+0.0072, +0.0507] (2 matches / 9 bets; too few matches) | +0.0167 [+0.0090, +0.0507] (2 matches / 9 bets; too few matches) | -25000 [n/a, n/a] (1 match / 1 bet; too few matches) |
| w03_e03 | in-play | 10 | +0.0372 [+0.0247, +0.0872] (2 matches / 10 bets; too few matches) | +0.0398 [+0.0396, +0.0417] (2 matches / 10 bets; too few matches) | +30879 [n/a, n/a] (1 match / 8 bets; too few matches) |
| w03_e06 | pre-match | 3 | +0.0103 [n/a, n/a] (1 match / 3 bets; too few matches) | +0.0110 [n/a, n/a] (1 match / 3 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 18 | +0.0106 [+0.0094, +0.0126] (2 matches / 17 bets; too few matches) | +0.0093 [+0.0055, +0.0122] (2 matches / 17 bets; too few matches) | +6700 [n/a, n/a] (1 match / 11 bets; too few matches) |
| w05_e03 | pre-match | 14 | +0.0057 [+0.0021, +0.0105] (2 matches / 14 bets; too few matches) | +0.0045 [-0.0014, +0.0135] (2 matches / 14 bets; too few matches) | -33860 [n/a, n/a] (1 match / 6 bets; too few matches) |
| w05_e03 | in-play | 6 | +0.0318 [-0.0144, +0.0549] (2 matches / 6 bets; too few matches) | +0.0491 [+0.0490, +0.0492] (2 matches / 6 bets; too few matches) | +40615 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | pre-match | 9 | +0.0151 [+0.0055, +0.0487] (2 matches / 9 bets; too few matches) | +0.0172 [-0.0007, +0.0487] (2 matches / 9 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | in-play | 13 | +0.0364 [-0.0078, +0.0742] (2 matches / 13 bets; too few matches) | +0.0497 [+0.0403, +0.0584] (2 matches / 13 bets; too few matches) | +62033 [n/a, n/a] (1 match / 6 bets; too few matches) |
| w07_e03 | pre-match | 16 | -0.0142 [-0.0272, -0.0041] (2 matches / 16 bets; too few matches) | -0.0102 [-0.0226, -0.0018] (2 matches / 16 bets; too few matches) | -36880 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | in-play | 7 | +0.0486 [+0.0444, +0.0733] (2 matches / 7 bets; too few matches) | +0.0509 [+0.0441, +0.0733] (2 matches / 7 bets; too few matches) | +40000 [n/a, n/a] (1 match / 1 bet; too few matches) |
| w07_e06 | pre-match | 4 | +0.0094 [-0.0155, +0.0344] (2 matches / 4 bets; too few matches) | +0.0094 [-0.0155, +0.0344] (2 matches / 4 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w07_e06 | in-play | 17 | +0.0310 [+0.0245, +0.0383] (2 matches / 17 bets; too few matches) | +0.0323 [+0.0149, +0.0464] (2 matches / 17 bets; too few matches) | +66313 [n/a, n/a] (1 match / 8 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0252 [+0.0077, +0.0448] | n/a [-10313.95, +12028.76] |
| w03_e06 | +0.0105 [-0.0097, +0.0308] | n/a [-10118.41, +12837.27] |
| w05_e03 | +0.0136 [-0.0048, +0.0307] | n/a [-12047.97, +16789.06] |
| w05_e06 | +0.0277 [+0.0029, +0.0519] | n/a [-13951.55, +22202.84] |
| w07_e03 | +0.0049 [-0.0136, +0.0236] | n/a [-10458.55, +11581.50] |
| w07_e06 | +0.0269 [+0.0081, +0.0473] | n/a [-13743.67, +19443.08] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 10, BACK_home 5, LAY_away 8, LAY_home 1
- w03_e06: BACK_away 7, BACK_home 9, LAY_away 9, LAY_home 4
- w05_e03: BACK_away 14, BACK_home 11, LAY_away 8, LAY_home 3
- w05_e06: BACK_away 9, BACK_home 8, LAY_away 8, LAY_home 4
- w07_e03: BACK_away 16, BACK_home 14, LAY_away 10, LAY_home 2
- w07_e06: BACK_away 8, BACK_home 6, LAY_away 13, LAY_home 3
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 4377, 'too_early': 6152}
- Matches: {'no_market': 735}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 35, 'inplay_without_state': 27822, 'no_band': 41, 'spread_too_wide': 2, 'trade_rate': 124}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 9 / 0 | 11 / 0 | 8 / 0 | 8 / 0 | 10 / 0 | 10 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 10 / 9,300 | 10 / 17,061 | 12 / 13,400 | 14 / 16,760 | 13 / 17,250 | 11 / 23,300 | - |

