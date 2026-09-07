import json
import unittest

from mtga_coach.ingest import ingest_log, parse_log


def packet(messages):
    return json.dumps({'greToClientEvent': {'greToClientMessages': messages}})


def state(number, previous=None, full=False, **fields):
    value = {'type': 'GameStateType_Full' if full else 'GameStateType_Diff',
             'gameStateId': number, **fields}
    if previous is not None:
        value['prevGameStateId'] = previous
    return {'type': 'GREMessageType_GameStateMessage', 'systemSeatIds': [2],
            'gameStateId': number, 'gameStateMessage': value}


def fixture(states, match='test-match', number=1, event='Historic_Ladder'):
    room = {'matchGameRoomStateChangedEvent': {'gameRoomInfo': {'gameRoomConfig': {
        'matchId': match, 'reservedPlayers': [{'eventId': event, 'systemSeatId': 2}]}}}}
    connect = {'type': 'GREMessageType_ConnectResp', 'systemSeatIds': [2],
               'connectResp': {'deckMessage': {'deckCards': [100, 100, 101], 'sideboardCards': []}}}
    states[0]['gameStateMessage']['gameInfo'] = {
        'matchID': match, 'gameNumber': number, 'stage': 'GameStage_Start'}
    return ('noise\n' + json.dumps(room) + '\n' + packet([connect, *states])).encode()


