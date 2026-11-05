"""S35: real-price corpus from Betfair historical stream files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scraper.parsers.betfair_historical import BetfairHistoricalParser
from scripts import build_real_price_corpus as corpus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "betfair"
T0 = 1700000000000  # publish time in ms

REAL_TICK_KEYS = {
    "time", "market_id", "match_id", "team_home", "team_away", "competition",
    "match_format", "family", "in_play", "ltp_home", "ltp_away", "ltp_draw",
    "back_home", "lay_home", "back_away", "lay_away",
    "traded_volume_home", "traded_volume_away", "source", "data_tier", "market_model",
}


def _parser() -> BetfairHistoricalParser:
    return BetfairHistoricalParser(resample_seconds=3.0, min_ticks=0)


def _mc(pt: int, market_id: str, **fields: Any) -> str:
    return json.dumps({"op": "mcm", "pt": pt, "mc": [{"id": market_id, **fields}]})


def _market(matches: list, market_id: str) -> Any:
    return next(m for m in matches if m.market_id == market_id)


@pytest.fixture
def built(tmp_path: Path):
    out = tmp_path / "betfair"
    result = corpus.build_corpus(FIXTURES, out)
    return out, result


# ================================================================
# Parser: ltp, ladder, traded volume, in-play, draw, no inferred result
# ================================================================


class TestParserFields:
    def test_basic_fixture_keeps_ltp_and_leaves_ladder_none(self) -> None:
        matches = _parser().parse_stream_market_file(FIXTURES / "t20_basic_ltp.jsonl")
        m = _market(matches, "1.300001")
        assert m.data_tier == "basic"
        assert m.in_play_ticks >= 10
        assert m.ticks[0]["ltp_home"] == 1.9
        assert all(t["ltp_home"] is not None for t in m.ticks)
        for t in m.ticks:
            for key in ("back_home", "lay_home", "back_away", "lay_away"):
                assert t[key] is None, key
            assert t["traded_volume_home"] is None

    def test_advanced_fixture_fills_ladder_and_volume(self) -> None:
        matches = _parser().parse_stream_market_file(FIXTURES / "t20_advanced_ladder.jsonl")
        m = _market(matches, "1.300002")
        assert m.data_tier == "advanced"
        first = m.ticks[0]
        assert (first["back_home"], first["lay_home"]) == (2.5, 2.52)
        assert first["ltp_home"] == 2.51
        assert first["traded_volume_home"] == 1200.0
        assert first["in_play"] is False
        # Level 0 is removed by a size-0 update; the best back falls to level 1.
        in_play = [t for t in m.ticks if t["in_play"]]
        assert in_play[0]["back_home"] == 2.48
        assert in_play[0]["back_away"] == 1.58
        assert m.result_type == "win"
        assert m.winner == "England"

    def test_no_winner_is_not_inferred_from_prices(self) -> None:
        lines = [
            _mc(T0, "1.600001", marketDefinition={
                "eventName": "A v B", "competitionName": "T20 International", "marketType": "MATCH_ODDS",
                "runners": [{"id": 1, "name": "A", "status": "ACTIVE"}, {"id": 2, "name": "B", "status": "ACTIVE"}],
            }, rc=[{"id": 1, "batb": [[0, 1.01, 100]], "batl": [[0, 1.02, 100]]},
                   {"id": 2, "batb": [[0, 50.0, 100]], "batl": [[0, 60.0, 100]]}]),
            _mc(T0 + 4000, "1.600001", marketDefinition={"status": "CLOSED"}),
        ]
        m = _parser().parse_stream_markets(lines)[0]
        assert m.winner == ""
        assert m.result_type == "no_result"

    def test_draw_selection_is_named_and_excluded_from_home_away(self) -> None:
        lines = [
            _mc(T0, "1.500001", marketDefinition={
                "eventName": "India v England", "competitionName": "Test Series",
                "marketType": "MATCH_ODDS", "inPlay": False,
                "runners": [
                    {"id": 1, "name": "The Draw", "status": "ACTIVE"},
                    {"id": 2, "name": "India", "status": "ACTIVE"},
                    {"id": 3, "name": "England", "status": "ACTIVE"},
                ],
            }, rc=[{"id": 1, "ltp": 4.0}, {"id": 2, "ltp": 2.0}, {"id": 3, "ltp": 2.2}]),
            _mc(T0 + 4000, "1.500001", marketDefinition={
                "status": "CLOSED",
                "runners": [
                    {"id": 1, "name": "The Draw", "status": "WINNER"},
                    {"id": 2, "name": "India", "status": "LOSER"},
                    {"id": 3, "name": "England", "status": "LOSER"},
                ],
            }),
        ]
        m = _parser().parse_stream_markets(lines)[0]
        assert (m.team_home, m.team_away) == ("India", "England")
        assert m.result_type == "draw"
        assert m.winner == ""
        assert m.ticks[0]["ltp_draw"] == 4.0
        assert m.ticks[0]["ltp_home"] == 2.0


# ================================================================
# Build: exclusions, result placement, idempotence, loader
# ================================================================


class TestBuild:
    def test_void_and_srl_rejected_with_reasons(self, built) -> None:
        _, result = built
        reasons = {r["market_id"]: r["reason"] for r in result.rejected}
        assert reasons == {"1.300003": "no_winner", "1.300004": "simulated_event"}
        assert {e["market_id"] for e in result.accepted} == {"1.300001", "1.300002"}

    def test_result_only_in_first_tick_meta(self, built) -> None:
        out, _ = built
        episodes = corpus.load_real_corpus(out=out)
        assert len(episodes) == 2
        for ep in episodes:
            first, rest = ep["ticks"][0], ep["ticks"][1:]
            assert "_meta" in first
            for tick in rest:
                assert "_meta" not in tick
                assert not any(k.startswith(("winner", "loser")) for k in tick)
        adv = next(e for e in episodes if e["market_id"] == "1.300002")
        assert adv["ticks"][0]["_meta"] == {
            "winner": "England",
            "loser": "Pakistan",
            "winner_is_home": False,
            "result_type": "win",
        }

    def test_real_tick_contract_keys(self, built) -> None:
        out, _ = built
        tick = corpus.load_real_corpus(out=out, family="t20", tier="advanced")[0]["ticks"][0]
        assert set(tick) == REAL_TICK_KEYS | {"_meta"}
        assert tick["market_model"] == "real"
        assert tick["source"] == "betfair_historical"
        assert tick["match_id"] == "bf_1_300002"
        assert tick["family"] == "t20"

    def test_rerun_writes_nothing(self, built) -> None:
        out, first = built
        before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        second = corpus.build_corpus(FIXTURES, out)
        assert second.written == 0
        assert second.unchanged == first.written
        after = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        assert before == after

    def test_manifest_records_accepted_and_rejected(self, built) -> None:
        out, _ = built
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["counts"]["accepted"] == 2
        assert {r["market_id"]: r["reason"] for r in manifest["rejected"]} == {
            "1.300003": "no_winner",
            "1.300004": "simulated_event",
        }
        for entry in manifest["accepted"]:
            assert len(entry["sha1"]) == 40
            assert entry["n_in_play"] >= 10

    def test_unreadable_file_is_rejected_not_fatal(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "broken.json.gz").write_bytes(b"not gzip")
        result = corpus.build_corpus(src, tmp_path / "out")
        assert result.accepted == []
        assert result.rejected[0]["reason"] == "unreadable_file"


class TestLoader:
    def test_filters_by_family_and_tier(self, built) -> None:
        out, _ = built
        assert [e["market_id"] for e in corpus.load_real_corpus(tier="basic", out=out)] == ["1.300001"]
        assert [e["market_id"] for e in corpus.load_real_corpus(family="t20", tier="advanced", out=out)] == [
            "1.300002"
        ]
        assert corpus.load_real_corpus(family="multiday", out=out) == []
        assert len(corpus.load_real_corpus(out=out)) == 2

    def test_missing_corpus_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            corpus.load_real_corpus(out=tmp_path / "missing")
