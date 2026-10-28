# Task log

What ran for each task, and where the evidence is. Newest first.
Job folders live under `jobs/` (local only, never committed).

## 2026-10-28: inbox #68 (Builder mode never boots in export, v0.2.1)

- #68.1: the boot gate used `FileAccess.file_exists` on
  `res://scripts/builder_hooks.gd`, false in an exported game (the
  .pck holds .gdc, never .gd). Now `ResourceLoader.exists`, which
  follows the remap — in `builder_mode.gd` and the same gate in
  the three headless checks. Every other `FileAccess` use is on
  `user://` paths or JSON reads, both fine in the .pck.
- #68.2: a source-rule test (`tests/test_addon_source.py`) fails on
  any `FileAccess.file_exists` on a `.gd` path; verified it fails
  on the pre-fix tree.
- #68.3: the `tests/` exclusion belongs to the game's copy script
  and export preset (both live outside this repo), so it's one
  line for the reviewer, not a change here.
- Moved `v0.2.1` onto this commit: the item defines it as #67 plus
  this fix, and nothing had consumed the tag yet (the fox test
  runs on v0.2.0; Kai copies v0.2.1 afterwards).
- Engine evidence (Godot 4.7.2 headless, this repo's addon): the
  three checks pass 0-errors in a bare project (CI recipe) and in
  the game project; a one-shot driver in the game project shows
  the gate true, `booted()` true, both buttons registered and the
  strings folder installed, with no errors. Game checkout
  restored clean afterwards.
- Suite: 575 passed, 12 skipped; ruff check + format clean.

## 2026-10-28: inbox #67 (#66.1's second wording, tag v0.2.1)

- #67.1: the hunter shows up in two checks with two wordings, so no
  more string matching. `_check_animal` runs the checks on a copy of
  the region with the still-unmade targets taken out of `animals.*`
  and `fish` (`_pruned_regions`, overlaid through `check_draft`'s
  `extra`, so the job itself never changes and nothing needs
  putting back). `_excused_missing` is gone.
- Verified on the live data first: the reviewer's
  `jobs/anaconda-v020` region plus its capybara file, cloned to
  /tmp — unpruned reproduces both live wordings, pruned with
  {"anaconda"} yields zero errors.
- #67.2: the test runs the real world checks, not a stub. A fixture
  world carries the real `check_draft.py`, `check_animals.py`,
  `validate_region.py`, `check_taxa.py` and `check_models.py` (plus
  the real archetypes, the shipped Nuuksio recast to seat hunter,
  prey and a hunter-kind fish, and a shipped .glb's bytes for the
  built-models gate); it skips without the sibling checkout. The
  full prey-then-hunter run passes with zero fix rounds, the
  unpruned region fails all three wordings, and the job region
  keeps its full cast.
- Tagged `v0.2.1` on green replays.
- Suite: 574 passed, 12 skipped; ruff check + format clean.

## 2026-10-27: inbox #66 (anaconda live set: two bugs, tag v0.2.0)

- #66.1: `oneshot` failed the prey because the region already named
  the unmade hunter. While making the minimum set, the checks now
  excuse exactly the target animals not yet made (`_excused_missing`
  matches the world's `has no file animals/<id>.json` wording);
  anything else about them still fails. No more wasted fix round.
- #66.2: the `home` stage drops the home-only fields (`based_on`,
  `about`) when turning a made home into a region, as the world's
  `homes.py make` does with `HOME_ONLY`. Calling `make` itself
  would need the made home inside the world's `homes/`, so the
  two-key drop stays a copy with a pointer at the tool.
- Tagged `v0.2.0` (D7, D8, strict research, looks stage) on green
  replays, for Kai's production fox test.
- Suite: 574 passed, 12 skipped; ruff check + format clean.

## 2026-10-27: inbox #65 (decision D8: homes work like animals)

- #65.1: the free-form `home_write` call is out; the AI fills a strict
  choices form instead. `prompts/home_choices.md` and
  `_home_choices_schema()` are the lab's `PROMPT` and `schema()`
  verbatim (template-ized): surroundings plus looks-stage plant looks,
  the animal's foods, the closest home's description/palette/trees/lake
  life, the library plant ids as an enum. Verified: the rendered
  rainforest prompt diffs clean against the lab's (only the real animal
  name and exact center differ — the lab fell back to "animal" and the
  rounded cell), and the schema is JSON-identical to the lab's.
