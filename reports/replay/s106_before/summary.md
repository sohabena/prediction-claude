# PHOENIX paper replay

Window 2026-11-01T16:00:41.205386+00:00 to 2026-11-05T00:00:00+00:00; 25 matches considered; 14201 cycles run, 4972 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only', 'mkt_dyn', 'mkt_dyn_e06', 'placebo_random', 'placebo_favourite'], 'reference': 'mkt_dyn', 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)', 'market_dynamics': 'current: 20261104 (s97-1)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 280 | 180 | 151 | 29 | +0.0073 [-0.0045, +0.0207] (7 matches / 178 bets; too few matches) | +34644 [-235046, +249031] (7 matches / 180 bets; too few matches) | 97,760 | 25.71 | 5 (2.8%) |
| w03_e06 | 217 | 151 | 122 | 29 | +0.0090 [-0.0075, +0.0302] (7 matches / 150 bets; too few matches) | +18666 [-260339, +256338] (7 matches / 151 bets; too few matches) | 100,000 | 21.57 | 3 (2.0%) |
| w05_e03 | 257 | 172 | 142 | 30 | +0.0141 [+0.0086, +0.0228] (7 matches / 171 bets; too few matches) | +26352 [-232515, +190971] (7 matches / 172 bets; too few matches) | 100,000 | 24.57 | 4 (2.3%) |
| w05_e06 | 223 | 152 | 130 | 22 | +0.0164 [+0.0035, +0.0319] (7 matches / 149 bets; too few matches) | -1234 [-270212, +239033] (7 matches / 152 bets; too few matches) | 100,000 | 21.71 | 8 (5.3%) |
| w07_e03 | 228 | 163 | 149 | 14 | +0.0107 [-0.0048, +0.0212] (7 matches / 162 bets; too few matches) | +49798 [-216712, +221365] (7 matches / 163 bets; too few matches) | 100,000 | 23.29 | 11 (6.7%) |
| w07_e06 | 185 | 144 | 132 | 12 | +0.0152 [-0.0004, +0.0247] (7 matches / 143 bets; too few matches) | +77653 [-205151, +269849] (7 matches / 144 bets; too few matches) | 100,000 | 20.57 | 10 (6.9%) |
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
| w03_e03 | +0.0109 [-0.0102, +0.0319] (7 matches / 178 bets; too few matches) | 145 | no |
| w03_e06 | +0.0160 [-0.0094, +0.0419] (7 matches / 150 bets; too few matches) | 145 | no |
| w05_e03 | +0.0173 [+0.0038, +0.0279] (7 matches / 171 bets; too few matches) | 145 | no |
| w05_e06 | +0.0216 [-0.0019, +0.0427] (7 matches / 149 bets; too few matches) | 145 | no |
| w07_e03 | +0.0072 [-0.0235, +0.0288] (7 matches / 162 bets; too few matches) | 145 | no |
| w07_e06 | +0.0108 [-0.0188, +0.0313] (7 matches / 143 bets; too few matches) | 145 | no |
| market_only | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | no |
| mkt_dyn | +0.0263 [+0.0093, +0.0451] (11 matches / 250 bets) | 171 | yes |
| mkt_dyn_e06 | +0.0067 [-0.0427, +0.0422] (9 matches / 252 bets; too few matches) | 168 | no |


## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 280 | 180 | 100 | 64.3% | 66.4% | 0.0% (0) | 0 | 0.0% (0) | -0.1214 / -3.3% |
| w03_e06 | 217 | 151 | 66 | 69.6% | 73.8% | 0.0% (0) | 0 | 0.0% (0) | -0.1458 / -2.2% |
| w05_e03 | 257 | 172 | 85 | 66.9% | 60.4% | 0.0% (0) | 0 | 0.0% (0) | -0.0180 / -1.6% |
| w05_e06 | 223 | 152 | 71 | 68.2% | 60.8% | 0.0% (0) | 0 | 0.0% (0) | -0.0284 / -1.6% |
| w07_e03 | 228 | 163 | 65 | 71.5% | 62.6% | 0.0% (0) | 0 | 0.0% (0) | +0.0367 / 0.2% |
| w07_e06 | 185 | 144 | 41 | 77.8% | 68.2% | 0.0% (0) | 0 | 0.0% (0) | +0.0549 / 0.4% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |
| mkt_dyn | 367 | 251 | 116 | 68.4% | 67.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0253 / -2.2% |
| mkt_dyn_e06 | 357 | 252 | 105 | 70.6% | 67.2% | 0.0% (0) | 0 | 0.0% (0) | -0.0240 / -2.1% |
| placebo_random (placebo) | 288 | 171 | 117 | 59.4% | 56.8% | 0.0% (0) | 0 | 0.0% (0) | -0.2763 / -7.7% |
| placebo_favourite (placebo) | 155 | 90 | 65 | 58.1% | 61.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0610 / -3.6% |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 11 | +0.0005 [-0.0268, +0.0554] (3 matches / 11 bets; too few matches) | +0.0027 [-0.0249, +0.0554] (3 matches / 11 bets; too few matches) | -53510 [-85530, +0] (3 matches / 11 bets; too few matches) |
| w03_e03 | in-play | 169 | +0.0077 [-0.0040, +0.0205] (7 matches / 167 bets; too few matches) | -0.0007 [-0.0055, +0.0088] (7 matches / 167 bets; too few matches) | +88154 [-209838, +343764] (7 matches / 169 bets; too few matches) |
| w03_e06 | pre-match | 5 | -0.0039 [-0.0231, +0.0090] (2 matches / 5 bets; too few matches) | -0.0056 [-0.0231, +0.0091] (2 matches / 5 bets; too few matches) | -44700 [-89400, +0] (2 matches / 5 bets; too few matches) |
| w03_e06 | in-play | 146 | +0.0094 [-0.0074, +0.0303] (7 matches / 145 bets; too few matches) | -0.0104 [-0.0179, +0.0080] (7 matches / 145 bets; too few matches) | +63366 [-218857, +273549] (7 matches / 146 bets; too few matches) |
| w05_e03 | pre-match | 15 | -0.0020 [-0.0372, +0.0139] (3 matches / 15 bets; too few matches) | -0.0006 [-0.0408, +0.0208] (3 matches / 15 bets; too few matches) | -77660 [-129660, +0] (3 matches / 15 bets; too few matches) |
| w05_e03 | in-play | 157 | +0.0157 [+0.0097, +0.0246] (7 matches / 156 bets; too few matches) | +0.0131 [+0.0059, +0.0274] (7 matches / 156 bets; too few matches) | +104012 [-197863, +320387] (7 matches / 157 bets; too few matches) |
| w05_e06 | pre-match | 12 | +0.0042 [-0.0268, +0.0535] (3 matches / 12 bets; too few matches) | +0.0041 [-0.0275, +0.0535] (3 matches / 12 bets; too few matches) | -94100 [-150000, +0] (3 matches / 12 bets; too few matches) |
| w05_e06 | in-play | 140 | +0.0174 [+0.0046, +0.0324] (7 matches / 137 bets; too few matches) | +0.0011 [-0.0072, +0.0194] (7 matches / 137 bets; too few matches) | +92866 [-206338, +316454] (7 matches / 140 bets; too few matches) |
| w07_e03 | pre-match | 32 | -0.0169 [-0.0472, +0.0039] (4 matches / 32 bets; too few matches) | -0.0081 [-0.0444, +0.0100] (4 matches / 32 bets; too few matches) | -81938 [-170996, +7120] (4 matches / 32 bets; too few matches) |
| w07_e03 | in-play | 131 | +0.0175 [+0.0042, +0.0269] (7 matches / 130 bets; too few matches) | +0.0109 [-0.0042, +0.0319] (7 matches / 130 bets; too few matches) | +131736 [-195648, +376509] (7 matches / 131 bets; too few matches) |
| w07_e06 | pre-match | 7 | -0.0127 [-0.0372, +0.0392] (3 matches / 7 bets; too few matches) | -0.0096 [-0.0405, +0.0392] (3 matches / 7 bets; too few matches) | -100000 [-150000, +0] (3 matches / 7 bets; too few matches) |
| w07_e06 | in-play | 137 | +0.0166 [+0.0025, +0.0250] (7 matches / 136 bets; too few matches) | +0.0027 [-0.0201, +0.0223] (7 matches / 136 bets; too few matches) | +177653 [-163084, +453153] (7 matches / 137 bets; too few matches) |
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
| w03_e03 | +0.0073 [-0.0036, +0.0176] | n/a [-1720.94, +2138.00] |
| w03_e06 | +0.0090 [-0.0024, +0.0210] | n/a [-1959.80, +2271.21] |
| w05_e03 | +0.0141 [+0.0049, +0.0235] | n/a [-2099.18, +2403.94] |
| w05_e06 | +0.0164 [+0.0043, +0.0287] | n/a [-3146.32, +3017.49] |
| w07_e03 | +0.0107 [-0.0001, +0.0219] | n/a [-2230.65, +2652.63] |
| w07_e06 | +0.0152 [+0.0041, +0.0272] | n/a [-2635.65, +3863.47] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |
| mkt_dyn | +0.0326 [+0.0246, +0.0403] | n/a [-1948.85, +2894.99] |
| mkt_dyn_e06 | +0.0246 [+0.0161, +0.0335] | n/a [-2383.54, +3899.59] |
| placebo_random (placebo) | +0.0006 [-0.0086, +0.0097] | n/a [-4329.26, -208.65] |
| placebo_favourite (placebo) | +0.0153 [-0.0018, +0.0340] | n/a [+438.68, +6970.23] |

