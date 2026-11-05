# S97 market-dynamics model, 20261104

Model `models/market_dynamics/20261104.joblib` (version s97-1), scikit-learn 1.6.1. Target: the band market probability 10 minutes after a snapshot minus the probability at the snapshot. Features: market (band price, changes over 1 to 10 minutes, volatility, spreads, distance from the reference price, time since the last change) and the scoreboard where a live score exists, with a no-scoreboard flag.

## Sample, stated honestly

- Dataset `dataset.parquet`: 14 real matches, 4032 snapshots in play every 30 s (2013 with a live scoreboard from 6 matches, 2019 without).
- Matches with at least one labelled snapshot: 14. Below 10 matches every interval below is labelled 'too few matches' and is not evidence.
- Snapshots dropped, by reason: {'snapshots_label_window_gap': 770, 'snapshots_no_band': 1493, 'snapshots_no_fresh_price': 810}.

## Leave-one-match-out accuracy

- Mean absolute error of the 10-minute change: model 0.0670 against the zero baseline 0.0581 (ratio 1.153; below 1 means the model beats no change).
- Direction accuracy on moves larger than the half-spread: 0.493 over 3058 snapshots.

## Simulated CLV after the 60 s delay and the spread (match-clustered)

A simulated trade is taken when |predicted change| exceeds the threshold, at most every 2 minutes and 30 per match. The CLV is the realised move in the bet's direction from the fill (60 s) to 10 minutes after the decision, minus half the home spread at the fill. The placebo takes a seeded random side at the same times.

| threshold | trades | matches with trades | model CLV [95% CI] (matches / trades) | placebo CLV [95% CI] | excess over placebo [95% CI] |
|---|---:|---:|---|---|---|
| |change| > 0.01 | 387 | 14 | -0.0240 [-0.0372, -0.0097] (14 matches / 387 bets) | -0.0225 [-0.0316, -0.0127] (14 matches / 387 bets) | +0.0014 [-0.0103, +0.0142] (14 matches / 387 bets) |
| |change| > 0.02 | 380 | 14 | -0.0269 [-0.0397, -0.0136] (14 matches / 380 bets) | -0.0248 [-0.0394, -0.0120] (14 matches / 380 bets) | +0.0003 [-0.0116, +0.0127] (14 matches / 380 bets) |
| |change| > 0.03 | 324 | 14 | -0.0303 [-0.0445, -0.0161] (14 matches / 324 bets) | -0.0241 [-0.0318, -0.0163] (14 matches / 324 bets) | -0.0036 [-0.0166, +0.0102] (14 matches / 324 bets) |
| |change| > 0.05 | 164 | 13 | -0.0320 [-0.0516, -0.0145] (13 matches / 164 bets) | -0.0182 [-0.0283, -0.0060] (13 matches / 164 bets) | -0.0109 [-0.0398, +0.0149] (13 matches / 164 bets) |

Split by scoreboard at the primary threshold (0.02):

| segment | trades | matches with trades | model CLV [95% CI] (matches / trades) | placebo CLV [95% CI] | excess over placebo [95% CI] |
|---|---:|---:|---|---|---|
| with_scoreboard (2013 snapshots, 6 matches) | 156 | 6 | -0.0123 [-0.0270, +0.0078] (6 matches / 156 bets; too few matches) | -0.0030 [-0.0168, +0.0104] (6 matches / 156 bets; too few matches) | -0.0090 [-0.0269, +0.0108] (6 matches / 156 bets; too few matches) |
| no_scoreboard (2019 snapshots, 14 matches) | 257 | 12 | -0.0330 [-0.0462, -0.0214] (12 matches / 257 bets) | -0.0291 [-0.0332, -0.0247] (12 matches / 257 bets) | -0.0024 [-0.0137, +0.0073] (12 matches / 257 bets) |

## Activation

- Decision: activated. no active model: the first model is activated.

## Reading

- Positive excess over placebo on 10 or more matches, with the interval above zero, is the only evidence this model adds to the market. Anything less is not a result.
- The model is not walk-forward, uses only in-play snapshots, and its CLV is simulated from recorded prices, not from fills. The paper replay (`scripts/replay_paper.py`) is the test of the trading rules.