- #65.2: code adds `id`, `tropical` (|lat| < 23) and `sources`
  (research's plus GBIF.org), then runs the world's
  `tools/adapt_home.py --check --out`. Its ERROR/FAIL lines feed the
  fix round, which changes the choices with #59's memory. Tries keep
  answer/choices/home/errors; only a passing try lands in `homes/`.
- #65.3: closest home is `boreal_lake` for lake and sea habitats,
  `boreal_forest` otherwise.
- #65.4: no new plant specs; looks-stage plants go into the report
  (HTML and plain text) as "Plants to make later".
- #65.5: place name without country already landed in #64.4; dry
  middle comes from the adapt tool itself.
- Removed with the old maker: `home_write.md`, the carryover/taxa/
  terrain/sources/tree-spec machinery, the sandbox plant builds and
  both sandbox check readers (~700 lines out).
- Replay: `anaconda/newhome.json` is now the lab's real rainforest
  choices (the AI's part); `newhome-made.json` stands in for the
  adapt tool's output downstream. Synced `web/build/animal-list.json`
  (emperor penguin reference).
- Suite: 570 passed, 12 skipped; ruff check + format clean.

## 2026-10-27: inbox #64 (made rainforest home, five fixes)

- #64.1: land stays out of the water. Prompt rule plus `_terrain_errors`
  in code: `base_height` at least `water_level` + 3 (default water 8, so
  at least 11). Checked against the game (`world.gd height()`: land runs
  from base up by relief), so the floor keeps the lowest land dry; only
  present numbers are checked, missing ones stay the world's check's
  business. No relief bound: relief only raises land, and flat places
  (relief 0-4) are legitimate per CONTRIBUTING.
- #64.2: prompt rule — `terrain.edge` is `lake` only for lake and sea
  shores and must be set (leaving it out rings the map too); a river
  home is `edge: "none"` with water from `lakes`/`streams` (both in the
  region schema and the game).
- #64.3: the maker finishes only when the world's checks pass. The
  `homes.py check` on the made home (copied into the sandbox's `homes/`)
  already ran — try 1 of the live rainforest shows its errors — but a
  failed try left its home behind and the next run took it as done.
  Now the early return needs `checks/newhome.json` too, so a failure
  retries instead of standing. Code carries the research's sources into
  the template (the writer leaves `sources` out); prompt asks for
  one-for-one `flower_names`, the world's check gates the rest.
- #64.4: `place_name` is just the place ("Fox forest"); the game adds
  the country itself. Intake writes it so, and home strips a trailing
  ", Country" for old briefs (both the `homes.py make` and made-template
  paths), so the fox's and anaconda's places fix on re-run.
- #64.5: the `models/examples/trees/` fallback is out — no library tree
  specs exist, so nothing is offered and every tree uses a built-in
  shape. The shipped-spec machinery stays for when the library lands.
- Fixture: the recorded anaconda home answer carried the template's
  `base_height` 9 (flooded under the new rule); bumped to 11, the rest
  untouched. Synced `web/build/animal-list.json` (new reef_shark
  reference).
- Suite: 575 passed, 12 skipped; ruff check + format clean.

## 2026-10-27: inbox #63 (lab-tried edits call)

- #63.1: the edits call no longer names marks — `marks` left the schema
  and prompt; `_tool_marks()` maps the research's own words to
  ear_tufts/ruff/mane/tail_tip/tail_tuft, and words with no tool mark
  (plus `unmakable` from the call) go to `checks/oneshot.json`, never
  back to the lab. Code marks land in `edits.json` for the adapt tool.
- #63.2: the edits call sees the reference as the lab sees it — a looks
  line (`_reference_looks()` reads the Looks column of the world's
  `models/README.md`, with fox/vole/moose lines in code until the column
  lands) plus the table line.
- #63.3: `ThingLooks` gained shoulder/height/tail cm with sources;
  `_looks_sizes()` feeds the main animal and, behind `$PREY_LOOKS`, the
  prey edits, in centimetres as found.
- #63.4: no tail in the research (or under 3 cm) sends `tail_m 0`, never
  a √2 guess; the adapt tool removes it.
- Synced `web/build/animal-list.json` with the world (new brown_bear,
  common_crane, horse references; ostrich/emu now makable).
- Suite: 565 passed, 12 skipped; ruff check + format clean.

## 2026-10-27: inbox #58.5 (looks call) and #62 (D7 live fixes)

- #58.5: a new `looks` stage after `minimum`: one short
  strict call (3 searches at most, `text.format` with the
  `ThingLooks` schema) for the prey and for every
  plant-kind surrounding the game has no spec for. Each
  returns latin, size, colours, marks or shape notes, the
  year for plants, and sources; library items get no call.
  The stage is idempotent (skips when `looks.json`
  exists). The prey's looks feed the edits prompt
  (colours, marks); the plants' feed `$PLANT_LOOKS` in the
  home prompt. Makers tolerate a missing `looks.json`
  (jobs already in flight).
- #62.1: the research asks for the main colour a child
  would name, as a field guide's plate shows it — not the
  most striking variant (the lynx read fox-red).
- #62.2: the edits prompt leans towards the marks: telling
  differences read clear at game distance (1.4–1.6, not
  1.2); small factors keep the reference's look.
- Research recordings regenerated for the new prompt
  (answers untouched).
- Reviewer: `builder stage looks --job <job> --record`
  between `minimum` and `newhome` on the live jobs; then
  the capybara live. Recordings from the lynx and the
  capybara become the replay tests next.
- Suite: 561 passed, 12 skipped.

## 2026-10-27: inbox #60 (D7): the maker edits references instead of writing specs

- Two calls per animal now: `animal_write` for the animal
  file, then a strict edits call (colours, 7 factors, marks,
  repaints, unmakable) whose schema is built per call from
  the reference spec's own colour and part names. Code adds
  the sourced sizes and runs the world's
  `tools/adapt_model.py`; the 70k-character rules and the
  hand-written spec are gone (`model_spec.md` stays only
  for kinds with no `start_from`, which the list doesn't
  offer). Prey goes through the same tool.
- The edits call sees only what it needs: the looks, the
  reference-table line, and the part list (name, colour,
  bone role). The fix round revises the edits, with the
  previous answer as an assistant message (#59).
- Code owns the sizes: when only length, shoulder or tail
  miss after a clean build, code rescales and re-runs adapt
  without spending a model call; if the tool saturates
  (legs clamp at 1.8x), the run stops with the exact
  numbers for a person. Verified against the real tool: an
  adapted lynx measures shoulder 0.60 m and tail 0.151 m
  for 0.60/0.15 sourced — the measure check now uses the
  tool's own definitions (back-top shoulders, tail-part
  span), and the old live lynx spec still flags its tail
  (22 cm vs 15).
- Ordering fix the split surfaced: the animals verdict
  needs the built `.glb`, so the animal phase filters
  "model file not found" (the model phase resolves it);
  every other verdict error stays the file's own fault.
- #60.5: marks no edit can make ride `unmakable` into
  `checks/oneshot.json` and the build report ("Marks the
  tool can't make yet", plus a line in the PR text) for the
  reviewer to add to the tool.
- #60.6: replay stand-ins split into animal + edits
  answers. Reviewer, after the #58.4 research test: run the
  lynx live (`builder stage oneshot --job jobs/review-lynx
  --record --world ../animundo-world`), and the same
  `oneshot` on the anaconda job for the capybara (prey runs
  first; the run then stops on the anaconda's missing
  `grass_snake` files — expected, the `examples/`
  resolution is still open). Their real answers then
  replace the stand-ins as the replay test.
- Suite: 554 passed, 12 skipped.

## 2026-10-27: inbox #58 part 1: strict research, tree specs (looks call waits on #58.4)

- #58.1–3: one Pydantic model per builder schema
  (`builder/strict.py`: `Research`, `ThingLooks`), flattened
  to the strict subset (plain object root,
  `additionalProperties: false` everywhere, `required`
  naming every key, no `allOf`/`oneOf`, `$ref`s inlined,
  flat food-kind enum with `prey`); chat keeps
  `response_format`, Responses gains `text_format`
  (`MetaClient.responses_create`). Tests assert the subset
  on both schemas.
- Research now sends `text.format` strict with `web_search`
  and strips nulls back to the file shape (loose schema
  still judges). Recordings regenerated (answers untouched).
- #58.4 live test, reviewer runs (~$0.06, ask Kai first):
  `builder stage research --job jobs/review-anaconda
  --record --world ../animundo-world`. Report: does the
  call hold (no 400), and does `research.json` carry looks
  plus every field? If not, the research becomes two steps
  (search notes, then a strict chat call) and #58.5 follows
  that shape.
- #58.6: the home maker offers the world's tree specs
  (`models/trees/`, falling back to `models/examples/trees/`
  — baobab, coconut palm, cypress, mangrove, umbrella
  thorn). A surroundings tree that matches by latin (same
  GBIF species) or closest kind uses the spec id as `shape`
  with `plant` the spec itself; `round` stays for unmatched
  broadleaf. Used specs copy into the job's `models/trees/`
  (and the sandbox) so submit carries them; the "no tree
  specs yet" line is gone.
- Suite: 539 passed, 12 skipped.

## 2026-10-27: inbox #57, #56.1, #59: the maker models with looks, measures and a memory

- #57.1: the research returns a `looks` block (shoulder or
  height, tail, coat colours, 2–3 field marks, each with a
  source). It feeds `$DESCRIPTION` (marks and sizes in
  words) and `$MEASUREMENTS` (shoulder, height, tail).
- #57.2: `$CHANGE` carries the Reference-models row for the
  species' kind, found in code by `start_from` and the
  species name (snake kinds alias to the Snakes row, so the
  anaconda lands it too). #57.3: the proportions sentence
  replaces "do not copy its sizes".
- #57.4: a free measurement check in code reads each spec
  (shoulder, torso span, tail-bone chain, leg top, body
  depth) and compares with the sourced sizes (20%) and the
  reference's leg ratio (30%). Misses name exact numbers
  for the fix round ("tail 23 cm, sourced 15 cm" — the live
  lynx spec's only miss; the fox passes clean).
- #57.5: marks span ~15% of the body's length or height, or
  colour a whole part (a spotted coat is a second colour on
  body parts, not tiny balls); game distance is the bar.
- #56.1: behaviour comes from the survey and the archetype,
  never the example animal (`digs`, `hunts`, `territorial`,
  `family`); a missing-text error now guides the fix round
  (write the text or set the flag false) instead of
  stopping. This unblocks the live lynx (`digs` inherited
  from `land_mammal` with no `dig` text).
- #59: every fix round is a real conversation — the first
  message as is, the previous answer as the assistant's,
  then "Change only what these problems name…" — in both
  makers, words, and every other writer with a fix round
  (region, survey, species plan, safety, animals, plants,
  models incl. vision, reference picks). One helper,
  `builder/prompts.py:fix_messages`; the test asserts the
  assistant message.
- Fixtures: research recordings regenerated for the new
  research prompt (answers untouched); replay stand-in
  specs scaled to the recorded research sizes. Reviewer:
  re-run `oneshot` live on the lynx (~$0.17).
- Note: the anaconda's `start_from` is `grass_snake`, whose
  spec lives at `models/examples/` with no animal file; the
  maker reads neither yet, so its `oneshot` re-run needs
  that first. Suite: 530 passed, 12 skipped.

## 2026-10-27: inbox #54.2, #55, replay tests: home seats the unmade, no template carry-over

- #54.2: `home` seats an animal `minset.json` says will be
  made (its file comes from `oneshot`): the cast, the
  plan (decision `new`, taxon from the brief's key) and
  the welcome all tolerate the missing file; seating
  roles use the `start_from` file's hunts as the proxy.
  `oneshot` appends its made prey to the species plan, so
  the prey ships in the pack (it never did before).
- #55.1: a made home keeps no living thing from its
  template. Code check on every try: each fish must be a
  library animal GBIF recorded near the spot; each kept
  ground plant must be research-named (latin key or name)
  or recorded; empty stays empty.
- #55.2: floating and water plants map to `lake_life`
  (`reeds`/`lilies`/`pondweed` densities), never specs;
  in the prompt, since only the writer knows what floats.
- #55.3: a tree `plant` id must be a tree spec or a
  locale plant page, and named in the surroundings;
  otherwise the writer leaves `plant` out.
- #55 verified on the live failing answer
  (`jobs/review-anaconda/records/newhome.jsonl`): try 1's
  invented fish, try 2's roach/perch/bream/pike,
  bracken/fern/moss and `kapok_tree`/`rubber_tree` all
  fail with drop-it errors. Reviewer: re-run `newhome`
  live on the anaconda.
- Replay tests (`tests/test_replays.py`): the review
  jobs' real briefs and research recordings, committed
  under `tests/fixtures/replays/`, run keyless as far as
  each build goes — fox to pack (nothing made), lynx
  through `oneshot` to pack, anaconda through `newhome`
  and two `oneshot` runs to pack. Maker answers are
  committed stand-ins adapted from real files (see the
  fixtures README); builds and the homes check are stubs
  that verify placement.
- Reviewer: run `oneshot` live on the lynx (a few cents
  plus a Blender build) and look at the preview sheet.

## 2026-10-27: inbox #50: the new-home maker (rainforest)

- The `newhome` stage makes the minimum set's home: one
  model call (`home_write`: the research's surroundings,
  `boreal_forest.json` as the worked example, the region
  and plant rules, at most 2 new plant specs), built in a
  scratch world. Done when the world's `homes.py check`
  and `check_models.py` pass on it. One fix round at most;
  still failing stops naming a person. Cost on
  `checks/newhome.json`, the report's home line.
- New plants resolve their latin with GBIF (`--same`):
  already-grown reuses its spec (the home points at it),
  else the spec keeps the taxon with GBIF's accepted name.
  Two new specs never share one species.
- `check_models.py` runs in the sandbox, not through
  `check_draft.py`: the draft overlay has no previews
  folder, and plants require their preview sheet. Only
  this home's lines feed back either way. (The animal
  maker is unaffected: animals need no preview sheet.)
- `minimum` now says to run the newhome stage and resume;
  home pipeline order is intake, research, minimum,
  newhome, home, oneshot, words, pack.
- Evidence: 9 maker tests (recorded pass + fail answers,
  replayed keyless); probes vs the real checkout (boreal
  home adapted to rainforest, bilberry spec adapted to
  water lettuce, stub builds, real `homes.py`,
  `check_models.py`, GBIF taxon): made first try both
  legs. Live rainforest run is the reviewer's:
  `builder stage newhome --job jobs/review-anaconda --record`.

## 2026-10-27: inbox #51.3, #51.4, #53: seasons, species keys, sources

- #51.3: the children's block births animals with the real
  season only where the place has seasons.
- #51.4: the research asks each food's and surrounding's
  `latin` (schema, prompt); `builder/taxa.py` resolves it
  with the world's `tools/taxon.py`, and the minimum, the
  maker and the home match the list by `gbif_key`, falling
  back to the English name for old answers. A resolved key
  on no entry is never substituted; minset prey carries its
  key for the maker.
- #51.4 also fixed live: named prey already in the library
  is never made, and library prey is checked for new
  hunters too (any library animal in the home-to-build that
  hunts nothing). All three review jobs now give #51's
  sets: nothing for the fox, the lynx for the lynx, and the
  anaconda, the rainforest home and the capybara.
- #53.2: source objects (`{title, url}`) normalise to their
  URL before the schema; the lynx research replays.
- #53.1 already fixed at `ff07611` (recorded requests carry
  `max_tool_calls`); #53.3 already held (`minset.json` is
  written before the home stop). Recorded requests were
  regenerated for the new prompt text, answers untouched.
- Note: `builder stage` replays a finished stage's saved
  result; pop it from `state.json`'s `completed` for a
  genuine re-run.

## 2026-10-27: inbox #52.3, #52.4: the fox pack in the game, release v0.1.0

- #52.3: `tests/check_home_pack.gd` installs the fox's
  real pack (`tests/fixture/fox-pack/`, from `jobs/review-fox`
  replayed to pack) and checks it lists and loads like any
  draft place: manifest and hashes, registered folder,
  region found with its name; in the game, listed with its
  title. 0 errors headless in a scratch project and in a
  scratch copy of the game. Fixture ids are `review_fox`
  (the game's folder pattern has no hyphen; real draft ids
  never hyphenate).
- #52.3 also: the recorded research requests (fixtures and
  the review jobs' recordings) now carry `max_tool_calls`,
  so replays run warning-free.
- #52.4: `v0.1.0` is tagged on this commit. Reviewer: copy
  the addon into the game (T36) and deploy with Kai's
  go-ahead; the fox funnel is intake to pack to PR to game.

## 2026-10-27: inbox #51.1, #51.2, #52.1, #52.2: research reads its answer, fox to pack, submit carries the report

- #51.1: grounded answers narrate first (`phase:
  "commentary") and answer last; the research now parses
  only the non-commentary text (`final_text`, with a test in
  the live shape: reasoning, commentary, searches, final
  message).
- #51.2: a parsing answer past its search round is kept;
  the search count is logged (`checks/research.json`,
  result). The API caps the runaway (`max_tool_calls` 10).
- #52.1: `jobs/review-fox` replays intake to pack keyless:
  research parses (10 searches, $0.00), minimum makes
  nothing, pack holds the region with 16 reused species.
- #52.2: `builder submit` fits an `animal_home` draft: the
  PR carries the region file (nothing new: reuses the
  library), a Build report section (per-thing lines, total),
  and `report.html` is rebuilt in the job. Tested against
  a scratch clone of the world with a stub `gh`: branch
  committed, gate green, no push; the real checkout
  untouched. New homes join the layout with the home maker.

## 2026-10-27: inbox #50, #48.4: the one-shot animal maker, welcome's food

- #48.4: the welcome names the research's `main_food`
  (a prey animal only when it is seated in the place), never
  carrion; otherwise the first diet kind that isn't carrion.
  Seated-prey test included.
- #50: the `oneshot` stage makes the minimum set's animals,
  prey first then the animal, one model call per thing (the
  `animal_write` + `model_spec` prompts, `start_from` as the
  only example, no sheet). At most one fix round with the
  checks' errors; still failing stops naming a person.
  Done when the world's `animals`, `models`, `taxa` and
  `region` verdicts pass on a scratch-world build. Cost on
  `checks/oneshot.json`, the report's animal/prey lines.
- #50: `minimum` no longer stops on the animal or the prey;
  only a missing home still waits (home maker next). Home
  pipeline order is now intake, research, minimum, home,
  oneshot, words, pack.
- #50 evidence: 9 maker tests (recorded pass + fail answers,
  replayed keyless); probe vs the real checkout (fox files
  adapted to lynx, stub build, real `check_draft.py`): made
  first try, all four verdicts pass. Live lynx run is the
  reviewer's, after the #51 research rerun.

