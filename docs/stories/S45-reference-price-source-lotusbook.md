# S45 — CLV and trust measured against LotusBook match odds (Betfair unavailable)

**Priority:** P0 · **Tier:** haiku · **Depends on:** S42, S44 committed · **Wave:** 15
**Conflicts with:** none in wave 15 (S46 and S48 own other files)

## Story

As the person betting by hand in a country where Betfair is blocked, I need signal trust and the kill switch to be
measured against a price I can actually see, LotusBook's exchange-mirror match odds. Otherwise every signal stays
"unproven" forever.

## Background (verified at `7d029ca`)

- `strategy/trust.py` only counts `odds_ticks.source = 'betfair'`:
  - `BETFAIR_SOURCE = "betfair"` L38;
  - `REASON_NO_BETFAIR = "insufficient Betfair evidence"` L37;
  - `betfair_clv(bet, ticks)` L92 and `annotate_betfair(...)` L109 (pre-off last price `last_pre_off_price` L63,
    in-play `price_at_horizon` L74 at +600 s).
- `rl/lifecycle/graduation_handler.py:43 SIGNAL_TICKS_SQL` filters `source = 'betfair'`; `load_signal_evidence` L169,
  `check_signal_version` L194.
- `virtual_trading/kill_switch.py` imports `annotate_betfair` (L31) and reads `betfair_clv` (L129-151) for the rolling
  CLV rule.
- `scripts/audit_clv.py` `--source` defaults to `betfair` (L278); `SOURCES` L68-72.
- `docs/architecture/profit-architecture.md` §7: the reference source is LotusBook match odds, flagged as a mirror.

## Owned files

- `strategy/trust.py`
- `rl/lifecycle/graduation_handler.py` (`SIGNAL_TICKS_SQL` and its callers only)
- `virtual_trading/kill_switch.py` (the CLV source only)
- `scripts/audit_clv.py` (default `--source` only)
- `shared/constants.py` (new `CLV_REFERENCE_SOURCE` only)
- New: `tests/unit/test_s45_reference_source.py`

## Tasks

1. `shared/constants.py`: `CLV_REFERENCE_SOURCE = os.getenv("CLV_REFERENCE_SOURCE", "lotusbook")` (allowed:
   `lotusbook`, `betfair`). Comment that LotusBook match odds are an exchange mirror and its lag is unmeasured.
2. `strategy/trust.py`: rename to source-neutral names, keeping the old names as aliases for imports:
   - `reference_clv(bet, ticks, source=CLV_REFERENCE_SOURCE)`;
   - `annotate_reference(...)`, writing a `reference_clv` field (also written as `betfair_clv` only when the source is
     `betfair`);
   - `REASON_NO_REFERENCE = "insufficient reference-price evidence"`.
   Ticks are filtered by the configured source. The registry entry and the trust reason say which source was used.
3. `SIGNAL_TICKS_SQL`: filter `source = :source`, with the param from `CLV_REFERENCE_SOURCE`.
4. Kill switch rolling CLV: use `annotate_reference` / `reference_clv`.
5. `audit_clv.py`: the CLI default `--source` = `CLV_REFERENCE_SOURCE`.
6. **Self-reference guard:** a paper bet is filled at a LotusBook price, and its CLV is the same-side LotusBook price
   600 s later (in-play) or at the off (pre-match). The reference tick must be **strictly after** the fill time. Assert
   this in a test.

## Success criteria

- [ ] With the default config, a version whose LotusBook CLV meets every criterion becomes "trusted" (fake data), and
      the reason names `lotusbook`.
- [ ] With `CLV_REFERENCE_SOURCE=betfair` and no Betfair ticks, it stays unproven with the reference-evidence reason.
- [ ] The rolling-CLV kill uses the configured source (test).
- [ ] The reference tick is strictly later than the fill (test).
- [ ] Old names (`betfair_clv`, `annotate_betfair`) still import. Baseline suite passes (607 at `7d029ca`).

## Verification

```bash
python -m pytest tests/unit/test_s45_reference_source.py -q
python -m pytest tests/unit tests/rl -q
```

## Out of scope

- Measuring the mirror's lag (impossible without Betfair access).
