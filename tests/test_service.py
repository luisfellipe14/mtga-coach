import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mtga_coach.service import CoachService

from tests.test_storage import game


def importer(_: bytes) -> dict:
    return {"source_sha256": "source-a", "record_count": 3, "warnings": ["partial tail"], "games": []}


class CoachServiceTests(unittest.TestCase):
    def make_service(self, games: list[dict]) -> CoachService:
        self.directory = tempfile.TemporaryDirectory()

        def import_games(_: bytes) -> dict:
            return {"source_sha256": "source-a", "record_count": len(games), "warnings": [], "games": games}

        self.service = CoachService(data_dir=Path(self.directory.name), importer=import_games)
        return self.service

    def tearDown(self) -> None:
        directory = getattr(self, "directory", None)
        if directory is not None:
            self.service.store.close()
            directory.cleanup()

    def test_summary_excludes_in_progress_from_result_denominator(self) -> None:
        service = self.make_service([game("won", result="win"), game("open", result="unknown", status="in_progress")])
        service.import_upload(b"snapshot")

        summary = service.summary()

        self.assertEqual(summary["games"], 2)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["win_rate"], 1.0)

    def test_bo3_keeps_match_and_game_results_separate(self) -> None:
        service = self.make_service([
            game("bo3-1", match_id_hashed="bo3-safe", mode="BO3", game_number=1, result="loss", match_result="win"),
            game("bo3-2", match_id_hashed="bo3-safe", mode="BO3", game_number=2, result="win", match_result="win"),
        ])
        service.import_upload(b"snapshot")

        detail = service.game_detail("bo3-1")
        summary = service.summary()

        self.assertEqual(detail["result"], "loss")
        self.assertEqual(detail["match_result"], "win")
        self.assertEqual(summary["matches"], 1)
        self.assertEqual(summary["by_mode"]["BO3"]["games"], 2)
        self.assertEqual(summary["by_mode"]["BO3"]["matches"], 1)
        self.assertEqual(summary["by_mode"]["BO3"]["match_wins"], 1)
        self.assertEqual(summary["by_mode"]["BO3"]["by_stage"]["game1"]["losses"], 1)
        self.assertEqual(summary["by_mode"]["BO3"]["by_stage"]["post_sideboard"]["wins"], 1)

    def test_post_sideboard_configuration_is_not_experiment_version(self) -> None:
        sideboarded = game("bo3-2", mode="BO3", game_number=2, deck={"main": [{"id": 1, "quantity": 3}], "sideboard": [{"id": 2, "quantity": 1}]})
        service = self.make_service([game("bo3-1", mode="BO3"), sideboarded])
        service.import_upload(b"snapshot")

        self.assertEqual(len(service.experiments()), 0)

    def test_context_excludes_future_reveal(self) -> None:
        frames = [game()["frames"][0], {**game()["frames"][0], "index": 1, "state_id": "state-1", "zones": [{"type": "hand", "objects": [{"card_id": 999}]}], "action": {"type": "cast", "card_ids": [999]}}]
        prior = game("game-prior", match_id_hashed="secret-match", mode="BO3", game_number=1, frames=[{**game()["frames"][0], "zones": [{"type": "battlefield", "owner": 2, "objects": [{"card_id": 777, "owner": 2}]}]}])
        current = game(frames=frames, match_id_hashed="secret-match", mode="BO3", game_number=2, result="win")
        service = self.make_service([prior, current])
        service.import_upload(b"snapshot")

        context = service.decision_context("game-a", 0)

        self.assertTrue(context["eligible"])
        self.assertNotIn("secret-match", context["text"])
        self.assertNotIn("999", context["text"])
        self.assertEqual(len(context["context"]["frames"]), 1)
        self.assertEqual(context["context"]["origin"]["state_id"], "state-0")
        self.assertEqual(context["context"]["self_seat"], 1)
        self.assertEqual(context["context"]["prior_match_reveals"], [{"card_id": 777, "knowledge": "historical_prior_match"}])

    def test_experiment_accepts_manual_text_changes_only_for_observed_deck(self) -> None:
        service = self.make_service([game()])
        service.import_upload(b"snapshot")

        experiment = service.save_experiment({"deck_id": "deck-a", "title": "Curva", "hypothesis": "Testar mana", "changes": "-1 terreno", "status": "planned"})

        self.assertEqual(experiment["changes"], "-1 terreno")
        with self.assertRaises(ValueError):
            service.save_experiment({"deck_id": "missing", "title": "X", "hypothesis": "Y", "changes": "Z", "status": "planned"})
        with self.assertRaises(ValueError):
            service.save_experiment({"deck_id": "deck-a", "title": "X", "hypothesis": "Y", "changes": "Z", "status": "unsupported"})

    def test_import_enforces_limit_and_does_not_persist_raw_snapshot(self) -> None:
        service = self.make_service([game()])
        source = Path(self.directory.name) / "Player.log"
        source.write_bytes(b"abc")
        service.log_path = source

        with patch("mtga_coach.service.MAX_LOG_BYTES", 2):
            with self.assertRaises(ValueError):
                service.import_configured()
            with self.assertRaises(ValueError):
                service.import_upload(b"abc")

        result = service.import_configured()
        self.assertTrue(result["created"])
        self.assertFalse((Path(self.directory.name) / "sources").exists())

    def test_context_uses_card_owner_and_exports_known_deck_composition(self) -> None:
        prior = game("game-prior", match_id_hashed="match-safe", mode="BO3", game_number=1, frames=[{**game()["frames"][0], "zones": [{"type": "battlefield", "owner": 0, "objects": [{"card_id": 777, "owner": 2}, {"card_id": 778, "owner": 1}]}]}])
        current = game("game-current", match_id_hashed="match-safe", mode="BO3", game_number=2)
        unknown_self = game("unknown-self", self_seat=0)
        service = self.make_service([prior, current, unknown_self])
        service.import_upload(b"snapshot")

        context = service.decision_context("game-current", 0)

        self.assertEqual(context["context"]["deck"], current["deck"])
        self.assertEqual(context["context"]["prior_match_reveals"], [{"card_id": 777, "knowledge": "historical_prior_match"}])
        self.assertFalse(service.decision_context("unknown-self", 0)["eligible"])


