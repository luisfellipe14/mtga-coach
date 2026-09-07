"""Grades a player writes, and the order of preference they sit in."""

import tempfile
import unittest
from pathlib import Path

from mtga_coach import build
from mtga_coach.community import CommunityGrades


def card(name, colours=("R",), mana=2):
    return {"name": name, "resolved": True, "colors": list(colours), "color_identity": list(colours),
            "mana_tokens": list(colours), "is_land": False, "type_codes": ["Creature"],
            "mana_value": mana, "rarity": "common", "power": "2", "toughness": "2", "text": ""}


class GradeStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.grades = CommunityGrades(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_a_grade_round_trips(self):
        self.grades.save("TST", 101, 3.5, "premium common", "handle")
        stored = self.grades.grades("TST")
        self.assertEqual(stored[101]["grade"], 3.5)
        self.assertEqual(stored[101]["by"], "handle")

    def test_the_scale_is_enforced(self):
        with self.assertRaises(ValueError):
            self.grades.save("TST", 101, 7.0)
        with self.assertRaises(ValueError):
            self.grades.save("TST", 101, -1)

    def test_a_handle_cannot_carry_anything_but_a_handle(self):
        # It travels into a public file, so it is not a place for a sentence.
        with self.assertRaises(ValueError):
            self.grades.save("TST", 101, 3.0, "", "not a handle <script>")

    def test_export_is_the_file_the_repository_expects(self):
        self.grades.save("TST", 101, 3.0, "note", "handle")
        export = self.grades.export("TST")
        self.assertEqual(export["filename"], "community/set-reviews/TST.json")
        self.assertIn('"grade": 3.0', export["text"])
        self.assertEqual(export["licence"], "CC0-1.0")

    def test_removing_a_grade_leaves_the_file_valid(self):
        self.grades.save("TST", 101, 3.0)
        self.assertTrue(self.grades.remove("TST", 101))
        self.assertEqual(self.grades.grades("TST"), {})
        self.assertFalse(self.grades.remove("TST", 101))


class PreferenceTest(unittest.TestCase):
    """A measurement beats an opinion beats a heuristic, and the answer says which."""

    def setUp(self):
        self.cards = {n: card(f"Card {n}") for n in range(1, 25)}
        self.pool = list(range(1, 25))

    def test_without_anything_the_basis_is_the_card_text(self):
        answer = build.suggest(self.pool, self.cards)
        self.assertEqual(answer["basis"], "structure")
        self.assertIn("read off the cards themselves", answer["note"])

    def test_grades_take_over_when_they_cover_the_pool(self):
        grades = {n: {"grade": 3.0} for n in self.pool}
        answer = build.suggest(self.pool, self.cards, grades=grades)
        self.assertEqual(answer["basis"], "community")
        self.assertIn("opinions with a name on them", answer["note"])

    def test_a_measurement_outranks_a_grade(self):
        grades = {n: {"grade": 5.0} for n in self.pool}
        ratings = {n: {"gih_wr": 0.55, "gih_games": 900} for n in self.pool}
        answer = build.suggest(self.pool, self.cards, ratings=ratings, grades=grades)
        self.assertEqual(answer["basis"], "17lands")
        self.assertEqual(answer["note"], "")

    def test_a_handful_of_grades_does_not_take_over(self):
        grades = {1: {"grade": 5.0}, 2: {"grade": 4.5}}
        answer = build.suggest(self.pool, self.cards, grades=grades)
        self.assertEqual(answer["basis"], "structure")


if __name__ == "__main__":
    unittest.main()
