"""Does an event pay for itself at the rate you actually win?

Every limited entry costs gems or gold, and Arena's prize structure pays by wins. Those
two facts make the question answerable rather than a matter of opinion: given a win rate,
the expected return of an entry is a sum over the possible records, each weighted by how
likely that record is under the event's own stop rule.

What this module does **not** do is decide whether the answer is good. A draft that
returns 80% of its entry is still the cheapest way most players will ever open cards, and
turning that into "do not draft" would be this app substituting its arithmetic for a
person's reasons for playing.

The prize tables are Wizards' published structures. They change; when the app cannot tell
which structure an event used, it says so and computes nothing.
"""

# Arena's limited events, as published by Wizards. Each entry: the two prices, the record
# the event stops at, and the payout per number of wins. Gems first, gold second; gold is
# None where the event has no gold price.
EVENTS = {
    "QuickDraft": {
        "label": "Quick Draft",
        "gems": 750, "gold": 5000, "wins_cap": 7, "losses_cap": 3,
        "packs": 1,
        "prizes": {0: {"gems": 50, "packs": 1.2}, 1: {"gems": 100, "packs": 1.22},
                   2: {"gems": 200, "packs": 1.24}, 3: {"gems": 300, "packs": 1.26},
                   4: {"gems": 450, "packs": 1.30}, 5: {"gems": 650, "packs": 1.35},
                   6: {"gems": 850, "packs": 1.40}, 7: {"gems": 950, "packs": 2.0}},
    },
    "PremierDraft": {
        "label": "Premier Draft",
        "gems": 1500, "gold": 10000, "wins_cap": 7, "losses_cap": 3,
        "packs": 3,
        "prizes": {0: {"gems": 50, "packs": 1}, 1: {"gems": 100, "packs": 1},
                   2: {"gems": 250, "packs": 2}, 3: {"gems": 1000, "packs": 2},
                   4: {"gems": 1400, "packs": 3}, 5: {"gems": 1600, "packs": 4},
                   6: {"gems": 1800, "packs": 5}, 7: {"gems": 2200, "packs": 6}},
    },
    "TradDraft": {
        "label": "Traditional Draft",
        "gems": 1500, "gold": 10000, "wins_cap": 3, "losses_cap": 1,
        "packs": 3, "best_of": 3,
        "prizes": {0: {"gems": 0, "packs": 1}, 1: {"gems": 0, "packs": 1},
                   2: {"gems": 1000, "packs": 4}, 3: {"gems": 3000, "packs": 6}},
    },
}
# What the store charges for a pack, used only to put packs and gems on one axis. It is a
# price, not a valuation: a pack is worth what it opens, which nobody can put a number on.
PACK_GEMS = 200
SOURCE = ("Entry prices and prize structures as published by Wizards of the Coast for each "
          "event. They change between seasons; this table is the app's copy and the app "
          "computes nothing for an event it does not recognise.")
# Nothing in the table above is read from the game — it is typed in, and typed-in numbers
# rot. The one figure confirmed on a real account is the Quick Draft entry: a 750-gem drop
# was measured in the wallet at the moment of entry. Everything else needs a look at the
# client's own event screen before it is trusted.
UNVERIFIED = ("The prize table is typed into this app, not read from the game. The Quick "
              "Draft entry of 750 gems is confirmed against a measured wallet drop; the "
              "payouts are not. Check the event screen in the client before betting a "
              "decision on them.")


def outcome_probabilities(win_rate, wins_cap, losses_cap):
    """P(final record) for an event that stops at N wins or M losses.

    Games are treated as independent with a constant win rate. That is the assumption the
    whole calculation rests on and it is not quite true — a run meets stronger opponents as
    it goes — so the answer is a estimate under a stated model, not a measurement.
    """
    rate = min(max(float(win_rate), 0.0), 1.0)
    # states[(wins, losses)] = probability of standing there
    states = {(0, 0): 1.0}
    final = {}
    while states:
        nxt = {}
        for (wins, losses), probability in states.items():
            for won, step in ((True, rate), (False, 1 - rate)):
                if step <= 0:
                    continue
                position = (wins + 1, losses) if won else (wins, losses + 1)
                weight = probability * step
                if position[0] >= wins_cap or position[1] >= losses_cap:
                    final[position] = final.get(position, 0.0) + weight
                else:
                    nxt[position] = nxt.get(position, 0.0) + weight
        states = nxt
    return final


def expected_return(event, win_rate):
    """Gems and packs an entry returns on average, and the record distribution behind it."""
    table = EVENTS.get(event)
    if table is None:
        return None
    outcomes = outcome_probabilities(win_rate, table["wins_cap"], table["losses_cap"])
    gems = packs = 0.0
    records = []
    for (wins, losses), probability in sorted(outcomes.items()):
        prize = table["prizes"].get(min(wins, max(table["prizes"])), {"gems": 0, "packs": 0})
        gems += probability * prize["gems"]
        packs += probability * prize["packs"]
        records.append({"wins": wins, "losses": losses, "probability": round(probability, 6),
                        "gems": prize["gems"], "packs": prize["packs"]})
    return {"event": event, "label": table["label"], "win_rate": round(win_rate, 4),
            "entry_gems": table["gems"], "entry_gold": table["gold"],
            "packs_included": table["packs"],
            "expected_gems": round(gems, 1), "expected_packs": round(packs, 2),
            "records": records}


def verdict(event, win_rate):
    """The entry against what it returns, with the pack price used to compare them."""
    answer = expected_return(event, win_rate)
    if answer is None:
        return {"event": event, "known": False, "note": (
            f"No published prize structure stored for {event}, so nothing is computed.")}
    # The packs the entry itself includes are part of what the gems bought, so they count
    # on the return side; leaving them out would make every draft look worse than it is.
    returned = answer["expected_gems"] + (answer["expected_packs"] + answer["packs_included"]) * PACK_GEMS
    entry = answer["entry_gems"]
    return {**answer, "known": True, "pack_gems": PACK_GEMS,
            "returned_gems_equivalent": round(returned, 1),
            "net_gems": round(returned - entry, 1),
            "ratio": round(returned / entry, 3) if entry else None,
            "break_even_rate": break_even(event),
            "source": SOURCE,
            "note": ("Packs are counted at the store price so they can be added to gems. That "
                     "is a price, not a valuation: a pack is worth what it opens.")}


def break_even(event, precision=0.001):
    """The win rate at which the entry returns what it cost, by bisection."""
    table = EVENTS.get(event)
    if table is None:
        return None
    def net(rate):
        answer = expected_return(event, rate)
        returned = answer["expected_gems"] + (answer["expected_packs"] + answer["packs_included"]) * PACK_GEMS
        return returned - table["gems"]
    low, high = 0.0, 1.0
    if net(low) >= 0:
        return 0.0
    if net(high) < 0:
        return None
    while high - low > precision:
        middle = (low + high) / 2
        if net(middle) < 0:
            low = middle
        else:
            high = middle
    return round((low + high) / 2, 3)
