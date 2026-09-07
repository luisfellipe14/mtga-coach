"""Turn GRE annotations into a readable event stream.

The reducer answers "what was the board at this instant"; the timeline answers
"what happened". Both come from the same message: `annotations` carry the transfers,
damage, life changes and reveals that a state snapshot alone cannot express.

Identity is never invented. When the engine moves a hidden object, the event records
the movement with `card_id: None`, which is what the player actually knew.
"""

from . import protocol

# Transfer categories that end a permanent's stay on the battlefield.
LEAVES_PLAY = {"Sacrifice", "Destroy", "SBA_Damage", "SBA_ZeroToughness",
               "SBA_LegendRule", "SBA_Deathtouch", "SBA_ZeroLoyalty"}

KIND_LABELS = {
    "draw": "Drew", "play_land": "Land played", "cast": "Cast",
    "resolve": "Resolved", "leaves_play": "Left the battlefield", "discard": "Discarded",
    "exile": "Exiled", "countered": "Countered", "return": "Returned",
    "mill": "Milled", "move": "Moved", "life": "Life changed",
    "damage": "Damage", "reveal": "Revealed", "token": "Token created",
    "turn": "New turn", "shuffle": "Shuffled", "counter": "Counter",
    "action": "Player action", "mulligan": "Mulligan", "target": "Target chosen",
}

_CATEGORY_KINDS = {
    "Draw": "draw", "PlayLand": "play_land", "CastSpell": "cast",
    "Resolve": "resolve", "Discard": "discard", "Exile": "exile",
    "Countered": "countered", "Countered_Fizzle": "countered", "Return": "return",
    "Mill": "mill", "Conjure": "token", "CardRevealed": "reveal",
}


def _kind_for_transfer(category, source_zone, target_zone):
    if category in LEAVES_PLAY:
        return "leaves_play"
    kind = _CATEGORY_KINDS.get(category)
    if kind:
        return kind
    if source_zone == "Battlefield" and target_zone != "Battlefield":
        return "leaves_play"
    if target_zone == "Hand" and source_zone == "Library":
        return "draw"
    return "move"


def _transfer_event(annotation, resolve, zone_kind_of):
    source_zone = zone_kind_of(protocol.detail(annotation, "zone_src"))
    target_zone = zone_kind_of(protocol.detail(annotation, "zone_dest"))
    category = protocol.detail(annotation, "category") or ""
    instance_id = next(iter(annotation.get("affectedIds", [])), None)
    identity = resolve(instance_id)
    return {
        "kind": _kind_for_transfer(category, source_zone, target_zone),
        "seat": identity.get("owner"), "card_id": identity.get("card_id"),
        "instance_id": instance_id, "from_zone": source_zone, "to_zone": target_zone,
        "category": category,
        "category_label": protocol.TRANSFER_NAMES.get(category, category or "Moved"),
    }


