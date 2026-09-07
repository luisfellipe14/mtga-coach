"""Rank the pack in front of you, and name a pick.

The number under every recommendation is 17Lands' games-in-hand win rate: across the
drafts their contributors played, the share won of the games where that card was drawn or
opened. It is the strongest public signal about a card in a set, and it is also blind to
the one thing that decides a real pick — what is already in your pool. So the raw number
is adjusted here for exactly one thing, colour, because that is the factor a table can
measure: a card outside the colours a pool has committed to wins fewer games *for that
deck*, and how much fewer depends on how far into the draft the decision is.

Everything else the draft turns on — the archetype, the signals coming round, what the
players either side are cutting — is left as a note rather than folded into a number,
because inventing a coefficient for it would hide a guess inside a total.
"""

from . import build
from .analysis import hard_pips

# A card entirely outside a committed pool's colours gives up this much win rate. The
# figure is a deliberate choice, not a measurement: it is large enough that a late
# off-colour bomb loses to an on-colour playable, and small enough that pick three still
# takes the best card in the pack.
OFF_COLOUR_PENALTY = 6.0
# The penalty has to be in the units of whatever scale is ranking the pack, or it either
# decides every pick or none of them. The measured scale is win-rate points; the other two
# are the 0-10 rank used by the deck builder, where the same weight is about half as large.
PENALTY_BY_BASIS = {"17lands": OFF_COLOUR_PENALTY, "community": 3.0, "structure": 3.0}
# What share of the pack a source has to cover before it is the one ranking the pack.
COVERAGE = 0.6
# By this many picks a pool has told you what it is. Before it, the penalty scales in.
COMMITMENT_PICKS = 12
# Two cards inside this margin are a judgement call, not a ranking.
CLOSE_MARGIN = 0.4
# 17Lands publishes rates on tiny samples too; under this the number moves with noise.
THIN_SAMPLE = 500
# A pack comes back around after this many seats in an eight-player pod.
WHEEL_DISTANCE = 8

CAVEAT = ("The win rate is other players' games with that card, not your deck with it. "
          "The colour adjustment is this app's, and it is the only thing added to the "
          "public number.")
BASIS_CAVEAT = {
    "community": ("No published win rate covers this set yet, so the order comes from grades "
                  "written by hand and signed. They are opinions, and the measurement replaces "
                  "them the moment 17Lands has one."),
    "structure": ("No published win rate and no grade covers this set yet, so the order is read "
                  "off the cards themselves: removal, bodies, card draw, curve. It can tell a "
                  "removal spell from a lifegain spell. It cannot tell a bomb from a trap."),
}


def _colours_of(card):
    """The colours a card commits you to. A land commits nothing and fixes instead."""
    if not card or not card.get("resolved"):
        return []
    if card.get("is_land"):
        return []
    return list(card.get("colors") or [])


def lane(pool_cards):
    """The colours a pool has actually paid for, heaviest first.

    Pips are counted rather than cards: a card with two red pips argues for red twice as
    loudly as one with a single red pip, and that is the same measure the mana base uses.
    """
    weights = {}
    for card in pool_cards:
        if not card or not card.get("resolved"):
            continue
        if card.get("is_land"):
            # A land does not commit the deck, but it does make a colour cheaper to be in.
            for colour in card.get("color_identity") or []:
                weights[colour] = weights.get(colour, 0) + 0.5
            continue
        pips = hard_pips(card)
        for colour in _colours_of(card):
            weights[colour] = weights.get(colour, 0) + max(1, pips.get(colour, 0))
    order = sorted(weights, key=lambda colour: (-weights[colour], colour))
    return order[:2], weights


def _fit(card, chosen):
    """How much of a card's colour requirement the pool already supports, 0 to 1."""
    colours = _colours_of(card)
    if not colours:
        return 1.0
    inside = sum(1 for colour in colours if colour in chosen)
    return inside / len(colours)


def _why(card, row, fit, chosen, commitment, pick_number, basis="17lands"):
    reasons = []
    if basis == "17lands" and row.get("gih_wr") is not None:
        games = row.get("gih_games")
        reasons.append(f"{row['gih_wr'] * 100:.1f}% win rate in games where it was drawn"
                       + (f", over {games:,} of them" if isinstance(games, int) else ""))
    elif basis != "17lands":
        if card.get("is_land"):
            produces = "".join(card.get("color_identity") or []) or "nothing"
            reasons.append(f"land producing {produces}"
                           + (" — fixes the colours your pool is paying for"
                              if set(card.get("color_identity") or []) & set(chosen) else ""))
        else:
            _, structural = build.structural_score(card)
            if structural:
                reasons.append(", ".join(structural[:3]))
    colours = _colours_of(card)
    if not colours:
        reasons.append("colourless — it goes in any deck you end up with")
    elif fit >= 1:
        reasons.append(f"inside the colours your pool is paying for ({''.join(chosen) or 'none yet'})")
    elif fit > 0:
        reasons.append(f"half in your colours: {''.join(colours)} against your {''.join(chosen)}")
    elif commitment >= 0.5:
        reasons.append(f"outside your colours ({''.join(colours)} against your {''.join(chosen)})")
    else:
        reasons.append(f"{''.join(colours)}, and your pool is not committed yet")
    alsa = row.get("alsa")
    if isinstance(alsa, (int, float)) and pick_number and alsa >= pick_number + WHEEL_DISTANCE:
        reasons.append(f"tends to still be there late (average last seen at pick {alsa:.1f}), "
                       "so it may come back on the wheel")
    if isinstance(row.get("gih_games"), int) and row["gih_games"] < THIN_SAMPLE:
        reasons.append(f"thin sample: only {row['gih_games']:,} games behind that rate")
    return reasons


