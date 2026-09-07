"""Deck and sample mathematics computed locally, with the sample size always visible.

Every function here is closed-form: no simulation, no external service and no model
opinion. What the numbers do not support is returned as `None`, never as a rounded
guess. The consumer is required to show `n` next to any rate produced here.
"""

from math import comb, isqrt
from statistics import NormalDist

STARTING_HAND = 7

# Wizards states the BO1 opening hand is chosen from separately randomised copies of the
# deck, "leaning towards" the hand whose land ratio is closest to the deck's average
# (WotC, October 2018; clarified May 2019 that it leans rather than always picks). The
# exact weighting is undisclosed, so a measured BO1 opening-hand rate is not comparable
# to the hypergeometric baseline below. Draws after the opening hand are not smoothed.
BO1_SMOOTHING_CAVEAT = (
    "Arena's best-of-one draws the opening hand from separately shuffled copies of the deck "
    "and leans toward the deck's average land ratio. The exact weighting is undisclosed, so a "
    "best-of-one opening-hand rate is not comparable to the hypergeometric baseline."
)


def hypergeometric_at_least(wanted, successes, population, draws):
    """P(at least `wanted` successes) when drawing `draws` from `population`.

    Returns 0.0 when the draw cannot contain that many successes, and 1.0 when it
    always does; invalid parameters raise instead of silently returning a number.
    """
    if min(wanted, successes, population, draws) < 0 or draws > population or successes > population:
        raise ValueError("parameters outside the valid range")
    if wanted == 0:
        return 1.0
    if wanted > successes or wanted > draws:
        return 0.0
    total = comb(population, draws)
    below = sum(comb(successes, k) * comb(population - successes, draws - k)
                for k in range(0, min(wanted, successes, draws)))
    return 1.0 - below / total


def cards_seen(turn, on_play, mulligans=0):
    """Cards seen by the start of `turn`: the London mulligan keeps a seven-card look.

    A London mulligan draws seven and bottoms `mulligans` cards, so the number of
    *distinct cards seen* stays seven; the hand shrinks but the sample does not.
    """
    if turn < 1:
        raise ValueError("turns start at 1")
    return STARTING_HAND + (turn - 1) + (0 if on_play else 1)


def draw_probability(copies, deck_size, turn, on_play=True, wanted=1):
    """P(holding at least `wanted` copies by the start of `turn`)."""
    return hypergeometric_at_least(wanted, copies, deck_size, min(cards_seen(turn, on_play), deck_size))


# Frank Karsten, "How Many Sources Do You Need to Consistently Cast Your Spells? A 2022
# Update" (TCGplayer), 60-card deck, ~90% consistency, on the play, London mulligan
# modelled. Key: (coloured pips, turn the spell is wanted) -> coloured sources.
# The mulligan modelling is why these numbers are lower than the plain hypergeometric
# floor below: a player who mulligans a 0/1/6/7-land hand improves the effective draw.
KARSTEN_2022_SOURCES = {
    (1, 1): 14, (1, 2): 13, (1, 3): 12,
    (2, 2): 21, (2, 3): 18, (2, 4): 16,
    (3, 3): 23, (3, 4): 21,
    (4, 4): 24,
}
KARSTEN_2022_CITATION = ("Frank Karsten, \"How Many Sources Do You Need to Consistently "
                         "Cast Your Spells? A 2022 Update\", TCGplayer — 60-card deck, 90% "
                         "consistency, on the play, London mulligan modelled.")

# Karsten, "How Many Lands Do You Need in Your Deck? An Updated Analysis" (TCGplayer):
# regression over 95,000+ tournament decklists.
LAND_BASE, LAND_PER_MANA_VALUE = 19.59, 1.90
LAND_ADJUSTMENTS = {"cheap_draw": -0.28, "fast_mana": -1.00,
                    "untapped_mdfc": -0.74, "tapped_mdfc": -0.38}


def published_sources_needed(pips, turn):
    """Karsten's published requirement, or the nearest published turn for that pip count.

    Returns (sources, exact) where `exact` is False when the table has no entry for that
    turn and the closest published turn was used instead. Outside the published range the
    answer is None — the app then shows the hypergeometric floor and says which it is.
    """
    if (pips, turn) in KARSTEN_2022_SOURCES:
        return KARSTEN_2022_SOURCES[(pips, turn)], True
    candidates = [key for key in KARSTEN_2022_SOURCES if key[0] == pips]
    if not candidates:
        return None, False
    nearest = min(candidates, key=lambda key: (abs(key[1] - turn), key[1]))
    return KARSTEN_2022_SOURCES[nearest], False


def lands_recommended(average_mana_value, cheap_draw=0, fast_mana=0,
                      untapped_mdfc=0, tapped_mdfc=0):
    """Karsten's land-count regression for a 60-card deck, with its documented offsets."""
    total = LAND_BASE + LAND_PER_MANA_VALUE * average_mana_value
    total += LAND_ADJUSTMENTS["cheap_draw"] * cheap_draw
    total += LAND_ADJUSTMENTS["fast_mana"] * fast_mana
    total += LAND_ADJUSTMENTS["untapped_mdfc"] * untapped_mdfc
    total += LAND_ADJUSTMENTS["tapped_mdfc"] * tapped_mdfc
    return round(total, 2)


def sources_needed(pips, turn, deck_size=60, target=0.90, on_play=True):
    """Hypergeometric floor: sources for `pips` by `turn` with no mulligan modelled.

    This is deliberately stricter than the published table — it assumes the player keeps
    whatever seven cards arrive. Present it as a floor, never as Karsten's number.
    """
    seen = min(cards_seen(turn, on_play), deck_size)
    for sources in range(pips, deck_size + 1):
        if hypergeometric_at_least(pips, sources, deck_size, seen) >= target:
            return sources
    return None


