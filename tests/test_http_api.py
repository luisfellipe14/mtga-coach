import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
import socket

from mtga_coach.http_api import MAX_WORKERS, create_server
from mtga_coach.service import CoachService

from tests.test_storage import game


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()

        def importer(_: bytes) -> dict:
            return {"source_sha256": "source-a", "record_count": 1, "warnings": [], "games": [game()]}

        self.service = CoachService(data_dir=Path(self.directory.name), importer=importer)
        self.service.import_upload(b"snapshot")
        self.server = create_server(self.service, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.store.close()
        self.directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        body = json.dumps(payload).encode() if payload is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        headers = {"Host": f"127.0.0.1:{self.server.server_port}"}
        if body is not None:
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(body)), "X-MTGA-Coach": "1"})
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def test_notes_and_experiments_round_trip(self) -> None:
        status, note = self.request("POST", "/api/notes", {"game_id": "game-a", "frame_index": 0, "body": "Rever mana", "tags": ["mana"]})
        self.assertEqual(status, 201)
        self.assertEqual(note["body"], "Rever mana")

        status, experiment = self.request("POST", "/api/experiments", {"deck_id": "deck-a", "title": "Curva", "hypothesis": "Menos terrenos", "changes": {"remove": [1]}, "status": "planned"})
        self.assertEqual(status, 201)
        self.assertEqual(experiment["status"], "planned")

        status, detail = self.request("GET", "/api/games/game-a")
        self.assertEqual(status, 200)
        self.assertEqual(detail["notes"][0]["tags"], ["mana"])
        status, experiments = self.request("GET", "/api/experiments")
        self.assertEqual(status, 200)
        self.assertEqual(experiments["experiments"][0]["title"], "Curva")

    def test_cards_are_enveloped_and_blocked_context_is_bad_request(self) -> None:
        status, cards = self.request("GET", "/api/cards?ids=1")
        self.assertEqual(status, 200)
        self.assertIn("cards", cards)

        self.service.store.save_import({"source_sha256": "blocked", "record_count": 1, "warnings": [], "games": [game("blocked-game", frames=[{**game()["frames"][0], "quality": "blocked"}])]}, "upload", 1)
        status, response = self.request("GET", "/api/games/blocked-game/context?index=0")
        self.assertEqual(status, 400)
        self.assertEqual(response["error"], "contexto indisponível")

    def test_health_identifies_the_application_and_server_drops_excess_connections(self) -> None:
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health, {"status": "ok", "app": "mtga-coach", "version": "0.2.0"})

        capacity_server = create_server(self.service, port=0)
        held = []
        while capacity_server.request_slots.acquire(blocking=False):
            held.append(True)
        self.assertLessEqual(len(held), MAX_WORKERS)
        server_side, client_side = socket.socketpair()
        try:
            capacity_server.process_request(server_side, ("127.0.0.1", 0))
            client_side.settimeout(1)
            self.assertEqual(client_side.recv(1), b"")
        finally:
            client_side.close()
            for _ in held:
                capacity_server.request_slots.release()
            capacity_server.server_close()
