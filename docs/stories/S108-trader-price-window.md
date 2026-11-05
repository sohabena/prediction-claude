# S108 — The trader reads prices only as far back as open bets still need them

**Pillar:** 3 · **Priority:** P0 · **Tier:** lead (after S107 part B, which edits the same file) · **Wave:** 24

## Evidence (2026-11-05)

Cycle time was 15 s until 2026-11-04 13:37 UTC, then about 58 s, then 140 to 170 s with stalls of 8 to 17 minutes
(06:30 to 08:00 UTC). `run_cycle` reads prices (`price_rows`) and sizes (`size_rows`) for every candidate and open
match from `since = min(now, decided_at of every open bet) - 20 min`. Sydney Thunder W v Perth Scorchers W
(lb_36271347) held 67 open bets, the oldest a pre-match bet decided 2026-11-04 20:42, about 12 h before the start, so
every cycle read 12+ hours of every match's prices. The monitor failed `paper_trading` (last cycle 653 s old).

## Acceptance criteria

1. `since` comes only from bets that still need old prices: pending bets (decided_at - lookback, for the 60 s fill),
   filled in-play bets whose CLV is not recorded (filled_at - lookback, the reference is 10 min after the fill),
   and filled pre-match bets whose CLV is not recorded and whose off has passed (off - lookback). A filled bet with
   CLV recorded, or a pre-match bet before its off, never moves `since` back. Default stays `now - 20 min`.
2. The decision cache still covers each candidate's staleness limits (60 s in play, 15 min pre-match): 20 min is
   enough.
3. Tests with a fake feed: an old filled bet with CLV recorded leaves the window at 20 min; a pending bet and a
   CLV-due bet extend it exactly as needed; fills and CLV unchanged on a replayed fixture.
4. After deploying: cycle time back near 15 s with the same open bets (log check), monitor `paper_trading` OK.
