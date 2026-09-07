"""Application service for local manual MTGA game review."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Callable

from . import analysis, build, coach, economy, pick, timeline
from .community import CommunityGrades
from .ingest import format_name, relabel_action
from .art import CREDIT as ART_CREDIT, ArtCache
from .decklist import format_arena, parse_arena
from .draft import raw_status, write_raw
from .limited import CREDIT as LIMITED_CREDIT, FORMATS, LimitedRatings
from .rulings import CREDIT as RULINGS_CREDIT, RulingsCache
from .secrets import KeyStore
from .storage import ReviewStore

Importer = Callable[[bytes], dict]
# The configured log is streamed, so its ceiling only guards against a runaway file.
MAX_LOG_BYTES = 512 * 1024 * 1024
# A browser upload is held whole in memory; the follower is the path for a long session.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
READ_CHUNK = 4 * 1024 * 1024
TERMINAL = {"win", "loss", "draw"}
# Rates to read the prize table against when a player has no limited record of their own.
REFERENCE_RATES = (0.40, 0.50, 0.55, 0.60)
# Under this many completed limited games, a measured rate is noise.
LIMITED_MIN_GAMES = 20


def _is_limited(game: dict) -> bool:
    """A draft or sealed game, told from the event the client recorded."""
    event = str(game.get("event_id") or "") + str(game.get("format") or "")
    return "Draft" in event or "Sealed" in event


# Below this many nonland cards seen, the opponent's colours are a guess, not a reading.
MATCHUP_MIN_CARDS = 3
# Arena's ladder, lowest first. Mythic is a percentile rather than a tier, so it sits at
# the top as one step and the app does not pretend to place a player inside it.
RANK_TIERS = ("Bronze", "Silver", "Gold", "Platinum", "Diamond", "Mythic")
LEVELS_PER_TIER = 4


def rank_position(point: dict, steps_in_tier: dict | None = None) -> float:
    """A height for the curve. Ordinal only — the distance between two tiers is not a claim.

    Arena counts levels downward inside a tier: level 4 is the bottom of Gold and level 1
    is the top. The step inside a level has to be part of the height or the curve is flat
    for the weeks a player spends inside one tier — which is most of them. How many steps
    a level holds is not stated anywhere in the log, so it is taken from the highest step
    actually seen in that tier, and the answer says that is where it came from.
    """
    try:
        tier = RANK_TIERS.index(str(point.get("class")))
    except ValueError:
        return 0.0
    level = point.get("level")
    if not isinstance(level, int) or not 1 <= level <= LEVELS_PER_TIER:
        return float(tier * LEVELS_PER_TIER)
    height = float(tier * LEVELS_PER_TIER + (LEVELS_PER_TIER - level))
    step = point.get("step")
    ceiling = (steps_in_tier or {}).get(str(point.get("class")))
    if isinstance(step, int) and isinstance(ceiling, int) and ceiling > 0:
        height += min(step, ceiling) / (ceiling + 1)
    return round(height, 3)


def steps_seen(points: list) -> dict:
    """The highest step observed per tier. A floor on the real number, never an invention."""
    seen: dict[str, int] = {}
    for point in points:
        name, step = str(point.get("class")), point.get("step")
        if isinstance(step, int) and step > seen.get(name, 0):
            seen[name] = step
    return seen


def _rank_label(point: dict) -> str:
    level = point.get("level")
    return f"{point.get('class')}{f' {level}' if isinstance(level, int) else ''}"
SUMMARY_FIELDS = ("id", "match_id_hashed", "game_number", "mode", "mode_basis", "format",
                  "event_id", "deck_id", "result", "match_result", "result_reason", "status",
                  "turns", "decision_count", "quality", "started_at", "on_play",
                  "mulligans_self", "mulligans_opponent", "opponent_name", "completed_reason",
                  "frame_count")


def _default_data_dir() -> Path:
    from .config import default_data_dir

    return default_data_dir()


def community_scale() -> str:
    from .community import SCALE

    return SCALE


def limited_format(event_name: str) -> str:
    """Which 17Lands table describes this event.

    The tables are per format because the formats play differently: the bots in a quick
    draft do not pick like a table of people, and the cards that win there are not always
    the cards that win in premier.
    """
    name = str(event_name or "")
    if "Quick" in name or "Bot" in name:
        return "QuickDraft"
    if "Trad" in name:
        return "TradSealed" if "Sealed" in name else "TradDraft"
    if "Sealed" in name:
        return "Sealed"
    return "PremierDraft"


def _default_importer(data: bytes) -> dict:
    from .ingest import ingest_log

    return ingest_log(data)


class CoachService:
    def __init__(
        self,
        data_dir: Path | None = None,
        log_path: Path | None = None,
        card_database_path: Path | None = None,
        importer: Importer = _default_importer,
    ) -> None:
        self.data_dir = data_dir if data_dir is not None else _default_data_dir()
        self.log_path = log_path
        self.card_database_path = card_database_path
        self.importer = importer
        self.store = ReviewStore(self.data_dir)
        self.watcher = None
        self.art = ArtCache(self.data_dir)
        self.rulings = RulingsCache(self.data_dir)
        self.limited = LimitedRatings(self.data_dir)
        self.community = CommunityGrades(self.data_dir)
        self.keys = KeyStore(self.data_dir)
        # The catalogue is a read-only file that never changes while the app runs, so a
        # resolved card can be kept; the summary asks for the same ids on every call.
        self._cards: dict[int, dict] = {}

    # ------------------------------------------------------------------ import

    def import_configured(self) -> dict:
        """Read the configured log in chunks: a played-out session is far too big to hold."""
        if self.log_path is None:
            from .config import default_log_path

            self.log_path = default_log_path()
        if self.log_path is None or not self.log_path.is_file():
            raise ValueError("configured file not found")
        if self.log_path.stat().st_size > MAX_LOG_BYTES:
            raise ValueError("file exceeds the limit")
        if self.importer is not _default_importer:
            return self._import(self.log_path.read_bytes(), "configured")
        return self._import_stream(self.log_path, "configured")

    def _import_stream(self, path: Path, source_kind: str) -> dict:
        from .ingest import LogIngestor

        digest = sha256()
        ingestor = LogIngestor(None)
        total = 0
        with path.open("rb") as source:
            while chunk := source.read(READ_CHUNK):
                digest.update(chunk)
                total += len(chunk)
                ingestor.feed(chunk)
        ingestor.finish()
        ingestor.source_sha256 = digest.hexdigest()
        snapshot = ingestor.snapshot(drain_raw=True)
        write_raw(self.data_dir, snapshot.get("draft_raw") or [])
        for game in snapshot["games"]:
            game["source_sha256"] = digest.hexdigest()
        result = self.store.save_import(snapshot, source_kind, total)
        return {**result, "record_count": snapshot.get("record_count", 0),
                "games": len(snapshot.get("games", [])), "warnings": snapshot.get("warnings", [])}

    def import_upload(self, body: bytes) -> dict:
        if len(body) > MAX_UPLOAD_BYTES:
            raise ValueError("file exceeds the limit")
        return self._import(body, "upload")

    def _import(self, body: bytes, source_kind: str) -> dict:
        if len(body) > MAX_LOG_BYTES:
            raise ValueError("file exceeds the limit")
        normalized = self.importer(body)
        write_raw(self.data_dir, normalized.get("draft_raw") or [])
        source_hash = str(normalized.get("source_sha256") or sha256(body).hexdigest())
        normalized = {**normalized, "source_sha256": source_hash}
        result = self.store.save_import(normalized, source_kind, len(body))
        return {**result, "record_count": int(normalized.get("record_count", 0)),
                "games": len(normalized.get("games", [])),
                "warnings": normalized.get("warnings", [])}

    # ------------------------------------------------------------------ lists

    def games(self) -> dict:
        return {"games": [self._summary_game(game) for game in self.store.games()]}

    def _summary_game(self, game: dict) -> dict:
        summary = {key: game.get(key) for key in SUMMARY_FIELDS}
        summary["deck_label"] = self._deck_label(str(game.get("deck_id", "")))
        # The format label is derived on read, so a row stored under an older build (or an
        # older interface language) still reads the way the interface reads today.
        summary["format"] = format_name(str(game.get("event_id") or "")) or summary["format"]
        return summary

    def _deck_label(self, deck_id: str) -> str:
        binding = self.store.deck_bindings().get(deck_id)
        if binding:
            return binding.get("label") or binding.get("deck_uid") or deck_id
        return f"Composition {deck_id[:8]}"

    def summary(self) -> dict:
        games = self.store.games()
        decks: dict[str, list[dict]] = {}
        for game in games:
            decks.setdefault(str(game.get("deck_id", "unknown")), []).append(game)
        return {
            **self._metrics(games),
            "by_mode": self._by_mode_metrics(games),
            "by_start": self._by_start_metrics(games),
            "decks": [self._deck_summary(deck_id, deck_games) for deck_id, deck_games in sorted(decks.items())],
            "imports": self.store.import_count(), "review_mode": "manual",
            "rank": self.store.profile("rank"), "inventory": self.store.profile("inventory"),
            "named_decks": self.store.named_decks(), "database_bytes": self.store.database_bytes(),
            "capture": self.capture_status(), "art": self.art_status(),
            "coach": self.coach_status(), "art_credit": ART_CREDIT,
            "rulings": self.rulings_status(), "rulings_credit": RULINGS_CREDIT,
            "warnings": sorted({*self.store.import_warnings(),
                                *(warning for game in games for warning in game.get("warnings", [])
                                  if isinstance(warning, str))}),
        }

    def _metrics(self, games: list[dict]) -> dict:
        completed = [game for game in games if game.get("status") == "complete" and game.get("result") in TERMINAL]
        wins = sum(game.get("result") == "win" for game in completed)
        losses = sum(game.get("result") == "loss" for game in completed)
        draws = sum(game.get("result") == "draw" for game in completed)
        match_results: dict[str, str] = {}
        for game in games:
            result = game.get("match_result")
            if isinstance(result, str) and result in TERMINAL:
                match_results[str(game.get("match_id_hashed"))] = result
        match_wins = sum(result == "win" for result in match_results.values())
        match_losses = sum(result == "loss" for result in match_results.values())
        return {
            "games": len(games), "completed": len(completed), "wins": wins, "losses": losses, "draws": draws,
            "win_rate": wins / (wins + losses) if wins + losses else None,
            "interval": analysis.wilson_interval(wins, wins + losses) if wins + losses else None,
            "matches": len({game.get("match_id_hashed") for game in games}),
            "match_wins": match_wins, "match_losses": match_losses,
            "match_win_rate": match_wins / (match_wins + match_losses) if match_wins + match_losses else None,
        }

    def _by_mode_metrics(self, games: list[dict]) -> dict:
        result = {mode: self._metrics([game for game in games if game.get("mode") == mode]) for mode in ("BO1", "BO3")}
        bo3_games = [game for game in games if game.get("mode") == "BO3"]
        result["BO3"]["by_stage"] = {
            "game1": self._metrics([game for game in bo3_games if game.get("game_number") == 1]),
            "post_sideboard": self._metrics([game for game in bo3_games if isinstance(game.get("game_number"), int) and game["game_number"] > 1]),
        }
        return result

    def _by_start_metrics(self, games: list[dict]) -> dict:
        """On the play versus on the draw — the split every tracker reports and the log proves."""
        return {
            "on_play": self._metrics([game for game in games if game.get("on_play") is True]),
            "on_draw": self._metrics([game for game in games if game.get("on_play") is False]),
            "unknown": len([game for game in games if game.get("on_play") is None]),
        }

    def _deck_summary(self, deck_id: str, games: list[dict]) -> dict:
        metrics = self._metrics(games)
        deck = games[-1].get("deck", {"main": [], "sideboard": []})
        return {"id": deck_id, "label": self._deck_label(deck_id), "colours": self.deck_colours(deck),
                "main_count": sum(item.get("quantity", 0) for item in deck.get("main", []) if isinstance(item, dict)),
                "game_count": len(games), **metrics, "by_mode": self._by_mode_metrics(games),
                "by_start": self._by_start_metrics(games), "deck": deck}

    # ------------------------------------------------------------------ detail

    def game_detail(self, game_id: str) -> dict | None:
        game = self.store.game_summary(game_id)
        if game is None:
            return None
        detail = dict(game)
        detail["deck_label"] = self._deck_label(str(game.get("deck_id", "")))
        detail["format"] = format_name(str(game.get("event_id") or "")) or detail["format"]
        detail["notes"] = self.store.notes(game_id)
        detail["frame_index"] = self.store.frame_index(game_id)
        detail["cards"] = {str(card_id): card for card_id, card in self._cards_for_game(game).items()}
        return detail

    def frames(self, game_id: str, start: int = 0, limit: int = 25) -> dict:
        if self.store.game_summary(game_id) is None:
            raise KeyError("game not found")
        limit = max(1, min(int(limit), 100))
        frames = [self._relabel(frame) for frame in self.store.frames(game_id, max(0, int(start)), limit)]
        return {"game_id": game_id, "start": start, "count": len(frames), "frames": frames}

    @staticmethod
    def _relabel(frame: dict) -> dict:
        """Derive every label on read, so a frame stored by an earlier build reads correctly.

        This is not only cosmetic: the same frame is what the sanitised export hands to the
        model, and a stale label would travel with it.
        """
        frame = dict(frame)
        if isinstance(frame.get("action"), dict):
            frame["action"] = relabel_action(frame["action"])
        for key in ("actions", "available_actions"):
            if isinstance(frame.get(key), list):
                frame[key] = [relabel_action(item) for item in frame[key]]
        if isinstance(frame.get("events"), list):
            frame["events"] = [{**event, "label": timeline.KIND_LABELS.get(event.get("kind"), event.get("label"))}
                               if isinstance(event, dict) else event for event in frame["events"]]
        return frame

    def timeline(self, game_id: str) -> dict:
        """Turn-by-turn narrative built from the annotations, not from the board snapshots."""
        game = self.store.game_summary(game_id)
        if game is None:
            raise KeyError("game not found")
        events = self.store.events(game_id)
        cards = self.cards(sorted({event["card_id"] for event in events if event.get("card_id")}))

        def name(card_id):
            card = cards.get(str(card_id)) or {}
            return card.get("name") or f"Carta #{card_id}"

        seat = game.get("self_seat")
        described = []
        for event in self._readable_events(events):
            described.append({**event, "is_self": event.get("seat") == seat if seat else None,
                              "text": timeline.describe(event, name, seat)})
        return {"game_id": game_id, "self_seat": seat, "events": described,
                "cards": cards, "counts": self._event_counts(events)}

    @staticmethod
    def _readable_events(events: list[dict]) -> list[dict]:
        """Drop the action annotation when another event of the same frame already says it.

        The engine reports a play twice: once as the user action and once as the zone
        transfer it caused. The transfer names the card and the zones, so it is the one
        worth reading; the bare action survives only when nothing else covers that object.
        """
        covered: dict[int, set] = {}
        for event in events:
            if event["kind"] != "action" and event.get("instance_id") is not None:
                covered.setdefault(event["frame_index"], set()).add(event["instance_id"])
        readable = []
        for event in events:
            if event["kind"] == "action":
                instance = event.get("instance_id")
                # An action that names no card says only "the player did something".
                if (instance is None or not event.get("card_id")
                        or instance in covered.get(event["frame_index"], set())):
                    continue
            readable.append(event)
        return readable

    @staticmethod
    def _event_counts(events: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for event in events:
            counts[event["kind"]] = counts.get(event["kind"], 0) + 1
        return dict(sorted(counts.items()))

    def cards(self, ids: list[int]) -> dict:
        from .catalog import resolve_cards

        wanted = [int(card_id) for card_id in ids]
        missing = [card_id for card_id in wanted if card_id not in self._cards]
        if missing:
            self._cards.update(resolve_cards(missing, self.card_database_path))
        return {str(card_id): self._cards[card_id] for card_id in wanted if card_id in self._cards}

    def deck_colours(self, deck: dict) -> list[str]:
        """Colour identity of a list, in the printed WUBRG order.

        Colour is the one visual property of a deck that is also a fact about it, so the
        interface can lean on it instead of inventing a palette.
        """
        entries = [item for section in ("main", "sideboard")
                   for item in deck.get(section, []) if isinstance(item, dict)]
        cards = self.cards(sorted({item["id"] for item in entries if isinstance(item.get("id"), int)}))
        seen = {colour for card in cards.values() for colour in card.get("colors") or []}
        return [colour for colour in "WUBRG" if colour in seen]

    def _cards_for_game(self, game: dict) -> dict[int, dict]:
        card_ids: set[int] = set()
        deck = game.get("deck", {})
        if isinstance(deck, dict):
            for cards in (deck.get("main", []), deck.get("sideboard", [])):
                for card in cards if isinstance(cards, list) else []:
                    if isinstance(card, dict) and isinstance(card.get("id"), int):
                        card_ids.add(card["id"])
        for event in self.store.events(str(game.get("id"))):
            if isinstance(event.get("card_id"), int):
                card_ids.add(event["card_id"])
        return {int(card_id): card for card_id, card in self.cards(sorted(card_ids)).items()}

    # ------------------------------------------------------------------ in-game analysis

    def library_state(self, game_id: str, index: int) -> dict:
        """What is left in the player's own library at a frame, and the odds from it.

        Exact for the player's deck, because Arena discloses every zone the player sees.
        It is not computed for the opponent, whose list the log never states.
        """
        game = self.store.game_summary(game_id)
        if game is None:
            raise KeyError("game not found")
        frame = self.store.frame(game_id, index)
        if frame is None:
            raise ValueError("frame not found")
        frame = self._relabel(frame)
        seat = game.get("self_seat")
        deck_counts = {item["id"]: item["quantity"] for item in game.get("deck", {}).get("main", [])
                       if isinstance(item, dict) and isinstance(item.get("id"), int)}
        if not seat or not deck_counts:
            return {"eligible": False, "reason": "No registered deck or identified seat."}
        seen: dict[int, int] = {}
        for zone in frame.get("zones", []):
            if zone.get("type") in ("Library", "Sideboard"):
                continue
            for card in zone.get("objects", []):
                if card.get("owner") == seat and isinstance(card.get("card_id"), int):
                    seen[card["card_id"]] = seen.get(card["card_id"], 0) + 1
        remaining = analysis.remaining_library(deck_counts, seen)
        library = next((zone for zone in frame.get("zones", [])
                        if zone.get("type") == "Library" and zone.get("owner") == seat), None)
        reported = library.get("total_count") if isinstance(library, dict) else None
        size = sum(remaining.values())
        cards = self.cards(sorted(remaining))
        entries = []
        for card_id, quantity in sorted(remaining.items(), key=lambda item: -item[1]):
            card = cards.get(str(card_id)) or {}
            entries.append({"card_id": card_id, "quantity": quantity,
                            "name": card.get("name") or f"Carta #{card_id}",
                            "is_land": bool(card.get("is_land")),
                            "mana_value": card.get("mana_value"),
                            "next_draw": quantity / size if size else None,
                            "within_three": analysis.hypergeometric_at_least(1, quantity, size, min(3, size)) if size else None})
        lands = sum(quantity for entry, quantity in
                    ((cards.get(str(cid)) or {}, qty) for cid, qty in remaining.items()) if entry.get("is_land"))
        return {"eligible": True, "size": size, "reported_size": reported,
                "matches_report": reported is None or reported == size,
                "lands": lands, "land_ratio": lands / size if size else None,
                "entries": entries, "seen": len(deck_counts) - len(remaining)}

    def live_game(self) -> dict:
        """The game being played right now, with what is left in the library.

        This is the same arithmetic the replay shows, asked of the newest frame instead of
        a chosen one. It is a panel beside the game, not an overlay on it: the app reads a
        log and draws in a browser window, and it will never sit on top of Arena.
        """
        if self.watcher is None or not self.watcher.status().get("running"):
            return {"live": False, "reason": (
                "Not following. Turn Follow matches on and the panel updates while you play.")}
        games = [game for game in self.store.games()
                 if game.get("status") != "complete" and game.get("frame_count")]
        if not games:
            return {"live": True, "playing": False, "reason": (
                "Following, but no game is in progress. The panel fills in on the first turn.")}
        game = max(games, key=lambda item: (str(item.get("started_at") or ""), item.get("id")))
        index = self.store.frame_index(str(game["id"]))
        if not index:
            return {"live": True, "playing": False, "reason": "No frame recorded for this game yet."}
        last = index[-1]
        library = self.library_state(str(game["id"]), int(last["index"]))
        frame = self.store.frame(str(game["id"]), int(last["index"])) or {}
        seat = game.get("self_seat")
        players = [{"seat": player.get("seat"), "life": player.get("life"),
                    "is_self": player.get("seat") == seat}
                   for player in frame.get("players", []) if isinstance(player, dict)]
        return {"live": True, "playing": True, "game_id": game["id"],
                "deck_label": self._deck_label(str(game.get("deck_id", ""))),
                "turn": frame.get("turn"), "phase": frame.get("phase"), "step": frame.get("step"),
                "frame": int(last["index"]), "frames": len(index),
                "opponent_name": game.get("opponent_name"), "players": players,
                "library": library,
                "note": ("Read from the log as Arena writes it, so it lags the game by up to "
                         "the follower's polling interval. The library is exact for your own "
                         "deck and is never computed for the opponent.")}

    def opponent_profile(self, game_id: str) -> dict:
        """Everything the opponent showed, with no archetype guessed on top of it."""
        game = self.store.game_summary(game_id)
        if game is None:
            raise KeyError("game not found")
        seat = game.get("self_seat")
        # Read from what was visible on the board, not from the event stream: a permanent
        # already in play when the game state resynchronised produces no event and was
        # still seen.
        revealed = sorted({card_id for card_id in game.get("opponent_revealed") or []
                           if isinstance(card_id, int)})
        cards = self.cards(revealed)
        colours: dict[str, int] = {}
        entries = []
        for card_id in revealed:
            card = cards.get(str(card_id)) or {}
            for colour in card.get("colors") or []:
                colours[colour] = colours.get(colour, 0) + 1
            entries.append({"card_id": card_id, "name": card.get("name") or f"Carta #{card_id}",
                            "mana_cost": card.get("mana_cost"), "type_line": card.get("type_line"),
                            "is_land": bool(card.get("is_land"))})
        prior = self.store.opponent_cards_before(str(game.get("match_id_hashed")),
                                                 int(game.get("game_number") or 1), int(seat or 0))
        return {"game_id": game_id, "opponent_name": game.get("opponent_name"),
                "cards": entries, "colours": dict(sorted(colours.items())),
                "prior_games": prior,
                "note": ("Partial list: only the cards the opponent actually showed. "
                         "It is not their deck and it names no archetype.")}

    # ------------------------------------------------------------------ deck analysis

    def deck_report(self, deck_id: str) -> dict:
        games = [game for game in self.store.games() if str(game.get("deck_id")) == deck_id]
        if not games:
            raise KeyError("deck not observed")
        deck = games[-1].get("deck", {"main": [], "sideboard": []})
        main = deck.get("main", [])
        side = deck.get("sideboard", [])
        cards = self.cards(sorted({item["id"] for item in main + side
                                   if isinstance(item, dict) and isinstance(item.get("id"), int)}))
        entries = [{"card": cards.get(str(item["id"]), {}), "quantity": item.get("quantity", 0)}
                   for item in main if isinstance(item, dict)]
        resolved = [entry for entry in entries if entry["card"].get("resolved")]
        unresolved = [entry for entry in entries if not entry["card"].get("resolved")]
        spells = [entry for entry in resolved if not entry["card"].get("is_land")]
        spell_count = sum(entry["quantity"] for entry in spells)
        average = (sum(entry["card"]["mana_value"] * entry["quantity"] for entry in spells) / spell_count
                   if spell_count else None)
        sources = analysis.colour_sources(entries)
        from .catalog import wildcard_cost

        return {
            "deck_id": deck_id, "label": self._deck_label(deck_id), "colours": self.deck_colours(deck),
            "games": len(games), **self._metrics(games), "by_start": self._by_start_metrics(games),
            "deck": deck, "cards": cards,
            "unresolved": [entry["card"].get("id") for entry in unresolved],
            "curve": analysis.mana_curve(entries),
            "average_mana_value": round(average, 2) if average is not None else None,
            "lands": sources["total_lands"], "sources": sources["by_colour"],
            "lands_recommended": analysis.lands_recommended(average) if average is not None else None,
            "colour_requirements": analysis.colour_requirements(entries),
            "flexible_costs": analysis.flexible_costs(entries),
            "wildcards": wildcard_cost(entries + [{"card": cards.get(str(item["id"]), {}),
                                                   "quantity": item.get("quantity", 0)}
                                                  for item in side if isinstance(item, dict)],
                                        self.store.owned_cards()),
            "karsten_citation": analysis.KARSTEN_2022_CITATION,
            "bo1_caveat": analysis.BO1_SMOOTHING_CAVEAT,
            "card_stats": self.card_stats(deck_id),
        }

    def card_stats(self, deck_id: str) -> dict:
        """Personal win rate with the card in hand, with the interval and the honest floor.

        The definition mirrors the public "games in hand" metric: a game counts for a card
        when the card was in the opening hand or drawn later. The sample of one player is
        far below what separates a good card from an average one, so the required sample
        size travels with the numbers.
        """
        games = [game for game in self.store.games()
                 if str(game.get("deck_id")) == deck_id and game.get("status") == "complete"
                 and game.get("result") in ("win", "loss")]
        seen: dict[int, list[int]] = {}
        for game in games:
            won = 1 if game.get("result") == "win" else 0
            in_hand = set(game.get("opening_hand") or []) | set(game.get("drawn") or [])
            for card_id in in_hand:
                record = seen.setdefault(card_id, [0, 0])
                record[0] += won
                record[1] += 1
        cards = self.cards(sorted(seen))
        rows = []
        for card_id, (wins, total) in sorted(seen.items(), key=lambda item: -item[1][1]):
            card = cards.get(str(card_id)) or {}
            rows.append({"card_id": card_id, "name": card.get("name") or f"Carta #{card_id}",
                         "games_in_hand": total, "wins": wins,
                         "interval": analysis.wilson_interval(wins, total)})
        return {"rows": rows, "sample": len(games),
                "games_to_detect_five_points": analysis.games_needed(0.50, 0.05),
                "note": ("Personal sample. An interval that straddles the deck's overall rate "
                         "does not tell the card apart from chance.")}

    def compare_samples(self, first: dict, second: dict) -> dict:
        """Two win-rate samples side by side, with what it would take to tell them apart."""
        def interval(sample):
            wins, total = int(sample.get("wins", 0)), int(sample.get("games", 0))
            return {"wins": wins, "games": total, "interval": analysis.wilson_interval(wins, total)}
        left, right = interval(first), interval(second)
        overlap = None
        if left["interval"] and right["interval"]:
            overlap = not (left["interval"]["high"] < right["interval"]["low"]
                           or right["interval"]["high"] < left["interval"]["low"])
        baseline = left["interval"]["rate"] if left["interval"] else 0.5
        baseline = min(max(baseline, 0.05), 0.90)
        return {"first": left, "second": right, "intervals_overlap": overlap,
                "games_needed_for_five_points": analysis.games_needed(baseline, 0.05),
                "verdict": ("The samples do not separate: the intervals overlap."
                            if overlap in (True, None) else
                            "The intervals do not overlap in this sample."),
                "warning": ("Checking after every game and stopping when it looks good inflates "
                            "the false positive rate. Fix the sample size before you start.")}

    # ------------------------------------------------------------------ user material

    def save_note(self, payload: dict) -> dict:
        game_id = payload.get("game_id")
        body = payload.get("body")
        frame_index = payload.get("frame_index")
        tags = payload.get("tags", [])
        if not isinstance(game_id, str) or not isinstance(body, str) or not body.strip() or len(body) > 4000:
            raise ValueError("invalid note")
        if frame_index is not None and (not isinstance(frame_index, int) or frame_index < 0):
            raise ValueError("invalid note position")
        if not isinstance(tags, list) or any(not isinstance(tag, str) or len(tag) > 64 for tag in tags):
            raise ValueError("invalid tags")
        return self.store.add_note(game_id, frame_index, body.strip(), tags)

    def notes(self) -> list[dict]:
        return self.store.notes()

    def save_experiment(self, payload: dict) -> dict:
        deck_id, title, hypothesis, changes, status = (payload.get(key) for key in ("deck_id", "title", "hypothesis", "changes", "status"))
        if not all(isinstance(item, str) and item.strip() for item in (deck_id, title, hypothesis, status)) or len(title) > 200 or len(hypothesis) > 4000:
            raise ValueError("invalid experiment")
        if not isinstance(changes, (dict, list, str)) or isinstance(changes, str) and len(changes) > 4000:
            raise ValueError("invalid changes")
        if status not in {"planned", "active", "completed", "abandoned"}:
            raise ValueError("estado do invalid experiment")
        if not any(game.get("deck_id") == deck_id for game in self.store.games()):
            raise ValueError("deck not observed")
        return self.store.add_experiment(deck_id, title, hypothesis, changes, status)

    def experiments(self) -> list[dict]:
        return self.store.experiments()

    def bind_deck(self, payload: dict) -> dict:
        deck_id, deck_uid = payload.get("deck_id"), payload.get("deck_uid")
        if not isinstance(deck_id, str) or not isinstance(deck_uid, str) or not deck_id or not deck_uid:
            raise ValueError("invalid binding")
        known = {deck["uid"]: deck for deck in self.store.named_decks()}
        if deck_uid not in known:
            raise ValueError("named deck not observed")
        if not any(game.get("deck_id") == deck_id for game in self.store.games()):
            raise ValueError("composition not observed")
        return self.store.bind_deck(deck_id, deck_uid, known[deck_uid].get("name") or deck_uid)

    # ------------------------------------------------------------------ capture

    def capture_status(self) -> dict:
        if self.watcher is None:
            return {"running": False, "reason": "follower not started"}
        return self.watcher.status()

    def set_capture(self, running: bool) -> dict:
        """Start or stop following the log. Reading is the whole of it: nothing is written."""
        if self.watcher is None:
            from .capture import LogWatcher

            self.watcher = LogWatcher(self, self.log_path)
        return self.watcher.start() if running else self.watcher.stop()

    # ------------------------------------------------------------------ card art

    def art_status(self) -> dict:
        return {**self.art.stats(), "enabled": bool(self.store.profile("art_enabled")),
                "source": "scryfall"}

    def set_art(self, enabled: bool) -> dict:
        self.store.set_profile("art_enabled", bool(enabled))
        return self.art_status()

    def art_file(self, card_id: int):
        path = self.art.path_for(card_id)
        return path if path.is_file() else None

    def fetch_art(self, card_ids: list[int]) -> dict:
        """Download the missing art for these cards. Only card identifiers leave the machine."""
        if not self.store.profile("art_enabled"):
            raise ValueError("card art is switched off")
        cards = self.cards(sorted({int(value) for value in card_ids if int(value) > 0}))
        result = self.art.fetch(list(cards.values()))
        return {**result, **self.art_status()}

    def deck_art_ids(self, deck_id: str) -> list[int]:
        games = [game for game in self.store.games() if str(game.get("deck_id")) == deck_id]
        if not games:
            raise KeyError("deck not observed")
        deck = games[-1].get("deck", {})
        return [item["id"] for section in ("main", "sideboard")
                for item in deck.get(section, []) if isinstance(item, dict)]

    def rulings_status(self) -> dict:
        return {**self.rulings.stats(), "enabled": bool(self.store.profile("rulings_enabled"))}

    def set_rulings(self, enabled: bool) -> dict:
        self.store.set_profile("rulings_enabled", bool(enabled))
        return self.rulings_status()

    def fetch_rulings(self, card_ids: list[int]) -> dict:
        """Ask about the cards in front of the player, and never about the whole catalogue."""
        if not self.store.profile("rulings_enabled"):
            raise ValueError("card rulings are switched off")
        cards = self.cards(sorted({int(value) for value in card_ids if int(value) > 0}))
        return {**self.rulings.fetch(list(cards.values())), **self.rulings_status()}

    def rulings_for(self, card_ids: list[int]) -> dict:
        return {str(card_id): items
                for card_id, items in self.rulings.for_cards(sorted(set(card_ids))).items()}

    def rank_history(self, track: str = "constructed") -> dict:
        """The climb, read from the readings the log restates.

        Arena writes the rank the same way it writes the wallet: it never reports a change,
        it repeats the current position. So the curve starts when this app started looking,
        not when the account started playing, and the screen says so rather than implying
        a complete history.
        """
        points = self.store.rank_history(track)
        if not points:
            return {"track": track, "points": [], "note": (
                "No rank reading stored yet. The log states the rank while the client runs, "
                "so the curve begins at the first session this app followed.")}
        ceilings = steps_seen(points)
        marked = [{**point, "position": rank_position(point, ceilings)} for point in points]
        first, last = marked[0], marked[-1]
        return {
            "track": track, "points": marked, "readings": len(marked),
            "from": first["recorded_at"], "to": last["recorded_at"],
            "first": _rank_label(first), "last": _rank_label(last),
            "moved": last["position"] - first["position"],
            "tiers": RANK_TIERS, "steps_seen": ceilings,
            "wins": last.get("wins"), "losses": last.get("losses"),
            "note": ("Measured from rank readings in the log. The height inside a tier uses the "
                     "highest step seen in that tier, because the log never states how many "
                     "steps a tier holds — so the shape is right and the spacing is a floor."),
        }

    def matchups(self, minimum_seen: int = MATCHUP_MIN_CARDS) -> dict:
        """Win rate against the colours the opponent actually showed.

        The colours come from cards that were seen, so a game that ended on turn four
        reports less than it should. Games under the threshold are counted and named
        rather than silently folded into a colourless bucket.
        """
        rows = []
        thin = 0
        for game in self.store.games():
            if game.get("result") not in TERMINAL or game.get("status") != "complete":
                continue
            revealed = [cid for cid in game.get("opponent_revealed") or [] if isinstance(cid, int)]
            cards = self.cards(revealed)
            colours = sorted({colour for cid in revealed
                              for colour in (cards.get(str(cid)) or {}).get("colors") or []})
            spells = sum(1 for cid in revealed if not (cards.get(str(cid)) or {}).get("is_land"))
            if spells < minimum_seen:
                thin += 1
                continue
            rows.append({"key": "".join(colours) or "C", "result": game["result"]})
        buckets: dict[str, dict] = {}
        for row in rows:
            bucket = buckets.setdefault(row["key"], {"colours": row["key"], "wins": 0, "losses": 0, "draws": 0})
            bucket[{"win": "wins", "loss": "losses", "draw": "draws"}[row["result"]]] += 1
        table = []
        for bucket in buckets.values():
            played = bucket["wins"] + bucket["losses"]
            table.append({**bucket, "games": played + bucket["draws"],
                          "interval": analysis.wilson_interval(bucket["wins"], played) if played else None})
        table.sort(key=lambda item: (-item["games"], item["colours"]))
        return {
            "rows": table, "counted": len(rows), "skipped_thin": thin,
            "minimum_seen": minimum_seen,
            "note": ("Colours are read from the cards the opponent showed, so a short game "
                     f"reports fewer than it should. {thin} game(s) showed fewer than "
                     f"{minimum_seen} nonland cards and are left out rather than counted as "
                     "colourless."),
        }

    def economy(self) -> dict:
        """What an entry returns at the rate you actually win limited games.

        The rate has to be your limited rate, not your overall one: drafting and building
        a sixty-card deck are different skills, and using the wrong one here is how a
        player concludes that drafting pays when for them it does not.
        """
        games = [game for game in self.store.games()
                 if game.get("result") in TERMINAL and _is_limited(game)]
        wins = sum(1 for game in games if game["result"] == "win")
        played = sum(1 for game in games if game["result"] in ("win", "loss"))
        measured = analysis.wilson_interval(wins, played) if played else None
        rate = measured["rate"] if measured else None
        events = []
        for name in economy.EVENTS:
            row = {"event": name, "label": economy.EVENTS[name]["label"],
                   "break_even": economy.break_even(name),
                   "reference": [economy.verdict(name, value) for value in REFERENCE_RATES]}
            if rate is not None:
                row["yours"] = economy.verdict(name, rate)
            events.append(row)
        return {
            "events": events, "reference_rates": list(REFERENCE_RATES),
            "limited_games": played, "limited_wins": wins, "measured": measured,
            "enough": played >= LIMITED_MIN_GAMES, "minimum": LIMITED_MIN_GAMES,
            "pack_gems": economy.PACK_GEMS, "source": economy.SOURCE,
            "unverified": economy.UNVERIFIED,
            "note": ("Games are treated as independent at a constant win rate, which is an "
                     "assumption and not a measurement: a run meets stronger opponents as it "
                     "goes. The break-even rate is the honest part — it depends only on the "
                     "published prize structure."
                     + ("" if played >= LIMITED_MIN_GAMES else
                        f" You have {played} completed limited game(s), which is not enough to "
                        "estimate your rate; the reference rates are there to read the table "
                        "with.")),
        }

    def wallet(self) -> dict:
        """Balance over time, measured from the log rather than modelled from payout tables.

        Arena writes no itemised transactions — the Changes array is always empty — but it
        restates the balance many times a session. What that supports is 'you gained this
        much across these games'; what it does not support is attributing a movement to a
        particular reward, and the interface must not pretend otherwise.
        """
        points = self.store.wallet()
        if not points:
            return {"points": [], "note": "No balance reading stored yet."}
        first, last = points[0], points[-1]
        games = [game for game in self.store.games() if game.get("started_at")]
        between = [game for game in games
                   if first["recorded_at"] <= str(game["started_at"]) <= last["recorded_at"]]
        return {
            "points": points, "readings": len(points),
            "from": first["recorded_at"], "to": last["recorded_at"],
            "gems": {"first": first["gems"], "last": last["gems"],
                     "change": (last["gems"] or 0) - (first["gems"] or 0)},
            "gold": {"first": first["gold"], "last": last["gold"],
                     "change": (last["gold"] or 0) - (first["gold"] or 0)},
            "games_between": len(between),
            "note": ("Measured from balance readings in the log. The log records no itemised "
                     "transactions, so a movement cannot be attributed to a specific reward."),
        }

    # ------------------------------------------------------------------ draft

    def draft(self) -> dict:
        """The pack on screen, ranked, while the draft is happening.

        The live reading comes from the follower, because a draft that is only read after
        the client restarts is a draft the log no longer holds. When nothing is being
        followed, the last draft stored is shown instead, which is what a review of the
        picks needs.
        """
        live = self.watcher.draft_state() if self.watcher is not None else None
        state = live or self.store.latest_draft()
        if not state:
            return {"active": False, "live": False, "raw": raw_status(self.data_dir),
                    "hint": ("Turn Follow matches on before you enter the draft: Arena writes "
                             "each pack to the log as it is dealt, and discards the file when the "
                             "client restarts."),
                    "diagnostics": self.watcher.draft_diagnostics() if self.watcher else None}
        pack_ids = [int(cid) for cid in state.get("pack_cards") or []]
        pool_ids = [int(cid) for cid in state.get("pool") or []]
        # The packs already passed are resolved too: a pick shown without the cards it beat
        # is the one part of a draft review that says nothing.
        passed = [int(cid) for entry in state.get("picks") or []
                  for cid in entry.get("pack_cards") or []]
        cards = {int(key): value
                 for key, value in self.cards(pack_ids + pool_ids + passed).items()}
        expansion = self._draft_set(pack_ids, pool_ids, cards)
        event = limited_format(state.get("event_name") or "")
        ratings, table = self._ratings_for(expansion, event, pack_ids)
        advice = None
        if ratings:
            advice = pick.advise(pack_ids, pool_ids, ratings, cards, state.get("pick"))
        return {
            "active": True, "live": bool(live), "draft_id": state.get("draft_id"),
            "event_name": state.get("event_name"), "pack": state.get("pack"),
            "pick": state.get("pick"), "pack_cards": pack_ids, "pool": pool_ids,
            "picks": state.get("picks") or [], "updated_at": state.get("updated_at"),
            "cards": {str(cid): card for cid, card in cards.items()},
            "expansion": expansion, "limited_event": event, "advice": advice,
            "table": table,
            "ratings": None if ratings else {
                "missing": True, "expansion": expansion, "event": event,
                "note": ("No 17Lands table stored for this set yet. Fetching it is one request "
                         "and it is then read from disk."),
            },
            "credit": LIMITED_CREDIT,
        }

    def draft_review(self) -> dict:
        """Every pick replayed against what the app would have taken, and why.

        This is the part of a draft worth rereading. It is also the part where the app has
        to be most careful about tone: it did not see the pack under a clock, it does not
        know the archetype that was open, and on a set this new it is reading card text.
        Disagreement is a question, never a correction.
        """
        state = (self.watcher.draft_state() if self.watcher is not None else None) or self.store.latest_draft()
        if not state:
            raise KeyError("no draft stored")
        picks = [entry for entry in state.get("picks") or [] if entry.get("pack_cards")]
        if not picks:
            return {"picks": [], "reason": (
                "No pick was recorded with the pack it came from, so there is nothing to "
                "replay. The bot draft only supplies that from the first pack onwards.")}
        ids = {int(cid) for entry in picks for cid in entry["pack_cards"]}
        cards = {int(key): value for key, value in self.cards(sorted(ids)).items()}
        expansion = self._draft_set([], sorted(ids), cards)
        event = limited_format(state.get("event_name") or "")
        ratings, table = self._ratings_for(expansion, event, sorted(ids))
        grades = self.community.grades(expansion) if expansion else {}
        # With nothing measuring the set, the app's own order is noise — that was tested —
        # so the review shows what happened and says nothing about whether it was right.
        basis = pick.advise(picks[0]["pack_cards"], [], ratings, cards, 1, grades=grades)["basis"]
        compares = basis != "structure"
        pool: list[int] = []
        rows = []
        agreed = 0
        for entry in picks:
            advice = pick.advise(entry["pack_cards"], list(pool), ratings, cards,
                                 entry.get("pick"), grades=grades)
            taken = int(entry["card_id"])
            suggested = advice.get("pick")
            same = suggested == taken
            agreed += 1 if same else 0
            mine = next((item for item in advice["ranked"] if item["card_id"] == taken), None)
            best = advice["ranked"][0] if advice["ranked"] else None
            rows.append({
                "pack": entry.get("pack"), "pick": entry.get("pick"),
                "taken": taken, "taken_name": (cards.get(taken) or {}).get("name") or f"#{taken}",
                "suggested": suggested, "suggested_name": advice.get("pick_name"),
                "agreed": same,
                "gap": None if not mine or not best else round(best["score"] - mine["score"], 2),
                "rank_of_yours": (None if not mine else
                                  1 + [item["card_id"] for item in advice["ranked"]].index(taken)),
                "why": (advice["ranked"][0]["why"] if best and not same else []),
                "options": len(advice["ranked"]) + len(advice["unrated"]),
                "lane": advice["lane"],
            })
            pool.append(taken)
        disagreements = sorted((row for row in rows if not row["agreed"] and row["gap"] is not None),
                               key=lambda row: -row["gap"]) if compares else []
        return {
            "picks": rows, "agreed": agreed if compares else None, "of": len(rows),
            "compares": compares, "expansion": expansion, "table": table, "basis": basis,
            "biggest": disagreements[:5],
            "cards": {str(cid): card for cid, card in cards.items()},
            "note": ("The app is replaying with the whole pool visible in hindsight and no clock. "
                     "Where it disagrees, the question is which of the two readings was right — "
                     "it did not sit at the table."
                     if compares else
                     "Nothing measures this set, and ordering a pack by card text was tested "
                     "against 17Lands and came out near enough to random. So this is the record "
                     "of what was in each pack and what left it — no second opinion, because the "
                     "app does not have one worth reading here."),
        }

    def draft_deck(self) -> dict:
        """The pool turned into a registrable deck, with the pairs that lost.

        A pool is not a deck, and the step between them is where a new player gives away
        the most games. What is decided here is only the mechanical part: the colour pair
        the pool paid for, the best cards in it, and a mana base for the pips those cards
        demand.
        """
        state = (self.watcher.draft_state() if self.watcher is not None else None) or self.store.latest_draft()
        if not state:
            raise KeyError("no draft stored")
        pool_ids = [int(cid) for cid in state.get("pool") or []]
        cards = {int(key): value for key, value in self.cards(pool_ids).items()}
        expansion = self._draft_set([], pool_ids, cards)
        event = limited_format(state.get("event_name") or "")
        ratings, table = self._ratings_for(expansion, event, pool_ids)
        answer = build.suggest(pool_ids, cards, ratings,
                               grades=self.community.grades(expansion) if expansion else None)
        deck_ids = [item["card_id"] for item in answer.get("spells") or []]
        return {**answer, "expansion": expansion, "table": table,
                "cards": {str(cid): cards[cid] for cid in set(pool_ids + deck_ids) if cid in cards},
                "pool_size": len(pool_ids), "credit": LIMITED_CREDIT}

    def draft_deck_export(self) -> dict:
        """The suggested deck as an Arena list, ready to paste into the client."""
        answer = self.draft_deck()
        if not answer.get("spells"):
            raise KeyError("no deck could be built")
        cards = {int(key): value for key, value in (answer.get("cards") or {}).items()}
        counts: dict[int, int] = {}
        for item in answer["spells"]:
            counts[item["card_id"]] = counts.get(item["card_id"], 0) + 1
        for land in answer["land_base"]["nonbasic"]:
            counts[land["card_id"]] = counts.get(land["card_id"], 0) + int(land["quantity"])
        lines = [{"id": cid, "quantity": quantity} for cid, quantity in counts.items()]
        text = format_arena({"main": lines, "sideboard": []},
                            {str(cid): card for cid, card in cards.items()})
        # Arena reads a basic land by name alone, and the app has no printing for one.
        basics = "\n".join(f"{quantity} {build.BASIC_NAMES[colour]}"
                           for colour, quantity in answer["land_base"]["basics"].items() if quantity)
        return {**text, "text": text["text"] + basics + ("\n" if basics else ""),
                "cards": sum(counts.values()) + sum(answer["land_base"]["basics"].values())}

    def _draft_set(self, pack_ids: list[int], pool_ids: list[int], cards: dict) -> str:
        """The set being drafted, taken from the cards themselves.

        Reading it from the event name would mean parsing a string Arena changes every
        season; the printing on the cards in the pack is the same fact without the parsing.
        """
        counts: dict[str, int] = {}
        for cid in pack_ids or pool_ids:
            code = str((cards.get(cid) or {}).get("set") or "").upper()
            if code:
                counts[code] = counts.get(code, 0) + 1
        return max(counts, key=lambda code: counts[code]) if counts else ""

    def _ratings_for(self, expansion: str, event: str,
                     pack_ids: list[int] | None = None) -> tuple[dict | None, dict]:
        """The table to rank by, and which one it turned out to be.

        A queue that has just opened has no table of its own: 17Lands answers with every
        card in the set and no win rate, because nobody has played it enough yet. So the
        tables are not tried in a fixed order — they are compared on the only thing that
        matters at this moment, how many cards of the pack in front of the player each one
        actually covers. The answer says which one won and how much of the pack it reaches,
        because a substituted table and a thin table are both facts about the advice.
        """
        missing = {"event": None, "requested": event, "substituted": False,
                   "cards": 0, "covered": 0, "of_pack": len(pack_ids or [])}
        if not expansion:
            return None, missing
        wanted = {int(cid) for cid in pack_ids or []}
        best, chosen = None, None
        for candidate in dict.fromkeys([event, "PremierDraft", "TradDraft", "QuickDraft"]):
            table = self.limited.ratings(expansion, candidate)
            if not table:
                continue
            covered = len(wanted & set(table)) if wanted else len(table)
            if best is None or covered > best:
                best, chosen = covered, (candidate, table)
        if chosen is None:
            return None, missing
        candidate, table = chosen
        note = ""
        if candidate != event:
            note = (f"17Lands has no usable {event} table for {expansion}, so the order comes "
                    f"from {candidate}. The cards are the same; the queue is not.")
        if wanted and best is not None and best < len(wanted) / 2:
            note = (note + " " if note else "") + (
                f"{best} of {len(wanted)} cards in this pack carry a published rate. "
                f"{expansion} is new enough that 17Lands has barely any data on it, and a "
                "ranking built on the rest would be this app inventing numbers.")
        return table, {"event": candidate, "requested": event, "substituted": candidate != event,
                       "cards": len(table), "covered": best or 0, "of_pack": len(wanted),
                       "note": note}

    def community_grades(self, expansion: str = "") -> dict:
        """Grades written on this machine, with the pool they were written about."""
        expansion = str(expansion or "").strip().upper()
        if not expansion:
            state = (self.watcher.draft_state() if self.watcher is not None else None) or self.store.latest_draft() or {}
            pool = [int(cid) for cid in state.get("pool") or []]
            expansion = self._draft_set([], pool, {int(k): v for k, v in self.cards(pool).items()})
        grades = self.community.grades(expansion) if expansion else {}
        return {"expansion": expansion, "grades": {str(k): v for k, v in grades.items()},
                "sets": self.community.sets(), "scale": community_scale(),
                "note": ("A grade is one person's opinion with their name on it. It never "
                         "overrides a measured win rate — it fills the fortnight after a set "
                         "releases, when no measurement exists yet.")}

    def save_grade(self, payload: dict) -> dict:
        entry = self.community.save(payload.get("expansion", ""), payload.get("card_id", 0),
                                    payload.get("grade", 0), payload.get("note", ""),
                                    payload.get("by", ""))
        return {"saved": True, "entry": entry}

    def export_grades(self, expansion: str) -> dict:
        return self.community.export(expansion)

    def limited_sets(self) -> dict:
        return {"sets": self.limited.sets(), "formats": list(FORMATS), "credit": LIMITED_CREDIT}

    def fetch_limited(self, expansion: str, event: str = "PremierDraft", force: bool = False) -> dict:
        """Store one set's ratings. An empty answer falls through to the table that exists.

        This is one user action, so it does what the user meant: get me the numbers for
        this set. A queue that has not opened yet returns nothing, and stopping there
        would leave the pack unranked for a reason the player cannot act on."""
        if not str(expansion).strip():
            raise ValueError("no set given")
        expansion = str(expansion).strip()
        result = self.limited.fetch(expansion, event, force)
        if not result["cards"] and event != "PremierDraft":
            fallback = self.limited.fetch(expansion, "PremierDraft", force)
            result["fallback"] = fallback
            result["note"] = (
                f"17Lands publishes no {event} rate for {expansion} yet — that queue has not "
                f"been played enough. Premier draft answered with {fallback['cards']} rated "
                "card(s), and that is what the ranking will use.")
        elif result["cards"] and result.get("offered") and result["cards"] < result["offered"] * 0.6:
            result["note"] = (
                f"{result['cards']} of {result['offered']} cards in {expansion} carry a rate so "
                "far. On a set this new the rest of the pack has no published number, and the "
                "screen will say so rather than rank them.")
        return result

    def draft_pool_export(self) -> dict:
        """The pool as an Arena deck list, so the picks can be opened in the client."""
        state = (self.watcher.draft_state() if self.watcher is not None else None) or self.store.latest_draft()
        if not state:
            raise KeyError("no draft stored")
        pool = [int(cid) for cid in state.get("pool") or []]
        cards = self.cards(pool)
        counts: dict[int, int] = {}
        for cid in pool:
            counts[cid] = counts.get(cid, 0) + 1
        deck = {"main": [{"id": cid, "quantity": quantity} for cid, quantity in counts.items()],
                "sideboard": []}
        return {**format_arena(deck, cards), "cards": len(pool)}

    # ------------------------------------------------------------------ deck lists

    def export_deck(self, deck_id: str) -> dict:
        games = [game for game in self.store.games() if str(game.get("deck_id")) == deck_id]
        if not games:
            raise KeyError("deck not observed")
        deck = games[-1].get("deck", {"main": [], "sideboard": []})
        ids = [item["id"] for section in ("main", "sideboard")
               for item in deck.get(section, []) if isinstance(item, dict)]
        exported = format_arena(deck, self.cards(sorted(set(ids))))
        return {"deck_id": deck_id, "label": self._deck_label(deck_id), **exported}

    def analyze_list(self, text: str) -> dict:
        """Run the same deck analysis over a list pasted from Arena or from a site.

        Nothing is stored: this answers what the list would look like without pretending
        the games were played with it.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty list")
        if len(text) > 20000:
            raise ValueError("list too long")
        parsed = parse_arena(text, self.card_database_path)
        main, side = parsed["deck"]["main"], parsed["deck"]["sideboard"]
        if not main:
            return {**parsed, "analysis": None,
                    "note": "No main-deck card was recognised."}
        cards = self.cards(sorted({item["id"] for item in main + side}))
        entries = [{"card": cards.get(str(item["id"]), {}), "quantity": item["quantity"]}
                   for item in main]
        return {**parsed, "cards": cards,
                "analysis": self._composition_report(entries, cards, side),
                "note": "Standalone list: no game was played with it, so there is no sample."}

    def _composition_report(self, entries: list[dict], cards: dict, sideboard: list[dict]) -> dict:
        from .catalog import wildcard_cost

        spells = [entry for entry in entries
                  if entry["card"].get("resolved") and not entry["card"].get("is_land")]
        spell_count = sum(entry["quantity"] for entry in spells)
        average = (sum(entry["card"]["mana_value"] * entry["quantity"] for entry in spells) / spell_count
                   if spell_count else None)
        sources = analysis.colour_sources(entries)
        full = entries + [{"card": cards.get(str(item["id"]), {}), "quantity": item["quantity"]}
                          for item in sideboard]
        return {
            "curve": analysis.mana_curve(entries),
            "average_mana_value": round(average, 2) if average is not None else None,
            "lands": sources["total_lands"], "sources": sources["by_colour"],
            "lands_recommended": analysis.lands_recommended(average) if average is not None else None,
            "colour_requirements": analysis.colour_requirements(entries),
            "flexible_costs": analysis.flexible_costs(entries),
            "wildcards": wildcard_cost(full, self.store.owned_cards()),
            "unresolved": [entry["card"].get("id") for entry in entries
                           if not entry["card"].get("resolved")],
            "karsten_citation": analysis.KARSTEN_2022_CITATION,
            "bo1_caveat": analysis.BO1_SMOOTHING_CAVEAT,
        }

    # ------------------------------------------------------------------ review by model

    def coach_status(self) -> dict:
        return {"package": coach.sdk_available(), "key": self.keys.has_key(),
                "key_hint": self.keys.fingerprint(), "model": coach.MODEL,
                "modes": sorted(coach.MODES),
                "ready": coach.sdk_available() and self.keys.has_key(),
                "install": "python -m pip install anthropic"}

    def save_api_key(self, value: str) -> dict:
        if not isinstance(value, str):
            raise ValueError("chave invalida")
        value = value.strip()
        if value and not value.startswith("sk-"):
            raise ValueError("a chave da Anthropic comeca com sk-")
        self.keys.save(value)
        return self.coach_status()

    def coach_material(self, kind: str, game_id: str | None = None,
                       index: int | None = None, deck_id: str | None = None) -> dict:
        """Only numbers this app computed, plus the sanitised position. Nothing raw."""
        if kind == "deck":
            report = self.deck_report(str(deck_id))
            return {key: report[key] for key in
                    ("label", "games", "wins", "losses", "interval", "by_start", "curve",
                     "average_mana_value", "lands", "sources", "lands_recommended",
                     "colour_requirements", "flexible_costs", "wildcards", "unresolved",
                     "karsten_citation", "bo1_caveat", "card_stats") if key in report}
        context = self.decision_context(str(game_id), int(index))
        if not context["eligible"]:
            raise ValueError(context["text"])
        material = {"position": context["context"]}
        try:
            material["library"] = self.library_state(str(game_id), int(index))
        except (KeyError, ValueError):
            material["library"] = {"eligible": False}
        material["opponent"] = self.opponent_profile(str(game_id))
        known = self.rulings_for([int(card_id) for card_id in context["context"].get("cards", {})])
        if known:
            # Rulings are about the cards actually on the table, so they cost a few hundred
            # tokens instead of the whole rulebook, and they answer the question the model
            # most often gets wrong: what this particular card does here.
            material["card_rulings"] = known
        return material

    def coach_prompt(self, mode: str, kind: str, **kwargs) -> dict:
        """The whole question as text, so anyone can paste it into a chat they already have.

        This is the path that costs nothing and needs no account. The in-app button is the
        same question sent for you; it is a convenience, never the way in.
        """
        material = self.coach_material(kind, **kwargs)
        body = coach.build_prompt(mode, material)
        return {"mode": mode, "text": coach.SYSTEM + "\n\n---\n\n" + body,
                "characters": len(coach.SYSTEM) + len(body),
                "note": ("Paste this into any assistant. It carries only the sanitised "
                         "position and the numbers this app computed.")}

    def coach_review(self, mode: str, kind: str, **kwargs) -> dict:
        return coach.review(self.keys.load(), mode, self.coach_material(kind, **kwargs))

    def coach_estimate(self, mode: str, kind: str, **kwargs) -> dict:
        return coach.estimate(self.keys.load(), mode, self.coach_material(kind, **kwargs))

    # ------------------------------------------------------------------ context export

    def decision_context(self, game_id: str, index: int) -> dict:
        game = self.store.game_summary(game_id)
        if game is None:
            raise KeyError("game not found")
        if not isinstance(game.get("self_seat"), int) or game["self_seat"] <= 0:
            return {"eligible": False, "context": {}, "text": "No context without a verifiable identification of the player."}
        selected = self.store.frame(game_id, index)
        selected = self._relabel(selected) if selected else None
        if selected is None or selected.get("quality") in {"degraded", "blocked"}:
            return {"eligible": False, "context": {}, "text": "No context for this stretch."}
        prior_reveals = self._prior_match_reveals(game)
        current = self._safe_frame(selected)
        card_ids = self._context_card_ids(game, current, prior_reveals)
        context = {
            "game": {"game_number": game.get("game_number"), "mode": game.get("mode"), "format": game.get("format")},
            "self_seat": game.get("self_seat"), "deck": game.get("deck", {"main": [], "sideboard": []}),
            "origin": {"source_sha256": game.get("source_sha256"), "state_id": current.get("state_id"),
                       "source_line": selected.get("source_line")},
            "frames": [current],
            "cards": {str(card_id): self._context_card(card) for card_id, card in self.cards(sorted(card_ids)).items()},
            "prior_match_reveals": prior_reveals, "review_mode": "manual",
        }
        text = ("Review only the sanitised context below; assume no future result and no "
                "undisclosed information.\n"
                + json.dumps(context, ensure_ascii=False, separators=(",", ":")))
        return {"eligible": True, "context": context, "text": text}

    def _context_card_ids(self, game: dict, frame: dict, prior_reveals: list[dict]) -> set[int]:
        card_ids: set[int] = set()
        deck = game.get("deck", {})
        if isinstance(deck, dict):
            for cards in (deck.get("main", []), deck.get("sideboard", [])):
                for card in cards if isinstance(cards, list) else []:
                    if isinstance(card, dict) and isinstance(card.get("id"), int):
                        card_ids.add(card["id"])
        for zone in frame.get("zones", []):
            for card in zone.get("objects", []) if isinstance(zone, dict) else []:
                if isinstance(card, dict) and isinstance(card.get("card_id"), int):
                    card_ids.add(card["card_id"])
        for action in [frame.get("action"), *frame.get("actions", []), *frame.get("available_actions", [])]:
            if isinstance(action, dict):
                card_ids.update(card_id for card_id in action.get("card_ids", []) if isinstance(card_id, int))
        card_ids.update(item["card_id"] for item in prior_reveals)
        return card_ids

    def _context_card(self, card: dict) -> dict:
        if card.get("resolved"):
            return {key: card.get(key) for key in ("id", "name", "name_en", "text", "mana_cost", "mana_value", "type_line", "colors", "is_land", "power", "toughness", "resolved", "set", "collector_number", "rarity", "rebalanced", "linked_faces")}
        return {"id": card.get("id"), "resolved": False, "gap": "unresolved card id"}

    def _safe_frame(self, frame: dict) -> dict:
        return {key: frame.get(key) for key in ("index", "state_id", "turn", "phase", "step", "active_player", "priority_player", "players", "zones", "action", "actions", "available_actions", "events", "quality", "warnings")}

    def _prior_match_reveals(self, game: dict) -> list[dict]:
        seat = game.get("self_seat")
        if not isinstance(seat, int) or seat <= 0:
            return []
        revealed = self.store.opponent_cards_before(str(game.get("match_id_hashed")),
                                                    int(game.get("game_number") or 1), seat)
        return [{"card_id": card_id, "knowledge": "historical_prior_match"} for card_id in revealed]
