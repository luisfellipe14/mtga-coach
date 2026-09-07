"""Reading the packs. Arithmetic on his own draft, with no outside data anywhere in it."""

import unittest

from mtga_coach import signals


def card(name, colours, land=False):
    return {"name": name, "resolved": True, "is_land": land,
            "colors": list(colours), "color_identity": list(colours)}


class TallyTest(unittest.TestCase):
    def setUp(self):
        self.cards = {1: card("Red One", "R"), 2: card("Blue One", "U"),
                      3: card("Gold", "UR"), 4: card("Land", "", land=True),
                      5: card("Colourless", "")}

    def test_a_gold_card_counts_once_for_each_of_its_colours(self):
        counts, total = signals._tally([[3]], self.cards)
        self.assertEqual((counts["U"], counts["R"]), (1, 1))
        # It is one card, so the denominator sees it once. Otherwise every gold card
        # would inflate the share of both its colours against itself.
        self.assertEqual(total, 1)

    def test_lands_and_colourless_cards_are_not_a_colour_signal(self):
        counts, total = signals._tally([[4, 5]], self.cards)
        self.assertEqual(total, 0)
        self.assertEqual(sum(counts.values()), 0)


class ReadingTest(unittest.TestCase):
    def setUp(self):
        self.cards = {index: card(f"Red {index}", "R") for index in range(1, 40)}
        self.cards.update({index: card(f"Blue {index}", "U") for index in range(40, 80)})

    def picks(self, packs):
        return [{"pack": 1, "pick": position + 1, "pack_cards": pack, "card_id": pack[0]}
                for position, pack in enumerate(packs)]

    def test_a_colour_running_above_its_own_baseline_is_named(self):
        early = [[40, 41, 42, 43]] * 4          # blue early, so blue is the baseline
        late = [[1, 2, 3, 4, 5, 6, 7, 8]] * 6   # red late, so red is what is coming round
        answer = signals.read(self.picks(early + late), self.cards)
        pack = answer["packs"][0]
        self.assertTrue(pack["enough"])
        self.assertEqual(pack["colours"][0]["colour"], "R")
        self.assertIn("Red was clearly coming round", pack["reading"])

    def test_too_few_cards_refuses_to_read_a_signal(self):
        answer = signals.read(self.picks([[1, 2], [40, 41], [1], [40], [1, 2]]), self.cards)
        pack = answer["packs"][0]
        self.assertFalse(pack["enough"])
        self.assertIn("too few to read a signal", pack["reading"])

    def test_an_even_draft_says_nothing_stood_out(self):
        even = [[1, 40, 2, 41, 3, 42]] * 8
        answer = signals.read(self.picks(even), self.cards)
        self.assertIn("Nothing stood out", answer["packs"][0]["reading"])

    def test_the_baseline_is_the_draft_itself(self):
        # A set that simply prints more red must not read as red being open in every seat.
        allred = [[1, 2, 3, 4, 5, 6]] * 10
        answer = signals.read(self.picks(allred), self.cards)
        self.assertEqual(answer["baseline"]["R"], 1.0)
        self.assertIn("Nothing stood out", answer["packs"][0]["reading"])


class WheelTest(unittest.TestCase):
    def setUp(self):
        self.cards = {index: card(f"Card {index}", "R") for index in range(1, 30)}

    def test_a_card_that_comes_back_exactly_eight_picks_later_wheeled(self):
        picks = [{"pack": 1, "pick": 1, "pack_cards": [1, 2, 3], "card_id": 1},
                 {"pack": 1, "pick": 9, "pack_cards": [2, 4], "card_id": 2}]
        found = signals.wheeled(picks, self.cards)
        self.assertEqual([item["card_id"] for item in found], [2])
        self.assertEqual((found[0]["first_seen"], found[0]["came_back"]), (1, 9))

    def test_the_same_common_in_two_boosters_is_not_a_wheel(self):
        """Two different boosters holding the same common says nothing about the table."""
        picks = [{"pack": 1, "pick": 1, "pack_cards": [5], "card_id": 5},
                 {"pack": 1, "pick": 3, "pack_cards": [5], "card_id": 5}]
        self.assertEqual(signals.wheeled(picks, self.cards), [])

    def test_a_wheel_in_one_pack_is_not_a_wheel_in_another(self):
        picks = [{"pack": 1, "pick": 1, "pack_cards": [7], "card_id": 7},
                 {"pack": 2, "pick": 9, "pack_cards": [7], "card_id": 7}]
        self.assertEqual(signals.wheeled(picks, self.cards), [])


if __name__ == "__main__":
    unittest.main()
