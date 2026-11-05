# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T12:30:00+00:00; 18 matches considered; 5672 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only', 'placebo_random', 'placebo_favourite'], 'reference': 'w05_e03', 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 66 | 45 | 29 | 0 | +0.0258 [+0.0183, +0.0296] (2 matches / 41 bets; too few matches) | +54300 [n/a, n/a] (1 match / 29 bets; too few matches) | 42,282 | 22.50 | 3 (6.7%) |
| w03_e06 | 54 | 31 | 20 | 0 | +0.0104 [+0.0097, +0.0118] (2 matches / 28 bets; too few matches) | +50110 [n/a, n/a] (1 match / 20 bets; too few matches) | 86,302 | 15.50 | 1 (3.2%) |
| w05_e03 | 79 | 53 | 31 | 0 | +0.0167 [+0.0165, +0.0168] (2 matches / 50 bets; too few matches) | +16696 [n/a, n/a] (1 match / 31 bets; too few matches) | 37,165 | 26.50 | 1 (1.9%) |
| w05_e06 | 69 | 47 | 28 | 0 | +0.0024 [-0.0103, +0.0245] (2 matches / 44 bets; too few matches) | +25219 [n/a, n/a] (1 match / 28 bets; too few matches) | 67,574 | 23.50 | 0 (0.0%) |
| w07_e03 | 84 | 55 | 34 | 0 | +0.0140 [+0.0041, +0.0197] (2 matches / 52 bets; too few matches) | +23818 [n/a, n/a] (1 match / 34 bets; too few matches) | 23,581 | 27.50 | 2 (3.6%) |
| w07_e06 | 63 | 50 | 30 | 0 | +0.0135 [+0.0114, +0.0146] (2 matches / 47 bets; too few matches) | +18741 [n/a, n/a] (1 match / 30 bets; too few matches) | 49,732 | 25.00 | 4 (8.0%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |
| placebo_random (placebo) | 60 | 38 | 17 | 0 | -0.0013 [-0.0160, +0.0126] (2 matches / 35 bets; too few matches) | -32015 [n/a, n/a] (1 match / 17 bets; too few matches) | 32,015 | 19.00 | 2 (5.3%) |
| placebo_favourite (placebo) | 27 | 14 | 10 | 0 | -0.0084 [-0.0405, +0.0718] (2 matches / 14 bets; too few matches) | +48891 [n/a, n/a] (1 match / 10 bets; too few matches) | 100,000 | 7.00 | 0 (0.0%) |

## Placebo controls (S85): excess CLV over placebo_random, match-clustered

The placebos copy the decision times and stakes of the reference variant (w05_e03). They are never primary, never ranked and never signals. The excess of a variant is, per match, its mean CLV minus placebo_random's mean CLV on that match; the interval resamples matches. A CI from fewer than 10 matches is labelled 'too few matches'.

No variant's CLV beats placebo_random: no excess-CLV confidence interval lies above zero on at least 10 matches.

| variant | excess CLV over placebo_random [95% CI] (matches / bets) | placebo_random CLV bets on those matches | above zero on 10+ matches |
|---|---|---:|---|
| w03_e03 | +0.0257 [+0.0058, +0.0456] (2 matches / 41 bets; too few matches) | 35 | no |
| w03_e06 | +0.0124 [-0.0008, +0.0257] (2 matches / 28 bets; too few matches) | 35 | no |
| w05_e03 | +0.0184 [+0.0039, +0.0328] (2 matches / 50 bets; too few matches) | 35 | no |
| w05_e06 | +0.0088 [+0.0057, +0.0119] (2 matches / 44 bets; too few matches) | 35 | no |
| w07_e03 | +0.0136 [-0.0085, +0.0356] (2 matches / 52 bets; too few matches) | 35 | no |
| w07_e06 | +0.0147 [-0.0012, +0.0306] (2 matches / 47 bets; too few matches) | 35 | no |
| market_only | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | no |


## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 66 | 45 | 21 | 68.2% | 72.1% | 0.0% (0) | 0 | 0.0% (0) | -0.1553 / -3.2% |
| w03_e06 | 54 | 31 | 23 | 57.4% | 59.0% | 0.0% (0) | 0 | 0.0% (0) | -0.2087 / -3.3% |
| w05_e03 | 79 | 53 | 26 | 67.1% | 65.7% | 0.0% (0) | 0 | 0.0% (0) | -0.0996 / -1.4% |
| w05_e06 | 69 | 47 | 22 | 68.1% | 69.3% | 0.0% (0) | 0 | 0.0% (0) | -0.1649 / -3.1% |
| w07_e03 | 84 | 55 | 29 | 65.5% | 65.9% | 0.0% (0) | 0 | 0.0% (0) | -0.0338 / -1.5% |
| w07_e06 | 63 | 50 | 13 | 79.4% | 83.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0606 / -1.0% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |
| placebo_random (placebo) | 60 | 38 | 22 | 63.3% | 68.6% | 0.0% (0) | 0 | 0.0% (0) | -0.0626 / -2.4% |
| placebo_favourite (placebo) | 27 | 14 | 13 | 51.9% | 51.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0336 / -2.1% |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 9 | +0.0120 [+0.0072, +0.0507] (2 matches / 9 bets; too few matches) | +0.0167 [+0.0090, +0.0507] (2 matches / 9 bets; too few matches) | -25000 [n/a, n/a] (1 match / 1 bet; too few matches) |
| w03_e03 | in-play | 36 | +0.0296 [+0.0288, +0.0332] (2 matches / 32 bets; too few matches) | +0.0202 [+0.0178, +0.0274] (2 matches / 32 bets; too few matches) | +79300 [n/a, n/a] (1 match / 28 bets; too few matches) |
| w03_e06 | pre-match | 3 | +0.0103 [n/a, n/a] (1 match / 3 bets; too few matches) | +0.0110 [n/a, n/a] (1 match / 3 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 28 | +0.0104 [+0.0097, +0.0126] (2 matches / 25 bets; too few matches) | +0.0059 [+0.0019, +0.0075] (2 matches / 25 bets; too few matches) | +50110 [n/a, n/a] (1 match / 20 bets; too few matches) |
| w05_e03 | pre-match | 14 | +0.0057 [+0.0021, +0.0105] (2 matches / 14 bets; too few matches) | +0.0045 [-0.0014, +0.0135] (2 matches / 14 bets; too few matches) | -33860 [n/a, n/a] (1 match / 6 bets; too few matches) |
| w05_e03 | in-play | 39 | +0.0210 [+0.0183, +0.0269] (2 matches / 36 bets; too few matches) | +0.0149 [+0.0117, +0.0222] (2 matches / 36 bets; too few matches) | +50556 [n/a, n/a] (1 match / 25 bets; too few matches) |
| w05_e06 | pre-match | 9 | +0.0151 [+0.0055, +0.0487] (2 matches / 9 bets; too few matches) | +0.0172 [-0.0007, +0.0487] (2 matches / 9 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | in-play | 38 | -0.0009 [-0.0148, +0.0393] (2 matches / 35 bets; too few matches) | +0.0172 [+0.0108, +0.0346] (2 matches / 35 bets; too few matches) | +75219 [n/a, n/a] (1 match / 26 bets; too few matches) |
| w07_e03 | pre-match | 19 | -0.0108 [-0.0272, -0.0012] (2 matches / 19 bets; too few matches) | -0.0079 [-0.0226, -0.0008] (2 matches / 19 bets; too few matches) | -35498 [n/a, n/a] (1 match / 12 bets; too few matches) |
| w07_e03 | in-play | 36 | +0.0282 [+0.0224, +0.0316] (2 matches / 33 bets; too few matches) | +0.0338 [+0.0325, +0.0347] (2 matches / 33 bets; too few matches) | +59316 [n/a, n/a] (1 match / 22 bets; too few matches) |
| w07_e06 | pre-match | 4 | +0.0094 [-0.0155, +0.0344] (2 matches / 4 bets; too few matches) | +0.0094 [-0.0155, +0.0344] (2 matches / 4 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w07_e06 | in-play | 46 | +0.0138 [+0.0132, +0.0150] (2 matches / 43 bets; too few matches) | +0.0130 [+0.0128, +0.0135] (2 matches / 43 bets; too few matches) | +68741 [n/a, n/a] (1 match / 28 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| placebo_random (placebo) | pre-match | 15 | -0.0089 [-0.0106, -0.0054] (2 matches / 15 bets; too few matches) | -0.0030 [-0.0060, +0.0043] (2 matches / 15 bets; too few matches) | -2509 [n/a, n/a] (1 match / 5 bets; too few matches) |
| placebo_random (placebo) | in-play | 23 | +0.0044 [-0.0204, +0.0415] (2 matches / 20 bets; too few matches) | -0.0159 [-0.0465, +0.0241] (2 matches / 20 bets; too few matches) | -29507 [n/a, n/a] (1 match / 12 bets; too few matches) |
| placebo_favourite (placebo) | pre-match | 5 | +0.0101 [-0.0406, +0.0860] (2 matches / 5 bets; too few matches) | +0.0229 [-0.0402, +0.0860] (2 matches / 5 bets; too few matches) | +39550 [n/a, n/a] (1 match / 3 bets; too few matches) |
| placebo_favourite (placebo) | in-play | 9 | -0.0187 [-0.0405, +0.0575] (2 matches / 9 bets; too few matches) | -0.0156 [-0.0502, +0.0575] (2 matches / 9 bets; too few matches) | +9341 [n/a, n/a] (1 match / 7 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0258 [+0.0010, +0.0535] | n/a [-4886.98, +8173.36] |
| w03_e06 | +0.0104 [-0.0107, +0.0325] | n/a [-5374.24, +10241.81] |
| w05_e03 | +0.0167 [+0.0029, +0.0298] | n/a [-6346.97, +7723.69] |
| w05_e06 | +0.0024 [-0.0161, +0.0205] | n/a [-8114.99, +11184.19] |
| w07_e03 | +0.0140 [-0.0008, +0.0287] | n/a [-4673.35, +6510.22] |
| w07_e06 | +0.0135 [+0.0000, +0.0258] | n/a [-9197.08, +11800.46] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |
| placebo_random (placebo) | -0.0013 [-0.0229, +0.0226] | n/a [-9559.64, +6068.93] |
| placebo_favourite (placebo) | -0.0084 [-0.0421, +0.0279] | n/a [-3328.62, +11346.06] |

## Intents by side

- w03_e03: BACK_away 19, BACK_home 14, LAY_away 26, LAY_home 7
- w03_e06: BACK_away 12, BACK_home 14, LAY_away 22, LAY_home 6
- w05_e03: BACK_away 24, BACK_home 24, LAY_away 20, LAY_home 11
- w05_e06: BACK_away 21, BACK_home 14, LAY_away 22, LAY_home 12
- w07_e03: BACK_away 23, BACK_home 23, LAY_away 21, LAY_home 17
- w07_e06: BACK_away 20, BACK_home 13, LAY_away 20, LAY_home 10
- market_only: none
- placebo_random (placebo): BACK_away 11, BACK_home 15, LAY_away 17, LAY_home 17
- placebo_favourite (placebo): BACK_away 4, BACK_home 23

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 4377, 'too_early': 6152}
- Matches: {'no_market': 735}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 147, 'inplay_without_state': 27816, 'no_band': 41, 'spread_too_wide': 20, 'trade_rate': 652}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst | placebo_random (placebo) fills / worst | placebo_favourite (placebo) fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 29 / 42,282 | 20 / 86,302 | 31 / 0 | 28 / 67,574 | 34 / 0 | 30 / 0 | - | 17 / 32,015 | 10 / 100,000 |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 16 / 13,063 | 11 / 29,400 | 22 / 37,165 | 19 / 37,630 | 21 / 23,581 | 20 / 49,732 | - | 21 / 0 | 4 / 100,000 |

