"""S33: CLV canonical formulas, settlement closing-price selection, portfolio CLV, and the
read-only audit. All database access is replaced by an in-memory fake session."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import audit_clv
from shared.metrics import bet_clv, clv_prob, portfolio_clv, selection_of, side_of
from shared.schemas import BettingAction, VirtualBet
from virtual_trading.settlement import SettlementEngine, select_closing_price

BASE = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _bet(action: BettingAction, odds: float, clv: float | None = None,
         closing: float | None = None) -> VirtualBet:
    return VirtualBet(
        id="b1", match_id="m1", placed_at=BASE, action=action, team="A",
        odds=odds, stake=10.0, clv=clv, closing_odds=closing,
    )


class _FakePortfolio:
    def __init__(self) -> None:
        self.closed: list[tuple[str, float]] = []

    def close_position(self, bet_id: str, pnl: float) -> None:
        self.closed.append((bet_id, pnl))


# --------------------------------------------------------------------------- formulas


class TestCanonicalClv:
    def test_back_beating_close_is_positive(self) -> None:
        """Bet BACK at 2.10, market closes at 1.90: positive CLV (beat the closing line)."""
        clv = clv_prob("BACK", 2.10, 1.90)
        assert clv > 0
        assert clv == pytest.approx(1 / 1.90 - 1 / 2.10)

    def test_back_worse_than_close_is_negative(self) -> None:
        assert clv_prob("BACK", 1.90, 2.10) < 0

    def test_lay_formula_is_side_aware(self) -> None:
        """LAY: 1/entry - 1/close, so laying at 1.80 into a 2.00 close is positive."""
        assert clv_prob("LAY", 1.80, 2.00) == pytest.approx(1 / 1.80 - 1 / 2.00)
        assert clv_prob("LAY", 1.80, 2.00) > 0
        assert clv_prob("LAY", 2.00, 1.80) < 0

    def test_invalid_or_missing_odds_give_zero(self) -> None:
        assert clv_prob("BACK", 1.0, 2.0) == 0.0
        assert clv_prob("BACK", 2.0, None) == 0.0

    def test_settlement_compute_clv_delegates_to_canonical(self) -> None:
        for side in ("BACK", "LAY"):
            assert SettlementEngine.compute_clv(2.1, 1.9, side=side) == clv_prob(side, 2.1, 1.9)

    def test_selection_and_side_from_action(self) -> None:
        assert side_of(BettingAction.LAY_AWAY_SM) == "LAY"
        assert side_of(BettingAction.BACK_HOME_LG) == "BACK"
        assert selection_of(BettingAction.LAY_AWAY_SM) == "AWAY"
        assert selection_of(BettingAction.BACK_HOME_SM) == "HOME"
        assert selection_of(BettingAction.HOLD) == ""


# --------------------------------------------------------------------------- settlement


class TestSelectClosingPrice:
    def _ticks(self) -> list[dict]:
        return [
            {"time": BASE, "is_live": False, "back_home": 2.0, "lay_home": 2.02,
             "back_away": 1.9, "lay_away": 1.92},
            {"time": BASE + timedelta(minutes=10), "is_live": False, "back_home": 1.8,
             "lay_home": 1.82, "back_away": 2.1, "lay_away": 2.12},
            # In-play tick after suspension must be ignored
            {"time": BASE + timedelta(minutes=30), "is_live": True, "back_home": 1.2,
             "lay_home": 1.21, "back_away": 3.5, "lay_away": 3.6},
        ]

    def test_last_pre_suspension_same_side_price(self) -> None:
        ticks = self._ticks()
        assert select_closing_price(ticks, "BACK", "HOME") == 1.8
        assert select_closing_price(ticks, "LAY", "HOME") == 1.82
        assert select_closing_price(ticks, "BACK", "AWAY") == 2.1

    def test_no_valid_tick_returns_none(self) -> None:
        ticks = [{"time": BASE, "is_live": True, "back_home": 2.0, "lay_home": 2.0}]
        assert select_closing_price(ticks, "BACK", "HOME") is None
        assert select_closing_price([], "BACK", "HOME") is None
        assert select_closing_price(self._ticks(), "BACK", "") is None

    def test_settle_with_ticks_stores_closing_and_clv(self) -> None:
        engine = SettlementEngine(_FakePortfolio())
        bet = _bet(BettingAction.BACK_HOME_SM, odds=2.1)
        engine.settle_on_match_result(bet, "A", closing_ticks=self._ticks())
        assert bet.closing_odds == 1.8
        assert bet.clv == pytest.approx(clv_prob("BACK", 2.1, 1.8))
        assert bet.clv > 0

    def test_settle_lay_uses_lay_price(self) -> None:
        engine = SettlementEngine(_FakePortfolio())
        bet = _bet(BettingAction.LAY_HOME_SM, odds=1.7)
        engine.settle_on_match_result(bet, "B", closing_ticks=self._ticks())
        assert bet.closing_odds == 1.82
        assert bet.clv == pytest.approx(clv_prob("LAY", 1.7, 1.82))

    def test_explicit_closing_odds_take_precedence(self) -> None:
        engine = SettlementEngine(_FakePortfolio())
        bet = _bet(BettingAction.BACK_HOME_SM, odds=2.1)
        engine.settle_on_match_result(bet, "A", closing_odds=1.95, closing_ticks=self._ticks())
        assert bet.closing_odds == 1.95

    def test_no_closing_price_leaves_clv_unset(self) -> None:
        engine = SettlementEngine(_FakePortfolio())
        bet = _bet(BettingAction.BACK_HOME_SM, odds=2.1)
        engine.settle_on_match_result(bet, "A")
        assert bet.clv is None and bet.closing_odds is None


# --------------------------------------------------------------------------- portfolio CLV


class TestPortfolioClv:
    def test_beating_close_gives_positive_mean_and_rate(self) -> None:
        bets = [_bet(BettingAction.BACK_HOME_SM, 2.1, clv=0.02) for _ in range(6)]
        summary = portfolio_clv(bets)
        assert summary["n"] == 6
        assert summary["mean_clv"] == pytest.approx(0.02)
        assert summary["median_clv"] == pytest.approx(0.02)
        assert summary["positive_clv_rate"] == 1.0
        assert summary["ci95_lower"] == pytest.approx(0.02)
        assert summary["ci95_upper"] == pytest.approx(0.02)

    def test_positive_rate_and_median_on_mixed_values(self) -> None:
        values = [0.05, -0.01, 0.03, -0.02, 0.0]
        bets = [{"clv": v} for v in values]
        summary = portfolio_clv(bets)
        assert summary["positive_clv_rate"] == pytest.approx(2 / 5)  # zero is not positive
        assert summary["median_clv"] == pytest.approx(0.0)
        assert summary["mean_clv"] == pytest.approx(sum(values) / 5)
        assert summary["ci95_lower"] <= summary["mean_clv"] <= summary["ci95_upper"]

    def test_bootstrap_ci_is_reproducible_and_excludes_zero_for_clear_edge(self) -> None:
        bets = [{"clv": 0.03 + 0.001 * (i % 3)} for i in range(40)]
        first = portfolio_clv(bets, n_bootstrap=2000)
        second = portfolio_clv(bets, n_bootstrap=2000)
        assert first == second
        assert first["ci95_excludes_zero"] is True

    def test_empty_and_small_samples_are_nan_not_zero(self) -> None:
        empty = portfolio_clv([])
        assert empty["n"] == 0 and math.isnan(empty["mean_clv"])
        assert math.isnan(empty["ci95_lower"])
        small = portfolio_clv([{"clv": 0.1}, {"clv": 0.2}])
        assert small["n"] == 2 and small["mean_clv"] == pytest.approx(0.15)
        assert math.isnan(small["ci95_lower"]) and math.isnan(small["ci95_upper"])

    def test_bets_without_clv_are_skipped(self) -> None:
        bets = [_bet(BettingAction.BACK_HOME_SM, 2.1, clv=0.01), _bet(BettingAction.BACK_HOME_SM, 2.1)]
        assert portfolio_clv(bets)["n"] == 1

    def test_stored_clv_missing_is_recomputed_from_closing(self) -> None:
        bet = _bet(BettingAction.BACK_HOME_SM, 2.1, closing=1.9)
        assert bet_clv(bet) == pytest.approx(clv_prob("BACK", 2.1, 1.9))
        assert portfolio_clv([bet])["n"] == 1

    def test_field_selects_metric(self) -> None:
        rows = [{"clv": 0.01, "clv_60s": -0.02}] * 5
        assert portfolio_clv(rows, field="clv")["mean_clv"] == pytest.approx(0.01)
        assert portfolio_clv(rows, field="clv_60s")["mean_clv"] == pytest.approx(-0.02)

    def test_no_sharpe_key(self) -> None:
        assert not any("sharpe" in k for k in portfolio_clv([{"clv": 0.1}] * 6))


# --------------------------------------------------------------------------- auditor helpers


def _tick(t: datetime, **prices: float | None) -> dict:
    base = {"time": t, "match_id": "m1", "competition": "IPL 2026", "team_home": "Mumbai Indians",
            "team_away": "Chennai Super Kings", "back_home": None, "lay_home": None,
            "back_away": None, "lay_away": None, "is_live": False, "source": "lotusbook"}
    base.update(prices)
    return base


def _bet_row(action: str, odds: float, closing: float | None = None, match_id: str = "m1",
             agent: str = "v1", source: str = "lotusbook", clv: float | None = None) -> dict:
    return {"bet_id": f"{action}-{odds}", "match_id": match_id, "placed_at": BASE, "action": action,
            "team": "x", "odds": odds, "stake": 10.0, "closing_odds": closing, "clv": clv,
            "agent_version": agent, "closing_source": source,
            "team_home": "Mumbai Indians", "team_away": "Chennai Super Kings"}


class TestAuditHelpers:
    def test_price_after_uses_first_valid_tick_at_or_after_60s(self) -> None:
        ticks = [
            _tick(BASE + timedelta(seconds=30), back_home=3.0),
            _tick(BASE + timedelta(seconds=60), back_home=1.9),   # exactly 60 s
            _tick(BASE + timedelta(seconds=90), back_home=1.5),
        ]
        assert audit_clv.price_after(ticks, BASE, "BACK", "HOME") == 1.9

    def test_price_after_skips_invalid_and_missing(self) -> None:
        ticks = [
            _tick(BASE + timedelta(seconds=61), back_home=None),
            _tick(BASE + timedelta(seconds=62), back_home=1.0),
            _tick(BASE + timedelta(seconds=63), back_home=2.4),
        ]
        assert audit_clv.price_after(ticks, BASE, "BACK", "HOME") == 2.4
        assert audit_clv.price_after(ticks[:2], BASE, "BACK", "HOME") is None

    def test_odds_ratio_pct_signs(self) -> None:
        assert audit_clv.odds_ratio_pct("BACK", 2.1, 1.9) > 0
        assert audit_clv.odds_ratio_pct("LAY", 1.7, 1.9) > 0
        assert audit_clv.odds_ratio_pct("LAY", 2.0, 1.8) < 0
        assert audit_clv.odds_ratio_pct("BACK", 2.0, None) is None

    def test_build_rows_side_aware_60s_and_exclusions(self) -> None:
        ticks = {
            "m1": [
                _tick(BASE - timedelta(minutes=1), back_home=2.0, lay_away=2.0, back_away=2.0),
                _tick(BASE + timedelta(seconds=60), back_home=1.9, lay_away=2.5, back_away=2.2),
            ],
            "m2": [_tick(BASE, competition="SRL T10 Simulated League", match_id="m2")],
        }
        bets = [
            _bet_row("BACK_HOME_SM", 2.0, closing=1.9),                # beats 60 s and close
            _bet_row("LAY_AWAY_SM", 2.0, closing=1.8),                 # LAY vs 60 s price 2.5
            _bet_row("BACK_AWAY_LG", 2.0, match_id="m2", closing=1.9),  # simulated -> excluded
            _bet_row("HOLD", 2.0, closing=1.9),                        # not a bet -> excluded
        ]
        rows, counts = audit_clv.build_audit_rows(bets, ticks)
        assert counts == {"bets_read": 4, "excluded_simulated": 1, "excluded_unknown_action": 1}
        assert len(rows) == 2
        back, lay = rows
        assert back["clv_60s"] == pytest.approx(1 / 1.9 - 1 / 2.0) and back["clv_60s"] > 0
        assert lay["side"] == "LAY" and lay["selection"] == "AWAY"
        assert lay["clv_60s"] == pytest.approx(1 / 2.0 - 1 / 2.5) and lay["clv_60s"] > 0
        assert lay["clv"] == pytest.approx(clv_prob("LAY", 2.0, 1.8))
        assert lay["clv_odds_pct"] == pytest.approx((1.8 / 2.0 - 1) * 100)

    def test_no_60s_price_leaves_clv_60s_none(self) -> None:
        rows, _ = audit_clv.build_audit_rows([_bet_row("BACK_HOME_SM", 2.0, closing=1.9)],
                                             {"m1": [_tick(BASE, back_home=2.0)]})
        assert rows[0]["clv_60s"] is None and rows[0]["price_60s"] is None

    def test_summarise_breaks_down_by_version_and_source(self) -> None:
        bets = [
            _bet_row("BACK_HOME_SM", 2.0, closing=1.9, agent="v1", source="lotusbook"),
            _bet_row("BACK_HOME_SM", 2.0, closing=1.9, agent="v2", source="betfair"),
            _bet_row("BACK_HOME_SM", 2.0, closing=1.9, agent="v2", source="betfair"),
        ]
        rows, _ = audit_clv.build_audit_rows(bets, {"m1": [_tick(BASE + timedelta(seconds=60),
                                                                  back_home=1.8)]})
        summary = audit_clv.summarise(rows)
        assert summary["overall"]["n_bets"] == 3
        assert summary["by_agent_version"]["v2"]["n_bets"] == 2
        assert summary["by_closing_source"]["betfair"]["n_bets"] == 2
        assert summary["overall"]["closing"]["positive_clv_rate"] == 1.0


# --------------------------------------------------------------------------- read-only run


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)


class _FakeSession:
    """Answers the audit's SELECTs from memory and records every statement."""

    def __init__(self, bets: list[dict], ticks: list[dict]) -> None:
        self.bets = bets
        self.ticks = ticks
        self.statements: list[tuple[str, object]] = []

    async def execute(self, stmt, params=None):  # noqa: ANN001
        sql = str(stmt)
        self.statements.append((sql, params))
        if "SET TRANSACTION READ ONLY" in sql:
            return _Result([])
        if "FROM virtual_bets" in sql:
            return _Result(self.bets)
        if "FROM odds_ticks" in sql:
            ids = set(params["match_ids"])
            return _Result([t for t in self.ticks if t["match_id"] in ids])
        raise AssertionError(f"unexpected statement: {sql}")


