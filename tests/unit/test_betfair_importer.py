"""
Unit tests for Betfair historical data parser and importer.
Compatible with both pytest and standard python -m unittest.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from scraper.importers.betfair_importer import BetfairHistoricalImporter
from scraper.parsers.betfair_historical import BetfairHistoricalParser


class TestBetfairHistoricalParser(unittest.TestCase):
    """Tests for BetfairHistoricalParser."""

    def setUp(self) -> None:
        self.parser = BetfairHistoricalParser(resample_seconds=1.0, min_ticks=2)

    def test_parse_stream_lines_basic(self) -> None:
        """Parser correctly parses stream JSON lines with market definition and runner changes."""
        lines = [
            # 1. Market definition line
            json.dumps({
                "op": "mcm",
                "pt": 1700000000000,
                "mc": [{
                    "id": "1.2001",
                    "marketDefinition": {
                        "eventName": "India v Australia",
                        "competitionName": "ICC T20 World Cup",
                        "marketType": "MATCH_ODDS",
                        "runners": [
                            {"id": 101, "name": "India", "status": "ACTIVE"},
                            {"id": 102, "name": "Australia", "status": "ACTIVE"},
                        ],
                    },
                }],
            }),
            # 2. First tick line
            json.dumps({
                "op": "mcm",
                "pt": 1700000001000,
                "mc": [{
                    "id": "1.2001",
                    "rc": [
                        {"id": 101, "batb": [[0, 1.85, 5000]], "batl": [[0, 1.88, 3000]]},
                        {"id": 102, "batb": [[0, 2.10, 4000]], "batl": [[0, 2.14, 2500]]},
                    ],
                }],
            }),
            # 3. Second tick line (2s later)
            json.dumps({
                "op": "mcm",
                "pt": 1700000003000,
                "mc": [{
                    "id": "1.2001",
                    "rc": [
                        {"id": 101, "batb": [[0, 1.70, 8000]], "batl": [[0, 1.72, 6000]]},
                        {"id": 102, "batb": [[0, 2.30, 2000]], "batl": [[0, 2.36, 1500]]},
                    ],
                }],
            }),
            # 4. Final settlement line
            json.dumps({
                "op": "mcm",
                "pt": 1700000005000,
                "mc": [{
                    "id": "1.2001",
                    "marketDefinition": {
                        "status": "CLOSED",
                        "runners": [
                            {"id": 101, "name": "India", "status": "WINNER"},
                            {"id": 102, "name": "Australia", "status": "LOSER"},
                        ],
                    },
                    "rc": [
                        {"id": 101, "batb": [[0, 1.01, 100000]], "batl": [[0, 1.02, 50000]]},
                        {"id": 102, "batb": [[0, 50.0, 100]], "batl": [[0, 60.0, 50]]},
                    ],
                }],
            }),
        ]

        matches = self.parser.parse_stream_lines(lines)
        self.assertEqual(len(matches), 1)
        m = matches[0]

        self.assertEqual(m.match_id, "bf_1_2001")
        self.assertEqual(m.team_home, "India")
        self.assertEqual(m.team_away, "Australia")
        self.assertEqual(m.competition, "ICC T20 World Cup")
        self.assertEqual(m.winner, "India")
        self.assertEqual(m.loser, "Australia")
        self.assertGreaterEqual(len(m.ticks), 2)

        # Check first tick values
        first = m.ticks[0]
        self.assertEqual(first["back_home"], 1.85)
        self.assertEqual(first["lay_home"], 1.88)
        self.assertEqual(first["back_away"], 2.10)
        self.assertEqual(first["lay_away"], 2.14)
        self.assertTrue(first["is_live"])

    def test_resample_skips_rapid_subsecond_ticks(self) -> None:
        """Ticks arriving faster than resample_seconds are skipped to avoid database bloat."""
        parser = BetfairHistoricalParser(resample_seconds=3.0, min_ticks=1)

        lines = [
            json.dumps({
                "op": "mcm",
                "pt": 1700000000000,
                "mc": [{
                    "id": "1.3001",
                    "marketDefinition": {
                        "runners": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
                    },
                    "rc": [
                        {"id": 1, "batb": [[0, 2.0, 100]]},
                        {"id": 2, "batb": [[0, 2.0, 100]]},
                    ],
                }],
            }),
            # 500ms later (should be skipped by resample interval of 3.0s)
            json.dumps({
                "op": "mcm",
                "pt": 1700000000500,
                "mc": [{
                    "id": "1.3001",
                    "rc": [
                        {"id": 1, "batb": [[0, 2.05, 100]]},
                        {"id": 2, "batb": [[0, 1.95, 100]]},
                    ],
                }],
            }),
            # 4.0s later (should be recorded)
            json.dumps({
                "op": "mcm",
                "pt": 1700000004000,
                "mc": [{
                    "id": "1.3001",
                    "rc": [
                        {"id": 1, "batb": [[0, 2.10, 100]]},
                        {"id": 2, "batb": [[0, 1.90, 100]]},
                    ],
                }],
            }),
        ]

        matches = parser.parse_stream_lines(lines)
        self.assertEqual(len(matches), 1)
        # Should have exactly 2 ticks, not 3
        self.assertEqual(len(matches[0].ticks), 2)

    def test_spread_violation_corrected(self) -> None:
        """If raw stream data has back > lay (inversion anomaly), it is corrected to ensure back <= lay."""
        lines = [
            json.dumps({
                "op": "mcm",
                "pt": 1700000000000,
                "mc": [{
                    "id": "1.4001",
                    "marketDefinition": {
                        "runners": [{"id": 1, "name": "Team1"}, {"id": 2, "name": "Team2"}],
                    },
                    "rc": [
                        # back 2.20 > lay 2.00 (inverted)
                        {"id": 1, "batb": [[0, 2.20, 100]], "batl": [[0, 2.00, 100]]},
                        {"id": 2, "batb": [[0, 1.80, 100]], "batl": [[0, 1.85, 100]]},
                    ],
                }],
            }),
        ]
        parser = BetfairHistoricalParser(resample_seconds=0.1, min_ticks=1)
        matches = parser.parse_stream_lines(lines)
        self.assertEqual(len(matches), 1)
        tick = matches[0].ticks[0]
        self.assertLessEqual(tick["back_home"], tick["lay_home"])

    def test_parse_csv_format(self) -> None:
        """Parser parses Betfair historical tabular CSV format."""
        csv_content = """EVENT_ID,EVENT_NAME,RECORD_TIME,SELECTION_NAME,BEST_BACK_PRICE,BEST_LAY_PRICE,WIN_FLAG
