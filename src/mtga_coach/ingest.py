"""Extract gameplay fields from Arena logs; raw account payloads never leave this module.

`LogIngestor` accepts bytes as they arrive so a session can be captured while Arena is
running — the client truncates Player.log on every restart, so a reader that only runs
after the fact loses the session it was meant to review.
"""
import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from . import protocol
from .draft import DraftTracker
from .reducer import GameReducer
from .scanner import RecordScanner

DOTNET_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)


def parse_log(data, source_sha256=None):
    """Whole-file parse, kept for callers that already hold every byte."""
    scanner = RecordScanner()
    records = scanner.feed(data, final=True)
    return {"records": records, "warnings": list(scanner.warnings),
            "source_sha256": source_sha256 or hashlib.sha256(data).hexdigest()}


def read_timestamp(value):
    """Arena writes .NET ticks, epoch milliseconds or epoch seconds. Unknown forms stay None."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number > 10 ** 17:
        return (DOTNET_EPOCH + timedelta(microseconds=number // 10)).isoformat()
    if number > 10 ** 12:
        return datetime.fromtimestamp(number / 1000, timezone.utc).isoformat()
    if number > 10 ** 9:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    return None


def events(value, line, depth=0):
    """Yield (kind, payload, line) for every protocol object nested in a record."""
    if depth > 30:
        return
    if isinstance(value, dict):
        if "gameRoomConfig" in value:
            config = value["gameRoomConfig"]
            if isinstance(config, dict) and config.get("matchId"):
                yield "room", config, line
            if "finalMatchResult" in value:
                yield "final_result", value["finalMatchResult"], line
        if "authenticateResponse" in value and isinstance(value["authenticateResponse"], dict):
            yield "auth", value["authenticateResponse"], line
        if "DeckId" in value and "Name" in value:
            yield "deck_summary", value, line
        if "InventoryInfo" in value and isinstance(value["InventoryInfo"], dict):
            yield "inventory", value["InventoryInfo"], line
        if "constructedClass" in value or "limitedClass" in value:
            yield "rank", value, line
        kind = value.get("type")
        if isinstance(kind, str) and kind.startswith(("GREMessageType_", "ClientMessageType_")):
            yield "message", value, line
            return  # The inner protocol payload must not be counted twice.
        for child in value.values():
            if isinstance(child, str) and child[:1] in ("{", "["):
                try:
                    yield from events(json.loads(child), line, depth + 1)
                except (ValueError, RecursionError):
                    pass
            elif isinstance(child, (dict, list)):
                yield from events(child, line, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from events(child, line, depth + 1)


def composition(deck):
    def cards(values):
        counts = Counter()
        for value in values or []:
            if isinstance(value, int) and value > 0:
                counts[value] += 1
            elif isinstance(value, dict):
                cid = value.get("cardId", value.get("id"))
                quantity = value.get("quantity", 1)
                if isinstance(cid, int) and isinstance(quantity, int) and cid > 0 and quantity > 0:
                    counts[cid] += quantity
        return [{"id": cid, "quantity": qty} for cid, qty in sorted(counts.items())]
    return {"main": cards(deck.get("deckCards", deck.get("MainDeck", []))),
            "sideboard": cards(deck.get("sideboardCards", deck.get("Sideboard", [])))}


def deck_hash(deck):
    return hashlib.sha256(json.dumps(deck, sort_keys=True).encode()).hexdigest()[:20]


def match_mode(event):
    """Fallback only. `gameInfo.matchWinCondition` decides the mode when it is present."""
    if event.startswith("Traditional") or "BestOf3" in event:
        return "BO3"
    if event in ("Ladder", "Play") or event in (
        "Historic_Ladder", "Historic_Play", "Timeless_Ladder", "Timeless_Play",
        "Explorer_Ladder", "Explorer_Play", "Alchemy_Ladder", "Alchemy_Play",
    ):
        return "BO1"
    return "unknown"


def format_name(event):
    for term in ("Historic", "Timeless", "Explorer", "Alchemy", "Draft", "Sealed", "Brawl"):
        if term in event:
            return term
    return "Standard" if event in ("Ladder", "Play", "Traditional_Ladder", "Traditional_Play") else event or "Unknown format"


ACTION_NAMES = {"Play": "Play land", "Cast": "Cast", "Activate": "Activate ability",
                "Pass": "Pass priority", "Special": "Special action"}
CLIENT_NAMES = {
    "MulliganResp": "Mulligan decision", "DeclareAttackersResp": "Declare attackers",
    "SubmitAttackersReq": "Confirm attack", "DeclareBlockersResp": "Declare blockers",
    "SubmitBlockersReq": "Confirm blocks", "SelectTargetsResp": "Choose target",
    "SubmitTargetsReq": "Confirm targets", "SelectNResp": "Choose cards",
    "SearchResp": "Search library", "OptionalActionResp": "Optional action",
    "OrderResp": "Order effects", "ConcedeReq": "Concede", "CancelActionReq": "Cancel action",
    "EffectCostResp": "Pay cost", "CastingTimeOptionsResp": "Choose mode",
    "ChooseStartingPlayerResp": "Choose who goes first",
}


def action_choice(action):
    kind = action.get("actionType", "").removeprefix("ActionType_")
    cid = action.get("grpId")
    return {"type": kind, "label": ACTION_NAMES.get(kind, kind or "Action"),
            "card_ids": [cid] if isinstance(cid, int) and cid > 0 else [],
            "instance_id": action.get("instanceId"), "ability_id": action.get("abilityGrpId")}


def relabel_action(action):
    """Rebuild an action's label from its recorded type.

    Labels written at import time freeze the wording of that build. Deriving them on read
    keeps one source of truth — and matters beyond cosmetics, because the same frame is
    what the model receives.
    """
    if not isinstance(action, dict):
        return action
    kind = str(action.get("type", ""))
    if kind == "PerformActionResp":
        choices = action.get("choices") or []
        label = " / ".join(ACTION_NAMES.get(choice.get("type"), choice.get("type") or "Action")
                           for choice in choices if isinstance(choice, dict))
        return {**action, "label": label or "Take action",
                "choices": [{**choice, "label": ACTION_NAMES.get(choice.get("type"), choice.get("label"))}
                            for choice in choices if isinstance(choice, dict)]}
    if kind in ACTION_NAMES:
        return {**action, "label": ACTION_NAMES[kind]}
    if kind not in CLIENT_NAMES:
        return action
    label = CLIENT_NAMES[kind]
    if kind == "MulliganResp":
        response = (action.get("selection") or {}).get("mulliganResp", {}).get("decision", "")
        label = {"MulliganOption_Keep": "Kept the hand", "MulliganOption_AcceptHand": "Kept the hand",
                 "MulliganOption_Mulligan": "Took a mulligan"}.get(response, label)
    return {**action, "label": label}


def decision(message, line):
    kind = message["type"].removeprefix("ClientMessageType_")
    if kind == "PerformActionResp":
        choices = [action_choice(a) for a in message.get("performActionResp", {}).get("actions", [])]
        return {"type": kind, "label": " / ".join(a["label"] for a in choices) or "Take action",
                "card_ids": list(dict.fromkeys(cid for a in choices for cid in a["card_ids"])),
                "choices": choices, "source_line": line}
    if kind not in CLIENT_NAMES:
        return None
    label = CLIENT_NAMES[kind]
    if kind == "MulliganResp":
        response = message.get("mulliganResp", {}).get("decision", "")
        label = {"MulliganOption_Keep": "Kept the hand", "MulliganOption_AcceptHand": "Kept the hand",
                 "MulliganOption_Mulligan": "Took a mulligan"}.get(response, label)
    # Only gameplay selections, never the unrestricted original payload.
    keys = ("mulliganResp", "declareAttackersResp", "declareBlockersResp", "selectTargetsResp",
            "selectNResp", "orderResp", "searchResp", "effectCostResp", "castingTimeOptionsResp")
    details = {key: message[key] for key in keys if key in message}
    return {"type": kind, "label": label, "card_ids": [], "selection": details, "source_line": line}


def _hashed(value, length=16):
    return hashlib.sha256(str(value).encode()).hexdigest()[:length]


class LogIngestor:
    """Accumulates protocol facts across one Arena log session."""

    def __init__(self, source_sha256=None):
        self.scanner = RecordScanner()
        self.source_sha256 = source_sha256
        self.record_count = 0
        self.games, self.reducers, self.frame_maps, self.metadata = {}, {}, {}, {}
        self.matches = {}
        self.named_decks = {}
        self.account = {"user_hash": None}
        self.rank = None
        self.inventory = None
        self.wallet = []
        self.draft = DraftTracker()
        self._clock = None
        self.current_match, self.current_key = None, None
        self._self_user_id = None
        self.dirty = set()
        self.released = set()

    # ------------------------------------------------------------------ feeding

    def feed(self, data, final=False):
        for record in self.scanner.feed(data, final):
            self.record_count += 1
            self._consume(record)

    def finish(self):
        self.feed(b"", final=True)

    @property
    def warnings(self):
        return list(self.scanner.warnings)

    def snapshot(self, dirty_only=False):
        keys = self.dirty if dirty_only else self.games.keys()
        games = [self._finalise(self.games[key]) for key in list(keys) if key in self.games]
        return {"source_sha256": self.source_sha256, "record_count": self.record_count,
                "warnings": self.warnings, "games": games,
                "matches": deepcopy(self.matches), "named_decks": deepcopy(self.named_decks),
                "account": dict(self.account), "rank": deepcopy(self.rank),
                "inventory": deepcopy(self.inventory), "wallet": deepcopy(self.wallet),
                "draft": self.draft.state() if self.draft.active else None}

    def clear_dirty(self):
        self.dirty.clear()

    def release(self, keys):
        """Drop the frames of games already persisted; a session must not grow without bound."""
        for key in keys:
            game = self.games.get(key)
            if game is None or game.get("status") != "complete":
                continue
            game["frames"] = []
            self.released.add(key)
            self.reducers.pop(key, None)
            self.frame_maps.pop(key, None)
            self.dirty.discard(key)

    # ------------------------------------------------------------------ records

    def _consume(self, record):
        # The draft is written outside the game protocol and outside a match, so it is
        # offered every record before the game handlers look at any of them.
        self.draft.consume(record)
        # Records arrive in order, so the most recent timestamp seen bounds anything that
        # carries none of its own — an inventory reading, for one.
        stamp = read_timestamp(record["payload"].get("timestamp")) if isinstance(record["payload"], dict) else None
        if stamp:
            self._clock = stamp
        for kind, value, line in events(record["payload"], record["line"]):
            handler = getattr(self, f"_on_{kind}", None)
            if handler is not None:
                handler(value, line, record)

    def _on_auth(self, value, line, record):
        client_id = value.get("clientId")
        if client_id:
            self._self_user_id = client_id
            self.account["user_hash"] = _hashed(client_id)

    def _on_deck_summary(self, value, line, record):
        uid = value.get("DeckId")
        if not isinstance(uid, str) or not uid:
            return
        attributes = {item.get("name"): item.get("value")
                      for item in value.get("Attributes", []) if isinstance(item, dict)}
        # The log also lists every preconstructed deck the client knows, by localisation key
        # and without a version. Those are catalogue entries, not decks the player built.
        if not attributes.get("Version") or str(value.get("Name", "")).startswith("?=?Loc/"):
            return
        entry = self.named_decks.setdefault(uid, {"uid": uid})
        entry.update({"name": value.get("Name") or entry.get("name"),
                      "format": attributes.get("Format") or entry.get("format"),
                      "version": attributes.get("Version") or entry.get("version"),
                      "last_played": (attributes.get("LastPlayed") or "").strip('"') or entry.get("last_played")})

    def _on_inventory(self, value, line, record):
        self.inventory = {key: value.get(key) for key in
                          ("Gems", "Gold", "WildCardCommons", "WildCardUnCommons",
                           "WildCardRares", "WildCardMythics", "TotalVaultProgress")}
        # The log carries no itemised transactions — the Changes array is always empty —
        # but it restates the balance many times a session. Keeping each distinct reading
        # with its timestamp turns that into a measured history instead of a guess.
        point = {**self.inventory, "at": read_timestamp(record["payload"].get("timestamp")) or self._clock}
        if not self.wallet or {k: v for k, v in self.wallet[-1].items() if k != "at"} != self.inventory:
            self.wallet.append(point)

    def _on_rank(self, value, line, record):
        self.rank = {key: value.get(key) for key in
                     ("constructedSeasonOrdinal", "constructedClass", "constructedLevel",
                      "constructedStep", "constructedMatchesWon", "constructedMatchesLost",
                      "limitedClass", "limitedLevel")}

    def _on_room(self, config, line, record):
        raw_match = config["matchId"]
        self.current_match = _hashed(raw_match)
        self.current_key = None  # A new room never inherits the previous game's decision pointer.
        meta = self._meta(self.current_match)
        players = [p for p in config.get("reservedPlayers", []) if isinstance(p, dict)]
        event_ids = [p.get("eventId") for p in players if p.get("eventId")]
        if event_ids:
            meta["event_id"] = event_ids[0]
        entry = self.matches.setdefault(self.current_match, {"id": self.current_match})
        entry["event_id"] = meta.get("event_id", entry.get("event_id", ""))
        entry.setdefault("started_at", read_timestamp(record["payload"].get("timestamp")))
        for player in players:
            if self._self_user_id and player.get("userId") == self._self_user_id:
                meta["seat"] = player.get("systemSeatId", meta.get("seat", 0))
                meta["team"] = player.get("teamId")
                entry["self_seat"] = meta["seat"]
                self._apply_seat(self.current_match, meta["seat"])
            elif self._self_user_id:
                entry["opponent_name"] = player.get("playerName")
                entry["opponent_hash"] = _hashed(player.get("userId"), 12)
                entry["opponent_seat"] = player.get("systemSeatId")

    def _on_final_result(self, value, line, record):
        match_id = value.get("matchId")
        key = _hashed(match_id) if match_id else self.current_match
        if not key:
            return
        entry = self.matches.setdefault(key, {"id": key})
        entry["completed_reason"] = value.get("matchCompletedReason")
        entry["ended_at"] = read_timestamp(record["payload"].get("timestamp"))
        entry["result_list"] = [item for item in value.get("resultList", []) if isinstance(item, dict)]
        self.dirty.update(key_ for key_, game in self.games.items()
                          if game["match_id_hashed"] == key)

    def _on_message(self, value, line, record):
        message_type = value["type"]
        if message_type == "GREMessageType_ConnectResp":
            self._on_connect(value)
            return
        for field in ("submitDeckResp", "submitDeckReq"):
            if field in value and self.current_match:
                supplied = value[field].get("deckMessage", value[field])
                if "deckCards" in supplied:
                    self._meta(self.current_match)["deck"] = composition(supplied)
        if message_type == "GREMessageType_MulliganReq":
            self._count_mulligan(value)
        gsm = value.get("gameStateMessage")
        if isinstance(gsm, dict):
            self._on_game_state(gsm, line, record)
            return
        if not self.current_key or self.current_key not in self.games:
            return
        game = self.games[self.current_key]
        target = self.frame_maps[self.current_key].get(value.get("gameStateId"))
        if target is None:
            # Search/selection messages with a missing state cannot be attached to an invented position.
            return
        if "actionsAvailableReq" in value:
            target["available_actions"] = [action_choice(a) for a in value["actionsAvailableReq"].get("actions", [])]
        if message_type.startswith("ClientMessageType_"):
            chosen = decision(value, line)
            if chosen:
                target["actions"].append(chosen)
                target["action"] = chosen
                game["decision_count"] += 1
                self.dirty.add(self.current_key)

    def _on_connect(self, value):
        if not self.current_match:
            return
        meta = self._meta(self.current_match)
        seats = value.get("systemSeatIds", [])
        if not meta.get("seat") and len(seats) == 1 and type(seats[0]) is int and seats[0] > 0:
            meta["seat"] = seats[0]
            self._apply_seat(self.current_match, seats[0])
        meta["deck"] = composition(value.get("connectResp", {}).get("deckMessage", {}))
        if meta["deck"]["main"]:
            meta.setdefault("registered_deck_id", deck_hash(meta["deck"]))

    def _count_mulligan(self, value):
        if not self.current_key or self.current_key not in self.games:
            return
        game = self.games[self.current_key]
        for seat in value.get("systemSeatIds", []):
            side = "self" if seat == game["self_seat"] else "opponent"
            # The engine asks once per decision, so the count of requests after the first
            # is the number of hands the player sent back.
            game["mulligan_requests"][side] = game["mulligan_requests"].get(side, 0) + 1

    # ------------------------------------------------------------------ game state

    def _meta(self, match):
        return self.metadata.setdefault(match, {"seat": 0, "deck": {"main": [], "sideboard": []}})

    def _apply_seat(self, match, seat):
        for key, game in self.games.items():
            if game["match_id_hashed"] == match:
                game["self_seat"] = seat
                self.reducers[key].self_seat = seat

    def _on_game_state(self, gsm, line, record):
        info = gsm.get("gameInfo", {})
        if info.get("matchID"):
            self.current_match = _hashed(info["matchID"])
            self.current_key = f"{self.current_match}-{info.get('gameNumber', 1)}"
        if not self.current_key or not self.current_match:
            return
        meta = self._meta(self.current_match)
        if self.current_key not in self.games:
            self._start_game(info, meta, record)
        game, reducer = self.games[self.current_key], self.reducers[self.current_key]
        sid = gsm.get("gameStateId")
        if sid is None or sid in self.frame_maps[self.current_key]:
            return
        frame = reducer.apply(gsm, line)
        frame["index"] = len(game["frames"])
        self.frame_maps[self.current_key][sid] = frame
        game["frames"].append(frame)
        game["frame_count"] = game.get("frame_count", 0) + 1
        self.dirty.add(self.current_key)
        game["turns"] = max(game["turns"], frame["turn"])
        if frame["quality"] != "complete":
            game["quality"] = "degraded"
        self._absorb_game_info(game, reducer, info)
        if game["on_play"] is None and frame["turn"] == 1 and frame["active_player"]:
            game["on_play"] = frame["active_player"] == game["self_seat"] if game["self_seat"] else None
        self._record_card_knowledge(game, frame)

    def _record_card_knowledge(self, game, frame):
        """Which of the player's own cards reached the hand, and when.

        The opening hand is read at the first frame of turn one — after the London
        mulligan has already settled the kept seven — and later arrivals come from the
        draw events. Opponent draws stay anonymous because the log never names them."""
        seat = game["self_seat"]
        if not seat:
            return
        if game["opening_hand"] is None and frame["turn"] >= 1:
            hand = next((zone for zone in frame["zones"]
                         if zone["type"] == "Hand" and zone["owner"] == seat), None)
            if hand is not None:
                game["opening_hand"] = [card["card_id"] for card in hand["objects"]]
        for event in frame["events"]:
            if event["kind"] == "draw" and event.get("seat") == seat and event.get("card_id"):
                game["drawn"].append(event["card_id"])

    def _start_game(self, info, meta, record):
        event = meta.get("event_id", "")
        mode = protocol.match_mode_from_win_condition(info.get("matchWinCondition"))
        self.games[self.current_key] = {
            "id": self.current_key, "match_id_hashed": self.current_match,
            "game_number": info.get("gameNumber", 1),
            "mode": mode or match_mode(event), "mode_basis": "protocol" if mode else "event name",
            "format": format_name(event), "event_id": event,
            "super_format": info.get("superFormat", ""), "mulligan_type": info.get("mulliganType", ""),
            "deck_id": deck_hash(meta["deck"]),
            "registered_deck_id": meta.get("registered_deck_id", deck_hash(meta["deck"])),
            "deck": deepcopy(meta["deck"]), "result": "unknown", "match_result": "unknown",
            "result_reason": "", "status": "in_progress", "turns": 0, "decision_count": 0,
            "quality": "complete", "self_seat": meta["seat"], "self_team": meta.get("team"),
            "on_play": None, "mulligan_requests": {}, "frames": [], "frame_count": 0,
            "opening_hand": None, "drawn": [],
            "timestamp": read_timestamp(record["payload"].get("timestamp")),
            "source_sha256": self.source_sha256,
        }
        self.reducers[self.current_key] = GameReducer(meta["seat"])
        self.frame_maps[self.current_key] = {}

    def _absorb_game_info(self, game, reducer, info):
        mode = protocol.match_mode_from_win_condition(info.get("matchWinCondition"))
        if mode:
            game["mode"], game["mode_basis"] = mode, "protocol"
        current = reducer.current["info"]
        if current.get("stage") == "GameStage_GameOver":
            game["status"] = "complete"
        team = reducer.current["players"].get(game["self_seat"], {}).get("teamId")
        if team is None:
            team = game.get("self_team")
        for result in current.get("results", []):
            outcome = self._outcome(result, team)
            key = "match_result" if result.get("scope") == "MatchScope_Match" else "result"
            game[key] = outcome
            if result.get("reason"):
                game["result_reason"] = result["reason"]

    @staticmethod
    def _outcome(result, team):
        if result.get("result") == "ResultType_Draw":
            return "draw"
        if team is not None and result.get("winningTeamId") is not None:
            return "win" if result["winningTeamId"] == team else "loss"
        return "unknown"

    # ------------------------------------------------------------------ closing

    def _finalise(self, game):
        """Apply match-level facts that only arrive after the last game state."""
        game = dict(game)
        match = self.matches.get(game["match_id_hashed"], {})
        game["opponent_hash"] = match.get("opponent_hash")
        game["opponent_name"] = match.get("opponent_name")
        game["completed_reason"] = match.get("completed_reason")
        game["started_at"] = game.get("timestamp") or match.get("started_at")
        team = game.get("self_team")
        if team is None:
            frames = game.get("frames") or []
            for frame in frames:
                for player in frame.get("players", []):
                    if player.get("is_self") and player.get("team_id") is not None:
                        team = player["team_id"]
                        break
        results = match.get("result_list") or []
        if results and team is not None:
            game_results = [item for item in results if item.get("scope") == "MatchScope_Game"]
            index = int(game.get("game_number", 1)) - 1
            if 0 <= index < len(game_results):
                # The room's final report is authoritative: a concede leaves no GameOver state.
                game["result"] = self._outcome(game_results[index], team)
                game["result_reason"] = game_results[index].get("reason", game.get("result_reason", ""))
                game["status"] = "complete"
            for item in results:
                if item.get("scope") == "MatchScope_Match":
                    game["match_result"] = self._outcome(item, team)
        mulligans = game.pop("mulligan_requests", {})
        game["mulligans_self"] = max(0, mulligans.get("self", 1) - 1)
        game["mulligans_opponent"] = max(0, mulligans.get("opponent", 1) - 1)
        return game


def ingest_log(data):
    ingestor = LogIngestor(hashlib.sha256(data).hexdigest())
    ingestor.feed(data)
    ingestor.finish()
    return ingestor.snapshot()
