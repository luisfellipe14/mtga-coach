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
        raise ValueError("o servidor aceita apenas endereço loopback")

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
            self._error(HTTPStatus.NOT_FOUND, "recurso não encontrado")
            return
        except (ValueError, TypeError):
            self._error(HTTPStatus.BAD_REQUEST, "parâmetro inválido")
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
        elif path == "/api/capture":
            self._send(HTTPStatus.OK, service.capture_status())
        elif path == "/api/cards":
            values = query.get("ids", [""])[0].split(",")
            self._send(HTTPStatus.OK, {"cards": service.cards([int(value) for value in values if value])})
        elif path.startswith("/api/decks/"):
            self._send(HTTPStatus.OK, service.deck_report(path.removeprefix("/api/decks/")))
        elif path.startswith("/api/games/"):
            return self._route_game(path.removeprefix("/api/games/"), query)
        else:
            return False
        return True

    def _route_game(self, remainder: str, query: dict) -> bool:
        service = self.coach_service
        game_id, _, action = remainder.partition("/")
        game_id, action = game_id.rstrip("/"), action.strip("/")
        if action == "context":
            context = service.decision_context(game_id, int(query["index"][0]))
            if not context["eligible"]:
                self._error(HTTPStatus.BAD_REQUEST, "contexto indisponível")
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
                self._error(HTTPStatus.NOT_FOUND, "jogo não encontrado")
            else:
                self._send(HTTPStatus.OK, detail)
        else:
            return False
        return True

    def do_POST(self) -> None:
        if not self._safe_request():
            return
        if self.headers.get("X-MTGA-Coach") != "1":
            self._error(HTTPStatus.BAD_REQUEST, "cabeçalho da aplicação ausente")
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/import" and self.headers.get_content_type() != "application/json":
            self._error(HTTPStatus.BAD_REQUEST, "Content-Type inválido")
            return
        if parsed.path == "/api/import" and self.headers.get_content_type() not in {"application/json", "application/octet-stream"}:
            self._error(HTTPStatus.BAD_REQUEST, "Content-Type inválido")
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
                    raise ValueError("fonte inválida")
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
            elif parsed.path in ("/api/capture/start", "/api/capture/stop"):
                self._send(HTTPStatus.OK, self.coach_service.set_capture(
                    parsed.path.endswith("start")))
            else:
                self._error(HTTPStatus.NOT_FOUND, "rota não encontrada")
        except (ValueError, TypeError, UnicodeDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "requisição inválida")
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "jogo não encontrado")

    def _safe_request(self) -> bool:
        port = self.server.server_port
        host = self.headers.get("Host", "")
        accepted = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if host not in accepted:
            self._error(HTTPStatus.BAD_REQUEST, "Host inválido")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}"}:
            self._error(HTTPStatus.BAD_REQUEST, "Origin inválido")
            return False
        return True

    def _body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "Content-Length inválido")
            return None
        if length < 0 or length > limit:
            self._error(HTTPStatus.BAD_REQUEST, "corpo excede o limite")
            return None
        try:
            body = self.rfile.read(length)
        except (OSError, socket.timeout):
            self._error(HTTPStatus.BAD_REQUEST, "corpo incompleto")
            return None
        if len(body) != length:
            self._error(HTTPStatus.BAD_REQUEST, "corpo incompleto")
            return None
        return body

    def _json(self, body: bytes) -> dict:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON deve ser objeto")
        return payload

    def _static(self, path: str) -> None:
        name = STATIC_FILES.get(path)
        static_dir = Path(__file__).with_name("static")
        file = static_dir / name if name is not None else None
        if file is None or not file.is_file():
            self._error(HTTPStatus.NOT_FOUND, "rota não encontrada")
            return
        content_type = "text/css; charset=utf-8" if name.endswith(".css") else "application/javascript; charset=utf-8" if name.endswith(".js") else "text/html; charset=utf-8"
        data = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
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
