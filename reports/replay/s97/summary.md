# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-04T02:00:00+00:00; 19 matches considered; 8855 cycles run, 8877 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only', 'mkt_dyn', 'mkt_dyn_e06', 'placebo_random', 'placebo_favourite'], 'reference': 'w03_e03', 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)', 'market_dynamics': 'current: 20261104 (s97-1)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 127 | 84 | 82 | 0 | +0.0196 [+0.0081, +0.0316] (4 matches / 81 bets; too few matches) | +86419 [-8204, +161143] (3 matches / 82 bets; too few matches) | 59,791 | 21.00 | 1 (1.2%) |
| w03_e06 | 97 | 63 | 61 | 0 | +0.0188 [+0.0088, +0.0373] (4 matches / 61 bets; too few matches) | +70787 [-116567, +168669] (3 matches / 61 bets; too few matches) | 65,251 | 15.75 | 1 (1.6%) |
| w05_e03 | 131 | 84 | 80 | 0 | +0.0155 [+0.0074, +0.0249] (4 matches / 82 bets; too few matches) | +64397 [+16934, +112964] (3 matches / 80 bets; too few matches) | 24,682 | 21.00 | 2 (2.4%) |
| w05_e06 | 126 | 81 | 77 | 0 | +0.0040 [-0.0150, +0.0167] (4 matches / 78 bets; too few matches) | +84000 [-39600, +213171] (3 matches / 77 bets; too few matches) | 97,146 | 20.25 | 2 (2.5%) |
| w07_e03 | 131 | 93 | 82 | 0 | +0.0092 [+0.0011, +0.0211] (5 matches / 90 bets; too few matches) | +111132 [+64665, +168827] (3 matches / 82 bets; too few matches) | 100,000 | 18.60 | 2 (2.2%) |
| w07_e06 | 107 | 85 | 81 | 0 | +0.0159 [+0.0067, +0.0242] (4 matches / 83 bets; too few matches) | +89417 [+64990, +128341] (3 matches / 81 bets; too few matches) | 94,553 | 21.25 | 7 (8.2%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |
| mkt_dyn | 234 | 165 | 106 | 0 | +0.0326 [+0.0195, +0.0454] (11 matches / 164 bets) | +145899 [-107803, +374740] (8 matches / 106 bets; too few matches) | 57,068 | 15.00 | 8 (4.8%) |
| mkt_dyn_e06 | 213 | 158 | 99 | 0 | +0.0402 [+0.0235, +0.0601] (9 matches / 156 bets; too few matches) | +246321 [-52187, +616553] (7 matches / 99 bets; too few matches) | 45,861 | 17.56 | 8 (5.1%) |
| placebo_random (placebo) | 96 | 47 | 45 | 0 | +0.0025 [-0.0221, +0.0101] (4 matches / 46 bets; too few matches) | -58832 [-157718, +14523] (3 matches / 45 bets; too few matches) | 52,573 | 11.75 | 2 (4.3%) |
| placebo_favourite (placebo) | 51 | 23 | 22 | 0 | +0.0015 [-0.0069, +0.0228] (3 matches / 22 bets; too few matches) | +72000 [-81127, +154328] (3 matches / 22 bets; too few matches) | 100,000 | 5.75 | 0 (0.0%) |

## Placebo controls (S85): excess CLV over placebo_random, match-clustered

The placebos copy the decision times and stakes of the reference variant (w03_e03). They are never primary, never ranked and never signals. The excess of a variant is, per match, its mean CLV minus placebo_random's mean CLV on that match; the interval resamples matches. A CI from fewer than 10 matches is labelled 'too few matches'.

No variant's CLV beats placebo_random: no excess-CLV confidence interval lies above zero on at least 10 matches.

| variant | excess CLV over placebo_random [95% CI] (matches / bets) | placebo_random CLV bets on those matches | above zero on 10+ matches |
|---|---|---:|---|
| w03_e03 | +0.0162 [+0.0030, +0.0294] (4 matches / 81 bets; too few matches) | 46 | no |
| w03_e06 | +0.0182 [-0.0008, +0.0482] (4 matches / 61 bets; too few matches) | 46 | no |
| w05_e03 | +0.0230 [+0.0073, +0.0384] (4 matches / 82 bets; too few matches) | 46 | no |
| w05_e06 | +0.0058 [-0.0157, +0.0314] (4 matches / 78 bets; too few matches) | 46 | no |
| w07_e03 | +0.0189 [-0.0019, +0.0378] (4 matches / 83 bets; too few matches) | 46 | no |
| w07_e06 | +0.0253 [+0.0049, +0.0456] (4 matches / 83 bets; too few matches) | 46 | no |
| market_only | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | no |
| mkt_dyn | +0.0385 [+0.0223, +0.0690] (4 matches / 118 bets; too few matches) | 46 | no |
| mkt_dyn_e06 | +0.0447 [+0.0179, +0.0846] (4 matches / 121 bets; too few matches) | 46 | no |


## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 127 | 84 | 43 | 66.1% | 72.5% | 0.0% (0) | 0 | 0.0% (0) | -0.0675 / -2.7% |
| w03_e06 | 97 | 63 | 34 | 64.9% | 73.0% | 0.0% (0) | 0 | 0.0% (0) | -0.1984 / -3.6% |
| w05_e03 | 131 | 84 | 47 | 64.1% | 48.9% | 0.0% (0) | 0 | 0.0% (0) | -0.0407 / -1.4% |
| w05_e06 | 126 | 81 | 45 | 64.3% | 54.7% | 0.0% (0) | 0 | 0.0% (0) | -0.0714 / -2.5% |
| w07_e03 | 130 | 93 | 37 | 71.5% | 50.5% | 0.0% (0) | 0 | 0.0% (0) | +0.0081 / -1.1% |
| w07_e06 | 107 | 85 | 22 | 79.4% | 63.3% | 0.0% (0) | 0 | 0.0% (0) | -0.0579 / -1.7% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |
| mkt_dyn | 234 | 165 | 69 | 70.5% | 71.8% | 0.0% (0) | 0 | 0.0% (0) | -0.0200 / -1.9% |
| mkt_dyn_e06 | 213 | 158 | 55 | 74.2% | 69.1% | 0.0% (0) | 0 | 0.0% (0) | -0.0199 / -1.3% |
| placebo_random (placebo) | 96 | 47 | 49 | 49.0% | 57.9% | 0.0% (0) | 0 | 0.0% (0) | -0.3111 / -3.9% |
| placebo_favourite (placebo) | 51 | 23 | 28 | 45.1% | 48.1% | 0.0% (0) | 0 | 0.0% (0) | -0.0274 / -1.9% |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 9 | +0.0173 [+0.0131, +0.0507] (2 matches / 9 bets; too few matches) | +0.0179 [+0.0086, +0.0507] (2 matches / 9 bets; too few matches) | -59160 [-68320, -50000] (2 matches / 9 bets; too few matches) |
| w03_e03 | in-play | 75 | +0.0199 [+0.0081, +0.0309] (4 matches / 72 bets; too few matches) | -0.0030 [-0.0119, +0.0340] (4 matches / 72 bets; too few matches) | +145579 [+94276, +236143] (3 matches / 73 bets; too few matches) |
| w03_e06 | pre-match | 2 | +0.0094 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0134 [n/a, n/a] (1 match / 2 bets; too few matches) | -35000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w03_e06 | in-play | 61 | +0.0191 [+0.0088, +0.0373] (4 matches / 59 bets; too few matches) | -0.0039 [-0.0118, +0.0263] (4 matches / 59 bets; too few matches) | +105787 [-11567, +168669] (3 matches / 59 bets; too few matches) |
| w05_e03 | pre-match | 15 | +0.0076 [+0.0030, +0.0130] (2 matches / 15 bets; too few matches) | +0.0057 [-0.0004, +0.0154] (2 matches / 15 bets; too few matches) | -82910 [-91340, -74480] (2 matches / 15 bets; too few matches) |
| w05_e03 | in-play | 69 | +0.0173 [+0.0078, +0.0281] (4 matches / 67 bets; too few matches) | +0.0049 [-0.0333, +0.0246] (4 matches / 67 bets; too few matches) | +147307 [+112964, +175012] (3 matches / 65 bets; too few matches) |
| w05_e06 | pre-match | 8 | +0.0148 [+0.0034, +0.0487] (2 matches / 8 bets; too few matches) | +0.0166 [-0.0030, +0.0487] (2 matches / 8 bets; too few matches) | -100000 [-100000, -100000] (2 matches / 8 bets; too few matches) |
| w05_e06 | in-play | 73 | +0.0028 [-0.0199, +0.0175] (4 matches / 70 bets; too few matches) | -0.0072 [-0.0357, +0.0256] (4 matches / 70 bets; too few matches) | +184000 [+110400, +228428] (3 matches / 69 bets; too few matches) |
| w07_e03 | pre-match | 26 | -0.0051 [-0.0272, +0.0072] (3 matches / 26 bets; too few matches) | +0.0023 [-0.0226, +0.0159] (3 matches / 26 bets; too few matches) | -85498 [-100000, -70996] (2 matches / 19 bets; too few matches) |
| w07_e03 | in-play | 67 | +0.0150 [+0.0040, +0.0360] (4 matches / 64 bets; too few matches) | +0.0210 [-0.0148, +0.0432] (4 matches / 64 bets; too few matches) | +196630 [+168827, +249905] (3 matches / 63 bets; too few matches) |
| w07_e06 | pre-match | 4 | +0.0166 [-0.0155, +0.0487] (2 matches / 4 bets; too few matches) | +0.0166 [-0.0155, +0.0487] (2 matches / 4 bets; too few matches) | -100000 [-100000, -100000] (2 matches / 4 bets; too few matches) |
| w07_e06 | in-play | 81 | +0.0158 [+0.0081, +0.0237] (4 matches / 79 bets; too few matches) | -0.0067 [-0.0480, +0.0123] (4 matches / 79 bets; too few matches) | +189417 [+128341, +224920] (3 matches / 77 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn | in-play | 165 | +0.0326 [+0.0195, +0.0454] (11 matches / 164 bets) | +0.0281 [+0.0112, +0.0487] (11 matches / 164 bets) | +145899 [-107803, +374740] (8 matches / 106 bets; too few matches) |
| mkt_dyn_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| mkt_dyn_e06 | in-play | 158 | +0.0402 [+0.0235, +0.0601] (9 matches / 156 bets; too few matches) | +0.0422 [+0.0132, +0.0742] (9 matches / 156 bets; too few matches) | +246321 [-52187, +616553] (7 matches / 99 bets; too few matches) |
| placebo_random (placebo) | pre-match | 5 | -0.0053 [-0.0376, +0.0028] (2 matches / 5 bets; too few matches) | -0.0072 [-0.0376, -0.0048] (2 matches / 5 bets; too few matches) | -15000 [-38000, +8000] (2 matches / 5 bets; too few matches) |
| placebo_random (placebo) | in-play | 42 | +0.0035 [-0.0221, +0.0121] (4 matches / 41 bets; too few matches) | -0.0194 [-0.0584, -0.0042] (4 matches / 41 bets; too few matches) | -43832 [-157718, +23701] (3 matches / 40 bets; too few matches) |
| placebo_favourite (placebo) | pre-match | 3 | +0.0012 [-0.0382, +0.0209] (2 matches / 3 bets; too few matches) | +0.0165 [-0.0382, +0.0209] (2 matches / 3 bets; too few matches) | +31620 [+6240, +57000] (2 matches / 3 bets; too few matches) |
| placebo_favourite (placebo) | in-play | 20 | +0.0015 [-0.0069, +0.0241] (3 matches / 19 bets; too few matches) | +0.0132 [+0.0064, +0.0316] (3 matches / 19 bets; too few matches) | +40380 [-81127, +144968] (3 matches / 19 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0196 [+0.0008, +0.0382] | n/a [-2080.00, +4346.17] |
| w03_e06 | +0.0188 [+0.0019, +0.0364] | n/a [-2816.56, +5330.71] |
| w05_e03 | +0.0155 [+0.0008, +0.0293] | n/a [-3354.40, +4778.34] |
| w05_e06 | +0.0040 [-0.0131, +0.0211] | n/a [-3374.17, +5939.44] |
| w07_e03 | +0.0092 [-0.0070, +0.0250] | n/a [-3066.87, +5534.73] |
| w07_e06 | +0.0159 [-0.0012, +0.0327] | n/a [-3806.08, +6514.62] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |
| mkt_dyn | +0.0326 [+0.0216, +0.0427] | n/a [-3703.03, +6466.74] |
| mkt_dyn_e06 | +0.0402 [+0.0288, +0.0516] | n/a [-2412.40, +7775.02] |
| placebo_random (placebo) | +0.0025 [-0.0180, +0.0220] | n/a [-4695.37, +1906.52] |
| placebo_favourite (placebo) | +0.0015 [-0.0380, +0.0368] | n/a [-1162.43, +7256.19] |

## Intents by side

- w03_e03: BACK_away 25, BACK_home 38, LAY_away 39, LAY_home 25
- w03_e06: BACK_away 18, BACK_home 31, LAY_away 28, LAY_home 20
- w05_e03: BACK_away 31, BACK_home 46, LAY_away 29, LAY_home 25
- w05_e06: BACK_away 29, BACK_home 36, LAY_away 40, LAY_home 21
- w07_e03: BACK_away 34, BACK_home 36, LAY_away 26, LAY_home 35
- w07_e06: BACK_away 29, BACK_home 28, LAY_away 27, LAY_home 23
- market_only: none
- mkt_dyn: BACK_away 39, BACK_home 34, LAY_away 82, LAY_home 79
- mkt_dyn_e06: BACK_away 31, BACK_home 30, LAY_away 78, LAY_home 74
- placebo_random (placebo): BACK_away 26, BACK_home 20, LAY_away 36, LAY_home 14
- placebo_favourite (placebo): BACK_away 23, BACK_home 28

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 106, 'simulated': 9532, 'too_early': 6316, 'twin': 1862}
- Matches: {'no_market': 670}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 55, 'flip_cooldown': 351, 'inplay_without_state': 61760, 'no_band': 246, 'spread_too_wide': 929, 'stale_book': 237, 'trade_rate': 2161}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst | mkt_dyn fills / worst | mkt_dyn_e06 fills / worst | placebo_random (placebo) fills / worst | placebo_favourite (placebo) fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lb_36262294 | Melbourne Stars W v Perth Scorchers W | WBBL | - | - | - | - | - | - | - | 3 / 4,270 | - | - | - |
| lb_36264203 | Wellington Firebirds v Northern Districts | Ford Trophy | - | - | - | - | - | - | - | 28 / 0 | 28 / 0 | - | - |
| lb_36266935 | Central Stags v Auckland Aces | Ford Trophy | 2 / 41,911 | 2 / 42,718 | 4 / 24,682 | 4 / 80,869 | 4 / 20,225 | 4 / 20,593 | - | 30 / 0 | 31 / 0 | 2 / 3,703 | 1 / 8,542 |
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 29 / 59,791 | 23 / 65,251 | 31 / 0 | 28 / 97,146 | 31 / 0 | 29 / 0 | - | 28 / 0 | 29 / 0 | 19 / 5,527 | 7 / 100,000 |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 31 / 2,735 | 24 / 38,856 | 32 / 0 | 32 / 13,200 | 30 / 0 | 32 / 0 | - | 30 / 0 | 30 / 0 | 18 / 17,200 | 5 / 100,000 |
| lb_36269512 | West Indies A v England Lions | T20 Matches | 22 / 21,947 | 14 / 57,654 | 17 / 21,746 | 17 / 89,329 | 21 / 100,000 | 20 / 94,553 | - | 30 / 0 | 33 / 0 | 8 / 52,573 | 10 / 48,014 |
| lb_36271322 | New Zealand v India | One Day Internationals | - | - | 0 / 0 | - | 7 / 0 | - | - | - | - | - | - |
| lb_75330686 | Otago Volts v Canterbury Kings | Ford Trophy | - | - | - | - | - | - | - | 1 / 25,000 | - | - | - |
| lb_75747938 | Dubai v Ajman | Emirates D20 | - | - | - | - | - | - | - | 1 / 22,498 | 1 / 22,941 | - | - |
| lb_75878940 | Oman v Netherlands | ICC Cricket World Cup League Two | - | - | - | - | - | - | - | 3 / 57,068 | 2 / 45,861 | - | - |
| lb_76073798 | Incredibles v Warriors | T20 PCB Super-3, Women | - | - | - | - | - | - | - | 6 / 13,812 | 1 / 24,489 | - | - |
| lb_76227860 | Cdb A v Dpgc | T20 MCA Super Premier League | - | - | - | - | - | - | - | 0 / 0 | - | - | - |
| lb_76227866 | Hnb A v Hayleys Group A | T20 MCA Super Premier League | - | - | - | - | - | - | - | 5 / 17,973 | 3 / 19,070 | - | - |

