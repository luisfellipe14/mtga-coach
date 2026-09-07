"""What the packs were telling you, counted rather than felt.

Reading signals is the skill that separates a drafter who ends up in the open lane from
one who fights three other people for the same cards. It is also the hardest thing to
learn alone, because it happens across forty-five packs under a clock and leaves no trace
you can go back to — unless something wrote the packs down.

This app wrote them down. So the question becomes arithmetic: of the cards still sitting
in a pack by the fifth pick, how many were red? More than the packs on average carried?
Then red was going round, and the seat to your right was not taking it.

Two things this module refuses to do. It does not tell you which lane to have taken —
that depends on the cards, and card quality is the one thing measured to be beyond it. And
it does not treat its own numbers as certain: eight cards of a colour in a fourteen-card
pack is a fact, and two cards more than average across four packs is noise. The threshold
is stated, not hidden.
"""

from collections import Counter

# A pack's first picks say little: everybody's first pick is the best card, whatever colour
# it is. What a colour looks like from the fifth pick on is what the table is passing.
LATE_FROM = 5
# Below this many late cards seen in a pack, the share is arithmetic on nothing.
MIN_SAMPLE = 12
# How far a colour's share has to sit above its own baseline before it is worth a sentence.
STRONG, MILD = 0.10, 0.05
# A pod is eight seats, so a booster comes back to you exactly eight picks after you passed
# it. Any smaller gap is not the same booster returning — it is two different boosters that
# both held the same common, which is a duplicate and says nothing about the table.
WHEEL_DISTANCE = 8
COLOURS = "WUBRG"
COLOUR_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}
BOT_CAVEAT = ("In a quick draft the packs come back from bots, and bots do not pass like a "
              "table of people. The counts are real; what they say about a human pod is not.")


def _colours_of(card):
    if not card or not card.get("resolved") or card.get("is_land"):
        return []
    return list(card.get("colors") or [])


def _tally(packs, cards):
    """How many cards of each colour appeared, counting a gold card once per colour."""
    counts = Counter()
    total = 0
    for pack in packs:
        for card_id in pack:
            colours = _colours_of(cards.get(int(card_id)))
            if not colours:
                continue
            total += 1
            for colour in colours:
                counts[colour] += 1
    return counts, total


def read(picks, cards, late_from=LATE_FROM):
    """Colour signals per pack, and the cards that came back around.

    `picks` is the draft as recorded: each entry carries the pack it came from and the
    position it was made at, which is what makes late picks separable from early ones.
    """
    by_pack = {}
    for entry in picks:
        pack = entry.get("pack")
        if pack is None or not entry.get("pack_cards"):
            continue
        by_pack.setdefault(int(pack), []).append(entry)

    # The baseline is this draft's own colour mix, so a set that simply prints more red
    # does not read as red being open in every seat.
    everything = [entry["pack_cards"] for group in by_pack.values() for entry in group]
    baseline_counts, baseline_total = _tally(everything, cards)
    baseline = {colour: baseline_counts[colour] / baseline_total if baseline_total else 0
                for colour in COLOURS}

    rows = []
    for pack in sorted(by_pack):
        entries = sorted(by_pack[pack], key=lambda item: item.get("pick") or 0)
        late = [entry["pack_cards"] for entry in entries if (entry.get("pick") or 0) >= late_from]
        counts, total = _tally(late, cards)
        colours = []
        for colour in COLOURS:
            share = counts[colour] / total if total else 0
            colours.append({
                "colour": colour, "name": COLOUR_NAMES[colour], "seen": counts[colour],
                "share": round(share, 3), "baseline": round(baseline[colour], 3),
                "delta": round(share - baseline[colour], 3),
            })
        colours.sort(key=lambda item: -item["delta"])
        rows.append({
            "pack": pack, "late_from": late_from, "cards_seen": total,
            "enough": total >= MIN_SAMPLE, "colours": colours,
            "reading": _reading(colours, total, pack, late_from),
        })
    return {"packs": rows, "baseline": {c: round(baseline[c], 3) for c in COLOURS},
            "wheeled": wheeled(picks, cards),
            "note": ("Counted from the packs that reached you, from pick "
                     f"{late_from} on. The baseline is this draft's own colour mix, so a set "
                     "that prints more of a colour does not read as that colour being open.")}


def _reading(colours, total, pack, late_from):
    """One sentence a person can act on, or an honest refusal to make one."""
    if total < MIN_SAMPLE:
        return (f"Only {total} coloured cards seen from pick {late_from} of pack {pack} on. "
                "That is too few to read a signal from.")
    best = colours[0]
    if best["delta"] < MILD:
        return (f"Nothing stood out in pack {pack}: every colour came round about as often as "
                "it appeared in the draft overall.")
    strength = "clearly" if best["delta"] >= STRONG else "mildly"
    others = [item for item in colours[1:] if item["delta"] >= MILD]
    tail = (" " + " and ".join(item["name"] for item in others) + " also ran above baseline."
            if others else "")
    return (f"{best['name'].capitalize()} was {strength} coming round in pack {pack}: "
            f"{best['seen']} of the {total} cards you saw late, against "
            f"{round(best['baseline'] * 100)}% of the draft overall.{tail}")


def wheeled(picks, cards):
    """Cards that came back: seen in a pack, passed, and seen again in the same pack.

    A wheel is the most direct evidence there is that nobody between you and the pack
    wanted that card. It is also the one signal that needs no baseline at all — the card
    either came back or it did not.
    """
    positions = {}
    for entry in picks:
        pack, pick = entry.get("pack"), entry.get("pick")
        if pack is None or pick is None:
            continue
        for card_id in entry.get("pack_cards") or []:
            positions.setdefault((int(pack), int(card_id)), set()).add(int(pick))
    found = []
    for (pack, card_id), picks_seen in positions.items():
        pair = next(((first, first + WHEEL_DISTANCE) for first in sorted(picks_seen)
                     if first + WHEEL_DISTANCE in picks_seen), None)
        if pair is None:
            continue
        card = cards.get(card_id) or {}
        found.append({"card_id": card_id, "pack": pack, "first_seen": pair[0],
                      "came_back": pair[1], "name": card.get("name") or f"#{card_id}",
                      "colours": _colours_of(card)})
    found.sort(key=lambda item: (item["pack"], item["first_seen"], item["name"]))
    return found
