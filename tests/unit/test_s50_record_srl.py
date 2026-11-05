"""S50: record every live LotusBook match, SRL included, tagged as simulated.

Simulated events are recorded (odds ticks, match context, market page) but never approved, never
published to Redis and never auto-approved. Readers exclude them unless they opt in.

All database and Redis access uses fakes; no DB, Redis or browser is touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import rl.data_loader as data_loader_mod
import scraper.manager as manager_mod
from backend.models.match_status import MatchTrainingStatus
from backend.models.odds import OddsTick
from backend.repositories import training_repository as training_mod
from scraper.manager import ScraperManager
from scraper.market_pages import eligibility
from scripts import audit_clv
from scripts import export_recorded_corpus as exporter
from shared.constants import CHANNEL_MATCH_CONTEXT, CHANNEL_MATCH_EVENTS
from shared.schemas import OddsEvent
from strategy.trust import trust_metrics
from tests.unit.test_s37_recording import (  # noqa: F401  (manager_env is a fixture)
    FakeDetector,
    FakeResult,
    FakeSession,
    fake_get_session,
    live_event,
    manager_env,
)

UTC = timezone.utc
SRL_COMPETITION = "T10 SRL League"


def _srl(match_id: str = "srl1", home: str = "Sydney Thunder SRL", away: str = "Hyderabad Kingsmen Srl"):
    return live_event(match_id=match_id, team_home=home, team_away=away, competition=SRL_COMPETITION)


def _ticks(session) -> list[OddsTick]:
    return [o for o in session.added if isinstance(o, OddsTick)]


def _registry(session) -> list[MatchTrainingStatus]:
    return [o for o in session.added if isinstance(o, MatchTrainingStatus)]


def _manager(monkeypatch, manager_env, *, auto_approve: bool = False) -> ScraperManager:
    """A real ScraperManager (real registry upsert) with the fake detector and fake Redis/DB."""
    monkeypatch.delenv("SCRAPER_RECORD_ALL", raising=False)
    mgr = ScraperManager()
    mgr._result_detector = FakeDetector()
    mgr._last_approved_refresh = datetime.now(UTC)  # skip the DB reload of approvals
    if auto_approve:
        config = {"config:scraper:auto_approve": "true"}
        manager_env.redis.client.get = AsyncMock(side_effect=lambda key: config.get(key))
    return mgr


# --------------------------------------------------------------------------- recording and publishing
async def test_srl_event_is_stored(monkeypatch, manager_env):
    mgr = _manager(monkeypatch, manager_env)
    await mgr._on_events([_srl()])

    ticks = _ticks(manager_env.session)
    assert len(ticks) == 1
    assert ticks[0].match_id == "srl1"
    assert mgr._quality_stats["recorded_simulated"] == 1
    assert mgr._quality_stats["stored"] == 1


async def test_srl_event_is_never_published(monkeypatch, manager_env):
    mgr = _manager(monkeypatch, manager_env, auto_approve=True)
    mgr._scrape_approved_ids.add("srl1")  # even a stale approval must not publish it
    await mgr._on_events([_srl()])

    assert _ticks(manager_env.session), "the SRL tick is still recorded"
    channels = {channel for channel, _ in manager_env.redis.published}
    assert CHANNEL_MATCH_EVENTS not in channels
    assert CHANNEL_MATCH_CONTEXT not in channels
    assert manager_env.redis.published == []


async def test_srl_match_is_not_in_active_matches_list(monkeypatch, manager_env):
    """The active-matches list is published to Redis (KEY_ACTIVE_MATCHES), so SRL stays out of it."""
    mgr = _manager(monkeypatch, manager_env)
    await mgr._on_events([_srl(), live_event(match_id="real1")])
    assert "srl1" not in mgr._active_matches
    assert "real1" in mgr._active_matches


async def test_srl_is_recorded_even_when_the_intl_filter_is_on(monkeypatch, manager_env):
    """qualifies() rejects SRL when the filter is on; the simulated path must not depend on it."""
    mgr = _manager(monkeypatch, manager_env)
    assert mgr._match_filter.enabled is True
    await mgr._on_events([_srl()])
    assert len(_ticks(manager_env.session)) == 1


async def test_srl_event_is_result_detected(monkeypatch, manager_env):
    """SRL results are recorded too (the detector itself never publishes them; see the S54 tests)."""
    mgr = _manager(monkeypatch, manager_env)
    seen: list[str] = []

    class SpyDetector(FakeDetector):
        def update_tick(self, **kwargs):
            seen.append(kwargs["match_id"])

    mgr._result_detector = SpyDetector()
    await mgr._on_events([_srl(), live_event(match_id="real1")])
    assert "srl1" in seen
    assert "real1" in seen


# --------------------------------------------------------------------------- approval
async def test_srl_is_never_approved_even_with_auto_approve_on(monkeypatch, manager_env):
    mgr = _manager(monkeypatch, manager_env, auto_approve=True)
    await mgr._on_events([_srl()])

    assert "srl1" not in mgr._scrape_approved_ids
    (record,) = _registry(manager_env.session)
    assert record.scrape_status == "simulated"
    assert record.auto_approved is False
    assert record.training_status == "pending"


async def test_srl_stays_out_of_approved_cache_when_a_row_says_approved(monkeypatch, manager_env):
    """A stored scrape_approved row (e.g. set by hand in the UI) is overridden for a simulated match."""
    mgr = _manager(monkeypatch, manager_env)
    record = SimpleNamespace(match_start_time=None, competition="", scrape_status="scrape_approved", auto_approved=False,
                             training_status="pending", is_simulated=False)

    class ExistingSession(FakeSession):
        async def execute(self, stmt, params=None):
            self.statements.append((str(stmt), params))
            return SimpleNamespace(scalar_one_or_none=lambda: record,
                                   fetchall=lambda: [], fetchone=lambda: None)

    session = ExistingSession()
    monkeypatch.setattr(manager_mod, "get_session", fake_get_session(session))
    mgr._scrape_approved_ids.add("srl1")
    await mgr._upsert_training_status(_srl(), auto_approve_enabled=True)

    assert record.scrape_status == "simulated"
    assert record.is_simulated is True
    assert "srl1" not in mgr._scrape_approved_ids


# --------------------------------------------------------------------------- registry row
async def test_registry_row_is_flagged_simulated(monkeypatch, manager_env):
    mgr = _manager(monkeypatch, manager_env, auto_approve=True)
    await mgr._upsert_training_status(_srl(), auto_approve_enabled=True)

    (record,) = _registry(manager_env.session)
    assert record.is_simulated is True
    assert record.scrape_status == "simulated"
    assert record.training_status == "pending"


async def test_registry_row_for_mixed_case_srl_name(monkeypatch, manager_env):
    mgr = _manager(monkeypatch, manager_env)
    await mgr._upsert_training_status(_srl(home="Hyderabad Kingsmen Srl", away="Rawalpindiz"),
                                      auto_approve_enabled=False)
    (record,) = _registry(manager_env.session)
    assert record.is_simulated is True
    assert record.scrape_status == "simulated"


async def test_real_match_registry_row_is_unchanged(monkeypatch, manager_env):
    mgr = _manager(monkeypatch, manager_env, auto_approve=True)
    real = live_event(match_id="real1")  # India v Australia, ICC
    await mgr._upsert_training_status(real, auto_approve_enabled=True)

    (record,) = _registry(manager_env.session)
    assert record.is_simulated is False
    assert record.scrape_status == "scrape_approved"
    assert record.auto_approved is True
    assert record.training_status == "approved"
    assert "real1" in mgr._scrape_approved_ids


async def test_real_match_is_still_published_when_approved(monkeypatch, manager_env):
    mgr = _manager(monkeypatch, manager_env)
    mgr._scrape_approved_ids.add("real1")
    await mgr._on_events([live_event(match_id="real1")])
    assert any(channel == CHANNEL_MATCH_EVENTS for channel, _ in manager_env.redis.published)


# --------------------------------------------------------------------------- model and migration
def test_model_has_is_simulated_column_defaulting_to_false():
    column = MatchTrainingStatus.__table__.c.is_simulated
    assert column.nullable is False
    assert column.default.arg is False


def test_db_init_adds_column_and_backfills_by_name():
    from backend.db_init import MIGRATION_STATEMENTS

    column = [s for s in MIGRATION_STATEMENTS if "column_name='is_simulated'" in s]
    assert column and "BOOLEAN NOT NULL DEFAULT false" in column[0]
    backfill = [s for s in MIGRATION_STATEMENTS if s.lstrip().startswith("UPDATE match_training_status")]
    assert backfill and "SET is_simulated = true" in backfill[0]
    for token in ("srl", "simulated", "virtual", "e-?cricket"):
        assert token in backfill[0]


def test_alembic_004_is_chained_after_003():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "alembic" / "versions" / "004_match_is_simulated.py").read_text(encoding="utf-8")
    assert 'revision: str = "004_match_is_simulated"' in text
    assert 'down_revision: Union[str, None] = "003_market_ticks"' in text
    assert "ADD COLUMN IF NOT EXISTS is_simulated BOOLEAN NOT NULL DEFAULT false" in text


# --------------------------------------------------------------------------- market page eligibility
def test_live_srl_match_is_eligible_for_a_market_page():
    assert eligibility(is_live=True, team_home="Sydney Thunder SRL", team_away="Hyderabad Kingsmen Srl",
                       competition="T10 SRL League") == (True, "ok")


def test_not_live_srl_match_is_still_not_eligible():
    assert eligibility(is_live=False, team_home="Sydney Thunder SRL", team_away="Perth Scorchers SRL") == (
        False, "not_live")


# --------------------------------------------------------------------------- readers exclude SRL by default
class _Capture:
    """Fake session that records the SQL text and bind parameters of each query."""

    def __init__(self, rows=None):
        self.calls: list[tuple[str, dict]] = []
        self._rows = rows or []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return FakeResult(self._rows)


def _capture_session(monkeypatch, module, rows=None) -> _Capture:
    session = _Capture(rows)
    monkeypatch.setattr(module, "get_session", fake_get_session(session))
    return session


async def test_data_loader_excludes_simulated_by_default(monkeypatch):
    session = _capture_session(monkeypatch, data_loader_mod)
    loader = data_loader_mod.MatchDataLoader()
    assert await loader._get_qualifying_match_ids(min_ticks=10, days_back=30) == []
    sql, params = session.calls[0]
    assert "mts.is_simulated" in sql
    assert params["include_simulated"] is False


async def test_data_loader_opt_in_includes_simulated(monkeypatch):
    session = _capture_session(monkeypatch, data_loader_mod)
    loader = data_loader_mod.MatchDataLoader()
    await loader._get_qualifying_match_ids(min_ticks=10, days_back=30, include_simulated=True)
    assert session.calls[0][1]["include_simulated"] is True


async def test_training_repository_excludes_simulated_by_default(monkeypatch):
    session = _capture_session(monkeypatch, training_mod)
    await training_mod.TrainingRepository.get_recent_match_ticks(5)
    sql, params = session.calls[0]
    assert "mts.is_simulated" in sql
    assert params["include_simulated"] is False

    await training_mod.TrainingRepository.get_recent_match_ticks(5, include_simulated=True)
    assert session.calls[1][1]["include_simulated"] is True


def test_exporter_drops_srl_rows_by_default_and_keeps_them_on_opt_in():
    base = {"source": exporter.RECORDED_ODDS_SOURCE, "competition": "ICC", "team_home": "India",
            "team_away": "Australia"}
    srl = dict(base, team_home="Sydney Thunder SRL", team_away="Perth Scorchers SRL")
    synthetic = dict(base, source="cricsheet_official")

    kept, dropped = exporter.select_real_rows([base, srl, synthetic])
    assert [r["team_home"] for r in kept] == ["India"]
    assert dropped == 2

    kept_opt_in, dropped_opt_in = exporter.select_real_rows([base, srl, synthetic], include_simulated=True)
    assert len(kept_opt_in) == 2
    assert dropped_opt_in == 1  # synthetic rows stay excluded either way


def test_clv_audit_excludes_srl_bets_by_default():
    bet = {"bet_id": "b1", "match_id": "m1", "placed_at": datetime(2026, 11, 1, tzinfo=UTC),
           "action": "BACK_HOME_SM", "team": "x", "odds": 2.0, "stake": 10.0, "closing_odds": 1.9,
           "clv": None, "agent_version": "v1", "closing_source": "lotusbook",
           "team_home": "Sydney Thunder SRL", "team_away": "Perth Scorchers SRL"}
    rows, counts = audit_clv.build_audit_rows([bet], {})
    assert rows == [] and counts["excluded_simulated"] == 1

    rows, counts = audit_clv.build_audit_rows([bet], {}, include_simulated=True)
    assert len(rows) == 1 and counts["excluded_simulated"] == 0


def _settled_bet(match_id: str, is_real: bool, pnl: float) -> dict:
    placed = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    return {"match_id": match_id, "placed_at": placed, "settled_at": placed + timedelta(hours=3),
            "profit_loss": pnl, "is_real": is_real, "reference_clv": 0.02, "clv": 0.02}


def test_trust_metrics_exclude_srl_bets_by_default():
    annotated = [_settled_bet("m1", True, 5.0), _settled_bet("srl1", False, 9.0)]
    default = trust_metrics(annotated, allocated_bankroll=100.0, n_bootstrap=100)
    assert default["bet_volume"] == 1.0
    assert default["min_matches"] == 1.0

    opted_in = trust_metrics(annotated, allocated_bankroll=100.0, n_bootstrap=100, include_simulated=True)
    assert opted_in["bet_volume"] == 2.0
    assert opted_in["min_matches"] == 2.0
