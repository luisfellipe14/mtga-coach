"""Follow Player.log while Arena runs.

Arena truncates Player.log every time the client starts, so a session that is not read
while it is being written is gone. This watcher reads the file forward, notices when it
has been replaced, and hands the new bytes to a streaming ingestor whose games are
flushed to the database as they progress. It only reads: it never writes to the log, to
the game, or to the network.
"""

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

from .draft import write_raw
from .ingest import LogIngestor

FINGERPRINT_BYTES = 4096
POLL_SECONDS = 2.0
FLUSH_SECONDS = 5.0
READ_CHUNK = 4 * 1024 * 1024


def read_head(path, length=FINGERPRINT_BYTES):
    """The file's opening bytes, which Arena rewrites when the client restarts."""
    try:
        with Path(path).open("rb") as handle:
            return handle.read(length)
    except OSError:
        return b""


def fingerprint(path, length=FINGERPRINT_BYTES):
    head = read_head(path, length)
    return hashlib.sha256(head).hexdigest()[:24] if head else ""


def detailed_logs_enabled(path):
    """Arena writes this line at startup; without it the log carries no protocol records."""
    try:
        with Path(path).open("rb") as handle:
            head = handle.read(256 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return None
    if "DETAILED LOGS: ENABLED" in head:
        return True
    if "DETAILED LOGS: DISABLED" in head:
        return False
    return None


class LogWatcher:
    def __init__(self, service, path=None, poll_seconds=POLL_SECONDS, flush_seconds=FLUSH_SECONDS):
        from .config import default_log_path

        self.service = service
        self.path = Path(path) if path is not None else default_log_path()
        self.poll_seconds = poll_seconds
        self.flush_seconds = flush_seconds
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._ingestor = None
        self._offset = 0
        self._head = b""
        self._fingerprint = ""
        self._session_id = ""
        self._pending_since_flush = 0
        self._draft_mark = ""
        self._wallet_mark = 0
        self._rank_mark = 0
        self.state = {"running": False, "path": str(self.path), "bytes_read": 0,
                      "sessions": 0, "games": 0, "flushes": 0, "last_read_at": None,
                      "error": None, "detailed_logs": None, "previous_imported": False}

    # ------------------------------------------------------------------ lifecycle

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="mtga-coach-watch", daemon=True)
            self.state["running"] = True
            self._thread.start()
        return self.status()

    def stop(self, timeout=5.0):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self.state["running"] = False
        return self.status()

    def status(self):
        with self._lock:
            return dict(self.state, offset=self._offset, session=self._session_id,
                        pending=self._pending_since_flush)

    def draft_state(self):
        """The draft this session is following, or None while no pack has been seen."""
        with self._lock:
            if self._ingestor is None or not self._ingestor.draft.active:
                return None
            return self._ingestor.draft.state()

    def draft_diagnostics(self):
        """What the reader matched and what it could not place, for an unseen dialect."""
        with self._lock:
            if self._ingestor is None:
                return {"matched_keys": [], "unrecognised_shapes": [], "records": 0}
            tracker = self._ingestor.draft
            return {"matched_keys": sorted(tracker.matched_keys),
                    "unrecognised_shapes": list(tracker.shapes), "records": tracker.records}

    def _loop(self):
        self.import_previous_session()
        last_flush = 0.0
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as error:  # A watcher must not take the server down with it.
                with self._lock:
                    self.state["error"] = f"{type(error).__name__}: {error}"
            waited = self._stop.wait(self.poll_seconds)
            last_flush += self.poll_seconds
            if last_flush >= self.flush_seconds or waited:
                last_flush = 0.0
                try:
                    self.flush()
                except Exception as error:
                    with self._lock:
                        self.state["error"] = f"{type(error).__name__}: {error}"
        self.flush()

    # ------------------------------------------------------------------ reading

    def import_previous_session(self):
        """Read Player-prev.log once: it holds the session the client discarded on restart."""
        previous = self.path.with_name(self.path.stem + "-prev" + self.path.suffix)
        if not previous.is_file():
            return False
        try:
            body = previous.read_bytes()
        except OSError:
            return False
        if not body:
            return False
        try:
            self.service.import_upload(body)
        except ValueError:
            return False
        with self._lock:
            self.state["previous_imported"] = True
        return True

    def poll_once(self):
        """Read whatever is new. Returns the number of bytes consumed in this pass."""
        if not self.path.is_file():
            with self._lock:
                self.state["error"] = "log file not found"
            return 0
        size = self.path.stat().st_size
        with self._lock:
            known = self._head
        # A growing file keeps its opening bytes; a restarted client rewrites them. Compare
        # only as many bytes as were already seen, or a short file would look replaced on
        # every append.
        head = read_head(self.path, max(len(known), 1))
        with self._lock:
            rotated = (self._ingestor is None or size < self._offset
                       or not head.startswith(known[:len(head)]) or len(head) < len(known))
        if rotated:
            self._rotate(read_head(self.path))
        elif len(known) < FINGERPRINT_BYTES:
            with self._lock:
                self._head = read_head(self.path)
        consumed = 0
        with self.path.open("rb") as handle:
            handle.seek(self._offset)
            while True:
                chunk = handle.read(READ_CHUNK)
                if not chunk:
                    break
                self._ingestor.feed(chunk)
                consumed += len(chunk)
                self._offset += len(chunk)
        with self._lock:
            self.state["bytes_read"] += consumed
            self.state["error"] = None
            if consumed:
                self.state["last_read_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self._pending_since_flush += consumed
        return consumed

    def _rotate(self, head):
        """Close the finished session and open a new ingestor for the replaced file."""
        with self._lock:
            if self._ingestor is not None:
                self._ingestor.finish()
                self._flush_locked()
            digest = hashlib.sha256(head).hexdigest()[:24]
            self._ingestor = LogIngestor(f"captura:{digest}")
            self._offset = 0
            self._head = head
            self._fingerprint = digest
            self._session_id = digest
            self.state["sessions"] += 1
            self.state["detailed_logs"] = detailed_logs_enabled(self.path)

    # ------------------------------------------------------------------ persistence

    def flush(self):
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self):
        if self._ingestor is None:
            return 0
        # Draft records are written whether or not any game was flushed: a draft happens
        # before the first match, when there is nothing else to persist.
        write_raw(self.service.data_dir, self._ingestor.draft.drain_raw())
        snapshot = self._ingestor.snapshot(dirty_only=True)
        games = snapshot.get("games", [])
        # A draft happens before the first match of the event, so a flush that only
        # persists games loses the pool of anyone who closes the client after drafting.
        draft = snapshot.get("draft") or {}
        moved = ((draft.get("updated_at") or "") != self._draft_mark
                 or len(snapshot.get("wallet") or []) != self._wallet_mark
                 or len(snapshot.get("rank_points") or []) != self._rank_mark)
        if not games and not moved:
            self._pending_since_flush = 0
            return 0
        self._draft_mark = draft.get("updated_at") or ""
        self._wallet_mark = len(snapshot.get("wallet") or [])
        self._rank_mark = len(snapshot.get("rank_points") or [])
        self.service.store.absorb_session(snapshot)
        finished = [game["id"] for game in games if game.get("status") == "complete"]
        self._ingestor.clear_dirty()
        self._ingestor.release(finished)
        self.state["flushes"] += 1
        self.state["games"] = len(self._ingestor.games)
        self._pending_since_flush = 0
        return len(games)
