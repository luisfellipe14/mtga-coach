# MTGA Coach

A local app for reviewing your own Magic: The Gathering Arena games and tuning your decks,
built on the log the client already writes on your machine. It runs entirely on
`127.0.0.1`, on the Python standard library, with no account anywhere.

## What it does

- **Follows `Player.log` while you play** and records each game as it progresses. This is not
  a convenience: Arena **wipes `Player.log` every time the client starts**. Without the
  follower running, the session you just played is gone when you restart the game.
- **Reconstructs every game** turn by turn — known hand, battlefield, stack, life, actions —
  and marks explicitly where the reconstruction has a gap.
- **Builds the timeline** of what happened (draws, lands, spells, damage, reveals) from the
  protocol annotations, not just from the state snapshots.
- **Counts your library** at any instant of the game and gives the odds for the next draw and
  the three after it.
- **Lists what the opponent showed** — cards and colours. It names no archetype: this is what
  was seen, not their deck.
- **Analyses the deck**: curve, mana base against Karsten's published source table, wildcard
  cost, and win rate per card in hand with the confidence interval beside it.
- **Imports and exports decklists** in Arena's own format, so a list goes straight back into
  the client, and any list you paste gets the same analysis before you build it.
- **Keeps your notes and hypotheses** per position and per deck.
- **Optionally fetches card art** from Scryfall and **optionally asks Claude** to read a
  position. Both are off until you switch them on.

What it does **not** do: it never tells you what the correct play was, never assigns win
probabilities to alternative lines. Nothing public can evaluate a Magic line with any
guarantee — the game is Turing-complete, so no oracle exists. The app hands you a sanitised
context and the numbers; the reading is yours.

## Install

**One thing is required either way: detailed logs in Arena.** Gear icon → *Adjust Options*
→ *View Account* → tick **Detailed Logs (Plugin Support)** → restart the client. Without it
the log holds no protocol records and no tracker of any kind can read your games. The
Statistics screen tells you whether they are on.

**If you just want to use it.** Download `MTGA Coach.exe`, put it anywhere, double-click.
It opens your browser at `http://127.0.0.1:18731`. No installer, no Python, no account. Keep
the small console window open while you play; closing it stops the app.

**If you want to run from source.** Python 3.14 on the PATH, then:

```
python run.py                                   # http://127.0.0.1:18731
python run.py --import-current                  # import the configured log on start-up
python run.py --smoke                           # quick server check
```

Everything but the AI reading runs on the standard library alone. For that one feature:
`python -m pip install anthropic` and your own Anthropic API key.

The server binds to `127.0.0.1` only; any other address is refused at start-up.

**Building the executable yourself:**

```
python -m pip install pyinstaller
python -m PyInstaller --clean --noconfirm mtga-coach.spec   # → dist/MTGA Coach.exe
```

## How to use it

1. Open the app **before** you play and click **Follow matches**. Leave the tab open.
2. Play. Games appear in the list as they progress.
3. Open a game, step through the frames (← → arrows), and use the sidebar tabs:
   *Decision*, *Library*, *Opponent*, *Timeline*, *AI reading*.
4. Write the note you want to revisit. It comes back under **Training**.
5. Under **Decks**, link the observed composition to the deck saved in Arena (the log never
   does that for you), read the mana base and the wildcard cost, and copy the list back out.
6. Under **Statistics**, see the rate by mode, by who went first, and the size of the sample.
7. Under **Settings**, switch on card art, store your API key, or analyse a pasted list.

**Import logs** reads the whole current `Player.log` in chunks, without loading it into
memory. **Upload log** takes a file you pick, up to 64 MB. Re-importing the same file
duplicates nothing: identity is the SHA-256 of the content.

## Where the data lives

Everything under `%LOCALAPPDATA%/mtga-coach/`:

- `reviews.sqlite3` — games, compressed frames, events, notes and hypotheses.
- `art/` — card art you chose to download.
- `sources/` — copies of any log you uploaded by hand.
- `anthropic.key` — your API key, encrypted with Windows DPAPI for your account only.

Account and match identifiers are stored **hashed**; the opponent's name is kept because it
appears on screen. Nothing leaves this machine except the two opt-in features below.

Frames are stored as compressed blocks: a 19-turn game takes about 2% of its raw JSON. On the
test sample, eight games went from 25.5 MB to under 1 MB.

## The two things that use the network

Both start switched off, and both are per-feature toggles in **Settings**.

