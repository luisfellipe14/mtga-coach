"""Turn a drafted pool into a deck that can be registered.

A pool is not a deck. The step between them is where a new player loses the most games:
five colours of good cards, no mana base, fourteen creatures that do not attack together.
This module does the boring half of that — pick the colour pair the pool actually paid
for, take the best twenty-three cards in it, and split the lands by the pips those cards
demand — and leaves the interesting half alone.

Two sources of card quality, in this order:

* the 17Lands win rate, when the set has one. It is measured, and it beats any opinion.
* the card's own text, when the set is too new for anyone to have data. That fallback is
  a heuristic and is labelled as one everywhere it surfaces: it can tell removal from a
  lifegain spell, and it cannot tell a bomb from a trap.

The deck sizes are the game's, not this app's: 40 for limited, 60 for constructed, 100
for commander, each a minimum rather than a target.
"""

import re

from . import analysis

# Minimum deck sizes, from the game's rules. A limited deck is 40 and nobody plays 41.
DECK_MINIMUMS = {"limited": 40, "constructed": 60, "commander": 100}
# The land count every limited primer converges on, and the ratio behind it.
LIMITED_LANDS = 17
SPELLS = DECK_MINIMUMS["limited"] - LIMITED_LANDS
# A pool rarely offers more than a handful of expensive cards worth playing.
MAX_EXPENSIVE = 5
EXPENSIVE_FROM = 5
# Fewer playables than this and the pair is not a lane, it is a pile.
MIN_PLAYABLES = 14
COLOURS = "WUBRG"

COMMUNITY_NOTE = (
    "No published win rate covers this set yet, so the order below uses the grades written "
    "on this machine. Those are opinions with a name on them, and they are replaced by the "
    "measurement the moment 17Lands has one.")

STRUCTURAL_NOTE = (
    "No published win rate covers this set yet, so the order below is read off the cards "
    "themselves: removal, bodies, card draw, curve. It can tell a removal spell from a "
    "lifegain spell. It cannot tell a bomb from a trap.")

# Text patterns, most decisive first. The score is a rank, not a measurement.
PATTERNS = (
    (re.compile(r"destroy target creature|exile target creature", re.I), 8.0, "unconditional removal"),
    (re.compile(r"deals? (\d+) damage to (?:target |any target|up to one target)", re.I), None, "damage removal"),
    (re.compile(r"counter target (?:creature )?spell", re.I), 5.5, "counterspell"),
    (re.compile(r"return target (?:nonland permanent|creature)[^.]*owner'?s hand", re.I), 5.0, "bounce"),
    (re.compile(r"look at the top|draw (?:two|three|a card)", re.I), 5.0, "card advantage"),
    (re.compile(r"tap target creature|stun counter|doesn'?t untap", re.I), 4.5, "tempo"),
    (re.compile(r"destroy target artifact|destroy target enchantment", re.I), 2.0, "narrow answer"),
    (re.compile(r"you gain \d+ life", re.I), 2.0, "lifegain"),
)
EVASION = re.compile(r"\bflying\b|\btrample\b|\bmenace\b|\bdouble strike\b|\bfirst strike\b|\bward\b|\bdeathtouch\b|\blifelink\b|\bvigilance\b", re.I)


def _damage_score(text):
    """A burn spell is worth what it kills."""
    found = [int(value) for value in re.findall(
        r"deals? (\d+) damage to (?:target|any target|up to one target)", text or "", re.I)]
    if not found:
        return None
    best = max(found)
    return 7.5 if best >= 4 else 6.5 if best == 3 else 4.5


def structural_score(card):
    """How good the card looks from its own text. A rank among playables, nothing more."""
    if not card.get("resolved") or card.get("is_land"):
        return 0.0, []
    text = card.get("text") or ""
    reasons = []
    score = 0.0
    damage = _damage_score(text)
    if damage is not None:
        score = max(score, damage)
        reasons.append("removal by damage")
    for pattern, value, label in PATTERNS:
        if value is None or not pattern.search(text):
            continue
        if value > score:
            score = value
            reasons = [label]
        elif value == score:
            reasons.append(label)
    creature = "Creature" in (card.get("type_codes") or [])
    if creature:
        score = max(score, 3.5 + _body(card))
        reasons.append("creature")
        if EVASION.search(text):
            # Evasion is worth what it delivers: a 4/4 flier ends games, a 1/4 flier
            # blocks them. Scaling by power is the difference between those two.
            score += 1.5 if _power(card) >= 3 else 0.5
            reasons.append("evasion")
    mana = card.get("mana_value")
    # The deck already caps how many expensive cards it takes, so a second penalty here
    # would push every top-of-curve creature out of a deck that needs one.
    if isinstance(mana, int) and mana >= EXPENSIVE_FROM and not creature:
        score -= 0.5
        reasons.append("expensive")
    return round(score, 2), reasons


