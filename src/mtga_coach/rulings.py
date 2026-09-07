"""Official card rulings, fetched once per card and kept on disk.

Rulings are the piece of rules knowledge worth carrying, because they are *about this
card*: "you lose 2 life even if the target has no nonland cards", "it still enters
tapped". They are retrieved by card identity, so there is no search step and nothing to
get wrong, and only the cards present in the position ever travel with a question.

The Comprehensive Rules are deliberately not bundled: at roughly 225,000 tokens they cost
more per question than every other part of the request combined, and they do not answer
"what does this card do here" — the rulings do.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .art import API, BATCH, CARD_PAUSE, COLLECTION_PAUSE, USER_AGENT

CREDIT = "Card rulings published by Wizards of the Coast, retrieved through Scryfall."
MAX_PER_CARD = 8


def _get(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _post(url, payload, timeout=25):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class RulingsCache:
    def __init__(self, data_dir, fetcher=None):
        self.directory = Path(data_dir) / "rulings"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or _RealFetcher()

    def path_for(self, card_id):
        return self.directory / f"{int(card_id)}.json"

    def has(self, card_id):
        return self.path_for(card_id).is_file()

    def get(self, card_id):
        """Stored rulings for one card, or None when the card was never asked about.

        An empty list is a real answer — this card has no rulings — and is not the same
        as never having looked.
        """
        try:
            value = json.loads(self.path_for(card_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, list) else None

    def for_cards(self, card_ids):
        found = {}
        for card_id in card_ids:
            stored = self.get(card_id)
            if stored:
                found[int(card_id)] = stored
        return found

    def stats(self):
        files = list(self.directory.glob("*.json"))
        with_text = sum(1 for item in files if (self.get(int(item.stem)) or []))
        return {"cards_checked": len(files), "cards_with_rulings": with_text,
                "bytes": sum(item.stat().st_size for item in files), "credit": CREDIT}

    def pending(self, cards):
        return [card for card in cards if card.get("resolved") and not self.has(card["id"])]

    def fetch(self, cards, limit=120):
        wanted = self.pending(cards)[:limit]
        if not wanted:
            return {"requested": 0, "stored": 0, "with_rulings": 0, "errors": []}
        found, errors = self.fetcher.resolve(wanted)
        stored = with_rulings = 0
        for card_id, rulings in found.items():
            trimmed = [{"date": item.get("published_at"), "text": item.get("comment")}
                       for item in rulings[:MAX_PER_CARD] if item.get("comment")]
            self.path_for(card_id).write_text(json.dumps(trimmed, ensure_ascii=False), encoding="utf-8")
            stored += 1
            if trimmed:
                with_rulings += 1
        return {"requested": len(wanted), "stored": stored, "with_rulings": with_rulings,
                "errors": errors[:5]}


class _RealFetcher:
    """Talks to Scryfall. Isolated so the tests never reach the network."""

    def resolve(self, cards):
        found, errors = {}, []
        by_print, without_print = {}, []
        for card in cards:
            if card.get("set") and card.get("collector_number"):
                by_print.setdefault((card["set"].lower(), str(card["collector_number"])), []).append(card["id"])
            else:
                without_print.append(card["id"])
        # One batch call gives every card's rulings address; only then is there one
        # request per card, and only for cards the batch actually matched.
        addresses = {}
        keys = list(by_print)
        for start in range(0, len(keys), BATCH):
            chunk = keys[start:start + BATCH]
            payload = {"identifiers": [{"set": code, "collector_number": number} for code, number in chunk]}
            try:
                answer = _post(f"{API}/cards/collection", payload)
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
                errors.append(f"batch lookup failed: {error}")
                time.sleep(COLLECTION_PAUSE)
                continue
            for entry in answer.get("data", []):
                key = (str(entry.get("set", "")).lower(), str(entry.get("collector_number", "")))
                for card_id in by_print.get(key, []):
                    if entry.get("rulings_uri"):
                        addresses[card_id] = entry["rulings_uri"]
            time.sleep(COLLECTION_PAUSE)
        for card_id in without_print:
            try:
                entry = _get(f"{API}/cards/arena/{int(card_id)}")
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, TimeoutError):
                time.sleep(CARD_PAUSE)
                continue
            if entry.get("rulings_uri"):
                addresses[card_id] = entry["rulings_uri"]
            time.sleep(CARD_PAUSE)
        for card_id, address in addresses.items():
            try:
                found[card_id] = _get(address).get("data", [])
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
                errors.append(f"{card_id}: {type(error).__name__}")
            time.sleep(CARD_PAUSE)
        return found, errors