## 2026-10-27: inbox #42 core, #45.1, #48: research, minimum set, writers carry the rules

- #45.1: the research is one short call (5 searches): main
  food plus up to 3 others, about 5 surroundings, size and
  year, each with a source (`research` stage, prompt, schema).
  Nothing else is researched; food-kind names come from it.
- #42: the `minimum` stage decides the set in code, free: make
  the animal (not in library), make the home (no template),
  one prey (hunts, no library prey in the habitat, smallest
  named prey that can be made). The one-shot and new-home
  makers land next; until then the minimum stops cleanly as
  not possible yet, naming what comes.
- #42: home runs on research and the job's own files (made
  template, made animals join without asking GBIF); new
  species are marked `new` in the plan. Web: no-home animals
  can be picked ("We'll make its home too..."). Report: a
  line per new thing and its cost.
- #48: safety is out of the home pipeline (stage and tests
  stay; `--safety` / `BUILDER_SAFETY=1` restores it; the pack
  gate skips the verdict for safety-less home jobs). The
  shared `prompts/for_children.md` block (verbatim) goes into
  every child-facing writer: animal_write, plant_write,
  region_write, words_translate, words_plant_about and the
  new research (which replaces home_research).
- Feed: new stages report `stage_done`; specific "Making a
  ..." lines need game-side strings (blocked).
