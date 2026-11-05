# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 16 matches considered; 5298 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 120, 'size_cap': True, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 56 | 27 | 25 | 0 | +0.0460 [n/a, n/a] (1 match / 23 bets; too few matches) | -22046 [n/a, n/a] (1 match / 25 bets; too few matches) | 22,046 | 13.50 | 21 (77.8%) |
| w03_e06 | 26 | 16 | 16 | 0 | +0.0491 [n/a, n/a] (1 match / 14 bets; too few matches) | -42 [n/a, n/a] (1 match / 16 bets; too few matches) | 42 | 16.00 | 16 (100.0%) |
| w05_e03 | 85 | 44 | 40 | 0 | +0.0134 [n/a, n/a] (1 match / 37 bets; too few matches) | -28314 [n/a, n/a] (1 match / 40 bets; too few matches) | 28,314 | 22.00 | 31 (70.5%) |
| w05_e06 | 59 | 32 | 30 | 0 | +0.0142 [n/a, n/a] (1 match / 27 bets; too few matches) | -47266 [n/a, n/a] (1 match / 30 bets; too few matches) | 47,266 | 16.00 | 25 (78.1%) |
| w07_e03 | 92 | 53 | 45 | 0 | +0.0111 [n/a, n/a] (1 match / 42 bets; too few matches) | -33581 [n/a, n/a] (1 match / 45 bets; too few matches) | 50,000 | 26.50 | 35 (66.0%) |
| w07_e06 | 63 | 36 | 34 | 0 | +0.0114 [n/a, n/a] (1 match / 31 bets; too few matches) | -47029 [n/a, n/a] (1 match / 34 bets; too few matches) | 47,029 | 18.00 | 29 (80.6%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 56 | 27 | 29 | 48.2% | 5.9% | 96.3% (26) | 358,439 | 18.5% (5) | -0.4430 / -10.1% |
| w03_e06 | 26 | 16 | 10 | 61.5% | 1.7% | 100.0% (16) | 174,322 | 0.0% (0) | -0.2044 / -6.3% |
| w05_e03 | 84 | 44 | 40 | 52.4% | 7.0% | 90.9% (40) | 685,988 | 25.0% (11) | -0.2289 / -5.1% |
| w05_e06 | 59 | 32 | 27 | 54.2% | 3.9% | 96.9% (31) | 885,084 | 21.9% (7) | -0.3059 / -6.2% |
| w07_e03 | 92 | 53 | 39 | 57.6% | 8.9% | 84.9% (45) | 777,645 | 32.1% (17) | -0.1613 / -3.2% |
| w07_e06 | 62 | 36 | 26 | 58.1% | 3.6% | 97.2% (35) | 962,625 | 19.4% (7) | -0.2461 / -4.8% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |

## Size cap: capped (reported) vs uncapped, P&L in currency (S87)

The difference is capped minus uncapped. Its 95% CI resamples matches. Uncapped is the same replay with no size: the pre-S87 fill, in which a fill takes the whole asked stake.

| variant | P&L capped | P&L uncapped | difference [95% CI] (matches) | filled stake capped | filled stake uncapped | fills capped / uncapped | max worst case capped / uncapped |
|---|---:|---:|---|---:|---:|---:|---:|
| w03_e03 | -22,046 | 4,326 | -26371 [-52743, +0] (2) | 51,763 | 202,589 | 27 / 11 | 22,046 / 25,000 |
| w03_e06 | -42 | 8,002 | -8044 [n/a, n/a] (1) | 3,478 | 252,213 | 16 / 11 | 42 / 0 |
| w05_e03 | -28,314 | 16,103 | -44417 [-88834, +0] (2) | 116,012 | 317,818 | 44 / 15 | 28,314 / 48,200 |
| w05_e06 | -47,266 | 24,908 | -72174 [-144348, +0] (2) | 72,734 | 343,498 | 32 / 12 | 47,266 / 50,000 |
| w07_e03 | -33,581 | 4,180 | -37761 [-75523, +0] (2) | 160,675 | 215,000 | 53 / 17 | 50,000 / 39,250 |
| w07_e06 | -47,029 | 15,509 | -62538 [-125075, +0] (2) | 73,027 | 274,209 | 36 / 13 | 47,029 / 50,000 |
| market_only | 0 | 0 | +0 [n/a, n/a] (0) | 0 | 0 | 0 / 0 | 0 / 0 |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 5 | +0.0494 [n/a, n/a] (1 match / 3 bets; too few matches) | +0.0491 [n/a, n/a] (1 match / 3 bets; too few matches) | -24000 [n/a, n/a] (1 match / 3 bets; too few matches) |
| w03_e03 | in-play | 22 | +0.0455 [n/a, n/a] (1 match / 20 bets; too few matches) | +0.0252 [n/a, n/a] (1 match / 20 bets; too few matches) | +1954 [n/a, n/a] (1 match / 22 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 16 | +0.0491 [n/a, n/a] (1 match / 14 bets; too few matches) | +0.0261 [n/a, n/a] (1 match / 14 bets; too few matches) | -42 [n/a, n/a] (1 match / 16 bets; too few matches) |
| w05_e03 | pre-match | 11 | +0.0211 [n/a, n/a] (1 match / 7 bets; too few matches) | +0.0222 [n/a, n/a] (1 match / 7 bets; too few matches) | -34340 [n/a, n/a] (1 match / 7 bets; too few matches) |
| w05_e03 | in-play | 33 | +0.0116 [n/a, n/a] (1 match / 30 bets; too few matches) | +0.0404 [n/a, n/a] (1 match / 30 bets; too few matches) | +6026 [n/a, n/a] (1 match / 33 bets; too few matches) |
| w05_e06 | pre-match | 7 | +0.0491 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0491 [n/a, n/a] (1 match / 5 bets; too few matches) | -50000 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w05_e06 | in-play | 25 | +0.0063 [n/a, n/a] (1 match / 22 bets; too few matches) | -0.0038 [n/a, n/a] (1 match / 22 bets; too few matches) | +2734 [n/a, n/a] (1 match / 25 bets; too few matches) |
| w07_e03 | pre-match | 17 | -0.0014 [n/a, n/a] (1 match / 9 bets; too few matches) | -0.0005 [n/a, n/a] (1 match / 9 bets; too few matches) | -37880 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | in-play | 36 | +0.0145 [n/a, n/a] (1 match / 33 bets; too few matches) | +0.0227 [n/a, n/a] (1 match / 33 bets; too few matches) | +4299 [n/a, n/a] (1 match / 36 bets; too few matches) |
| w07_e06 | pre-match | 7 | +0.0491 [n/a, n/a] (1 match / 5 bets; too few matches) | +0.0491 [n/a, n/a] (1 match / 5 bets; too few matches) | -50000 [n/a, n/a] (1 match / 5 bets; too few matches) |
| w07_e06 | in-play | 29 | +0.0041 [n/a, n/a] (1 match / 26 bets; too few matches) | -0.0108 [n/a, n/a] (1 match / 26 bets; too few matches) | +2971 [n/a, n/a] (1 match / 29 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0460 [-0.0036, +0.1002] | n/a [-2086.02, +81.73] |
| w03_e06 | +0.0491 [+0.0104, +0.0924] | n/a [-171.51, +174.98] |
| w05_e03 | +0.0134 [-0.0291, +0.0564] | n/a [-1972.65, +440.88] |
| w05_e06 | +0.0142 [-0.0459, +0.0704] | n/a [-2931.48, -247.65] |
| w07_e03 | +0.0111 [-0.0267, +0.0471] | n/a [-1892.92, +372.19] |
| w07_e06 | +0.0114 [-0.0423, +0.0620] | n/a [-2579.05, -205.76] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 9, BACK_home 1, LAY_away 46
- w03_e06: BACK_away 7, LAY_away 19
- w05_e03: BACK_away 13, BACK_home 10, LAY_away 62
- w05_e06: BACK_away 8, BACK_home 1, LAY_away 50
- w07_e03: BACK_away 18, BACK_home 11, LAY_away 62, LAY_home 1
- w07_e06: BACK_away 10, BACK_home 1, LAY_away 52
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 748}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 172, 'inplay_without_state': 26448, 'no_band': 41, 'trade_rate': 69}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 25 / 22,046 | 16 / 42 | 40 / 28,314 | 30 / 47,266 | 45 / 33,581 | 34 / 47,029 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 20,000 | - | 4 / 20,000 | 2 / 20,000 | 8 / 50,000 | 2 / 20,000 | - |

