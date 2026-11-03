# PHOENIX paper replay

Window 2026-11-01T00:00:00+00:00 to 2026-11-03T10:56:38+00:00; 16 matches considered; 5298 cycles run, 8821 grid steps skipped (no fresh price, no open bet). Settings: {'include_srl': False, 'variants': ['w03_e03', 'w03_e06', 'w05_e03', 'w05_e06', 'w07_e03', 'w07_e06', 'market_only'], 'cycle_seconds': 15, 'fill_delay_seconds': 60, 'max_match_loss': 100000.0, 'guards': {'max_spread': 0.15, 'in_play_max_age_s': 60.0, 'prematch_max_age_s': 900.0, 'max_trades_per_window': 3, 'trade_window_s': 600.0, 'flip_cooldown_s': 300.0}, 'suspensions': 'none (every variant trades; the live S73 state is not applied)'}.

Virtual only. The live ledger (`paper_bets`) was not read or written. CLV is in probability points against the reference price; P&L is gross of commission. Headline CIs are seeded percentile bootstraps that resample matches (S84): bets on one match move together. Each figure shows its matches and bets; a CI from fewer than 10 matches is labelled 'too few matches'. Churn: a trade is a filled bet; tiny trades have stake under 2,000.

## Per variant (match-clustered)

| variant | intents | fills | settled | void | CLV mean [95% CI] (matches / bets) | P&L total [95% CI] (matches / bets) | max worst case / match | trades per match | tiny trades |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| w03_e03 | 87 | 42 | 40 | 0 | +0.0305 [n/a, n/a] (1 match / 40 bets; too few matches) | -9338 [n/a, n/a] (1 match / 40 bets; too few matches) | 54,616 | 21.00 | 1 (2.4%) |
| w03_e06 | 53 | 30 | 30 | 0 | +0.0209 [n/a, n/a] (1 match / 28 bets; too few matches) | +33627 [n/a, n/a] (1 match / 30 bets; too few matches) | 57,629 | 30.00 | 1 (3.3%) |
| w05_e03 | 81 | 45 | 42 | 0 | +0.0264 [n/a, n/a] (1 match / 42 bets; too few matches) | -23000 [n/a, n/a] (1 match / 42 bets; too few matches) | 75,000 | 22.50 | 0 (0.0%) |
| w05_e06 | 76 | 42 | 40 | 0 | +0.0352 [n/a, n/a] (1 match / 40 bets; too few matches) | -8965 [n/a, n/a] (1 match / 40 bets; too few matches) | 50,000 | 21.00 | 1 (2.4%) |
| w07_e03 | 79 | 49 | 45 | 0 | +0.0186 [n/a, n/a] (1 match / 45 bets; too few matches) | +5983 [n/a, n/a] (1 match / 45 bets; too few matches) | 100,000 | 24.50 | 2 (4.1%) |
| w07_e06 | 75 | 45 | 42 | 0 | +0.0313 [n/a, n/a] (1 match / 42 bets; too few matches) | +37044 [n/a, n/a] (1 match / 42 bets; too few matches) | 75,000 | 22.50 | 1 (2.2%) |
| market_only | 0 | 0 | 0 | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) | 0 | n/a | 0 (n/a) |

## Pre-match and in-play, separately (match-clustered)

