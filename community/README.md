# Open limited data

This directory is a small public good, and it is the reason this repository exists as
more than a place to download an app.

## The problem it exists for

On the day a set releases, nobody has data. The public aggregate everyone relies on —
[17Lands](https://www.17lands.com/) — needs games to have been played before it can say
anything: seven days after *Secrets of Strixhaven* released, it carried a published win
rate for **9 of the set's 341 cards**, and none at all in the premier or traditional
queues. That number is not a criticism of 17Lands. It is arithmetic.

The trackers that do have an opinion on day one have it because they license a set review
from a publisher, or because they collect from a user base large enough to produce data
immediately. Both are perfectly legitimate. Both are also closed: the player who needs the
answer cannot see where it came from, cannot correct it, and cannot take it elsewhere.

## What is here

A card review that anyone can read, correct, fork, or use in a competing app.

```
community/set-reviews/<SET>.json
```

```json
{
  "set": "SOS",
  "updated": "2026-09-07",
  "scale": "0.0 to 5.0, the scale set reviews have used for twenty years",
  "cards": {
    "102481": {"grade": 3.0, "note": "Premium common removal, always maindeck.", "by": "handle"}
  }
}
```

The key is the **Arena card id** (`mtga_id` on 17Lands, `arena_id` on Scryfall), because
it is exact — no name matching, no set mapping, nothing to get subtly wrong when a card is
reprinted or rebalanced.

## The rules of this data

1. **It is free, and it stays free.** The contents of `community/` are released under
   [CC0](https://creativecommons.org/publicdomain/zero/1.0/): no attribution required, no
   permission needed, commercial use allowed. Copy it into your own tracker if it helps.
2. **A grade is an opinion, and it is signed.** Every entry carries who wrote it. The app
   shows grades as opinion, never as measurement, and always below a measured win rate
   when one exists.
3. **Nothing copied from a paid review.** Do not paste grades or prose from a publisher's
   set review, however freely they are displayed inside somebody else's app. Write your
   own or leave the card blank. A blank card is honest; a copied one is a liability for
   everyone downstream.
4. **Measurement replaces opinion the moment it exists.** These grades are for the window
   where no data exists. Once 17Lands covers the set, the app prefers 17Lands and says so.

## How to contribute

Open a pull request against `community/set-reviews/<SET>.json`. One PR can be one card.
Say in the description why you graded it that way — the reasoning is worth more than the
number, and it is what a new player actually reads.

You do not need to be good at limited to help. "This card looked unbeatable and then I
lost with it three times" is a real data point, and it is exactly the kind of thing a
win-rate table takes three weeks to notice.
