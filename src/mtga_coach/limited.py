"""Public draft statistics from 17Lands, joined to Arena card ids.

17Lands publishes what a card is worth in a set: the win rate of games where it was in
hand (GIH WR), and how late it is still going round (ALSA). Their per-card record carries
`mtga_id`, which is the same identifier the Arena log uses, so the join is exact — no
name matching, no set mapping, nothing to get subtly wrong.

The whole set is fetched in one request and kept on disk, because the numbers move slowly
and a draft is not the moment to be waiting on a network. What this module never does is
turn a number into a verdict: a win rate is what happened across other people's games, and
the deck being drafted is not those games.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "https://www.17lands.com/card_ratings/data"
USER_AGENT = "MTGACoach/0.3 (personal review app)"
FORMATS = ("PremierDraft", "TradDraft", "QuickDraft", "Sealed", "TradSealed")
# The ratings barely move day to day, and every read that hits disk is a read that does
# not hit their servers.
FRESH_FOR_HOURS = 24
CREDIT = ("Draft statistics from 17Lands (17lands.com), collected from players who opted in. "
          "A win rate describes their games, not the deck you are drafting.")


def _now():
    return datetime.now(timezone.utc)


class LimitedRatings:
    def __init__(self, data_dir, fetcher=None):
        self.directory = Path(data_dir) / "limited"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher or _RealFetcher()

    def path_for(self, expansion, event):
        return self.directory / f"{str(expansion).upper()}-{event}.json"

    def stored(self, expansion, event="PremierDraft"):
        try:
            value = json.loads(self.path_for(expansion, event).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and "cards" in value else None

    def is_fresh(self, entry):
        try:
            age = _now() - datetime.fromisoformat(entry["fetched_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return age.total_seconds() < FRESH_FOR_HOURS * 3600

    def ratings(self, expansion, event="PremierDraft"):
        """Ratings by Arena card id, or None when the set was never fetched."""
        entry = self.stored(expansion, event)
        return {int(key): value for key, value in entry["cards"].items()} if entry else None

    def fetch(self, expansion, event="PremierDraft", force=False):
        if event not in FORMATS:
            raise ValueError("unknown limited format")
        entry = self.stored(expansion, event)
        if entry and self.is_fresh(entry) and not force:
            return {"expansion": expansion, "event": event, "cards": len(entry["cards"]),
                    "reused": True, "fetched_at": entry["fetched_at"]}
        rows = self.fetcher.ratings(expansion, event)
        cards = {}
        for row in rows:
            card_id = row.get("mtga_id")
            if not isinstance(card_id, int) or row.get("ever_drawn_win_rate") is None:
                continue
            cards[str(card_id)] = {
                "name": row.get("name"),
                "gih_wr": row.get("ever_drawn_win_rate"),
                "gih_games": row.get("ever_drawn_game_count"),
                "improvement": row.get("drawn_improvement_win_rate"),
                "alsa": row.get("avg_seen"),
                "ata": row.get("avg_pick"),
                "colour": row.get("color"),
            }
        payload = {"expansion": str(expansion).upper(), "event": event,
                   "fetched_at": _now().isoformat(timespec="seconds"), "cards": cards}
        self.path_for(expansion, event).write_text(json.dumps(payload), encoding="utf-8")
        return {"expansion": payload["expansion"], "event": event, "cards": len(cards),
                "reused": False, "fetched_at": payload["fetched_at"]}

    def sets(self):
        entries = []
        for path in sorted(self.directory.glob("*.json")):
            expansion, _, event = path.stem.partition("-")
            stored = self.stored(expansion, event) or {}
            entries.append({"expansion": expansion, "event": event,
                            "cards": len(stored.get("cards", {})),
                            "fetched_at": stored.get("fetched_at"),
                            "fresh": self.is_fresh(stored)})
        return entries

    def rank(self, expansion, card_ids, event="PremierDraft"):
        """Cards ordered by win rate, with the ones the data does not cover named.

        The order is a report of other players' results. It is not a pick: a pick depends
        on what is already in the pool, and this function has never seen the pool.
        """
        table = self.ratings(expansion, event)
        if table is None:
            return None
        rated, unrated = [], []
        for card_id in card_ids:
            row = table.get(int(card_id))
            if row:
                rated.append({"card_id": int(card_id), **row})
            else:
                unrated.append(int(card_id))
        rated.sort(key=lambda item: -(item["gih_wr"] or 0))
        thin = [item["card_id"] for item in rated if (item["gih_games"] or 0) < 500]
        return {"expansion": str(expansion).upper(), "event": event, "rated": rated,
                "unrated": unrated, "thin_sample": thin, "credit": CREDIT}


class _RealFetcher:
    def ratings(self, expansion, event):
        url = f"{SOURCE}?expansion={str(expansion).upper()}&format={event}"
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=40) as response:
            body = json.load(response)
        time.sleep(0.5)
        return body if isinstance(body, list) else []
