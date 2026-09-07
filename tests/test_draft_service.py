"""The draft as it travels: log record, ingestor, database, service answer.

The point of this file is the seam. The tracker is tested on its own elsewhere; here the
question is whether a pack read during a session is still there after the flush, which is
the only reason the draft is written to the database at all — the log will be gone.
"""

import json
import tempfile
import unittest
from pathlib import Path

from mtga_coach.ingest import LogIngestor
from mtga_coach.service import CoachService
from mtga_coach.storage import ReviewStore

PACK = {"DraftId": "draft-1", "EventName": "QuickDraft_TST_20260901", "PackNumber": 0,
        "PickNumber": 2, "DraftPack": ["1001", "1002", "1003"], "PickedCards": ["1004", "1005"]}


def line(payload):
    return ("[UnityCrossThreadLogger]<== BotDraft_DraftStatus "
            + json.dumps(payload) + "\n").encode("utf-8")


class DraftIngestTest(unittest.TestCase):
    def test_a_draft_line_survives_the_scanner(self):
        ingestor = LogIngestor("test")
        ingestor.feed(line(PACK), final=True)
        state = ingestor.snapshot()["draft"]
        self.assertEqual(state["pack_cards"], [1001, 1002, 1003])
        self.assertEqual(state["pool"], [1004, 1005])
        self.assertEqual((state["pack"], state["pick"]), (1, 3))

    def test_a_log_without_a_draft_reports_none(self):
        ingestor = LogIngestor("test")
        ingestor.feed(b'{"matchGameRoomStateChangedEvent": {}}\n', final=True)
        self.assertIsNone(ingestor.snapshot()["draft"])


class DraftStorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.directory.name))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_the_draft_outlives_the_session_that_read_it(self):
        ingestor = LogIngestor("test")
        ingestor.feed(line(PACK), final=True)
        self.store.absorb_session(ingestor.snapshot())
        stored = self.store.latest_draft()
        self.assertEqual(stored["draft_id"], "draft-1")
        self.assertEqual(stored["pack_cards"], [1001, 1002, 1003])
        self.assertEqual(stored["pool"], [1004, 1005])
        self.assertEqual(stored["event_name"], "QuickDraft_TST_20260901")

    def test_a_later_flush_replaces_the_earlier_position(self):
        first = LogIngestor("test")
        first.feed(line(PACK), final=True)
        self.store.absorb_session(first.snapshot())
        later = dict(PACK, PickNumber=5, DraftPack=["1006", "1007"], PickedCards=["1004", "1005", "1003"])
        second = LogIngestor("test")
        second.feed(line(later), final=True)
        self.store.absorb_session(second.snapshot())
        stored = self.store.latest_draft()
        self.assertEqual(stored["pack_cards"], [1006, 1007])
        self.assertEqual(stored["pool"], [1004, 1005, 1003])


class DraftServiceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name)
        self.service = CoachService(data_dir=path, card_database_path=path / "absent.mtga")
        ingestor = LogIngestor("test")
        ingestor.feed(line(PACK), final=True)
        self.service.store.absorb_session(ingestor.snapshot())

    def tearDown(self):
        self.service.store.close()
        self.directory.cleanup()

    def test_without_a_ratings_table_the_pack_is_shown_and_the_gap_declared(self):
        answer = self.service.draft()
        self.assertTrue(answer["active"])
        self.assertFalse(answer["live"])
        self.assertIsNone(answer["advice"])
        self.assertTrue(answer["ratings"]["missing"])
        self.assertEqual(answer["limited_event"], "QuickDraft")
        self.assertEqual(answer["pack_cards"], [1001, 1002, 1003])

    def test_with_a_stored_table_the_pack_is_ranked(self):
        path = self.service.limited.path_for("TST", "QuickDraft")
        path.write_text(json.dumps({
            "expansion": "TST", "event": "QuickDraft", "fetched_at": "2026-09-07T00:00:00+00:00",
            "cards": {"1001": {"name": "One", "gih_wr": 0.52, "gih_games": 900, "alsa": 5.0},
                      "1002": {"name": "Two", "gih_wr": 0.60, "gih_games": 900, "alsa": 2.0}}}),
            encoding="utf-8")
        # The set is read off the cards, which an absent catalogue cannot supply, so the
        # answer is asked for the set the table was stored under.
        self.service._cards.update({cid: {"id": cid, "name": f"Card {cid}", "resolved": True,
                                          "set": "TST", "colors": [], "color_identity": [],
                                          "mana_tokens": [], "is_land": False, "type_codes": [],
                                          "mana_value": 2, "rarity": "common"}
                                    for cid in (1001, 1002, 1003, 1004, 1005)})
        answer = self.service.draft()
        self.assertEqual(answer["expansion"], "TST")
        self.assertEqual(answer["advice"]["pick"], 1002)
        self.assertEqual([item["card_id"] for item in answer["advice"]["unrated"]], [1003])

    def test_the_pool_exports_as_a_deck_list(self):
        self.service._cards.update({cid: {"id": cid, "name": f"Card {cid}", "resolved": True,
                                          "set": "TST", "collector_number": str(cid),
                                          "colors": [], "color_identity": [], "mana_tokens": [],
                                          "is_land": False, "type_codes": [], "mana_value": 2,
                                          "rarity": "common"}
                                    for cid in (1004, 1005)})
        export = self.service.draft_pool_export()
        self.assertEqual(export["cards"], 2)
        self.assertIn("Card 1004", export["text"])

    def test_with_no_draft_at_all_the_answer_says_what_to_do(self):
        empty = tempfile.TemporaryDirectory()
        service = CoachService(data_dir=Path(empty.name), card_database_path=Path(empty.name) / "x")
        answer = service.draft()
        self.assertFalse(answer["active"])
        self.assertIn("Follow matches", answer["hint"])
        service.store.close()
        empty.cleanup()


if __name__ == "__main__":
    unittest.main()
