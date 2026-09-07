import json
import tempfile
import unittest
from pathlib import Path

from mtga_coach.capture import LogWatcher, detailed_logs_enabled, fingerprint
from mtga_coach.service import CoachService


def room(match, event="Historic_Ladder"):
    return json.dumps({"matchGameRoomStateChangedEvent": {"gameRoomInfo": {"gameRoomConfig": {
        "matchId": match, "reservedPlayers": [{"eventId": event, "systemSeatId": 2, "userId": "me"}]}}}})


def messages(items):
    return json.dumps({"greToClientEvent": {"greToClientMessages": items}})


def connect():
    return {"type": "GREMessageType_ConnectResp", "systemSeatIds": [2],
            "connectResp": {"deckMessage": {"deckCards": [100, 100, 101], "sideboardCards": []}}}


def state(match, number, full=True, previous=None, stage="GameStage_Play", results=None):
    info = {"matchID": match, "gameNumber": 1, "stage": stage,
            "matchWinCondition": "MatchWinCondition_SingleElimination"}
    if results:
        info["results"] = results
    body = {"type": "GameStateType_Full" if full else "GameStateType_Diff", "gameStateId": number,
            "gameInfo": info, "turnInfo": {"turnNumber": 1, "activePlayer": 2},
            "players": [{"systemSeatNumber": 2, "lifeTotal": 20, "teamId": 2}]}
    if previous is not None:
        body["prevGameStateId"] = previous
    return {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [2],
            "gameStateId": number, "gameStateMessage": body}


def session(match, finished=False):
    results = [{"scope": "MatchScope_Game", "result": "ResultType_WinLoss", "winningTeamId": 2},
               {"scope": "MatchScope_Match", "result": "ResultType_WinLoss", "winningTeamId": 2}]
    lines = ["DETAILED LOGS: ENABLED", room(match),
             messages([connect(), state(match, 1)])]
    if finished:
        lines.append(messages([state(match, 2, full=False, previous=1,
                                     stage="GameStage_GameOver", results=results)]))
    return ("\n".join(lines) + "\n").encode()


class LogWatcherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.log = self.root / "Player.log"
        self.service = CoachService(data_dir=self.root / "data", log_path=self.log)
        self.watcher = LogWatcher(self.service, self.log)

    def tearDown(self):
        self.service.store.close()
        self.directory.cleanup()

    def test_growing_file_is_read_forward_without_reprocessing_earlier_bytes(self):
        head = session("match-a")
        self.log.write_bytes(head)
        self.assertEqual(self.watcher.poll_once(), len(head))
        self.assertEqual(self.watcher.poll_once(), 0)

        tail = messages([state("match-a", 2, full=False, previous=1)]).encode() + b"\n"
        with self.log.open("ab") as handle:
            handle.write(tail)
        self.assertEqual(self.watcher.poll_once(), len(tail))
        self.watcher.flush()

        stored = self.service.store.games()
        self.assertEqual([item["id"] for item in stored], ["e6615737e448a64a-1"])
        self.assertEqual(stored[0]["frame_count"], 2)
        self.assertEqual(stored[0]["status"], "in_progress")

    def test_a_replaced_log_starts_a_new_session_instead_of_reading_from_the_old_offset(self):
        self.log.write_bytes(session("match-a", finished=True))
        self.watcher.poll_once()
        self.watcher.flush()
        first = self.watcher.status()

        # Arena truncates Player.log when the client restarts: same path, new content.
        self.log.write_bytes(session("match-b"))
        self.watcher.poll_once()
        self.watcher.flush()

        self.assertEqual(self.watcher.status()["sessions"], first["sessions"] + 1)
        self.assertEqual(len(self.service.store.games()), 2)
        self.assertEqual(self.watcher.status()["detailed_logs"], True)

    def test_a_finished_game_is_released_from_memory_after_being_stored(self):
        self.log.write_bytes(session("match-a", finished=True))
        self.watcher.poll_once()
        self.watcher.flush()
        ingestor = self.watcher._ingestor
        self.assertEqual([game["frames"] for game in ingestor.games.values()], [[]])
        self.assertEqual(ingestor.reducers, {})
        stored = self.service.store.game("e6615737e448a64a-1")
        self.assertEqual(stored["result"], "win")
        self.assertEqual(len(stored["frames"]), 2)

    def test_a_half_written_record_is_completed_by_the_next_read(self):
        head = session("match-a")
        split = len(head) - 40
        self.log.write_bytes(head[:split])
        self.watcher.poll_once()
        self.watcher.flush()
        self.assertEqual(self.service.store.games(), [])
        with self.log.open("ab") as handle:
            handle.write(head[split:])
        self.watcher.poll_once()
        self.watcher.flush()
        self.assertEqual(len(self.service.store.games()), 1)

    def test_previous_session_file_is_imported_once(self):
        self.log.write_bytes(session("match-a"))
        previous = self.root / "Player-prev.log"
        previous.write_bytes(session("match-old", finished=True))
        self.assertTrue(self.watcher.import_previous_session())
        self.assertEqual([item["id"] for item in self.service.store.games()], ["c96f650ba9a52b1b-1"])
        self.assertTrue(self.watcher.import_previous_session())
        self.assertEqual(self.service.store.import_count(), 1)

    def test_a_missing_log_is_reported_instead_of_raising(self):
        self.assertEqual(self.watcher.poll_once(), 0)
        self.assertIn("not found", self.watcher.status()["error"])
        self.assertEqual(fingerprint(self.log), "")
        self.assertIsNone(detailed_logs_enabled(self.log))


if __name__ == "__main__":
    unittest.main()