def _power(card):
    try:
        return int(card.get("power") or 0)
    except (TypeError, ValueError):
        return 0


def _body(card):
    """Stats against cost, capped: a 6/6 for six is not twice a 3/3 for three."""
    try:
        power, toughness = int(card.get("power") or 0), int(card.get("toughness") or 0)
    except (TypeError, ValueError):
        return 0.0
    mana = max(1, int(card.get("mana_value") or 1))
    return max(-1.0, min(2.0, (power + toughness - 2 * mana) / 2))


def _rated_score(card, row):
    """The measured signal, put on the same scale as the structural one."""
    if not row or row.get("gih_wr") is None:
        return None
    # 50% is an average card; every point of win rate above it is worth a lot.
    return round(3.0 + (row["gih_wr"] - 0.50) * 60, 2)


# Below this share of the pool covered by published rates, the measured scale is not
# used at all. Mixing the two would put a card ranked on other players' games next to a
# card ranked on its own text as though the two numbers meant the same thing.
RATINGS_COVERAGE = 0.6


def _grade_score(row):
    """A community grade on the same 0-10 axis. The scale is 0-5, so it doubles."""
    if not row or row.get("grade") is None:
        return None
    return round(float(row["grade"]) * 2, 2)


def playables(pool_ids, cards, ratings=None, grades=None):
    """Every distinct card in the pool with a score, on one scale, and why it got it.

    The scale is chosen once for the whole pool. A ranking where two cards were measured
    and twenty were guessed at is a ranking that reads as measured and is not.
    """
    spells = [int(cid) for cid in dict.fromkeys(int(value) for value in pool_ids)
              if not (cards.get(int(cid)) or {}).get("is_land")]
    covered = sum(1 for cid in spells
                  if _rated_score(cards.get(cid) or {}, (ratings or {}).get(cid)) is not None)
    measured = bool(spells) and covered / len(spells) >= RATINGS_COVERAGE
    graded = 0 if measured else sum(1 for cid in spells if _grade_score((grades or {}).get(cid)))
    opinion = not measured and bool(spells) and graded / len(spells) >= RATINGS_COVERAGE
    seen = {}
    for cid in pool_ids:
        cid = int(cid)
        card = cards.get(cid) or {}
        entry = seen.get(cid)
        if entry is not None:
            entry["quantity"] += 1
            continue
        rated = _rated_score(card, (ratings or {}).get(cid)) if measured else None
        if rated is None and opinion:
            rated = _grade_score((grades or {}).get(cid))
        structural, reasons = structural_score(card)
        seen[cid] = {
            "card_id": cid, "name": card.get("name") or f"#{cid}", "quantity": 1,
            "card": card, "is_land": bool(card.get("is_land")),
            "colours": [] if card.get("is_land") else list(card.get("colors") or []),
            "mana_value": card.get("mana_value"),
            "score": rated if rated is not None else structural,
            "basis": ("17lands" if measured and rated is not None else
                      "community" if rated is not None else "structure"),
            "why": reasons,
        }
    entries = list(seen.values())
    return entries, {"measured": measured, "covered": covered, "of_pool": len(spells),
                     "opinion": opinion, "graded": graded}


def _fits(entry, pair):
    return not entry["colours"] or set(entry["colours"]) <= set(pair)


def _pick_spells(entries, pair, wanted=SPELLS):
    """The best `wanted` cards in a colour pair, with a ceiling on expensive ones."""
    eligible = [entry for entry in entries if not entry["is_land"] and _fits(entry, pair)]
    eligible.sort(key=lambda entry: (-entry["score"], entry["name"]))
    chosen, expensive = [], 0
    overflow = []
    for entry in eligible:
        for _ in range(entry["quantity"]):
            if len(chosen) >= wanted:
                break
            costly = isinstance(entry["mana_value"], int) and entry["mana_value"] >= EXPENSIVE_FROM
            if costly and expensive >= MAX_EXPENSIVE:
                overflow.append(entry)
                continue
            chosen.append(entry)
            expensive += 1 if costly else 0
    return chosen, overflow


def _land_split(chosen, pair, pool_lands, total=LIMITED_LANDS):
    """Basics in proportion to the pips the chosen spells actually demand."""
    demand = {colour: 0 for colour in pair}
    for entry in chosen:
        for colour, pips in analysis.hard_pips(entry["card"]).items():
            if colour in demand:
                demand[colour] += pips
    duals = [land for land in pool_lands
             if set(land["card"].get("color_identity") or []) & set(pair)]
    nonbasic = sum(land["quantity"] for land in duals)
    basics = max(0, total - nonbasic)
    weight = sum(demand.values()) or 1
    split = {colour: round(basics * count / weight) for colour, count in demand.items()}
    # Rounding has to land exactly on the land count; the heavier colour absorbs it.
    drift = basics - sum(split.values())
    if split:
        heaviest = max(split, key=lambda colour: (demand[colour], colour))
        split[heaviest] += drift
    return {"basics": split, "nonbasic": duals, "total": total}


