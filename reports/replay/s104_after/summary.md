# PHOENIX paper replay

Window 2026-11-01T16:00:41.205386+00:00 to 2026-11-04T12:00:00+00:00; 30 matches considered; 11255 cycles run, 5035 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only', 'mkt_dyn', 'mkt_dyn_e06', 'placebo_random', 'placebo_favourite'], 'reference': 'w03_e03', 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)', 'market_dynamics': 'current: 20261104 (s97-1)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 248 | 165 | 135 | 30 | +0.0049 [-0.0078, +0.0190] (6 matches / 163 bets; too few matches) | -6839 [-273646, +223203] (6 matches / 165 bets; too few matches) | 100,000 | 27.50 | 3 (1.8%) |
| w03_e06 | 202 | 143 | 114 | 29 | +0.0032 [-0.0114, +0.0200] (6 matches / 142 bets; too few matches) | -1211 [-265931, +230957] (6 matches / 143 bets; too few matches) | 100,000 | 23.83 | 3 (2.1%) |
| w05_e03 | 234 | 159 | 129 | 30 | +0.0082 [+0.0042, +0.0134] (6 matches / 158 bets; too few matches) | -81372 [-303445, +109933] (6 matches / 159 bets; too few matches) | 100,000 | 26.50 | 4 (2.5%) |
| w05_e06 | 207 | 140 | 119 | 21 | +0.0072 [-0.0050, +0.0178] (6 matches / 137 bets; too few matches) | -30896 [-283826, +221764] (6 matches / 140 bets; too few matches) | 100,000 | 23.33 | 8 (5.7%) |
| w07_e03 | 200 | 140 | 127 | 13 | +0.0041 [-0.0169, +0.0164] (6 matches / 139 bets; too few matches) | -93286 [-312061, +104348] (6 matches / 140 bets; too few matches) | 99,650 | 23.33 | 6 (4.3%) |
| w07_e06 | 170 | 130 | 116 | 14 | +0.0128 [-0.0047, +0.0235] (6 matches / 129 bets; too few matches) | -42123 [-266737, +128888] (6 matches / 130 bets; too few matches) | 100,000 | 21.67 | 4 (3.1%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |
| mkt_dyn | 322 | 223 | 195 | 28 | +0.0321 [+0.0191, +0.0473] (13 matches / 222 bets) | +153256 [-119172, +421105] (13 matches / 223 bets) | 57,850 | 17.15 | 7 (3.1%) |
| mkt_dyn_e06 | 305 | 213 | 183 | 30 | +0.0324 [+0.0154, +0.0476] (11 matches / 213 bets) | +187051 [-131428, +502188] (11 matches / 213 bets) | 77,135 | 19.36 | 8 (3.8%) |
| placebo_random (placebo) | 195 | 113 | 98 | 15 | -0.0052 [-0.0224, +0.0091] (6 matches / 112 bets; too few matches) | -104475 [-223560, +6637] (6 matches / 113 bets; too few matches) | 71,063 | 18.83 | 4 (3.5%) |
| placebo_favourite (placebo) | 94 | 55 | 39 | 16 | +0.0190 [+0.0017, +0.0346] (6 matches / 55 bets; too few matches) | +117474 [-53981, +294059] (6 matches / 55 bets; too few matches) | 100,000 | 9.17 | 2 (3.6%) |

## Placebo controls (S85): excess CLV over placebo_random, match-clustered

The placebos copy the decision times and stakes of the reference variant (w03_e03). They are never primary, never ranked and never signals. The excess of a variant is, per match, its mean CLV minus placebo_random's mean CLV on that match; the interval resamples matches. A CI from fewer than 10 matches is labelled 'too few matches'.

No variant's CLV beats placebo_random: no excess-CLV confidence interval lies above zero on at least 10 matches.

| variant | excess CLV over placebo_random [95% CI] (matches / bets) | placebo_random CLV bets on those matches | above zero on 10+ matches |
|---|---|---:|---|
| w03_e03 | +0.0153 [-0.0066, +0.0365] (6 matches / 163 bets; too few matches) | 112 | no |
| w03_e06 | +0.0147 [-0.0112, +0.0405] (6 matches / 142 bets; too few matches) | 112 | no |
| w05_e03 | +0.0169 [+0.0036, +0.0289] (6 matches / 158 bets; too few matches) | 112 | no |
| w05_e06 | +0.0157 [+0.0009, +0.0342] (6 matches / 137 bets; too few matches) | 112 | no |
| w07_e03 | +0.0061 [-0.0164, +0.0254] (6 matches / 139 bets; too few matches) | 112 | no |
| w07_e06 | +0.0168 [+0.0010, +0.0328] (6 matches / 129 bets; too few matches) | 112 | no |
| market_only | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | no |
| mkt_dyn | +0.0426 [+0.0167, +0.0716] (6 matches / 175 bets; too few matches) | 112 | no |
| mkt_dyn_e06 | +0.0421 [+0.0185, +0.0675] (6 matches / 178 bets; too few matches) | 112 | no |


## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 248 | 165 | 83 | 66.5% | 71.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0830 / -2.9% |
| w03_e06 | 202 | 143 | 59 | 70.8% | 74.3% | 0.0% (0) | 0 | 0.0% (0) | -0.1186 / -2.1% |
| w05_e03 | 234 | 159 | 75 | 67.9% | 64.7% | 0.0% (0) | 0 | 0.0% (0) | -0.0061 / -1.6% |
| w05_e06 | 207 | 140 | 67 | 67.6% | 61.3% | 0.0% (0) | 0 | 0.0% (0) | +0.0256 / -1.1% |
| w07_e03 | 200 | 140 | 60 | 70.0% | 57.7% | 0.0% (0) | 0 | 0.0% (0) | +0.1127 / 1.5% |
| w07_e06 | 170 | 130 | 40 | 76.5% | 67.8% | 0.0% (0) | 0 | 0.0% (0) | +0.0482 / -0.1% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |
| mkt_dyn | 322 | 223 | 99 | 69.3% | 71.4% | 0.0% (0) | 0 | 0.0% (0) | -0.0195 / -2.3% |
| mkt_dyn_e06 | 305 | 213 | 92 | 69.8% | 70.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0561 / -1.6% |
| placebo_random (placebo) | 195 | 113 | 82 | 57.9% | 58.2% | 0.0% (0) | 0 | 0.0% (0) | -0.1571 / -3.5% |
| placebo_favourite (placebo) | 94 | 55 | 39 | 58.5% | 57.6% | 0.0% (0) | 0 | 0.0% (0) | -0.0504 / -3.0% |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 11 | +0.0001 [-0.0268, +0.0507] (3 matches / 11 bets; too few matches) | +0.0004 [-0.0249, +0.0507] (3 matches / 11 bets; too few matches) | -52210 [-81630, +0] (3 matches / 11 bets; too few matches) |
| w03_e03 | in-play | 154 | +0.0052 [-0.0072, +0.0194] (6 matches / 152 bets; too few matches) | -0.0065 [-0.0166, +0.0137] (6 matches / 152 bets; too few matches) | +45371 [-210411, +286079] (6 matches / 154 bets; too few matches) |
| w03_e06 | pre-match | 4 | -0.0117 [-0.0231, -0.0003] (2 matches / 4 bets; too few matches) | -0.0121 [-0.0231, +0.0038] (2 matches / 4 bets; too few matches) | -35000 [-70000, +0] (2 matches / 4 bets; too few matches) |
| w03_e06 | in-play | 139 | +0.0037 [-0.0112, +0.0203] (6 matches / 138 bets; too few matches) | -0.0117 [-0.0200, +0.0069] (6 matches / 138 bets; too few matches) | +33789 [-227327, +248590] (6 matches / 139 bets; too few matches) |
| w05_e03 | pre-match | 15 | -0.0058 [-0.0372, +0.0138] (3 matches / 15 bets; too few matches) | -0.0038 [-0.0408, +0.0191] (3 matches / 15 bets; too few matches) | -84340 [-150000, +0] (3 matches / 15 bets; too few matches) |
| w05_e03 | in-play | 144 | +0.0096 [+0.0052, +0.0145] (6 matches / 143 bets; too few matches) | +0.0020 [-0.0113, +0.0181] (6 matches / 143 bets; too few matches) | +2968 [-245264, +188179] (6 matches / 144 bets; too few matches) |
| w05_e06 | pre-match | 12 | +0.0034 [-0.0268, +0.0487] (3 matches / 12 bets; too few matches) | +0.0028 [-0.0275, +0.0487] (3 matches / 12 bets; too few matches) | -94100 [-150000, +0] (3 matches / 12 bets; too few matches) |
| w05_e06 | in-play | 128 | +0.0076 [-0.0046, +0.0190] (6 matches / 125 bets; too few matches) | -0.0020 [-0.0139, +0.0163] (6 matches / 125 bets; too few matches) | +63204 [-218530, +288414] (6 matches / 128 bets; too few matches) |
| w07_e03 | pre-match | 32 | -0.0172 [-0.0472, +0.0033] (4 matches / 32 bets; too few matches) | -0.0081 [-0.0444, +0.0097] (4 matches / 32 bets; too few matches) | -81848 [-170996, +7300] (4 matches / 32 bets; too few matches) |
| w07_e03 | in-play | 108 | +0.0105 [-0.0083, +0.0217] (6 matches / 107 bets; too few matches) | +0.0005 [-0.0159, +0.0252] (6 matches / 107 bets; too few matches) | -11438 [-259663, +161321] (6 matches / 108 bets; too few matches) |
| w07_e06 | pre-match | 7 | -0.0100 [-0.0372, +0.0487] (3 matches / 7 bets; too few matches) | -0.0064 [-0.0405, +0.0487] (3 matches / 7 bets; too few matches) | -100000 [-150000, +0] (3 matches / 7 bets; too few matches) |
| w07_e06 | in-play | 123 | +0.0141 [-0.0022, +0.0235] (6 matches / 122 bets; too few matches) | -0.0059 [-0.0321, +0.0124] (6 matches / 122 bets; too few matches) | +57877 [-206587, +254927] (6 matches / 123 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | in-play | 223 | +0.0321 [+0.0191, +0.0473] (13 matches / 222 bets) | +0.0344 [+0.0159, +0.0581] (13 matches / 222 bets) | +153256 [-119172, +421105] (13 matches / 223 bets) |
| mkt_dyn_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn_e06 | in-play | 213 | +0.0324 [+0.0154, +0.0476] (11 matches / 213 bets) | +0.0323 [+0.0162, +0.0493] (11 matches / 213 bets) | +187051 [-131428, +502188] (11 matches / 213 bets) |
| placebo_random (placebo) | pre-match | 9 | +0.0104 [-0.0027, +0.0500] (3 matches / 9 bets; too few matches) | +0.0188 [-0.0025, +0.0500] (3 matches / 9 bets; too few matches) | +547 [-57000, +58641] (3 matches / 9 bets; too few matches) |
| placebo_random (placebo) | in-play | 104 | -0.0066 [-0.0244, +0.0089] (6 matches / 103 bets; too few matches) | -0.0114 [-0.0336, +0.0048] (6 matches / 103 bets; too few matches) | -105022 [-235533, +19708] (6 matches / 104 bets; too few matches) |
| placebo_favourite (placebo) | pre-match | 5 | +0.0120 [-0.0382, +0.0253] (3 matches / 5 bets; too few matches) | +0.0220 [-0.0382, +0.0253] (3 matches / 5 bets; too few matches) | +31620 [+0, +85500] (3 matches / 5 bets; too few matches) |
| placebo_favourite (placebo) | in-play | 50 | +0.0197 [+0.0007, +0.0363] (6 matches / 50 bets; too few matches) | +0.0116 [-0.0023, +0.0378] (6 matches / 50 bets; too few matches) | +85854 [-63998, +255542] (6 matches / 50 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0049 [-0.0070, +0.0168] | n/a [-2306.06, +2099.33] |
| w03_e06 | +0.0032 [-0.0076, +0.0136] | n/a [-2044.72, +2142.72] |
| w05_e03 | +0.0082 [-0.0027, +0.0180] | n/a [-2745.78, +1774.30] |
| w05_e06 | +0.0072 [-0.0041, +0.0186] | n/a [-3329.58, +2680.13] |
| w07_e03 | +0.0041 [-0.0083, +0.0165] | n/a [-3071.16, +1664.54] |
| w07_e06 | +0.0128 [-0.0004, +0.0259] | n/a [-3664.97, +3161.48] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |
| mkt_dyn | +0.0321 [+0.0230, +0.0407] | n/a [-2082.50, +3446.56] |
| mkt_dyn_e06 | +0.0324 [+0.0231, +0.0420] | n/a [-2374.44, +4292.09] |
| placebo_random (placebo) | -0.0052 [-0.0177, +0.0068] | n/a [-2988.28, +1003.89] |
| placebo_favourite (placebo) | +0.0190 [-0.0007, +0.0386] | n/a [+142.37, +3979.91] |

## Intents by side

- w03_e03: BACK_away 76, BACK_home 61, LAY_away 60, LAY_home 51
- w03_e06: BACK_away 58, BACK_home 51, LAY_away 49, LAY_home 44
- w05_e03: BACK_away 76, BACK_home 64, LAY_away 39, LAY_home 55
- w05_e06: BACK_away 57, BACK_home 48, LAY_away 48, LAY_home 54
- w07_e03: BACK_away 48, BACK_home 60, LAY_away 39, LAY_home 53
- w07_e06: BACK_away 50, BACK_home 44, LAY_away 36, LAY_home 40
- market_only: none
- mkt_dyn: BACK_away 50, BACK_home 52, LAY_away 106, LAY_home 114
- mkt_dyn_e06: BACK_away 47, BACK_home 59, LAY_away 87, LAY_home 112
- placebo_random (placebo): BACK_away 50, BACK_home 51, LAY_away 43, LAY_home 51
- placebo_favourite (placebo): BACK_away 78, BACK_home 16

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 147, 'simulated': 13827, 'too_early': 9638, 'twin': 1868}
- Matches: {'no_market': 870}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 196, 'flip_cooldown': 781, 'inplay_without_state': 85327, 'no_band': 839, 'spread_too_wide': 1593, 'stale_book': 331, 'trade_rate': 4203}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst | mkt_dyn fills / worst | mkt_dyn_e06 fills / worst | placebo_random (placebo) fills / worst | placebo_favourite (placebo) fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | - | - | - | - | - | - | - | 2 / 320 | - | - | - |
| lb_36264203 | Wellington Firebirds v Northern Districts | Ford Trophy | - | - | - | - | - | - | - | 29 / 0 | 28 / 0 | - | - |
| lb_36266935 | Central Stags v Auckland Aces | Ford Trophy | 30 / 48,849 | 30 / 0 | 30 / 0 | 30 / 0 | 24 / 97,881 | 22 / 98,286 | - | 30 / 0 | 30 / 0 | 25 / 7,616 | 10 / 99,089 |
| lb_36266944 | Hobart Hurricanes W v Melbourne Renegades W | WBBL | 30 / 59,200 | 29 / 58,700 | 30 / 37,550 | 21 / 64,850 | 13 / 90,200 | 14 / 88,800 | - | 28 / 0 | 30 / 0 | 15 / 16,186 | 16 / 100,000 |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 24 / 43,275 | 20 / 93,881 | 29 / 0 | 26 / 25,871 | 30 / 7,664 | 30 / 6,550 | - | 30 / 0 | 30 / 0 | 20 / 15,430 | 2 / 814 |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 30 / 24,873 | 27 / 32,946 | 30 / 47,302 | 30 / 6,141 | 30 / 48,695 | 30 / 0 | - | 29 / 0 | 28 / 0 | 23 / 0 | 4 / 100,000 |
| lb_36269512 | West Indies A v England Lions | T20 Matches | 24 / 0 | 16 / 49,232 | 20 / 32,074 | 17 / 51,688 | 17 / 99,509 | 19 / 100,000 | - | 28 / 0 | 30 / 0 | 13 / 71,063 | 15 / 62,520 |
| lb_36271322 | New Zealand v India | One Day Internationals | 27 / 100,000 | 21 / 100,000 | 20 / 100,000 | 16 / 100,000 | 26 / 99,650 | 15 / 100,000 | - | 30 / 43,564 | 30 / 77,135 | 17 / 58,436 | 8 / 100,000 |
| lb_75330686 | Otago Volts v Canterbury Kings | Ford Trophy | - | - | - | - | - | - | - | 0 / 0 | - | - | - |
| lb_75363236 | Karnataka v Chhattisgarh | Senior Women's T20 Trophy Elite | - | - | - | - | - | - | - | 0 / 0 | 0 / 0 | - | - |
| lb_75747938 | Dubai v Ajman | Emirates D20 | - | - | - | - | - | - | - | 1 / 16,142 | 1 / 16,415 | - | - |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | - | - | - | - | - | - | - | 4 / 57,850 | 1 / 22,833 | - | - |
| lb_76073798 | Incredibles v Warriors | T20 PCB Super-3, Women | - | - | - | - | - | - | - | 5 / 16,807 | - | - | - |
| lb_76227860 | Cdb A v Dpgc | T20 MCA Super Premier League | - | - | - | - | - | - | - | 1 / 23,369 | 1 / 23,833 | - | - |
| lb_76227866 | Hnb A v Hayleys Group A | T20 MCA Super Premier League | - | - | - | - | - | - | - | 6 / 0 | 4 / 0 | - | - |

