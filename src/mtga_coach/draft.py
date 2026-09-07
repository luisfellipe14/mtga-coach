"""Follow a draft as it is being drafted.

Arena writes the draft to the same Player.log as the games, but not through the game
protocol: a draft record is a bare payload on a `<== Draft.Notify` or `<== BotDraft_...`
line, and the scanner keeps only the JSON, so the method name is gone by the time it
arrives here. Recognition is therefore by shape — a dict that carries a pack of cards is
a pack event whatever the client decided to call it that patch.

Two dialects exist, and both are read: the human draft states the pack it is showing and
logs each pick separately, while the bot draft restates the pool on every pack. What they
have in common is a list of card ids, and that is what the recogniser keys on.

Everything a shape-based reader cannot place is recorded as a key signature (names only,
no values) so an unfamiliar dialect can be read off the diagnostics instead of guessed at.
"""

import json
from datetime import datetime, timezone

# The keys each dialect uses. A pack event is any dict carrying one of the pack keys with
# at least two card ids in it — one card is a pick, not a pack.
PACK_KEYS = ("PackCards", "CardsInPack", "DraftPack", "PackContents", "packCards", "cardsInPack")
POOL_KEYS = ("PickedCards", "PickedCardIds", "pickedCards", "PoolCards", "poolCards")
PACK_NUMBER_KEYS = ("SelfPack", "PackNumber", "packNumber", "Pack", "pack")
PICK_NUMBER_KEYS = ("SelfPick", "PickNumber", "pickNumber", "Pick", "pick")
DRAFT_ID_KEYS = ("draftId", "DraftId", "DraftID", "draftID")
EVENT_KEYS = ("EventName", "eventName", "InternalEventName", "internalEventName", "EventId", "eventId")
PICKED_CARD_KEYS = ("CardId", "cardId", "GrpId", "grpId", "PickGrpId", "pickGrpId")
MIN_PACK_SIZE = 2
MAX_SHAPES = 25
# Three packs of fifteen is the shape of every draft Arena runs today; the ceilings only
# stop a malformed record from claiming an absurd position.
MAX_PACK = 6
MAX_PICK = 30


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def card_ids(value):
    """Card ids out of any of the forms Arena writes: list, comma string, or objects."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        parts = value
    else:
        return []
    ids = []
    for part in parts:
        if isinstance(part, dict):
            part = next((part[key] for key in PICKED_CARD_KEYS if key in part), None)
        try:
            number = int(part)
        except (TypeError, ValueError):
            continue
        if number > 0:
            ids.append(number)
    return ids


def _first(value, keys):
    for key in keys:
        if key in value and value[key] not in (None, ""):
            return key, value[key]
    return None, None


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def signature(value, depth=0):
    """The key names of a record, nested, with no values. Safe to keep and to show."""
    parts = []
    for key in sorted(str(name) for name in value):
        child = value.get(key)
        if isinstance(child, dict) and depth < 3:
            parts.append(f"{key}[{signature(child, depth + 1)}]")
        else:
            parts.append(key)
    return ",".join(parts)


def dicts(value, depth=0):
    """Every dict in a record, following the JSON that Arena nests inside strings."""
    if depth > 12:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, str) and child[:1] in ("{", "["):
                try:
                    yield from dicts(json.loads(child), depth + 1)
                except (ValueError, RecursionError):
                    pass
            elif isinstance(child, (dict, list)):
                yield from dicts(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from dicts(child, depth + 1)


class DraftTracker:
    """The state of the draft currently being drafted, rebuilt from the log as it grows."""

    def __init__(self):
        self.draft_id = ""
        self.event_name = ""
        self.pack_number = None
        self.pick_number = None
        self.pack_cards = []
        self.pool = []
        self.picks = []
        self.updated_at = None
        self.matched_keys = set()
        self.shapes = []
        self.records = 0
        # Human drafts count packs and picks from one, bot drafts from zero. Rather than
        # guess per key, the base is read off the stream: a zero can only come from a
        # zero-based dialect, and the first pick of a draft is where it shows up.
        self.zero_based = False

    # ------------------------------------------------------------------ state

    @property
    def active(self):
        return bool(self.pack_cards or self.pool)

    def position(self):
        """Pack and pick as a person counts them, whatever base the client wrote."""
        shift = 1 if self.zero_based else 0
        pack = None if self.pack_number is None else self.pack_number + shift
        pick = None if self.pick_number is None else self.pick_number + shift
        return pack, pick

    def state(self):
        pack, pick = self.position()
        return {"draft_id": self.draft_id, "event_name": self.event_name,
                "pack": pack, "pick": pick, "pack_cards": list(self.pack_cards),
                "pool": list(self.pool), "picks": [dict(item) for item in self.picks],
                "updated_at": self.updated_at, "records": self.records,
                "matched_keys": sorted(self.matched_keys),
                "unrecognised_shapes": list(self.shapes), "zero_based": self.zero_based}

    # ------------------------------------------------------------------ reading

    def consume(self, record):
        payload = record.get("payload")
        if not isinstance(payload, (dict, list)):
            return False
        touched = False
        for value in dicts(payload):
            if self._pack_event(value) or self._pick_event(value):
                touched = True
        if not touched:
            self._note_shape(payload)
        return touched

    def _identify(self, value):
        key, draft_id = _first(value, DRAFT_ID_KEYS)
        if isinstance(draft_id, str):
            if draft_id != self.draft_id:
                # A second draft in one session starts from nothing: keeping the previous
                # pool would advise a pick out of cards that are in another deck.
                if self.draft_id:
                    self._reset()
                self.draft_id = draft_id
            self.matched_keys.add(key)
        key, event = _first(value, EVENT_KEYS)
        if isinstance(event, str) and not event.isdigit():
            self.event_name = event
            self.matched_keys.add(key)

    def _reset(self):
        self.pack_cards, self.pool, self.picks = [], [], []
        self.pack_number = self.pick_number = None

    def _position(self, value):
        key, raw = _first(value, PACK_NUMBER_KEYS)
        pack = _number(raw)
        if pack is not None and 0 <= pack <= MAX_PACK:
            self.pack_number = pack
            self.matched_keys.add(key)
            self.zero_based = self.zero_based or pack == 0
        key, raw = _first(value, PICK_NUMBER_KEYS)
        pick = _number(raw)
        if pick is not None and 0 <= pick <= MAX_PICK:
            self.pick_number = pick
            self.matched_keys.add(key)
            self.zero_based = self.zero_based or pick == 0

    def _pack_event(self, value):
        key, raw = _first(value, PACK_KEYS)
        cards = card_ids(raw)
        if len(cards) < MIN_PACK_SIZE:
            return False
        self.matched_keys.add(key)
        self._identify(value)
        self._position(value)
        self.pack_cards = cards
        pool_key, pool = _first(value, POOL_KEYS)
        picked = card_ids(pool)
        if picked:
            # The bot draft restates the whole pool, which is more reliable than a pick
            # log this reader may have started too late to have seen.
            self.matched_keys.add(pool_key)
            self.pool = picked
        self.records += 1
        self.updated_at = _now()
        return True

    def _pick_event(self, value):
        """A pick the player made: one card, in a record that also names the draft."""
        if not any(key in value for key in DRAFT_ID_KEYS):
            return False
        # A pack event carries a card key too in some dialects; the pack branch runs first
        # and this guard stops the same record from counting twice.
        if any(key in value for key in PACK_KEYS):
            return False
        key, raw = _first(value, PICKED_CARD_KEYS)
        card = _number(raw)
        if card is None or card <= 0:
            return False
        self.matched_keys.add(key)
        self._identify(value)
        self._position(value)
        pack, pick = self.position()
        if any(item["card_id"] == card and item["pack"] == pack and item["pick"] == pick
               for item in self.picks):
            return False
        self.picks.append({"card_id": card, "pack": pack, "pick": pick,
                           "pack_cards": list(self.pack_cards), "at": _now()})
        if self.pool.count(card) < sum(1 for item in self.picks if item["card_id"] == card):
            self.pool.append(card)
        self.records += 1
        self.updated_at = _now()
        return True

    def _note_shape(self, payload):
        """Keep the key names of anything draft-shaped this reader could not place."""
        if len(self.shapes) >= MAX_SHAPES:
            return
        for value in dicts(payload):
            if not isinstance(value, dict):
                continue
            if not any("draft" in str(key).lower() for key in value):
                continue
            text = signature(value)[:400]
            if text not in self.shapes:
                self.shapes.append(text)
            return
