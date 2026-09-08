"""Moments: facts about a turn, never a grade on a play."""

import unittest

from mtga_coach import moments


def card(cid, name, **extra):
    base = {"id": cid, "name": name, "resolved": True, "is_land": False, "mana_value": 2,
            "type_codes": ["Creature"], "text": "", "colors": ["R"]}
    base.update(extra)
    return base


def frame(index, turn, seat, hand=(), battlefield=(), active=None):
    return {"index": index, "turn": turn, "active_player": seat if active is None else active,
            "zones": [{"type": "Hand", "owner": seat,
                       "objects": [{"card_id": cid, "owner": seat, "controller": seat}
                                   for cid in hand]},
                      {"type": "Battlefield", "owner": 0,
                       "objects": [{"card_id": cid, "owner": seat, "controller": seat,
                                    "tapped": tapped}
                                   for cid, tapped in battlefield]}]}


class UnusedManaTest(unittest.TestCase):
    def setUp(self):
        self.cards = {1: card(1, "Mountain", is_land=True, mana_value=0, type_codes=["Land"]),
                      2: card(2, "Two Drop"),
                      3: card(3, "Bolt", type_codes=["Instant"], text="Deals 3 damage."),
                      4: card(4, "Aura", type_codes=["Enchantment"], text="Enchant creature"),
                      5: card(5, "Ambusher", text="Flash\nThis creature enters.")}

    def test_two_untapped_lands_and_a_two_drop_in_hand_is_a_moment(self):
        frames = [frame(0, 3, 1, hand=[2], battlefield=[(1, False), (1, False)])]
        found = moments.find(frames, 1, self.cards)
        self.assertEqual([item["kind"] for item in found if item["kind"] == "unused_mana"],
                         ["unused_mana"])

    def test_an_instant_held_is_never_a_moment(self):
        """Holding removal is the point of holding removal."""
        frames = [frame(0, 3, 1, hand=[3], battlefield=[(1, False), (1, False)])]
        self.assertEqual([item for item in moments.find(frames, 1, self.cards)
                          if item["kind"] == "unused_mana"], [])

    def test_a_creature_with_flash_is_never_a_moment(self):
        frames = [frame(0, 3, 1, hand=[5], battlefield=[(1, False), (1, False)])]
        self.assertEqual([item for item in moments.find(frames, 1, self.cards)
                          if item["kind"] == "unused_mana"], [])

    def test_an_aura_with_no_creature_on_the_board_could_not_be_cast(self):
        """Measured on real games: this was most of what the check used to find."""
        frames = [frame(0, 3, 1, hand=[4], battlefield=[(1, False), (1, False)])]
        self.assertEqual([item for item in moments.find(frames, 1, self.cards)
                          if item["kind"] == "unused_mana"], [])

    def test_the_same_aura_with_a_creature_on_the_board_is_a_moment(self):
        frames = [frame(0, 3, 1, hand=[4], battlefield=[(1, False), (1, False), (2, False)])]
        self.assertEqual([item["kind"] for item in moments.find(frames, 1, self.cards)
                          if item["kind"] == "unused_mana"], ["unused_mana"])

    def test_tapped_lands_are_not_mana_left_over(self):
        frames = [frame(0, 3, 1, hand=[2], battlefield=[(1, True), (1, True)])]
        self.assertEqual([item for item in moments.find(frames, 1, self.cards)
                          if item["kind"] == "unused_mana"], [])


class LandDropTest(unittest.TestCase):
    def setUp(self):
        self.cards = {1: card(1, "Mountain", is_land=True, mana_value=0, type_codes=["Land"]),
                      2: card(2, "Two Drop")}

    def test_a_land_in_hand_and_no_new_land_in_play_is_a_moment(self):
        frames = [frame(0, 2, 1, hand=[1], battlefield=[(1, True)]),
                  frame(1, 3, 1, hand=[1], battlefield=[(1, True)])]
        found = [item for item in moments.find(frames, 1, self.cards) if item["kind"] == "land_drop"]
        self.assertEqual([item["turn"] for item in found], [3])

    def test_playing_the_land_is_not_a_moment(self):
        frames = [frame(0, 2, 1, hand=[1], battlefield=[(1, True)]),
                  frame(1, 3, 1, hand=[], battlefield=[(1, True), (1, True)])]
        self.assertEqual([item for item in moments.find(frames, 1, self.cards)
                          if item["kind"] == "land_drop"], [])

    def test_the_opponents_turns_are_not_judged(self):
        frames = [frame(0, 2, 1, hand=[1], battlefield=[(1, True)], active=2)]
        self.assertEqual(moments.find(frames, 1, self.cards), [])


class SummaryTest(unittest.TestCase):
    def test_the_summary_refuses_to_grade(self):
        answer = moments.summarise([{"kind": "land_drop"}], 10)
        self.assertNotIn("accuracy", json_keys(answer))
        self.assertNotIn("score", json_keys(answer))
        self.assertIn("Turing-complete", answer["note"])


def json_keys(value):
    return " ".join(value).lower()


if __name__ == "__main__":
    unittest.main()


class ColourTest(unittest.TestCase):
    """Red lands behind white cards is not idle mana. Found by him in a real game."""

    def setUp(self):
        self.cards = {
            1: card(1, "Mountain", is_land=True, mana_value=0, type_codes=["Land"],
                    colors=["R"], color_identity=["R"]),
            2: card(2, "White Two Drop", mana_value=2, mana_tokens=["1", "W"],
                    colors=["W"], color_identity=["W"]),
            3: card(3, "Red Two Drop", mana_value=2, mana_tokens=["1", "R"],
                    colors=["R"], color_identity=["R"]),
            4: card(4, "Double Black", mana_value=2, mana_tokens=["B", "B"],
                    colors=["B"], color_identity=["B"]),
            5: card(5, "Swamp", is_land=True, mana_value=0, type_codes=["Land"],
                    colors=["B"], color_identity=["B"]),
        }

    def played(self, hand, battlefield):
        frames = [frame(0, 4, 1, hand=hand, battlefield=battlefield)]
        return [item for item in moments.find(frames, 1, self.cards)
                if item["kind"] == "unused_mana"]

    def test_a_white_card_behind_red_lands_is_not_a_moment(self):
        self.assertEqual(self.played([2], [(1, False), (1, False)]), [])

    def test_the_same_mana_with_a_red_card_is_a_moment(self):
        self.assertEqual(len(self.played([3], [(1, False), (1, False)])), 1)

    def test_two_black_pips_need_two_black_sources(self):
        self.assertEqual(self.played([4], [(5, False), (1, False)]), [])
        self.assertEqual(len(self.played([4], [(5, False), (5, False)])), 1)
