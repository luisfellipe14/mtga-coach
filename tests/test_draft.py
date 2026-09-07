"""The draft reader and the pick advice.

The two dialects here are written the way Arena writes them: the bot draft numbers packs
and picks from zero and restates the pool, the human draft numbers from one and logs each
pick on its own. Both are exercised, because a reader that only understands one of them
is a reader that silently shows an empty pack for half the events in the client.
"""

import unittest

from mtga_coach import pick
from mtga_coach.draft import DraftTracker, card_ids, signature
from mtga_coach.service import limited_format


def record(payload, line=1):
    return {"line": line, "payload": payload}


def card(name, colours, tokens, **extra):
    base = {"id": extra.get("id", 0), "name": name, "resolved": True, "colors": list(colours),
            "color_identity": list(colours), "mana_tokens": list(tokens), "is_land": False,
            "type_codes": ["Creature"], "mana_value": len(tokens), "rarity": "common"}
    base.update(extra)
    return base


class CardIdsTest(unittest.TestCase):
    def test_reads_every_form_arena_writes(self):
        self.assertEqual(card_ids("101,102, 103"), [101, 102, 103])
        self.assertEqual(card_ids(["101", 102]), [101, 102])
        self.assertEqual(card_ids([{"grpId": 101}, {"CardId": "102"}]), [101, 102])
        self.assertEqual(card_ids(None), [])
        self.assertEqual(card_ids("not a card"), [])


class BotDraftTest(unittest.TestCase):
    def test_pack_and_pool_from_a_zero_based_dialect(self):
        tracker = DraftTracker()
        tracker.consume(record({"CurrentEventState": "PickNext", "Payload": {
            "DraftId": "bot-1", "EventName": "QuickDraft_HOB_20260901",
            "PackNumber": 0, "PickNumber": 0,
            "DraftPack": ["101", "102", "103"], "PickedCards": []}}))
        state = tracker.state()
        self.assertEqual(state["pack_cards"], [101, 102, 103])
        # Zero in the log, one on screen: nobody calls it pick zero.
        self.assertEqual((state["pack"], state["pick"]), (1, 1))
        self.assertTrue(state["zero_based"])
        self.assertEqual(state["event_name"], "QuickDraft_HOB_20260901")

        tracker.consume(record({"Payload": {
            "DraftId": "bot-1", "PackNumber": 0, "PickNumber": 1,
            "DraftPack": ["104", "105"], "PickedCards": ["102"]}}))
        state = tracker.state()
        self.assertEqual(state["pool"], [102])
        self.assertEqual((state["pack"], state["pick"]), (1, 2))

    def test_a_single_card_is_not_a_pack(self):
        tracker = DraftTracker()
        tracker.consume(record({"DraftId": "bot-1", "DraftPack": ["101"]}))
        self.assertEqual(tracker.state()["pack_cards"], [])


class HumanDraftTest(unittest.TestCase):
    def setUp(self):
        self.tracker = DraftTracker()
        self.tracker.consume(record({"draftId": "human-1", "SelfPack": 1, "SelfPick": 1,
                                     "PackCards": "201,202,203"}))

    def test_pack_is_one_based_when_no_zero_ever_arrives(self):
        state = self.tracker.state()
        self.assertEqual((state["pack"], state["pick"]), (1, 1))
        self.assertFalse(state["zero_based"])
        self.assertEqual(state["pack_cards"], [201, 202, 203])

    def test_pick_arrives_as_its_own_record(self):
        # The request the client sends when the player clicks a card, nested JSON and all.
        self.tracker.consume(record({"request": '{"method":"Draft.MakePick","params":'
                                                '{"draftId":"human-1","cardId":"202",'
                                                '"packNumber":"1","pickNumber":"1"}}'}))
        state = self.tracker.state()
        self.assertEqual(state["pool"], [202])
        self.assertEqual(state["picks"][0]["card_id"], 202)
        self.assertEqual(state["picks"][0]["pack_cards"], [201, 202, 203])

    def test_the_same_pick_twice_is_recorded_once(self):
        payload = {"draftId": "human-1", "cardId": 202, "packNumber": 1, "pickNumber": 1}
        self.tracker.consume(record(payload))
        self.tracker.consume(record(dict(payload)))
        self.assertEqual(len(self.tracker.state()["picks"]), 1)

    def test_a_new_draft_does_not_inherit_the_previous_pool(self):
        self.tracker.consume(record({"draftId": "human-1", "cardId": 202,
                                     "packNumber": 1, "pickNumber": 1}))
        self.tracker.consume(record({"draftId": "human-2", "SelfPack": 1, "SelfPick": 1,
                                     "PackCards": "301,302"}))
        state = self.tracker.state()
        self.assertEqual(state["pool"], [])
        self.assertEqual(state["pack_cards"], [301, 302])


