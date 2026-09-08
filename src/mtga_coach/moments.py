"""Moments in a game worth looking at again — facts, never verdicts.

The obvious thing to want here is what chess.com does: a number on every move, a label from
brilliant to blunder, an accuracy score at the end. That works in chess because an engine
evaluates any position to within a hundredth of a pawn, so the label is just the gap
between the move played and the move the engine wanted.

Magic has no such engine, and not for want of trying: the game is Turing-complete, so no
oracle can evaluate a line in general. Anything that put a letter grade on a Magic play
would be inventing the grade. This app does not do that anywhere else and will not start
here.

What it can do is find the places where the log proves something happened that a player
almost never means to do. Not "this was a mistake" — "you ended turn six with four untapped
lands and a four-drop in hand, and here is the position". The reading stays the player's;
what changes is that they no longer have to remember which turn it was.

Every check below is arithmetic on recorded state, and every one carries what it cannot
see. The two big blind spots, stated once: a card held deliberately for the next turn looks
identical to a card forgotten, and the opponent's board is only as visible as what they
revealed.
"""

from collections import Counter

# Instants and flash are meant to be held: flagging them would be teaching the wrong habit.
SORCERY_SPEED = ("Creature", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle")
FLASH = "flash"
# A card that needs a target it does not have was not castable, whatever the mana said. An
# Aura with no creature on the board and a burn spell with nothing to point at both read as
# idle mana otherwise — measured on real games, that was most of what the check found.
NEEDS_CREATURE = ("enchant creature", "target creature", "target attacking",
                  "target blocking", "another target creature")
KINDS = {
    "land_drop": "A land in hand and no land played",
    "unused_mana": "Ended the turn with mana up and a sorcery-speed card in hand",
    "stranded": "Still in hand when the game ended",
}


def _own(frame, seat, kind):
    for zone in frame.get("zones", []):
        if zone.get("type") == kind and zone.get("owner") == seat:
            return zone.get("objects") or []
    return []


def _untapped_lands(frame, seat, cards):
    battlefield = next((zone for zone in frame.get("zones", [])
                        if zone.get("type") == "Battlefield"), None)
    if battlefield is None:
        return 0
    total = 0
    for card in battlefield.get("objects") or []:
        if card.get("controller") != seat or card.get("tapped"):
            continue
        if (cards.get(card.get("card_id")) or {}).get("is_land"):
            total += 1
    return total


def _lands_in_play(frame, seat, cards):
    battlefield = next((zone for zone in frame.get("zones", [])
                        if zone.get("type") == "Battlefield"), None)
    if battlefield is None:
        return 0
    return sum(1 for card in battlefield.get("objects") or []
               if card.get("controller") == seat
               and (cards.get(card.get("card_id")) or {}).get("is_land"))


def _castable(card, mana):
    """Could this have been cast with that much mana, ignoring colour.

    Colour is left out on purpose: a shortfall of colour is the mana base's problem and the
    deck screen already measures it. What this asks is the cruder question — was there
    enough mana at all — because a card that could not be cast for any reason is not a
    moment worth a player's attention.
    """
    value = card.get("mana_value")
    return isinstance(value, int) and value <= mana and value > 0


def _sorcery_speed(card):
    types = card.get("type_codes") or []
    if not any(kind in types for kind in SORCERY_SPEED):
        return False
    return FLASH not in (card.get("text") or "").lower()


def _needs_a_creature(card):
    text = (card.get("text") or "").lower()
    return any(phrase in text for phrase in NEEDS_CREATURE)


def _creatures_on_board(frame, cards):
    battlefield = next((zone for zone in frame.get("zones", [])
                        if zone.get("type") == "Battlefield"), None)
    if battlefield is None:
        return 0
    return sum(1 for card in battlefield.get("objects") or []
               if "Creature" in ((cards.get(card.get("card_id")) or {}).get("type_codes") or []))


def turns(frames, seat):
    """The last frame of each of the player's own turns, which is where a turn is judged."""
    last = {}
    for frame in frames:
        if frame.get("active_player") != seat or not isinstance(frame.get("turn"), int):
            continue
        turn = frame["turn"]
        if turn not in last or frame.get("index", 0) >= last[turn].get("index", 0):
            last[turn] = frame
    return [last[turn] for turn in sorted(last)]


def find(frames, seat, cards, deck_counts=None):
    """Every moment the recorded state supports, in the order they happened."""
    found = []
    ends = turns(frames, seat)
    previous_lands = 0
    for frame in ends:
        turn = frame["turn"]
        hand = [cards.get(card.get("card_id")) or {} for card in _own(frame, seat, "Hand")]
        lands_now = _lands_in_play(frame, seat, cards)

        held_land = next((card for card in hand if card.get("is_land")), None)
        if held_land and lands_now <= previous_lands and turn > 1:
            found.append({
                "kind": "land_drop", "turn": turn, "frame": frame.get("index"),
                "card_ids": [held_land.get("id")],
                "detail": (f"Turn {turn} ended with {held_land.get('name')} in hand and the same "
                           f"{lands_now} lands in play as the turn before."),
                "blind_spot": ("A land is sometimes held on purpose — for a bluff, or because "
                               "the deck wants it in the graveyard. The log cannot tell those "
                               "apart from forgetting."),
            })
        previous_lands = max(previous_lands, lands_now)

        mana = _untapped_lands(frame, seat, cards)
        creatures = _creatures_on_board(frame, cards)
        idle = [card for card in hand
                if _sorcery_speed(card) and _castable(card, mana)
                and not (_needs_a_creature(card) and not creatures)]
        if mana and idle:
            names = ", ".join(sorted({card.get("name", "?") for card in idle})[:3])
            found.append({
                "kind": "unused_mana", "turn": turn, "frame": frame.get("index"),
                "card_ids": [card.get("id") for card in idle if card.get("id")],
                "detail": (f"Turn {turn} ended with {mana} untapped land(s) and {names} in hand, "
                           "which sorcery speed means could not wait."),
                "blind_spot": ("Only untapped lands are counted, so a turn spent on an ability "
                               "or a creature's mana reads as idle here. Instants and flash are "
                               "never flagged, because holding those is the point, and neither is "
                               "a card that needed a target the board did not have."),
            })

    if ends:
        final = frames[-1] if frames else None
        if final is not None:
            stranded = Counter()
            for card in _own(final, seat, "Hand"):
                entry = cards.get(card.get("card_id")) or {}
                if entry.get("name"):
                    stranded[entry["name"]] += 1
            if stranded:
                found.append({
                    "kind": "stranded", "turn": final.get("turn"), "frame": final.get("index"),
                    "card_ids": [],
                    "detail": ("The game ended with "
                               + ", ".join(f"{count}x {name}" for name, count in stranded.most_common(5))
                               + " still in hand."),
                    "blind_spot": ("A hand full of cards at the end is as often a mana problem "
                                   "as a play problem, and this cannot tell which."),
                })
    return found


def summarise(found, turn_count):
    """Counts by kind, with the rate per turn, and nothing resembling a grade."""
    counts = Counter(item["kind"] for item in found)
    return {
        "moments": len(found), "turns": turn_count,
        "by_kind": [{"kind": kind, "label": KINDS[kind], "count": counts[kind]}
                    for kind in KINDS if counts[kind]],
        "note": ("These are recorded facts, not judgements. Magic has no engine that can rank a "
                 "play the way a chess engine ranks a move — the game is Turing-complete, so no "
                 "oracle exists — and this app will not invent one. What it can do is take you "
                 "back to the turn."),
    }
