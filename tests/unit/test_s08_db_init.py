"""S08: TimescaleDB hardening checks (no database required)."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from backend import db_init
from backend.models.bets import VirtualBetRecord
from backend.models.odds import OddsTick, OddsTickHistorical
from shared.config import DatabaseConfig

ROOT = Path(__file__).resolve().parents[2]


def _migration():
    spec = importlib.util.spec_from_file_location(
        "m002", ROOT / "alembic/versions/002_timescale_hardening.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_retention_only_on_live_table():
    stmts = db_init.RETENTION_STATEMENTS + _migration().UPGRADE_STATEMENTS
    for s in stmts:
        if "add_retention_policy" in s:
            assert "'odds_ticks'" in s and "historical" not in s
    others = (db_init.HYPERTABLE_STATEMENTS + db_init.COMPRESSION_STATEMENTS
              + db_init.INDEX_STATEMENTS + db_init.CHUNK_INTERVAL_STATEMENTS)
    for s in others:
        assert "retention" not in s


def test_chunk_intervals():
    joined = " ".join(db_init.HYPERTABLE_STATEMENTS)
    assert re.search(r"'odds_ticks',.*?INTERVAL '1 day'", joined)
    assert re.search(r"'odds_ticks_historical',.*?INTERVAL '30 days'", joined)
    assert any("set_chunk_time_interval('odds_ticks', INTERVAL '1 day')" in s
               for s in db_init.CHUNK_INTERVAL_STATEMENTS)


def test_safety_statement_removes_retention():
    assert "remove_retention_policy('odds_ticks', if_exists => TRUE)" in db_init.SAFETY_STATEMENTS[0]


def test_migration_chain_and_order():
    m = _migration()
    assert m.revision == "002_timescale_hardening" and m.down_revision == "001_initial"
    up = m.UPGRADE_STATEMENTS
    copy_i = next(i for i, s in enumerate(up) if "INSERT INTO odds_ticks_historical" in s)
    pk_i = next(i for i, s in enumerate(up) if "ADD PRIMARY KEY (time, match_id, tick_seq)" in s)
    assert "remove_retention_policy" in up[0]
    assert copy_i < pk_i
    assert up[-1].startswith("SELECT add_retention_policy('odds_ticks'")
    assert any("DROP TABLE IF EXISTS odds_ticks_historical" in s for s in m.DOWNGRADE_STATEMENTS)


def test_orm_primary_keys():
    assert [c.name for c in OddsTick.__table__.primary_key.columns] == ["time", "match_id", "tick_seq"]
    assert OddsTickHistorical.__tablename__ == "odds_ticks_historical"
    assert {c.name for c in OddsTickHistorical.__table__.primary_key.columns} == {
        "time", "match_id", "tick_seq"}
    assert {c.name for c in VirtualBetRecord.__table__.primary_key.columns} == {"placed_at", "bet_id"}


def test_partial_live_index():
    assert any("idx_odds_live_partial" in s and "WHERE is_live" in s for s in db_init.INDEX_STATEMENTS)


def test_pool_defaults_and_env(monkeypatch):
    for k in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    c = DatabaseConfig()
    assert (c.pool_size, c.max_overflow, c.pool_timeout) == (5, 5, 10)
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "2")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "7")
    c = DatabaseConfig()
    assert (c.pool_size, c.max_overflow, c.pool_timeout) == (3, 2, 7)
