import tempfile
import unittest
from pathlib import Path

from mtga_coach.storage import ReviewStore


def game(game_id: str = "game-a", **overrides: object) -> dict:
    value = {
        "id": game_id,
        "match_id_hashed": "match-safe",
        "game_number": 1,
        "mode": "BO1",
        "format": "Alchemy",
        "event_id": "event-safe",
        "deck_id": "deck-a",
        "registered_deck_id": "deck-a",
        "result": "win",
        "match_result": "win",
        "status": "complete",
        "turns": 5,
        "decision_count": 1,
        "quality": "complete",
        "self_seat": 1,
        "deck": {"main": [{"id": 1, "quantity": 4}], "sideboard": []},
        "frames": [
            {
                "index": 0,
                "state_id": "state-0",
                "turn": 1,
                "phase": "Main1",
                "step": "PrecombatMain",
                "active_player": 1,
                "priority_player": 1,
                "players": [{"seat": 1, "life": 20, "is_self": True}],
                "zones": [],
                "action": None,
                "actions": [],
                "available_actions": [{"type": "cast", "card_ids": [1]}],
                "quality": "complete",
                "warnings": [],
                "source_line": 1,
            }
        ],
        "source_sha256": "source-a",
        "timestamp": "2026-09-06T00:00:00Z",
    }
    value.update(overrides)
    return value


class ReviewStoreTests(unittest.TestCase):
    def test_reimport_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            snapshot = {"source_sha256": "source-a", "record_count": 1, "warnings": [], "games": [game()]}

            first = store.save_import(snapshot, "upload", 10)
            second = store.save_import(snapshot, "upload", 10)

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(store.import_count(), 1)
            self.assertEqual([item["id"] for item in store.games()], ["game-a"])
            store.close()

    def test_partial_reimport_preserves_completed_game_and_merges_new_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            try:
                complete = game(frames=[game()["frames"][0], {**game()["frames"][0], "index": 1, "state_id": "state-1"}])
                store.save_import({"source_sha256": "source-complete", "record_count": 2, "warnings": [], "games": [complete]}, "upload", 20)
                partial = game(result="unknown", status="in_progress", source_sha256="source-partial", frames=[game()["frames"][0]])
                store.save_import({"source_sha256": "source-partial", "record_count": 1, "warnings": [], "games": [partial]}, "upload", 10)

                after_partial = store.game("game-a")
                self.assertEqual(after_partial["result"], "win")
                self.assertEqual([frame["state_id"] for frame in after_partial["frames"]], ["state-0", "state-1"])

                reconnect = game(result="unknown", status="in_progress", source_sha256="source-growing", frames=[
                    {**game()["frames"][0], "index": 0, "state_id": "state-1"},
                    {**game()["frames"][0], "index": 1, "state_id": "state-2"},
                ])
                store.save_import({"source_sha256": "source-growing", "record_count": 2, "warnings": [], "games": [reconnect]}, "upload", 20)

                merged = store.game("game-a")
                self.assertEqual(merged["result"], "win")
                self.assertEqual([(frame["index"], frame["state_id"]) for frame in merged["frames"]], [(0, "state-0"), (1, "state-1"), (2, "state-2")])
            finally:
                store.close()


class MergeTests(unittest.TestCase):
    def test_reimport_upgrades_a_state_already_stored_instead_of_discarding_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            try:
                poor = game(frames=[{**game()["frames"][0], "events": []}])
                store.save_import({"source_sha256": "antigo", "record_count": 1, "warnings": [], "games": [poor]}, "upload", 10)
                richer = game(frames=[{**game()["frames"][0], "events": [{"kind": "draw", "card_id": 1}]}])
                store.save_import({"source_sha256": "novo", "record_count": 1, "warnings": [], "games": [richer]}, "upload", 10)

                frames = store.game("game-a")["frames"]
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0]["events"], [{"kind": "draw", "card_id": 1}])
                self.assertEqual([item["kind"] for item in store.events("game-a")], ["draw"])
            finally:
                store.close()