class IngestTests(unittest.TestCase):
    def test_missing_connect_keeps_local_seat_unknown_and_blocks_context(self):
        message = state(1, full=True,
            gameInfo={'matchID': 'cut-log', 'gameNumber': 1},
            players=[{'systemSeatNumber': 2, 'lifeTotal': 20}],
            zones=[{'zoneId': 5, 'type': 'ZoneType_Hand', 'ownerSeatId': 2,
                    'objectInstanceIds': [10]}],
            gameObjects=[{'instanceId': 10, 'grpId': 100, 'ownerSeatId': 2,
                          'visibility': 'Visibility_Private', 'viewers': [2]}])
        game = ingest_log(packet([message]).encode())['games'][0]
        self.assertEqual(game['self_seat'], 0)
        self.assertEqual(game['quality'], 'degraded')
        frame = game['frames'][0]
        self.assertEqual(frame['quality'], 'blocked')
        self.assertFalse(any(player['is_self'] for player in frame['players']))
        self.assertEqual(frame['zones'][0]['objects'], [])
        self.assertTrue(frame['warnings'])

    def test_unity_diagnostic_with_non_json_braces_does_not_hide_games(self):
        data = b'[D3D12 Device Filter] denied: {"Vendor": "Intel", "Driver": any}\n' + fixture([state(1, full=True)])
        self.assertEqual(len(ingest_log(data)['games']), 1)

    def test_nested_payload_and_incomplete_tail_preserve_valid_records(self):
        data = fixture([state(1, full=True)])
        encoded = json.dumps({'payload': data.decode()}).encode()
        # Non-JSON text wrapped in a string is not interpreted as a protocol payload.
        self.assertEqual(len(ingest_log(data + b'\n{"unfinished":')['games']), 1)
        self.assertTrue(parse_log(data + b'\n{"unfinished":')['warnings'])

    def test_connect_response_marks_self_seat_and_does_not_expose_identity(self):
        data = fixture([state(1, full=True, players=[{'systemSeatNumber': 2, 'lifeTotal': 20}])])
        game = ingest_log(data)['games'][0]
        self.assertEqual(game['self_seat'], 2)
        self.assertTrue(game['frames'][0]['players'][0]['is_self'])
        self.assertNotIn('test-match', json.dumps(game))
        self.assertEqual(game['mode'], 'BO1')
        self.assertEqual(game['deck']['main'], [{'id': 100, 'quantity': 2}, {'id': 101, 'quantity': 1}])

    def test_diff_replaces_objects_zones_and_clears_default_tapped(self):
        obj = {'instanceId': 10, 'grpId': 100, 'zoneId': 5, 'visibility': 'Visibility_Public',
               'ownerSeatId': 2, 'controllerSeatId': 2, 'isTapped': True}
        zone = {'zoneId': 5, 'type': 'ZoneType_Battlefield', 'objectInstanceIds': [10]}
        game = ingest_log(fixture([
            state(1, full=True, zones=[zone], gameObjects=[obj]),
            state(2, 1, gameObjects=[{k: v for k, v in obj.items() if k != 'isTapped'}]),
            state(3, 2, zones=[{'zoneId': 5, 'type': 'ZoneType_Battlefield'}], diffDeletedInstanceIds=[10]),
        ]))['games'][0]
        self.assertTrue(game['frames'][0]['zones'][0]['objects'][0]['tapped'])
        self.assertFalse(game['frames'][1]['zones'][0]['objects'][0]['tapped'])
        self.assertEqual(game['frames'][2]['zones'][0]['objects'], [])

    def test_missing_predecessor_blocks_until_full_resynchronisation(self):
        game = ingest_log(fixture([state(1, full=True), state(3, 2), state(4, 3),
                                   state(5, full=True)]))['games'][0]
        self.assertEqual([f['quality'] for f in game['frames']], ['complete', 'blocked', 'blocked', 'complete'])
        self.assertTrue(game['frames'][1]['warnings'])

    def test_future_reveal_does_not_mutate_past_frame_and_libraries_are_hidden(self):
        zone = {'zoneId': 5, 'type': 'ZoneType_Hand', 'ownerSeatId': 1, 'objectInstanceIds': [10]}
        hidden = {'instanceId': 10, 'grpId': 999, 'zoneId': 5,
                  'visibility': 'Visibility_Private', 'viewers': [1], 'ownerSeatId': 1}
        game = ingest_log(fixture([
            state(1, full=True, zones=[zone], gameObjects=[hidden]),
            state(2, 1, gameObjects=[{**hidden, 'visibility': 'Visibility_Public'}]),
        ]))['games'][0]
        self.assertEqual(game['frames'][0]['zones'][0]['objects'], [])
        self.assertEqual(game['frames'][0]['zones'][0]['hidden_count'], 1)
        self.assertEqual(game['frames'][1]['zones'][0]['objects'][0]['card_id'], 999)

    def test_stack_ability_uses_source_card_not_ability_id_as_card(self):
        zone = {'zoneId': 27, 'type': 'ZoneType_Stack', 'objectInstanceIds': [10]}
        obj = {'instanceId': 10, 'grpId': 172105, 'type': 'GameObjectType_Ability',
               'objectSourceGrpId': 90810, 'zoneId': 27, 'visibility': 'Visibility_Public'}
        frame = ingest_log(fixture([state(1, full=True, zones=[zone], gameObjects=[obj])]))['games'][0]['frames'][0]
        self.assertEqual(frame['zones'][0]['objects'][0]['card_id'], 90810)
        self.assertEqual(frame['zones'][0]['objects'][0]['ability_id'], 172105)

    def test_results_resolve_team_of_local_seat(self):
        finish = state(2, 1, gameInfo={'matchID': 'test-match', 'gameNumber': 1,
            'stage': 'GameStage_GameOver', 'matchState': 'MatchState_MatchComplete',
            'results': [{'scope': 'MatchScope_Game', 'winningTeamId': 9, 'result': 'ResultType_WinLoss'},
                        {'scope': 'MatchScope_Match', 'winningTeamId': 9, 'result': 'ResultType_WinLoss'}]})
        game = ingest_log(fixture([state(1, full=True, players=[{'systemSeatNumber': 2, 'teamId': 9, 'lifeTotal': 20}]), finish]))['games'][0]
        self.assertEqual((game['result'], game['match_result'], game['status']), ('win', 'win', 'complete'))

    def test_decision_attaches_to_requested_state_without_future_action(self):
        raw = fixture([state(1, full=True), state(2, 1)])
        action = {'type': 'ClientMessageType_PerformActionResp', 'gameStateId': 1,
                  'performActionResp': {'actions': [{'actionType': 'ActionType_Play', 'grpId': 100}]}}
        game = ingest_log(raw + b'\n' + json.dumps(action).encode())['games'][0]
        self.assertEqual(game['frames'][0]['action']['card_ids'], [100])
        self.assertIsNone(game['frames'][1]['action'])


if __name__ == '__main__':
    unittest.main()
