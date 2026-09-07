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
- **Reads what the packs were passing.** Of the cards still sitting in a pack by the fifth
  pick, how many were red — more than the packs carried on average? Then red was going
  round. It also names the cards that came back to you a full eight picks later, which is
  the most direct evidence there is that nobody between you and the pack wanted them. This
  is arithmetic on your own packs: no outside data, so it works on a set nobody has
  measured yet.
- **Tells you what each pair of colours is for in the set**, read from the card database
  Arena installed on your machine. Complete on release day, offline, and it makes no claim
  about which cards are good — only what a pair is built around.
- **Reads the draft while you draft it.** The pack on screen is ranked by the 17Lands
  win rate, adjusted for the colours your pool has already paid for, with the reason under
  every card. It then builds the 40 — the pair, the twenty-three, and a mana base for the
  pips those cards actually demand — and shows the colour pairs that lost, with their
  totals, so the choice stays yours. It also keeps every pick with the pack it came from,
  which is the part worth rereading after the draft.
- **Shows the library while you play**: what is left in your own deck, the chance of each
  card on the next draw and within three. It is a panel beside the game, not an overlay on
  it, and it lags the game by the follower's polling interval.
- **Tracks the climb**: rank read from the log the same way the wallet is, so the curve
  starts when the app started looking and never claims to be a full history.
- **Counts the matchups** by the colours the opponent actually showed, with the interval
  next to every rate and the games that showed too little named rather than folded in.
- **Answers whether a draft pays for itself** at the rate you win limited games. The
  break-even rate falls out of the published prize structure alone: 45.7% for Quick Draft,
  40.2% for Premier, 50.0% exactly for Traditional.
- **Lets you grade cards** for a set nobody has data on yet, and exports them as the file a
  pull request to `community/` expects.
- **Keeps your notes and hypotheses** per position and per deck.
- **Optionally fetches card art and card rulings** from Scryfall, and can hand a position to
  an assistant. Everything above works offline with none of that switched on.

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
- **Card rulings (Scryfall).** The official rulings for a card — "you lose 2 life even if the
  target has no nonland cards", "it still enters tapped". Fetched once per card, kept on disk,
  shown in the card inspector, and attached to an AI reading for the cards in that position
  only. The Comprehensive Rules are deliberately *not* bundled: at roughly 225,000 tokens they
  would cost more per question than everything else in the request combined, and they do not
  answer "what does this card do here" — the rulings do.
- **AI reading (Anthropic), optional and not the way in.** The button that costs nothing is
  **Copy the question**: it puts the whole prompt — the sanitised position, the numbers the app
  computed, and the instruction — on your clipboard, for you to paste into Claude, ChatGPT or
  whatever assistant you already use. No account here, no key, nothing sent from this app.
  If you happen to have an Anthropic API key, you can store it and skip the copy-and-paste;
  that is a shortcut, billed to you, at a fraction of a cent per reading. The key is encrypted
  locally and never written to the database or to git.

## Open data, and why it is here

A tracker is only as good as the numbers behind it, and on the day a set releases nobody
has numbers. Seven days after *Secrets of Strixhaven* came out, 17Lands carried a
published win rate for **9 of its 341 cards**. The apps that had an opinion on day one had
it because they license a set review or collect from a user base big enough to produce
data immediately — both fair, both closed.

[`community/`](community/README.md) is the alternative: card grades under CC0, one file
per set, keyed by Arena card id, contributed by pull request and usable by anyone,
including a competing tracker. A grade is shown as an opinion, is signed by whoever wrote
it, and is superseded the moment a measured win rate exists.

Nothing in there is copied from anybody's paid review, and nothing ever will be.

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
- **The draft reader is validated on the bot draft only.** A quick draft has been captured
  and read end to end; the dialect that human premier and traditional drafts write is
  handled by the same shape-based reader but has not met a real one yet. If you draft
  those, `%LOCALAPPDATA%\mtga-coach\draft-raw.jsonl` holds exactly what is needed to
  confirm it — card ids and key names, nothing about your account.
- **A new set has almost no public data.** The pack ranking needs 17Lands, and 17Lands
  needs games to have been played. Until then the deck builder ranks by the cards' own
  text, which can tell a removal spell from a lifegain spell and cannot tell a bomb from a
  trap. It says which of the two it is doing, every time.
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

**It is free, and it has to be.** The Fan Content Policy permits fan projects on a
non-commercial basis only, so there is no paid version to build: no licence, no subscription,
no paywall. Nothing in the app is gated either — the replay, the library tracker, the opponent
list, the deck analysis and the statistics all work offline with no account. The one feature
that can cost money is the in-app AI shortcut, it is off by default, and the same reading is
available free by pasting the copied question into any assistant.

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