- Evidence: 467 tests pass; fox probe (research, minimum
  makes nothing, home, words skipped, pack) passes the gate;
  scripted record + keyless CLI replay green. No live model
  runs from here.

## 2026-10-27: inbox #46, #47: short names, the welcome, English-only words

- #46.1: homes are named "Fox forest, Finland": the last word
  of the English name plus the habitat's last segment, in code
  (`short_name`, `habitat_word`), the same in the worker and on
  the web card.
- #46.2/#45.2: the home stage writes the region's `welcome`
  ("Play as the fox ... Go and find some bilberries to eat!"),
  from the animal and the first named food in its diet order;
  words translates it with the region name, and safety reviews
  it and writes fixes back.
- #47.1: words writes nothing for an `animal_home` job and
  makes no model call (a home's words are English already).
- #47.2: a home's sources are the research's sources plus
  "GBIF.org"; the template's own pages stay behind.
- #47.3: nothing to rebuild here (no committed fixture carries
  template text); the probe confirms the plain description.
- Evidence: 450 tests pass; the fox probe writes "Fox forest,
  Finland" with a 137-character welcome and `["GBIF.org"]`
  sources, and the pack gate passes on it.

## 2026-10-27: inbox #43.1, #44, T39: stages run alone, recorded, replayed

`builder stage <name> --job <dir>` runs one stage on a saved job
folder and saves its output (`state.json`) and its cost
(`log.jsonl`), nothing else. `--record` saves the stage's model
answers under `records/<name>.jsonl`; `--replay` runs on them
with no key and no cost. `builder build --replay <dir>` replays
a whole job the same way. A recording holds no keys and no
private model reasoning, so it can be committed. Anything else a
stage reaches (GBIF, the world's tools) still runs live and free.

Start a home job folder without the web app:

```
builder build --kind animal_home --animal fox --cell 61.5_26.0 \
    --world ../animundo-world --dry-run   # checks only
```

Stages that run alone (all verified this turn except where
noted; `<job>` holds the previous stages' outputs):

- `intake` (free): needs only the job's params.
  `builder stage intake --job <job>`
- `home` (free unless the template lacks a food kind): needs
  `home.json`. `builder stage home --job <job> [--record|--replay]`
- `looks` (1 short call per thing, 3 searches at most): needs
  `home.json`, `research.json`, `minset.json`; run between
  `minimum` and `newhome`, skipped when `looks.json` exists.
  `builder stage looks --job <job> [--record|--replay]`
- `oneshot` (2 model calls per thing, at most 4): needs
  `home.json`, `research.json`, `minset.json`, `regions/`.
  `builder stage oneshot --job <job> [--record|--replay]`
- `newhome` (1 model call, at most 2): needs `home.json`,
  `research.json`, `minset.json`.
  `builder stage newhome --job <job> [--record|--replay]`
- `words` (model calls): needs `regions/` and
  `species-plan.json`.
  `builder stage words --job <job> [--record|--replay]`
- `safety` (model calls): needs `regions/`, `locale/` (and
  `animals/` for a park).
  `builder stage safety --job <job> [--record|--replay]`
- `pack` (free): needs the whole job before it.
  `builder stage pack --job <job>`
- Park stages `survey`, `capability`, `region`, `species_plan`,
  `animals`, `references`, `models`, `plants` run under the same
  command once their inputs exist; not yet tried alone this
  turn.

Fixtures: `tests/fixtures/research-{fox,lynx,anaconda}.jsonl`
(saved food-research answers; the home stage replays them with
no model client in `test_home_replays_saved_research`).

Evidence: a scripted fox home recorded to `/tmp/replay-src`
($0.00, no live calls); `build --replay` on it with all keys
unset ran all five stages, wrote the pack, and logged $0.00
with host `replay`; each stage also replayed alone via
`builder stage`. No live model runs were made from here; the
reviewer runs the real step tests.

## 2026-10-27: inbox #3, arm's-length reviewer (T16 fix)

- Reviewer compares the render against the sheet part by part and
  frame by frame; a clean review after blocking issues is verified
  by a second look before it counts. New birds start from
  `great_tit.json`, scaled. The loop reads its models verdict from
  the world's draft checker, this species' problems only. A build
  that fails with no output stops at once instead of burning
  rounds.
- Attempt 1 (`jobs/eagle-r2/`, $0.22) died in the sandbox: Blender
  5.2.3 crashes in Metal detection under it, so all 6 rounds failed
  before review.
- Attempt 2 ($0.53) built and reviewed all 6 rounds but never
  converged: the fly bar ("level and off the ground") is higher
  than the builder can reach (the game's own great tit flies low
  and tipped), and the writer drifted `fly_pitch` 45 to 68 chasing
  it. Fixed the bar to the achievable look, pinned the pose keys
  in the spec prompt, softened the re-raise.
- Attempt 3 ($0.63 over 16 calls, incl. two API-timeout resumes)
  ran all 6 rounds with a new failure mode: identical issues every
  round, with fixes landing but overshooting (beak tiny to huge,
  tail stick to detached slabs) while the reviewer repeated R1
  wording verbatim ("beak tiny" over a giant beak). Two causes:
  the fixer never saw its own build, and the old issues in the
  review prompt anchored re-raises. Fix prompt now includes the
  previous preview; the review judges blind, with the re-verify
  follow-up keeping the old-issue check.
- Evidence: `jobs/eagle-r2/` (re-run with the fixed reviewer).

## 2026-10-27: inbox #1, #2, draft checker

- Region, species-plan healing and animals stages read the world's
  `check_draft.py` verdicts; the JSON lands in `checks/` and in the
  build report. The animals/models verdicts still fail on the
  world's own unbuilt models, so stages report but never gate on
  them; the models stage keeps its species filter.

## 2026-10-27: T33, Baseten serves the live builder

- All chat runs on Muse Spark Open with
  `BUILDER_OPEN_MODEL_HOST=baseten`; search, images and SAM stay on
  Meta. Own $100 pool, warn below $20, fallback inside the Meta
  caps. Proved live: a translation, a review-shaped call, and a
  real eagle review ($0.024, 7 issues where Meta passed the round).

## 2026-10-27: T38, build report

- `builder report <job>` writes a self-contained `report.html`:
  stages, cost, sources, checks, sheets, masks, round previews,
  review notes. Evidence: `jobs/eagle-demo/report.html`.

## 2026-10-26/27: T15, T16, model loop

- Spec T15/T16 names the red deer; the demo species is the golden
  eagle instead (test area `xx_eagle`, hand-seeded survey).
- 3 rounds to zero blocking issues, $0.64 over 22 calls.
- Evidence: `jobs/eagle-demo/`: `references/` (2 sheets, 4 masks),
  `rounds/` (spec, build log, preview each), `reviews/`,
  `models/` (final spec, `.glb`, preview), `log.jsonl`.

## 2026-10-27: inbox #5, #7, #8 (game-animals bar, deer next)

- #5 done: models verdict feeds back ALL world `problems`
  (existing models too — they are known to work, and the world
  may rebuild them first). `builder/stages/models.py`,
  `tests/test_models.py::test_models_final_verdict_problems`.
- #7 done: fly rule judges attitude in frame and height vs the
  rendered backdrop ("never by height alone"); fly pose keys
  pinned. `prompts/model_review_bird*.md`, `model_reverify.md`.
- #8 in code: kind-aware review prompts — game animals'
  previews (`moose.png`, `great_tit.png`) are the bar figure;
  bird prompts keep the fly rule, mammal prompts ground-more.
  Mammals anchor to the moose with the reviewed eagle as
  second anchor (`CLOSEST` order moose, eagle). Evidence:
  `tests/test_models.py` (anchors, per-kind fly rule, silent
  runs), `jobs/deer-demo/` seeded but not run.
- `builder report` now shows the final preview next to the
  game's closest animal (`tests/test_report.py`).

## 2026-10-27: T14 animal stage vs the real world

- Live run for one species (red deer, scratch job `jobs/t14-deer/`,
  gitignored): locale/schema/taxa/region/models verdicts pass;
  the only animals-verdict problem is the deer's own missing
  model, which the models stage builds later.
- Bug found live: a missing-model complaint naming a NEW species
  was routed back as a fixable problem and burned all 6 rounds
  ($0.17). The skip now runs before owner routing.
  `tests/test_animals.py::test_new_species_missing_model_is_ignored`.
- English text reads naturally; final `reads well` verdict is Kai's:
  `jobs/t14-deer/animals/red_deer.json`.

## 2026-10-27: inbox #9-#13 (approved deer, loosened reviewer, table)

- #13: Kai approved the deer's round 5 as the first demo model.
  The loop stopped there although the reviewer still blocked r5
  on antler detail; `jobs/deer-demo/models/` (gitignored) is
  finalised from `rounds/red_deer/5/spec.json` (1400 tris,
  12 bones, all clips) with `builder report` written.
- #12/#13: the reviewer judges only child-recognition and fit
  next to the game's own animal; small parts roughly right is
  fine, detail is never blocking. All four review prompts, the
  re-verify, and the spec/fix prompts carry "Simple, not
  realistic". Live check: the loosened reviewer passes the
  approved r5 with zero issues (was 1 blocking).
  `tests/test_meta_live.py::test_live_reviewer_passes_approved_deer`.
- #9/#11: no more per-species hard-coding. `builder/reference.py`
  parses the world's reference table every run; the species plan
  attaches each new species' match (start spec, change notes,
  kind) and marks "not possible yet" kinds `needs_code` (word
  match, picker backstop); the models stage starts and bars from
  it. Live probe: eagle -> kestrel/bird, deer -> moose/mammal.
- #10 comes free: snakes, porpoises/dolphins, butterflies have
  table rows with starts, so they are never `needs_code`.
- World rebuilt after the pull (40 models, 0 problems).

## 2026-10-27: eagle converged, round 1 (inbox #8, #13)

- Fresh attempt `jobs/eagle-r3` (gitignored) with the loosened
  reviewer and the kestrel bar: the picker matched the eagle to
  "Birds of prey, owls" -> kestrel live, and round 1 passed with
  zero issues. 7 calls, $0.11 (the strict reviewer never got the
  eagle below blocking in 4 attempts).
- Real size (0.774 m vs 77 cm sourced), golden colours present,
  sits next to the kestrel; the models verdict passes. Other
  verdicts fail only on the hand seed's thin region/animal
  files, which their own stages own.
- This was the first of the 2 allowed attempts (#8); no second
  needed. Kai's approval of the final render still stands as
  the T16 gate: `jobs/eagle-r3/models/golden_eagle.png`, with
  the bar beside it in `jobs/eagle-r3/report.html`.

## 2026-10-27: T17 words (translations)

- New `WordsStage` after animals: each new species' texts plus
  the region name into every language in `locale/languages.json`
  (today Finnish), written as new-keys-only `locale/<lang>/`
  files, proved by the draft checker's locale verdict.
- Tone comes from the game's own Finnish (the moose's lines in
  the prompt); `{placeholders}` preserved; keys must match
  exactly (one retry, then a loud stop).
- `reasoning_effort="low"` on every call; `MetaClient` nests it
  into `chat_template_kwargs` on Baseten (top-level 400s there)
  and keeps it top-level on Meta (probed: Meta 400s the nested
  form). Live Baseten translation with the nesting works.
- Live check on the deer (`jobs/t14-deer/`, gitignored): 2 calls,
  $0.01; locale passes; Finnish reads naturally ("Saksanhirvi",
  "Sinulla on nälkä", "Lepäät kuusen alla tuulensuojassa").
  A Finnish speaker's verdict is still Kai's.

## 2026-10-27: T46 plants

- New `PlantsStage` after models: the region's new ground-cover
  models (at most 3, enforced in the region prompt and both
  stages) loop draft -> GBIF verify (`taxon.py --plant --same`,
  reusing a known spec under another name) -> Blender build in
  the shared sandbox -> models verdict -> preview review, 6
  rounds. Finals in the job's `models/plants/`; food-marked or
  poisonous plants (world list or `year.notes`) are flagged for
  an expert in the result.
- Draft follows the bilberry (bush) and wood anemone (ground);
  the review bars the same-kind game plant. No reference sheets
  for plants in v1: the examples carry the shape, and sheets
  would need plant measurements the survey has no source for.
- Live check (`jobs/t46-sorrel/`, gitignored): wood sorrel
  converged in 4 rounds ($0.27), GBIF 8211958, schema/taxa pass;
  the models verdict's only line is the world's own gap (below).
  Looks right: basal clover leaves, June flower, bare winter.
  Kai's eyeball pending: `models/plants/wood_sorrel.png`.
- Found live: `check_draft.py` never overlays previews, so the
  plant rules always miss the draft's preview sheet and the loop
  could never exit (reviews were clean at r4-r6 first). The
  stage skips that one line for its own plant (the build just
  wrote it); the world side should overlay previews or excuse
  drafts. Also found: the plant schema forbids `warning` /
  `about_safety`, so edibility notes go in `year.notes`.
- Words now runs last and writes plant about texts (English
  composed from the spec, Finnish translated, Latin copied).
  Rainforest `needs_code` was already covered by the capability
  mangrove test. Report/pack integration for plants waits for
  T21.

## 2026-10-27: inbox #20 (eagle approved, T16 done)

- Kai approved the golden eagle from `jobs/eagle-r3` round 1.
  T16 is done with two approved models: red deer (round 5,
  approved over the reviewer's antler block) and golden eagle
  (round 1, clean). No more model runs until after Kai's
  production test.

## 2026-10-27: T18 child-safety reviewer

- New `SafetyStage` after words: a separate reader reviews every
  new text (animal texts, place name/description/places, food
  and flower and plant names, plant about texts, EN+FI) with
  sources, sensitive-species context and the game's own tone.
  Rewrites go back to a writer call once (EN+FI together, files
  updated in place); fails and second flags stop the job for a
  person. Verdicts in `checks/safety.json`.
- `tests/safety_cases.json`: 30 good + 30 bad. Live eval passes
  clean: 0 bad missed, 0 good flagged (spec allows 2).
  `tests/test_meta_live.py::test_live_safety_sixty`.
- Found live on the deer job (70 items): the reviewer failed the
  shipped carrion names "roe deer remains" as bedtime-frightening.
  Calibrated: plain food/plant/flower names pass, only graphic
  detail fails them; the two names are regression cases now.
  The rewrite path also proved live (2 items fixed, re-passed).
- `exclusions.json` already exists and intake already enforces
  it; it is empty and core-team-maintained. Kai: it needs its
  first real entries (sanctioned/conflict/disputed areas) before
  any public running.

## 2026-10-27: T21 pack step (submit still to come)

- New `PackStage`, last in the pipeline: gates on the root
  `safety.json` (all pass) plus a fresh full `check_draft.py`
  run, ignoring only the known missing-preview line, then lays
  out `pack/` per spec 01 (draft region id, only new files and
  keys, locale keys redrafted, manifest with sha256 + bytes)
  and refuses packs over the size limits (2 MB a model, 8 MB
  a pack).
- `SafetyStage` now also writes the root `safety.json` gate
  record (`{"items": [...]}`) next to `checks/safety.json`.
- Build/resume tails are honest now: they name the actual next
  unfinished stage, or say the job is done (exit 0), instead of
  a hardcoded list. Improve with `other` still gates on the
  unbuilt `improve_plan`.
- Proved with a zero-cost probe (`/tmp/pack_probe.py`, scratch
  copy only): t14-deer's real texts and safety verdicts plus
  deer-demo's approved model files pack clean through the real
  world checker — all six gates pass, 6 files, 1.29 MB.

## 2026-10-27: T21 `builder submit` (inbox #14)

- New command for an admin's own computer: it lays the draft's
  newest round out like animundo-world on `builder/<draft_id>`
  with the real place id (region, new animals and model specs,
  committed preview sheets, locale keys merged, never a changed
  shipped text), runs `check_draft.py` on exactly that content,
  commits signed-off (DCO) and opens the PR with the admin's own
  `gh`. The link goes back to the job's `submit.json`.
- The `.glb` files are not committed: the world builds (and
  ignores) its own; the gate still checks the round's builds
  from the job. The body has the area, the sources, every
  check, the new species with previews, the final review notes
  (verified rounds win), the person-approval note and the
  expert flags; it never names the family and never carries
  their notes (a decoy-note test pins that).
- Gate and layout failures roll the branch back fully; a
  failed `gh` leaves the committed branch and the next run
  carries on from it. A second submit is refused.
- Proved end to end against a throwaway copy of the real world
  with a stub `gh` (`/tmp/submit_probe.py`): all six real gates
  pass, the branch holds 6 files, the commit is signed off.
- Still open: the round comes from the local job; the Storage
  download lands with T25. The first real PR is Kai's call.

## 2026-10-27: T23 rules (inbox #30, #31)

- `firebase/firestore.rules` and `firebase/storage.rules`: drafts
  (verified grown-up creates, family reads, owner improves /
  submits after playing / reports once, owner deletes outside
  review), feed, public places, jobs (no clients), the off
  switch (read-only), admins, the admin inbox (admins read and
  mark handled), wishes (one vote per account and area) and the
  new `admin_actions` (admins ask the worker: send on, publish,
  turn down with a reason, show again, remove with an answer).
  Admins read drafts, feeds and packs, never write them. Each
  file carries one mergeable `// BEGIN/END animundo-builder`
  block with match sections only.
- 39 emulator tests (`cd firebase && npm test`) prove every
  "only", including submit-only-after-play and feedback-once.
  The sandbox here forbids listening sockets, so the reviewer
  runs them on Kai's Mac. Found that way: Storage `allow read`
  includes `list`, so packs use `allow get`; missing drafts
  need an `exists()` guard for a clean deny; the suite names
  its files, runs serially, and shares one `--project` with
  the emulators.
- Also noted inbox #29 (T26 is now the web app at
  animundo.app/build, T25 the small game addon, web app first);
  it lands after T22 and the short T20.

## 2026-10-27: T22 worker --listen

- `builder worker --listen` runs the milestone-1 funnel on Kai's
  Mac (Application Default Credentials, no key file): it
  listens on `drafts` and `admin_actions`, limits-checks new
  drafts (2 active, 3 a week, the daily ledger, unreadable
  ledger refuses), builds round by round with a live feed,
  uploads the pack and the report to Storage, moves submits to
  `in_review`, hides/removes on feedback, and carries out Send
  on (runs `builder submit` with Kai's `gh`), Published (writes
  `places_public`), Not accepted, Show again and Remove.
- Statuses are the guards: restarts only replay events still
  waiting; processed improves and ask-person notes are cleared.
  Content errors fail at once (two retries for transient ones
  only); a failed improve keeps the last good pack playable.
  Stuck `queued`/`building`/`improving` drafts are named at
  startup and left alone. Docs: `docs/worker.md`.
- Feed keys for the T26 web app: `explore.feed.round_started`,
  `sources_found`, `stage_done`, `models_built`, `preview`,
  `round_done`, `stage_failed`, `not_possible`, `retry`.
  Reports live at `reports/<draft>/<round>/report.html`
  (Storage rule added, family and admins read). Drafts gained
  `admin_answer`; new contracts `admin-action` and
  `admin-inbox-item`.
- Also in this commit: CLI and worker share `builder/jobs.py`
  for contexts and stage assembly; `PackStage(version=round)`;
  capability's stop is public (`NotPossibleError`); areas take
  `biome` and `buildable` (the T20-short/T26 file must satisfy
  the area schema as a bare list or `{"areas": [...]}`);
  `FileLedger.day_spent()`; Blender runs without secret
  variables in its environment.
- Verified with 22 FakeStore tests (no Firebase from here);
  the Firebase connect and the first live draft wait for Kai's
  terminal (ADC + the areas file from T26). The Storage
  `reports/` addition needs one more reviewer emulator run.

## 2026-10-27: short T20 area list (inbox #28)

- `web/build/areas.json`: spec/09's milestone-1 list in build
  order (Repovesi first, Sundarbans last), each with a real
  centroid and bounding outline, its biome, ecoregion where it
  has a land one, and `buildable: true`. The worker reads this
  file for intake; the web app reads it for the map.
- Svalbard is filed under Norway (`no_svalbard`, country NO)
  as in spec/09, not SJ. Ningaloo has no ecoregion (marine).

## 2026-10-27: T26 builder web app (inbox #29)

- `web/build/` is the grown-ups' app (published at
  animundo.app/build): email-link sign-in, a Leaflet map of the
  12 areas with looks (yours, being built, buildable, in the
  game, not possible yet), counts, biome/state filters and
  wishes, an area card with Build this place, and Your drafts
  with Watch the agents (live feed, preview sheets), Improve
  (round < 4, else Ask a person), Submit (after playing),
  Not right and Report a problem, all in English and Finnish.
  `?explorer=<species>` from the game becomes the draft's
  first explorer. Static files only; Leaflet 1.9.4 vendored,
  OSM tiles with attribution, RESOLVE credited for
  ecoregions; Firebase is the game's `animundo-dev` project.
