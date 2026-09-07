"""Read and write the deck format the Arena client itself imports and exports.

A line is `<quantity> <english name> (<SET>) <number>`, grouped under `Deck`,
`Sideboard` and `Companion`. The set and number make the printing exact; the name alone
still resolves, so a list copied from a website works too.
"""

import re

from .catalog import english_name, lookup_by_name, lookup_by_print

def _same_card(resolved, wanted):
    """Names match when either side is the front face of the other (split and adventure cards)."""
    if not resolved:
        return False
    left, right = resolved.strip().lower(), wanted.strip().lower()
    return left == right or left.split(" // ")[0] == right.split(" // ")[0]


SECTIONS = {"deck": "main", "mazo": "main", "sideboard": "sideboard",
            "reserva": "sideboard", "companion": "companion", "commander": "commander"}
LINE = re.compile(r"^\s*(\d+)\s+(.+?)(?:\s+\(([A-Za-z0-9]{2,6})\)\s*([A-Za-z0-9\-★]+))?\s*$")


def format_arena(deck, cards):
    """The list as Arena reads it back. Unresolved ids are written as a comment, not dropped."""
    lines, unresolved = [], []
    for label, key in (("Deck", "main"), ("Sideboard", "sideboard")):
        entries = deck.get(key) or []
        if not entries:
            continue
        if lines:
            lines.append("")
        lines.append(label)
        for entry in entries:
            card = cards.get(str(entry.get("id"))) or {}
            quantity = int(entry.get("quantity", 0))
            if not quantity:
                continue
            name = card.get("name_en") or card.get("name")
            if not card.get("resolved") or not name:
                unresolved.append(entry.get("id"))
                lines.append(f"# {quantity} unresolved card (id {entry.get('id')})")
                continue
            code, number = card.get("set"), card.get("collector_number")
            lines.append(f"{quantity} {name} ({code.upper()}) {number}" if code and number
                         else f"{quantity} {name}")
    return {"text": "\n".join(lines) + ("\n" if lines else ""), "unresolved": unresolved}


def parse_arena(text, card_database_path=None):
    """Turn a pasted list into card ids, saying which lines it could not place."""
    section = "main"
    counts = {"main": {}, "sideboard": {}, "companion": {}, "commander": {}}
    problems = []
    for number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        heading = SECTIONS.get(line.rstrip(":").lower())
        if heading:
            section = heading
            continue
        match = LINE.match(line)
        if not match:
            problems.append({"line": number, "text": line[:120], "reason": "line not recognised"})
            continue
        quantity, name, code, collector = match.groups()
        wanted = name.strip()
        card_id = None
        if code and collector:
            candidate = lookup_by_print(code, collector, card_database_path)
            # A set code Arena does not share resolves to a different card at that number.
            # The name on the line is the check that catches it.
            if candidate is not None and _same_card(english_name(candidate, card_database_path), wanted):
                card_id = candidate
            elif candidate is not None:
                problems.append({"line": number, "text": line[:120],
                                 "reason": f"({code}) {collector} is not '{wanted}' in this "
                                           "database; matched by name instead"})
        if card_id is None:
            card_id = lookup_by_name(wanted, card_database_path)
        if card_id is None:
            problems.append({"line": number, "text": line[:120],
                             "reason": f"'{wanted}' is not in the local Arena database"})
            continue
        bucket = counts[section]
        bucket[card_id] = bucket.get(card_id, 0) + int(quantity)
    deck = {key: [{"id": cid, "quantity": qty} for cid, qty in sorted(value.items())]
            for key, value in counts.items()}
    total = sum(item["quantity"] for item in deck["main"])
    return {"deck": {"main": deck["main"], "sideboard": deck["sideboard"]},
            "companion": deck["companion"], "commander": deck["commander"],
            "main_count": total, "problems": problems}
