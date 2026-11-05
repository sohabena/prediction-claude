# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 16 matches considered; 5298 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 30, 'size_cap': True, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 158 | 92 | 90 | 0 | +0.0120 [n/a, n/a] (1 match / 78 bets; too few matches) | -15199 [n/a, n/a] (1 match / 90 bets; too few matches) | 20,000 | 46.00 | 87 (94.6%) |
| w03_e06 | 35 | 23 | 23 | 0 | +0.0575 [n/a, n/a] (1 match / 21 bets; too few matches) | -432 [n/a, n/a] (1 match / 23 bets; too few matches) | 432 | 23.00 | 23 (100.0%) |
| w05_e03 | 228 | 146 | 143 | 0 | +0.0143 [n/a, n/a] (1 match / 128 bets; too few matches) | -17148 [n/a, n/a] (1 match / 143 bets; too few matches) | 30,000 | 73.00 | 132 (90.4%) |
| w05_e06 | 189 | 120 | 118 | 0 | +0.0109 [n/a, n/a] (1 match / 103 bets; too few matches) | -36697 [n/a, n/a] (1 match / 118 bets; too few matches) | 36,697 | 60.00 | 112 (93.3%) |
| w07_e03 | 244 | 171 | 163 | 0 | +0.0142 [n/a, n/a] (1 match / 148 bets; too few matches) | -18960 [n/a, n/a] (1 match / 163 bets; too few matches) | 39,700 | 85.50 | 153 (89.5%) |
| w07_e06 | 222 | 152 | 149 | 0 | +0.0169 [n/a, n/a] (1 match / 134 bets; too few matches) | -32250 [n/a, n/a] (1 match / 149 bets; too few matches) | 32,250 | 76.00 | 143 (94.1%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 158 | 92 | 66 | 58.2% | 2.9% | 98.9% (91) | 910,812 | 5.4% (5) | -0.1497 / -3.2% |
| w03_e06 | 35 | 23 | 12 | 65.7% | 1.7% | 100.0% (23) | 173,465 | 0.0% (0) | -0.0743 / -2.3% |
| w05_e03 | 228 | 146 | 82 | 64.0% | 3.8% | 96.6% (141) | 1,872,654 | 8.2% (12) | -0.0936 / -1.9% |
| w05_e06 | 189 | 120 | 69 | 63.5% | 1.6% | 99.2% (119) | 2,922,795 | 5.8% (7) | -0.1143 / -2.3% |
| w07_e03 | 244 | 171 | 73 | 70.1% | 4.5% | 95.9% (164) | 2,342,060 | 9.9% (17) | -0.0685 / -1.4% |
| w07_e06 | 222 | 152 | 70 | 68.5% | 1.6% | 99.3% (151) | 3,652,250 | 5.3% (8) | -0.0849 / -1.7% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |

## Size cap: capped (reported) vs uncapped, P&L in currency (S87)

The difference is capped minus uncapped. Its 95% CI resamples matches. Uncapped is the same replay with no size: the pre-S87 fill, in which a fill takes the whole asked stake.

| variant | P&L capped | P&L uncapped | difference [95% CI] (matches) | filled stake capped | filled stake uncapped | fills capped / uncapped | max worst case capped / uncapped |
|---|---:|---:|---|---:|---:|---:|---:|
| w03_e03 | -15,199 | 7,109 | -22308 [-44617, +0] (2) | 52,801 | 182,371 | 92 / 12 | 20,000 / 20,520 |
| w03_e06 | -432 | 4,080 | -4512 [n/a, n/a] (1) | 3,635 | 191,000 | 23 / 9 | 432 / 0 |
| w05_e03 | -17,148 | 4,313 | -21461 [-42922, +0] (2) | 134,346 | 250,513 | 146 / 12 | 30,000 / 31,100 |
| w05_e06 | -36,697 | 3,773 | -40470 [-80940, +0] (2) | 83,303 | 318,598 | 120 / 13 | 36,697 / 44,100 |
| w07_e03 | -18,960 | 3,120 | -22080 [-44161, +0] (2) | 178,840 | 224,750 | 171 / 17 | 39,700 / 50,000 |
| w07_e06 | -32,250 | 14,643 | -46893 [-93787, +0] (2) | 97,750 | 292,573 | 152 / 12 | 32,250 / 50,000 |
| market_only | 0 | 0 | +0 [n/a, n/a] (0) | 0 | 0 | 0 / 0 | 0 / 0 |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 5 | +0.0507 [n/a, n/a] (1 match / 3 bets; too few matches) | +0.0507 [n/a, n/a] (1 match / 3 bets; too few matches) | -24000 [n/a, n/a] (1 match / 3 bets; too few matches) |
| w03_e03 | in-play | 87 | +0.0104 [n/a, n/a] (1 match / 75 bets; too few matches) | +0.0065 [n/a, n/a] (1 match / 75 bets; too few matches) | +8801 [n/a, n/a] (1 match / 87 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 23 | +0.0575 [n/a, n/a] (1 match / 21 bets; too few matches) | +0.0524 [n/a, n/a] (1 match / 21 bets; too few matches) | -432 [n/a, n/a] (1 match / 23 bets; too few matches) |
| w05_e03 | pre-match | 12 | +0.0126 [n/a, n/a] (1 match / 9 bets; too few matches) | +0.0140 [n/a, n/a] (1 match / 9 bets; too few matches) | -34600 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w05_e03 | in-play | 134 | +0.0144 [n/a, n/a] (1 match / 119 bets; too few matches) | -0.0109 [n/a, n/a] (1 match / 119 bets; too few matches) | +17452 [n/a, n/a] (1 match / 134 bets; too few matches) |
| w05_e06 | pre-match | 7 | +0.0499 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0499 [n/a, n/a] (1 match / 5 bets; too few matches) | -50000 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w05_e06 | in-play | 113 | +0.0089 [n/a, n/a] (1 match / 98 bets; too few matches) | -0.0084 [n/a, n/a] (1 match / 98 bets; too few matches) | +13303 [n/a, n/a] (1 match / 113 bets; too few matches) |
| w07_e03 | pre-match | 17 | -0.0011 [n/a, n/a] (1 match / 9 bets; too few matches) | -0.0002 [n/a, n/a] (1 match / 9 bets; too few matches) | -37800 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | in-play | 154 | +0.0151 [n/a, n/a] (1 match / 139 bets; too few matches) | -0.0083 [n/a, n/a] (1 match / 139 bets; too few matches) | +18840 [n/a, n/a] (1 match / 154 bets; too few matches) |
| w07_e06 | pre-match | 8 | +0.0434 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0434 [n/a, n/a] (1 match / 5 bets; too few matches) | -50000 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w07_e06 | in-play | 144 | +0.0159 [n/a, n/a] (1 match / 129 bets; too few matches) | -0.0088 [n/a, n/a] (1 match / 129 bets; too few matches) | +17750 [n/a, n/a] (1 match / 144 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0120 [-0.0170, +0.0431] | n/a [-535.86, +99.85] |
| w03_e06 | +0.0575 [+0.0261, +0.0927] | n/a [-114.57, +59.41] |
| w05_e03 | +0.0143 [-0.0051, +0.0330] | n/a [-505.75, +244.30] |
| w05_e06 | +0.0109 [-0.0123, +0.0344] | n/a [-731.37, +17.49] |
| w07_e03 | +0.0142 [-0.0028, +0.0311] | n/a [-454.96, +206.15] |
| w07_e06 | +0.0169 [-0.0010, +0.0347] | n/a [-535.85, +50.09] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 8, LAY_away 150
- w03_e06: BACK_away 7, LAY_away 28
- w05_e03: BACK_away 15, BACK_home 8, LAY_away 204, LAY_home 1
- w05_e06: BACK_away 9, LAY_away 180
- w07_e03: BACK_away 17, BACK_home 11, LAY_away 215, LAY_home 1
- w07_e06: BACK_away 10, LAY_away 212
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 748}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 69, 'inplay_without_state': 26448, 'no_band': 41, 'trade_rate': 140}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 90 / 15,199 | 23 / 432 | 143 / 17,148 | 118 / 36,697 | 163 / 18,960 | 149 / 32,250 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 20,000 | - | 3 / 30,000 | 2 / 20,000 | 8 / 39,700 | 3 / 30,000 | - |