class DiagnosticsTest(unittest.TestCase):
    def test_an_unknown_draft_shape_is_kept_as_key_names_only(self):
        tracker = DraftTracker()
        tracker.consume(record({"DraftSomethingNew": {"secretId": "abc", "value": 3}}))
        shapes = tracker.state()["unrecognised_shapes"]
        self.assertEqual(shapes, ["DraftSomethingNew[secretId,value]"])
        self.assertNotIn("abc", shapes[0])

    def test_signature_carries_no_values(self):
        self.assertEqual(signature({"b": 1, "a": {"c": "secret"}}), "a[c],b")


class AdviceTest(unittest.TestCase):
    def setUp(self):
        self.cards = {
            1: card("Red Bomb", "R", ["R", "R", "2"], id=1, rarity="mythic"),
            2: card("Blue Playable", "U", ["U", "1"], id=2),
            3: card("Colourless Thing", "", ["3"], id=3),
            4: card("Blue Filler", "U", ["U", "2"], id=4),
        }
        self.ratings = {
            1: {"name": "Red Bomb", "gih_wr": 0.615, "gih_games": 9000, "alsa": 1.4},
            2: {"name": "Blue Playable", "gih_wr": 0.575, "gih_games": 8000, "alsa": 4.2},
            3: {"name": "Colourless Thing", "gih_wr": 0.548, "gih_games": 7000, "alsa": 9.6},
        }

    def test_the_best_card_wins_when_the_pool_is_empty(self):
        advice = pick.advise([1, 2, 3], [], self.ratings, self.cards, pick_number=1)
        self.assertEqual(advice["pick"], 1)
        self.assertEqual(advice["pick_name"], "Red Bomb")
        self.assertEqual(advice["commitment"], 0.0)
        # With no pool there is nothing to adjust for, so the order is 17Lands' own.
        self.assertEqual([item["card_id"] for item in advice["ranked"]], [1, 2, 3])
        self.assertEqual([item["colour_adjustment"] for item in advice["ranked"]], [0.0, 0.0, 0.0])

    def test_a_committed_pool_moves_the_pick_into_its_colours(self):
        pool = [2, 4] * 6  # twelve blue picks: the pool has said what it is
        advice = pick.advise([1, 2, 3], pool, self.ratings, self.cards, pick_number=13)
        self.assertEqual(advice["lane"], ["U"])
        self.assertEqual(advice["commitment"], 1.0)
        self.assertEqual(advice["pick"], 2)
        red = next(item for item in advice["ranked"] if item["card_id"] == 1)
        self.assertEqual(red["colour_adjustment"], -pick.OFF_COLOUR_PENALTY)
        colourless = next(item for item in advice["ranked"] if item["card_id"] == 3)
        self.assertEqual(colourless["colour_adjustment"], 0.0)

    def test_an_early_off_colour_bomb_still_wins(self):
        advice = pick.advise([1, 2], [2, 4], self.ratings, self.cards, pick_number=3)
        self.assertEqual(advice["pick"], 1)

    def test_a_card_without_a_published_rate_is_named_not_ranked(self):
        advice = pick.advise([1, 4], [], self.ratings, self.cards, pick_number=1)
        self.assertEqual([item["card_id"] for item in advice["unrated"]], [4])
        self.assertTrue(any("no published rate" in note for note in advice["notes"]))

    def test_two_cards_within_the_margin_are_declared_a_close_call(self):
        ratings = dict(self.ratings)
        ratings[2] = {**ratings[2], "gih_wr": 0.613}
        advice = pick.advise([1, 2], [], ratings, self.cards, pick_number=1)
        self.assertEqual(advice["close_calls"], [2])

    def test_a_wheel_is_pointed_out_while_it_is_still_possible(self):
        advice = pick.advise([3], [], self.ratings, self.cards, pick_number=1)
        self.assertTrue(any("wheel" in reason for reason in advice["ranked"][0]["why"]))


class LimitedFormatTest(unittest.TestCase):
    def test_event_names_map_to_the_table_that_describes_them(self):
        self.assertEqual(limited_format("QuickDraft_HOB_20260901"), "QuickDraft")
        self.assertEqual(limited_format("PremierDraft_HOB"), "PremierDraft")
        self.assertEqual(limited_format("TradDraft_HOB"), "TradDraft")
        self.assertEqual(limited_format("Sealed_HOB"), "Sealed")
        self.assertEqual(limited_format(""), "PremierDraft")


if __name__ == "__main__":
    unittest.main()