- Verified here: `node --check`, JSON validity, EN/FI key
  parity, every `data-i18n` and worker feed key present, every
  local file referenced exists. The browser run waits for
  Kai's terminal (`cd web/build && python3 -m http.server`
  per the README); `animundo.app` must be an authorized
  domain in the Firebase console or sign-in fails there.

## 2026-10-27: web app fixes (inbox #33)

- `[hidden]` now really hides (Leaflet CSS beat it), and
  unselected language buttons got their dark text back.
- The Firebase web key is whole again, allowlisted in
  `.gitleaks.toml` for exactly that key in exactly
  `web/build/app.js` (verified both ways: ours passes there,
  any other GCP key anywhere still flags). Key restriction
  and authorized-domain notes are in the README.
- `?emulator=1` connects Auth, Firestore and Storage to the
  local emulators (ports pinned to `firebase.json` by test)
  and offers a test grown-up button; `firebase/seed-emulator.mjs`
  (no dependencies) creates `test@example.org` with a verified
  email, idempotently, and fails loudly if the flag does not
  stick. The seed script is unrun from here (no listening
  sockets); the reviewer confirms it with the next run.
- The app now wears the game site's look: dark forest
  background, cream text, sun-yellow pills and headings, the
  ANIMUNDO brand row, English/Suomi toggle and a matching
  footer, with CARTO's dark basemap under OSM attribution and
  area colors retuned for it (no more purple).

