"""Card grades a player writes, kept locally and exportable as a pull request.

This is the write end of the open data in `community/`. A grade lives on this machine
until its author decides to send it, and what gets sent is a plain JSON file anyone can
read, fork or ignore — including a competing tracker.

The grade is deliberately the weakest signal the app has. Order of preference, and the
interface says which one it used every time:

1. the 17Lands win rate, which is measured across other people's games;
2. a community grade, which is one person's opinion with their name on it;
3. the card's own text, which is a heuristic.

A grade never overrides a measurement. It exists for the fortnight after a set releases,
when the measurement does not exist yet and the alternative is nothing at all.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# The scale set reviews have used for twenty years, so a number here means what a reader
# of any review already expects it to mean.
MIN_GRADE, MAX_GRADE = 0.0, 5.0
MAX_NOTE = 400
# A handle, not an identity: it travels with the grade into a public file.
HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
SCALE = ("0.0 to 5.0. 3.0 is a card you are happy to maindeck; 2.0 is filler; "
         "4.0 and up wins games on its own.")
LICENCE = "CC0-1.0"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CommunityGrades:
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / "community"
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, expansion):
        return self.directory / f"{str(expansion).upper()}.json"

    def load(self, expansion):
        try:
            value = json.loads(self.path_for(expansion).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and isinstance(value.get("cards"), dict) else None

    def grades(self, expansion):
        """Grades by Arena card id, in the shape the ranking reads."""
        stored = self.load(expansion)
        if not stored:
            return {}
        out = {}
        for key, entry in stored["cards"].items():
            try:
                out[int(key)] = entry
            except (TypeError, ValueError):
                continue
        return out

    def save(self, expansion, card_id, grade, note="", by=""):
        """Record one grade. Returns the stored entry, or raises on a value it will not keep."""
        expansion = str(expansion).strip().upper()
        if not expansion:
            raise ValueError("no set given")
        card_id = int(card_id)
        if card_id <= 0:
            raise ValueError("invalid card")
        grade = round(float(grade), 1)
        if not MIN_GRADE <= grade <= MAX_GRADE:
            raise ValueError("grade outside the scale")
        note = str(note or "").strip()[:MAX_NOTE]
        by = str(by or "").strip()
        if by and not HANDLE_PATTERN.match(by):
            raise ValueError("handle may hold letters, digits, dot, dash and underscore only")
        document = self.load(expansion) or {
            "set": expansion, "scale": SCALE, "licence": LICENCE, "cards": {}}
        entry = {"grade": grade, "note": note, "by": by, "at": _now()}
        document["cards"][str(card_id)] = entry
        document["updated"] = _now()[:10]
        document["set"] = expansion
        document.setdefault("scale", SCALE)
        document.setdefault("licence", LICENCE)
        self.path_for(expansion).write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return entry

    def remove(self, expansion, card_id):
        document = self.load(expansion)
        if not document or str(int(card_id)) not in document["cards"]:
            return False
        document["cards"].pop(str(int(card_id)))
        document["updated"] = _now()[:10]
        self.path_for(expansion).write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return True

    def sets(self):
        entries = []
        for path in sorted(self.directory.glob("*.json")):
            document = self.load(path.stem) or {}
            entries.append({"expansion": path.stem, "cards": len(document.get("cards", {})),
                            "updated": document.get("updated")})
        return entries

    def export(self, expansion):
        """The file exactly as it should land in `community/set-reviews/` in the repository."""
        document = self.load(expansion)
        if not document:
            raise KeyError("no grade stored for that set")
        return {"filename": f"community/set-reviews/{str(expansion).upper()}.json",
                "text": json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                "cards": len(document["cards"]), "licence": LICENCE}
