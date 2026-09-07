"""Small loopback-only HTTP API for the MTGA Coach local UI."""

from __future__ import annotations

import json
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .service import MAX_UPLOAD_BYTES, CoachService

IMPORT_LIMIT = MAX_UPLOAD_BYTES
JSON_LIMIT = 64 * 1024
MAX_WORKERS = 16
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/app.css": "app.css"}


def _loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class _LimitedThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        self.request_slots = threading.BoundedSemaphore(MAX_WORKERS)

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        if not self.request_slots.acquire(blocking=False):
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


def create_server(service: CoachService, host: str = "127.0.0.1", port: int = 18731) -> _LimitedThreadingHTTPServer:
    if not _loopback(host):
        raise ValueError("the server only binds to a loopback address")

    class Handler(_Handler):
        coach_service = service

    return _LimitedThreadingHTTPServer((host, port), Handler)


class _Handler(BaseHTTPRequestHandler):
    coach_service: CoachService
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self) -> None:
        if not self._safe_request():
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if self._route_get(parsed.path, query):
                return
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "resource not found")
            return
        except (ValueError, TypeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid parameter")
            return
        self._static(parsed.path)

    def _route_get(self, path: str, query: dict) -> bool:
        service = self.coach_service
        if path == "/api/health":
            self._send(HTTPStatus.OK, {"status": "ok", "app": "mtga-coach", "version": __version__})
        elif path == "/api/summary":
            self._send(HTTPStatus.OK, service.summary())
        elif path == "/api/games":
            self._send(HTTPStatus.OK, service.games())
        elif path == "/api/experiments":
            self._send(HTTPStatus.OK, {"experiments": service.experiments()})
        elif path == "/api/notes":
            self._send(HTTPStatus.OK, {"notes": service.notes()})
        elif path == "/api/rulings":
            values = query.get("ids", [""])[0].split(",")
            self._send(HTTPStatus.OK, {"rulings": service.rulings_for([int(v) for v in values if v])})
        elif path == "/api/draft":
            self._send(HTTPStatus.OK, service.draft())
        elif path == "/api/primer":
            self._send(HTTPStatus.OK, service.set_primer(query.get("set", [""])[0]))
        elif path == "/api/draft/signals":
            self._send(HTTPStatus.OK, service.draft_signals())
        elif path == "/api/draft/review":
            self._send(HTTPStatus.OK, service.draft_review())
        elif path == "/api/draft/deck":
            self._send(HTTPStatus.OK, service.draft_deck())
        elif path == "/api/draft/deck/export":
            self._send(HTTPStatus.OK, service.draft_deck_export())
        elif path == "/api/draft/export":
            self._send(HTTPStatus.OK, service.draft_pool_export())
        elif path == "/api/community":
            self._send(HTTPStatus.OK, service.community_grades(query.get("set", [""])[0]))
        elif path == "/api/community/export":
            self._send(HTTPStatus.OK, service.export_grades(query.get("set", [""])[0]))
        elif path == "/api/limited":
            self._send(HTTPStatus.OK, service.limited_sets())
        elif path == "/api/rank":
            self._send(HTTPStatus.OK, service.rank_history(
                str(query.get("track", ["constructed"])[0])))
        elif path == "/api/matchups":
            self._send(HTTPStatus.OK, service.matchups())
        elif path == "/api/live":
            self._send(HTTPStatus.OK, service.live_game())
        elif path == "/api/payouts":
            self._send(HTTPStatus.OK, service.measured_payouts())
        elif path == "/api/economy":
            self._send(HTTPStatus.OK, service.economy())
        elif path == "/api/wallet":
            self._send(HTTPStatus.OK, service.wallet())
        elif path == "/api/capture":
            self._send(HTTPStatus.OK, service.capture_status())
        elif path == "/api/cards":
            values = query.get("ids", [""])[0].split(",")
            self._send(HTTPStatus.OK, {"cards": service.cards([int(value) for value in values if value])})
        elif path.startswith("/api/decks/"):
            deck_id, _, action = path.removeprefix("/api/decks/").partition("/")
            if action.strip("/") == "export":
                self._send(HTTPStatus.OK, service.export_deck(deck_id))
            elif action.strip("/"):
                return False
            else:
                self._send(HTTPStatus.OK, service.deck_report(deck_id))
        elif path.startswith("/art/"):
            return self._card_art(path.removeprefix("/art/"))
        elif path.startswith("/api/games/"):
            return self._route_game(path.removeprefix("/api/games/"), query)
        else:
            return False
        return True

    def _card_art(self, name: str) -> bool:
        """Serve one cached image. Nothing is fetched here; a missing file is a 404."""
        stem = name.removesuffix(".jpg")
        if not stem.isdigit():
            return False
        file = self.coach_service.art_file(int(stem))
        if file is None:
            self._error(HTTPStatus.NOT_FOUND, "image not cached")
            return True
        data = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=604800")
        self.end_headers()
        self.wfile.write(data)
        return True

    def _route_game(self, remainder: str, query: dict) -> bool:
        service = self.coach_service
        game_id, _, action = remainder.partition("/")
        game_id, action = game_id.rstrip("/"), action.strip("/")
        if action == "context":
            context = service.decision_context(game_id, int(query["index"][0]))
            if not context["eligible"]:
                self._error(HTTPStatus.BAD_REQUEST, "context unavailable")
            else:
                self._send(HTTPStatus.OK, context)
        elif action == "frames":
            self._send(HTTPStatus.OK, service.frames(game_id, int(query.get("start", ["0"])[0]),
                                                     int(query.get("limit", ["25"])[0])))
        elif action == "timeline":
            self._send(HTTPStatus.OK, service.timeline(game_id))
        elif action == "library":
            self._send(HTTPStatus.OK, service.library_state(game_id, int(query["index"][0])))
        elif action == "opponent":
            self._send(HTTPStatus.OK, service.opponent_profile(game_id))
        elif not action:
            detail = service.game_detail(game_id)
            if detail is None:
                self._error(HTTPStatus.NOT_FOUND, "game not found")
            else:
                self._send(HTTPStatus.OK, detail)
        else:
            return False
        return True

    def do_POST(self) -> None:
        if not self._safe_request():
            return
        if self.headers.get("X-MTGA-Coach") != "1":
            self._error(HTTPStatus.BAD_REQUEST, "application header missing")
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/import" and self.headers.get_content_type() != "application/json":
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Type")
            return
        if parsed.path == "/api/import" and self.headers.get_content_type() not in {"application/json", "application/octet-stream"}:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Type")
            return
        limit = IMPORT_LIMIT if parsed.path == "/api/import" and self.headers.get_content_type() == "application/octet-stream" else JSON_LIMIT
        body = self._body(limit)
        if body is None:
            return
        try:
            if parsed.path == "/api/import":
                if self.headers.get_content_type() == "application/octet-stream":
                    self._send(HTTPStatus.CREATED, self.coach_service.import_upload(body))
                    return
                payload = self._json(body)
                if payload != {"source": "configured"}:
                    raise ValueError("invalid source")
                self._send(HTTPStatus.CREATED, self.coach_service.import_configured())
            elif parsed.path == "/api/notes":
                self._send(HTTPStatus.CREATED, self.coach_service.save_note(self._json(body)))
            elif parsed.path == "/api/experiments":
                self._send(HTTPStatus.CREATED, self.coach_service.save_experiment(self._json(body)))
            elif parsed.path == "/api/decks/bind":
                self._send(HTTPStatus.CREATED, self.coach_service.bind_deck(self._json(body)))
            elif parsed.path == "/api/compare":
                payload = self._json(body)
                self._send(HTTPStatus.OK, self.coach_service.compare_samples(
                    payload.get("first") or {}, payload.get("second") or {}))
            elif parsed.path == "/api/art":
                self._send(HTTPStatus.OK, self.coach_service.set_art(
                    bool(self._json(body).get("enabled"))))
            elif parsed.path == "/api/art/fetch":
                payload = self._json(body)
                ids = payload.get("card_ids")
                if payload.get("deck_id"):
                    ids = self.coach_service.deck_art_ids(str(payload["deck_id"]))
                if not isinstance(ids, list):
                    raise ValueError("invalid card_ids")
                self._send(HTTPStatus.OK, self.coach_service.fetch_art(ids))
            elif parsed.path == "/api/rulings":
                self._send(HTTPStatus.OK, self.coach_service.set_rulings(
                    bool(self._json(body).get("enabled"))))
            elif parsed.path == "/api/community/grade":
                self._send(HTTPStatus.CREATED, self.coach_service.save_grade(self._json(body)))
            elif parsed.path == "/api/limited/fetch":
                payload = self._json(body)
                self._send(HTTPStatus.OK, self.coach_service.fetch_limited(
                    str(payload.get("expansion", "")), str(payload.get("event") or "PremierDraft"),
                    bool(payload.get("force"))))
            elif parsed.path == "/api/rulings/fetch":
                ids = self._json(body).get("card_ids")
                if not isinstance(ids, list):
                    raise ValueError("invalid card_ids")
                self._send(HTTPStatus.OK, self.coach_service.fetch_rulings(ids))
            elif parsed.path == "/api/decks/analyze":
                self._send(HTTPStatus.OK, self.coach_service.analyze_list(
                    self._json(body).get("text", "")))
            elif parsed.path == "/api/coach/key":
                self._send(HTTPStatus.OK, self.coach_service.save_api_key(
                    self._json(body).get("key", "")))
            elif parsed.path == "/api/coach/prompt":
                payload = self._json(body)
                kind = str(payload.get("kind", "position"))
                arguments = {"deck_id": payload["deck_id"]} if kind == "deck" else {
                    "game_id": payload.get("game_id"), "index": payload.get("index")}
                self._send(HTTPStatus.OK, self.coach_service.coach_prompt(
                    str(payload.get("mode", "")), kind, **arguments))
            elif parsed.path in ("/api/coach/review", "/api/coach/estimate"):
                self._coach(parsed.path.endswith("review"), self._json(body))
            elif parsed.path in ("/api/capture/start", "/api/capture/stop"):
                self._send(HTTPStatus.OK, self.coach_service.set_capture(
                    parsed.path.endswith("start")))
            else:
                self._error(HTTPStatus.NOT_FOUND, "route not found")
        except (ValueError, TypeError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid request")
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "game not found")

    def _coach(self, run: bool, payload: dict) -> None:
        from .coach import CoachUnavailable

        mode = str(payload.get("mode", ""))
        kind = str(payload.get("kind", "position"))
        arguments = {"deck_id": payload["deck_id"]} if kind == "deck" else {
            "game_id": payload.get("game_id"), "index": payload.get("index")}
        try:
            action = self.coach_service.coach_review if run else self.coach_service.coach_estimate
            self._send(HTTPStatus.OK, action(mode, kind, **arguments))
        except CoachUnavailable as error:
            # Not the user's mistake and not a bad request: the review simply cannot run.
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)}, close=True)

    def _safe_request(self) -> bool:
        port = self.server.server_port
        host = self.headers.get("Host", "")
        accepted = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if host not in accepted:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Host")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}"}:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Origin")
            return False
        return True

    def _body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return None
        if length < 0 or length > limit:
            self._error(HTTPStatus.BAD_REQUEST, "body exceeds the limit")
            return None
        try:
            body = self.rfile.read(length)
        except (OSError, socket.timeout):
            self._error(HTTPStatus.BAD_REQUEST, "incomplete body")
            return None
        if len(body) != length:
            self._error(HTTPStatus.BAD_REQUEST, "incomplete body")
            return None
        return body

    def _json(self, body: bytes) -> dict:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _static(self, path: str) -> None:
        name = STATIC_FILES.get(path)
        static_dir = Path(__file__).with_name("static")
        file = static_dir / name if name is not None else None
        if file is None or not file.is_file():
            self._error(HTTPStatus.NOT_FOUND, "route not found")
            return
        content_type = "text/css; charset=utf-8" if name.endswith(".css") else "application/javascript; charset=utf-8" if name.endswith(".js") else "text/html; charset=utf-8"
        data = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # The interface ships inside the app and changes with it. Letting the browser keep
        # a copy means an updated app still draws the previous version's screen, which is
        # a bug the user cannot diagnose and cannot fix except by clearing site data.
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _send(self, status: HTTPStatus, payload: object, close: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self.close_connection = True
        self._send(status, {"error": message}, close=True)
