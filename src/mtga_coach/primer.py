"""What each pair of colours is trying to do in this set, read off the cards themselves.

A new drafter's first question is not which card is better. It is: what is blue-red even
supposed to be doing here? Every set answers that with two-colour cards and a handful of
named mechanics, and both are sitting in the card database the client already installed on
the player's disk.

So this is a set primer with no source but the game's own files: complete on release day,
offline, and identical for everybody. It is also the honest counterpart to the ranking
that was measured not to work — it makes no claim about which card is good. It says what a
colour pair is built around, and lets the player decide whether they want to be there.

The mechanics are found by shape: an ability that opens with a capitalised word followed by
an em dash is how Magic has printed a named mechanic for twenty years.
"""

import re
from collections import Counter

# `Opus — Whenever you cast an instant or sorcery spell…`. The dash is the giveaway, and it
# is what separates a mechanic from a sentence that merely starts with a capital.
MECHANIC = re.compile(r"^([A-Z][A-Za-z']+(?:[ -][A-Za-z']+){0,2})\s+—", re.M)
# Reminder text and evergreen words are not what a set is about.
EVERGREEN = {"Flying", "Trample", "Vigilance", "Haste", "Lifelink", "Deathtouch", "Menace",
             "Reach", "Defender", "First", "Double", "Ward", "Flash", "Hexproof",
             "Indestructible", "Equip", "Enchant", "Scry", "Surveil", "Mill", "Whenever",
             "When", "At", "You", "If", "Target", "Choose", "Sacrifice", "Draw", "Return",
             "This", "The", "Each", "Put", "Create", "Exile", "Destroy", "Counter"}
PAIRS = ("WU", "WB", "WR", "WG", "UB", "UR", "UG", "BR", "BG", "RG")
COLOUR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
# Below this many cards carrying it, a mechanic is a card, not a theme.
MIN_FOR_THEME = 3
# Not every colour pair gets a named keyword: this set printed four mechanics for five
# pairs. What the other pairs still have is rules text, and a pair that talks about the
# graveyard twice as often as the set does is telling you what it is for. The phrases are
# the handful of things a limited deck is ever built around.
PHRASES = {
    "the graveyard": ("graveyard",),
    "+1/+1 counters": ("+1/+1 counter",),
    "instants and sorceries": ("instant or sorcery", "instant and sorcery"),
    "tokens": ("create a token", "creature token"),
    "sacrificing your own creatures": ("sacrifice a creature", "sacrifice another"),
    "discarding and drawing": ("discard a card", "discard two"),
    "gaining life": ("gain 1 life", "gain 2 life", "gain 3 life", "you gained life"),
    "attacking": ("whenever this creature attacks", "attacks,"),
    "lands": ("land card", "a land enters"),
    "artifacts": ("artifact",),
}
# How much more often than the set at large before a phrase is worth a sentence.
PHRASE_LIFT = 1.6


def mechanics(text):
    """Named mechanics in a card's rules text, in the order they appear."""
    found = []
    for match in MECHANIC.finditer(text or ""):
        name = match.group(1).strip()
        if name.split()[0] in EVERGREEN or len(name) < 3:
            continue
        if name not in found:
            found.append(name)
    return found


def _pair_of(card):
    colours = sorted(card.get("colors") or [], key="WUBRG".index)
    return "".join(colours) if len(colours) == 2 else None


def build(cards):
    """A primer per colour pair: the gold cards that define it and what they reward.

    `cards` is the whole set as the local catalogue resolves it.
    """
    resolved = [card for card in cards.values() if card.get("resolved") and not card.get("is_token")]
    set_wide = Counter()
    for card in resolved:
        for name in mechanics(card.get("text")):
            set_wide[name] += 1

    # A mechanic belongs to the colours that carry it, and most of them live on the
    # single-coloured cards rather than the gold ones — counting only gold cards found
    # Opus and Repartee and missed the rest of the set entirely.
    homes = {}
    for name, total in set_wide.items():
        if total < MIN_FOR_THEME:
            continue
        colours = Counter()
        for card in resolved:
            if name in mechanics(card.get("text")):
                for colour in card.get("colors") or []:
                    colours[colour] += 1
        top = [colour for colour, _ in colours.most_common(2)]
        if len(top) == 2:
            homes[name] = ("".join(sorted(top, key="WUBRG".index)), colours)

    pairs = []
    for pair in PAIRS:
        gold = [card for card in resolved if _pair_of(card) == pair]
        if not gold:
            continue
        counts = Counter()
        for card in resolved:
            if _pair_of(card) == pair or set(card.get("colors") or []) <= set(pair):
                for name in mechanics(card.get("text")):
                    counts[name] += 1
        themes = sorted(((name, counts[name],
                          round(counts[name] / max(set_wide[name], 1), 3))
                         for name, (home, _) in homes.items() if home == pair),
                        key=lambda item: (-item[1], item[0]))
        in_pair = [card for card in resolved if set(card.get("colors") or []) <= set(pair)
                   and card.get("colors")]
        leanings = _leanings(in_pair, resolved)
        pairs.append({
            "pair": pair,
            "colours": [COLOUR_NAMES[colour] for colour in pair],
            "cards": sorted(({"card_id": card["id"], "name": card["name"],
                              "rarity": card.get("rarity"), "mana_value": card.get("mana_value"),
                              "mechanics": mechanics(card.get("text"))}
                             for card in gold),
                            key=lambda item: (item["mana_value"] or 0, item["name"])),
            "themes": [{"name": name, "cards": count, "share_of_set": lift}
                       for name, count, lift in themes[:4]],
            "leanings": leanings,
            "reading": _reading(pair, gold, themes, leanings),
        })
    return {
        "pairs": pairs,
        "set_mechanics": [{"name": name, "cards": count}
                          for name, count in set_wide.most_common(12)
                          if count >= MIN_FOR_THEME],
        "cards_read": len(resolved),
        "note": ("Read from the card database the Arena client installed on this machine. No "
                 "network, complete on the day a set releases, and it says nothing about which "
                 "cards are good — only what each pair of colours is built around."),
    }


def _leanings(in_pair, resolved):
    """What this pair's rules text talks about more than the set does."""
    found = []
    for label, needles in PHRASES.items():
        def hits(cards):
            return sum(1 for card in cards
                       if any(needle in (card.get("text") or "").lower() for needle in needles))
        mine, everything = hits(in_pair), hits(resolved)
        if mine < MIN_FOR_THEME or not everything or not in_pair:
            continue
        lift = (mine / len(in_pair)) / (everything / len(resolved))
        if lift >= PHRASE_LIFT:
            found.append({"about": label, "cards": mine, "lift": round(lift, 2)})
    found.sort(key=lambda item: -item["lift"])
    return found[:3]


def _reading(pair, gold, themes, leanings=()):
    names = " and ".join(COLOUR_NAMES[colour] for colour in pair)
    if not themes:
        if leanings:
            about = " and ".join(item["about"] for item in leanings[:2])
            return (f"{names.capitalize()} has no named mechanic in this set, but its cards talk "
                    f"about {about} far more than the rest of the set does.")
        return (f"{names.capitalize()}: {len(gold)} two-colour cards, with no mechanic they "
                "share and no subject they return to. In this set the pair is a pile of good "
                "cards rather than a plan.")
    lead, count, share = themes[0]
    rest = [name for name, _, _ in themes[1:3]]
    tail = f", alongside {' and '.join(rest)}" if rest else ""
    return (f"{names.capitalize()} is built around {lead}{tail}. "
            f"{count} cards in these colours carry it, which is "
            f"{round(share * 100)}% of every card in the set that does.")
