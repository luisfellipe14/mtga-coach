import json
import unittest

from mtga_coach import timeline
from mtga_coach.ingest import ingest_log


def annotation(kind, affected, details=None, affector=None, identifier=1):
    value = {"id": identifier, "affectedIds": affected, "type": [f"AnnotationType_{kind}"]}
    if affector is not None:
        value["affectorId"] = affector
    if details:
        value["details"] = [{"key": key, "type": "KeyValuePairValueType_int32", "valueInt32": [item]}
                            if isinstance(item, int) else
                            {"key": key, "type": "KeyValuePairValueType_string", "valueString": [item]}
                            for key, item in details.items()]
    return value


ZONES = {31: "Library", 28: "Battlefield", 35: "Hand", 33: "Graveyard"}


def resolve_from(table):
    return lambda instance_id: table.get(instance_id, {"card_id": None, "owner": None})


class TimelineTests(unittest.TestCase):
    def test_zone_transfer_categories_become_named_events(self):
        table = {287: {"card_id": 100, "owner": 2}}
        events = timeline.build_events([
            annotation("ZoneTransfer", [287], {"zone_src": 31, "zone_dest": 35, "category": "Draw"}),
            annotation("ZoneTransfer", [287], {"zone_src": 35, "zone_dest": 28, "category": "PlayLand"}, identifier=2),
            annotation("ZoneTransfer", [287], {"zone_src": 28, "zone_dest": 33, "category": "SBA_Damage"}, identifier=3),
        ], resolve_from(table), ZONES.get)
        self.assertEqual([event["kind"] for event in events], ["draw", "play_land", "leaves_play"])
        self.assertEqual(events[0]["from_zone"], "Library")
        self.assertEqual(events[0]["card_id"], 100)
        self.assertEqual(events[2]["category_label"], "Morte por dano")

    def test_an_undisclosed_object_produces_an_event_without_an_identity(self):
        events = timeline.build_events(
            [annotation("ZoneTransfer", [999], {"zone_src": 31, "zone_dest": 35, "category": "Draw"})],
            resolve_from({}), ZONES.get)
        self.assertEqual(events[0]["kind"], "draw")
        self.assertIsNone(events[0]["card_id"])
        self.assertIn("não revelada", timeline.describe(events[0], lambda cid: "nunca"))

    def test_zero_damage_bookkeeping_is_not_reported_as_damage(self):
        real = annotation("DamageDealt", [288], {"damage": 3, "type": 1}, affector=292)
        noise = annotation("DamageDealt", [288], {"damage": 0, "type": 1}, affector=292, identifier=2)
        events = timeline.build_events([real, noise], resolve_from({292: {"card_id": 55, "owner": 1}}), ZONES.get)
        self.assertEqual([event["kind"] for event in events], ["damage"])
        self.assertEqual(events[0]["amount"], 3)

    def test_life_change_keeps_its_sign(self):
        events = timeline.build_events([annotation("ModifiedLife", [2], {"life": -2})], resolve_from({}), ZONES.get)
        self.assertEqual(events[0]["amount"], -2)
        self.assertIn("-2", timeline.describe(events[0], lambda cid: ""))

    def test_object_id_changes_are_reported_as_lineage_not_as_events(self):
        renumber = annotation("ObjectIdChanged", [166], {"orig_id": 166, "new_id": 287})
        self.assertEqual(timeline.object_id_changes([renumber]), [(166, 287)])
        self.assertEqual(timeline.build_events([renumber], resolve_from({}), ZONES.get), [])

    def test_unknown_annotation_types_are_ignored_rather_than_invented(self):
        events = timeline.build_events([annotation("SomethingBrandNew", [1])], resolve_from({}), ZONES.get)
        self.assertEqual(events, [])


class ReducerLineageTests(unittest.TestCase):
    def test_a_renumbered_object_keeps_its_card_and_leaves_no_ghost(self):
        obj = {"instanceId": 166, "grpId": 100, "zoneId": 28, "ownerSeatId": 2,
               "controllerSeatId": 2, "visibility": "Visibility_Public"}
        zone = {"zoneId": 28, "type": "ZoneType_Battlefield", "objectInstanceIds": [166]}
        first = {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [2], "gameStateId": 1,
                 "gameStateMessage": {"type": "GameStateType_Full", "gameStateId": 1,
                                      "gameInfo": {"matchID": "m", "gameNumber": 1,
                                                   "matchWinCondition": "MatchWinCondition_SingleElimination"},
                                      "players": [{"systemSeatNumber": 2, "lifeTotal": 20}],
                                      "zones": [zone], "gameObjects": [obj]}}
        renamed = {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [2], "gameStateId": 2,
                   "gameStateMessage": {"type": "GameStateType_Diff", "gameStateId": 2, "prevGameStateId": 1,
                                        "annotations": [annotation("ObjectIdChanged", [166], {"orig_id": 166, "new_id": 287})],
                                        "zones": [{**zone, "objectInstanceIds": [287]}]}}
        room = {"matchGameRoomStateChangedEvent": {"gameRoomInfo": {"gameRoomConfig": {
            "matchId": "m", "reservedPlayers": [{"eventId": "Historic_Ladder", "systemSeatId": 2}]}}}}
        connect = {"type": "GREMessageType_ConnectResp", "systemSeatIds": [2],
                   "connectResp": {"deckMessage": {"deckCards": [100], "sideboardCards": []}}}
        data = (json.dumps(room) + "\n"
                + json.dumps({"greToClientEvent": {"greToClientMessages": [connect, first, renamed]}})).encode()
        game = ingest_log(data)["games"][0]
        self.assertEqual(game["mode"], "BO1")
        self.assertEqual(game["mode_basis"], "protocolo")
        battlefield = game["frames"][1]["zones"][0]
        self.assertEqual([item["instance_id"] for item in battlefield["objects"]], [287])
        self.assertEqual(battlefield["objects"][0]["card_id"], 100)


if __name__ == "__main__":
    unittest.main()