| variant | segment | fills | CLV mean [95% CI] (matches / bets) | stake-weighted CLV [95% CI] | P&L total [95% CI] (matches / bets) |
|---|---|---:|---|---|---|
| w03_e03 | pre-match | 6 | +0.0507 [n/a, n/a] (1 match / 4 bets; too few matches) | +0.0507 [n/a, n/a] (1 match / 4 bets; too few matches) | -100000 [n/a, n/a] (1 match / 4 bets; too few matches) |
| w03_e03 | in-play | 36 | +0.0282 [n/a, n/a] (1 match / 36 bets; too few matches) | +0.0253 [n/a, n/a] (1 match / 36 bets; too few matches) | +90662 [n/a, n/a] (1 match / 36 bets; too few matches) |
| w03_e06 | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| w03_e06 | in-play | 30 | +0.0209 [n/a, n/a] (1 match / 28 bets; too few matches) | +0.0210 [n/a, n/a] (1 match / 28 bets; too few matches) | +33627 [n/a, n/a] (1 match / 30 bets; too few matches) |
| w05_e03 | pre-match | 7 | +0.0274 [n/a, n/a] (1 match / 4 bets; too few matches) | +0.0274 [n/a, n/a] (1 match / 4 bets; too few matches) | -100000 [n/a, n/a] (1 match / 4 bets; too few matches) |
| w05_e03 | in-play | 38 | +0.0263 [n/a, n/a] (1 match / 38 bets; too few matches) | +0.0125 [n/a, n/a] (1 match / 38 bets; too few matches) | +77000 [n/a, n/a] (1 match / 38 bets; too few matches) |
| w05_e06 | pre-match | 6 | +0.0497 [n/a, n/a] (1 match / 4 bets; too few matches) | +0.0497 [n/a, n/a] (1 match / 4 bets; too few matches) | -100000 [n/a, n/a] (1 match / 4 bets; too few matches) |
| w05_e06 | in-play | 36 | +0.0336 [n/a, n/a] (1 match / 36 bets; too few matches) | +0.0258 [n/a, n/a] (1 match / 36 bets; too few matches) | +91035 [n/a, n/a] (1 match / 36 bets; too few matches) |
| w07_e03 | pre-match | 8 | +0.0181 [n/a, n/a] (1 match / 4 bets; too few matches) | +0.0181 [n/a, n/a] (1 match / 4 bets; too few matches) | -100000 [n/a, n/a] (1 match / 4 bets; too few matches) |
| w07_e03 | in-play | 41 | +0.0187 [n/a, n/a] (1 match / 41 bets; too few matches) | +0.0121 [n/a, n/a] (1 match / 41 bets; too few matches) | +105983 [n/a, n/a] (1 match / 41 bets; too few matches) |
| w07_e06 | pre-match | 7 | +0.0416 [n/a, n/a] (1 match / 4 bets; too few matches) | +0.0416 [n/a, n/a] (1 match / 4 bets; too few matches) | -100000 [n/a, n/a] (1 match / 4 bets; too few matches) |
| w07_e06 | in-play | 38 | +0.0302 [n/a, n/a] (1 match / 38 bets; too few matches) | +0.0191 [n/a, n/a] (1 match / 38 bets; too few matches) | +137044 [n/a, n/a] (1 match / 38 bets; too few matches) |
| market_only | pre-match | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |
| market_only | in-play | 0 | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | n/a [n/a, n/a] (0 matches / 0 bets; too few matches) | +0 [n/a, n/a] (0 matches / 0 bets; too few matches) |

## Detail: per-bet intervals (reference only; they ignore the match correlation)

| variant | CLV mean per bet [95% CI] | P&L per bet [95% CI] |
|---|---|---|
| w03_e03 | +0.0305 [+0.0067, +0.0540] | n/a [-4625.09, +3837.09] |
| w03_e06 | +0.0209 [-0.0115, +0.0551] | n/a [-4394.91, +5624.89] |
| w05_e03 | +0.0264 [+0.0039, +0.0498] | n/a [-6063.35, +4709.33] |
| w05_e06 | +0.0352 [+0.0148, +0.0570] | n/a [-5534.99, +4972.09] |
| w07_e03 | +0.0186 [-0.0018, +0.0384] | n/a [-6228.00, +6665.51] |
| w07_e06 | +0.0313 [+0.0094, +0.0522] | n/a [-5460.80, +6785.42] |
| market_only | n/a [n/a, n/a] | n/a [n/a, n/a] |

## Intents by side

- w03_e03: BACK_away 16, LAY_away 71
- w03_e06: BACK_away 7, LAY_away 46
- w05_e03: BACK_away 24, BACK_home 1, LAY_away 48, LAY_home 8
- w05_e06: BACK_away 15, LAY_away 55, LAY_home 6
- w07_e03: BACK_away 22, BACK_home 6, LAY_away 42, LAY_home 9
- w07_e06: BACK_away 18, BACK_home 5, LAY_away 39, LAY_home 13
- market_only: none

## Skips (counted per cycle, summed over the run)

- Registry: {'settled': 80, 'simulated': 3652, 'too_early': 5764}
- Matches: {'no_market': 748}

## Guard refusals (S75, counted per cycle and per candidate or intent, summed over the run)

- {'crossed_book': 2, 'flip_cooldown': 94, 'inplay_without_state': 26436, 'no_band': 41, 'spread_too_wide': 34, 'trade_rate': 684}

## Per match

| match | teams | competition | w03_e03 fills / worst | w03_e06 fills / worst | w05_e03 fills / worst | w05_e06 fills / worst | w07_e03 fills / worst | w07_e06 fills / worst | market_only fills / worst |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| lb_36267578 | Biratnagar Kings v Janakpur Bolts | Nepal Premier League | 40 / 54,616 | 30 / 57,629 | 42 / 23,000 | 40 / 33,264 | 45 / 0 | 42 / 0 | - |
| lb_36267634 | Lumbini Lions v Pokhara Avengers | Nepal Premier League | 2 / 50,000 | - | 3 / 75,000 | 2 / 50,000 | 4 / 100,000 | 3 / 75,000 | - |