def _single_annotation_events(annotation, resolve, zone_kind_of):
    """Zero or more events for one annotation. Unknown types produce nothing."""
    events = []
    affected = annotation.get("affectedIds", []) or []
    for kind in protocol.annotation_types(annotation):
        if kind == "ZoneTransfer":
            events.append(_transfer_event(annotation, resolve, zone_kind_of))
        elif kind == "ModifiedLife":
            amount = protocol.detail(annotation, "life")
            events.append({"kind": "life", "seat": next(iter(affected), None),
                           "amount": amount, "card_id": None, "instance_id": None})
        elif kind == "DamageDealt":
            amount = protocol.detail(annotation, "damage")
            if not amount:
                continue  # The engine emits zero-damage bookkeeping for every blocker pair.
            source = resolve(annotation.get("affectorId"))
            events.append({"kind": "damage", "seat": source.get("owner"),
                           "amount": amount, "card_id": source.get("card_id"),
                           "instance_id": annotation.get("affectorId"),
                           "targets": list(affected)})
        elif kind == "RevealedCardCreated":
            identity = resolve(next(iter(affected), None))
            events.append({"kind": "reveal", "seat": identity.get("owner"),
                           "card_id": identity.get("card_id"),
                           "instance_id": next(iter(affected), None)})
        elif kind == "TokenCreated":
            identity = resolve(next(iter(affected), None))
            events.append({"kind": "token", "seat": identity.get("owner"),
                           "card_id": identity.get("card_id"),
                           "instance_id": next(iter(affected), None)})
        elif kind == "NewTurnStarted":
            events.append({"kind": "turn", "seat": next(iter(affected), None),
                           "card_id": None, "instance_id": None})
        elif kind == "Shuffle":
            events.append({"kind": "shuffle", "seat": next(iter(affected), None),
                           "card_id": None, "instance_id": None,
                           "count": len(protocol.detail_list(annotation, "NewIds"))})
        elif kind == "CounterAdded":
            identity = resolve(next(iter(affected), None))
            events.append({"kind": "counter", "seat": identity.get("owner"),
                           "card_id": identity.get("card_id"),
                           "instance_id": next(iter(affected), None),
                           "amount": protocol.detail(annotation, "count")})
        elif kind == "UserActionTaken":
            index = protocol.detail(annotation, "actionType")
            identity = resolve(next(iter(affected), None))
            events.append({"kind": "action", "seat": annotation.get("affectorId"),
                           "card_id": identity.get("card_id"),
                           "instance_id": next(iter(affected), None),
                           "action_type": protocol.USER_ACTION_TYPES.get(index, index)})
    return events


def build_events(annotations, resolve, zone_kind_of):
    """Events for one game-state message.

    `resolve(instance_id)` returns {'card_id', 'owner'} for a known object, or empty
    values when the object was never disclosed. `zone_kind_of(zone_id)` returns the
    zone type without its prefix, or '' when the zone was never declared.
    """
    events = []
    for annotation in annotations or []:
        if not isinstance(annotation, dict):
            continue
        for event in _single_annotation_events(annotation, resolve, zone_kind_of):
            event.setdefault("card_id", None)
            event.setdefault("instance_id", None)
            event["annotation_id"] = annotation.get("id")
            event["label"] = KIND_LABELS.get(event["kind"], event["kind"])
            events.append(event)
    return events


def object_id_changes(annotations):
    """(old_id, new_id) pairs, so an object keeps its identity when Arena renumbers it."""
    pairs = []
    for annotation in annotations or []:
        if not isinstance(annotation, dict):
            continue
        if "ObjectIdChanged" not in protocol.annotation_types(annotation):
            continue
        old = protocol.detail(annotation, "orig_id")
        new = protocol.detail(annotation, "new_id")
        if isinstance(old, int) and isinstance(new, int):
            pairs.append((old, new))
    return pairs


def describe(event, card_name, self_seat=None):
    """One line for the interface. `card_name(card_id)` supplies the local catalogue name."""
    seat = event.get("seat")
    if self_seat and seat in (1, 2):
        who = "You" if seat == self_seat else "Opponent"
    else:
        who = {1: "Player 1", 2: "Player 2"}.get(seat, "")
    # The label is presentation, so it is read from the current table rather than from
    # whatever was stored when the game was first imported.
    label = KIND_LABELS.get(event.get("kind"), event.get("label") or event.get("kind"))
    name = card_name(event["card_id"]) if event.get("card_id") else "an unknown card"
    if event["kind"] == "life":
        amount = event.get("amount")
        return f"{who}: life {amount:+d}" if isinstance(amount, int) else f"{who}: life changed"
    if event["kind"] == "damage":
        return f"{name} dealt {event.get('amount')} damage"
    if event["kind"] == "turn":
        if self_seat and seat in (1, 2):
            return "Your turn" if seat == self_seat else "Opponent's turn"
        return f"{who}'s turn" if who else "New turn"
    if event["kind"] == "shuffle":
        return f"{who} shuffled {event.get('count', 0)} cards"
    if event["kind"] == "draw" and not event.get("card_id"):
        return f"{who} drew an undisclosed card" if who else "A card was drawn, undisclosed"
    return f"{label}: {name}"