class LateDeckTest(unittest.TestCase):
    """The client announces the deck before the room it belongs to."""

    @staticmethod
    def line(payload):
        return (json.dumps(payload) + "\n").encode("utf-8")

    def snapshot(self, records):
        from mtga_coach.ingest import LogIngestor

        ingestor = LogIngestor("test")
        for record in records:
            ingestor.feed(self.line(record))
        ingestor.finish()
        return ingestor.snapshot()

    def connect(self, cards):
        return {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_ConnectResp", "systemSeatIds": [1],
            "connectResp": {"deckMessage": {"deckCards": cards}}}]}}

    def room(self, match_id):
        return {"matchGameRoomStateChangedEvent": {"gameRoomInfo": {"gameRoomConfig": {
            "matchId": match_id, "reservedPlayers": [
                {"userId": "me", "systemSeatId": 1, "teamId": 1, "eventId": "Ladder"}]}}}}

    def state(self, match_id, state_id):
        return {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_GameStateMessage", "gameStateId": state_id,
            "gameStateMessage": {"gameStateId": state_id, "type": "GameStateType_Full",
                                 "gameInfo": {"matchID": match_id, "gameNumber": 1,
                                              "stage": "GameStage_Start"},
                                 "turnInfo": {"turnNumber": 1, "activePlayer": 1},
                                 "players": [{"systemSeatNumber": 1, "lifeTotal": 20, "teamId": 1}],
                                 "zones": [], "gameObjects": []}}]}}

    def test_a_deck_announced_before_its_room_still_reaches_the_game(self):
        cards = [101] * 20 + [102] * 20
        snapshot = self.snapshot([
            self.room("first"), self.state("first", 1),
            # The measured order: connect, then the room of the match it belongs to.
            self.connect(cards), self.room("second"), self.state("second", 2),
        ])
        games = {game["id"]: game for game in snapshot["games"]}
        second = next(game for game in games.values() if len(game["deck"]["main"]) > 0)
        self.assertEqual(sum(item["quantity"] for item in second["deck"]["main"]), 40)

    def test_the_pending_deck_does_not_land_on_the_previous_match(self):
        cards = [101] * 40
        snapshot = self.snapshot([
            self.room("first"), self.state("first", 1),
            self.connect(cards), self.room("second"), self.state("second", 2),
        ])
        first = next(game for game in snapshot["games"] if game["match_id_hashed"] !=
                     next(g["match_id_hashed"] for g in snapshot["games"] if g["deck"]["main"]))
        self.assertEqual(first["deck"]["main"], [])