12345,England v Pakistan,2025-05-10 14:00:00,England,1.80,1.82,1
12345,England v Pakistan,2025-05-10 14:00:00,Pakistan,2.15,2.18,0
12345,England v Pakistan,2025-05-10 14:05:00,England,1.65,1.67,1
12345,England v Pakistan,2025-05-10 14:05:00,Pakistan,2.40,2.45,0
12345,England v Pakistan,2025-05-10 14:10:00,England,1.10,1.12,1
12345,England v Pakistan,2025-05-10 14:10:00,Pakistan,9.00,9.50,0
"""
        with NamedTemporaryFile("w+", suffix=".csv", delete=False) as tmp:
            tmp.write(csv_content)
            tmp_path = Path(tmp.name)

        try:
            parser = BetfairHistoricalParser(resample_seconds=1.0, min_ticks=2)
            matches = parser.parse_csv_file(tmp_path)
            self.assertEqual(len(matches), 1)
            m = matches[0]
            self.assertEqual(m.team_home, "England")
            self.assertEqual(m.team_away, "Pakistan")
            self.assertEqual(m.winner, "England")
            self.assertEqual(m.loser, "Pakistan")
            self.assertEqual(len(m.ticks), 3)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestBetfairSyntheticGenerator(unittest.TestCase):
    """Tests for synthetic demo dataset generator."""

    def test_generate_synthetic_match(self) -> None:
        """Synthetic match generator produces valid in-play trajectories."""
        start = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        match = BetfairHistoricalImporter.generate_synthetic_match(
            match_id="test_syn_001",
            team_home="India",
            team_away="Australia",
            competition="ICC World Cup",
            start_time=start,
            ticks_count=60,
            home_win_prob=1.0,  # guaranteed home win
        )

        self.assertEqual(match.match_id, "test_syn_001")
        self.assertEqual(match.team_home, "India")
        self.assertEqual(match.team_away, "Australia")
        self.assertEqual(match.winner, "India")
        self.assertEqual(match.loser, "Australia")
        self.assertEqual(len(match.ticks), 60)

        # Monotonically increasing time
        times = [t["timestamp"] for t in match.ticks]
        self.assertEqual(times, sorted(times))

        # Spreads valid
        for t in match.ticks:
            self.assertLessEqual(t["back_home"], t["lay_home"])
            self.assertLessEqual(t["back_away"], t["lay_away"])
            self.assertTrue(t["is_live"])
            self.assertIsNotNone(t["overround"])

        # Convergence for winner
        last = match.ticks[-1]
        self.assertLess(last["back_home"], 1.15)
        self.assertGreater(last["back_away"], 5.0)


if __name__ == "__main__":
    unittest.main()
