"""Service-level checks on the deck, library and sample analysis built from a real-shaped log."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mtga_coach.service import CoachService

# Two black one-drops, one blue three-drop, and lands: enough to exercise the mana base.
SWAMP, ISLAND, THOUGHTSEIZE, COUNTERSPELL = 90001, 90002, 90003, 90004
DECK = [SWAMP] * 10 + [ISLAND] * 4 + [THOUGHTSEIZE] * 4 + [COUNTERSPELL] * 4


def room(match, user="me"):
    return json.dumps({"matchGameRoomStateChangedEvent": {"gameRoomInfo": {"gameRoomConfig": {
        "matchId": match,
        "reservedPlayers": [{"eventId": "Historic_Ladder", "systemSeatId": 2, "teamId": 2,
                             "userId": user, "playerName": "Eu"},
                            {"eventId": "Historic_Ladder", "systemSeatId": 1, "teamId": 1,
                             "userId": "outro", "playerName": "Adversário"}]}}}})


def zone(zone_id, kind, owner, ids):
    return {"zoneId": zone_id, "type": f"ZoneType_{kind}", "ownerSeatId": owner, "objectInstanceIds": ids}


def obj(instance_id, card_id, zone_id, owner):
    return {"instanceId": instance_id, "grpId": card_id, "zoneId": zone_id, "ownerSeatId": owner,
            "controllerSeatId": owner, "visibility": "Visibility_Public"}


def game_states(match, hand_cards, opponent_cards, won):
    hand_objects = [obj(10 + index, card, 5, 2) for index, card in enumerate(hand_cards)]
    opponent_objects = [obj(50 + index, card, 6, 1) for index, card in enumerate(opponent_cards)]
    full = {"type": "GameStateType_Full", "gameStateId": 1,
            "gameInfo": {"matchID": match, "gameNumber": 1, "stage": "GameStage_Play",
                         "matchWinCondition": "MatchWinCondition_SingleElimination",
                         "superFormat": "SuperFormat_Constructed"},
            "turnInfo": {"turnNumber": 1, "activePlayer": 2, "phase": "Phase_Main1"},
            "players": [{"systemSeatNumber": 2, "lifeTotal": 20, "teamId": 2},
                        {"systemSeatNumber": 1, "lifeTotal": 20, "teamId": 1}],
            "zones": [zone(5, "Hand", 2, [item["instanceId"] for item in hand_objects]),
                      zone(6, "Battlefield", 0, [item["instanceId"] for item in opponent_objects]),
                      zone(7, "Library", 2, list(range(200, 200 + 40)))],
            "gameObjects": hand_objects + opponent_objects}
    over = {"type": "GameStateType_Diff", "gameStateId": 2, "prevGameStateId": 1,
            "annotations": [{"id": 1, "affectedIds": [10], "type": ["AnnotationType_ZoneTransfer"],
                             "details": [{"key": "zone_src", "type": "KeyValuePairValueType_int32", "valueInt32": [7]},
                                         {"key": "zone_dest", "type": "KeyValuePairValueType_int32", "valueInt32": [5]},
                                         {"key": "category", "type": "KeyValuePairValueType_string", "valueString": ["Draw"]}]}],
            "gameInfo": {"matchID": match, "gameNumber": 1, "stage": "GameStage_GameOver",
                         "results": [{"scope": "MatchScope_Game", "result": "ResultType_WinLoss",
                                      "winningTeamId": 2 if won else 1},
                                     {"scope": "MatchScope_Match", "result": "ResultType_WinLoss",
                                      "winningTeamId": 2 if won else 1}]}}
    return [{"type": "GREMessageType_GameStateMessage", "systemSeatIds": [2], "gameStateId": body["gameStateId"],
             "gameStateMessage": body} for body in (full, over)]


def log(matches):
    auth = json.dumps({"authenticateResponse": {"clientId": "me", "screenName": "Eu"}})
    connect = {"type": "GREMessageType_ConnectResp", "systemSeatIds": [2],
               "connectResp": {"deckMessage": {"deckCards": DECK, "sideboardCards": []}}}
    lines = [auth]
    for match, hand, opponent, won in matches:
        lines.append(room(match))
        lines.append(json.dumps({"greToClientEvent": {"greToClientMessages":
                                                      [connect, *game_states(match, hand, opponent, won)]}}))
    return ("\n".join(lines) + "\n").encode()


CARDS = {
    str(SWAMP): {"id": SWAMP, "name": "Pântano", "resolved": True, "mana_value": 0, "colors": ["B"],
                 "is_land": True, "mana_cost": "", "mana_tokens": [], "type_line": "Terreno", "rarity": "basic"},
    str(ISLAND): {"id": ISLAND, "name": "Ilha", "resolved": True, "mana_value": 0, "colors": ["U"],
                  "is_land": True, "mana_cost": "", "mana_tokens": [], "type_line": "Terreno", "rarity": "basic"},
    str(THOUGHTSEIZE): {"id": THOUGHTSEIZE, "name": "Capturar Pensamento", "resolved": True, "mana_value": 1,
                        "colors": ["B"], "is_land": False, "mana_cost": "{B}", "mana_tokens": ["B"], "type_line": "Feitiço",
                        "rarity": "rare"},
    str(COUNTERSPELL): {"id": COUNTERSPELL, "name": "Anular", "resolved": True, "mana_value": 3,
                        "colors": ["U"], "is_land": False, "mana_cost": "{1}{U}{U}", "mana_tokens": ["1", "U", "U"], "type_line": "Instantânea",
                        "rarity": "uncommon"},
}


class ServiceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = CoachService(data_dir=Path(self.directory.name))
        # The installed Arena database is not a test dependency: the catalogue is stubbed.
        self.service.cards = lambda ids: {str(cid): CARDS[str(cid)] for cid in ids if str(cid) in CARDS}
        self.service.import_upload(log([
            ("partida-1", [THOUGHTSEIZE, SWAMP], [ISLAND], True),
            ("partida-2", [COUNTERSPELL, SWAMP], [SWAMP, THOUGHTSEIZE], False),
        ]))

    def tearDown(self):
        self.service.store.close()
        self.directory.cleanup()

    def deck_id(self):
        return self.service.store.games()[0]["deck_id"]

    @staticmethod
    def game_id(match):
        return f"{hashlib.sha256(match.encode()).hexdigest()[:16]}-1"

    def test_mode_and_start_come_from_the_protocol_not_from_the_event_name(self):
        games = self.service.games()["games"]
        self.assertEqual({game["mode"] for game in games}, {"BO1"})
        self.assertEqual({game["mode_basis"] for game in games}, {"protocolo"})
        self.assertTrue(all(game["on_play"] is True for game in games))
        summary = self.service.summary()
        self.assertEqual(summary["by_start"]["on_play"]["games"], 2)
        self.assertEqual(summary["by_start"]["on_draw"]["games"], 0)

    def test_summary_reports_an_interval_beside_every_rate(self):
        summary = self.service.summary()
        self.assertEqual((summary["wins"], summary["losses"]), (1, 1))
        self.assertEqual(summary["interval"]["n"], 2)
        self.assertLess(summary["interval"]["low"], 0.5)
        self.assertGreater(summary["interval"]["high"], 0.5)

    def test_deck_report_names_the_colour_shortfall_and_the_card_that_causes_it(self):
        report = self.service.deck_report(self.deck_id())
        self.assertEqual(report["lands"], 14)
        self.assertEqual(report["sources"], {"B": 10, "U": 4})
        self.assertEqual(report["curve"], {1: 4, 3: 4})
        blue = next(item for item in report["colour_requirements"] if item["colour"] == "U")
        self.assertEqual((blue["pips"], blue["turn"], blue["needed"], blue["have"]), (2, 3, 18, 4))
        self.assertEqual(blue["driver"], "Anular")
        self.assertEqual(report["wildcards"]["cost"], {"common": 0, "uncommon": 4, "rare": 4, "mythic": 0})
        self.assertIn("Karsten", report["karsten_citation"])

    def test_card_statistics_carry_the_interval_and_the_required_sample(self):
        stats = self.service.deck_report(self.deck_id())["card_stats"]
        rows = {row["name"]: row for row in stats["rows"]}
        self.assertEqual(rows["Capturar Pensamento"]["games_in_hand"], 1)
        self.assertEqual(rows["Capturar Pensamento"]["wins"], 1)
        self.assertEqual(rows["Anular"]["wins"], 0)
        self.assertEqual(rows["Capturar Pensamento"]["interval"]["n"], 1)
        self.assertGreater(stats["games_to_detect_five_points"], 1000)

    def test_library_subtracts_every_copy_already_seen_and_answers_draw_odds(self):
        game_id = self.game_id("partida-1")
        library = self.service.library_state(game_id, 0)
        self.assertTrue(library["eligible"])
        # Two of the twenty-two known cards are visible in hand at this frame.
        self.assertEqual(library["size"], len(DECK) - 2)
        swamp = next(item for item in library["entries"] if item["name"] == "Pântano")
        self.assertEqual(swamp["quantity"], 9)
        self.assertAlmostEqual(swamp["next_draw"], 9 / 20)

    def test_opponent_profile_lists_what_was_shown_and_refuses_to_name_an_archetype(self):
        game_id = self.game_id("partida-1")
        profile = self.service.opponent_profile(game_id)
        self.assertEqual([card["name"] for card in profile["cards"]], ["Ilha"])
        self.assertEqual(profile["colours"], {"U": 1})
        self.assertIn("não nomeia um arquétipo", profile["note"])

    def test_frames_are_served_in_pages_and_an_unknown_game_is_refused(self):
        game_id = self.game_id("partida-1")
        page = self.service.frames(game_id, 0, 1)
        self.assertEqual(page["count"], 1)
        self.assertEqual(page["frames"][0]["index"], 0)
        with self.assertRaises(KeyError):
            self.service.frames("inexistente", 0, 1)

    def test_timeline_reads_the_annotations_the_state_snapshot_cannot_express(self):
        game_id = self.game_id("partida-1")
        timeline = self.service.timeline(game_id)
        self.assertEqual(timeline["counts"], {"draw": 1})
        self.assertEqual(timeline["events"][0]["frame_index"], 1)
        self.assertTrue(timeline["events"][0]["is_self"])

    def test_comparing_two_small_samples_reports_overlap_and_the_needed_size(self):
        result = self.service.compare_samples({"wins": 12, "games": 20}, {"wins": 14, "games": 20})
        self.assertTrue(result["intervals_overlap"])
        self.assertIn("não se separam", result["verdict"])
        self.assertGreater(result["games_needed_for_five_points"], 1000)
        self.assertIn("antes de começar", result["warning"])

    def test_binding_a_composition_requires_a_deck_the_log_actually_named(self):
        with self.assertRaises(ValueError):
            self.service.bind_deck({"deck_id": self.deck_id(), "deck_uid": "inventado"})


if __name__ == "__main__":
    unittest.main()
