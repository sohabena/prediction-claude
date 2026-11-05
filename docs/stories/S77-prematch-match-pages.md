# S77 — Pre-match match pages

**Pillar:** 1 · **Priority:** P0 · **Tier:** haiku · **Depends on:** S52, S56 · **Wave:** 19A
**Plan:** docs/architecture/next-phase-plan.md

## User story

As the pre-match model and the paper trader, I want the match page's full book (both sides' back and lay with sizes,
every market) from an hour before the start, because pre-match is where manual bets can act and the listing often
hides lays.

## Acceptance criteria

- The market-page worker opens for a real match from `PREMATCH_PAGE_LEAD_MIN` (default 60) minutes before
  `match_start_time`, not only when live. SRL keeps opening only when live (it has no pre-match listing).
- The worker cap (12) prefers live matches; pre-match pages fill remaining slots, earliest start first, and are closed
  first when a live match needs a slot.
- Pre-match rows land in `market_ticks` with the same per-record dedupe and seeding.
- Monitor: share of real matches that started in the window with pre-match match-page rows (warn < 90%).
- Dataset (S58) snapshots T-60m/T-10m/T-0 pick up match-page lays (they already merge sources).
- Tests: lead window, slot priority and pre-emption, SRL excluded pre-match.