class TestRunAudit:
    def test_run_is_read_only_and_filters_sources(self) -> None:
        ticks = [dict(_tick(BASE + timedelta(seconds=60), back_home=1.9), match_id="m1")]
        session = _FakeSession([_bet_row("BACK_HOME_SM", 2.0, closing=1.9)], ticks)
        report = asyncio.run(audit_clv.run_audit(session, limit=5))

        sqls = [s for s, _ in session.statements]
        assert "READ ONLY" in sqls[0]
        for sql in sqls:
            upper = sql.upper()
            assert not any(verb in upper for verb in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP "))
        bets_sql = next(s for s in sqls if "FROM virtual_bets" in s)
        assert "holdout_eval:%" in bets_sql and "LIMIT :limit" in bets_sql
        ticks_sql = next(s for s in sqls if "FROM odds_ticks" in s)
        assert "source IN ('lotusbook', 'betfair')" in ticks_sql
        assert session.statements[1][1] == {"limit": 5}
        assert report["overall"]["n_bets"] == 1
        assert report["overall"]["post_60s"]["mean_clv"] == pytest.approx(1 / 1.9 - 1 / 2.0)

    def test_no_bets_means_no_tick_query(self) -> None:
        session = _FakeSession([], [])
        report = asyncio.run(audit_clv.run_audit(session, limit=10))
        assert all("FROM odds_ticks" not in s for s, _ in session.statements)
        assert report["overall"]["n_bets"] == 0
        assert math.isnan(report["overall"]["closing"]["mean_clv"])

    def test_format_report_shows_n_a_for_missing_stats(self) -> None:
        report = asyncio.run(audit_clv.run_audit(_FakeSession([], []), limit=10))
        text = audit_clv.format_report(report)
        assert "CLV audit (read-only)" in text
        assert "n/a" in text
        assert "by agent_version" in text and "by closing_source" in text

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(SystemExit):
            audit_clv.main(["--limit", "0"])

    def test_module_source_contains_no_write_statements(self) -> None:
        import pathlib

        src = pathlib.Path(audit_clv.__file__).read_text(encoding="utf-8").upper()
        for verb in ("INSERT INTO", "UPDATE VIRTUAL_BETS", "DELETE FROM", "DROP TABLE"):
            assert verb not in src
        assert "SET TRANSACTION READ ONLY" in src


def test_ten_minute_horizon_clv_for_in_play_bets():
    """In-play CLV is measured at a fixed horizon, because the end-of-match price restates the result."""
    from scripts.audit_clv import HORIZON_SECONDS, build_audit_rows

    placed = BASE
    ticks = [
        {"time": placed + timedelta(seconds=s), "is_live": True, "back_home": p, "lay_home": p + 0.02,
         "back_away": None, "lay_away": None, "competition": "IPL", "team_home": "A", "team_away": "B"}
        for s, p in [(0, 2.0), (60, 1.95), (HORIZON_SECONDS, 1.8), (HORIZON_SECONDS + 3000, 1.01)]
    ]
    bet = {"bet_id": "1", "match_id": "m", "placed_at": placed, "action": "BACK_HOME_SM", "team": "A",
           "odds": 2.0, "stake": 10.0, "closing_odds": 1.01, "clv": None, "agent_version": "signal:value:v1",
           "closing_source": "lotusbook_odds", "team_home": "A", "team_away": "B"}
    rows, _ = build_audit_rows([bet], {"m": ticks})
    assert rows[0]["price_10m"] == 1.8
    assert rows[0]["clv_10m"] == pytest.approx(1 / 1.8 - 1 / 2.0)
