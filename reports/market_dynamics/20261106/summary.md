# S97 market-dynamics model, 20261106

Model `models/market_dynamics/20261106.joblib` (version s97-1), scikit-learn 1.6.1. Target: the band market probability 10 minutes after a snapshot minus the probability at the snapshot. Features: market (band price, changes over 1 to 10 minutes, volatility, spreads, distance from the reference price, time since the last change) and the scoreboard where a live score exists, with a no-scoreboard flag.

## Sample, stated honestly

- Dataset `dataset.csv`: 6 real matches, 1392 snapshots in play every 30 s (370 with a live scoreboard from 2 matches, 1022 without).
- Matches with at least one labelled snapshot: 6. Below 10 matches every interval below is labelled 'too few matches' and is not evidence.
- Snapshots dropped, by reason: {'snapshots_label_window_gap': 421, 'snapshots_no_band': 868, 'snapshots_no_fresh_price': 2633}.

## Leave-one-match-out accuracy

- Mean absolute error of the 10-minute change: model 0.0812 against the zero baseline 0.0677 (ratio 1.200; below 1 means the model beats no change).
- Direction accuracy on moves larger than the half-spread: 0.582 over 984 snapshots.

## Simulated CLV after the 60 s delay and the spread (match-clustered)

A simulated trade is taken when |predicted change| exceeds the threshold, at most every 2 minutes and 30 per match. The CLV is the realised move in the bet's direction from the fill (60 s) to 10 minutes after the decision, minus half the home spread at the fill. The placebo takes a seeded random side at the same times.

| threshold | trades | matches with trades | model CLV [95% CI] (matches / trades) | placebo CLV [95% CI] | excess over placebo [95% CI] |
|---|---:|---:|---|---|---|
| |change| > 0.01 | 155 | 6 | -0.0142 [-0.0238, -0.0018] (6 matches / 155 bets; too few matches) | -0.0229 [-0.0262, -0.0171] (6 matches / 155 bets; too few matches) | +0.0157 [+0.0021, +0.0344] (6 matches / 155 bets; too few matches) |
| |change| > 0.02 | 155 | 6 | -0.0146 [-0.0228, -0.0024] (6 matches / 155 bets; too few matches) | -0.0213 [-0.0257, -0.0151] (6 matches / 155 bets; too few matches) | +0.0140 [+0.0004, +0.0341] (6 matches / 155 bets; too few matches) |
| |change| > 0.03 | 146 | 6 | -0.0129 [-0.0227, -0.0003] (6 matches / 146 bets; too few matches) | -0.0126 [-0.0207, -0.0007] (6 matches / 146 bets; too few matches) | -0.0031 [-0.0151, +0.0095] (6 matches / 146 bets; too few matches) |
| |change| > 0.05 | 101 | 6 | -0.0082 [-0.0271, +0.0160] (6 matches / 101 bets; too few matches) | -0.0279 [-0.0516, -0.0158] (6 matches / 101 bets; too few matches) | +0.0552 [+0.0047, +0.1399] (6 matches / 101 bets; too few matches) |

Split by scoreboard at the primary threshold (0.02):

| segment | trades | matches with trades | model CLV [95% CI] (matches / trades) | placebo CLV [95% CI] | excess over placebo [95% CI] |
|---|---:|---:|---|---|---|
| with_scoreboard (370 snapshots, 2 matches) | 35 | 2 | +0.0004 [-0.0117, +0.0735] (2 matches / 35 bets; too few matches) | -0.0191 [-0.0246, +0.0140] (2 matches / 35 bets; too few matches) | +0.0361 [+0.0129, +0.0594] (2 matches / 35 bets; too few matches) |
| no_scoreboard (1022 snapshots, 5 matches) | 124 | 5 | -0.0190 [-0.0270, -0.0123] (5 matches / 124 bets; too few matches) | -0.0343 [-0.0373, -0.0286] (5 matches / 124 bets; too few matches) | +0.0083 [-0.0085, +0.0219] (5 matches / 124 bets; too few matches) |

## Activation

- Decision: not activated. --no-activate: current.json not changed.
- Active model before this run: `models/market_dynamics/20261104.joblib`.
- Held-out comparison on 6 matches the active model did not train on: candidate -0.014615604744391096, active -0.00794435463599705 (simulated CLV, primary threshold).

## Reading

- Positive excess over placebo on 10 or more matches, with the interval above zero, is the only evidence this model adds to the market. Anything less is not a result.
- The model is not walk-forward, uses only in-play snapshots, and its CLV is simulated from recorded prices, not from fills. The paper replay (`scripts/replay_paper.py`) is the test of the trading rules.

