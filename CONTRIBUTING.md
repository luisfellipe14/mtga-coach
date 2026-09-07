# Contributing

This is a personal project someone else can use. That is the whole ambition, and it sets
the bar for what gets merged.

## Two very different kinds of contribution

**Card grades** (`community/set-reviews/`) need no code and no setup. See
[`community/README.md`](community/README.md). One card per pull request is fine.

**Code** follows the rules below.

## The one rule that is not negotiable

**Never state something the log does not support.**

This app runs beside a game people care about, and its whole value is that a number on the
screen is a fact about their games. So:

- If the log does not record it, the app says so instead of estimating it. There is no
  itemised transaction record in `Player.log`, so the wallet screen says "measured from
  balance readings" and refuses to attribute a movement to a specific reward.
- If a table covers 9 of 341 cards, the screen says which 9 rather than ranking the rest.
- If a published figure is for a 60-card deck, it is not scaled to a 40-card one. A limited
  deck falls through to a computed floor, and the screen names which basis it used.
- The app never says what the correct play was. Magic is Turing-complete; no public oracle
  can evaluate a line, and pretending otherwise is the failure mode this project exists to
  avoid.

A pull request that makes a number look more confident than its source will be asked to
make it look less confident.

## Getting set up

```
git clone <this repo>
cd mtga-coach
python run.py
```

Python 3.11+, standard library only. There are no runtime dependencies and adding one
needs a reason in the pull request. The tests:

```
python -m unittest discover -s tests -t .
```

They run without Arena installed and without network access. Keep it that way: anything
that talks to Scryfall or 17Lands goes behind a fetcher object the tests replace.

## What a good pull request looks like

- **A test that fails without your change.** For a bug, the test reproduces it first.
- **Comments that say why, not what.** `# the budget is released on drain because the
  ceiling is about what is held in memory` earns its place. `# increment counter` does not.
- **One thing at a time.** A fix and a refactor in one diff is two reviews.
- **No new dependency**, no telemetry, no phoning home. The app must keep working with the
  network switched off.

## Things that would genuinely help

- **A draft log from a format I cannot test.** Traditional draft and human premier draft
  write a different dialect from the bot draft, and the reader for them is written but not
  validated against a real one. `%LOCALAPPDATA%\mtga-coach\draft-raw.jsonl` holds exactly
  the records needed, key names and card ids only.
- **A Best-of-three match.** The BO3 path is the last one still marked unvalidated.
- **macOS.** The paths in `config.py` are Windows; nothing else should be.
- **Card grades for a new set**, which is where this helps a new player most and costs the
  least to give.

## What this project will not do

- Modify the game, read its memory, or automate any input. It reads a log file the client
  already writes, and nothing else.
- Charge for anything. Wizards' Fan Content Policy forbids it, and the whole point is a
  tool that does not put the beginner on the wrong side of a paywall.
- Collect your data. If a future version ever sends anything anywhere, it will be opt-in,
  it will say exactly what it sends, and the aggregate will be published here under CC0.
