"""Private SQLite persistence for local MTGA Coach reviews.

Frames are the bulk of a session and are stored as compressed blocks: a game's board
history is ~2% of its raw JSON that way, and a block still decompresses in a couple of
milliseconds, so the replay keeps random access without the database growing per game to
the size of the log it came from. Everything the lists and statistics need lives in plain
columns, so a summary never decompresses a frame.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
FRAME_BLOCK = 25

GAME_COLUMNS = (
    "id", "source_sha256", "match_id_hashed", "game_number", "mode", "mode_basis", "format",
    "event_id", "super_format", "deck_id", "registered_deck_id", "result", "match_result",
    "result_reason", "status", "turns", "decision_count", "quality", "self_seat", "on_play",
    "mulligans_self", "mulligans_opponent", "opponent_hash", "opponent_name",
    "completed_reason", "started_at", "frame_count",
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _value(value: str) -> object:
    return json.loads(value)


def _pack(frames: list[dict]) -> bytes:
    return zlib.compress(_json(frames).encode("utf-8"), 6)


def _unpack(blob: bytes) -> list[dict]:
    value = json.loads(zlib.decompress(blob).decode("utf-8"))
    return value if isinstance(value, list) else []


def _revealed_by_opponent(frames: list[dict], self_seat: int) -> list[int]:
    """Opponent cards the player actually saw: every disclosed object outside hand and library.

    Read from the frames rather than from the event stream, because a permanent that was
    already on the battlefield when a resynchronisation happened produces no event and was
    still, plainly, seen.
    """
    revealed: set[int] = set()
    for frame in frames:
        for zone in frame.get("zones", []) if isinstance(frame, dict) else []:
            if not isinstance(zone, dict) or str(zone.get("type", "")).lower() in {"hand", "library"}:
                continue
            for card in zone.get("objects", []):
                if (isinstance(card, dict) and isinstance(card.get("card_id"), int)
                        and isinstance(card.get("owner"), int) and card["owner"] != self_seat):
                    revealed.add(card["card_id"])
    return sorted(revealed)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReviewStore:
    """Stores reduced game records and user-authored review material only."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "reviews.sqlite3"
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        legacy = self._legacy_rows()
        self._create_schema()
        if legacy:
            self._restore_legacy(legacy)

    # ------------------------------------------------------------------ schema

    def _legacy_rows(self) -> list[dict]:
        """Version 1 kept whole games in one JSON column. Read them before replacing it."""
        tables = {row[0] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "games" not in tables or "schema_meta" in tables:
            return []
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(games)")}
        if "game_json" not in columns:
            return []
        rows = self.connection.execute("SELECT game_json FROM games").fetchall()
        games = [item for row in rows if isinstance((item := _value(row["game_json"])), dict)]
        self.connection.execute("ALTER TABLE games RENAME TO games_v1")
        self.connection.commit()
        return games

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS imports (
                sha256 TEXT PRIMARY KEY, source_kind TEXT NOT NULL, byte_count INTEGER NOT NULL,
                record_count INTEGER NOT NULL, warnings_json TEXT NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS games (
                id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, match_id_hashed TEXT NOT NULL,
                game_number INTEGER NOT NULL DEFAULT 1, mode TEXT NOT NULL DEFAULT 'unknown',
                mode_basis TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT '',
                event_id TEXT NOT NULL DEFAULT '', super_format TEXT NOT NULL DEFAULT '',
                deck_id TEXT NOT NULL DEFAULT 'unknown', registered_deck_id TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT 'unknown', match_result TEXT NOT NULL DEFAULT 'unknown',
                result_reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'in_progress',
                turns INTEGER NOT NULL DEFAULT 0, decision_count INTEGER NOT NULL DEFAULT 0,
                quality TEXT NOT NULL DEFAULT 'complete', self_seat INTEGER NOT NULL DEFAULT 0,
                on_play INTEGER, mulligans_self INTEGER NOT NULL DEFAULT 0,
                mulligans_opponent INTEGER NOT NULL DEFAULT 0, opponent_hash TEXT,
                opponent_name TEXT, completed_reason TEXT, started_at TEXT,
                frame_count INTEGER NOT NULL DEFAULT 0, deck_json TEXT NOT NULL DEFAULT '{}',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                opening_hand_json TEXT NOT NULL DEFAULT 'null', drawn_json TEXT NOT NULL DEFAULT '[]',
                opponent_revealed_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS games_match ON games(match_id_hashed, game_number);
            CREATE INDEX IF NOT EXISTS games_deck ON games(deck_id);
            CREATE TABLE IF NOT EXISTS frame_index (
                game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                idx INTEGER NOT NULL, state_id TEXT, turn INTEGER NOT NULL DEFAULT 0,
                phase TEXT NOT NULL DEFAULT '', step TEXT NOT NULL DEFAULT '',
                quality TEXT NOT NULL DEFAULT 'complete', has_action INTEGER NOT NULL DEFAULT 0,
                event_kinds TEXT NOT NULL DEFAULT '', source_line INTEGER,
                PRIMARY KEY (game_id, idx)
            );
            CREATE TABLE IF NOT EXISTS frame_blocks (
                game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                block INTEGER NOT NULL, first_index INTEGER NOT NULL, count INTEGER NOT NULL,
                payload BLOB NOT NULL, PRIMARY KEY (game_id, block)
            );
            CREATE TABLE IF NOT EXISTS game_events (
                game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL, frame_idx INTEGER NOT NULL, turn INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL, seat INTEGER, card_id INTEGER, instance_id INTEGER,
                payload_json TEXT NOT NULL, PRIMARY KEY (game_id, seq)
            );
            CREATE INDEX IF NOT EXISTS events_kind ON game_events(game_id, kind);
            CREATE INDEX IF NOT EXISTS events_card ON game_events(card_id);
            CREATE TABLE IF NOT EXISTS named_decks (
                uid TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT '', last_played TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS deck_bindings (
                deck_id TEXT PRIMARY KEY, deck_uid TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
                bound_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profile (
                key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallet (
                recorded_at TEXT NOT NULL, gems INTEGER, gold INTEGER,
                wc_common INTEGER, wc_uncommon INTEGER, wc_rare INTEGER, wc_mythic INTEGER,
                vault INTEGER, PRIMARY KEY (recorded_at, gems, gold)
            );
            CREATE TABLE IF NOT EXISTS rank_history (
                recorded_at TEXT NOT NULL, track TEXT NOT NULL, class TEXT,
                level INTEGER, step INTEGER, wins INTEGER, losses INTEGER,
                PRIMARY KEY (recorded_at, track, class, level, step)
            );
            CREATE TABLE IF NOT EXISTS drafts (
                draft_id TEXT PRIMARY KEY, event_name TEXT NOT NULL DEFAULT '',
                pack INTEGER, pick INTEGER, pool_json TEXT NOT NULL DEFAULT '[]',
                pack_cards_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS draft_picks (
                draft_id TEXT NOT NULL, pack INTEGER NOT NULL, pick INTEGER NOT NULL,
                card_id INTEGER NOT NULL, pack_cards_json TEXT NOT NULL DEFAULT '[]',
                recorded_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (draft_id, pack, pick)
            );
            CREATE TABLE IF NOT EXISTS captures (
                path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, size INTEGER NOT NULL,
                offset INTEGER NOT NULL, session_id TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL REFERENCES games(id), frame_index INTEGER,
                body TEXT NOT NULL, tags_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id TEXT NOT NULL, title TEXT NOT NULL,
                hypothesis TEXT NOT NULL, changes_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        # A table created by version 1 survives CREATE TABLE IF NOT EXISTS unchanged, so the
        # columns added since then have to be requested one by one.
        self._ensure_column("imports", "imported_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("notes", "created_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("experiments", "created_at", "TEXT NOT NULL DEFAULT ''")
        self.connection.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
                                (str(SCHEMA_VERSION),))
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _restore_legacy(self, games: list[dict]) -> None:
        with self.connection:
            for game in games:
                self._write_game(game, str(game.get("source_sha256") or "legado"))
            self.connection.execute("DROP TABLE IF EXISTS games_v1")

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    # ------------------------------------------------------------------ imports

    def import_count(self) -> int:
        with self.lock:
            return int(self.connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0])

    def import_warnings(self) -> list[str]:
        with self.lock:
            rows = self.connection.execute("SELECT warnings_json FROM imports").fetchall()
            warnings: set[str] = set()
            for row in rows:
                value = _value(row["warnings_json"])
                if isinstance(value, list):
                    warnings.update(item for item in value if isinstance(item, str))
            return sorted(warnings)

    def save_import(self, snapshot: dict, source_kind: str, byte_count: int) -> dict:
        source_hash = str(snapshot["source_sha256"])
        with self.lock:
            existing = self.connection.execute("SELECT 1 FROM imports WHERE sha256 = ?", (source_hash,)).fetchone()
            if existing is not None:
                return {"created": False, "source_sha256": source_hash}
            games = snapshot.get("games", [])
            if not isinstance(games, list):
                raise ValueError("games must be a list")
            with self.connection:
                self.connection.execute(
                    "INSERT INTO imports (sha256, source_kind, byte_count, record_count, "
                    "warnings_json, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (source_hash, source_kind, byte_count, int(snapshot.get("record_count", 0)),
                     _json(snapshot.get("warnings", [])), _now()))
                for game in games:
                    if not isinstance(game, dict):
                        raise ValueError("invalid game")
                    self._upsert_game(game, source_hash)
                self._absorb_side_facts(snapshot)
        return {"created": True, "source_sha256": source_hash}

    def absorb_session(self, snapshot: dict) -> None:
        """Persist games from a live capture, where there is no completed file to hash."""
        with self.lock, self.connection:
            for game in snapshot.get("games", []):
                if isinstance(game, dict):
                    self._upsert_game(game, str(snapshot.get("source_sha256") or "captura"))
            self._absorb_side_facts(snapshot)

    def _absorb_side_facts(self, snapshot: dict) -> None:
        for deck in (snapshot.get("named_decks") or {}).values():
            if not isinstance(deck, dict) or not deck.get("uid"):
                continue
            self.connection.execute(
                "INSERT INTO named_decks (uid, name, format, version, last_played) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
                "name=excluded.name, format=excluded.format, version=excluded.version, "
                "last_played=MAX(excluded.last_played, named_decks.last_played)",
                (deck["uid"], deck.get("name") or "", deck.get("format") or "",
                 str(deck.get("version") or ""), deck.get("last_played") or ""))
        for point in snapshot.get("wallet") or []:
            if not isinstance(point, dict) or not point.get("at"):
                continue
            self.connection.execute(
                "INSERT OR IGNORE INTO wallet (recorded_at, gems, gold, wc_common, wc_uncommon, "
                "wc_rare, wc_mythic, vault) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (point["at"], point.get("Gems"), point.get("Gold"), point.get("WildCardCommons"),
                 point.get("WildCardUnCommons"), point.get("WildCardRares"),
                 point.get("WildCardMythics"), point.get("TotalVaultProgress")))
        for point in snapshot.get("rank_points") or []:
            if not isinstance(point, dict) or not point.get("at") or not point.get("class"):
                continue
            self.connection.execute(
                "INSERT OR IGNORE INTO rank_history (recorded_at, track, class, level, step, "
                "wins, losses) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (point["at"], point.get("track") or "", point.get("class"), point.get("level"),
                 point.get("step"), point.get("wins"), point.get("losses")))
        self._absorb_draft(snapshot.get("draft"))
        for key in ("rank", "inventory", "account"):
            value = snapshot.get(key)
            if value:
                self.connection.execute(
                    "INSERT INTO profile (key, value_json, updated_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, _json(value), _now()))

    def _absorb_draft(self, draft: dict | None) -> None:
        """Keep the draft as it stood at this flush.

        The pool matters after the fact as much as during: the log is discarded on the
        next client restart, and the picks are the only record of what was passed."""
        if not isinstance(draft, dict) or not draft.get("draft_id"):
            return
        self.connection.execute(
            "INSERT INTO drafts (draft_id, event_name, pack, pick, pool_json, pack_cards_json, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(draft_id) DO UPDATE SET "
            "event_name=excluded.event_name, pack=excluded.pack, pick=excluded.pick, "
            "pool_json=excluded.pool_json, pack_cards_json=excluded.pack_cards_json, "
            "updated_at=excluded.updated_at",
            (draft["draft_id"], draft.get("event_name") or "", draft.get("pack"), draft.get("pick"),
             _json(draft.get("pool") or []), _json(draft.get("pack_cards") or []),
             draft.get("updated_at") or _now()))
        for entry in draft.get("picks") or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("card_id"), int):
                continue
            self.connection.execute(
                "INSERT OR REPLACE INTO draft_picks (draft_id, pack, pick, card_id, "
                "pack_cards_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (draft["draft_id"], int(entry.get("pack") or 0), int(entry.get("pick") or 0),
                 entry["card_id"], _json(entry.get("pack_cards") or []), entry.get("at") or _now()))

    def latest_draft(self) -> dict | None:
        """The most recent draft seen, with its picks. None when none was ever read."""
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM drafts ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row is None:
                return None
            picks = self.connection.execute(
                "SELECT pack, pick, card_id, pack_cards_json, recorded_at FROM draft_picks "
                "WHERE draft_id = ? ORDER BY pack, pick", (row["draft_id"],)).fetchall()
        return {"draft_id": row["draft_id"], "event_name": row["event_name"],
                "pack": row["pack"], "pick": row["pick"], "pool": _value(row["pool_json"]),
                "pack_cards": _value(row["pack_cards_json"]), "updated_at": row["updated_at"],
                "picks": [{"pack": item["pack"], "pick": item["pick"], "card_id": item["card_id"],
                           "pack_cards": _value(item["pack_cards_json"]), "at": item["recorded_at"]}
                          for item in picks]}

    # ------------------------------------------------------------------ games

    def _upsert_game(self, game: dict, source_hash: str) -> None:
        game_id = str(game["id"])
        prior = self._read_game(game_id)
        if prior is not None:
            merged = self._merge_game(prior, game)
            if merged is None:
                return
            self._validate_noted_frames(game_id, prior, merged)
            game = merged
        self._write_game(game, source_hash)

    def _write_game(self, game: dict, source_hash: str) -> None:
        game_id = str(game["id"])
        frames = [frame for frame in game.get("frames", []) if isinstance(frame, dict)]
        values = {column: game.get(column) for column in GAME_COLUMNS}
        values["id"] = game_id
        values["source_sha256"] = str(game.get("source_sha256") or source_hash)
        values["frame_count"] = len(frames)
        for column in ("match_id_hashed", "mode", "mode_basis", "format", "event_id",
                       "super_format", "deck_id", "registered_deck_id", "result",
                       "match_result", "result_reason", "status", "quality"):
            values[column] = str(values[column] if values[column] is not None else "")
        for column in ("game_number", "turns", "decision_count", "self_seat",
                       "mulligans_self", "mulligans_opponent"):
            values[column] = int(values[column] or 0)
        values["on_play"] = None if game.get("on_play") is None else int(bool(game["on_play"]))
        placeholders = ", ".join("?" for _ in GAME_COLUMNS)
        updates = ", ".join(f"{column}=excluded.{column}" for column in GAME_COLUMNS if column != "id")
        self.connection.execute(
            f"INSERT INTO games ({', '.join(GAME_COLUMNS)}, deck_json, warnings_json, "
            f"opening_hand_json, drawn_json, opponent_revealed_json) "
            f"VALUES ({placeholders}, ?, ?, ?, ?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, deck_json=excluded.deck_json, "
            "warnings_json=excluded.warnings_json, "
            "opening_hand_json=excluded.opening_hand_json, drawn_json=excluded.drawn_json, "
            "opponent_revealed_json=excluded.opponent_revealed_json",
            (*[values[column] for column in GAME_COLUMNS],
             _json(game.get("deck") or {"main": [], "sideboard": []}),
             _json(game.get("warnings") or []),
             _json(game.get("opening_hand")), _json(game.get("drawn") or []),
             _json(_revealed_by_opponent(frames, values["self_seat"]))))
        self._write_frames(game_id, frames)

    def _write_frames(self, game_id: str, frames: list[dict]) -> None:
        self.connection.execute("DELETE FROM frame_blocks WHERE game_id = ?", (game_id,))
        self.connection.execute("DELETE FROM frame_index WHERE game_id = ?", (game_id,))
        self.connection.execute("DELETE FROM game_events WHERE game_id = ?", (game_id,))
        sequence = 0
        for start in range(0, len(frames), FRAME_BLOCK):
            block = frames[start:start + FRAME_BLOCK]
            self.connection.execute(
                "INSERT INTO frame_blocks (game_id, block, first_index, count, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (game_id, start // FRAME_BLOCK, start, len(block), _pack(block)))
        for frame in frames:
            kinds = sorted({str(event.get("kind")) for event in frame.get("events", [])
                            if isinstance(event, dict)})
            self.connection.execute(
                "INSERT INTO frame_index (game_id, idx, state_id, turn, phase, step, quality, "
                "has_action, event_kinds, source_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (game_id, int(frame.get("index", 0)), str(frame.get("state_id", "")),
                 int(frame.get("turn") or 0), str(frame.get("phase") or ""),
                 str(frame.get("step") or ""), str(frame.get("quality") or "complete"),
                 int(bool(frame.get("action"))), ",".join(kinds), frame.get("source_line")))
            for event in frame.get("events", []):
                if not isinstance(event, dict):
                    continue
                self.connection.execute(
                    "INSERT INTO game_events (game_id, seq, frame_idx, turn, kind, seat, card_id, "
                    "instance_id, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (game_id, sequence, int(frame.get("index", 0)), int(frame.get("turn") or 0),
                     str(event.get("kind")), event.get("seat"), event.get("card_id"),
                     event.get("instance_id"), _json(event)))
                sequence += 1

    def _merge_game(self, previous: dict, replacement: dict) -> dict | None:
        old_frames = [frame for frame in previous.get("frames", []) if isinstance(frame, dict)]
        new_frames = [frame for frame in replacement.get("frames", []) if isinstance(frame, dict)]
        old_ids = {frame.get("state_id") for frame in old_frames if frame.get("state_id") is not None}
        new_ids = {frame.get("state_id") for frame in new_frames if frame.get("state_id") is not None}
        terminal_results = {"win", "loss", "draw"}
        prior_terminal = previous.get("status") == "complete" or previous.get("result") in terminal_results
        replacement_terminal = replacement.get("status") == "complete" or replacement.get("result") in terminal_results
        if new_ids.issubset(old_ids) and not replacement_terminal:
            return None

        merged = {**previous, **replacement}
        known_frames = list(old_frames)
        # A state already stored keeps its position but takes the newer reading: a later
        # import comes from a reader that may resolve more of the same message. Only a
        # state never seen before extends the game.
        position = {frame.get("state_id"): index for index, frame in enumerate(known_frames)}
        next_index = max((int(frame.get("index", -1)) for frame in old_frames), default=-1) + 1
        for frame in new_frames:
            state_id = frame.get("state_id")
            if state_id in old_ids:
                known_frames[position[state_id]] = {**frame, "index": known_frames[position[state_id]].get("index")}
                continue
            known_frames.append({**frame, "index": next_index})
            next_index += 1
        merged["frames"] = known_frames
        if prior_terminal and not replacement_terminal:
            merged["status"] = previous.get("status")
            merged["result"] = previous.get("result")
            merged["match_result"] = previous.get("match_result")
        return merged

    def _validate_noted_frames(self, game_id: str, prior: dict, replacement: dict) -> None:
        old_frames = {item.get("index"): item.get("state_id") for item in prior.get("frames", []) if isinstance(item, dict)}
        new_frames = {item.get("index"): item.get("state_id") for item in replacement.get("frames", []) if isinstance(item, dict)}
        noted = self.connection.execute("SELECT frame_index FROM notes WHERE game_id = ? AND frame_index IS NOT NULL", (game_id,))
        for row in noted:
            index = row["frame_index"]
            if old_frames.get(index) != new_frames.get(index):
                raise ValueError("re-importing would move a frame a note is attached to")

    def _row_to_game(self, row: sqlite3.Row) -> dict:
        game = {column: row[column] for column in GAME_COLUMNS}
        game["on_play"] = None if row["on_play"] is None else bool(row["on_play"])
        game["deck"] = _value(row["deck_json"])
        game["warnings"] = _value(row["warnings_json"])
        game["opening_hand"] = _value(row["opening_hand_json"])
        game["drawn"] = _value(row["drawn_json"])
        game["opponent_revealed"] = _value(row["opponent_revealed_json"])
        return game

    def games(self) -> list[dict]:
        """Summaries only: lists and statistics must never decompress a frame block."""
        with self.lock:
            rows = self.connection.execute("SELECT * FROM games ORDER BY started_at, id").fetchall()
            return [self._row_to_game(row) for row in rows]

    def game_summary(self, game_id: str) -> dict | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            return self._row_to_game(row) if row is not None else None

    def _read_game(self, game_id: str) -> dict | None:
        summary = self.game_summary(game_id)
        if summary is None:
            return None
        summary["frames"] = self.frames(game_id)
        return summary

    def game(self, game_id: str) -> dict | None:
        return self._read_game(game_id)

    def frames(self, game_id: str, start: int = 0, limit: int | None = None) -> list[dict]:
        with self.lock:
            first_block = start // FRAME_BLOCK
            rows = self.connection.execute(
                "SELECT payload FROM frame_blocks WHERE game_id = ? AND block >= ? ORDER BY block",
                (game_id, first_block)).fetchall()
            collected: list[dict] = []
            for row in rows:
                collected.extend(_unpack(row["payload"]))
                if limit is not None and len(collected) >= (start - first_block * FRAME_BLOCK) + limit:
                    break
            offset = start - first_block * FRAME_BLOCK
            window = collected[offset:] if limit is None else collected[offset:offset + limit]
            return window

    def frame(self, game_id: str, index: int) -> dict | None:
        found = self.frames(game_id, index, 1)
        return found[0] if found else None

    def frame_index(self, game_id: str) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT idx, state_id, turn, phase, step, quality, has_action, event_kinds, source_line "
                "FROM frame_index WHERE game_id = ? ORDER BY idx", (game_id,)).fetchall()
            return [{"index": row["idx"], "state_id": row["state_id"], "turn": row["turn"],
                     "phase": row["phase"], "step": row["step"], "quality": row["quality"],
                     "has_action": bool(row["has_action"]), "source_line": row["source_line"],
                     "event_kinds": [kind for kind in row["event_kinds"].split(",") if kind]}
                    for row in rows]

    def events(self, game_id: str, kinds: list[str] | None = None) -> list[dict]:
        with self.lock:
            query = "SELECT frame_idx, turn, payload_json FROM game_events WHERE game_id = ?"
            parameters: list[object] = [game_id]
            if kinds:
                query += f" AND kind IN ({', '.join('?' for _ in kinds)})"
                parameters.extend(kinds)
            rows = self.connection.execute(query + " ORDER BY seq", parameters).fetchall()
            return [{**_value(row["payload_json"]), "frame_index": row["frame_idx"], "turn": row["turn"]}
                    for row in rows]

    def opponent_cards_before(self, match_id_hashed: str, game_number: int, self_seat: int) -> list[int]:
        """Card ids the opponent showed in earlier games of the same match."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT opponent_revealed_json FROM games WHERE match_id_hashed = ? AND game_number < ?",
                (match_id_hashed, game_number)).fetchall()
        revealed: set[int] = set()
        for row in rows:
            value = _value(row["opponent_revealed_json"])
            if isinstance(value, list):
                revealed.update(int(item) for item in value if isinstance(item, int))
        return sorted(revealed)

    def delete_game(self, game_id: str) -> None:
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM notes WHERE game_id = ?", (game_id,))
            self.connection.execute("DELETE FROM frame_blocks WHERE game_id = ?", (game_id,))
            self.connection.execute("DELETE FROM frame_index WHERE game_id = ?", (game_id,))
            self.connection.execute("DELETE FROM game_events WHERE game_id = ?", (game_id,))
            self.connection.execute("DELETE FROM games WHERE id = ?", (game_id,))

    # ------------------------------------------------------------------ user material

    def add_note(self, game_id: str, frame_index: int | None, body: str, tags: list[str]) -> dict:
        with self.lock:
            if self.game_summary(game_id) is None:
                raise KeyError("game not found")
            if frame_index is not None:
                known = self.connection.execute(
                    "SELECT 1 FROM frame_index WHERE game_id = ? AND idx = ?", (game_id, frame_index)).fetchone()
                if known is None:
                    raise ValueError("frame for the note not found")
            with self.connection:
                cursor = self.connection.execute(
                    "INSERT INTO notes (game_id, frame_index, body, tags_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (game_id, frame_index, body, _json(tags), _now()))
        return {"id": cursor.lastrowid, "game_id": game_id, "frame_index": frame_index, "body": body, "tags": tags}

    def notes(self, game_id: str | None = None) -> list[dict]:
        with self.lock:
            query = "SELECT id, game_id, frame_index, body, tags_json, created_at FROM notes"
            parameters: tuple = ()
            if game_id is not None:
                query += " WHERE game_id = ?"
                parameters = (game_id,)
            rows = self.connection.execute(query + " ORDER BY id", parameters).fetchall()
            return [{"id": row["id"], "game_id": row["game_id"], "frame_index": row["frame_index"],
                     "body": row["body"], "tags": _value(row["tags_json"]), "created_at": row["created_at"]}
                    for row in rows]

    def add_experiment(self, deck_id: str, title: str, hypothesis: str, changes: object, status: str) -> dict:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "INSERT INTO experiments (deck_id, title, hypothesis, changes_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (deck_id, title, hypothesis, _json(changes), status, _now()))
        return {"id": cursor.lastrowid, "deck_id": deck_id, "title": title, "hypothesis": hypothesis,
                "changes": changes, "status": status}

    def experiments(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, deck_id, title, hypothesis, changes_json, status, created_at FROM experiments ORDER BY id"
            ).fetchall()
            return [{"id": row["id"], "deck_id": row["deck_id"], "title": row["title"],
                     "hypothesis": row["hypothesis"], "changes": _value(row["changes_json"]),
                     "status": row["status"], "created_at": row["created_at"]} for row in rows]

    # ------------------------------------------------------------------ decks and profile

    def named_decks(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT uid, name, format, version, last_played FROM named_decks ORDER BY last_played DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def bind_deck(self, deck_id: str, deck_uid: str, label: str) -> dict:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO deck_bindings (deck_id, deck_uid, label, bound_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(deck_id) DO UPDATE SET "
                "deck_uid=excluded.deck_uid, label=excluded.label, bound_at=excluded.bound_at",
                (deck_id, deck_uid, label, _now()))
        return {"deck_id": deck_id, "deck_uid": deck_uid, "label": label}

    def deck_bindings(self) -> dict[str, dict]:
        with self.lock:
            rows = self.connection.execute("SELECT deck_id, deck_uid, label FROM deck_bindings").fetchall()
            return {row["deck_id"]: dict(row) for row in rows}

    def set_profile(self, key: str, value: object) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO profile (key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at", (key, _json(value), _now()))

    def profile(self, key: str) -> object:
        with self.lock:
            row = self.connection.execute("SELECT value_json FROM profile WHERE key = ?", (key,)).fetchone()
            return _value(row["value_json"]) if row is not None else None

    # ------------------------------------------------------------------ capture

    def capture_state(self, path: str) -> dict | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM captures WHERE path = ?", (path,)).fetchone()
            return dict(row) if row is not None else None

    def save_capture_state(self, path: str, fingerprint: str, size: int, offset: int, session_id: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO captures (path, fingerprint, size, offset, session_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
                "fingerprint=excluded.fingerprint, size=excluded.size, offset=excluded.offset, "
                "session_id=excluded.session_id, updated_at=excluded.updated_at",
                (path, fingerprint, size, offset, session_id, _now()))

    def wallet(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT recorded_at, gems, gold, wc_common, wc_uncommon, wc_rare, wc_mythic, vault "
                "FROM wallet ORDER BY recorded_at").fetchall()
            return [dict(row) for row in rows]

    def rank_history(self, track: str | None = None) -> list[dict]:
        with self.lock:
            sql = ("SELECT recorded_at, track, class, level, step, wins, losses "
                   "FROM rank_history")
            parameters: tuple = ()
            if track:
                sql += " WHERE track = ?"
                parameters = (track,)
            rows = self.connection.execute(sql + " ORDER BY recorded_at", parameters).fetchall()
        return [dict(row) for row in rows]

    def database_bytes(self) -> int:
        """Size on disk including the write-ahead log, which holds the newest rows."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = self.path.with_name(self.path.name + suffix)
            if candidate.exists():
                total += candidate.stat().st_size
        return total