## 2026-10-27: emulator auth, Natural Earth, card text (#34, #35)

- Emulator mode runs under `demo-animundo` (a `demo-` project
  can never reach production); the README starts and the seed
  script targets the same id. The seed sets the verified flag
  through the emulator's admin access, since a user token
  cannot. The app's feed query path was already right; the
  null-draft error came from the project mismatch, and the
  feed rule now guards with `exists()` anyway.
- No more tile service: Natural Earth 1:110m land and
  coastline (public domain) are vendored and drawn as GeoJSON
  in the game's dark colours, areas on top. The footer credits
  Natural Earth for the map and OSM + RESOLVE for the areas.
- The game icon is vendored (`icon_192.png`, relative links:
  no 404 locally or published), and the area card shows spec
  06's plain words above Build this place (EN exact, FI mine).
- T39 replay moves up per Kai: right after T25 + short T24.

## 2026-10-27: T25 builder addon in the game (inbox #29)

- The addon lives at `godot/addons/animundo_builder/`, the path the
  game's `tools/sync_builder.sh` copies in; entry `builder_mode.gd`,
  started as the BuilderMode autoload. Uses only BuilderHooks and
  the generic Cloud calls, backend injected, so the checks run the
  same code against stubs in a scratch project (new `godot` CI job).
