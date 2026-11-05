# S34 — Frontend UI transparency: Display Team Elo, Venue Priors, and Real-Time EV Breakdown

**Priority:** P2 · **Tier:** sonnet · **Depends on:** S26, S27 · **Wave:** 11
**Conflicts with:** none (strictly touches frontend components and backend API response serializers)

## Story

As the trader viewing the PHOENIX dashboard, I need the Match Management and Virtual Trading pages to display pre-match Team Elo ratings, venue par run-rates, and the exact Expected Value (EV) and win probability behind every placed bet, so that the decision-making process of the agents is completely transparent and auditable in real time.

## Background

- Currently, the Match Management UI ([`frontend/app/matches/page.tsx`](file:///c:/Users/sohai/Downloads/prediction-claude/frontend/app/matches/page.tsx)) displays live scores and back/lay prices, but does not show:
  1. The pre-match Elo ratings of both teams (e.g. India 1780 vs Netherlands 1410).
  2. The ground's historical par score/run-rate.
- Similarly, the Virtual Trading table displays `Action`, `Odds`, `Stake`, and `P&L`, but does not show:
  1. $P_{\text{win}}$ calculated by the `FairValueModel`.
  2. The edge percentage ($\text{EV} = P_{\text{win}} \cdot \text{odds} - 1.0$) that justified placing the bet.
- Adding these metrics gives complete transparency into model reasoning and builds operator confidence.

## Context Pack

- Backend Match Router: `backend/routers/match_router.py`
  - Injects `team_home_elo`, `team_away_elo`, `venue_par_rr` into `LiveMatchCard` response.
- Backend Virtual Trading Router: `backend/routers/trading_router.py`
  - Injects `win_prob` and `calculated_edge_pct` into `RecentBetRow` response.
- Frontend Match Cards: `frontend/app/matches/page.tsx`
- Frontend Trading Table: `frontend/app/trading/page.tsx`

## Owned Files

- `backend/routers/match_router.py`
- `backend/routers/trading_router.py`
- `frontend/app/matches/page.tsx`
- `frontend/app/trading/page.tsx`
- New: `tests/unit/test_s34_ui_endpoints.py`

## Tasks

- [ ] In `backend/routers/match_router.py`:
  - When returning active live matches, look up latest Elo ratings from `team_elo_ratings` table and venue par from `venue_priors`.
  - Include `home_elo`, `away_elo`, and `venue_par_score` in the JSON response.
- [ ] In `backend/routers/trading_router.py`:
  - Include `win_prob` and `ev_edge` on serialized `VirtualBet` rows.
- [ ] In `frontend/app/matches/page.tsx`:
  - Render compact Elo badges next to team names (e.g., `India (1745) vs Netherlands (1420)`).
  - Add a subtitle pill showing venue par (e.g., `Par: 168 rpo (8.4)`).
- [ ] In `frontend/app/trading/page.tsx`:
  - Add an "Edge / EV" column in the Recent Bets table (e.g. `+4.2% EV (P: 58%)`).
- [ ] Verify TypeScript compiles with `npx tsc --noEmit`.

## Verification

```bash
# Verify backend response serializers
python -m pytest tests/unit/test_s34_ui_endpoints.py -v

# Verify Next.js compilation
cd frontend && npx tsc --noEmit
```