- **Card art (Scryfall).** Sends a set code and a collector number, nothing else. Each image
  is downloaded once and served from disk afterwards. Card names and rules text keep coming
  from the Arena database installed on your PC.
- **AI reading (Anthropic).** Sends the sanitised position — only what you knew at that
  instant — plus the numbers the app computed. The model is instructed not to recompute them,
  not to invent card text, and never to call a play correct. Your key is encrypted locally and
  never written to the database or to git.

## Known limits

- **BO3 has not been checked against a real match.** The data model separates game from match
  from the start, and there are synthetic tests, but no real BO3 capture has gone through the
  app yet. Until it has, BO3 is unvalidated code.
- **The collection is not in the log.** Arena stopped publishing which cards you own, so the
  wildcard cost is the cost of the whole list, not what you are missing.
- **The log never links the list played to the deck saved in Arena.** You make that link once
  per composition and it is remembered.
- **Best-of-one opening hands are not random.** Wizards states that BO1 draws the opening hand
  from separately shuffled copies of the deck, leaning toward the average land ratio, without
  publishing the weighting. Comparing a measured BO1 opening-hand rate to the hypergeometric
  baseline is wrong, and the app says so where it shows the number.
- **A personal sample is small.** Telling 55% from 50% at 95% confidence and 80% power takes
  about 1,565 games per arm. The app shows the Wilson interval next to every rate precisely so
  that 20 games never look like a verdict.

## Method sources

- Frank Karsten, *How Many Sources Do You Need to Consistently Cast Your Spells? A 2022
  Update* (TCGplayer) — colour source table, 60-card deck, 90% consistency, London mulligan
  modelled.
- Frank Karsten, *How Many Lands Do You Need in Your Deck? An Updated Analysis* (TCGplayer) —
  the regression `19.59 + 1.90 × average mana value`.
- Wizards of the Coast (Oct 2018, clarified May 2019) — the statement on BO1 opening hands.
- Wilson score interval and the two-proportion test: standard statistics, computed locally.
- Card art and printing data: [Scryfall](https://scryfall.com). Card names and rules text:
  the Arena client's own database.

## Privacy

Worth stating plainly, since this reads a game log.

- **Nothing is uploaded.** There is no account, no telemetry, no crash reporting, no
  analytics. The two network features are opt-in, off by default, and described above:
  Scryfall receives a set code and a collector number; Anthropic receives a sanitised
  position, and only when you press the button.
- **Your account id and match ids are stored hashed.** The opponent's screen name is stored
  in plain text, because it is shown on screen — it is the same name Arena displays to you.
- **The key never leaves the machine** and is encrypted for your Windows account.
- **`%LOCALAPPDATA%/mtga-coach/sources/` holds any log you uploaded by hand.** A raw Arena
  log contains account identifiers, so treat that folder as private and do not share it. The
  database itself does not carry raw log text.
- Delete `%LOCALAPPDATA%/mtga-coach/` and everything the app knows is gone.

## Sharing it

The licence is MIT, so you may copy, modify and redistribute the software freely. Two limits
are worth knowing before you post it anywhere:

- **Non-commercial.** Wizards' Fan Content Policy permits fan projects only on a
  non-commercial basis. Give it away; do not sell it or put it behind a paywall.
- **Ship no card data.** The app deliberately reads card names and rules text from the Arena
  client the user already has, and fetches art from Scryfall only at the user's request. Keep
  it that way: bundling Wizards' card data would turn a fan project into a redistribution
  problem, and Scryfall's terms forbid repackaging their data as such.

If you report a bug, the useful details are: the version from `/api/health`, whether detailed
logs were on, and whether the game was BO1 or BO3. Never paste a raw `Player.log` into a
public thread — it identifies your account.

## Where this stands with Wizards

The app reads a local file that Wizards itself created for third-party plugins, with the
option enabled by the user, and does nothing else: no memory injection, no network
interception, no automated play, no data sent anywhere. Personal, non-commercial use, in line
with the Fan Content Policy. Not an official product and not endorsed by Wizards of the Coast.

## Development

```
python -m unittest discover -s tests -t .      # 82 tests
node tests/test_ui.mjs                         # pure view-model functions
python -m compileall -q src
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/launch_mtga_coach.ps1 -SmokeTest
```

The interface and every user-facing string are in English. Card text follows the Arena
client's own localisation tables — English by default, switchable in `catalog.LANGUAGE`.

Specification, plan and the state-of-the-art comparison live under `docs/`. Those are
internal working notes and are written in Portuguese; everything the product surfaces —
interface, API messages, exported context, AI prompts — is English.