- Boot reinstalls packs (checked again, failures unregistered),
  marks family drafts on the globe (your_draft, being_explored,
  not_possible_yet), and adds the grown-up and places buttons. The
  Builder screen lists test areas and drafts with Play (download,
  verify, install, record) and Report (owner only, the rules'
  shape). The addon carries its own EN/FI words; reason labels
  mirror the web app word for word (tested).
- Both headless checks pass here and in the game (0 errors):
  every draft status in both languages, buttons, screen, Play,
  Report, pack rules, newer-wins, built-in-wins, saved life kept
  while its pack is missing. `docs/game-interface.md` lists the
  plug-in calls and nine places reality differs from the spec.
- Game-side note for Kai: a pack without locale/ or animals/
  logs "Couldn't open directory" from i18n.gd/species.gd on
  every boot; the readers want a dir_exists guard.

## 2026-10-27: loop fixes #36.1 and #36.2, addon look fix (#29)

- #36.1: the animals stage excused only nowhere the region's
  not-yet-built plant lines, so every place with a new plant
  stopped before the plants stage ran. It now excuses exactly
  the ids the plants stage will build (`new_plant_ids`, shared
  with region and plants); anything else still stops the job.
- #36.2: a species call cut off at max_tokens read as ok and
  left one animal for a whole park. `is_cut_off` (explicit
  signal first, token count as fallback) now spots it, and
  survey research continues where it stopped (twice at most,
  then a loud stop). Rounds cap 6 to 8 for the headroom.
