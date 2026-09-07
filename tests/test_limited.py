"""Draft statistics: joined by Arena card id, cached, and never turned into a verdict."""

import json
import tempfile
import unittest
from pathlib import Path

from mtga_coach.limited import LimitedRatings


ROWS = [
    {"mtga_id": 100, "name": "Strong Card", "ever_drawn_win_rate": 0.61,
     "ever_drawn_game_count": 5000, "avg_seen": 2.1, "avg_pick": 1.8,
     "drawn_improvement_win_rate": 0.05, "color": "W"},
    {"mtga_id": 101, "name": "Thin Sample", "ever_drawn_win_rate": 0.66,
     "ever_drawn_game_count": 120, "avg_seen": 7.4, "avg_pick": 6.9,
     "drawn_improvement_win_rate": 0.02, "color": "U"},
    {"mtga_id": 102, "name": "No Data Yet", "ever_drawn_win_rate": None,
     "ever_drawn_game_count": None, "avg_seen": None, "avg_pick": None,
     "drawn_improvement_win_rate": None, "color": "B"},
]


class Fetcher:
    def __init__(self, rows=ROWS):
        self.rows, self.calls = rows, 0

    def ratings(self, expansion, event):
        self.calls += 1
        return self.rows


class LimitedRatingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.fetcher = Fetcher()
        self.ratings = LimitedRatings(self.root, self.fetcher)

    def tearDown(self):
        self.directory.cleanup()

    def test_a_set_is_fetched_once_and_reused_while_it_is_fresh(self):
        first = self.ratings.fetch("HOB")
        self.assertEqual((first["cards"], first["reused"]), (2, False))
        second = self.ratings.fetch("HOB")
        self.assertTrue(second["reused"])
        self.assertEqual(self.fetcher.calls, 1)
        # A card with no win rate yet is not stored as if it had one.
        self.assertNotIn(102, self.ratings.ratings("HOB"))

    def test_stale_data_is_fetched_again(self):
        self.ratings.fetch("HOB")
        path = self.ratings.path_for("HOB", "PremierDraft")
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["fetched_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertFalse(self.ratings.fetch("HOB")["reused"])
        self.assertEqual(self.fetcher.calls, 2)

    def test_ranking_orders_by_win_rate_and_names_what_it_cannot_cover(self):
        self.ratings.fetch("HOB")
        out = self.ratings.rank("HOB", [100, 101, 102, 999])
        self.assertEqual([item["card_id"] for item in out["rated"]], [101, 100])
        # A card the data does not cover is listed, not silently dropped.
        self.assertEqual(sorted(out["unrated"]), [102, 999])
        # And a high rate off a small sample is marked rather than trusted.
        self.assertEqual(out["thin_sample"], [101])
        self.assertIn("not the deck you are drafting", out["credit"])

    def test_a_set_never_fetched_answers_none_instead_of_an_empty_ranking(self):
        self.assertIsNone(self.ratings.ratings("XYZ"))
        self.assertIsNone(self.ratings.rank("XYZ", [100]))

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(ValueError):
            self.ratings.fetch("HOB", "MadeUpFormat")
