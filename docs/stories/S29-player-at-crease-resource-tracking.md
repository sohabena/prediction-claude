# S29 — Player-at-crease impact and top-order resource tracking from Cricsheet

**Priority:** P1 · **Tier:** sonnet · **Depends on:** S24 · **Wave:** 11
**Conflicts with:** none (strictly touches feature extraction and player categorization)

## Story

As the strategy owner, I need the in-play feature pipeline to distinguish between top-order specialist batters and lower-order tailenders, so that the model understands that losing 3 wickets for 30 runs is vastly different when top-order specialists are still at the crease versus when the tail is exposed.

## Background

- Currently, the match state feature group contains only `wickets_normalized` (`wickets / 10.0`).
- This treats all 10 wickets as having identical value. In modern cricket, wickets 1–4 (top-order / anchor / power hitters) account for ~75% of total expected runs, while wickets 8–10 (bowling tailenders) account for <10%.
- A team chasing 36 off 18 balls with Heinrich Klaasen and David Miller at the crease has an estimated 70% win probability; the exact same chase with #10 and #11 tailenders has <5% win probability.
- Cricsheet provides complete delivery-level information with `batter`, `bowler`, `non_striker`, and batting orders in `info.players`.
- By establishing a player categorization dictionary (top-order batter, middle-order batter, all-rounder, bowler) from historical Cricsheet appearances, we can compute an exact **top-order batting resource index** at every tick.

### Mathematical Formulation

1. **Batting Order Position:** For each batter, their standard historical batting position $pos \in [1, 11]$.
2. **Top-Order Wickets In Hand:**
   $$\text{top\_order\_wickets\_rem} = \max(0, 5 - \text{top\_order\_wickets\_lost})$$
3. **Batter Impact Rating:**
   Historical T20/ODI strike-rate and boundary frequency factor relative to format average.
4. **Active Crease Resource Metric:**
   $$\text{crease\_resource\_score} = \frac{\text{impact}(\text{striker}) + \text{impact}(\text{non\_striker})}{2.0}$$

## Context Pack

- Cricsheet structure: `info.players` maps each team to their 11 players.
- Deliveries identify the exact active striker and non-striker.
- Output lookup table: `data/ratings/player_roles.json` (mapping `player_name -> {primary_role: "batter"|"all_rounder"|"bowler", career_sr, avg_pos}`).
- Features added to `features/extractors/match_stats_features.py`:
  - `top_order_wickets_rem`: normalized $[0.0, 1.0]$.
  - `crease_resource_score`: normalized $[0.0, 2.0]$.

## Owned Files

- New: `scripts/compute_player_roles.py`
- New: `shared/player_registry.py`
- `features/extractors/match_stats_features.py`
- New: `tests/unit/test_s29_player_resources.py`

## Tasks

- [ ] Implement `scripts/compute_player_roles.py`:
  - Scans Cricsheet deliveries and player appearances.
  - Computes each player's median batting position and career boundary percentage.
  - Classifies players into `top_order` (1–3), `middle_order` (4–6), `all_rounder` (7–8), `bowler` (9–11).
  - Exports `data/ratings/player_roles.json`.
- [ ] Implement `shared/player_registry.py`:
  - In-memory fast lookup for active player roles.
  - Fallback logic: if player is unlisted, infer from current batting position in the innings.
- [ ] Update `features/extractors/match_stats_features.py`:
  - Add `compute_crease_resource_features(context, active_batter, active_bowler)` returning `top_order_wickets_rem` and `crease_resource_score`.
- [ ] Implement `tests/unit/test_s29_player_resources.py`:
  - Assert that early wickets of top-order batters reduce resource score significantly.
  - Assert that tailenders at the crease return low resource scores.

## Verification

```bash
# Run unit tests
python -m pytest tests/unit/test_s29_player_resources.py -v

# Run batch player categorization (dry run)
python -m scripts.compute_player_roles --limit 100 --dry-run
```

