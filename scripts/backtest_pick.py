"""Measure the card-text heuristic against the thing it stands in for.

The pack ranking has three possible sources — a measured win rate, a signed grade, and the
card's own text — and the third is the one that runs on a set nobody has data for yet. It
is also the only one nobody has ever checked. This script checks it, on sets where both
the heuristic and the measurement exist, by asking the question a drafter actually cares
about:

    if I take the card the heuristic likes instead of the card the data likes,
    how much win rate does that cost me, per pick?

Run: python scripts/backtest_pick.py [SET ...]
It needs the network (17Lands) and the installed Arena card database.
"""

import json
import random
import statistics
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mtga_coach import build  # noqa: E402
from mtga_coach.catalog import resolve_cards  # noqa: E402
from mtga_coach.limited import SOURCE, USER_AGENT  # noqa: E402

# 17Lands' public endpoint serves only what is being played right now: a set whose queue
# has closed answers with every card and game_count zero. So the ground truth available to
# this script is whatever is in rotation, and the sample is as small as that makes it.
DEFAULT_SETS = ("HOB",)
PACK_SIZE = 14
PACKS = 20000
SEED = 20260907
# 17Lands publishes rates on tiny samples too; below this the "truth" is noise as well.
MIN_GAMES = 300


def ratings(expansion, event="PremierDraft"):
    url = f"{SOURCE}?expansion={expansion}&format={event}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        rows = json.load(response)
    return [row for row in rows
            if isinstance(row.get("mtga_id"), int)
            and row.get("ever_drawn_win_rate") is not None
            and (row.get("ever_drawn_game_count") or 0) >= MIN_GAMES]


def spearman(pairs):
    """Rank correlation, computed here so the script needs no dependency."""
    if len(pairs) < 3:
        return None
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
                end += 1
            average = (position + end) / 2 + 1
            for index in range(position, end + 1):
                out[order[index]] = average
            position = end + 1
        return out
    left = ranks([pair[0] for pair in pairs])
    right = ranks([pair[1] for pair in pairs])
    n = len(pairs)
    mean_left, mean_right = sum(left) / n, sum(right) / n
    cov = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    var_left = sum((a - mean_left) ** 2 for a in left) ** 0.5
    var_right = sum((b - mean_right) ** 2 for b in right) ** 0.5
    return cov / (var_left * var_right) if var_left and var_right else None


def study(expansion):
    rows = ratings(expansion)
    if len(rows) < PACK_SIZE * 2:
        return {"set": expansion, "skipped": f"only {len(rows)} cards with a usable rate"}
    cards = resolve_cards([row["mtga_id"] for row in rows])
    scored = []
    for row in rows:
        card = cards.get(row["mtga_id"])
        if not card or not card.get("resolved"):
            continue
        score, _ = build.structural_score(card)
        scored.append({"id": row["mtga_id"], "name": card.get("name"),
                       "structure": score, "truth": row["ever_drawn_win_rate"] * 100})
    if len(scored) < PACK_SIZE * 2:
        return {"set": expansion, "skipped": f"only {len(scored)} cards resolved locally"}

    correlation = spearman([(item["structure"], item["truth"]) for item in scored])
    random.seed(SEED)
    losses, hits, random_losses = [], 0, []
    for _ in range(PACKS):
        pack = random.sample(scored, PACK_SIZE)
        best = max(pack, key=lambda item: item["truth"])
        mine = max(pack, key=lambda item: (item["structure"], -item["truth"]))
        blind = random.choice(pack)
        losses.append(best["truth"] - mine["truth"])
        random_losses.append(best["truth"] - blind["truth"])
        hits += 1 if mine["id"] == best["id"] else 0
    # Picking one card from fourteen and choosing twenty-three from a pool are different
    # tasks: the second only needs to tell a playable from a filler, and it is allowed to be
    # wrong about the order. Measuring them together would hide whichever one works.
    pool_size = min(45, len(scored))
    deck_gain, deck_best = [], []
    for _ in range(PACKS // 10):
        pool = random.sample(scored, pool_size)
        keep = 23 if pool_size >= 30 else pool_size // 2
        by_structure = sorted(pool, key=lambda item: -item["structure"])[:keep]
        by_truth = sorted(pool, key=lambda item: -item["truth"])[:keep]
        average = statistics.mean(item["truth"] for item in pool)
        deck_gain.append(statistics.mean(item["truth"] for item in by_structure) - average)
        deck_best.append(statistics.mean(item["truth"] for item in by_truth) - average)
    return {
        "set": expansion, "cards": len(scored), "spearman": correlation,
        "deck_gain": statistics.mean(deck_gain), "deck_best": statistics.mean(deck_best),
        "deck_recovered": statistics.mean(deck_gain) / statistics.mean(deck_best)
        if statistics.mean(deck_best) else 0.0,
        "top_pick_hit_rate": hits / PACKS,
        "mean_loss": statistics.mean(losses), "median_loss": statistics.median(losses),
        "random_loss": statistics.mean(random_losses),
        "recovered": 1 - statistics.mean(losses) / statistics.mean(random_losses),
    }


def main(argv):
    sets = argv[1:] or list(DEFAULT_SETS)
    print(f"{PACKS} packs of {PACK_SIZE}, seed {SEED}, cards with at least {MIN_GAMES} games\n")
    print(f"{'set':6} {'cards':>5} {'spearman':>9} {'top pick':>9} {'mean loss':>10} "
          f"{'random':>8} {'recovered':>10}")
    results = []
    for expansion in sets:
        try:
            answer = study(expansion)
        except Exception as error:  # a set that 17Lands does not serve is not a crash
            print(f"{expansion:6} failed: {type(error).__name__}: {error}")
            continue
        if answer.get("skipped"):
            print(f"{expansion:6} skipped: {answer['skipped']}")
            continue
        results.append(answer)
        print(f"{answer['set']:6} {answer['cards']:5} {answer['spearman']:9.3f} "
              f"{answer['top_pick_hit_rate']:8.1%} {answer['mean_loss']:9.2f}pp "
              f"{answer['random_loss']:7.2f}pp {answer['recovered']:9.1%}")
        print(f"       building a deck: structure picks a 23 worth {answer['deck_gain']:+.2f}pp "
              f"over the pool average; the measured best 23 is worth {answer['deck_best']:+.2f}pp "
              f"({answer['deck_recovered']:.1%} recovered)")
    if results:
        print(f"\nmean across {len(results)} sets: spearman "
              f"{statistics.mean(r['spearman'] for r in results):.3f}, "
              f"top pick {statistics.mean(r['top_pick_hit_rate'] for r in results):.1%}, "
              f"loss {statistics.mean(r['mean_loss'] for r in results):.2f}pp, "
              f"recovered {statistics.mean(r['recovered'] for r in results):.1%}")
    print("\nmean loss is win-rate points given up per pick against always taking the card "
          "17Lands measured highest. 'random' is the same figure for picking blind, so "
          "'recovered' is the share of the gap between blind and perfect that the card text "
          "closes.")


if __name__ == "__main__":
    main(sys.argv)
