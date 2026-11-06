"""S91: the read-only paper and data API. FastAPI TestClient on the real app (``backend.main``), with the database
readers and the result writer monkeypatched. No database, no network, no Redis, no real clock: ``now`` is passed in,
and the log and report files live in a temporary PHOENIX_ROOT.
"""

from __future__ import annotations

import asyncio
import json
import math
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import httpx

from backend.main import app
from backend.routers import data as data_api
from backend.routers import paper as paper_api
from backend.routers import readonly
from scripts import results_reconciliation as rec
from scripts import set_result
from tests.unit.test_s73_paper_review import _bet
from virtual_trading import paper_review as rv
from virtual_trading import scorecard as sc

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
BANK = 5_000_000.0
class _Client:
    """The TestClient surface these tests use (get, post), over httpx's ASGI transport on the real app. Starlette 0.27
    (installed with fastapi 0.104 here; requirements ask for fastapi >= 0.109) passes an ``app`` argument that httpx 0.28
    no longer accepts, so ``fastapi.testclient.TestClient`` cannot be built on this host. The app, its routes and its
    exception handling are the same; only the transport is named here. No lifespan (as with TestClient without ``with``)."""

    def __init__(self, application: Any) -> None:
        self._app = application

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def go() -> httpx.Response:
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
                return await http.request(method, url, **kwargs)

        return asyncio.run(go())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._send("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._send("POST", url, **kwargs)


client = _Client(app)


# ----------------------------------------------------------------------------- fakes
class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Records every statement. Returns the given rows for any SELECT."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.statements: list[str] = []
        self.rows = rows or []

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        self.statements.append(str(stmt))
        return FakeResult(self.rows)


def fake_read_only(rows: list[dict[str, Any]] | None = None, sink: list[FakeSession] | None = None):
    """A replacement for ``readonly.read_only_session``: the same SET statement first, no database."""

    @asynccontextmanager
    async def _session():
        session = FakeSession(rows)
        if sink is not None:
            sink.append(session)
        await session.execute(readonly.text("SET TRANSACTION READ ONLY"))
        yield session

    return _session


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary repository root with logs/ and reports/ (PHOENIX_ROOT, as in the container)."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports" / "paper").mkdir(parents=True)
    monkeypatch.setenv("PHOENIX_ROOT", str(tmp_path))
    return tmp_path


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


# ----------------------------------------------------------------------------- shared helpers
def test_repo_root_follows_phoenix_root_and_defaults_to_the_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_ROOT", "/app")
    assert readonly.repo_root() == Path("/app")
    assert readonly.logs_dir() == Path("/app") / "logs"
    assert readonly.reports_dir() == Path("/app") / "reports"
    monkeypatch.delenv("PHOENIX_ROOT")
    assert (readonly.repo_root() / "backend" / "routers" / "readonly.py").exists()