def _basis_for(pack, ratings, grades, cards):
    """Which source ranks this pack, decided once for the whole pack.

    A pack where two cards were measured and twelve were guessed at is a pack that reads
    as measured and is not. So the sources do not mix: the best-covered one wins outright,
    in the order measurement, signed opinion, the card's own text.
    """
    if not pack:
        return "structure"
    measured = sum(1 for cid in pack
                   if (ratings or {}).get(cid) and (ratings or {})[cid].get("gih_wr") is not None)
    if measured / len(pack) >= COVERAGE:
        return "17lands"
    graded = sum(1 for cid in pack if (grades or {}).get(cid, {}).get("grade") is not None)
    if graded / len(pack) >= COVERAGE:
        return "community"
    return "structure"


# A dual land is not a spell and the text reader scores it zero, which would rank the one
# card that fixes a two-colour deck below every filler in the pack. In the colours the pool
# is paying for it plays like a solid playable; outside them it is close to nothing.
LAND_IN_LANE = 4.5
LAND_OFF_LANE = 1.0


def _score_for(basis, card, ratings_row, grade_row, lane_colours=()):
    """The card's score on the scale the pack is being ranked with, or None."""
    if basis == "17lands":
        return None if not ratings_row or ratings_row.get("gih_wr") is None else ratings_row["gih_wr"] * 100
    if basis == "community":
        return None if not grade_row or grade_row.get("grade") is None else float(grade_row["grade"]) * 2
    if not card.get("resolved"):
        return None
    if card.get("is_land"):
        # A basic land is never a pick: the deck gets as many as it wants for free.
        if card.get("rarity") == "basic":
            return 0.0
        produces = set(card.get("color_identity") or [])
        if not produces:
            return LAND_OFF_LANE
        return LAND_IN_LANE if produces & set(lane_colours) else LAND_OFF_LANE
    score, _ = build.structural_score(card)
    return score


def advise(pack_ids, pool_ids, ratings, cards, pick_number=None, grades=None):
    """Rank a pack. `ratings` is keyed by Arena id; `cards` is the local catalogue.

    A set that has just released has no published win rate, and staying silent about the
    pack for a fortnight is worse than ranking it off the cards themselves and saying so.
    """
    pool_cards = [cards.get(cid) for cid in pool_ids]
    chosen, weights = lane(pool_cards)
    commitment = min(1.0, len(pool_ids) / COMMITMENT_PICKS)
    pack = list(dict.fromkeys(int(value) for value in pack_ids))
    basis = _basis_for(pack, ratings, grades, cards)
    penalty = PENALTY_BY_BASIS[basis]
    rated, unrated = [], []
    # A pack holds one of each card; reading the same id twice would put a card against
    # itself and report the tie as a close call.
    for cid in pack:
        card = cards.get(cid) or {}
        row = (ratings or {}).get(int(cid))
        base = _score_for(basis, card, row, (grades or {}).get(int(cid)), chosen)
        if base is None:
            unrated.append({"card_id": int(cid), "name": card.get("name") or f"Card #{cid}"})
            continue
        fit = _fit(card, chosen)
        adjustment = -penalty * commitment * (1 - fit)
        rated.append({
            "card_id": int(cid), "name": card.get("name") or row.get("name") or f"Card #{cid}",
            "rarity": card.get("rarity", ""), "mana_value": card.get("mana_value"),
            "colours": _colours_of(card), "gih_wr": (row or {}).get("gih_wr"),
            "gih_games": (row or {}).get("gih_games"), "alsa": (row or {}).get("alsa"),
            "base": round(base, 2), "colour_adjustment": round(adjustment, 2),
            "score": round(base + adjustment, 2), "fit": fit,
            "why": _why(card, row or {}, fit, chosen, commitment, pick_number, basis),
        })
    rated.sort(key=lambda item: (-item["score"], item["name"]))
    best = rated[0] if rated else None
    close = [item["card_id"] for item in rated[1:]
             if best and best["score"] - item["score"] <= CLOSE_MARGIN]
    return {
        "pick": best["card_id"] if best else None,
        "pick_name": best["name"] if best else "",
        "margin": round(best["score"] - rated[1]["score"], 2) if len(rated) > 1 else None,
        "close_calls": close, "ranked": rated, "unrated": unrated,
        "lane": chosen, "colour_weights": {k: round(v, 1) for k, v in weights.items()},
        "commitment": round(commitment, 2), "notes": _notes(pool_cards, unrated, commitment, chosen),
        "basis": basis, "caveat": CAVEAT if basis == "17lands" else BASIS_CAVEAT[basis],
    }


def _notes(pool_cards, unrated, commitment, chosen):
    notes = []
    resolved = [card for card in pool_cards if card and card.get("resolved")]
    creatures = [card for card in resolved if "Creature" in (card.get("type_codes") or [])]
    if len(resolved) >= 8:
        notes.append(f"{len(creatures)} creatures in {len(resolved)} picks — a limited deck usually "
                     "wants between fifteen and seventeen.")
        curve = {}
        for card in resolved:
            if not card.get("is_land") and card.get("mana_value") is not None:
                curve[card["mana_value"]] = curve.get(card["mana_value"], 0) + 1
        early = sum(count for value, count in curve.items() if value <= 2)
        if early <= max(2, len(resolved) // 5):
            notes.append(f"only {early} cards at two mana or less so far.")
    if unrated:
        notes.append(f"{len(unrated)} card(s) in this pack carry no published rate, so the order "
                     "cannot speak for them.")
    if commitment < 0.5 and chosen:
        notes.append("Early picks: the colour adjustment is deliberately small here, because the "
                     "pool has not committed to anything yet.")
    return notes