def wilson_interval(wins, total, confidence=0.95):
    """Wilson score interval — the honest error bar for a small personal sample.

    Returns None for an empty sample instead of a 0%–100% band dressed as a result.
    """
    if total <= 0:
        return None
    if wins < 0 or wins > total:
        raise ValueError("wins outside the sample")
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    rate = wins / total
    denominator = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denominator
    margin = z * ((rate * (1 - rate) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return {"rate": rate, "low": max(0.0, centre - margin), "high": min(1.0, centre + margin),
            "n": total, "confidence": confidence}


def games_needed(baseline, difference, power=0.80, confidence=0.95):
    """Games per arm to detect `difference` in win rate — why a 20-game sample says nothing.

    Two-proportion test, equal arms. The result is deliberately large: it is the number
    that keeps a deck change from being judged on noise.
    """
    if not 0 < baseline < 1 or difference <= 0 or baseline + difference >= 1:
        raise ValueError("rates must lie strictly between 0 and 1")
    z_alpha = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    z_beta = NormalDist().inv_cdf(power)
    other = baseline + difference
    pooled = (baseline + other) / 2
    numerator = (z_alpha * (2 * pooled * (1 - pooled)) ** 0.5
                 + z_beta * (baseline * (1 - baseline) + other * (1 - other)) ** 0.5) ** 2
    return int(-(-numerator // (difference * difference)))


def mana_curve(entries):
    """{mana value: quantity} for non-land cards, or None when the catalogue has a gap.

    `entries` are {'card': resolved card, 'quantity': int}. A single unresolved card
    suppresses the curve: a curve missing four cards is worse than no curve.
    """
    curve = {}
    for entry in entries:
        card = entry.get("card") or {}
        if not card.get("resolved") or card.get("mana_value") is None:
            return None
        if card.get("is_land"):
            continue
        value = int(card["mana_value"])
        curve[value] = curve.get(value, 0) + int(entry.get("quantity", 0))
    return dict(sorted(curve.items()))


def colour_sources(entries):
    """{colour: land count} for lands that produce each colour, by the card's own colours.

    Arena's card record gives a land its colour identity, which is the closest local
    proxy for what it taps for. A land whose colours are unknown counts only in `total`.
    """
    counts = {}
    total = 0
    for entry in entries:
        card = entry.get("card") or {}
        if not card.get("is_land"):
            continue
        quantity = int(entry.get("quantity", 0))
        total += quantity
        for colour in card.get("colors") or []:
            counts[colour] = counts.get(colour, 0) + quantity
    return {"by_colour": dict(sorted(counts.items())), "total_lands": total}


PURE_PIPS = set("WUBRG")


def hard_pips(card):
    """Coloured pips that can only be paid with that colour.

    A hybrid pip ({R/G}) is satisfied by either colour and a Phyrexian pip ({B/P}) by two
    life, so neither imposes a source count. Counting them as pure pips is how a mana-base
    check invents a shortfall in a colour the deck never actually needs.
    """
    counts = {}
    for token in card.get("mana_tokens") or []:
        if token in PURE_PIPS:
            counts[token] = counts.get(token, 0) + 1
    return counts


def flexible_costs(entries):
    """Cards whose coloured cost is hybrid or Phyrexian, and therefore left out."""
    named = []
    for entry in entries:
        card = entry.get("card") or {}
        if not card.get("resolved") or card.get("is_land"):
            continue
        if any("/" in str(token) for token in card.get("mana_tokens") or []):
            named.append(card.get("name") or f"#{card.get('id')}")
    return sorted(set(named))


def colour_requirements(entries, deck_size=60, target=0.90):
    """Per-colour source shortfall for the earliest turn each pip count is wanted.

    For every colour, the deck's own cards define the demand: a {1}{B}{B} three-drop
    asks for two black sources by turn three. The answer names the shortfall and the
    card that sets it, so the recommendation is auditable against the list.
    """
    demands = {}
    for entry in entries:
        card = entry.get("card") or {}
        if not card.get("resolved") or card.get("is_land") or card.get("mana_value") is None:
            continue
        turn = max(1, int(card["mana_value"]))
        for colour, pips in hard_pips(card).items():
            key = (colour, pips)
            if key not in demands or turn < demands[key]["turn"]:
                demands[key] = {"turn": turn, "card": card.get("name") or f"#{card.get('id')}"}
    available = colour_sources(entries)["by_colour"]
    findings = []
    for (colour, pips), demand in sorted(demands.items()):
        published, exact = published_sources_needed(pips, demand["turn"])
        floor = sources_needed(pips, demand["turn"], deck_size, target)
        needed = published if published is not None else floor
        if needed is None:
            continue
        have = available.get(colour, 0)
        findings.append({"colour": colour, "pips": pips, "turn": demand["turn"],
                         "needed": needed, "have": have, "shortfall": max(0, needed - have),
                         "driver": demand["card"],
                         "basis": "karsten-2022" if published is not None else "hypergeometric",
                         "exact_turn": exact, "hypergeometric_floor": floor})
    return findings


def remaining_library(deck_counts, seen_counts):
    """Cards still unseen in the library: registered list minus every observed copy.

    Exact for the player's own deck, because Arena discloses every zone the player can
    see. It is not computed for the opponent, whose list is unknown by construction.
    """
    remaining = {}
    for card_id, quantity in deck_counts.items():
        left = quantity - seen_counts.get(card_id, 0)
        if left > 0:
            remaining[card_id] = left
    return remaining


def isqrt_floor(value):
    """Exposed for callers that need an integer square root without importing math."""
    return isqrt(value)
