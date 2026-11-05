# PHOENIX paper replay

Window 2026-11-01T16:00:41.205386+00:00 to 2026-11-05T00:00:00+00:00; 25 matches considered; 14201 cycles run, 4972 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only', 'mkt_dyn', 'mkt_dyn_e06', 'placebo_random', 'placebo_favourite'], 'reference': 'mkt_dyn', 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)', 'market_dynamics': 'current: 20261104 (s97-1)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 258 | 176 | 147 | 29 | +0.0079 [-0.0031, +0.0187] (7 matches / 175 bets; too few matches) | +15554 [-246880, +222903] (7 matches / 176 bets; too few matches) | 97,760 | 25.14 | 5 (2.8%) |
| w03_e06 | 219 | 150 | 119 | 31 | +0.0081 [-0.0078, +0.0296] (7 matches / 149 bets; too few matches) | +18816 [-260184, +256338] (7 matches / 150 bets; too few matches) | 100,000 | 21.43 | 3 (2.0%) |
| w05_e03 | 245 | 169 | 138 | 31 | +0.0146 [+0.0080, +0.0233] (7 matches / 168 bets; too few matches) | +13735 [-245751, +181069] (7 matches / 169 bets; too few matches) | 100,000 | 24.14 | 5 (3.0%) |
| w05_e06 | 226 | 157 | 128 | 29 | +0.0134 [+0.0005, +0.0301] (7 matches / 153 bets; too few matches) | -31441 [-295597, +215231] (7 matches / 157 bets; too few matches) | 100,000 | 22.43 | 8 (5.1%) |
| w07_e03 | 205 | 151 | 133 | 18 | +0.0123 [-0.0002, +0.0212] (7 matches / 150 bets; too few matches) | +81266 [-211618, +285764] (7 matches / 151 bets; too few matches) | 100,000 | 21.57 | 12 (7.9%) |
| w07_e06 | 196 | 151 | 133 | 18 | +0.0148 [+0.0017, +0.0240] (7 matches / 149 bets; too few matches) | +76782 [-212482, +280457] (7 matches / 151 bets; too few matches) | 100,000 | 21.57 | 10 (6.6%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |
| mkt_dyn | 367 | 251 | 221 | 30 | +0.0326 [+0.0190, +0.0481] (11 matches / 250 bets) | +94390 [-226218, +408608] (11 matches / 251 bets) | 62,080 | 22.82 | 6 (2.4%) |
| mkt_dyn_e06 | 357 | 252 | 219 | 33 | +0.0246 [+0.0125, +0.0354] (9 matches / 252 bets; too few matches) | +175296 [-140479, +509768] (9 matches / 252 bets; too few matches) | 58,000 | 28.00 | 13 (5.2%) |
| placebo_random (placebo) | 288 | 171 | 155 | 16 | +0.0006 [-0.0081, +0.0103] (11 matches / 171 bets) | -365733 [-520811, -207668] (11 matches / 171 bets) | 71,910 | 15.55 | 4 (2.3%) |
| placebo_favourite (placebo) | 155 | 90 | 84 | 6 | +0.0153 [-0.0019, +0.0439] (11 matches / 88 bets) | +338889 [+145854, +554051] (11 matches / 90 bets) | 100,000 | 8.18 | 1 (1.1%) |

## Placebo controls (S85): excess CLV over placebo_random, match-clustered

The placebos copy the decision times and stakes of the reference variant (mkt_dyn). They are never primary, never ranked and never signals. The excess of a variant is, per match, its mean CLV minus placebo_random's mean CLV on that match; the interval resamples matches. A CI from fewer than 10 matches is labelled 'too few matches'.

Excess CLV over placebo_random is above zero (95% lower bound, at least 10 matches) for mkt_dyn.

| variant | excess CLV over placebo_random [95% CI] (matches / bets) | placebo_random CLV bets on those matches | above zero on 10+ matches |
|---|---|---:|---|
| w03_e03 | +0.0113 [-0.0071, +0.0294] (7 matches / 175 bets; too few matches) | 145 | no |
| w03_e06 | +0.0155 [-0.0098, +0.0414] (7 matches / 149 bets; too few matches) | 145 | no |
| w05_e03 | +0.0177 [+0.0018, +0.0304] (7 matches / 168 bets; too few matches) | 145 | no |
| w05_e06 | +0.0202 [-0.0036, +0.0410] (7 matches / 153 bets; too few matches) | 145 | no |
| w07_e03 | +0.0113 [-0.0105, +0.0265] (7 matches / 150 bets; too few matches) | 145 | no |
| w07_e06 | +0.0138 [-0.0085, +0.0299] (7 matches / 149 bets; too few matches) | 145 | no |
| market_only | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | no |
| mkt_dyn | +0.0263 [+0.0093, +0.0451] (11 matches / 250 bets) | 171 | yes |
| mkt_dyn_e06 | +0.0067 [-0.0427, +0.0422] (9 matches / 252 bets; too few matches) | 168 | no |


## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 258 | 176 | 82 | 68.2% | 71.0% | 0.0% (0) | 0 | 0.0% (0) | -0.1267 / -3.3% |
| w03_e06 | 219 | 150 | 69 | 68.5% | 74.7% | 0.0% (0) | 0 | 0.0% (0) | -0.1459 / -2.1% |
| w05_e03 | 245 | 169 | 76 | 69.0% | 63.2% | 0.0% (0) | 0 | 0.0% (0) | -0.0240 / -1.6% |
| w05_e06 | 226 | 157 | 69 | 69.5% | 63.9% | 0.0% (0) | 0 | 0.0% (0) | -0.0675 / -2.0% |
| w07_e03 | 205 | 151 | 54 | 73.7% | 70.2% | 0.0% (0) | 0 | 0.0% (0) | +0.0422 / 0.0% |
| w07_e06 | 196 | 151 | 45 | 77.0% | 66.6% | 0.0% (0) | 0 | 0.0% (0) | +0.0497 / 0.3% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |
| mkt_dyn | 367 | 251 | 116 | 68.4% | 67.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0253 / -2.2% |
| mkt_dyn_e06 | 357 | 252 | 105 | 70.6% | 67.2% | 0.0% (0) | 0 | 0.0% (0) | -0.0240 / -2.1% |
| placebo_random (placebo) | 288 | 171 | 117 | 59.4% | 56.8% | 0.0% (0) | 0 | 0.0% (0) | -0.2763 / -7.7% |
| placebo_favourite (placebo) | 155 | 90 | 65 | 58.1% | 61.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0610 / -3.6% |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e03 | in-play | 176 | +0.0079 [-0.0031, +0.0187] (7 matches / 175 bets; too few matches) | -0.0055 [-0.0121, +0.0099] (7 matches / 175 bets; too few matches) | +15554 [-246880, +222903] (7 matches / 176 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 150 | +0.0081 [-0.0078, +0.0296] (7 matches / 149 bets; too few matches) | -0.0107 [-0.0183, +0.0080] (7 matches / 149 bets; too few matches) | +18816 [-260184, +256338] (7 matches / 150 bets; too few matches) |
| w05_e03 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w05_e03 | in-play | 169 | +0.0146 [+0.0080, +0.0233] (7 matches / 168 bets; too few matches) | +0.0055 [-0.0016, +0.0204] (7 matches / 168 bets; too few matches) | +13735 [-245751, +181069] (7 matches / 169 bets; too few matches) |
| w05_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w05_e06 | in-play | 157 | +0.0134 [+0.0005, +0.0301] (7 matches / 153 bets; too few matches) | +0.0007 [-0.0057, +0.0145] (7 matches / 153 bets; too few matches) | -31441 [-295597, +215231] (7 matches / 157 bets; too few matches) |
| w07_e03 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w07_e03 | in-play | 151 | +0.0123 [-0.0002, +0.0212] (7 matches / 150 bets; too few matches) | +0.0017 [-0.0069, +0.0211] (7 matches / 150 bets; too few matches) | +81266 [-211618, +285764] (7 matches / 151 bets; too few matches) |
| w07_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w07_e06 | in-play | 151 | +0.0148 [+0.0017, +0.0240] (7 matches / 149 bets; too few matches) | +0.0006 [-0.0186, +0.0180] (7 matches / 149 bets; too few matches) | +76782 [-212482, +280457] (7 matches / 151 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | in-play | 251 | +0.0326 [+0.0190, +0.0481] (11 matches / 250 bets) | +0.0281 [+0.0104, +0.0497] (11 matches / 250 bets) | +94390 [-226218, +408608] (11 matches / 251 bets) |
| mkt_dyn_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn_e06 | in-play | 252 | +0.0246 [+0.0125, +0.0354] (9 matches / 252 bets; too few matches) | +0.0189 [+0.0069, +0.0307] (9 matches / 252 bets; too few matches) | +175296 [-140479, +509768] (9 matches / 252 bets; too few matches) |
| placebo_random (placebo) | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| placebo_random (placebo) | in-play | 171 | +0.0006 [-0.0081, +0.0103] (11 matches / 171 bets) | +0.0037 [-0.0114, +0.0168] (11 matches / 171 bets) | -365733 [-520811, -207668] (11 matches / 171 bets) |
| placebo_favourite (placebo) | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| placebo_favourite (placebo) | in-play | 90 | +0.0153 [-0.0019, +0.0439] (11 matches / 88 bets) | +0.0104 [-0.0072, +0.0420] (11 matches / 88 bets) | +338889 [+145854, +554051] (11 matches / 90 bets) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0079 [-0.0034, +0.0191] | n/a [-1918.29, +1973.99] |
| w03_e06 | +0.0081 [-0.0035, +0.0202] | n/a [-1984.02, +2201.97] |
| w05_e03 | +0.0146 [+0.0044, +0.0249] | n/a [-2126.86, +2471.90] |
| w05_e06 | +0.0134 [+0.0006, +0.0268] | n/a [-2877.75, +2641.11] |
| w07_e03 | +0.0123 [+0.0003, +0.0239] | n/a [-2069.34, +3056.55] |
| w07_e06 | +0.0148 [+0.0033, +0.0260] | n/a [-2462.95, +3410.29] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |
| mkt_dyn | +0.0326 [+0.0246, +0.0403] | n/a [-1948.85, +2894.99] |
| mkt_dyn_e06 | +0.0246 [+0.0161, +0.0335] | n/a [-2383.54, +3899.59] |
| placebo_random (placebo) | +0.0006 [-0.0086, +0.0097] | n/a [-4329.26, -208.65] |
| placebo_favourite (placebo) | +0.0153 [-0.0018, +0.0340] | n/a [+438.68, +6970.23] |

## Intents by side

- w03_e03: BACK_away 75, BACK_home 62, LAY_away 68, LAY_home 53
- w03_e06: BACK_away 65, BACK_home 47, LAY_away 48, LAY_home 59
- w05_e03: BACK_away 70, BACK_home 69, LAY_away 44, LAY_home 62
- w05_e06: BACK_away 64, BACK_home 57, LAY_away 53, LAY_home 52
- w07_e03: BACK_away 49, BACK_home 58, LAY_away 35, LAY_home 63
- w07_e06: BACK_away 50, BACK_home 53, LAY_away 40, LAY_home 53
- market_only: none
- mkt_dyn: BACK_away 79, BACK_home 60, LAY_away 109, LAY_home 119
- mkt_dyn_e06: BACK_away 87, BACK_home 71, LAY_away 90, LAY_home 109
- placebo_random (placebo): BACK_away 78, BACK_home 73, LAY_away 62, LAY_home 75
- placebo_favourite (placebo): BACK_away 84, BACK_home 71

## Skips (counted per cycle, summed over the run)

- Registry: {'bookmaker_only': 13672, 'settled': 170, 'simulated': 19027, 'too_early': 17278, 'twin': 1912}
- Matches: {'no_market': 1082}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 206, 'flip_cooldown': 643, 'inplay_without_state': 37386, 'no_band': 304, 'prematch_off': 39462, 'spread_too_wide': 1006, 'stale_book': 6, 'trade_rate': 5094}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst | mkt_dyn fills / worst | mkt_dyn_e06 fills / worst | placebo_random (placebo) fills / worst | placebo_favourite (placebo) fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | - | - | - | - | - | - | - | 2 / 50,000 | - | 1 / 24,250 | 4 / 35,950 |
| lb_36264203 | Wellington Firebirds v Northern Districts | Ford Trophy | - | - | - | - | - | - | - | 30 / 0 | 28 / 0 | 14 / 40,312 | 17 / 92,282 |
| lb_36266935 | Central Stags v Auckland Aces | Ford Trophy | 33 / 379 | 30 / 0 | 30 / 0 | 29 / 0 | 24 / 97,881 | 22 / 98,286 | - | 32 / 0 | 32 / 0 | 38 / 32,414 | 11 / 100,000 |
| lb_36266944 | Hobart Hurricanes W v Melbourne Renegades W | WBBL | 29 / 43,745 | 31 / 47,659 | 31 / 26,450 | 29 / 37,100 | 18 / 83,900 | 18 / 83,900 | - | 30 / 0 | 33 / 0 | 16 / 0 | 6 / 100,000 |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 25 / 56,878 | 21 / 99,239 | 28 / 0 | 29 / 29,098 | 28 / 0 | 31 / 0 | - | 30 / 0 | 29 / 0 | 13 / 67,677 | 12 / 100,000 |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 28 / 2,513 | 25 / 32,540 | 29 / 0 | 27 / 22,161 | 30 / 0 | 33 / 0 | - | 29 / 0 | 32 / 0 | 20 / 42,492 | 9 / 100,000 |
| lb_36269512 | West Indies A v England Lions | T20 Matches | 23 / 0 | 15 / 37,452 | 20 / 21,963 | 18 / 79,331 | 18 / 100,000 | 19 / 100,000 | - | 29 / 0 | 30 / 0 | 17 / 57,146 | 14 / 65,951 |
| lb_36271322 | New Zealand v India | One Day Internationals | 28 / 97,760 | 19 / 100,000 | 20 / 100,000 | 17 / 100,000 | 19 / 100,000 | 15 / 100,000 | - | 33 / 38,947 | 33 / 56,298 | 21 / 26,126 | 8 / 100,000 |
| lb_36273920 | West Indies A v England Lions | T20 Matches | 10 / 12,278 | 9 / 50,658 | 11 / 26,660 | 8 / 37,748 | 14 / 33,003 | 13 / 78,425 | - | 28 / 24,960 | 32 / 17,179 | 20 / 0 | 5 / 100,000 |
| lb_75747934 | Dubai v Sharjah | Emirates D20 | - | - | - | - | - | - | - | 3 / 40,594 | - | 2 / 13,680 | 2 / 14,000 |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | - | - | - | - | - | - | - | 5 / 62,080 | 3 / 58,000 | 9 / 71,910 | 2 / 29,000 |

