"""Fetch card art from Scryfall once and keep it on disk.

This is the only part of the app that touches the network, and it is off until the user
turns it on. What leaves the machine is a set code and a collector number — never a match,
a deck, an account or anything about how the user played. Each image is downloaded once and
served from disk afterwards, so a warmed cache runs offline like the rest of the app.

Card identity comes from the Arena database already installed locally; Scryfall is asked
only for the picture.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.scryfall.com"
# ASCII only: a header with an accented character is rejected by Scryfall with a 403.
USER_AGENT = "MTGACoach/0.2 (personal review app)"
# Scryfall asks for 50-100 ms between calls; /cards/collection is the stricter endpoint.
COLLECTION_PAUSE = 0.5
CARD_PAUSE = 0.12
BATCH = 75
IMAGE_KIND = "art_crop"
CREDIT = "Card art via Scryfall. Names and rules text come from the local Arena database."


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


def _download(url, destination, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    destination.write_bytes(data)
    return len(data)


def image_url(card):
    """The art crop of the front face, or None when the printing carries no image."""
    images = card.get("image_uris")
    if not images and card.get("card_faces"):
        images = (card["card_faces"][0] or {}).get("image_uris")
    return (images or {}).get(IMAGE_KIND)


class ArtCache:
    def __init__(self, data_dir, fetcher=None):
        self.directory = Path(data_dir) / "art"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.misses_path = self.directory / "sem-imagem.json"
        self.fetcher = fetcher or _RealFetcher()

    # ------------------------------------------------------------------ disk

    def path_for(self, card_id):
        return self.directory / f"{int(card_id)}.jpg"

    def has(self, card_id):
        path = self.path_for(card_id)
        return path.is_file() and path.stat().st_size > 0

    def misses(self):
        try:
            value = json.loads(self.misses_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {int(item) for item in value} if isinstance(value, list) else set()

    def _remember_misses(self, ids):
        """A card with no paper printing must not be asked for on every open."""
        if not ids:
            return
        self.misses_path.write_text(json.dumps(sorted(self.misses() | set(ids))), encoding="utf-8")

    def stats(self):
        files = [item for item in self.directory.glob("*.jpg") if item.is_file()]
        return {"cached": len(files), "bytes": sum(item.stat().st_size for item in files),
                "without_image": len(self.misses()), "credit": CREDIT}

    def clear(self):
        for item in self.directory.glob("*.jpg"):
            item.unlink(missing_ok=True)
        self.misses_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ fetching

    def pending(self, cards):
        """Cards worth asking about: resolved, not cached, not already known to have none."""
        known = self.misses()
        return [card for card in cards
                if card.get("resolved") and not self.has(card["id"]) and card["id"] not in known]

    def fetch(self, cards, limit=200):
        """Download the art for `cards`. Returns what was stored, missed and failed."""
        wanted = self.pending(cards)[:limit]
        if not wanted:
            return {"requested": 0, "stored": 0, "without_image": 0, "failed": 0, "bytes": 0}
        by_id = {card["id"]: card for card in wanted}
        found, unmatched, errors = self.fetcher.resolve(wanted)
        stored = failed = written = 0
        for card_id, url in found.items():
            try:
                written += self.fetcher.download(url, self.path_for(card_id))
                stored += 1
            except (urllib.error.URLError, OSError, TimeoutError) as error:
                failed += 1
                errors.append(f"{card_id}: {type(error).__name__}")
        # Only a card the source answered about is remembered as having no image; a card we
        # never managed to ask about must stay pending, or one bad connection hides it forever.
        self._remember_misses(unmatched)
        return {"requested": len(by_id), "stored": stored, "without_image": len(unmatched),
                "failed": failed, "bytes": written, "errors": errors[:5]}


class _RealFetcher:
    """Talks to Scryfall. Isolated so the tests never reach the network."""

    def resolve(self, cards):
        found, unmatched, errors = {}, [], []
        by_print = {}
        without_print = []
        for card in cards:
            if card.get("set") and card.get("collector_number"):
                by_print.setdefault((card["set"].lower(), str(card["collector_number"])), []).append(card["id"])
            else:
                without_print.append(card["id"])
        keys = list(by_print)
        for start in range(0, len(keys), BATCH):
            chunk = keys[start:start + BATCH]
            payload = {"identifiers": [{"set": code, "collector_number": number}
                                       for code, number in chunk]}
            try:
                answer = _post(f"{API}/cards/collection", payload)
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
                # Report it: a silently skipped batch looks exactly like "no image exists".
                errors.append(f"batch lookup failed: {error}")
                time.sleep(COLLECTION_PAUSE)
                continue
            matched = set()
            for entry in answer.get("data", []):
                key = (str(entry.get("set", "")).lower(), str(entry.get("collector_number", "")))
                url = image_url(entry)
                for card_id in by_print.get(key, []):
                    matched.add(card_id)
                    if url:
                        found[card_id] = url
            for key in chunk:
                for card_id in by_print[key]:
                    if card_id not in matched:
                        without_print.append(card_id)
            time.sleep(COLLECTION_PAUSE)
        # Arena-only printings have no paper collector number; the arena id is the last try.
        for card_id in without_print:
            try:
                entry = _get(f"{API}/cards/arena/{int(card_id)}")
            except urllib.error.HTTPError as error:
                # 404 is the honest answer "this Arena card has no paper printing".
                (unmatched if error.code == 404 else errors).append(
                    card_id if error.code == 404 else f"{card_id}: HTTP {error.code}")
                time.sleep(CARD_PAUSE)
                continue
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
                errors.append(f"{card_id}: {type(error).__name__}")
                time.sleep(CARD_PAUSE)
                continue
            url = image_url(entry)
            if url:
                found[card_id] = url
            else:
                unmatched.append(card_id)
            time.sleep(CARD_PAUSE)
        return found, unmatched, errors

    def download(self, url, destination):
        return _download(url, destination)