## Intents by side

- w03_e03: BACK_away 84, BACK_home 61, LAY_away 71, LAY_home 64
- w03_e06: BACK_away 58, BACK_home 51, LAY_away 49, LAY_home 59
- w05_e03: BACK_away 77, BACK_home 70, LAY_away 47, LAY_home 63
- w05_e06: BACK_away 55, BACK_home 53, LAY_away 50, LAY_home 65
- w07_e03: BACK_away 51, BACK_home 68, LAY_away 47, LAY_home 62
- w07_e06: BACK_away 48, BACK_home 48, LAY_away 40, LAY_home 49
- market_only: none
- mkt_dyn: BACK_away 79, BACK_home 60, LAY_away 109, LAY_home 119
- mkt_dyn_e06: BACK_away 87, BACK_home 71, LAY_away 90, LAY_home 109
- placebo_random (placebo): BACK_away 78, BACK_home 73, LAY_away 62, LAY_home 75
- placebo_favourite (placebo): BACK_away 84, BACK_home 71

## Skips (counted per cycle, summed over the run)

- Registry: {'bookmaker_only': 13672, 'settled': 170, 'simulated': 19027, 'too_early': 17278, 'twin': 1912}
- Matches: {'no_market': 1082}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 206, 'flip_cooldown': 760, 'inplay_without_state': 37385, 'no_band': 304, 'spread_too_wide': 980, 'trade_rate': 4811}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst | mkt_dyn fills / worst | mkt_dyn_e06 fills / worst | placebo_random (placebo) fills / worst | placebo_favourite (placebo) fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | - | - | - | - | - | - | - | 2 / 50,000 | - | 1 / 24,250 | 4 / 35,950 |
| lb_36264203 | Wellington Firebirds v Northern Districts | Ford Trophy | - | - | - | - | - | - | - | 30 / 0 | 28 / 0 | 14 / 40,312 | 17 / 92,282 |
| lb_36266935 | Central Stags v Auckland Aces | Ford Trophy | 33 / 379 | 30 / 0 | 30 / 0 | 29 / 0 | 24 / 97,881 | 22 / 98,286 | - | 32 / 0 | 32 / 0 | 38 / 32,414 | 11 / 100,000 |
| lb_36266944 | Hobart Hurricanes W v Melbourne Renegades W | WBBL | 29 / 60,270 | 29 / 58,700 | 30 / 37,550 | 22 / 63,000 | 14 / 90,900 | 12 / 89,500 | - | 30 / 0 | 33 / 0 | 16 / 0 | 6 / 100,000 |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 24 / 48,200 | 21 / 99,239 | 29 / 0 | 27 / 30,261 | 33 / 0 | 30 / 0 | - | 30 / 0 | 29 / 0 | 13 / 67,677 | 12 / 100,000 |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 33 / 0 | 28 / 32,690 | 32 / 0 | 31 / 0 | 34 / 0 | 33 / 0 | - | 29 / 0 | 32 / 0 | 20 / 42,492 | 9 / 100,000 |
| lb_36269512 | West Indies A v England Lions | T20 Matches | 23 / 0 | 15 / 37,452 | 20 / 21,963 | 18 / 79,331 | 18 / 100,000 | 19 / 100,000 | - | 29 / 0 | 30 / 0 | 17 / 57,146 | 14 / 65,951 |
| lb_36271322 | New Zealand v India | One Day Internationals | 28 / 97,760 | 19 / 100,000 | 20 / 100,000 | 17 / 100,000 | 26 / 99,650 | 15 / 100,000 | - | 33 / 38,947 | 33 / 56,298 | 21 / 26,126 | 8 / 100,000 |
| lb_36273920 | West Indies A v England Lions | T20 Matches | 10 / 12,278 | 9 / 50,658 | 11 / 26,660 | 8 / 37,748 | 14 / 33,003 | 13 / 78,425 | - | 28 / 24,960 | 32 / 17,179 | 20 / 0 | 5 / 100,000 |
| lb_75747934 | Dubai v Sharjah | Emirates D20 | - | - | - | - | - | - | - | 3 / 40,594 | - | 2 / 13,680 | 2 / 14,000 |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | - | - | - | - | - | - | - | 5 / 62,080 | 3 / 58,000 | 9 / 71,910 | 2 / 29,000 |

