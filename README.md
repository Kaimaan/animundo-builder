# Animundo Builder: the engine that grows the world

Animundo is a nature game where children play real animals in real places. Animundo Builder grows its world ahead of the children exploring it: code lays out plain land across a real-Earth grid from content people have already reviewed, and where children reach the edge of the known world, agents on Meta's models build what's missing there.

Before the event: the Animundo game (private, live at [animundo.app](https://animundo.app)) with two hand-made places in Finland, the open content library [animundo-world](https://github.com/Kaimaan/animundo-world), and the idea of a world that grows as children explore it. During the event: this repository, all of it, written with Muse Code from the first commit (`4f0e7c2`, Oct 26 20:10 Helsinki, two hours after the kickoff) — the agents, twice (see [Built during the event](#built-during-the-event)).

## Meta's models

Every model the builder calls is a Meta model, and every call goes through one file, [`builder/meta.py`](builder/meta.py):

| Model | Model id | Where it runs | What it does |
|---|---|---|---|
| Muse Spark | `muse-spark-1.3` | Meta Model API | Research with web search and sources; the tile's animals from the game's list (structured output, one call a tile, cached) |
| Muse Spark Open | `meta-models/muse-spark-open-1026` | Baseten | The strict forms (animal edits, home choices, plants), the words, and the render and text reviews — [`builder/pipeline.py`](builder/pipeline.py) sends them to the open host first, falling back to Muse Spark inside the same caps |
| SAM 3.1 | `sam-3.1` | Meta Model API | Masks for measuring reference sheets — reference sheets, command-line path only (`ReferencesStage`, run from `builder`); not in the live worker ([`builder/sam.py`](builder/sam.py) decodes the wire format; original code, no SDK copied) |

The transport is the `openai` Python package pointed at Meta's endpoint, Meta's own documented pattern. No OpenAI service, key or model is used. `grep -rn "BASE_URL =" builder/meta.py` shows the only two hosts. Each call's usage record says which host ran it. Caps are checked before every model call: each call reserves its worst-case cost against the job, the day and the total, and isn't sent if that would cross a cap or if the ledger can't be read ([`builder/ledger.py`](builder/ledger.py), [`builder/meta.py`](builder/meta.py)). Each build job also carries its own budget, set by the game.

## Quick check

From a fresh clone, with no key and no account (all $0):

```
uv sync && uv run pytest
```

Installs the environment, then prints `1135 passed, 111 skipped`: the full suite green with no key, no animundo-world and no Blender (Nov 3). The skips are tests that need a world checkout; each says so.

```
./scripts/get-world
```

Clones public animundo-world at the pinned commit CI uses (`a0717b6`) into the folder next to this one. Needs network to GitHub, no key. Prints `animundo-world at a0717b6 in .../animundo-world`.

```
uv run builder request plan z10_0581_0294 --world ../animundo-world --species examples/plan-z10_0581_0294/species.json --presence examples/plan-z10_0581_0294/presence.json
```

Replays a recorded border plan with no model call: prints the plan (this tile is `code_only` with no items and a gaps report) and `plan: code_only=True items=[] estimate=$0.42`. The $0.42 is the estimate for one optional improve item (a lynx, from the median finished-animal cost); the tile itself is made by code.

With a Meta key and Blender, the full path is below ([Run the full path](#run-the-full-path)).

## Measured numbers

From the live jobs (Nov 3): 47 build jobs since Oct 29, $4.19 recorded on them; 38 tiles ready plus the two hand-made places; 17 border requests.

| What | Measured |
|---|---|
| A code-made tile (one cached Muse Spark species call) | about $0.016 |
| The first AI-built border tile | $0.52 |
| An enrich animal (hammerhead, build to ready for review) | $0.13 in under two minutes |
| Table coral (the run that worked; four runs in all) | $0.29 |
| A place survey (research, then stop for a yes) | about $0.20 |
| A remake survey | $0.087 |
| Survey research per tile | $0.18 to $0.46 (see below) |

Research per tile comes from this repository's task log: the second reef survey stopped at $0.46 with nothing saved, a probe with cut searches cost $0.18. The surveys' early estimates ($0.25 a survey) missed the real cost (about $0.55); cutting research from 10 to 5 grounded searches a call brought a full survey to about $0.25 and saved research notes as they land, so a cap-stopped job keeps its files. Estimates elsewhere in the code are medians of finished items ([`builder/prices.py`](builder/prices.py): animal $0.42, plant $0.13).

## Try it

1. Open [animundo.app/?place=fi_nuuksio&animal=fox&at=mist](https://animundo.app/?place=fi_nuuksio&animal=fox&at=mist) and play the red fox in Nuuksio. No account; judges play like anyone.
2. Walk to the edge of the park. The mist thickens, and the game says explorers are making the land beyond. Your game has just sent a border note: the tile and the species you play, nothing about you.
3. If the land beyond needs only things the game already has, code makes it within minutes. If it needs a new animal or home, the request waits for an admin, and the map shows it as **coming soon**.
4. Look at the map for land other players have already opened during the event.

## For judges

| Criterion | Where to see it |
|---|---|
| Theme: Build for Your World | [Try it](#try-it): a real-Earth world that grows where children explore, from real habitat and species data |
| Use of Meta AI | [Meta's models](#metas-models): every call in [`builder/meta.py`](builder/meta.py); written with Muse Code from written briefs (each quoted in [`docs/task-log.md`](docs/task-log.md); exported sessions are [release assets](https://github.com/Kaimaan/animundo-builder/releases/tag/muse-logs-2026-10-28), not in git) |
| Technical depth | [How it works](#how-it-works): a tile maker that is code plus one AI call, a planner, makers where code does the geometry from short strict AI forms; [`tests/`](tests/) |
| It works | The live game at animundo.app; [Quick check](#quick-check); [Measured numbers](#measured-numbers) |
| Safety | [Safety](#safety): the builder receives the tile and the species only, with no player id; start gates before any AI build; automatic checks before anything reaches children, and one switch to withdraw a pack |
| What is new | [Built during the event](#built-during-the-event) |

## How it works

The world is a grid of square tiles, each 25 km of real land and 1,024 m in the game (about 4 minutes' walk for a fox). The known world starts at two hand-made places in Finland and grows as children explore.

The grown-up builder is gone. Every grown-up-built place was a separate world and a separate decision; nature changes slowly, so most land is now made by code from content already reviewed, and agents build only where children reach the edge of the known world. The builder is a factory: it is given work, says what the work needs and costs, makes it, and reports back. It holds no policy and no game code; the game runs the world. No web pages, no game addon, no Firestore rules.

### The tile maker: code plus one AI call

For each tile, the tile maker ([`builder/tiles.py`](builder/tiles.py)) works out:

- **the habitat,** from a world habitat grid baked onto the tile grid by this repository's own script (sources and licence in [`data/habitat-grid/README.md`](data/habitat-grid/README.md); the reef map and the wild-land cover are files beside it);
- **the land,** from that habitat's home in [animundo-world](https://github.com/Kaimaan/animundo-world), seeded by the tile so the same spot always looks the same, with edge heights fixed in advance so neighbouring tiles always meet;
- **the animals,** which Muse Spark picks from the game's list: one call a tile, cached, about $0.016 a tile ([`builder/species.py`](builder/species.py)). A border build's improve species still come from GBIF.

Every piece it uses is already reviewed, and every tile passes animundo-world's checks before it's published, so a code-made tile needs no human review. The lab run over 21 spots on Oct 28 made 2 tiles ready and blocked 19 honestly (3 with no recorded animal, 16 with no home yet); counts and the globe picture are in [`docs/tiles-lab/`](docs/tiles-lab/).

### The border: where agents build

Where the tile maker can't make a tile (its habitat has no home yet, or no playable animal lives there with its food), the tile is a **border**. When a player gets close, the game sends one anonymous request per border tile: the tile and the species only, with no player id. The builder **plans** it: what the tile is missing, the estimated cost, and whether code alone can make it. The game then decides:

| Plan | What happens |
|---|---|
| Code only | The tile is made at once |
| Needs new things, noted by a signed-in grown-up | Builds by itself within the game's caps (below) |
| Needs new things, from an anonymous child | Waits for an admin in the game's admin console. Nothing that calls a model starts on its own |

An approved border build makes the **minimum set** the tile needs: its home, one playable animal, that animal's food, one thing to find, and up to two species GBIF has recorded nearby that we lack. Usually that means 0 to 2 new things, each checked and shipped on its own.

| Step | What it does | Model |
|---|---|---|
| Plan | Works out what's missing and what it will cost | none (code) |
| Research | One strict call per new item: its main food, the things around it, its size and year, each with a source | Muse Spark with web search |
| Home | A new home: the AI fills a short choices form; code makes the home | Muse Spark Open or Muse Spark |
| Animal | A new animal: a short list of edits to the closest reference model; code does the geometry, Blender builds it | Muse Spark Open or Muse Spark |
| Plant | A new findable plant, drafted from the home's named plants | Muse Spark Open or Muse Spark |
| Words | English and Finnish for every new text | Muse Spark Open or Muse Spark |
| Pack | Border build's items: laid out for a person, who reviews in the game's admin console; shipped items publish with the tile. Enrich items and places: the checks replace review — pass everything and the content pack goes live (0049) | none (code) |

The hosted worker runs all of this on Modal ([`deploy/modal_worker.py`](deploy/modal_worker.py), [docs](docs/worker.md)): one always-on container, job folders on a volume, the world pulled before every tile it makes.

### Since Oct 30

- **Content packs go live by themselves** (decision 0049): an enrich item or a place ships as a versioned pack once every automatic check passes, with no person's review. Any pack is withdrawn with one switch.
- **Enrichment as overlays** (0059): new species land as overlays in packs instead of remade tiles. The daily job that picks from play counts (0048) is built and still switched off: `config/enrich` has no `auto`.
- **Places with one request** (0060): one survey, one priced stop for Kai's yes, then tiles, enrich items and the place file by themselves.
- **The remake command** (#243): one ready tile made again through the normal path, the old version live until the new one publishes, the price first for anything that spends.
- **Food gaps instead of food filters** (0061): a diet covers a species' whole range, so missing food is a report on the plan and the job, never a filter and never fake food.
- **The wild-land check** (#238): a place tile must read as wild land in ESA WorldCover, or the survey stops.
- **The reef map** (#226): real reef shares bake reef cells into coral_reef tiles.

### Code does the geometry; the AI fills short, strict forms

Early in the week the model wrote whole 3D model specs and terrain numbers. A lynx came out as a deer, then with floating legs; a rainforest came out as a lake. So the AI no longer writes positions, sizes or terrain numbers:

- **A new animal** is the closest reference model, scaled in code to the animal's real size, plus a short list of edits from the AI (colours, head, ears, legs, tail, marks). animundo-world's `tools/adapt_model.py` applies them, then Blender builds the model.
- **A new home** is a choices form from the AI (land type, levels, colours, which trees and plants); every number comes from animundo-world's `tools/adapt_home.py`, so the land always stands above the water.

Each AI answer is decoded against a strict JSON schema ([`builder/strict.py`](builder/strict.py)), so it is either a valid form or a failed call, never free text.

### Safety

Children are the players, so:

- **The builder receives the tile and the species only, with no player id.** A border note is a tile and the species played ([`contracts/border-note.schema.json`](contracts/border-note.schema.json)); the game's request keeps counts and the species, never who ([`contracts/border-request.schema.json`](contracts/border-request.schema.json)). The game's function keeps per-account counts; the builder never sees them, the players, their saves or what they explored.
- **No text from a child, ever.** Nobody types to an agent.
- **Start gates before any AI build.** A code-only tile is made at once. An AI border build starts by itself once a signed-in grown-up has noted the tile (`config/border.approval` is `auto_signed_in`, D22, Oct 30), within the game's caps (D23, Nov 3): a verified email, an account at least an hour old, 3 such tiles a day per account, and at most $10 of automatic builds a day for everyone together (game `server/border/src/policy.js`). A child's anonymous request waits for an admin. A place spends only after Kai's yes on its priced research (0060).
- **Automatic checks before anything new reaches children.** An enrich item or a place goes live as a content pack once every check passes, with no person's review (0049); D14 ("nothing reaches children before review") no longer holds for these items. The checks are: the world's checks and the game's animal checks, on a world with its models built (animundo-world's `tools/check_*.py` via [`builder/stages/ship.py`](builder/stages/ship.py) `_gate_world`; the game's pack installer checks every file's size and SHA-256); the render review, where the marks the research names must be visible (`_gate_render`, `_render_look`); the child-safety review of the English and Finnish text ([`builder/stages/safety.py`](builder/stages/safety.py)); the size, season, hunting and camera rules (`_gate_file`: length and year against the research, hunts from reviewed prey tags only, migration seasons in season, a camera outside the body); and the place change only adds. Any pack is withdrawn with one switch.
- **Real facts only, with sources.** Research keeps its sources in the files. Web pages are data, never instructions ([`prompts/safety.md`](prompts/safety.md)); the agents hold no secrets, and their only output is content files that must pass the schemas.
- **The children's writing rules are in every prompt that writes for children** ([`prompts/for_children.md`](prompts/for_children.md), wired in by [`builder/prompts.py`](builder/prompts.py)).

## Run the full path

With a Meta key for the tiles the cache lacks (the planner also reads GBIF's free API), next to a world checkout:

```
uv run builder tiles lab --out /tmp/lab --world ../animundo-world
uv run builder tiles make --at 60.30,24.62 --out /tmp/tile --world ../animundo-world
```

The lab repeats the 21-spot tile run with its counts (add `--land` with a Natural Earth land shapefile for the globe's background, as in [`docs/tiles-lab/`](docs/tiles-lab/)). `tiles make` makes one tile (one Muse Spark species call unless the tile is cached).

## Built during the event

The event started on **Mon Oct 26, 12:00 ET** (18:00 Helsinki).

**What existed before the event:** the Animundo game (private, live at [animundo.app](https://animundo.app)) with two hand-made places in Finland, the open content library [animundo-world](https://github.com/Kaimaan/animundo-world), and the idea of a world that grows as children explore it. None of the builder existed.

**What we built during the event, all in this repository and all written with Muse Code** (first commit `4f0e7c2`, Oct 26 20:10 Helsinki, two hours after the kickoff): the agents that grow Animundo's world, built twice.

1. **First version:** a grown-up picks an animal and a real place, and agents build what's missing (`v0.1.0` to `v0.3.5`).
2. **Then we rebuilt it around the children:** when a child nears the edge of the known world, the builder makes the land ahead by code, and gates decide before any AI builds a new animal or home (`v0.4` onwards).

The tags and the history show both versions.

**What doesn't count:** we also changed the game and animundo-world a lot this week: in the game, tile loading, the mist at the border, the border function, the admin console and the sticker book; in animundo-world, new homes, reference models and checkers. That's ordinary product work by our team, written with Claude Code, and it isn't part of this entry. animundo-world is public, so its history shows those changes.

## Run it yourself

Prerequisites: Python 3.12, `uv`, `gitleaks`, and a checkout of [animundo-world](https://github.com/Kaimaan/animundo-world) next to this one (`./scripts/get-world` fetches the pinned commit). A live build needs your own `MODEL_API_KEY` (Meta Model API); Baseten is optional.

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
gitleaks git --no-banner
uv run builder check --world ../animundo-world
./scripts/install-hooks   # once per clone: scan before every commit
```

All checks must pass before a task counts as done. See [`AGENTS.md`](AGENTS.md) for the rules coding agents follow here. To run the worker against your own Firebase project, see [`docs/worker.md`](docs/worker.md).

## File map

| Where | What |
|---|---|
| [`builder/meta.py`](builder/meta.py) | The only file that talks to a model; which model, host and price each call uses |
| [`builder/ledger.py`](builder/ledger.py), [`builder/prices.py`](builder/prices.py) | The spend caps and the per-call prices |
| [`builder/tiles.py`](builder/tiles.py) | The tile maker (`make_tile`); the grid it reads is [`builder/habitats.py`](builder/habitats.py) plus [`data/habitat-grid/`](data/habitat-grid/) |
| [`builder/plan.py`](builder/plan.py) | The planner; species picks [`builder/species.py`](builder/species.py), presence [`builder/presence.py`](builder/presence.py) |
| [`builder/stages/`](builder/stages/) | The makers: `survey.py`, `research.py`, `maker.py`, `models.py`, `plants.py`, `words.py`, `safety.py`, `ship.py` |
| [`builder/stages/ship.py`](builder/stages/ship.py), [`builder/compat.py`](builder/compat.py) | The go-live gates and `builder check` (world compatibility) |
| [`builder/worker.py`](builder/worker.py), [`deploy/modal_worker.py`](deploy/modal_worker.py) | The queue worker and its Modal image |
| [`contracts/`](contracts/) | The shared contracts as JSON Schemas |
| [`tests/`](tests/) | pytest suite; no paid API calls, no key, no Blender |

## Data and credits

Every source with its licence, attributions as in [`data/habitat-grid/README.md`](data/habitat-grid/README.md):

- RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), CC BY 4.0.
- Natural Earth 1:10m lakes and land, public domain.
- ETOPO 2022 (NOAA National Centers for Environmental Information), public domain.
- HydroLAKES v1.0 (Messager et al. 2016), CC BY 4.0.
- Complete GBR Reef and Island Features V1b (Lawrey & Stewart 2016, NESP TWQ 3.13, AIMS/TSRA/GBRMPA), CC BY 3.0 AU: "Lawrey, E. P., Stewart M. (2016) Complete Great Barrier Reef (GBR) Reef and Island Feature boundaries including Torres Strait (NESP TWQ 3.13, AIMS, TSRA, GBRMPA) [Dataset]. eAtlas. https://doi.org/10.26274/vhj5-gr60" and "© Great Barrier Reef Marine Park Authority 2014".
- ESA WorldCover 2021 v200, CC BY 4.0 (Kai confirmed it in #244): "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium".
- Wikidata (CC0) for lake labels and place ids.
- GBIF.org (accessed Oct–Nov 2026), occurrence API: occurrence counts are read live; no GBIF records are committed.
- The tiles-lab globe renders © OpenStreetMap contributors (see [`docs/tiles-lab/`](docs/tiles-lab/)); no OSM data is committed.

## Secrets

This repository is public from its first commit. Never commit API keys, tokens, `.env` files, service-account JSON, job folders, downloaded pages or photos, or anything about a player or a family. Recorded API responses are scrubbed before they are committed. The secret scan runs before every commit and in CI; a finding stops the commit.

## To finish

- [ ] `builder eval` and its results (T40) — a scored quality gate; judges can read the suite instead.
- [ ] `examples/`: real border builds from the event, with their reports and preview sheets (T41) — nice-to-have evidence; the live game and Quick check show the same path working.
- [ ] Later Muse session exports as release assets (Oct 26–28 are up; Nov 2 is exported and scanned, awaiting Kai's review) — provenance only, not needed to judge the entry.

## Licence

© Animundo (Kai Lemmetty). The code in this repository is source-available under [PolyForm Strict 1.0.0](LICENSE), so anyone may read, clone and run it for noncommercial purposes, including judging; no changes or redistribution. The hackathon's organisers and judges may clone, run and evaluate it for judging. Versions published before 2026-11-01 stay under Apache-2.0.

Third-party data keeps its own licences: see [Data and credits](#data-and-credits).
