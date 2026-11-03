# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 16 matches considered; 5298 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 30, 'size_cap': False, 'unknown_size_cap': 10000.0, 'lay_size_unit': 'stake', 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 21 | 12 | 10 | 0 | +0.0280 [n/a, n/a] (1 match / 10 bets; too few matches) | +7109 [n/a, n/a] (1 match / 10 bets; too few matches) | 20,520 | 6.00 | 0 (0.0%) |
| w03_e06 | 13 | 9 | 9 | 0 | -0.0063 [n/a, n/a] (1 match / 9 bets; too few matches) | +4080 [n/a, n/a] (1 match / 9 bets; too few matches) | 0 | 9.00 | 1 (11.1%) |
| w05_e03 | 23 | 12 | 9 | 0 | +0.0055 [n/a, n/a] (1 match / 9 bets; too few matches) | +4313 [n/a, n/a] (1 match / 9 bets; too few matches) | 31,100 | 6.00 | 0 (0.0%) |
| w05_e06 | 17 | 13 | 10 | 0 | +0.0085 [n/a, n/a] (1 match / 10 bets; too few matches) | +3773 [n/a, n/a] (1 match / 10 bets; too few matches) | 44,100 | 6.50 | 1 (7.7%) |
| w07_e03 | 31 | 17 | 10 | 0 | +0.0038 [n/a, n/a] (1 match / 10 bets; too few matches) | +3120 [n/a, n/a] (1 match / 10 bets; too few matches) | 50,000 | 8.50 | 0 (0.0%) |
| w07_e06 | 19 | 12 | 10 | 0 | +0.0296 [n/a, n/a] (1 match / 10 bets; too few matches) | +14643 [n/a, n/a] (1 match / 10 bets; too few matches) | 50,000 | 6.00 | 1 (8.3%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Fills and sizes (S87): stake fill rate, size cap, slippage vs the decision price

| variant | decided | fills | unfilled | fill rate (count) | stake fill rate | size-capped share | stake lapsed | size-unknown share | mean adverse slippage (odds / %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| w03_e03 | 21 | 12 | 9 | 57.1% | 58.8% | 0.0% (0) | 0 | 0.0% (0) | -0.0417 / -1.7% |
| w03_e06 | 13 | 9 | 4 | 69.2% | 93.2% | 0.0% (0) | 0 | 0.0% (0) | -0.0800 / -2.4% |
| w05_e03 | 23 | 12 | 11 | 52.2% | 47.2% | 0.0% (0) | 0 | 0.0% (0) | +0.0017 / -0.1% |
| w05_e06 | 17 | 13 | 4 | 76.5% | 68.1% | 0.0% (0) | 0 | 0.0% (0) | -0.0146 / -0.6% |
| w07_e03 | 31 | 17 | 14 | 54.8% | 41.0% | 0.0% (0) | 0 | 0.0% (0) | -0.0182 / -0.8% |
| w07_e06 | 19 | 12 | 7 | 63.2% | 72.2% | 0.0% (0) | 0 | 0.0% (0) | +0.0050 / 0.1% |
| market_only | 0 | 0 | 0 | n/a | n/a | n/a (0) | 0 | n/a (0) | n/a / n/a |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 3 | +0.0507 [n/a, n/a] (1 match / 1 bet; too few matches) | +0.0507 [n/a, n/a] (1 match / 1 bet; too few matches) | -25000 [n/a, n/a] (1 match / 1 bet; too few matches) |
| w03_e03 | in-play | 9 | +0.0255 [n/a, n/a] (1 match / 9 bets; too few matches) | +0.0436 [n/a, n/a] (1 match / 9 bets; too few matches) | +32109 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 9 | -0.0063 [n/a, n/a] (1 match / 9 bets; too few matches) | +0.0076 [n/a, n/a] (1 match / 9 bets; too few matches) | +4080 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w05_e03 | pre-match | 10 | +0.0120 [n/a, n/a] (1 match / 7 bets; too few matches) | +0.0135 [n/a, n/a] (1 match / 7 bets; too few matches) | -37200 [n/a, n/a] (1 match / 7 bets; too few matches) |
| w05_e03 | in-play | 2 | -0.0175 [n/a, n/a] (1 match / 2 bets; too few matches) | -0.0093 [n/a, n/a] (1 match / 2 bets; too few matches) | +41513 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | pre-match | 5 | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0487 [n/a, n/a] (1 match / 2 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w05_e06 | in-play | 8 | -0.0016 [n/a, n/a] (1 match / 8 bets; too few matches) | -0.0008 [n/a, n/a] (1 match / 8 bets; too few matches) | +53773 [n/a, n/a] (1 match / 8 bets; too few matches) |
| w07_e03 | pre-match | 16 | -0.0041 [n/a, n/a] (1 match / 9 bets; too few matches) | -0.0018 [n/a, n/a] (1 match / 9 bets; too few matches) | -36880 [n/a, n/a] (1 match / 9 bets; too few matches) |
| w07_e03 | in-play | 1 | +0.0747 [n/a, n/a] (1 match / 1 bet; too few matches) | +0.0747 [n/a, n/a] (1 match / 1 bet; too few matches) | +40000 [n/a, n/a] (1 match / 1 bet; too few matches) |
| w07_e06 | pre-match | 4 | +0.0344 [n/a, n/a] (1 match / 2 bets; too few matches) | +0.0344 [n/a, n/a] (1 match / 2 bets; too few matches) | -50000 [n/a, n/a] (1 match / 2 bets; too few matches) |
| w07_e06 | in-play | 8 | +0.0284 [n/a, n/a] (1 match / 8 bets; too few matches) | +0.0229 [n/a, n/a] (1 match / 8 bets; too few matches) | +64643 [n/a, n/a] (1 match / 8 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0280 [-0.0116, +0.0610] | n/a [-8764.15, +11159.32] |
| w03_e06 | -0.0063 [-0.0629, +0.0504] | n/a [-13952.22, +15438.69] |
| w05_e03 | +0.0055 [-0.0109, +0.0211] | n/a [-12014.61, +17036.78] |
| w05_e06 | +0.0085 [-0.0102, +0.0257] | n/a [-15532.50, +20587.99] |
| w07_e03 | +0.0038 [-0.0160, +0.0252] | n/a [-10458.55, +11581.50] |
| w07_e06 | +0.0296 [+0.0071, +0.0496] | n/a [-14228.12, +19285.61] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 10, BACK_home 2, LAY_away 7, LAY_home 2
- w03_e06: BACK_away 4, BACK_home 2, LAY_away 3, LAY_home 4
- w05_e03: BACK_away 12, BACK_home 4, LAY_away 4, LAY_home 3
- w05_e06: BACK_away 8, BACK_home 2, LAY_away 4, LAY_home 3
- w07_e03: BACK_away 15, BACK_home 10, LAY_away 4, LAY_home 2
- w07_e06: BACK_away 6, BACK_home 1, LAY_away 6, LAY_home 6
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 748}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 77, 'inplay_without_state': 26448, 'no_band': 41, 'spread_too_wide': 2, 'trade_rate': 62}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 10 / 0 | 9 / 0 | 9 / 0 | 10 / 0 | 10 / 0 | 10 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 20,520 | - | 3 / 31,100 | 3 / 44,100 | 7 / 50,000 | 2 / 50,000 | - |

