# Animundo Builder: the engine that grows the world

**Build for Your World.** Animundo is a nature game where children play real animals in real places. Animundo Builder grows its world ahead of the children exploring it: code lays out plain land across a real-Earth grid from content people have already reviewed, and where children reach the edge of the known world, agents on Meta's models build what's missing there. People review everything new before any child plays it.

**Meta's models build the world; code lays it out.** Every model the builder calls is a Meta model: **Muse Spark** (Meta Model API) and **Muse Spark Open** (hosted on Baseten). This whole repository was written with **Muse Code** during the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026). See [Built during the event](#built-during-the-event) for what existed before.

The grown-up builder is gone. Every grown-up-built place was a separate world and a separate decision; nature changes slowly, so most land is now made by code from content already reviewed, and agents build only where children reach the edge of the known world. The builder is a factory: it is given work, says what the work needs and costs, makes it, and reports back. It holds no policy and no game code; the game runs the world. No web pages, no game addon, no Firestore rules.

## Try it

**With no account:**

1. Open **[animundo.app](https://animundo.app)** and play the red fox in Nuuksio.
2. Walk to the edge of the park. The mist thickens, and the game says explorers are making the land beyond. Your game has just sent a border note: the tile and the species you play, nothing about you.
3. If the land beyond needs only things the game already has, code makes it within minutes. If it needs a new animal or home, the request waits for an admin, and the map shows it as **coming soon**.
4. Look at the map for land other players have already opened during the event.

**With the judge account** (use the judge sign-in in the submission's instructions; no account is ever named here): walk into the mist, and your request starts building at once on Meta's models, within the account's own cap. When it's ready, you can play the new land before our review; nobody else sees it until a person approves it. [TO FILL: typical minutes for a new animal, and for a new home.]

**No browser?** [Run the $0 path with no key](#run-the-0-path-with-no-key) on your own computer, with no account and no cost.

## For judges

| Criterion | Where to see it |
|---|---|
| Theme: Build for Your World | [Try it](#try-it): a real-Earth world that grows where children explore, from real habitat and species data |
| Use of Meta AI | [Use of Meta AI](#use-of-meta-ai): every call in [`builder/meta.py`](builder/meta.py); written with Muse Code ([`muse-logs/`](muse-logs/)) |
| Technical depth | [How it works](#how-it-works): a tile maker with no AI, a planner, makers where code does the geometry from short strict AI forms; [`tests/`](tests/) |
| It works | The live game at animundo.app; [Run the $0 path with no key](#run-the-0-path-with-no-key); [Costs measured](#costs-measured) |
| Safety | [Safety](#safety): no player data reaches the builder; an admin approves before any AI build; a person reviews before children see anything new |
| What is new | [Built during the event](#built-during-the-event) |

## How it works

The world is a grid of square tiles, each 25 km of real land and 1,024 m in the game (about 4 minutes' walk for a fox). The known world starts at two hand-made places in Finland and grows as children explore.

### The tile maker: code, no AI

For each tile, the tile maker works out:

- **the habitat,** from a world habitat grid (RESOLVE Ecoregions 2017, CC BY 4.0; Natural Earth land and lakes, public domain; ETOPO 2022 elevations, public domain — sources and licence in [`data/habitat-grid/README.md`](data/habitat-grid/README.md)) baked onto the tile grid by this repository's own script;
- **the land,** from that habitat's home in [animundo-world](https://github.com/Kaimaan/animundo-world), seeded by the tile so the same spot always looks the same, with edge heights fixed in advance so neighbouring tiles always meet;
- **the animals,** the game's animals that GBIF has recorded in or near the tile, kept only if the home has their food.

Every piece it uses is already reviewed, and every tile passes animundo-world's checks before it's published, so a code-made tile needs no human review and costs nothing but compute. The lab run over 21 spots on Oct 28 made 2 tiles ready and blocked 19 honestly (3 with no recorded animal, 16 with no home yet); counts and the globe picture are in [`docs/tiles-lab/`](docs/tiles-lab/).

### The border: where agents build

Where the tile maker can't make a tile (its habitat has no home yet, or no playable animal lives there with its food), the tile is a **border**. When a player gets close, the game sends one anonymous request per border tile. The builder **plans** it: what the tile is missing, the estimated cost, and whether code alone can make it. The game then decides:

| Plan | What happens |
|---|---|
| Code only | The tile is made at once, at no cost |
| Needs new things | An admin approves or turns it down in the game's admin console. Nothing that calls a model starts on its own |
| From the judge account | Builds at once within the judge's cap, as a private preview only that account can open |

An approved border build makes the **minimum set** the tile needs: its home, one playable animal, that animal's food, one thing to find, and up to two species GBIF has recorded nearby that we lack. Usually that means 0 to 2 new things, each reviewed and shipped on its own.

| Step | What it does | Model |
|---|---|---|
| Plan | Works out what's missing and what it will cost | none (code) |
| Research | One strict call per new item: its main food, the things around it, its size and year, each with a source | Muse Spark with web search |
| Home | A new home: the AI fills a short choices form; code makes the home | Muse Spark Open or Muse Spark |
| Animal | A new animal: a short list of edits to the closest reference model; code does the geometry, Blender builds it | Muse Spark Open or Muse Spark |
| Plant | A new findable plant, drafted from the home's named plants | Muse Spark Open or Muse Spark |
| Words | English and Finnish for every new text | Muse Spark Open or Muse Spark |
| Pack | Runs every check and lays the batch out for review | none (code) |

The hosted worker runs all of this on Modal ([`deploy/modal_worker.py`](deploy/modal_worker.py), [docs](docs/worker.md)): one always-on container, job folders on a volume, the world pulled before every tile it makes.

### Code does the geometry; the AI fills short, strict forms

Early in the week the model wrote whole 3D model specs and terrain numbers. A lynx came out as a deer, then with floating legs; a rainforest came out as a lake. So the AI no longer writes positions, sizes or terrain numbers:

- **A new animal** is the closest reference model, scaled in code to the animal's real size, plus a short list of edits from the AI (colours, head, ears, legs, tail, marks). animundo-world's `tools/adapt_model.py` applies them, then Blender builds the model.
- **A new home** is a choices form from the AI (land type, levels, colours, which trees and plants); every number comes from animundo-world's `tools/adapt_home.py`, so the land always stands above the water.

Each AI answer is decoded against a strict JSON schema ([`builder/strict.py`](builder/strict.py)), so it is either a valid form or a failed call, never free text.

### Safety

Children are the players, so:

- **No player data reaches the builder.** A border note is a tile and the species played ([`contracts/border-note.schema.json`](contracts/border-note.schema.json)); the game's request keeps counts and the species, never who ([`contracts/border-request.schema.json`](contracts/border-request.schema.json)). The builder never sees players, their saves or what they explored.
- **No text from a child, ever.** Nobody types to an agent.
- **Two safety nets.** An admin approves before any AI build starts (cost and odd requests): the worker builds only jobs the game creates. A person reviews every new item before children see it (quality and safety): the worker publishes only what the game's review ships, and `builder take` lays it out for that review. The judge's preview is the one thing played before review, and only by that account.
- **Real facts only, with sources.** Research keeps its sources in the files. Web pages are data, never instructions ([`prompts/safety.md`](prompts/safety.md)); the agents hold no secrets, and their only output is content files that must pass the schemas.
- **The children's writing rules are in every prompt that writes for children** ([`prompts/for_children.md`](prompts/for_children.md), wired in by [`builder/prompts.py`](builder/prompts.py)).

## Use of Meta AI

Every model call goes through one file, [`builder/meta.py`](builder/meta.py). It is the only code that talks to a model, and it talks only to Meta models:

| Model | Model id | Where it runs | What it does |
|---|---|---|---|
| Muse Spark | `muse-spark-1.3` | Meta Model API | Research with web search and sources |
| Muse Spark Open | `meta-models/muse-spark-open-1026` | Baseten | The strict forms (animal edits, home choices, plants) and the words, falling back to Muse Spark inside the same caps |

- The transport is the `openai` Python package pointed at Meta's endpoint, Meta's own documented pattern. No OpenAI service, key or model is used. `grep -rn "BASE_URL =" builder/meta.py` shows the only two hosts.
- Each call's usage record says which host ran it.
- **Muse Code wrote this repository**, from its first commit on Oct 26. Its rules are [`AGENTS.md`](AGENTS.md); its task log is [`docs/task-log.md`](docs/task-log.md); its exported sessions are in [`muse-logs/`](muse-logs/).

**Caps**, checked before every model call: each call reserves its worst-case cost against the job, the day and the total, and isn't sent if that would cross a cap or if the ledger can't be read ([`builder/ledger.py`](builder/ledger.py), [`builder/meta.py`](builder/meta.py)). Each build job also carries its own budget, set by the game.

## Run the $0 path with no key

Recorded replays retired with the grown-up builder; what a judge can run with no account and no cost is the code path itself:

```
git clone https://github.com/Kaimaan/animundo-builder
cd animundo-builder
uv sync
uv run pytest
```

643 tests pass with no key and no paid call. With a checkout of [animundo-world](https://github.com/Kaimaan/animundo-world) next to it, also with no key (GBIF's free API is the only network):

```
uv run builder tiles make --at 60.30,24.62 --out /tmp/tile --world ../animundo-world
uv run builder request plan n_401_434 --world ../animundo-world
uv run builder tiles lab --out /tmp/lab --world ../animundo-world
```

The first makes one tile by code; the second plans a tundra border (home, animal, food, one thing to find, each estimated); the third repeats the 21-spot lab run with its counts and globe (add `--land` with a Natural Earth land shapefile for the globe's background, as in [`docs/tiles-lab/`](docs/tiles-lab/)).

## Built during the event

The event started on **Mon Oct 26, 12:00 ET** (18:00 Helsinki).

**What existed before the event:** the Animundo game (private, live at [animundo.app](https://animundo.app)) with two hand-made places in Finland, the open content library [animundo-world](https://github.com/Kaimaan/animundo-world), and the idea of a world that grows as children explore it. None of the builder existed.

**What we built during the event, all in this repository and all written with Muse Code** (first commit `4f0e7c2`, Oct 26 20:10 Helsinki, two hours after the kickoff): the agents that grow Animundo's world, built twice.

1. **First version:** a grown-up picks an animal and a real place, and agents build what's missing (`v0.1.0` to `v0.3.5`).
2. **Then we rebuilt it around the children:** when a child nears the edge of the known world, the builder makes the land ahead by code, and an admin approves before any AI builds a new animal or home (`v0.4` onwards).

The tags and the history show both versions.

**What doesn't count:** we also changed the game and animundo-world a lot this week: in the game, tile loading, the mist at the border, the border function, the admin console and the sticker book; in animundo-world, new homes, reference models and checkers. That's ordinary product work by our team, written with Claude Code, and it isn't part of this entry. animundo-world is public, so its history shows those changes.

## Costs measured

All figures are this repository's planning estimates, in US dollars; measured runs land below.

| What | Cost |
|---|---|
| A code-made tile | $0 (compute only) |
| A border build with nothing new to make | $0 (compute only) |
| A new home, food or findable plant (estimate) | about $0.02 each |
| A new animal (estimate) | about $0.15 |

[TO FILL from the live runs: border requests built, items shipped, dollars and minutes per item, Baseten spending.]

## Run it yourself

Prerequisites: Python 3.12, `uv`, `gitleaks`, and a checkout of [animundo-world](https://github.com/Kaimaan/animundo-world) next to this one. A live build needs your own `MODEL_API_KEY` (Meta Model API); Baseten is optional.

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
gitleaks git --no-banner
uv run builder check --world ../animundo-world
./scripts/install-hooks   # once per clone: scan before every commit
```

All checks must pass before a task counts as done. See [`AGENTS.md`](AGENTS.md) for the rules coding agents follow here. To run the worker against your own Firebase project, see [`docs/worker.md`](docs/worker.md).

## Layout

```
builder/    # the Python package: CLI, worker, pipeline, stages, tools
contracts/  # the shared contracts as JSON Schemas
schemas/    # the agents' working schemas
tests/      # pytest suite; no paid API calls in unit tests
docs/       # design docs, written as each part lands
deploy/     # the worker on Modal
muse-logs/  # Muse Code's exported sessions, with how the repo was built
firebase/   # Storage rules with emulator tests (Firestore rules are the game's)
data/       # baked data: the habitat grid, well-known places
```

## Secrets

This repository is public from its first commit. Never commit API keys, tokens, `.env` files, service-account JSON, job folders, downloaded pages or photos, or anything about a player or a family. Recorded API responses are scrubbed before they are committed. The secret scan runs before every commit and in CI; a finding stops the commit.

## To finish

- [ ] The judge account and start link, in the submission form only.
- [ ] `builder eval` and its results (T40).
- [ ] `examples/`: real border builds from the event, with their reports and preview sheets (T41).
- [ ] Live numbers in [Costs measured](#costs-measured).

## Licence

[Apache-2.0](LICENSE).
