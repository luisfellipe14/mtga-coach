"""The set primer: what a colour pair is built around, read off the game's own files."""

import unittest

from mtga_coach import primer


def card(cid, name, colours, text="", rarity="common"):
    return {"id": cid, "name": name, "resolved": True, "is_token": False,
            "colors": list(colours), "color_identity": list(colours), "text": text,
            "rarity": rarity, "mana_value": 2, "is_land": False}


class MechanicTest(unittest.TestCase):
    def test_a_named_mechanic_is_the_word_before_the_dash(self):
        self.assertEqual(primer.mechanics("Opus — Whenever you cast an instant."), ["Opus"])
        self.assertEqual(primer.mechanics("Infusion — If you gained life this turn."), ["Infusion"])

    def test_evergreen_words_are_not_mechanics(self):
        self.assertEqual(primer.mechanics("Flying\nTrample"), [])
        self.assertEqual(primer.mechanics("When this creature enters, draw a card."), [])

    def test_a_sentence_that_merely_starts_with_a_capital_is_not_a_mechanic(self):
        self.assertEqual(primer.mechanics("Target creature gets +1/+1."), [])


class PairTest(unittest.TestCase):
    def build(self, cards):
        return primer.build({item["id"]: item for item in cards})

    def test_a_mechanic_belongs_to_the_colours_that_carry_it(self):
        """Most mechanics live on single-coloured cards, not on the gold ones."""
        cards = [card(index, f"Blue {index}", "U", "Opus — Whenever you cast a spell.")
                 for index in range(1, 5)]
        cards += [card(index, f"Red {index}", "R", "Opus — Whenever you cast a spell.")
                  for index in range(5, 9)]
        cards += [card(index, f"Gold {index}", "UR", "Draw a card.") for index in range(9, 12)]
        answer = self.build(cards)
        pair = next(item for item in answer["pairs"] if item["pair"] == "UR")
        self.assertEqual([theme["name"] for theme in pair["themes"]], ["Opus"])
        self.assertIn("built around Opus", pair["reading"])

    def test_a_pair_with_no_named_mechanic_falls_back_to_what_it_talks_about(self):
        cards = [card(index, f"WR {index}", "WR", "Return a creature card from your graveyard.")
                 for index in range(1, 6)]
        cards += [card(index, f"Blue {index}", "U", "Draw a card.") for index in range(6, 30)]
        pair = next(item for item in self.build(cards)["pairs"] if item["pair"] == "WR")
        self.assertEqual(pair["themes"], [])
        self.assertIn("the graveyard", pair["reading"])

    def test_a_pair_with_nothing_to_say_says_nothing(self):
        cards = [card(index, f"WG {index}", "WG", "Draw a card.") for index in range(1, 6)]
        cards += [card(index, f"Blue {index}", "U", "Draw a card.") for index in range(6, 30)]
        pair = next(item for item in self.build(cards)["pairs"] if item["pair"] == "WG")
        self.assertIn("a pile of good cards rather than a plan", pair["reading"])

    def test_a_mechanic_on_one_card_is_not_a_theme(self):
        cards = [card(1, "Alone", "U", "Solitude — Something happens.")]
        cards += [card(index, f"Blue {index}", "U", "Draw a card.") for index in range(2, 20)]
        answer = self.build(cards)
        self.assertEqual([m["name"] for m in answer["set_mechanics"]], [])


if __name__ == "__main__":
    unittest.main()
