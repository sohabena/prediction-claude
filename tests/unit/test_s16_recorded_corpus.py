"""S16: recorded real-odds corpus export."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

from rl.environment import CricketBettingEnv
from scripts.export_recorded_corpus import (
    build_episode,
    build_episode_checked,
    counts_per_key,
    export_matches,
    load_recorded,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _odds(i: int, **over):
    row = {
        "time": T0 + timedelta(seconds=30 * i), "match_id": "m1",
        "team_home": "A", "team_away": "B", "competition": "T20I",
        "back_home": 1.9, "lay_home": 1.95, "back_away": 2.1, "lay_away": 2.15,
        "is_live": True,
    }
    row.update(over)
    return row


def _ctx(i: int, score: int, overs: float):
    return {
        "time": T0 + timedelta(seconds=30 * i), "match_id": "m1", "is_live": True,
        "score": score, "wickets": 0, "overs": overs, "run_rate": 6.0, "req_run_rate": 0.0,
        "innings": 1, "balls_remaining": 60, "batting_team": "A", "bowling_team": "B",
        "status": "live", "match_format": "T20",
    }


RESULT = {"match_id": "m1", "winner": "A", "loser": "B", "team_home": "A", "team_away": "B", "result_type": "win"}


def _match(n: int = 60, result=RESULT):
    return {
        "match_id": "m1", "result": result,
        "odds": [_odds(i) for i in range(n)],
        "context": [_ctx(i, i, i / 6.0) for i in range(0, n, 2)],
    }


def test_asof_join_never_uses_future_context():
    odds = [_odds(i) for i in range(5)]
    ctx = [_ctx(0, 10, 1.0), _ctx(3, 99, 2.0)]
    ep = build_episode(odds, ctx, RESULT, cadence="raw")
    assert ep is not None
    for tick in ep:
        t = datetime.fromisoformat(tick["time"])
        used = 10 if tick["score"] == 10 else 99
        used_time = T0 + timedelta(seconds=30 * (0 if used == 10 else 3))
        assert used_time <= t
    assert [t["score"] for t in ep] == [10, 10, 10, 99, 99]


def test_suspended_rows_dropped_and_no_result_rejected():
    odds = [_odds(0), _odds(1, back_home=None), _odds(2, lay_away=None), _odds(3)]
    ep = build_episode(odds, [], RESULT, cadence="raw")
    assert len(ep) == 2
    ep2, reason = build_episode_checked(odds, [], {"match_id": "m1", "winner": None}, cadence="raw")
    assert ep2 is None and reason == "no_result"
    assert build_episode(odds, [], None) is None


def test_result_only_in_meta():
    ep = build_episode([_odds(i) for i in range(3)], [], RESULT)
    assert ep[0]["_meta"]["winner"] == "A" and ep[0]["_meta"]["winner_is_home"] is True
    assert all("winner" not in t for t in ep)
    assert all("_meta" not in t for t in ep[1:])
    assert ep[0]["source"] == "recorded_lotusbook" and ep[0]["market_model"] == "real"


def test_thresholds_reject():
    ep, reason = build_episode_checked([_odds(i) for i in range(10)], [], RESULT, min_ticks=50)
    assert ep is None and reason == "too_few_ticks"
    gappy = [_odds(i) for i in range(5)] + [_odds(i) for i in range(95, 100)]
    ep, reason = build_episode_checked(gappy, [], RESULT, min_ticks=1, min_coverage=0.6)
    assert ep is None and reason == "low_coverage"


def test_episode_runs_in_env_and_settles_on_recorded_winner():
    ep = build_episode(_match()["odds"], _match()["context"], RESULT, cadence="raw")
    env = CricketBettingEnv(data=[ep], settlement_mode="real", episode_sampling="sequential")
    env.reset()
    done = False
    steps = 0
    while not done and steps < 500:
        _, _, term, trunc, _ = env.step(0)
        done = term or trunc
        steps += 1
    assert done
    assert env._match_winner == "A"
    assert env._determine_home_won() is True


def test_export_is_idempotent_and_loadable(tmp_path):
    kwargs = dict(out_dir=tmp_path, key_resolver=lambda m: "t20i_men", cadence="raw")
    manifest = export_matches([_match()], **kwargs)
    f = tmp_path / "t20i_men" / "m1.json.gz"
    assert f.exists() and counts_per_key(manifest) == {"t20i_men": 1}
    mtime = f.stat().st_mtime_ns
    export_matches([_match()], **kwargs)
    assert f.stat().st_mtime_ns == mtime
    assert len(load_recorded("t20i_men", tmp_path)) == 1
    export_matches([_match()], force=True, **kwargs)
    assert f.exists()
    assert json.loads((tmp_path / "manifest.json").read_text())["matches"][0]["n_ticks"] == 60


def test_rejected_listed_in_manifest(tmp_path):
    manifest = export_matches(
        [_match(result={"match_id": "m1", "winner": None})],
        out_dir=tmp_path, key_resolver=lambda m: "t20i_men", cadence="raw",
    )
    assert manifest["rejected"][0]["reason"] == "no_result"
    assert not (tmp_path / "t20i_men" / "m1.json.gz").exists()