def test_last_json_line_returns_the_last_complete_object(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    _write_jsonl(path, [{"n": 1}, {"n": 2}, {"n": 3}])
    assert readonly.last_json_line(path) == {"n": 3}


def test_last_json_line_skips_a_partial_last_line_and_error_lines(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text('{"cycle_at": "2026-11-03T11:00:00+00:00"}\nnot json\n{"cycle_at": "2026-11-03T11:0', encoding="utf-8")
    assert readonly.last_json_line(path, accept=lambda r: "cycle_at" in r) == {"cycle_at": "2026-11-03T11:00:00+00:00"}


def test_last_json_line_reads_a_line_longer_than_one_block(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    big = {"cycle_at": "2026-11-03T12:00:00+00:00", "pad": "x" * (3 * 64 * 1024)}
    _write_jsonl(path, [{"n": 1}, big])
    assert readonly.last_json_line(path)["cycle_at"] == big["cycle_at"]


def test_last_json_line_is_none_for_a_missing_or_empty_file(tmp_path: Path) -> None:
    assert readonly.last_json_line(tmp_path / "missing.jsonl") is None
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert readonly.last_json_line(tmp_path / "empty.jsonl") is None


def test_jsonable_removes_nan_and_infinity_and_converts_types() -> None:
    value = {
        "nan": float("nan"), "inf": math.inf, "ok": 1.5, "np": np.float64(2.5), "npi": np.int64(3),
        "dec": Decimal("1.25"), "when": datetime(2026, 11, 3, 12, 0), "day": date(2026, 11, 3),
        "nested": [{"x": np.float64("nan")}], "tup": (1, 2),
    }
    out = readonly.jsonable(value)
    assert out["nan"] is None and out["inf"] is None and out["nested"][0]["x"] is None
    assert out["ok"] == 1.5 and out["np"] == 2.5 and out["npi"] == 3 and out["dec"] == 1.25
    assert out["when"] == "2026-11-03T12:00:00+00:00"  # a naive datetime is taken as UTC
    assert out["day"] == "2026-11-03" and out["tup"] == [1, 2]
    json.dumps(out, allow_nan=False)  # must not raise


def test_read_only_session_runs_the_read_only_statement_first(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[FakeSession] = []

    @asynccontextmanager
    async def fake_get_session():
        session = FakeSession()
        seen.append(session)
        yield session

    import shared.db

    monkeypatch.setattr(shared.db, "get_session", fake_get_session)

    async def run() -> None:
        async with readonly.read_only_session() as session:
            assert session is seen[0]

    asyncio.run(run())
    assert seen[0].statements == ["SET TRANSACTION READ ONLY"]


def test_no_route_writes_except_the_one_result_post() -> None:
    writes = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not (path.startswith("/api/paper") or path.startswith("/api/data")):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method not in ("GET", "HEAD"):
                writes.add((method, path))
    assert writes == {("POST", "/api/data/results")}


# ----------------------------------------------------------------------------- live: pure shaping
def _registry(match_id: str, *, start: datetime, actual: datetime | None = None) -> dict[str, Any]:
    return {
        "match_id": match_id, "team_home": "Alpha", "team_away": "Beta", "competition": "Nepal Premier League",
        "match_start_time": start, "actual_start_time": actual, "is_simulated": False, "settled": False,
        "toss_winner": None, "toss_decision": None,
    }


def _prices(match_id: str, at: datetime) -> list[dict[str, Any]]:
    return [{
        "match_id": match_id, "time": at, "back_home": 1.9, "lay_home": 1.96, "back_away": 2.0, "lay_away": 2.06,
        "source": "lotusbook",
    }]


def _live_inputs(now: datetime) -> dict[str, Any]:
    fresh = now - timedelta(minutes=1)
    ledger = [
        _bet(variant="w05_e03", match_id="m1", status="filled", side="BACK", selection="home", price=2.0,
             stake=1_000.0, decided_at=now - timedelta(minutes=50 - i), in_play=False)
        for i in range(7)
    ]
    ledger.append(_bet(variant="placebo_random", match_id="m1", status="filled", side="LAY", selection="away",
                       price=2.1, stake=500.0, decided_at=now - timedelta(minutes=30), in_play=False))
    return {
        "registry": [_registry("m1", start=now - timedelta(hours=1), actual=now - timedelta(minutes=55))],
        "last_prices": {"m1": fresh},
        "price_rows": _prices("m1", fresh),
        "ledger": ledger,
        "context": [{
            "match_id": "m1", "time": fresh, "is_live": True, "score": 84, "wickets": 3, "overs": 10.2,
            "innings": 1, "batting_team": "Alpha", "bowling_team": "Beta", "status": "live",
            "match_format": "T20",
        }],
    }


def test_build_live_reports_positions_budget_tickets_and_last_trades() -> None:
    now = NOW
    cycle = {"cycle_at": now.isoformat(), "reference": "w05_e03", "considered": 1, "matches": [{"match_id": "m1"}],
             "guard_refusals": {}, "candidates": 1, "intents": 0, "primary": "w05_e03", "suspended": []}
    out = paper_api.build_live(now=now, cycle=cycle, inputs=_live_inputs(now), budgets={"w05_e03": 30})
    assert out["budget"] == 100_000 and out["hard_limit"] == 120_000
    assert out["cycle"]["stale"] is False and out["matches_logged"] == 1
    (match,) = out["matches"]
    assert match["team_home"] == "Alpha" and match["team_away"] == "Beta"
    assert match["competition"] == "Nepal Premier League"
    assert match["in_play"] is True  # the actual start is before now
    assert match["actual_start_time"] is not None and match["match_start_time"] is not None
    assert match["latest_score"]["score"] == 84 and match["latest_score"]["match_format"] == "T20"
    assert match["p_market"] is not None and 0.0 < match["p_market"] < 1.0
    assert match["p_model"] is None  # no decision carried a model probability in this fixture

    by_name = {v["variant"]: v for v in match["variants"]}
    main = by_name["w05_e03"]
    assert main["placebo"] is False and main["budget"] == 100_000 and main["hard_limit"] == 120_000
    assert main["pnl_if_home"] == pytest.approx(7 * 1_000.0)  # seven BACK home bets at 2.0 win 1,000 each
    assert main["pnl_if_away"] == pytest.approx(-7 * 1_000.0)
    assert main["worst_case"] == pytest.approx(7_000.0)
    assert main["tickets_used"] == 7 and main["ticket_budget"] == 30
    assert len(main["last_trades"]) == 5
    decided = [t["decided_at"] for t in main["last_trades"]]
    assert decided == sorted(decided, reverse=True)  # newest first
    assert by_name["placebo_random"]["placebo"] is True
    assert by_name["placebo_random"]["ticket_budget"] == sc.TICKET_BUDGET  # not in the budgets map: the default


def test_build_live_with_no_trader_cycle_is_empty_and_says_so() -> None:
    out = paper_api.build_live(now=NOW, cycle=None, inputs=dict(paper_api.EMPTY_INPUTS), budgets={})
    assert out["cycle"] is None and out["matches"] == [] and out["matches_logged"] == 0


def test_a_cycle_older_than_five_minutes_is_stale() -> None:
    old = {"cycle_at": (NOW - timedelta(minutes=9)).isoformat(), "matches": []}
    view = paper_api.cycle_view(old, NOW)
    assert view["stale"] is True and view["age_seconds"] == pytest.approx(540.0)


def test_live_endpoint_reads_the_last_cycle_and_its_rows(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    cycle = {"cycle_at": now.isoformat(), "reference": "w05_e03", "considered": 1, "matches": [{"match_id": "m1"}]}
    _write_jsonl(root / "logs" / "paper_trader.log", [
        {"cycle_at": (now - timedelta(seconds=15)).isoformat(), "matches": []},
        cycle,
        {"error": "later error line, not a cycle"},
    ])
    seen_ids: list[list[str]] = []
    fixture = _live_inputs(now)

    async def fake_inputs(session: Any, ids: Any, when: datetime) -> dict[str, Any]:
        seen_ids.append(list(ids))
        return fixture

    sessions: list[FakeSession] = []
    monkeypatch.setattr(paper_api, "read_only_session", fake_read_only(sink=sessions))
    monkeypatch.setattr(paper_api, "load_live_inputs", fake_inputs)
    response = client.get("/api/paper/live")
    assert response.status_code == 200
    body = response.json()
    assert seen_ids == [["m1"]]  # the matches of the last cycle record, not the error line
    assert sessions[0].statements == ["SET TRANSACTION READ ONLY"]
    assert body["matches"][0]["match_id"] == "m1"


def test_live_endpoint_without_a_trader_log_answers_with_no_matches(root: Path) -> None:
    response = client.get("/api/paper/live")
    assert response.status_code == 200
    assert response.json()["cycle"] is None and response.json()["matches"] == []


# ----------------------------------------------------------------------------- evidence
def _evidence_rows(*, settled: int, matches: int, clv_base: float = 0.02) -> list[dict[str, Any]]:
    rows = []
    for i in range(settled):
        mid = f"m{i % matches}"
        rows.append(_bet(variant="w05_e03", match_id=mid, status="settled", clv=clv_base + 0.001 * (i % 5),
                         in_play=(i % 3 == 0), settled_at=NOW - timedelta(days=1)))
        rows.append(_bet(variant="placebo_random", match_id=mid, status="settled", clv=0.0,
                         in_play=(i % 3 == 0), settled_at=NOW - timedelta(days=1)))
    return rows


def _evidence_data(rows: list[dict[str, Any]], *, gate: dict[str, Any] | None = None) -> dict[str, Any]:
    bankrolls = {"w05_e03": BANK, "placebo_random": BANK}
    state = rv.review(rows, bankrolls, now=NOW, n_resamples=200, draws=200, reference="w05_e03")
    counts = sc.match_counts([], NOW, date(2026, 10, 25))
    return {"state": state, "rows": rows, "missing": [], "counts": counts,
            "gate": gate if gate is not None else {"t20": {"inplay_model_allowed": False, "matches": 3,
                                                           "logloss_diff": 0.01}},
            "tier": None}


def test_build_evidence_says_r3_passed_only_when_a_strategy_variant_meets_r3() -> None:
    met = paper_api.build_evidence(_evidence_data(_evidence_rows(settled=320, matches=40)), NOW, None)
    assert met["r3_passed"] is True
    assert [v for v in met["variants"] if v["variant"] == "w05_e03"][0]["r3"]["met"] is True

    small = paper_api.build_evidence(_evidence_data(_evidence_rows(settled=12, matches=4)), NOW, None)
    assert small["r3_passed"] is False
    assert small["r3_target"] == {"settled_bets": 300, "matches": 30}


def test_build_evidence_is_json_safe_and_keeps_placebos_out_of_the_r3_question() -> None:
    data = _evidence_data(_evidence_rows(settled=40, matches=10))
    out = paper_api.build_evidence(data, NOW, {"file": "20261102.md", "date": "2026-11-02", "bytes": 10,
                                               "modified_at": NOW.isoformat()})
    json.dumps(readonly.jsonable(out), allow_nan=False)
    names = [v["variant"] for v in out["variants"]]
    assert "placebo_random" in names
    assert "placebo_random" not in out["excess_over_placebo"]["pre_match"]
    assert set(out["excess_over_placebo"]["pre_match"]) <= {"w05_e03"}
    assert out["in_play_gate"] == [{"format": "t20", "open": False, "matches": 3, "logloss_diff": 0.01}]
    assert out["progress"]["r3"] == {"target_matches": 30, "target_settled_bets": 300}
    assert out["daily_report"]["file"] == "20261102.md"
    assert [v["variant"] for v in out["fill_realism"]["variants"]] == ["w05_e03"]  # strategies only


def test_evidence_endpoint_serves_the_gathered_inputs(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "reports" / "paper" / "20261101.md").write_text("old", encoding="utf-8")
    (root / "reports" / "paper" / "20261102.md").write_text("newer report", encoding="utf-8")
    (root / "reports" / "paper" / "notes.md").write_text("ignored", encoding="utf-8")
    data = _evidence_data(_evidence_rows(settled=320, matches=40))

    async def fake_inputs(now: datetime) -> dict[str, Any]:
        return data

    monkeypatch.setattr(paper_api, "load_evidence_inputs", fake_inputs)
    response = client.get("/api/paper/evidence")
    assert response.status_code == 200
    body = response.json()
    assert body["r3_passed"] is True
    assert body["daily_report"]["file"] == "20261102.md" and body["daily_report"]["bytes"] == len("newer report")
    assert "variants" in body and "excess_over_placebo" in body and "fill_realism" in body


def test_latest_daily_report_reads_metadata_only(root: Path) -> None:
    assert paper_api.latest_daily_report(root / "reports" / "paper") is None
    (root / "reports" / "paper" / "20261103.md").write_text("abc", encoding="utf-8")
    report = paper_api.latest_daily_report(root / "reports" / "paper")
    assert report["file"] == "20261103.md" and report["date"] == "2026-11-03" and report["bytes"] == 3


# ----------------------------------------------------------------------------- data: health
def test_health_returns_the_last_monitor_record_with_its_checks(root: Path) -> None:
    older = {"ts": "2026-11-03T10:00:00+00:00", "overall": "ok", "checks": []}
    last = {"ts": "2026-11-03T11:00:00+00:00", "window_hours": 24.0, "overall": "fail", "exit_code": 1, "checks": [
        {"name": "freshness", "status": "ok", "value": 1.5, "detail": "newest 9s ago", "extra": "dropped"},
        {"name": "lay_completeness", "status": "warn", "value": 0.02, "detail": "some lays missing"},
    ]}
    _write_jsonl(root / "logs" / "data_quality.jsonl", [older, last])
    body = client.get("/api/data/health").json()
    assert body["available"] is True and body["ts"] == last["ts"] and body["overall"] == "fail"
    assert body["exit_code"] == 1 and body["window_hours"] == 24.0
    assert body["checks"][0] == {"name": "freshness", "status": "ok", "value": 1.5, "detail": "newest 9s ago"}
    assert [c["name"] for c in body["checks"]] == ["freshness", "lay_completeness"]


def test_health_without_a_monitor_log_says_unavailable(root: Path) -> None:
    body = client.get("/api/data/health").json()
    assert body == {"available": False, "ts": None, "age_seconds": None, "window_hours": None, "overall": None,
                    "exit_code": None, "checks": []}


# ----------------------------------------------------------------------------- data: matches
def _tick_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "match_id": "lb_1", "team_home": "Alpha", "team_away": "Beta", "competition": "Nepal Premier League",
        "is_simulated": False, "match_start_time": NOW + timedelta(hours=2), "actual_start_time": None,
        "actual_start_source": None, "score_pipeline": "livescore-v2", "last_tick": NOW - timedelta(minutes=2),
        "first_live_tick": None, "odds_rows": 40, "live_now": False, "match_page_seen": True,
        "result_type": None, "winner": None, "completed_at": None,
    }
    row.update(overrides)
    return row


def test_match_state_live_pre_match_and_finished() -> None:
    live = _tick_row(live_now=True, actual_start_time=NOW - timedelta(minutes=40), first_live_tick=NOW - timedelta(minutes=40))
    assert data_api.match_state(live, NOW) == "live"
    stale_live = dict(live, last_tick=NOW - timedelta(minutes=30))  # in play by the flag, but the tick is old
    assert data_api.match_state(stale_live, NOW) == "finished"
    assert data_api.match_state(_tick_row(), NOW) == "pre-match"  # starts in two hours
    assert data_api.match_state(_tick_row(match_start_time=NOW - timedelta(hours=3), first_live_tick=NOW - timedelta(hours=3)), NOW) == "finished"
    assert data_api.match_state(_tick_row(match_start_time=None), NOW) == "pre-match"  # no start, never seen live
    test_stumps = _tick_row(
        competition="Test Matches",
        match_start_time=NOW - timedelta(days=1),
        actual_start_time=NOW - timedelta(days=1),
        first_live_tick=NOW - timedelta(days=1),
        last_tick=NOW - timedelta(hours=8),
    )
    assert data_api.match_state(test_stumps, NOW) == "stumps"


def test_build_matches_hides_simulated_by_default_and_counts_missing_results() -> None:
    rows = [
        _tick_row(match_id="lb_live", live_now=True, actual_start_time=NOW - timedelta(minutes=20),
                  first_live_tick=NOW - timedelta(minutes=20)),
        _tick_row(match_id="lb_just_ended", match_start_time=NOW - timedelta(hours=2),
                  first_live_tick=NOW - timedelta(hours=2), last_tick=NOW - timedelta(hours=1)),
        _tick_row(match_id="lb_done", match_start_time=NOW - timedelta(hours=4), first_live_tick=NOW - timedelta(hours=4),
                  last_tick=NOW - timedelta(hours=3), result_type="win", winner="Alpha"),
        _tick_row(match_id="lb_gone", match_start_time=NOW - timedelta(hours=6), first_live_tick=NOW - timedelta(hours=6),
                  last_tick=NOW - timedelta(hours=5)),
        _tick_row(match_id="srl_1", competition="SRL T20 Simulated", is_simulated=True),
    ]
    out = data_api.build_matches(rows, NOW, 3, include_simulated=False)
    ids = [m["match_id"] for m in out["matches"]]
    assert "srl_1" not in ids and out["count"] == 4
    assert ids[0] == "lb_live"  # live first
    assert out["summary"] == {"live": 1, "pre-match": 0, "stumps": 0, "finished": 3, "missing_result": 1}
    ended = next(m for m in out["matches"] if m["match_id"] == "lb_just_ended")
    assert ended["state"] == "finished" and ended["result_status"] == "awaiting"  # under 3 h silent: not missing
    gone = next(m for m in out["matches"] if m["match_id"] == "lb_gone")
    assert gone["result_status"] == "missing" and gone["result"]["decided"] is False
    done = next(m for m in out["matches"] if m["match_id"] == "lb_done")
    assert done["result_status"] == "decided" and done["match_page_seen"] is True
    assert done["score_pipeline"] == "livescore-v2"

    with_sim = data_api.build_matches(rows, NOW, 3, include_simulated=True)
    assert any(m["match_id"] == "srl_1" and m["simulated"] for m in with_sim["matches"])


def test_build_matches_multiday_awaits_result_during_overnight_break() -> None:
    # Test match: Day 1 stumps 8 hours ago (last tick 8 h old, start 14 h old)
    test_row = _tick_row(
        match_id="lb_test",
        competition="Test Matches",
        match_start_time=NOW - timedelta(hours=14),
        actual_start_time=NOW - timedelta(hours=14),
        first_live_tick=NOW - timedelta(hours=14),
        last_tick=NOW - timedelta(hours=8),
    )
    out = data_api.build_matches([test_row], NOW, 3, include_simulated=False)
    m = out["matches"][0]
    assert m["state"] == "stumps"
    assert m["result_status"] == "awaiting"  # stays awaiting across multiday window
    assert out["summary"]["missing_result"] == 0
    assert out["summary"]["stumps"] == 1


def test_matches_endpoint_reads_in_a_read_only_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions: list[FakeSession] = []
    rows = [_tick_row(live_now=True, actual_start_time=NOW - timedelta(minutes=5), first_live_tick=NOW - timedelta(minutes=5))]
    monkeypatch.setattr(data_api, "read_only_session", fake_read_only(rows, sink=sessions))
    response = client.get("/api/data/matches", params={"days": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 2 and body["count"] == 1 and body["matches"][0]["state"] in {"live", "finished"}
    assert sessions[0].statements[0] == "SET TRANSACTION READ ONLY"
    assert client.get("/api/data/matches", params={"days": 99}).status_code == 422


def test_missing_results_endpoint_serves_the_reconciliation_list(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"match_id": "lb_9", "team_home": "A", "team_away": "B", "competition": "C",
            "match_start_time": NOW - timedelta(hours=5), "last_tick": NOW - timedelta(hours=4),
            "age_hours": 4.0, "result_type": None, "has_result": False}

    async def fake_missing(session: Any, now: datetime, lookback: timedelta = rec.LOOKBACK) -> list[dict[str, Any]]:
        return [item]

    monkeypatch.setattr(data_api, "read_only_session", fake_read_only())
    monkeypatch.setattr(rec, "missing_results", fake_missing)
    body = client.get("/api/data/missing-results").json()
    assert body["count"] == 1 and body["items"][0]["match_id"] == "lb_9"
    assert body["lookback_days"] == 7 and body["stale_after_hours"] == 3.0
    assert body["items"][0]["last_tick"].startswith("2026-11-03")


# ----------------------------------------------------------------------------- data: the manual result
RESULT_URL = "https://example.com/scorecard/match-9"


def _post(body: dict[str, Any]):
    return client.post("/api/data/results", json=body)


def _summary(**overrides: Any) -> dict[str, Any]:
    summary = {
        "match_id": "lb_1", "team_home": "Alpha", "team_away": "Beta", "competition": "Nepal Premier League",
        "is_simulated": False,
        "written": {"result_type": "win", "winner": "Alpha", "loser": "Beta", "margin": "15 runs",
                    "source": "public_scorecard"},
        "replaced": None, "bets_settled": {"settled": 3, "void": 1}, "url": RESULT_URL, "dry_run": False,
    }
    summary.update(overrides)
    return summary


def test_post_result_writes_settles_and_audits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: list[Any] = []
    audit = tmp_path / "set_result.jsonl"
    monkeypatch.setattr(set_result, "AUDIT_PATH", audit)

    async def fake_run(request: Any, *, now: datetime, session_factory: Any = None) -> dict[str, Any]:
        captured.append(request)
        return _summary()

    monkeypatch.setattr(set_result, "run", fake_run)
    response = _post({"match_id": "lb_1", "outcome": "winner", "winner": "alpha", "margin": "15 runs",
                      "url": RESULT_URL})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["written"] is True and body["paper_bets_settled"] == 4
    assert body["bets_settled"] == {"settled": 3, "void": 1}
    assert body["result"]["winner"] == "Alpha" and body["result"]["source"] == "public_scorecard"
    assert body["url"] == RESULT_URL and body["replaced"] is None
    request = captured[0]
    assert request.force is False and request.dry_run is False and request.source == "public_scorecard"
    assert request.winner == "alpha" and request.result_type == "win"
    record = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert record["match_id"] == "lb_1" and record["force"] is False and record["url"] == RESULT_URL


@pytest.mark.parametrize("body", [
    {"match_id": "lb_1", "outcome": "winner", "url": RESULT_URL},  # winner missing
    {"match_id": "lb_1", "outcome": "winner", "winner": "  ", "url": RESULT_URL},  # blank winner
    {"match_id": "lb_1", "outcome": "tie", "winner": "Alpha", "url": RESULT_URL},  # winner given for a tie
    {"match_id": "lb_1", "outcome": "tie", "margin": "2 runs", "url": RESULT_URL},  # margin only for a win
    {"match_id": "lb_1", "outcome": "no_result", "winner": "Alpha", "url": RESULT_URL},
    {"match_id": "lb_1", "outcome": "abandoned", "url": RESULT_URL},  # not an allowed outcome
    {"match_id": "lb_1", "outcome": "tie", "url": "ftp://example.com/x"},  # not http(s)
    {"match_id": "lb_1", "outcome": "tie"},  # no url
    {"match_id": "lb_1", "outcome": "tie", "url": RESULT_URL, "force": True},  # no force from the API
    {"match_id": "", "outcome": "tie", "url": RESULT_URL},
])
def test_post_result_refuses_invalid_bodies_without_touching_the_writer(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any],
) -> None:
    async def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("set_result.run must not be reached for an invalid body")

    monkeypatch.setattr(set_result, "run", must_not_run)
    response = _post(body)
    assert response.status_code == 422, response.text


def test_post_result_answers_409_when_a_trusted_result_would_be_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(request: Any, *, now: datetime, session_factory: Any = None) -> None:
        raise set_result.OverwriteRefused("lb_1 already has a trusted result (result_type win, winner Alpha); "
                                          "pass --force to replace it")

    monkeypatch.setattr(set_result, "run", refuse)
    response = _post({"match_id": "lb_1", "outcome": "tie", "url": RESULT_URL})
    assert response.status_code == 409
    assert "already has a trusted result" in response.json()["detail"]


def test_post_result_answers_422_for_other_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(request: Any, *, now: datetime, session_factory: Any = None) -> None:
        raise set_result.ResultError("winner 'Gamma' is neither 'Alpha' nor 'Beta' in the registry")

    monkeypatch.setattr(set_result, "run", refuse)
    response = _post({"match_id": "lb_1", "outcome": "winner", "winner": "Gamma", "url": RESULT_URL})
    assert response.status_code == 422 and "neither" in response.json()["detail"]


def test_post_result_hides_the_driver_error_and_returns_500(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(request: Any, *, now: datetime, session_factory: Any = None) -> None:
        raise RuntimeError("password=secret connection string")

    monkeypatch.setattr(set_result, "run", broken)
    response = _post({"match_id": "lb_1", "outcome": "tie", "url": RESULT_URL})
    assert response.status_code == 500
    assert "secret" not in response.text


def test_overwrite_refusal_is_a_result_error_so_the_cli_is_unchanged() -> None:
    assert issubclass(set_result.OverwriteRefused, set_result.ResultError)
    trusted = {"match_id": "lb_1", "winner": "Alpha", "loser": "Beta", "result_type": "win", "source": "x"}
    with pytest.raises(set_result.OverwriteRefused):
        set_result.check_overwrite(trusted, False, 0, "lb_1")
    with pytest.raises(set_result.OverwriteRefused):
        set_result.check_overwrite(trusted, True, 2, "lb_1")  # force, but settled bets exist
    set_result.check_overwrite(trusted, True, 0, "lb_1")  # force with no settled bets: allowed (CLI only)


def test_request_args_builds_a_valid_request_for_each_outcome() -> None:
    winner = data_api.ResultIn(match_id="lb_1", outcome="winner", winner="Alpha", margin="6 wickets", url=RESULT_URL)
    req = set_result.build_request(data_api.request_args(winner))
    assert req.result_type == "win" and req.winner == "Alpha" and req.margin == "6 wickets" and req.force is False
    tie = set_result.build_request(data_api.request_args(
        data_api.ResultIn(match_id="lb_1", outcome="tie", url=RESULT_URL)))
    assert tie.result_type == "tie" and tie.winner is None
    none = set_result.build_request(data_api.request_args(
        data_api.ResultIn(match_id="lb_1", outcome="no_result", url=RESULT_URL)))
    assert none.result_type == "no_result"
