# PHOENIX paper replay

Window 2026-11-01T16:00:41.205386+00:00 to 2026-11-05T06:00:00+00:00; 29 matches considered; 15641 cycles run, 4972 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only', 'mkt_dyn', 'mkt_dyn_e06', 'tail', 'placebo_random', 'placebo_favourite'], 'reference': 'mkt_dyn', 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)', 'market_dynamics': 'current: 20261104 (s97-1)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 329 | 214 | 151 | 29 | +0.0070 [-0.0034, +0.0192] (8 matches / 203 bets; too few matches) | +34644 [-235046, +249031] (7 matches / 180 bets; too few matches) | 97,760 | 23.78 | 5 (2.3%) |
| w03_e06 | 246 | 171 | 122 | 29 | +0.0074 [-0.0072, +0.0261] (8 matches / 167 bets; too few matches) | +18666 [-260339, +256338] (7 matches / 151 bets; too few matches) | 100,000 | 21.38 | 3 (1.8%) |
| w05_e03 | 304 | 206 | 142 | 30 | +0.0136 [+0.0090, +0.0208] (8 matches / 201 bets; too few matches) | +26352 [-232515, +190971] (7 matches / 172 bets; too few matches) | 100,000 | 22.89 | 4 (1.9%) |
| w05_e06 | 263 | 181 | 130 | 22 | +0.0163 [+0.0054, +0.0288] (8 matches / 171 bets; too few matches) | -1234 [-270212, +239033] (7 matches / 152 bets; too few matches) | 100,000 | 20.11 | 8 (4.4%) |
| w07_e03 | 268 | 190 | 149 | 14 | +0.0096 [-0.0035, +0.0194] (8 matches / 185 bets; too few matches) | +49798 [-216712, +221365] (7 matches / 163 bets; too few matches) | 100,000 | 21.11 | 11 (5.8%) |
| w07_e06 | 216 | 165 | 132 | 12 | +0.0145 [+0.0010, +0.0233] (8 matches / 160 bets; too few matches) | +77653 [-205151, +269849] (7 matches / 144 bets; too few matches) | 100,000 | 18.33 | 10 (6.1%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |
| mkt_dyn | 390 | 271 | 221 | 30 | +0.0320 [+0.0198, +0.0472] (12 matches / 267 bets) | +94390 [-226218, +408608] (11 matches / 251 bets) | 62,080 | 22.58 | 9 (3.3%) |
| mkt_dyn_e06 | 378 | 267 | 219 | 33 | +0.0245 [+0.0128, +0.0345] (10 matches / 265 bets) | +175296 [-140479, +509768] (9 matches / 252 bets; too few matches) | 58,000 | 26.70 | 14 (5.2%) |
| tail | 5 | 5 | 3 | 2 | +0.0199 [-0.0053, +0.0522] (3 matches / 5 bets; too few matches) | -6000 [-12000, +0] (3 matches / 5 bets; too few matches) | 4,000 | 1.67 | 0 (0.0%) |
| placebo_random (placebo) | 308 | 179 | 155 | 16 | -0.0004 [-0.0092, +0.0087] (12 matches / 178 bets) | -365733 [-520811, -207668] (11 matches / 171 bets) | 71,910 | 14.92 | 6 (3.4%) |
| placebo_favourite (placebo) | 174 | 101 | 84 | 6 | +0.0102 [-0.0064, +0.0343] (12 matches / 99 bets) | +338889 [+145854, +554051] (11 matches / 90 bets) | 100,000 | 8.42 | 2 (2.0%) |

## Placebo controls (S85): excess CLV over placebo_random, match-clustered

The placebos copy the decision times and stakes of the reference variant (mkt_dyn). They are never primary, never ranked and never signals. The excess of a variant is, per match, its mean CLV minus placebo_random's mean CLV on that match; the interval resamples matches. A CI from fewer than 10 matches is labelled 'too few matches'.

Excess CLV over placebo_random is above zero (95% lower bound, at least 10 matches) for mkt_dyn.

| variant | excess CLV over placebo_random [95% CI] (matches / bets) | placebo_random CLV bets on those matches | above zero on 10+ matches |
|---|---|---:|---|
| w03_e03 | +0.0132 [-0.0064, +0.0314] (8 matches / 203 bets; too few matches) | 152 | no |
| w03_e06 | +0.0162 [-0.0067, +0.0391] (8 matches / 167 bets; too few matches) | 152 | no |
| w05_e03 | +0.0195 [+0.0071, +0.0295] (8 matches / 201 bets; too few matches) | 152 | no |
| w05_e06 | +0.0240 [+0.0031, +0.0420] (8 matches / 171 bets; too few matches) | 152 | no |
| w07_e03 | +0.0096 [-0.0181, +0.0283] (8 matches / 185 bets; too few matches) | 152 | no |
| w07_e06 | +0.0136 [-0.0138, +0.0316] (8 matches / 160 bets; too few matches) | 152 | no |
| market_only | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | no |
| mkt_dyn | +0.0282 [+0.0134, +0.0462] (12 matches / 267 bets) | 178 | yes |
| mkt_dyn_e06 | +0.0106 [-0.0345, +0.0429] (10 matches / 265 bets) | 175 | no |
| tail | +0.0032 [-0.0327, +0.0587] (3 matches / 5 bets; too few matches) | 56 | no |


## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 329 | 214 | 115 | 65.0% | 66.2% | 0.0% (0) | 0 | 0.0% (0) | -0.1051 / -2.9% |
| w03_e06 | 246 | 171 | 75 | 69.5% | 72.6% | 0.0% (0) | 0 | 0.0% (0) | -0.1368 / -2.3% |
| w05_e03 | 303 | 206 | 97 | 68.0% | 61.1% | 0.0% (0) | 0 | 0.0% (0) | -0.0178 / -1.5% |
| w05_e06 | 262 | 181 | 81 | 69.1% | 61.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0251 / -1.4% |
| w07_e03 | 267 | 190 | 77 | 71.2% | 61.6% | 0.0% (0) | 0 | 0.0% (0) | +0.0341 / 0.2% |
| w07_e06 | 215 | 165 | 50 | 76.7% | 67.5% | 0.0% (0) | 0 | 0.0% (0) | +0.0473 / 0.3% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |
| mkt_dyn | 390 | 271 | 119 | 69.5% | 67.7% | 0.0% (0) | 0 | 0.0% (0) | -0.0277 / -2.3% |
| mkt_dyn_e06 | 377 | 267 | 110 | 70.8% | 67.8% | 0.0% (0) | 0 | 0.0% (0) | -0.0263 / -2.1% |
| tail | 5 | 5 | 0 | 100.0% | 100.0% | 0.0% (0) | 0 | 0.0% (0) | +5.3000 / -1.4% |
| placebo_random (placebo) | 308 | 179 | 129 | 58.1% | 56.1% | 0.0% (0) | 0 | 0.0% (0) | -0.2672 / -7.6% |
| placebo_favourite (placebo) | 174 | 101 | 73 | 58.0% | 61.1% | 0.0% (0) | 0 | 0.0% (0) | -0.0596 / -3.6% |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 23 | +0.0000 [-0.0145, +0.0147] (4 matches / 19 bets; too few matches) | +0.0021 [-0.0141, +0.0271] (4 matches / 19 bets; too few matches) | -53510 [-85530, +0] (3 matches / 11 bets; too few matches) |
| w03_e03 | in-play | 191 | +0.0077 [-0.0030, +0.0202] (8 matches / 184 bets; too few matches) | -0.0013 [-0.0058, +0.0062] (8 matches / 184 bets; too few matches) | +88154 [-209838, +343764] (7 matches / 169 bets; too few matches) |
| w03_e06 | pre-match | 5 | -0.0039 [-0.0231, +0.0090] (2 matches / 5 bets; too few matches) | -0.0056 [-0.0231, +0.0091] (2 matches / 5 bets; too few matches) | -44700 [-89400, +0] (2 matches / 5 bets; too few matches) |
| w03_e06 | in-play | 166 | +0.0077 [-0.0071, +0.0271] (8 matches / 162 bets; too few matches) | -0.0110 [-0.0177, +0.0037] (8 matches / 162 bets; too few matches) | +63366 [-218857, +273549] (7 matches / 146 bets; too few matches) |
| w05_e03 | pre-match | 35 | -0.0020 [-0.0189, +0.0067] (4 matches / 33 bets; too few matches) | -0.0007 [-0.0184, +0.0103] (4 matches / 33 bets; too few matches) | -77660 [-129660, +0] (3 matches / 15 bets; too few matches) |
| w05_e03 | in-play | 171 | +0.0167 [+0.0108, +0.0244] (8 matches / 168 bets; too few matches) | +0.0124 [+0.0053, +0.0249] (8 matches / 168 bets; too few matches) | +104012 [-197863, +320387] (7 matches / 157 bets; too few matches) |
| w05_e06 | pre-match | 23 | +0.0011 [-0.0136, +0.0265] (4 matches / 21 bets; too few matches) | +0.0011 [-0.0192, +0.0323] (4 matches / 21 bets; too few matches) | -94100 [-150000, +0] (3 matches / 12 bets; too few matches) |
| w05_e06 | in-play | 158 | +0.0184 [+0.0070, +0.0324] (8 matches / 150 bets; too few matches) | +0.0014 [-0.0065, +0.0174] (8 matches / 150 bets; too few matches) | +92866 [-206338, +316454] (7 matches / 140 bets; too few matches) |
| w07_e03 | pre-match | 49 | -0.0127 [-0.0364, +0.0010] (5 matches / 47 bets; too few matches) | -0.0057 [-0.0308, +0.0076] (5 matches / 47 bets; too few matches) | -81938 [-170996, +7120] (4 matches / 32 bets; too few matches) |
| w07_e03 | in-play | 141 | +0.0172 [+0.0049, +0.0255] (8 matches / 138 bets; too few matches) | +0.0104 [-0.0028, +0.0273] (8 matches / 138 bets; too few matches) | +131736 [-195648, +376509] (7 matches / 131 bets; too few matches) |
| w07_e06 | pre-match | 19 | -0.0063 [-0.0335, +0.0137] (4 matches / 17 bets; too few matches) | -0.0078 [-0.0341, +0.0195] (4 matches / 17 bets; too few matches) | -100000 [-150000, +0] (3 matches / 7 bets; too few matches) |
| w07_e06 | in-play | 146 | +0.0170 [+0.0038, +0.0255] (8 matches / 143 bets; too few matches) | +0.0027 [-0.0167, +0.0195] (8 matches / 143 bets; too few matches) | +177653 [-163084, +453153] (7 matches / 137 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | in-play | 271 | +0.0320 [+0.0198, +0.0472] (12 matches / 267 bets) | +0.0283 [+0.0117, +0.0512] (12 matches / 267 bets) | +94390 [-226218, +408608] (11 matches / 251 bets) |
| mkt_dyn_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn_e06 | in-play | 267 | +0.0245 [+0.0128, +0.0345] (10 matches / 265 bets) | +0.0207 [+0.0087, +0.0335] (10 matches / 265 bets) | +175296 [-140479, +509768] (9 matches / 252 bets; too few matches) |
| tail | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| tail | in-play | 5 | +0.0199 [-0.0053, +0.0522] (3 matches / 5 bets; too few matches) | +0.0199 [-0.0053, +0.0522] (3 matches / 5 bets; too few matches) | -6000 [-12000, +0] (3 matches / 5 bets; too few matches) |
| placebo_random (placebo) | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| placebo_random (placebo) | in-play | 179 | -0.0004 [-0.0092, +0.0087] (12 matches / 178 bets) | +0.0030 [-0.0130, +0.0157] (12 matches / 178 bets) | -365733 [-520811, -207668] (11 matches / 171 bets) |
| placebo_favourite (placebo) | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| placebo_favourite (placebo) | in-play | 101 | +0.0102 [-0.0064, +0.0343] (12 matches / 99 bets) | +0.0076 [-0.0096, +0.0343] (12 matches / 99 bets) | +338889 [+145854, +554051] (11 matches / 90 bets) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0070 [-0.0034, +0.0173] | n/a [-1720.94, +2138.00] |
| w03_e06 | +0.0074 [-0.0034, +0.0188] | n/a [-1959.80, +2271.21] |
| w05_e03 | +0.0136 [+0.0051, +0.0223] | n/a [-2099.18, +2403.94] |
| w05_e06 | +0.0163 [+0.0057, +0.0283] | n/a [-3146.32, +3017.49] |
| w07_e03 | +0.0096 [+0.0001, +0.0200] | n/a [-2230.65, +2652.63] |
| w07_e06 | +0.0145 [+0.0036, +0.0247] | n/a [-2635.65, +3863.47] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |
| mkt_dyn | +0.0320 [+0.0243, +0.0402] | n/a [-1948.85, +2894.99] |
| mkt_dyn_e06 | +0.0245 [+0.0157, +0.0327] | n/a [-2383.54, +3899.59] |
| tail | +0.0199 [-0.0037, +0.0489] | n/a [-2000.00, -400.00] |
| placebo_random (placebo) | -0.0004 [-0.0093, +0.0085] | n/a [-4329.26, -208.65] |
| placebo_favourite (placebo) | +0.0102 [-0.0063, +0.0270] | n/a [+438.68, +6970.23] |

## Intents by side

- w03_e03: BACK_away 106, BACK_home 80, LAY_away 79, LAY_home 64
- w03_e06: BACK_away 68, BACK_home 67, LAY_away 50, LAY_home 61
- w05_e03: BACK_away 98, BACK_home 87, LAY_away 56, LAY_home 63
- w05_e06: BACK_away 75, BACK_home 64, LAY_away 59, LAY_home 65
- w07_e03: BACK_away 71, BACK_home 82, LAY_away 53, LAY_home 62
- w07_e06: BACK_away 64, BACK_home 57, LAY_away 46, LAY_home 49
- market_only: none
- mkt_dyn: BACK_away 82, BACK_home 62, LAY_away 120, LAY_home 126
- mkt_dyn_e06: BACK_away 89, BACK_home 73, LAY_away 102, LAY_home 114
- tail: BACK_away 2, BACK_home 3
- placebo_random (placebo): BACK_away 82, BACK_home 80, LAY_away 67, LAY_home 79
- placebo_favourite (placebo): BACK_away 90, BACK_home 84

## Skips (counted per cycle, summed over the run)

- Registry: {'bookmaker_only': 14987, 'settled': 170, 'simulated': 21721, 'too_early': 18373, 'twin': 1912}
- Matches: {'no_market': 3032}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 206, 'flip_cooldown': 776, 'inplay_without_state': 37403, 'no_band': 304, 'spread_too_wide': 981, 'trade_rate': 5473}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst | mkt_dyn fills / worst | mkt_dyn_e06 fills / worst | tail fills / worst | placebo_random (placebo) fills / worst | placebo_favourite (placebo) fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | - | - | - | - | - | - | - | 2 / 50,000 | - | - | 1 / 24,250 | 4 / 35,950 |
| lb_36264203 | Wellington Firebirds v Northern Districts | Ford Trophy | - | - | - | - | - | - | - | 30 / 0 | 28 / 0 | - | 14 / 40,312 | 17 / 92,282 |
| lb_36266935 | Central Stags v Auckland Aces | Ford Trophy | 33 / 379 | 30 / 0 | 30 / 0 | 29 / 0 | 24 / 97,881 | 22 / 98,286 | - | 32 / 0 | 32 / 0 | - | 38 / 32,414 | 11 / 100,000 |
| lb_36266944 | Hobart Hurricanes W v Melbourne Renegades W | WBBL | 29 / 60,270 | 29 / 58,700 | 30 / 37,550 | 22 / 63,000 | 14 / 90,900 | 12 / 89,500 | - | 30 / 0 | 33 / 0 | 2 / 4,000 | 16 / 0 | 6 / 100,000 |
| lb_36267052 | Adelaide Strikers W v Brisbane Heat W | WBBL | 30 / 0 | 20 / 52,080 | 32 / 11,800 | 27 / 4,800 | 25 / 45,000 | 19 / 45,000 | - | 20 / 43,340 | 15 / 6,163 | - | 8 / 0 | 11 / 27,455 |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 24 / 48,200 | 21 / 99,239 | 29 / 0 | 27 / 30,261 | 33 / 0 | 30 / 0 | - | 30 / 0 | 29 / 0 | - | 13 / 67,677 | 12 / 100,000 |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 33 / 0 | 28 / 32,690 | 32 / 0 | 31 / 0 | 34 / 0 | 33 / 0 | - | 29 / 0 | 32 / 0 | 2 / 4,000 | 20 / 42,492 | 9 / 100,000 |
| lb_36269512 | West Indies A v England Lions | T20 Matches | 23 / 0 | 15 / 37,452 | 20 / 21,963 | 18 / 79,331 | 18 / 100,000 | 19 / 100,000 | - | 29 / 0 | 30 / 0 | - | 17 / 57,146 | 14 / 65,951 |
| lb_36271322 | New Zealand v India | One Day Internationals | 28 / 97,760 | 19 / 100,000 | 20 / 100,000 | 17 / 100,000 | 26 / 99,650 | 15 / 100,000 | - | 33 / 38,947 | 33 / 56,298 | - | 21 / 26,126 | 8 / 100,000 |
| lb_36271347 | Sydney Thunder W v Perth Scorchers W | WBBL | 4 / 37,100 | - | 2 / 50,000 | 2 / 50,000 | 2 / 50,000 | 2 / 50,000 | - | - | - | - | - | - |
| lb_36273920 | West Indies A v England Lions | T20 Matches | 10 / 12,278 | 9 / 50,658 | 11 / 26,660 | 8 / 37,748 | 14 / 33,003 | 13 / 78,425 | - | 28 / 24,960 | 32 / 17,179 | 1 / 2,000 | 20 / 0 | 5 / 100,000 |
| lb_36275005 | Lumbini Lions v Janakpur Bolts | Nepal Premier League | 0 / 0 | - | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | - | - | - | - | - | - |
| lb_75747934 | Dubai v Sharjah | Emirates D20 | - | - | - | - | - | - | - | 3 / 40,594 | - | - | 2 / 13,680 | 2 / 14,000 |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | - | - | - | - | - | - | - | 5 / 62,080 | 3 / 58,000 | - | 9 / 71,910 | 2 / 29,000 |

