# PHOENIX paper replay

Window 2026-11-01T16:00:41.205386+00:00 to 2026-11-04T12:00:00+00:00; 30 matches considered; 11255 cycles run, 5035 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only', 'mkt_dyn', 'mkt_dyn_e06', 'placebo_random', 'placebo_favourite'], 'reference': 'w03_e03', 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)', 'market_dynamics': 'current: 20261104 (s97-1)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 257 | 171 | 141 | 30 | +0.0050 [-0.0080, +0.0193] (6 matches / 169 bets; too few matches) | +11136 [-243043, +223902] (6 matches / 171 bets; too few matches) | 100,000 | 28.50 | 3 (1.8%) |
| w03_e06 | 202 | 143 | 114 | 29 | +0.0032 [-0.0114, +0.0200] (6 matches / 142 bets; too few matches) | -1211 [-265931, +230957] (6 matches / 143 bets; too few matches) | 100,000 | 23.83 | 3 (2.1%) |
| w05_e03 | 241 | 163 | 133 | 30 | +0.0084 [+0.0055, +0.0121] (6 matches / 162 bets; too few matches) | -19044 [-250785, +129025] (6 matches / 163 bets; too few matches) | 100,000 | 27.17 | 5 (3.1%) |
| w05_e06 | 208 | 141 | 120 | 21 | +0.0076 [-0.0049, +0.0178] (6 matches / 138 bets; too few matches) | -15896 [-277613, +230737] (6 matches / 141 bets; too few matches) | 100,000 | 23.50 | 8 (5.7%) |
| w07_e03 | 211 | 147 | 134 | 13 | +0.0059 [-0.0145, +0.0173] (6 matches / 146 bets; too few matches) | +21560 [-232180, +198485] (6 matches / 147 bets; too few matches) | 99,650 | 24.50 | 7 (4.8%) |
| w07_e06 | 182 | 135 | 121 | 14 | +0.0130 [-0.0048, +0.0226] (6 matches / 134 bets; too few matches) | +44609 [-220373, +247191] (6 matches / 135 bets; too few matches) | 100,000 | 22.50 | 5 (3.7%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |
| mkt_dyn | 330 | 228 | 200 | 28 | +0.0323 [+0.0197, +0.0474] (13 matches / 227 bets) | +170981 [-89052, +426003] (13 matches / 228 bets) | 57,850 | 17.54 | 8 (3.5%) |
| mkt_dyn_e06 | 319 | 222 | 190 | 32 | +0.0321 [+0.0156, +0.0482] (11 matches / 222 bets) | +220021 [-63299, +511072] (11 matches / 222 bets) | 45,685 | 20.18 | 11 (5.0%) |
| placebo_random (placebo) | 200 | 116 | 101 | 15 | -0.0051 [-0.0224, +0.0089] (6 matches / 115 bets; too few matches) | -130155 [-241134, -25462] (6 matches / 116 bets; too few matches) | 71,063 | 19.33 | 4 (3.4%) |
| placebo_favourite (placebo) | 94 | 55 | 39 | 16 | +0.0190 [+0.0017, +0.0346] (6 matches / 55 bets; too few matches) | +117474 [-53981, +294059] (6 matches / 55 bets; too few matches) | 100,000 | 9.17 | 2 (3.6%) |

## Placebo controls (S85): excess CLV over placebo_random, match-clustered

The placebos copy the decision times and stakes of the reference variant (w03_e03). They are never primary, never ranked and never signals. The excess of a variant is, per match, its mean CLV minus placebo_random's mean CLV on that match; the interval resamples matches. A CI from fewer than 10 matches is labelled 'too few matches'.

No variant's CLV beats placebo_random: no excess-CLV confidence interval lies above zero on at least 10 matches.

| variant | excess CLV over placebo_random [95% CI] (matches / bets) | placebo_random CLV bets on those matches | above zero on 10+ matches |
|---|---|---:|---|
| w03_e03 | +0.0158 [-0.0063, +0.0371] (6 matches / 169 bets; too few matches) | 115 | no |
| w03_e06 | +0.0151 [-0.0108, +0.0409] (6 matches / 142 bets; too few matches) | 115 | no |
| w05_e03 | +0.0174 [+0.0055, +0.0289] (6 matches / 162 bets; too few matches) | 115 | no |
| w05_e06 | +0.0163 [+0.0017, +0.0345] (6 matches / 138 bets; too few matches) | 115 | no |
| w07_e03 | +0.0076 [-0.0155, +0.0270] (6 matches / 146 bets; too few matches) | 115 | no |
| w07_e06 | +0.0171 [+0.0027, +0.0318] (6 matches / 134 bets; too few matches) | 115 | no |
| market_only | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | no |
| mkt_dyn | +0.0436 [+0.0187, +0.0720] (6 matches / 180 bets; too few matches) | 115 | no |
| mkt_dyn_e06 | +0.0429 [+0.0198, +0.0678] (6 matches / 187 bets; too few matches) | 115 | no |


## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 257 | 171 | 86 | 66.5% | 71.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0812 / -2.9% |
| w03_e06 | 202 | 143 | 59 | 70.8% | 74.3% | 0.0% (0) | 0 | 0.0% (0) | -0.1186 / -2.1% |
| w05_e03 | 241 | 163 | 78 | 67.6% | 60.2% | 0.0% (0) | 0 | 0.0% (0) | -0.0040 / -1.6% |
| w05_e06 | 208 | 141 | 67 | 67.8% | 61.4% | 0.0% (0) | 0 | 0.0% (0) | +0.0282 / -1.1% |
| w07_e03 | 211 | 147 | 64 | 69.7% | 60.5% | 0.0% (0) | 0 | 0.0% (0) | +0.1078 / 1.4% |
| w07_e06 | 182 | 135 | 47 | 74.2% | 67.0% | 0.0% (0) | 0 | 0.0% (0) | +0.0496 / -0.2% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |
| mkt_dyn | 330 | 228 | 102 | 69.1% | 71.6% | 0.0% (0) | 0 | 0.0% (0) | -0.0205 / -2.2% |
| mkt_dyn_e06 | 319 | 222 | 97 | 69.6% | 70.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0297 / -1.2% |
| placebo_random (placebo) | 200 | 116 | 84 | 58.0% | 58.8% | 0.0% (0) | 0 | 0.0% (0) | -0.1530 / -3.4% |
| placebo_favourite (placebo) | 94 | 55 | 39 | 58.5% | 57.6% | 0.0% (0) | 0 | 0.0% (0) | -0.0504 / -3.0% |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 11 | +0.0001 [-0.0268, +0.0507] (3 matches / 11 bets; too few matches) | +0.0004 [-0.0249, +0.0507] (3 matches / 11 bets; too few matches) | -52210 [-81630, +0] (3 matches / 11 bets; too few matches) |
| w03_e03 | in-play | 160 | +0.0054 [-0.0074, +0.0194] (6 matches / 158 bets; too few matches) | -0.0049 [-0.0126, +0.0121] (6 matches / 158 bets; too few matches) | +63346 [-210411, +318068] (6 matches / 160 bets; too few matches) |
| w03_e06 | pre-match | 4 | -0.0117 [-0.0231, -0.0003] (2 matches / 4 bets; too few matches) | -0.0121 [-0.0231, +0.0038] (2 matches / 4 bets; too few matches) | -35000 [-70000, +0] (2 matches / 4 bets; too few matches) |
| w03_e06 | in-play | 139 | +0.0037 [-0.0112, +0.0203] (6 matches / 138 bets; too few matches) | -0.0117 [-0.0200, +0.0069] (6 matches / 138 bets; too few matches) | +33789 [-227327, +248590] (6 matches / 139 bets; too few matches) |
| w05_e03 | pre-match | 15 | -0.0058 [-0.0372, +0.0138] (3 matches / 15 bets; too few matches) | -0.0038 [-0.0408, +0.0191] (3 matches / 15 bets; too few matches) | -84340 [-150000, +0] (3 matches / 15 bets; too few matches) |
| w05_e03 | in-play | 148 | +0.0099 [+0.0059, +0.0135] (6 matches / 147 bets; too few matches) | +0.0081 [-0.0034, +0.0180] (6 matches / 147 bets; too few matches) | +65296 [-207335, +286825] (6 matches / 148 bets; too few matches) |
| w05_e06 | pre-match | 12 | +0.0034 [-0.0268, +0.0487] (3 matches / 12 bets; too few matches) | +0.0028 [-0.0275, +0.0487] (3 matches / 12 bets; too few matches) | -94100 [-150000, +0] (3 matches / 12 bets; too few matches) |
| w05_e06 | in-play | 129 | +0.0080 [-0.0046, +0.0196] (6 matches / 126 bets; too few matches) | -0.0019 [-0.0136, +0.0163] (6 matches / 126 bets; too few matches) | +78204 [-203381, +317244] (6 matches / 129 bets; too few matches) |
| w07_e03 | pre-match | 32 | -0.0172 [-0.0472, +0.0033] (4 matches / 32 bets; too few matches) | -0.0081 [-0.0444, +0.0097] (4 matches / 32 bets; too few matches) | -81848 [-170996, +7300] (4 matches / 32 bets; too few matches) |
| w07_e03 | in-play | 115 | +0.0124 [-0.0066, +0.0231] (6 matches / 114 bets; too few matches) | +0.0084 [-0.0073, +0.0256] (6 matches / 114 bets; too few matches) | +103408 [-188843, +357482] (6 matches / 115 bets; too few matches) |
| w07_e06 | pre-match | 7 | -0.0100 [-0.0372, +0.0487] (3 matches / 7 bets; too few matches) | -0.0064 [-0.0405, +0.0487] (3 matches / 7 bets; too few matches) | -100000 [-150000, +0] (3 matches / 7 bets; too few matches) |
| w07_e06 | in-play | 128 | +0.0143 [-0.0025, +0.0230] (6 matches / 127 bets; too few matches) | -0.0017 [-0.0311, +0.0129] (6 matches / 127 bets; too few matches) | +144609 [-173261, +437069] (6 matches / 128 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | in-play | 228 | +0.0323 [+0.0197, +0.0474] (13 matches / 227 bets) | +0.0337 [+0.0152, +0.0574] (13 matches / 227 bets) | +170981 [-89052, +426003] (13 matches / 228 bets) |
| mkt_dyn_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn_e06 | in-play | 222 | +0.0321 [+0.0156, +0.0482] (11 matches / 222 bets) | +0.0311 [+0.0145, +0.0491] (11 matches / 222 bets) | +220021 [-63299, +511072] (11 matches / 222 bets) |
| placebo_random (placebo) | pre-match | 9 | +0.0104 [-0.0027, +0.0500] (3 matches / 9 bets; too few matches) | +0.0188 [-0.0025, +0.0500] (3 matches / 9 bets; too few matches) | +547 [-57000, +58641] (3 matches / 9 bets; too few matches) |
| placebo_random (placebo) | in-play | 107 | -0.0065 [-0.0244, +0.0088] (6 matches / 106 bets; too few matches) | -0.0113 [-0.0336, +0.0039] (6 matches / 106 bets; too few matches) | -130702 [-266435, +6198] (6 matches / 107 bets; too few matches) |
| placebo_favourite (placebo) | pre-match | 5 | +0.0120 [-0.0382, +0.0253] (3 matches / 5 bets; too few matches) | +0.0220 [-0.0382, +0.0253] (3 matches / 5 bets; too few matches) | +31620 [+0, +85500] (3 matches / 5 bets; too few matches) |
| placebo_favourite (placebo) | in-play | 50 | +0.0197 [+0.0007, +0.0363] (6 matches / 50 bets; too few matches) | +0.0116 [-0.0023, +0.0378] (6 matches / 50 bets; too few matches) | +85854 [-63998, +255542] (6 matches / 50 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0050 [-0.0064, +0.0164] | n/a [-2093.72, +2175.71] |
| w03_e06 | +0.0032 [-0.0076, +0.0136] | n/a [-2044.72, +2142.72] |
| w05_e03 | +0.0084 [-0.0017, +0.0184] | n/a [-2401.64, +2148.40] |
| w05_e06 | +0.0076 [-0.0041, +0.0190] | n/a [-3076.22, +2846.87] |
| w07_e03 | +0.0059 [-0.0055, +0.0177] | n/a [-2234.89, +2700.05] |
| w07_e06 | +0.0130 [+0.0007, +0.0262] | n/a [-3186.65, +3825.02] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |
| mkt_dyn | +0.0323 [+0.0236, +0.0408] | n/a [-1943.43, +3477.07] |
| mkt_dyn_e06 | +0.0321 [+0.0229, +0.0412] | n/a [-2261.18, +4225.44] |
| placebo_random (placebo) | -0.0051 [-0.0182, +0.0066] | n/a [-3114.52, +894.13] |
| placebo_favourite (placebo) | +0.0190 [-0.0007, +0.0386] | n/a [+142.37, +3979.91] |

## Intents by side

- w03_e03: BACK_away 76, BACK_home 65, LAY_away 65, LAY_home 51
- w03_e06: BACK_away 58, BACK_home 51, LAY_away 49, LAY_home 44
- w05_e03: BACK_away 76, BACK_home 68, LAY_away 42, LAY_home 55
- w05_e06: BACK_away 57, BACK_home 48, LAY_away 49, LAY_home 54
- w07_e03: BACK_away 48, BACK_home 65, LAY_away 45, LAY_home 53
- w07_e06: BACK_away 50, BACK_home 49, LAY_away 41, LAY_home 42
- market_only: none
- mkt_dyn: BACK_away 53, BACK_home 52, LAY_away 107, LAY_home 118
- mkt_dyn_e06: BACK_away 50, BACK_home 59, LAY_away 92, LAY_home 118
- placebo_random (placebo): BACK_away 53, BACK_home 52, LAY_away 43, LAY_home 52
- placebo_favourite (placebo): BACK_away 78, BACK_home 16

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 147, 'simulated': 13827, 'too_early': 9638, 'twin': 1868}
- Matches: {'no_market': 870}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 196, 'flip_cooldown': 781, 'inplay_without_state': 85327, 'no_band': 839, 'spread_too_wide': 1594, 'stale_book': 331, 'trade_rate': 4206}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst | mkt_dyn fills / worst | mkt_dyn_e06 fills / worst | placebo_random (placebo) fills / worst | placebo_favourite (placebo) fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | - | - | - | - | - | - | - | 2 / 320 | - | - | - |
| lb_36264203 | Wellington Firebirds v Northern Districts | Ford Trophy | - | - | - | - | - | - | - | 29 / 0 | 28 / 0 | - | - |
| lb_36266935 | Central Stags v Auckland Aces | Ford Trophy | 33 / 0 | 30 / 0 | 32 / 0 | 30 / 0 | 24 / 97,881 | 22 / 98,286 | - | 32 / 0 | 31 / 0 | 27 / 35,616 | 10 / 99,089 |
| lb_36266944 | Hobart Hurricanes W v Melbourne Renegades W | WBBL | 30 / 59,200 | 29 / 58,700 | 30 / 37,550 | 21 / 64,850 | 13 / 90,200 | 14 / 88,800 | - | 28 / 0 | 32 / 0 | 15 / 16,186 | 16 / 100,000 |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 24 / 45,021 | 20 / 93,881 | 29 / 0 | 26 / 25,871 | 33 / 0 | 32 / 0 | - | 30 / 0 | 30 / 0 | 20 / 13,430 | 2 / 814 |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 33 / 5,073 | 27 / 32,946 | 32 / 0 | 31 / 0 | 34 / 0 | 33 / 0 | - | 29 / 0 | 28 / 0 | 24 / 20,101 | 4 / 100,000 |
| lb_36269512 | West Indies A v England Lions | T20 Matches | 24 / 0 | 16 / 49,232 | 20 / 32,074 | 17 / 51,688 | 17 / 99,509 | 19 / 100,000 | - | 28 / 0 | 31 / 0 | 13 / 71,063 | 15 / 62,520 |
| lb_36271322 | New Zealand v India | One Day Internationals | 27 / 100,000 | 21 / 100,000 | 20 / 100,000 | 16 / 100,000 | 26 / 99,650 | 15 / 100,000 | - | 33 / 28,614 | 35 / 45,685 | 17 / 58,436 | 8 / 100,000 |
| lb_75330686 | Otago Volts v Canterbury Kings | Ford Trophy | - | - | - | - | - | - | - | 0 / 0 | - | - | - |
| lb_75363236 | Karnataka v Chhattisgarh | Senior Women's T20 Trophy Elite | - | - | - | - | - | - | - | 0 / 0 | 0 / 0 | - | - |
| lb_75747938 | Dubai v Ajman | Emirates D20 | - | - | - | - | - | - | - | 1 / 16,142 | 1 / 16,415 | - | - |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | - | - | - | - | - | - | - | 4 / 57,850 | 1 / 22,833 | - | - |
| lb_76073798 | Incredibles v Warriors | T20 PCB Super-3, Women | - | - | - | - | - | - | - | 5 / 16,807 | - | - | - |
| lb_76227860 | Cdb A v Dpgc | T20 MCA Super Premier League | - | - | - | - | - | - | - | 1 / 23,369 | 1 / 23,833 | - | - |
| lb_76227866 | Hnb A v Hayleys Group A | T20 MCA Super Premier League | - | - | - | - | - | - | - | 6 / 0 | 4 / 0 | - | - |