def suggest(pool_ids, cards, ratings=None, size=DECK_MINIMUMS["limited"], grades=None):
    """A registrable deck out of the pool, plus the colour pairs that lost and by how much.

    Only the mechanical part is decided here. The archetype, the trap rare and the card
    that is only good against one opponent are left to the player, and the alternatives
    are shown with their totals so the choice stays theirs.
    """
    entries, coverage = playables(pool_ids, cards, ratings, grades)
    pool_lands = [entry for entry in entries if entry["is_land"]]
    lands = size - round(size * (SPELLS / DECK_MINIMUMS["limited"]))
    spells_wanted = size - lands
    scored = []
    for first in range(len(COLOURS)):
        for second in range(first + 1, len(COLOURS)):
            pair = COLOURS[first] + COLOURS[second]
            chosen, _ = _pick_spells(entries, pair, spells_wanted)
            # A pair that cannot fill the deck is still worth showing: seeing that the
            # second-best lane was four cards short is what explains the first one.
            if len(chosen) < MIN_PLAYABLES:
                continue
            scored.append({"pair": pair, "total": round(sum(item["score"] for item in chosen), 1),
                           "chosen": chosen, "short": max(0, spells_wanted - len(chosen))})
    if not scored:
        return {"pair": None, "spells": [], "coverage": coverage,
                "reason": "No colour pair in this pool reaches enough playable cards.",
                "left_out": sorted(({"card_id": entry["card_id"], "name": entry["name"],
                                     "score": entry["score"], "colours": entry["colours"]}
                                    for entry in entries if not entry["is_land"]),
                                   key=lambda item: -item["score"])[:12]}
    # A complete deck beats an incomplete one whatever the totals say.
    scored.sort(key=lambda item: (item["short"], -item["total"]))
    best = scored[0]
    split = _land_split(best["chosen"], best["pair"], pool_lands, lands)
    deck_entries = _deck_entries(best["chosen"], split, cards)
    basis = ("17lands" if coverage["measured"] else
             "community" if coverage["opinion"] else "structure")
    return {
        "pair": best["pair"], "total": best["total"], "size": size, "lands": lands,
        "spells": [
            {key: item[key] for key in ("card_id", "name", "mana_value", "score", "basis", "why")}
            for item in best["chosen"]],
        "land_base": {"basics": split["basics"],
                      "nonbasic": [{"card_id": land["card_id"], "name": land["name"],
                                    "quantity": land["quantity"]} for land in split["nonbasic"]]},
        "curve": _curve(best["chosen"]),
        "creatures": sum(1 for item in best["chosen"]
                         if "Creature" in (item["card"].get("type_codes") or [])),
        "mana": analysis.colour_requirements(deck_entries, deck_size=size),
        "alternatives": [{"pair": item["pair"], "total": item["total"], "short": item["short"]}
                         for item in scored[1:4]],
        "short": best["short"], "coverage": coverage,
        "left_out": sorted(
            ({"card_id": entry["card_id"], "name": entry["name"], "score": entry["score"],
              "colours": entry["colours"]}
             for entry in entries
             if not entry["is_land"] and entry["card_id"] not in
             {item["card_id"] for item in best["chosen"]}),
            key=lambda item: -item["score"])[:12],
        "basis": basis,
        "note": (STRUCTURAL_NOTE if basis == "structure" else
                 COMMUNITY_NOTE if basis == "community" else ""),
    }


def _curve(chosen):
    curve = {}
    for entry in chosen:
        value = entry["mana_value"]
        if isinstance(value, int):
            curve[value] = curve.get(value, 0) + 1
    return dict(sorted(curve.items()))


def _deck_entries(chosen, split, cards):
    """The shape `analysis` reads: one entry per card with a quantity."""
    counts = {}
    for entry in chosen:
        counts[entry["card_id"]] = counts.get(entry["card_id"], 0) + 1
    entries = [{"card": cards.get(cid) or {}, "quantity": quantity}
               for cid, quantity in counts.items()]
    entries.extend({"card": land["card"], "quantity": land["quantity"]}
                   for land in split["nonbasic"])
    for colour, quantity in split["basics"].items():
        if quantity:
            entries.append({"card": _basic(colour), "quantity": quantity})
    return entries


BASIC_NAMES = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}


def _basic(colour):
    return {"id": 0, "name": BASIC_NAMES[colour], "resolved": True, "is_land": True,
            "colors": [colour], "color_identity": [colour], "mana_tokens": [],
            "mana_value": 0, "type_codes": ["Land"], "rarity": "basic"}
