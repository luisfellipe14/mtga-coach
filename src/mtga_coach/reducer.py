"""Conservative state reconstruction with per-frame knowledge boundaries.

Two products come out of the same message: the board as it stood (`project`) and what
happened to reach it (`timeline`). Neither invents identity — an object the player could
not see stays anonymous in both.
"""
from copy import deepcopy

from . import protocol, timeline

ZONE_PREFIX = protocol.ZONE_PREFIX
HIDDEN_ZONES = set(protocol.CONCEALED_ZONES)
SKIPPED_ZONES = set(protocol.TRANSIENT_ZONES)


def number(value):
    if isinstance(value, dict):
        return value.get("value", 0)
    return value


def _empty_state():
    return {"objects": {}, "zones": {}, "players": {}, "turn": {}, "info": {},
            "persistent": {}, "quality": "complete", "warnings": []}


# A diff names its predecessor, which in practice is the previous state or a recent one.
# Keeping every state of a long game costs more memory than the log itself, so the window
# is bounded: a predecessor older than this is reported as unresolved, never guessed.
STATE_WINDOW = 96


class GameReducer:
    def __init__(self, self_seat=0, state_window=STATE_WINDOW):
        self.self_seat = self_seat
        self.states = {}
        self.state_window = state_window
        self.current = None
        # Identity survives the state graph: an object seen once keeps its card even after
        # Arena renumbers it or moves it to a zone that no longer publishes its grpId.
        self.identity = {}
        self.zone_kinds = {}

    def _remember(self, obj):
        instance_id = obj.get("instanceId")
        if instance_id is None:
            return
        is_ability = obj.get("type") == "GameObjectType_Ability"
        card_id = (obj.get("objectSourceGrpId") if is_ability
                   else (obj.get("overlayGrpId") or obj.get("grpId")))
        known = self.identity.setdefault(instance_id, {"card_id": None, "owner": None})
        if card_id:
            known["card_id"] = card_id
        if obj.get("ownerSeatId") is not None:
            known["owner"] = obj.get("ownerSeatId")

    def resolve(self, instance_id):
        return dict(self.identity.get(instance_id) or {"card_id": None, "owner": None})

    def zone_kind_of(self, zone_id):
        return self.zone_kinds.get(zone_id, "")

    def apply(self, message, source_line):
        sid = message["gameStateId"]
        previous = message.get("prevGameStateId")
        full = message.get("type") == "GameStateType_Full"
        if full:
            raw = _empty_state()
        else:
            parent = self.states.get(previous)
            raw = deepcopy(parent or self.current or {**_empty_state(), "quality": "blocked"})
            if parent is None:
                raw["quality"] = "blocked"
                warning = f"Estado anterior {previous} não localizado; trecho sem reconstrução validada."
                if warning not in raw["warnings"]:
                    raw["warnings"].append(warning)
        annotations = message.get("annotations", []) or []
        # Lineage first: an event that names the new id must still find the old identity.
        for old_id, new_id in timeline.object_id_changes(annotations):
            if old_id in self.identity:
                self.identity[new_id] = dict(self.identity[old_id])
            if old_id in raw["objects"] and new_id not in raw["objects"]:
                moved = raw["objects"].pop(old_id)
                moved["instanceId"] = new_id
                raw["objects"][new_id] = moved
            else:
                raw["objects"].pop(old_id, None)
        for obj in message.get("gameObjects", []):
            raw["objects"][obj["instanceId"]] = deepcopy(obj)
            self._remember(obj)
        for iid in message.get("diffDeletedInstanceIds", []):
            raw["objects"].pop(iid, None)
        for zone in message.get("zones", []):
            raw["zones"][zone["zoneId"]] = deepcopy(zone)
            self.zone_kinds[zone["zoneId"]] = protocol.zone_kind(zone)
        for annotation in message.get("persistentAnnotations", []) or []:
            if isinstance(annotation, dict) and annotation.get("id") is not None:
                raw["persistent"][annotation["id"]] = deepcopy(annotation)
        for annotation_id in message.get("diffDeletedPersistentAnnotationIds", []) or []:
            raw["persistent"].pop(annotation_id, None)
        for player in message.get("players", []):
            seat = player.get("systemSeatNumber", player.get("systemSeatId"))
            if seat is not None:
                raw["players"][seat] = deepcopy(player)
        if "turnInfo" in message:
            raw["turn"] = deepcopy(message["turnInfo"])
        raw["info"].update(message.get("gameInfo", {}))
        self.states[sid] = raw
        while len(self.states) > self.state_window:
            self.states.pop(next(iter(self.states)))
        self.current = raw
        frame = self.project(raw, sid, source_line)
        frame["events"] = timeline.build_events(annotations, self.resolve, self.zone_kind_of)
        return frame

    def project(self, raw, sid, source_line):
        warnings = list(raw['warnings'])
        quality = raw['quality']
        if not self.self_seat:
            quality = 'blocked'
            warnings.append('Seu lado da mesa não foi identificado neste trecho do log.')
        zones = []
        for zid, zone in raw["zones"].items():
            kind = protocol.zone_kind(zone)
            if kind in SKIPPED_ZONES:
                continue
            visible = []
            ids = zone.get("objectInstanceIds", [])
            for iid in ids:
                obj = raw["objects"].get(iid)
                if obj is None or kind in HIDDEN_ZONES or obj.get("isFacedown"):
                    continue
                visibility = obj.get("visibility")
                own = obj.get("ownerSeatId") == self.self_seat and self.self_seat != 0
                disclosed = visibility == "Visibility_Public" or (
                    visibility == "Visibility_Private" and
                    (own or self.self_seat in obj.get("viewers", [])))
                if not disclosed:
                    continue
                is_ability = obj.get("type") == "GameObjectType_Ability"
                parent = raw["objects"].get(obj.get("parentId"), {})
                cid = ((obj.get("objectSourceGrpId") or parent.get("grpId")) if is_ability
                       else (obj.get("overlayGrpId") or obj.get("grpId")))
                if not cid:
                    continue
                visible.append({
                    "instance_id": iid, "card_id": cid, "owner": obj.get("ownerSeatId", 0),
                    "controller": obj.get("controllerSeatId", obj.get("ownerSeatId", 0)),
                    "tapped": bool(obj.get("isTapped", False)),
                    "power": number(obj.get("power")), "toughness": number(obj.get("toughness")),
                    "attack_state": obj.get("attackState", ""),
                    "block_state": obj.get("blockState", ""),
                    "summoning_sickness": bool(obj.get("hasSummoningSickness", False)),
                    "damage": number(obj.get("damage")), "object_type": obj.get("type", ""),
                    "ability_id": obj.get("grpId") if is_ability else None,
                    "ability_ids": [a.get("grpId") for a in obj.get("uniqueAbilities", []) if a.get("grpId")],
                })
            zones.append({"type": kind, "owner": zone.get("ownerSeatId", 0), "zone_id": zid,
                          "objects": visible, "hidden_count": max(0, len(ids) - len(visible)),
                          "total_count": len(ids)})
        turn = raw["turn"]
        return {
            "index": 0, "state_id": sid, "turn": turn.get("turnNumber", 0),
            "phase": turn.get("phase", "").removeprefix("Phase_"),
            "step": turn.get("step", "").removeprefix("Step_"),
            "active_player": turn.get("activePlayer", 0), "priority_player": turn.get("priorityPlayer", 0),
            "players": [{"seat": seat, "life": p.get("lifeTotal", 0), "is_self": seat == self.self_seat,
                         "team_id": p.get("teamId"),
                         "hand_size": p.get("handSize"), "starting_life": p.get("startingLifeTotal")}
                        for seat, p in sorted(raw["players"].items())],
            "zones": zones, "action": None, "actions": [], "available_actions": [], "events": [],
            "quality": quality, "warnings": warnings, "source_line": source_line,
        }
