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

from .analysis import hard_pips

# A card entirely outside a committed pool's colours gives up this much win rate. The
# figure is a deliberate choice, not a measurement: it is large enough that a late
# off-colour bomb loses to an on-colour playable, and small enough that pick three still
# takes the best card in the pack.
OFF_COLOUR_PENALTY = 6.0
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


def _why(card, row, fit, chosen, commitment, pick_number):
    reasons = []
    if row.get("gih_wr") is not None:
        games = row.get("gih_games")
        reasons.append(f"{row['gih_wr'] * 100:.1f}% win rate in games where it was drawn"
                       + (f", over {games:,} of them" if isinstance(games, int) else ""))
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


def advise(pack_ids, pool_ids, ratings, cards, pick_number=None):
    """Rank a pack. `ratings` is keyed by Arena id; `cards` is the local catalogue."""
    pool_cards = [cards.get(cid) for cid in pool_ids]
    chosen, weights = lane(pool_cards)
    commitment = min(1.0, len(pool_ids) / COMMITMENT_PICKS)
    rated, unrated = [], []
    # A pack holds one of each card; reading the same id twice would put a card against
    # itself and report the tie as a close call.
    for cid in dict.fromkeys(int(value) for value in pack_ids):
        card = cards.get(cid) or {}
        row = (ratings or {}).get(int(cid))
        if not row or row.get("gih_wr") is None:
            unrated.append({"card_id": int(cid), "name": card.get("name") or f"Card #{cid}"})
            continue
        fit = _fit(card, chosen)
        base = row["gih_wr"] * 100
        adjustment = -OFF_COLOUR_PENALTY * commitment * (1 - fit)
        rated.append({
            "card_id": int(cid), "name": card.get("name") or row.get("name") or f"Card #{cid}",
            "rarity": card.get("rarity", ""), "mana_value": card.get("mana_value"),
            "colours": _colours_of(card), "gih_wr": row.get("gih_wr"),
            "gih_games": row.get("gih_games"), "alsa": row.get("alsa"),
            "base": round(base, 2), "colour_adjustment": round(adjustment, 2),
            "score": round(base + adjustment, 2), "fit": fit,
            "why": _why(card, row, fit, chosen, commitment, pick_number),
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
        "caveat": CAVEAT,
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