- #36.2: the species plan backfills the world's animals GBIF
  records near the center, even when the survey missed them:
  a count query per file (fox and hare join Repovesi, the
  Saimaa seal rightly doesn't), seated in the cast by the
  file's own part, feeding predator gating. GBIF failures
  land in `backfill_unchecked`, never silently.
- #29 compliance: the addon's `not_possible_yet` globe look
  is gone (the look stays unused); the screen explains.

## 2026-10-27: loop fixes #36.3 to #36.7

- #36.3: survey searches capped per call in the prompts (10
  land, 12 species, 8 gaps, down from 25/40/15), 40 total,
  and input tokens capped (0.5M a call, 1.2M the survey);
  over any of them stops the job instead of spending on.
- #36.4: the feed listener starts only once the server has
  the draft, and a failed listen drops out and retries three
  times instead of staying dead.
- #36.5: a failed draft says plainly what happened and
  offers Try again (owner-only rule, failed to requested;
  the worker resumes the local job and clears the error)
  and Remove (already owner-allowed). Failed areas show as
  building until then.
- #36.6: the seed's admin URL goes through identitytoolkit.
- #36.7: `load()` reads `.env` from the working directory
  upward (never overriding real env, never printing: keys
  left the Config repr too). python-dotenv added.

## 2026-10-27: animal first, web screen and draft rules (#40 A, first half)

- The web app is animal first, English only: the grown-up
  types an animal (static `animal-list.json`, 8-entry stub
  until the reviewer's ~200 lands), the map shows GBIF's
  occurrence density (`orange.marker`, style name checked
  against GBIF's maps docs), a tap snaps to a 0.1-degree
  cell, cities refuse (vendored NE 50m urban areas) and
  empty squares refuse (one GBIF count query), and the
  card offers Make this home ("Lynx forest, Finland":
  habitat from the list, country from vendored NE admin
  shapes). Drafts carry kind animal_home, species_key,
  cell, place_name and country.
- Rules take animal_home creates (species key a positive
  int, cell snapped, name required, ISO country optional)
  with emulator tests; the draft contract too. The park
  flow (areas, cards, wishes) stays in the repo, off the
  main path; Finnish strings are dropped from the web
  app only. Point-in-shape and snapping probed in Node
  against the real shapes (Repovesi Finnish and wild,
  Helsinki urban, ocean empty).
