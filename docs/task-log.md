# Task log

What ran for each task, and where the evidence is. Newest first.
Job folders live under `jobs/` (local only, never committed).

## 2026-11-02: inbox #209
  (world 105aee5;
  builder 946a442,
  05d514d)

- A publish with
  no job of its
  own records its
  pass (946a442):
  one build_jobs
  remake doc,
  published, the
  changed tiles
  with versions,
  the world, cost
  0, plus its
  history rows.
  Jobbed publishes
  write nothing.
  The contract
  takes kind
  remake (tiles,
  no single tile;
  enrich keeps
  its 200 cap).
- Code tiles
  require
  name{en,fi}
  (05d514d); open
  ocean keeps an
  English-only sea
  name where the
  table has no
  fi. A remake
  backfills a
  missing name,
  so z10_0927_0562
  gains one on
  the next pass.
- The compass
  uses each
  place's filed
  fi_gen, cases
  fi by rule
  without it,
  and skips a
  place with no
  fi, never
  Englishing it.
  World 105aee5
  files the three
  genitives word
  for word from
  the inbox; the
  three compass
  lines read
  exactly as
  specified.
- Tests: the
  remake record
  with exactly
  its tiles and
  versions, the
  backfill, the
  filed genitives,
  no English
  place name
  inside fi, the
  schema both
  ways.

## 2026-11-02: inbox #210
  (builder 64234b1)

- The probe cut
  the survey to
  six keys, so
  check_answer
  refused it after
  a paid survey.
  The probe keeps
  both outside
  lists now, empty
  when missing;
  recorded species=
  answers and
  resurvey merges
  do the same.
- Tests: plan_tile
  end to end with
  a fake survey
  source produces a
  plan (fails
  without the fix);
  a recording
  without the lists
  still makes.
- Deploy 64234b1;
  then one survey
  should end in a
  plan, z10_0927_0563
  final, compass
  names by the reef
  and Nuuksio.

## 2026-11-02: inbox #207
  (world e03464c;
  builder 0eaf726)

- The place file
  stays the
  record. New
  `builder place
  file-pack`
  merges a
  shipped pack's
  species: old
  packs read
  their
  additions, new
  packs recompute
  (playable,
  tag-rule
  predators/prey;
  featured stays
  hand-done). The
  ship writes
  nothing new: a
  delta file
  needs a game
  change first.
- Backfill: reef
  gains
  hammerhead
  (playable +
  predator, live
  stays false).
- Test:
  missing-pack-
  species covers
  old and new
  packs; the
  reef checks
  clean live.

## 2026-11-02: inbox #208
  (builder f47fd37)

- Surveys failed
  strict mode since
  v16: the two
  outside lists
  join required,
  the prompt says
  empty is fine.
  Old caches still
  read. All other
  strict schemas
  audited exact.
- Tests: the
  strict-subset
  check over both
  survey schemas,
  the sent format
  equals the
  schema, resurvey
  asks through the
  same call.
- Redeploy the
  worker from
  f47fd37; then
  z10_0927_0563
  should plan
  final.

## 2026-11-01: inbox #202.3
  conversion 4 of 4
  (builder 8210cd5)

- Place requests
  file places
  (D20): the job
  writes
  places/<id>.json
  (names, centre,
  tile, cast,
  start, live
  false), checked
  against the
  place schema. No
  land, no fish,
  no seed.
- Stays playable:
  tile index + all
  six groups hash-
  identical to the
  #202 baseline,
  live
  enrich_hammerhead
  manifest 1053
  bytes. Your
  uncommitted
  world places
  work untouched.
- Reef, old
  region -> new
  place: playable
  the same 7,
  predators
  (reef_shark,
  lionfish) and
  prey (the 3
  small fish) the
  same; English
  name the same,
  Finnish added;
  tiles [[927,
  560]] and centre
  the same; start
  at the centre,
  facing north.
  The region file
  still plays
  (live false).

## 2026-11-01: inbox #202.3
  conversion 3 of 4
  (builder 3d5901a)

- Plan names from
  places (#169.2):
  region_places is
  named_places and
  reads places/,
  not regions/.
  Same English
  names and
  centres; Finnish
  now derives from
  the places'
  real Finnish
  names. Tests
  write places.
- Stays playable:
  tile index + all
  six groups hash-
  identical to the
  #202 baseline,
  live
  enrich_hammerhead
  manifest 1053
  bytes. The world
  checkout holds
  your uncommitted
  places work
  (places/,
  place schema,
  sync); this
  commit touches
  nothing there.

## 2026-11-01: inbox #202.3
  conversion 2 of 4
  (builder 3cc4bc7)

- Packs without
  additions
  (#176/#179) go:
  an enrich pack
  is the model +
  locale + the
  manifest. Region,
  fish-count and
  cam_height gates
  go with the
  seating; the
  file's own gates
  check length,
  year, hunts and
  camera against
  the research and
  the brief's
  tiles.
- Stays playable:
  tile index + all
  six groups hash-
  identical to the
  #202 baseline,
  live
  enrich_hammerhead
  manifest 1053
  bytes, world
  clean. No game
  change needed:
  the game skips a
  missing
  additions file
  (add_to_region
  continues, packs
  validate only
  present files).

## 2026-11-01: inbox #202.3
  conversion 1 of 4
  (builder c56045a)

- Enrich into a
  region (#168) is
  gone: the intake
  converted every
  tile already, so
  region_place and
  its seat/fish
  helpers go, with
  their ten tests.
- Stays playable:
  tile index + all
  six groups hash-
  identical to the
  #202 baseline,
  live
  enrich_hammerhead
  manifest 1053
  bytes, world
  clean. The game
  still reads its
  region files;
  this commit only
  narrows builder
  checks to tiles.

## 2026-11-01: inbox #201
  (builder ec73e2a;
  world 6517ff5)

- .1 (builder
  ec73e2a):
  home_for_cell
  skips retired
  biomes at every
  return: listed
  sea, generic sea,
  open_sea and the
  first-match loop.
  default_bundle,
  resurvey and the
  lab follow. A 717
  lake cell gets
  boreal_lakeland;
  forest cells are
  unchanged (test
  fails pre-fix).
  _closest_home
  stays.
- .2: the four
  published lake
  tiles remade by
  code from their
  recorded surveys
  ($0, no model)
  and published to
  tiles/v2/z10/
  074_035.json,
  group version 9:
  z10_0592_0284 v8,
  z10_0593_0283 v6,
  z10_0593_0285 v7,
  z10_0594_0284 v9.
  Index 23 ready at
  world 6517ff5.
  fi_saimaa left
  alone.
- .2 drift to know:
  each tile also
  gains the filed
  common_tern in
  playable (its
  survey named it;
  wanted's cap of
  12 had cut it).
  Nothing else
  moves: edges,
  level, water,
  plants, names and
  the other species
  are byte-same;
  one wanted cap
  refill (muskrat).
- .3 (world
  6517ff5):
  check_tile fails
  a single tile on
  a retired home or
  blend home;
  tiles in a group
  file (already
  published) keep
  theirs. World
  tests: 41 as
  expected.
- Suite: 1066 + 41
  passed, 12
  skipped; the one
  known world
  selftest failure
  counts 72/72
  both before and
  after .3.

## 2026-11-01: inbox #200
  (world 03c8657,
  f436c32; builder
  5b28f24)

- .1: blue_mussel
  goes nowhere. No
  live plan or job
  names it; ocean
  tiles carry no
  wanted, and fresh
  plans can't add
  it (.3). Its
  records stay in
  jobs/out189 and
  jobs/rec189,
  untouched. The
  $0.304 is spent.
- .2: tern filed
  (world 03c8657):
  the lab spec
  (== the job spec)
  to models/, its
  preview, the Mac
  .glb kept as the
  local ignored
  build (the game
  sync builds its
  own), the animal
  file with the
  build's length
  0.126 and yaw
  0.0, Finnish
  strings and the
  library mark.
- .2 traits (world
  f436c32): the
  #194 review's
  diet bottom_bugs
  and insects,
  shelter shore,
  and the open_sea
  mark, without
  which no ocean
  tile can want
  it. World checks
  pass except the
  known missing
  whale .glb.
- .3 (builder
  5b28f24): food
  names are never
  planned. The find
  is the survey
  pick, else the
  home's grown
  plants[].model.
  Test: a Coral Sea
  open-sea tile
  with today's
  world plans no
  blue_mussel item
  (fails on the old
  code).
- .4: both reef
  tiles remade by
  code, tern
  playable from
  GBIF records,
  and published:
  tiles/v2/z10/
  116_070.json
  (z10_0928_0560)
  and 115_069.json
  (z10_0927_0559),
  tile version 1,
  index 23 ready at
  world f436c32.
- Real spend: $0
  new (GBIF only,
  no model calls).
  Sunk $0.45 total
  (#189 $0.30 and
  #191 $0.15), in
  the $1.10
  ceiling.

## 2026-11-01: inbox #199
  (builder 3fe2020,
  98579f3)

- .1: a spread job
  carries spread.kind
  and what plus
  tiles; the worker
  claims it and
  remakes each listed
  grid tile by code
  with no client, so
  $0 and no model
  calls. Versions
  and the pack land
  on the job; cost
  stays 0.0.
- .1 skips: a tile
  where the species
  is off the survey,
  off the list, or
  out of habitat
  skips with the
  reason; place
  cells and non-grid
  tiles skip too.
  Only the spread
  species may leave
  wanted.
- .2: the species
  file comes from
  the world's
  library, else a
  live pack's
  manifest copy,
  staged into the
  checkout for the
  job and removed
  after. Neither
  means the job
  fails (no file).
- .3 (3fe2020):
  unknown kinds
  return before the
  claim, staying
  queued unowned.
- .4: four tests in
  test_worker.py: a
  keyless run that
  publishes the fit
  tile and skips the
  miss, a drifted
  remake that fails
  (remake_failed),
  a pack-only lion
  that spreads and
  leaves no file,
  and a listed-sea
  home fitting a
  coast tile.
- Full suite: 1105
  passed, 12
  skipped; only the
  known world
  checkout failure
  stays red.

## 2026-11-01: inbox #198
  (builder 8cf2083,
  bff41dd, 0be3d53)

- .1: a probe that
  fails the world's
  check returns a
  blocked plan with
  the reason, so the
  area reconciles to
  blocked; transient
  faults still raise
  to retry.
- .2 why: 0927_0563
  is 60% land on the
  Mission Beach coast
  (-17.8, 146.07)
  with marine eco 150
  over all of it; no
  rainforest home
  lists 150, and the
  fallback ignored
  the ecoregion, so
  the tile failed the
  check and waited.
- .2 fix: a coast
  cell with no home
  for its land side
  builds as sea when
  a listed sea home
  covers the eco
  (coral_reef here);
  the generic sea
  never flips new
  land (Tallinn still
  no_home). Bundles
  must list a listed
  eco.
- After the fix the
  tile makes (sea,
  coral_reef, passes
  the check) and the
  plan is code_only:
  $0 to ship, with 2
  optional improves
  (hammerhead,
  crocodile) at $0.84
  total. Name: Sea
  south of Great
  Barrier Reef.
- Cost: $0 spent and
  $0 to ship; the
  $0.02 survey is
  sunk. The plan ran
  on the neighbour
  reef survey ($0,
  synthetic for this
  tile); the worker's
  real survey may
  vary the improves.
  Nothing builds
  without Kai's go.

## 2026-11-01: inbox #197
  (gates 1-4; .5 waits
  on Kai's go)

- .1 (2428e6a): length
  passes 0.8 x typical
  to largest, 2 x
  typical under 20 cm;
  research names both
  (strict + schema +
  prompt). Six reviewed
  pass; 4.97 m fails.
- .2 (44a77a0): a lone
  Finnish map_name is
  allowed, and the ship
  writes it from the
  file's name.
- .3 (2087924): the
  fish count and seat
  read tags; the camera
  reads region depth,
  else the biome's.
- .4 (559562c): the
  ship looks at the
  final preview itself
  (shared review_marks);
  the gate fails a
  final miss, and any
  disagreement with
  both verdicts.
- .5 NOT run: the $0.50
  six-animal ship run
  needs Kai's go.
- Note: the lab's
  calibrate_gates.py
  builds research with
  typical only; it
  needs largest_cm per
  animal to pass .1.

## 2026-11-01: inbox #196.1
  (wanted measure, $0;
  builder 2fb44cd)

- Storage `tiles/v2/z10`
  (4 group files): 19
  ready tiles, 18 with
  wanted, 15 at exactly
  12. The cap binds on
  four in five tiles.
- Cached answers (13
  z10), each remade
  through the real
  `make_tile` with the
  cap lifted ($0 cache
  hits, real habitat
  filter, today's 30
  animal files): full
  drops total 397, so
  the 12 cut hides 253
  still-missing species.
- 10 worst (tile:
  full / cut):
  0580_0295: 57 / 45,
  0580_0296: 56 / 44,
  0581_0297: 44 / 32,
  0583_0296: 42 / 30,
  0581_0294: 35 / 23,
  0583_0295: 35 / 23,
  0582_0294: 34 / 22,
  0592_0284: 21 / 9,
  0594_0284: 21 / 9,
  0593_0283 and
  0593_0285: 20 / 8
  each (tie). The reef
  tiles cut 0 (full 5
  and 7).
- No cached answer
  names a plant the
  library lacks (0 of
  13): picks validate
  against the library
  enum, so a missing
  plant can't survive
  in the answer. The
  gap is upstream —
  the survey can't
  nominate outside
  plants — which .3
  fixes.
- Building 2 to 5
  next; nothing here
  spends.

## 2026-11-01: inbox #194
  (WAITING: report, no
  spend; mussel needs a
  ruling, not a cap)

- `common_tern`: DONE at
  $0.149, under every cap.
  Research $0.055 (5
  searches), looks $0.047
  (3 searches), maker,
  edits and checks $0.041,
  Finnish $0.006. New cap
  needed: $0.
- `blue_mussel`: $0.304
  spent, and it cannot
  finish as a plant at any
  cap. Research $0.195 (10
  searches), four stage
  chats $0.109, then the
  plants stage refuses it:
  GBIF resolves
  `Mytilus edulis` to
  Animalia, not Plantae
  (#190 called this: it is
  an animal planned as a
  plant). Paid research is
  kept, but a rebuild as
  an animal would research
  again (different stage).
- Sunk, both items: $0.45
  (#189 $0.30 + #191
  $0.15), inside the $1.10
  ceiling. All caps held
  (tracked from the run
  logs; the ledger stamps
  one id per run, so the
  $0.40 bound each run,
  not each item total).
- Total for both reef
  tiles, tern filed once:
  $0 more past the sunk
  $0.45, except the mussel
  ruling. Both items file
  once; the second tile
  then replans with
  nothing missing and
  remakes by code. No
  duplicate builds.
- Filing blocker, tern:
  its `.glb` won't build
  on this Mac — Blender
  5.2.3 crashes in Metal
  GPU detection before any
  Python runs (the mallard
  fails the same way).
  The spec, animal file
  (reviewed: diet
  bottom_bugs+insects,
  shelter shore), Finnish
  and list entry are
  ready; the world checks
  fail without the `.glb`.
- #191.3 done in this
  push (`ecc6215`, whose
  message mangled $0.42 —
  the code reads 0.42):
  animal $0.42, plant
  $0.13, medians from 10
  finished animals and 5
  finished plants, mussel
  excluded as unfinished.
- Waiting, as asked: no
  building, no publishing,
  until Kai approves the
  total and rules the
  mussel (build as animal,
  hand spec, drop, or gate
  exception).

## 2026-11-01: inbox #193
  (builder 19b209e)

- .1: the ship builds the
  place's missing plant
  models in its world
  before the world checks
  (a spec with no `.glb`
  gets one); built models
  stay cached in the
  checkout, so each worker
  builds once. A failing
  build stops loudly.
- .2: research writes marks
  at child level (shapes,
  never fine detail), and
  the marks review judges
  each in the views that
  show it (a head's outline
  in TOP).
- .3: the maker picks
  `hunts` from the reviewed
  prey tags only — the same
  list the gate checks —
  at both render sites;
  empty list means `[]`.
- Evidence: a ship replay
  whose region check fails
  until the fake builder
  lays the `.glb`s, then
  publishes; prompt phrase
  tests; the render test
  carrying a reviewed tag.
  Research replays
  re-recorded (prompt only;
  responses untouched).
  Suite: 1064 passed, only
  the known stale-checkout
  failure.

## 2026-11-01: inbox #192
  (builder c29a34f)

- The builder's storage
  block reads `tiles/v2/`
  get-only, like v1; v1
  stays. The game drops its
  copy at the next sync.
- Evidence: emulator tests
  for a v2 group file and
  the v2 index (run in CI;
  the sandbox here refuses
  emulator ports).

## 2026-11-01: inbox #189
  (STOP: both reef items
  need more than $0.15)

- Commits: none. Nothing
  in the builder changed;
  this was make, build and
  publish only.
- Published, first v2
  files, world `ec0d9d0`:
  `tiles/v2/z10/072_036.json`
  (4 Nuuksio tiles),
  `072_037.json` (4 more),
  `074_035.json` (4 Saimaa
  tiles), `115_070.json`
  (2 reef-neighbour sea
  tiles), and
  `tiles/v2/index.json`
  (14 ready, 0 blocked).
  `tiles/v1/` untouched.
- Real spend: $0.30 of the
  approved $0.60. The 14
  code tiles cost $0.00
  (ledger delta 0.0000).
- STOP, as approved: neither
  reef item fits $0.15.
  `common_tern` spent $0.10
  (research $0.055, looks
  $0.047) and the $0.08
  maker call refused the
  cap. `blue_mussel` spent
  $0.19 on research alone
  (one call, its searches)
  and the $0.15 cap stopped
  the pipeline after it.
  The $0.15 animal/plant
  medians in `prices.py`
  don't hold here; not
  re-measured without word.
- The two reef tiles are
  NOT published: their
  plans name the unbuilt
  items, and only filed
  species land in a tile.
  Nothing was filed, and
  the second tile's copies
  were not attempted.
- Built state, for review:
  `jobs/rec189-0928_0560/`
  (plan + records) and
  `jobs/out189-0928_0560/
  replay/` (tern: research,
  minimum, looks, newhome;
  mussel: research).
- Waiting on Kai: raise the
  per-item caps (to what?),
  then the builds resume,
  the items file, and the
  reef tiles remake and
  publish as versions.
- #190 noted: worker v15
  (with #185) is live, so
  the Modal caveat is
  lifted; unneeded, since
  the publish ran locally.
  The mussel's origin (open-
  sea food name, planned as
  a plant) is on the record;
  kept, as asked.

## 2026-11-01: inbox #188
  (builder 1c9fbec,
  4b23f5a)

- .1: #184.1 and #184.2
  stand, untouched.
- .2 (`1c9fbec`): additions
  take `plants` on `cover`
  or `water`: exactly `id`
  and `density` (above 0,
  at most 1), 20 a side.
  `trees` and any other key
  refuse the pack. No biome
  or region is edited: the
  laid side only proves the
  additions add the one
  plant.
- .3 (`1c9fbec`): a passing
  plant item publishes
  `packs/plant_<id>/1/`
  (spec, built `.glb`,
  preview, its own locale
  keys, one additions file
  per built-in place) after
  the spec, render,
  Finnish, region and
  laid-over-world gates,
  and the job goes
  `published` with `pack`.
  The reviewer staging, the
  merged locale and the
  catalogue step are gone;
  the worker's reviewer
  wait with them. Pack ids
  are kind-prefixed on both
  paths: the enrich item id
  would have published
  `packs/enrich_X/`, never
  a live pack yet.
- .4 (`4b23f5a`): a shipped
  place request files its
  `missing` itself:
  `replay_place` and
  `record_place` take a
  store, and the local
  place build wires
  `--project` through.
  Without a project the
  replay stays keyless.
- Evidence: a plant pack
  test whose manifest
  verifies and whose
  additions add only the
  coral to `plants.water`,
  with the biome file
  byte-identical; a replayed
  place test that writes
  `places[tile]`. Suite:
  1057 passed, only the
  known stale-checkout
  failure.
- Also in this push:
  `b602e8f` finishes #187
  (the rights wording
  missed `7009782`'s
  staging; LICENSE alone
  went in it).

## 2026-11-01: inbox #187
  (builder 7009782)

- `LICENSE` is gone; the
  README's Licence section
  carries the all-rights-
  reserved wording, back to
  2026-11-01.
- Data credits stay in the
  README and
  `data/habitat-grid/
  README.md`, as their own
  licences require.
- `pyproject.toml` loses its
  `license` field and the
  open-source description;
  AGENTS.md drops
  open-source too. No other
  licence or DCO wording in
  the tree (task-log history
  aside).
- #174.3 needed no change:
  no `git commit -s` in code
  or docs, and commits carry
  no sign-off trailer.

## 2026-11-01: inbox #186
  pre-report, waiting on
  $0.60 (builder 048cce1)

- All 16 tiles round the
  three places plan on
  z10 (builder `048cce1`,
  world `ec0d9d0`): Nuuksio
  8 (5 forest, 2 sea, 1 open
  ocean), Saimaa
  4 (lake), the reef's 4
  neighbours (2 sea, 2 open
  ocean).
- 14 tiles are code-only
  at $0: no items, only
  `improve` suggestions the
  remake does not build.
- 2 reef tiles cost money:
  `z10_0928_0560` and
  `z10_0927_0559` each want
  animal `common_tern`
  ($0.15) and plant
  `blue_mussel` ($0.15).
- Total: $0.60. The tern is
  an item on both tiles; if
  the first build files it,
  the second tile replans
  without it and the real
  spend is $0.45.
- Waiting for word before
  any tile that costs money.
  Nothing built, nothing
  published yet.
- Evidence: `048cce1` thins
  plant densities to fit the
  world ground budget (three
  halving retries, then loud
  as before), which unblocks
  `z10_0926_0560`'s probe
  (0.68 + 0.24 grasses over
  1.0). Every plan's probe
  makes its tile, so all 16
  make cleanly.

## 2026-11-01: inbox #185 (builder d8e1f65)

- The worker image mounts
  `z10.json.gz` at `/grids`
  instead of the deleted EASE
  files, and `seas.json.gz` at
  `/src/builder/data`, where the
  open-sea naming reads it.
- The seas file is rebaked for
  z10 (634,726 cells): the old
  EASE-keyed file named nothing
  on the new grid, on a Mac no
  less than on the worker.
  Aliases, places of interest
  and country names stay out of
  the image: no plan reads them.
  The staleness is worth one
  line: `data/species` still
  holds EASE-keyed caches, which
  never hit on z10 and quietly
  refetch.
- Evidence: a test weighs every
  file the image names against
  this checkout (and fails EASE
  back in), and a reef-side
  open-sea tile planned from an
  image-like layout gets Great
  Barrier Reef for its sea.
  Suite, ruff and gitleaks
  green. Ready to redeploy.

## 2026-11-01: inbox #184 (builder 6ddb637, fad4243, 30cd6dc, 6cd0eb7)

- Reef rock counts as water
  ground (#184.1): the enrich
  converter and seating, the tile
  keep test and parts, the draft
  region's parts, and
  closest_plant (exact grounds
  first, then reef across
  seabed). A reef spec lands in
  plants.water.
- A new plant keeps its start
  plant's grounds (#184.2): the
  brief carries `from`, code
  copies grows.on, so a coral
  from a coral grows reef and
  grasses keep the seabed. Layered
  cover and water ids build too,
  or an enrich plant on a
  biome-place never starts. The
  home choices offer on reef.
- The plant joins the biome,
  then flatten (#184.3): 0.2 for
  a coral on the rock, 0.1
  otherwise, one rule for laid
  places and filed biomes. The
  biome diff weighs the one
  entry; the flattened homes/
  copy carries it. Proven with
  the real flattener.
- A clean plant item stages what
  a reviewer would commit
  (#184.4): Finnish, safety, the
  render review, the region rule,
  and the world's checks on the
  filed state, where the filed
  region resolves the edited
  biome, budget and all. The
  ship/ set holds the model,
  preview, biome or region,
  homes, merged locale names and
  catalogue. No pack: the game
  reads no plant packs, so the
  job waits for a reviewer and
  ships the old way.
- Kai, three things before the
  live run goes green. First,
  the reef budget: the rock
  holds 0.98 already, so
  table_coral at 0.2 breaks the
  world's enforced 1.0 (proven
  with its own validator); the
  ship stops loud on it. Say
  whether to rebalance, raise
  the budget, or lower the
  coral. Second, the world's
  adapt_home drops on reef (it
  maps seabed only); one line
  wanted there. Third, pull the
  world checkout: origin holds
  the region-schema fix, and the
  whale .glb still wants its
  local build.
- Evidence: suite 1049 passed,
  12 skipped; ruff and gitleaks
  clean. `check --world` still
  red on the stale checkout.

## 2026-11-01: inbox #183 (builder 5a82477, 10f2233)

- The safety review survives a
  cut-off answer (#183.1): one
  short reason per verdict, no
  echo of the texts (the prompt
  says it, the schema enforces
  it), big sets in chunks of 40,
  and a cut-off answer splits
  its texts in half and asks
  fresh, never resending the
  same prompt. The cap stays:
  8000 is the Baseten host's own
  ceiling. A replay of the
  hammerhead safety prompt now
  writes valid verdicts.
- A fix round adds the mark that
  makes a missing shape (#183.2):
  the edits answer gains the
  tool's marks, and the miss note
  sends shapes there instead of
  to paint. Head marks are judged
  in the FRONT and TOP views, and
  a mark the review cannot see is
  missing, never a false shown.
- Hammerhead seeds the hammer
  mark from its name, so the
  first spec carries the part,
  always, checked before any fix
  round (#183.3). Sawfish and
  swordfish stop loud for the
  reviewer to add their mark to
  the tool, and never build
  without the part. Say the word
  if those two should ship
  unmakable-reported instead, or
  if their part names should be
  other than `saw` and `sword`.
- Evidence: suite 1031 passed,
  12 skipped; ruff and gitleaks
  clean. `check --world` still
  red on the two world-side
  breaks from #182's reply.

## 2026-11-01: inbox #182 (builder b5f7692, cc126cc, a27cc51, 58eda73)

- `builder/grid.py` is the zoom
  10 slippy-map grid (#182.1):
  `z10_XXXX_YYYY` ids, a lat/lon
  box, neighbours with a wrapped
  date line, 8x8 groups. No
  pyproj. It meets the game's
  three check points, and a
  point on a line falls south
  or east of it.
- One baked grid (#182.2):
  `z10.json.gz`, all 1,048,576
  cells, coast samples north-up;
  the presence cell is the
  tile's box. The EASE files
  are gone.
- Tiles are true north-up
  squares (#182.3): no
  `grid_north`, edges and coast
  in true directions, each side
  sample-equal to its one
  neighbour, `center` at row
  `y + 0.5`.
- Groups at `tiles/v2/z10/`,
  the index at `tiles/v2/`
  (#182.4); the tile, group and
  index contracts follow. Game:
  please copy them into
  `tools/contracts/`.
- `place.py` reads a region's
  `grid` as `z10` cells
  (#182.5).
- Point 6 (re-make round the
  places) waits for Kai's go:
  no live run yet.
- Evidence: suite 1023 passed,
  12 skipped; ruff and gitleaks
  clean. `check --world` is red
  on two world-side breaks, the
  same on 5210119: the region
  schema takes ease2 row 720
  (f02a3a5 widened every grid
  to 1023), and the humpback
  `.glb` is missing (24abe76),
  which fails all 72 broken
  cases. The Helsinki test tile
  moved one cell east, out of
  Nuuksio's new place cells.

## 2026-11-01: inbox #181 (builder 320cb9c, f492969, 09a05a1, 39f304b)

- On a place with a biome, every
  cast seats in `species` only
  (#181.1): maker, home and
  species plan all test the
  biome now, so no stray
  `animals` key reaches the
  region check.
- The edit rounds leave unbuilt
  plant models out of their
  problems (#181.2): the sandbox
  never builds them, so the
  rounds spent themselves on a
  region check that could not
  pass there.
- The render review's marks run
  inside the edit rounds
  (#181.3): the answer's key
  marks, or the research's when
  it names none. A missing mark
  blocks the round for a fix
  while budget lasts, and fails
  the job only when the rounds
  are used up.
- The file's game length is the
  researched typical adult
  (#181.4): the game rescales
  the model to it, so the
  build's mesh reading (fins
  read long) never stands. The
  measured line stays as the
  #140.3 tripwire; unsourced
  lengths keep the measured
  value.
- Evidence: an enrich replay on
  the reef region ends with no
  `animals` key; a markless
  answer still gets its research
  marks judged; the lynx files
  1.1 m and a 120 cm prey files
  1.2 m; suite, ruff, gitleaks,
  `builder check --world`.

## 2026-10-29: inbox #141, no species call for open water (3)

- The species answer resolved before the open-water
  return, spending a call on tiles nobody will open. It
  resolves after it now; blocked plans take the compass
  name, free.
- Evidence: an open-water plan with a spying client makes
  no call; suite, ruff, gitleaks.

## 2026-10-29: inbox #141, open water plans as blocked (2)

- Open-water borders planned with no items and terns in
  `coming`, and an Approve failed at once. They now plan
  as `blocked: open_water`: nothing to build, nothing
  coming, a field the game shows as "nobody can go there
  yet". An approved blocked plan fails with its reason
  instead of `no_items`.
- Evidence: a blocked-plan test, a no-improve test, a
  worker refusal test; suite, ruff, gitleaks.

## 2026-10-29: inbox #140, take lays the built model for the check (2)

- `take` failed its own draft check on the built `.glb`:
  the job collects it under `models/`, the world's check
  wants it at `assets/animals/`. Take now lays the
  bundle's `.glb` files there before checking, from what
  it downloaded, never required from the world path (the
  world never commits `.glb`).
- Evidence: a take against a `.glb`-less world passes
  with the laid model, and fails without it exactly as
  production did; suite, ruff, gitleaks.

## 2026-10-29: inbox #140, the tile maker takes the reviewed sea home (1)

- `home_for_cell` took only the home literally named `sea`
  for sea cells, so `sea_717` never applied and the tile
  stayed `no_home`. It now takes any home of habitat
  `sea` listing the cell's ecoregion, preferring one that
  lists it over the generic `sea`.
- Evidence: with world `d009df2`, `n_415_479` makes a
  ready tile from `sea_717` with the herring gull
  playable; suite, ruff, gitleaks.

## 2026-10-29: inbox #135, the species call names the square (5)

- The species call now answers a short child-friendly name
  in English and Finnish in the same call, strictly
  required; the cache keeps both. Plans carry the real
  name ("Helsinki shore" / "Helsingin ranta"), areas
  follow from the plan. Without names the compass names
  it as before, and a name never stops a plan: any
  species failure falls back to the compass.
- The planner resolves the answer once and shares it
  with the code-only probe; old cache entries without
  names still load and read compass until re-fetched.
- Evidence: fixture and recorded-answer name tests, a
  compass-fallback test, cache round-trip names; suite,
  ruff, gitleaks.

## 2026-10-29: inbox #139, build-first retries in code (3)

- When the home check fails only on "build it first",
  newhome builds what it names (tree specs too) and
  adapts again with no model call. Still missing after
  the rebuild stops as the worker's job ("the worker
  could not build it"), never "a person makes it".
- Evidence: a no-fix-round retry test and a worker-owned
  failure test; suite, ruff, gitleaks.

## 2026-10-29: inbox #139, the worker builds the plants (1-2)

- New homes failed on the worker because a fresh clone
  holds no built plant models. Chose the build: newhome
  ensures every named plant's `.glb` before adapting,
  built with the image's Blender and cached per world
  commit on the jobs Volume, so a restart only copies.
  Relaxing the world-owned check from here was the other
  way; building is what the script is for.
- The sea home adapted from the forest again: the planner
  now prefers the same habitat anywhere over the nearest
  home of another (no ring cell matches sea, so rings
  alone could not fix it). A sea border near Nuuksio
  adapts from `sea`.
- Evidence: a fresh-clone home test with the real adapt
  tool, a cache restore test, a same-habitat planner
  test; suite, ruff, gitleaks.

## 2026-10-29: inbox #135, first production round trip (1-3)

- The sea tile's home failed on a stray space ("not
  ' #5a4433'"): choices strings are stripped before the
  checks now, so no fix round is spent on whitespace.
- The fix round came back cut off mid-JSON: maker asks
  retry once with doubled room on an explicit length
  signal instead of failing the item.
- The gull and the grass failed at $0 without starting:
  they needed the failed home, so they report `skipped`
  with "needs home_717" in their reports; `failed` is for
  items that ran. A job of only failed and skipped still
  fails, naming the failed cause.
- Evidence: strip, retry and truncated-recording tests;
  a skipped-item worker test; suite, ruff, gitleaks.

## 2026-10-29: T39, a recorded border request replays with no key

- `builder request build <tile> --replay <dir>` rebuilds a
  recorded request locally: no key, no model cost, no
  Firestore; the job lands under `--out` laid out like a
  worker's, report per item. `--record <dir>` plans live
  and records the build (spends real money). Food builds
  by code either way; the worker and the replay share the
  intake (`prepare_item`).
- The scaffold is a valid region now (`zz_work.json`, the
  tile's centre, the home's sources), and a replayed fix
  round continues its stage's answers instead of starting
  over (the recorder appends, so must the replay).
- `examples/n_401_434` is the recorded tundra border (home,
  animal, food, find), scrubbed and committed; the README
  replays it. Local Blender 5.2.3 segfaults in Metal GPU
  detection before any script runs, so the model builds
  were verified up to the Blender step here; a working
  Blender completes them.
- Evidence: the keyless replay run; 5 replay tests, 2
  fix-round record tests; suite, ruff and gitleaks clean.

## 2026-10-29: inbox #134, the kangaroo tail within 10% (T94)

- The tail was never short: the tool's T94 reach (each part's
  centre distance from the tail base plus its half extent
  along its own line) measures exactly 1.0 m on the sized
  kangaroo. The builder's old z-span ruler read 0.87 m and
  could never agree, so `_measure_spec` now mirrors the
  reach, rotations included, and the back-leg shoulder skip
  with it (the tool leaves shoulders alone on upright
  specs; the builder no longer demands or rescales them).
- The kangaroo test asserts length and tail within 10% like
  the other sizes, the tail exact to a millimetre, and no
  size misses; rotation cases pin `_part_axis_along` to the
  tool's answers. Suite: 99 maker tests pass.
- Evidence: the sized kangaroo through real code sizing;
  part-by-part reach against the tool's own functions.

## 2026-10-29: T85, the README claims no Image or SAM

- Closed the inbox's other way: neither fits. The queue and its
  cards are the game's, so land pictures have no builder home;
  judge builds run the one-shot maker, which adapts from
  `start_from` and never reads reference sheets (wiring the
  sheet-plus-SAM loop in would mean replacing the maker with the
  old grown-up loop). `draw` and SAM survive only in
  `ReferencesStage`, which no worker or CLI pipeline runs.
- The README names no Muse Image, SAM, sheet or draw anywhere a
  judge could read as a model claim ("preview sheets" in T41
  means the T84 preview packs). Done when holds: the README
  claims only what a judge can see.
- Evidence: case-insensitive search for image/sam/draw/sheet/
  picture/segment/reference_sheet over the README, each hit
  read; `draw(` has no caller outside `references.py`.

## 2026-10-29: T79, no addon or web left in the builder

- T48 already took it all out: no `godot/` or `web/` directory,
  and nothing matching `godot|addon|web/admin` in tracked code or
  tests (only stale bytecode under ignored `__pycache__/` and
  third-party `node_modules/`). Docs mention them only as
  history, plus the README's stated absence ("No web pages, no
  game addon, no Firestore rules"). `firebase/` holds the
  Storage rules block plus its emulator tests, no Firestore
  rules. Nothing to remove; this entry is the check. The game
  half (its checks pass without the addon) is Claude's.
- Evidence: the search above over tracked code and tests;
  `git status` clean.

## 2026-10-29: inbox #132, D19 a new reference once per body plan

- D19: an animal whose list entry has `"shape": "new"` loops a
  free spec with `ModelsStage(reference="none")` (at most 3
  rounds, the world's checks on every one) instead of the edits
  call. The loop gets the closest spec, the sourced numbers and
  the research's looks, and the kind (bird or mammal) from the
  start spec's wing parts. Every other animal keeps the edits
  call. The list has no `shape` yet (the reviewer's T94); until
  then nothing routes here.
- Once a round passes, the spec is written as the reference and
  code sizes it with only length, shoulder and tail from the
  sourced numbers: no factors, no AI call. Then it builds, and
  the size checks run without the leg-to-body ratio. What still
  misses (the lab's kangaroo tail, 72cm of 100) goes to review
  with the misses listed, not to a fix call. A loop with no
  passing round keeps its last render, marked "not passed" with
  the reviewer's blocking issues, for the person to decide.
- Bird references offer `neck`, `bill` and `bill_depth` (to 4.0)
  when their parts have those names; a mammal is never asked for
  a bill, and the tool refuses one on a reference with none. The
  `model_edits` prompt says the muzzle is a mammal's face and
  the bill a bird's. No `drop`, no word-to-mark mapping, no
  sheets for models; the optional report sheet stays out.
- No live model calls were made: the kangaroo and giant anteater
  sizing runs replay the lab's loop-final specs through the real
  adapt tool ($0). The anteater lands within 10% on all three
  sizes; the kangaroo's length and shoulder do, and its tail is
  asserted listed at 72 of 100cm.
- Evidence: `uv run pytest -q` → 680 passed, 12 skipped;
  `ruff check` + `format --check` clean; `gitleaks git` clean;
  `builder check --world` skeleton on.

## 2026-10-29: inbox #131, D18 tile species from Muse Spark

- D18: `make_tile` takes its species from one Muse Spark call per
  tile (`builder/species.py`: `muse-spark-1.3`, low effort,
  16,000 tokens, strict `{"place", "present"}` with the list's
  ids as the enum), not GBIF. The habitat filter and the
  food-first picker are unchanged; with no counts the model's
  order ranks, then the id. `sources.species` reads
  `muse-spark-1.3:<date>`.
- The answer's `place` is checked against the grid (sea or land,
  and explicit continent words against coarse boxes); a mismatch
  retries once, then blocks with `species_place` (new in the
  tile-group contract). Checked against all 38 lab answers: the
  only flags are the Everglades bay-wobble runs the lab itself
  calls errors. A land-to-land flip told in local names still
  slips through: the check is a backstop, not a gazetteer.
- Answers cache per tile under `data/species/` (git-ignored),
  with the animal list's fingerprint; a re-run calls again only
  when the list changes. `BUILDER_SPECIES_CACHE_DIR` moves the
  cache (the live worker keeps it on the jobs Volume). Without a
  key, cached tiles still make and a miss fails clearly.
- The planner is untouched: its food, animal and improve picks
  stay GBIF, but its code-only probe makes the tile the worker
  would make, so a plan never calls a tile code-only that the
  model then blocks. `presence.py` stays for the T40 eval (not
  built here) and improve. No live model calls were made: the
  five lab recordings test the decode and the checks.
- The game repo's spec lines saying species come from GBIF are
  the reviewer's to change; the builder's README, AGENTS.md and
  `docs/worker.md` are updated. `tiles make` now needs a Meta key
  for uncached tiles, and the $0 path says so.
- Evidence: `uv run pytest -q` → 671 passed, 12 skipped;
  `ruff check` + `format --check` clean; `gitleaks git` clean;
  `builder check --world` skeleton on.

## 2026-10-29: inbox #133, review of phase 2's first commits

- Point 1: the Modal image sets `BUILDER_OPEN_MODEL_HOST=baseten`
  and `BUILDER_LEDGER_PATH=/jobs/spend-ledger.json` (`WORKER_ENV`
  in `deploy/modal_worker.py`). `docs/worker.md` says which calls
  prefer the open host (`ctx.chat`: the D7 edits and D8 choices
  forms, words, reviews, file writing) and which stay on Meta
  (search grounding, images, SAM), with the Meta fallback.
- Point 2: the Volume commits every few minutes while the worker
  runs (`supervise`), and `builder worker --listen` opens with one
  start line (open host, ledger path and both totals, world commit,
  the off switch). The ledger seeding stays Kai's manual step.
- Found with point 2: the image's `pip install` builds a wheel,
  which ships only `builder/` (no contracts, schemas, prompts or
  `exclusions.json`), so every plan and build would fail on a
  missing schema. The image installs editable instead (one flag,
  tested, in the same file).
- Point 3: each item gets what's left of `budget_usd`, and the job
  stops cleanly at it; `cost_usd` never goes over.
- Point 4: shipped plants check `models/plants/<what>.json` in the
  world (the .glb and preview .png are optional extras).
- Point 5: claiming a job snapshots the approved items to
  `jobs/<job>/plan.json` (first write wins); build, ship and
  preview read the snapshot, so a re-plan strands nothing. Jobs
  claimed before this fall back to the live plan.
- Point 6: the ship path is tested at last: reviewed to published,
  `not_accepted` dropped while the rest ship, a private preview
  rewritten after a review.
- Point 7: `make_tile` blocks excluded cells with `excluded`, and
  the planner writes no items for them. The core team lists tile
  ids under a `tiles` key in `exclusions.json` (left untouched:
  agents never add to it); a corrupt file fails loudly.
- Point 8: `coming` holds species ids; shipped entries keep their
  progress rows and the game's `review` says what shipped; animals
  without a `start_from` get no item until D19's `newshape`; a
  find adapts from a filed plant of its food kind, else the first
  filed. After a ship only the border tile is made again: the
  tiles-near-the-area re-run waits for phase 3's bulk run, which
  is where `--near` lives.
- Point 9: the README needs nothing yet (no sheets or SAM claims
  in it; the animals line changes with #131; the `[TO FILL]`s wait
  for measured Modal runs). The logs rule is now in `AGENTS.md`:
  release assets after Kai's review, never in git.
- Evidence: `uv run pytest -q` → 661 passed, 12 skipped;
  `ruff check` + `format --check` clean; `gitleaks git` clean;
  `builder check --world` skeleton on.

## 2026-10-29: inbox #102/T30, the README for judges

- T30: `README.md` rewritten for 0038 (the grown-up draft stays
  out): Try it, the For judges table, How it works, Use of Meta
  AI, the $0 path with no key, Built during the event, measured
  costs, Run it yourself, To finish. Every fact checked against
  this tree: model ids and hosts, stages, contracts, the lab
  run's counts and globe, grid sources and licences, tags and
  the first commit. No Muse Image/SAM claims (T85 doesn't fit);
  no recorded replay (retired with the grown-up builder); only
  evidenced numbers, the rest [TO FILL].
- Evidence: `uv run pytest -q` → 643 passed, 12 skipped;
  every relative link resolves.

## 2026-10-29: inbox #118 (phase 2, T86 the session logs)

- T86 revised: the logs first landed in `muse-logs/` and were
  pulled from git the same hour (leak review: the work domain,
  a masked work address, a home-folder listing with other
  project names). They ship as a GitHub release asset instead,
  after a blocklist re-scrub Kai reviews — never in git again.
- Evidence: `gitleaks git` clean.

## 2026-10-29: inbox #118 (phase 2, T77 Modal)

- T77: `deploy/modal_worker.py` (shape A, the unchanged listener,
  always on): Debian slim, Python 3.12, the world CI's Blender
  libraries and Blender 5.2.2 with mirror fallback, the builder
  from the repo, the baked grids; no keys, no job folders. Jobs
  on the `animundo-jobs` Volume, secrets `animundo-builder` and
  `animundo-firebase`, daily restart, max one container. The
  world is cloned fresh at container start, and the worker pulls
  it before every tile it makes (#99.4). Kai runs `modal
  deploy`. #99.5: the old replays retired with the grown-up
  builder; the $0 end-to-end is the code route (request to
  published tile, no model call), covered by tests. A fresh
  recorded animal build still needs Kai's go (~$0.15).
- T85 doesn't fit, so the README claims nothing: the queue and
  its cards are the game's (T75 moved), and judge builds run the
  one-shot maker, which adapts from `start_from` and never reads
  reference sheets; wiring the sheet-plus-SAM loop in would mean
  replacing the maker with the old grown-up loop.
- Evidence: `uv run pytest -q` → 643 passed, 12 skipped;
  `ruff check` + `format --check` clean; `gitleaks git` clean;
  `builder check --world` skeleton on.

## 2026-10-29: inbox #118 (phase 2, T84 the preview pack)

- T84: a `private` job's versioned preview pack under
  `previews/<job>/<version>/`, only its judge opens it. The
  job's own `visibility` drives the worker; the pack is written
  when items check and rewritten as reviews land.
- Evidence: `uv run pytest -q` → 640 passed, 12 skipped;
  `ruff check` + `format --check` clean.

## 2026-10-29: inbox #118 (phase 2, T76 finished: plant builds)

- Plant items build through the plants step: the brief carries
  the home's human names, the scaffold names the plant in its
  ground cover, and a code-only flora survey grounds the draft.
  The plants step skips items naming no plants (no survey, no
  sandbox, no model call), so home and animal items are
  untouched.
- Evidence: `uv run pytest -q` → 636 passed, 12 skipped;
  `ruff check` + `format --check` clean.

## 2026-10-29: inbox #118 (phase 2, T76 the worker, partial)

- T76: the worker builds jobs (`builder/build.py`, `worker.py`):
  expand items first (a new home, then food, then animals), each
  through the kept stages; improve items after; resume by stage.
  The off switch stops new starts and in-flight builds at the
  next stage. Spending alerts at 50/80/100%; failures alert
  loudly. Items charge their tracker's delta; a failed item never
  stops the others. The job's own `tile` drives the worker;
  nothing is guessed from plans. Partial: plant items fail loud
  at $0 until plant builds land.
- `builder take <job>` lays checked items out like animundo-world
  and runs the world's `check_draft.py` on each item; the report
  lands next to the taken files.
- Evidence: `uv run pytest -q` → 631 passed, 12 skipped;
  `ruff check` + `format --check` clean; `gitleaks git` clean;
  `builder check --world` skeleton on.

## 2026-10-29: inbox #118 (phase 2, T74 the planner + find item)

- T74: the planner by code (`builder/plan.py`, `request plan`):
  the home from the nearest ecoregion, one animal (none when one
  already serves), one food, one thing to find (the home's first
  named plant without a file, a `plant` item), at most two
  improve items; estimates from `prices.py`, names from a
  compass off Nuuksio and Saimaa. Requests re-plan when the
  world commit moves. A live tundra tile plans home, animal,
  food and find with estimates.
- Evidence: `uv run pytest -q` → 593 passed, 12 skipped;
  `ruff check` + `format --check` clean; `gitleaks git` clean;
  `builder check --world` skeleton on.

## 2026-10-29: inbox #117 (phase 1, one code-made tile), pushed untagged

- Tile contracts require `ecoregion`/`level`/`height` plus an
  optional `blend`; ocean tiles carry an ecoregion too. Grid
  cells hold both ecoregions and shares, biome, land, water and
  elevation. The picker is food-first with the world's
  `check_tile.py` as the gate; empty tiles report `no_animal`
  honestly instead of failing.
- Playable is spec-literal (spec 04): every kept animal whose
  file is playable is playable, even under prey; the Nuuksio
  tile keeps fox, moose and mountain hare playable, Pallas the
  fox. Test pinned on the Nuuksio tile.
- The full bake (518,400 cells per grid) is committed under
  `data/habitat-grid/`; the lab ran 21 spots (2 ready, 3
  `no_animal`, 16 `no_home`) with counts and the globe in
  `docs/tiles-lab/`.
- Evidence: `uv run pytest -q` → 591 passed, 12 skipped; `ruff
  check` + `format --check` clean; `gitleaks git` clean;
  `builder check --world` skeleton on.

## 2026-10-28: inbox #115-#116 (phase 0), tagged `v0.4.0`

- T47: the nine shared contracts as JSON Schemas in `contracts/`
  (tile, tile-group, tile-index, border-note, border-request,
  admin-action with the new actions, area, judge-build,
  known-world), draft 2020-12, `additionalProperties: false`
  everywhere. One valid and at least two broken examples each in
  `tests/examples/`; `border-request` refuses every player field
  tried. Old draft, feed-entry, place-public and area-index
  schemas gone; `pack-manifest` kept for judge previews.
- T48: the grown-up builder removed. `web/build/` gone (places
  data moved to `data/` for phase 4 landmarks); Firestore rules
  rebuilt for `areas`, `border_requests`, `judge_builds`,
  `judges` and the new `admin_actions`, Storage rules for
  `previews/`; worker stripped to listener plus atomic claim
  (border flows land in phase 2); CLI `build`/`resume`/`submit`
  gone with intake and submit; admin panel on email and password
  with the request queue; addon down to the `?draft=` opener and
  pack install. README and AGENTS.md say the grown-up builder is
  gone and why.
- Evidence: `uv run pytest -q` → 551 passed, 12 skipped; `ruff
  check` + `format --check` clean; `gitleaks git` clean; 3
  headless Godot checks 0 errors; `builder check --world`
  skeletons on; Firebase emulator tests rewritten for the new
  rules (CI runs them; the sandbox blocks emulator ports
  locally).

## 2026-10-28: inbox #87, #89 (D11) + #92 (D12) + #97, #98, #100, tagged `v0.3.5`

- #87.1: `scripts/places_of_interest.py fetch` wrote
  `web/build/places-of-interest.json` from Wikidata: 12,514
  places (short keys, 4 decimals), 372 inside cities dropped
  with the urban polygons.
- #87.2: `suggest` confirms places per animal (the lab's WILD
  filter, MIN_SIGHTINGS, sampling plus one page per top-10
  country, rank order) into `web/build/animal-places.json`.
  Resumable, waits out GBIF rate limits. NOTE: pushed partial
  first so screens stay testable; the data commit completes it.
- #87.3: the picker: ready-in-country cards, 2 best in the
  grown-up's country, ready elsewhere, best elsewhere (up to
  6), ranked log(sightings+1)xlinks. Near-you outranks
  foreign-ready, or #87.9's moose+CA Banff-first contradicts
  #89.1's Saimaa-ready-for-moose (both tests pass as written).
  Search filters locally; unconfirmed places get one live
  GBIF count; only 5+ sightings build.
- #87.4: country from the time zone (`tz-country.json`, 312
  zones, Helsinki FI Toronto CA), saved choice on
  `/grownups/{uid}` (new rules branch, country only), never
  the location prompt.
- #87.5: intake takes `place: {wikidata_id, ...}` instead of
  a cell; everything from the committed list. Region center
  is the median of one sampled page; id `<cc>_<slug>`
  (`fi_saimaa`); `aq_` south of 60S, `int_` elsewhere (#89.2).
  Research and home-choices prompts name the place and kind,
  byte-identical for old briefs (habitat still decides).
- #87.6: built match by Wikidata id, 25 km rule dropped;
  #89.1 `place-aliases.json` (Q192770 is fi_saimaa).
- #87.7: the map is gone with its code and tests (Leaflet,
  tiles, land/coast/country shapes, `builder/countries.py`).
  Only `ne_50m_urban_areas.geojson` stays, for the script.
- #92 (D12): Your animals is the start page (New animal
  first, drafts newest-first, no drafts goes straight to
  New); per-state buttons; published Play opens
  `?place=&animal=`; preview picture; rounds left on Improve.
- #97: rules bound `place_id` (region pattern, 64),
  `place_name` (80) and Improve-other `species` (id, 64),
  with too-long/bad-shape tests. The app truncates the title
  (one 91-char name exists) and caps species at 64. NOTE:
  the `^[a-z]{2}_` pattern rejects future `int_...` reuses;
  kept as specified, flagging it.
- #98: the opener returns `{place, animal}` (manifest
  `animal`, else the playable species whose taxon key is the
  draft's); every step goes through `draft_step`; emulator
  Play opens localhost with `?cloud=emulator`. PackRules
  checks the optional animal; pack-manifest schema updated.
- #100: `last_error()` keeps the failed step with its HTTP
  status (`packs/<id>/1/manifest.json: 403`); proposed
  game-side plug: `BuilderHooks.draft_opener_error()`.
- #103: the Fireworks host, its key and the configurable base
  URL are gone; unknown hosts fail clearly; every model call
  is api.meta.ai or inference.baseten.co (GBIF is data, not
  models). modal-glimmer stays raising.
- #104: `?judge=1` shows a password form (EN + FI strings, a
  Finnish browser overlays `i18n/fi.json`, rest falls back to
  English); one error only, password never stored; kept
  through sign-out; emulator test button untouched.
- Runnable now: `builder build --kind animal_home --animal
  fox --poi Q1815268 --world ../animundo-world --dry-run`.
- Evidence: `uv run pytest -q` → 658 passed, 12 skipped;
  `ruff check` + `format --check` clean; `node
  tests/web-harness.cjs` 79 checks pass; 3 headless Godot
  checks 0 errors; `check` CI green, then tagged.

## 2026-10-28: inbox #94-#95 (worker store + recordings), tagged `v0.3.4`

- #94: `_claim_requested` had been pasted inside the class, so
  every method after it became a dead nested function and the
  live worker died at `watch_drafts`. It now lives below the
  class; `STORE_METHODS` lists the 21 worker-called methods and
  `test_stores_expose_every_worker_method` checks the real and
  fake stores expose them all (it caught the fake missing both
  watch stubs).
- #95: replay recordings
  (`tests/fixtures/replays/*/records/research.jsonl`) were
  ignored by `*.jsonl`, red since ab01189. `.gitignore` now
  un-ignores fixture jsonl; scanned for keys, tokens, headers
  and emails (clean); `jobs/` and the ledger stay ignored.
- `v0.3.3` stays as pushed; this ships as `v0.3.4` on green
  `check`.
- Evidence: `uv run pytest -q` → 648 passed, 12 skipped; `ruff
  check` + `ruff format --check` clean.

## 2026-10-28: inbox D10 (#82, #83, #85.2, #86, #88), tagged `v0.3.3`

- #82.1/#88: `web/build/places.json` synced from the world (2
  places); a tap is in a place within 25 km of its centre (one
  constant each side: web `PLACE_KM`, intake `PLACE_KM`).
- #82.2/#86: both exist → no draft; the card says "Ready:
  <place>" with Play opening `?place=<id>&animal=<id>` (game
  c685a04). Ready draft cards Play the same way.
- #82.3: a `place:` draft reuses the built region: intake checks
  the place (unknown / outside 25 km / already playable
  refuse); the pipeline runs `reuse` instead of `newhome`/`home`
  (copy region, seat playable, feed from the research, plan with
  the picked); the pack carries the region under the place id;
  submit PRs against the built region file ("Update place").
  The brief keeps the reuse target in `reuse_place_id`: `home`
  later writes the made id to `place_id`, and old live jobs
  already carry it.
- #82.4: a new home seats GBIF neighbours from one facet search
  over the cell (`facet=speciesKey`, shape probed live), kept
  only with a sound taxon (the check_taxa per-file rules) and
  food in the template; the picked stays first in `playable`;
  cap 8. The replay food expectation changed with it: an unfed
  neighbour is dropped, not fed.
- #82.5: cards read "{Animal} in {place}" with "making… about
  40 min" from the request, "Ready to play · <today/yesterday/N
  days ago>", or "Failed, try again".
- #82.6/#83: Improve remapped for `animal_home` (looks →
  `oneshot` D7 fix, colours → `home` D8 fix, too few → neighbours
  at cap 12, other routed by "which animal"), then `pack` only:
  a fix changes the strict form, never game text. Rounds reset
  the touched stages (production re-runs skipped them as done).
  The note reaches the fix prompt as the grown-up's words (300
  chars) with the children's rules. Library animals and homes
  say so; an all-library improve reports "nothing to change
  here" at $0. The Improve heading is "Improve" already.
- #85.2: the welcome reads a fish prey's `kind` like the rest.
- Web sends `place_id`/`place_name` at request time and
  `species` for "other" when named; rules allow both (emulator
  tests for CI; ports blocked here). Contracts let
  `draft_place_id` be a place id; the addon's game-id rule
  already accepts those.
- Suite: 647 passed, 12 skipped; ruff check + format clean;
  node harness green; all 3 headless addon checks 0 errors.
- Open: improve on a reused place says the built place can't be
  changed (too-few there wants re-neighbouring the copy, not
  built); game-side shadowing of a reused region id is the
  reviewer's half; Kai's fox re-pack from #93 still pending.

## 2026-10-28: inbox #93, tagged `v0.3.2`

- Every pack's `draft_place_id` had the Firestore id's capitals,
  which the game's `^[a-z0-9_]+$` refuses, so each pack installed
  and then its region couldn't be found. One helper
  (`pack.draft_place_id`, like the addon's `pack_folder`) now
  names it everywhere: `draft_` + the id lower-cased and cleaned
  + 8 hex of its sha256, so "a-b" and "a_b" still differ. The
  pack (region id, file, manifest) and the draft doc use it; the
  web Play link still carries the raw Firestore id (tested).
- The opener returns "" with a warning when the manifest's place
  id isn't a game id (`BuilderHooks.is_id`), and
  `PackRules.manifest_error` refuses such a manifest, so a bad
  pack fails at the check: nothing registered, nothing on disk.
  The stub hooks grew the same `is_id` guard on `find_file`,
  which is why the stubs never caught this.
- Suite: 606 passed, 12 skipped; ruff check + format clean; all
  3 headless checks 0 errors against stubs and again against the
  game's real hooks in a throwaway game copy (removed after).
- #93.4, for the reviewer (Kai's Mac, no model calls): `builder
  stage pack --job jobs/K8j5PJ8oSZJix8PqYV8P` rewrites `pack/`
  with `draft_k8j5pj8oszjix8pqyv8p_efb9feaf`; upload every file
  under it to `packs/K8j5PJ8oSZJix8PqYV8P/1/<rel>` as
  `worker_store.upload_pack` does (same version, overwrites the
  bad pack); set the draft's `draft_place_id` to the new id;
  the old `regions/draft_K8j5...json` object can be deleted.
- Tagged alone per #93: D10–D12 move to `v0.3.3`.

## 2026-10-28: inbox #90-#91, tagged `v0.3.1`

- #90: Your drafts shows from the first paint when signed in
  (`$("drafts").hidden = !user` in the sign-in handler); only
  the map waits for an animal. Web test: the pane unhides on
  sign-in, every draft renders a card, a packed one gets Play.
- #91: `utc_now()` writes a `T`, not a space, so the game's
  Cloud sends `last_played_at` as a timestamp and the rules'
  Playing branch accepts it. The web app already writes real
  server timestamps; the addon had the only string clock.
- #91: `open_draft` still returns the place id when
  `record_played` fails (logged, not fatal). Checks: `utc_now`
  matches `^\d{4}-\d{2}-\d{2}T...Z$` with no space, and an
  opener whose patch stub fails still opens the place.
- Suite: 604 passed, 12 skipped; ruff check + format clean;
  all 3 headless addon checks 0 errors (Godot 4.7.2).
- Tagged alone per #91: D10 (#82-#84, #86, #88) and D11 (#87,
  #89) move to `v0.3.2`. #92 (D12 Your animals) with #87.

## 2026-10-28: inbox #84 (anaconda fixes; v0.3.1 waits for #82)

- #84.1: oneshot seats and plans every made target per target
  (a prey into `animals.prey`, fish by archetype), and a resume
  heals earlier runs' targets the same way; `animal_files`
  lists all made. The pack ships file and model off the plan.
- #84.2: a hunter's hunts are its seated research prey's tags
  in code, rewritten after every parse, so no fix round can
  drop them or swap prey for plants. Prey with files but no
  seat stops the run before any call (structural, no wording).
- #84.3: oneshot refreshes the welcome from the seated cast
  (before `words`), so it names the made prey.
- #84.4: `home` drops `seasons` within 23° of the equator. The
  `tropical` key itself is NOT carried: the region schema
  forbids extra keys and the game reads latitude, not the key
  (`season.gd`; |lat| < 23 with no seasons block is already
  seasonless). The "summer" word is the game's default name.
  Needs a world schema change to carry the key, if wanted.
- Finding (not fixed, out of scope): `home._cast_files` reads
  fish `file`, but real regions and the game use `kind`, so it
  misses real fish; `species_plan._cast_files` reads `kind`.
- Suite: 603 passed, 12 skipped; ruff check + format clean.
- Not tagged: #84.6's v0.3.1 is #82+#83+#84 now (#81 shipped
  in v0.3.0). #82 waits on the reviewer: `places.json` is not
  in the world yet (exact keys unknown), and the `?place=`
  game commit (#82.2) is not posted.

## 2026-10-28: inbox #80-#81, `v0.3.0` is #72-#81

- #80: the game's draft opener is live (bf3b016, in this
  checkout's history). The #79 opener matches it exactly
  (signature, awaits, folder then place id, `""` on failure),
  so no code changed.
- #81.1: two workers can't build one draft. `handle_create`
  claims `requested` → `queued` in one Firestore transaction
  (the loser returns at once); the draft and the log carry the
  winner's host:pid. Raced twice in tests: two threads, one
  build (stable over repeats).
- #81.2: `looks` writes the empty result at $0 with no call
  when the minset makes nothing (no animal, home or prey).
- #81.3: the build report's total and the draft's `cost_usd`
  come from the job's ledger total (the only lock-guarded
  record; the folder's log is the fallback). New `job_spent`
  on both ledger backends; the per-model breakdowns stay
  log-based.
- Suite: 597 passed, 12 skipped; ruff check + format clean.
- Moved `v0.3.0` to this commit: the tag was minutes old and
  uncopied, and #82 scopes it as #72-#81.

## 2026-10-28: inbox #72-#79, tagged `v0.3.0`

- #76: the GBIF records draw in their own `records` pane (z450,
  taps pass through) above the land: inland Finland reads orange
  for the red fox.
- #74: hexagon bins (`bin=hex`, 8 per tile), taps snap to the
  nearest recorded 0.1-degree cell within ~50 km (box query plus
  expanding rings), and the pick zooms to the records' bounds.
  Live GBIF probe: Helsinki cell 136 records, mid-ocean 0.
- #78: only recorded cells make a card (misses, cities and check
  failures stay silent); the pick highlights; only an animal with
  no records anywhere says "We don't know where it lives yet".
  Raster tiles can't hover-highlight per hexagon; noted, not built.
- #72: the map and the drafts stay hidden until the animal is
  picked; the drafts' only words are "These are your drafts."
- #75: the outline switches by zoom (110m to 3, 50m from 4, 10m
  land from 6), each tier lazy-loaded once; the 10m file is
  rounded to ~1 km (see `vendor/natural-earth/README.md`) and
  its outline doubles as the shore, so no 10m coastline ships.
  Point-in-polygon over the vendored land: Spitsbergen and
  Helsinki land, Fram Strait and the Aland Sea water.
- #73: a prey the minset says will be made counts as seated for
  the welcome ("Go and find a capybara to eat!").
- #77: the model reference falls back to `models/examples/`
  (adapt gets the found path; the looks fall back to the spec's
  name), and a resume skips an animal file whose last try passed.
- #79 (D9): drafts live in the web app. Packed cards get Play in
  the game (`?draft=<id>`, new tab); the addon loses its panel,
  looks and reinstall, both buttons open /build, and the draft
  opener installs one pack for the session (safe folder per id:
  lowercased plus a hash, so `Draft-1_ok` fits the pattern).
  Downloads verify everything before anything lands on disk.
- Suite: 591 passed, 12 skipped; ruff check + format clean;
  `node --check` clean; the three headless addon checks pass
  0-errors (one is new: `check_draft_opener.gd`).
- Reviewer reruns from here: resume `oneshot` on
  `jobs/anaconda-v020`, then `words` and `pack`; copy `v0.3.0`
  into the game. Kai's fox test needs the Storage IAM role
  first (#79.7).

## 2026-10-28: inbox #71 (animal-first panel) and #70 (`file_from`)

- #71.1: the Builder screen opens on the drafts, with one button at
  the top, Make a new home, to the web app. The Places to build tab
  and the addon's `areas.json` are gone (the web app keeps its own);
  dead code went with them (`_areas`, `_area`, `_areas_tab`,
  `_draft_for_area`, the lang toggle and its file).
- #71.2: the panel gets a solid dark StyleBoxFlat, like the game's
  own screens.
- #71.3: the screen reads the game's locale fresh each time it
  opens; the panel's own language button and `builder_lang.txt`
  are gone.
- #71.4: Close is an ✕ button with the word as its tooltip (the
  report form's cancel keeps the word).
- #70.1: the animal file's one-shot example comes from the list's
  `file_from` (the anaconda: the lizard); the model still starts
  from `start_from`. Rows without `file_from` fall back, as before.
- #70.3: `web/build/animal-list.json` already in sync (the `file_from`
  key arrived with the #69 sync; verified again here).
- Tagged `v0.2.2` (#69 + #70 + #71) on green replays and headless
  checks.
- Suite: 582 passed, 12 skipped; ruff check + format clean; the
  three headless addon checks pass 0-errors.

## 2026-10-28: inbox #69 (homes for every habitat)

- #69.1: the choices schema matches the lab's as it is now —
  `land` gains `mountains`, the sea description, required `ice`,
  `caves` and `seabed` enums with the lab's word-for-word
  descriptions, tree shapes plus the shipped library's spec ids,
  and a required `on` (`ground`/`seabed`) per plant. Schema is
  JSON-identical to the lab's `schema(plants, trees)`.
- #69.2: the prompt appends the library tree ids, adds the
  "Special places" paragraph and the no-trees hard rule. Rendered
  ice prompt diffs clean against the lab's (only the real animal
  name, the exact center, and the live plant library differ).
- #69.3: code drops `"on": "ground"` (keeps `"seabed"`) and sets
  `ice` to `"none"` in the tropics. Everything else rides with
  the choices to `adapt_home.py`.
- #69.4: closest home unchanged (`boreal_forest` land,
  `boreal_lake` lake/sea) — the adapt tool replaces what matters.
- #69.5: intake, minimum and newhome put no limits on habitats
  (intake already resolved ice and mountain in the lab jobs).
- #69.6: schema test for the new enums, plus `newhome` replays on
  `jobs/lab-ice-polar-bear` and `jobs/lab-mountain-ibex` (lab
  research + the world's tested example choices through the real
  `adapt_home.py --check`): first-try passes with `terrain.height`
  40 and 64.
- Synced `web/build/animal-list.json` (new `file_from` key).
- Suite: 580 passed, 12 skipped; ruff check + format clean.

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

- inbox 2026-10-28 #105/#111: email rewrite done and
  pushed. 75/75 commits now from the noreply address,
  the old address is gone, trees byte-identical, 10 tags
  moved with the history. Old main 0e0b46c, new main
  82c2009. Verified from a fresh clone. Note: the
  game repo still pins builder v0.3.5 at old id
  0e0b46c in .builder-sync; update at next builder
  sync.

## 2026-10-28: phase 1 tile maker on the #124-#128 contracts (#117, #124-#130)

- The tile maker follows one home per ecoregion (#124,
  #127, #128): the home matches the cell's first ecoregion
  and its habitat from the world checkout, `blend` names a
  second home at share 0.1-0.5, and `no_home`/`no_animal`
  block honestly. Levels come from ETOPO 2022 elevation
  (#125): sea 5, land `9 + round(50 * (1 - exp(-m/1500)))`,
  monotonic and under 64 at 8,849 m; `height` 32 or 64.
- The bake writes the new cell format (#124, #125):
  `[eco1, share1, eco2, share2, biome, land, water,
  elevation_m]`. ETOPO 2022 confirmed public domain at its
  NOAA ISO metadata record on Oct 28, 2026, with the
  citation in `data/habitat-grid/README.md`; Kai's
  confirmation still pending before any tile is published.
- #130's ice replay fix: `_replay_lab_home(..., "ice",
  32)` (world T87 put every home at water level 8).
- Suite green: 570 passed, 12 skipped; ruff check and
  format clean; gitleaks clean; `builder check --world`
  runs (skeleton). Full two-grid bake with the DEM
  launched to temp space (about 5 ms a cell after the
  shapefile load); the lab run (T80) follows it.
- Phase-0 #129 adjustments verified before the push: plan
  and build-job contracts in, area/admin-action/judge-build
  out, no `web/` or `godot/`, no Firestore rules, Storage
  rules cover tiles/review/previews with emulator tests,
  and the worker is a listener plus atomic claim with
  phase-2 seams. No temp ecoregion table anywhere.

## 2026-10-28: food-first species and the world check as gate (#117 T59)

- The first real tile (a taiga cell near Vaasa) failed the
  world's `food` check: the picker kept hunters whose prey
  was absent (kestrel hunts vole, absent) and missed the
  home fish's hunts (pike hunts frog). Per spec/04, the
  picker now keeps an animal only if the home has its food,
  a hunter only if its prey stays too, and the home's fish
  must find their prey in the tile; the maker then runs
  `tools/check_tile.py` and blocks with `no_animal` on a
  food failure (other failures raise, never mislabelled).
- The same tile now passes every world check (tile,
  animals, region, food, edges): kestrel out, frog in for
  the pike, fox kept with all three prey tags covered.
- Suite: 576 passed, 12 skipped; ruff and gitleaks clean.

## 2026-10-28: five spots and the lab run (T59, T80)

- Full bake with ETOPO 2022: both 720x720 grids in 29
  minutes, committed under `data/habitat-grid/`. Nuuksio
  and Pallas are boreal 717, Yasuni rainforest 483, the
  Serengeti savanna 57, Wales land 651 with a sea 651 cell
  next door, all as the inbox says.
- T59: Nuuksio and Pallas are ready and pass every world
  check; the Amazon, the Serengeti and Wales sea are
  honestly `no_animal` (homes exist, no animal files yet).
  The picker had dropped overflow playable animals from
  every list, starving the pike; playable now overflows
  into prey, as the world's example tile does.
- T80: `builder tiles lab` over 21 spots (the five plus
  sixteen), committed with the counts and `globe.png`
  under `docs/tiles-lab/`: 2 ready, 3 `no_animal`, 16
  `no_home`. The globe dots were verified pixel by pixel.
- Suite: 577 passed, 12 skipped; ruff, gitleaks and
  `builder check --world` clean.

## 2026-10-28: every kept playable file plays (spec 04 roles)

- Kai's fix before v0.4.1: on the Nuuksio tile the fox,
  moose and mountain hare sat under prey and weren't
  playable; at Pallas the fox wasn't either. Spec 04 says
  playable is every kept animal whose file is playable,
  with no limit, so the picker drops its 5/8/3 limits:
  playable is every kept playable file, predators every
  kept hunter, prey every kept animal someone hunts plus
  every kept animal in neither other list. Hunted playable
  animals (the hare) appear under both playable and prey.
- `test_nuuksio_tile_keeps_hunters_playable` makes
  n_413_478 from the committed grid and the real world
  with canned presence and asserts the fox, moose and
  mountain hare are playable. Both live tiles remade and
  passing the world's check.
- ETOPO 2022 confirmed by Kai; all three grid sources
  confirmed before any tile is published.

## 2026-10-29: the planner (T74, #118)

- `builder/plan.py` plans a border tile by code: the tile
  maker decides `code_only`; else one home item from the
  nearest home's ecoregion, one animal item (the explorer's
  own unfiled animal first, else most recorded near the
  cell), one food item for a filed but unfed animal, and up
  to 2 improve species. Estimates from `builder/prices.py`;
  names from the habitat and compass direction off Nuuksio
  or Saimaa, in English and Finnish.
- No plant items: nothing in the world grounds a "thing to
  find" before the build's research, and no phase-2 stage
  builds one. Flagged for the inbox.
- `builder request plan <tile>` prints the plan; the worker
  writes it to `plans/{tile}` on every new or changed
  request and again when the world commit moves, behind the
  `config/builder.enabled` off switch, with failures in
  `builder_alerts`.
- Live: boreal next to Nuuksio is `code_only`; a 774
  tundra border plans home_774 from tundra; Wales sea plans
  herring_gull with puffin and grey seal to improve.
- Suite: 591 passed, 12 skipped; ruff and gitleaks clean.

## 2026-10-29: the tile survey (#141.1)

- The species call is now a survey: our facts first
  (habitat and blend, land/lake/sea shares, height range,
  the home to build or adapt from, our plants), then parts
  with animals from the list, the plant to find, and both
  names. One call per tile, as before.
- Code checks afterwards: the place as before, water parts
  against our shares, and the English name against absent
  animals' kinds (a name may paint with "sea" or "blue",
  never name a missing seal). The retry carries the refusal
  back, so the second answer fixes the first. The cache keys
  on the tile, the list and the facts.
- The planner takes the survey's find first: unfiled it
  becomes the plant item (this replaces #140.4's "let the
  species call choose"); filed or missing, the home's own
  missing plant as before.
- `builder survey lab` runs the survey on the six tiles we
  have against the committed old answers; the run is in
  `docs/survey-lab/`. All six pass: finds are bilberry on
  the five land tiles and bladderwrack on the open sea, and
  every name describes its tile's own parts. Along the way
  the checks twice refused and the retry fixed a lake part
  our bake doesn't have; the name rule was narrowed from
  any word to the kind after it refused "blue" and "sea".
- Cost: one survey call measured $0.021 twice against the
  old call's $0.015-$0.018, so about a third over the old
  $0.016 at the same one-call shape; a $0.042 outlier was
  reasoning-length noise (a 150-id answer cost the same as
  an 81-id one).
- Tallinn's facts show the #141.4 problem live: the forest
  tile would adapt from `sea`.

## 2026-10-31: inbox #161, the converter everywhere

Reply with the commit ids, as asked. All on builder
`main`, pushed, against world `a76fc34` and later:

- #161.1 `b365908`: enrich checks run through
  `tools/tile_region.py` in each item's real target tiles;
  `zz_work` is dropped.
- #161.2 `e677691`: failures sort into the model's (a fix
  round) or the place's (reported, no model call).
- #161.3 `fbaf87c`: the tile maker validates through the
  converter too, and refuses a tile check that predates it
  instead of drifting from the game.
- The #147.4 check passed first (squirrel and bee stay
  playable on `n_414_480`); the lynx can resume whenever
  you deploy this.

## 2026-10-31: inbox #162, the humpback cost

Cost against the estimate, as asked (`replay-place-gbr-4`,
14 calls, all in the ledger under `replay/*`):

- Reef home rebuild (deep: water 40, sea 35): 4 calls,
  $0.188 against $0.20. Under, and the home is good.
- Whale item: 10 calls, $0.249 against $0.15. Over
  because `oneshot` failed and the one fix round still
  gave no third-length flippers and no knobbly head, so
  per #162.3 the whale went to `NEW_SHAPE` with no more
  calls spent on it.
- Total $0.437 against $0.35 (+25%). The draft region
  fails only on the missing whale, as expected.

## 2026-10-31: code-review batch #155-#160

One commit each, all pushed, suite 896 passed + 12 skipped
(11 live-model, 1 missing optional SAM parser):

- #155 `6a83b93`: atomic worker ownership, conditional
  writes, dead same-host takeover, 30-minute leases.
- #156 `b450b36`: one combined job allowance across both
  providers, with paired receipts and release-once.
- #157 `b03225a`: timeouts hold their reservations and
  pause the item; only proven-zero errors retry.
- #158 `687447f`: re-enabling re-queues queued jobs,
  interrupted builds and unplanned requests.
- #159 `ffb4d58`: ledger failures stay control failures:
  no fix round, no further items, job reports `ledger`.
- #160 `0afb1a9`: `check --world` is real (tools, data,
  contracts, the world's own seven self-tests: 21/21 on
  world `637e742`); CI checks the world out at a pinned
  rev and runs the compat test instead of skipping it.
- Ledger check: `place-gbr-2` carries the $0.91 fish
  rebuilds, the whale run its $0.437; 59 jobs total
  $21.37 with day sums matching. Who repays the whale
  and the deep-choices spend is still open.
- Not green, pre-existing: repo-wide ruff (978, nearly
  all E501) and format (32 files) fail on `main`; every
  file this batch touched passes on its own.

## 2026-10-31: inbox #166, the lynx runaway

- Why (#166.1): the animal step sent no reasoning cap,
  so Baseten's open model reasoned at default, filled
  the 8k cap, was cut off mid-JSON, filled the 16k
  retry the same way, and stalled a fix round past 60 s
  into a pause. Fix: the four maker asks (animal,
  edits, marks review, home) now send
  `reasoning_effort="low"`, the same remedy the words
  and species steps already use on this host. No host
  move: the model stays short on Baseten once capped,
  so moving to Meta would cost more for no reason.
  Watch item: `low` also applies on Meta now; if maker
  quality there slips, say so and we'll move the call.
- Fit (#166.2): chat timeouts size from the cap per
  host (`timeout_for`): Meta floor 120 tok/s (slowest
  of 22 logged calls was 141), Baseten 80 (one 16k
  call inside ~31 s, margined for variance), +30 s
  slack, never under the old 60 s; explicit timeouts
  still win. Responses keeps its 180 s floor; images
  and streams are not token-bound, unchanged.
- Way out (#166.3): the console writes
  `operator_action: "resume" | "give_up"` on the job
  doc. Resume settles the held spend and the normal
  path goes on; give-up settles it, records
  `uncertain_usd`, and fails the job as `gave_up`.
  Game side needs: a console button doing that one
  direct Firestore write (same path as `admin.mjs`,
  no rules change); `server/border` needs nothing,
  the worker's watch picks the field up. Note:
  restarts and re-enables still re-drive paused jobs
  (#157 behavior), so a deploy would retry the lynx;
  nothing live was touched here, it stays paused for
  Kai's call.

## 2026-10-31: inbox #167 (builder f57b011)

- Own plants are never planned again: common name
  without parentheses, Latin against the plant files,
  and the improved region's ground_cover/plants. The
  reef improve replay plans none of its three.
- Existing regions get no home item: improve adds
  animals and plants only, no terrain change.
- GBIF counts ride in survey.json; replays pass the
  prior survey's back: fast, offline, identical.
- Improve asks the library's habitat animals first
  (residents' habitats, or the cell's); the plan's
  `looked_for` holds the asked-about makeable ones in
  neither the answer nor the place (parrotfish, moray,
  hammerhead, manta on the reef replay).

## 2026-10-31: inbox #168 (builder 24d4ec5)

- Region tiles check in the region file: one
  `<region id>.json` per region, manifested, plus a
  home-lookup fallback to the region. Hammerhead on
  s_530_612 reaches checking; verified against the
  live reef file (hammerhead seated 8th).
- Region tiles publish nothing (.2 was already
  covered: versions without the tile).
- Failed items carry stage and message in the report
  and progress[].error; in-region tiles take the
  region's name.
- Ready for the hammerhead retry once the worker is
  restarted on this commit; Kai decides.

## 2026-10-31: inbox #169 (builder 3840941, 379755a, f1413a8)

- Tropical seas refuse the generic `sea` home (it fits
  boreal and temperate biomes 4, 5, 6, 8, 11): the
  Cairns coast blocks `no_home`; Helsinki keeps
  `sea_717`.
- Plan names point from the nearest hand-made region;
  ties keep the curated name. The Finnish genitive is
  a rule (vowel +n, -e +en, else +in) until the world
  files one.
- Keyless `request plan` prints the plan with the
  probe `needs_survey` and the survey's cost ($0.05
  first guess) instead of exiting 1.

## 2026-10-31: inbox #170 (builder 2e0c53c, dddeace)

- Surveys keep `missing` (survey order, at most 12)
  and `surveyed_at`; replays keep the prior date.
- `builder place file <dir>` files it under the
  place's own tiles as group `places`; tiles and
  index counts untouched. Improve surveys file too.
- Cost per survey from the reef: the live survey was
  $0.11 (6 searches); a replay is $0. A fresh survey
  each for Nuuksio and Saimaa should run about $0.11
  on the Meta credit.
- Spec 02 §2, next to `wanted`, paste-ready:
  `places` (object, optional): hand-made places'
  wanted lists by tile id, filed from the survey when
  the place ships (`builder place file`), so enrich
  can grow them too. `places["<tile>"] = {"region",
  "wanted", "surveyed_at"}` with `wanted` in tile
  `wanted` shape (at most 12). Never under `tiles`:
  the tile stays the hand-made region, and the game
  must not load a code-made tile there.

## 2026-10-31: inbox #165 (builder 0d457a4)

- Every tile event lands a row in `tile_history`:
  made, remade, blocked, left_out, with job, kind,
  maker and world commits, group version (null when
  nothing was written), reason and cost. Writes never
  fail the tile.
- The command line files to the store with
  credentials, else `tile-history.jsonl` beside the
  groups, and says so.
- Spec 02, new collection, paste-ready:
  `tile_history/{auto id}` (append-only): `tile`,
  `event` (made | remade | blocked | left_out), `at`,
  `job` (or null), `kind` (code | build | enrich |
  cli), `maker` (builder commit), `world` (commit),
  `version` (group version after the write, null when
  nothing was written), `reason`, `cost_usd`. Rules:
  admins read, no client writes.

## 2026-10-31: inbox #171 (builder 733870a)

- Paused jobs skip the catch-up, the reconcile and
  `build_job`: a restart leaves the lynx untouched
  and prints that it waits for an operator.
- Resume settles the held spend first, then marks
  items building; stale commands still clear.
- From your side: write `operator_action` on the job
  (`resume` or `give_up`); the worker clears it once
  obeyed. Details in `docs/worker.md`. Safe to stop
  the old container whenever Kai has decided.

## 2026-10-31: inbox #172 (builder 429ca35)

- The crash was `_update_if_owner`: the only update
  path that sent `DELETE` untranslated. One helper
  now serves every update; the test drives the real
  client's own write builder.
- The lynx ledger: `_settle_superseded` runs before
  the crashing write and settles are idempotent, so
  the held $0.077 almost certainly landed on the
  first give-up try. I can't reach the volume from
  here, so verify on the container (files
  `/jobs/spend-ledger.json`,
  `/jobs/spend-ledger-allowance.json` and
  `/jobs/spend-ledger-baseten.json`; pick the
  provider file by `progress[0].uncertain.host`):
  each side's receipt id from `progress[0].uncertain`
  should sit in its file's `settled`, with the
  amount in `jobs[enrich_20261031_animal_lynx].spent`.
  If an id is missing, settle it by hand (safe to
  run regardless):
  `FileLedger(path).settle(receipt_from_ref(ref),
  ref["reserved_usd"])` per side from `builder.ledger`.
- `modal container stop` needs `-y` with no
  terminal; noted in `deploy/modal_worker.py`.

## 2026-10-31: inbox #173 (builder 3e90858)

- Region seating now only adds: the animal joins
  `fish` (or `animals`) and the
  `playable`/`predators`/`prey` lists, and food,
  plants, terrain and everything else stay byte
  for byte. The test diffs the reef before and
  after and allows only those lists to change.
- A job whose checks fail no longer sits in
  `checking` looking passed: the failing checks
  land in `progress[].checks_failed` with a red
  line at the top of the report.
- "Not built" model verdicts split out as
  unknown instead of failing, for the worker's
  fresh clone with no `.glb` files.
- Sea animal rules: migration dates are checked
  against the place's hemisphere, `active`
  unless the sources show it leaving; a big
  fish hunts only `small_fish`; typical adult
  length; a fish count below the small fish.
- The report shows the job's own budget, not
  the $8 default; enrich items without a survey
  say so plainly.

## 2026-10-31: inbox #175 (builder a9a9c34)

- The whale run's $0.358 is in the ledger by
  hand under `replay/animal_humpback_whale`
  (now $0.6073 with the earlier run).
- A new shape can start from `models/examples/`:
  the spec read, the bar preview (now also
  `models/examples/previews/`) and the review
  title all resolve through the maker's
  reference lookup, which moved into the models
  stage (the maker imports it; no cycle). Covers
  `harbour_porpoise`, `grass_snake` and
  `peacock_butterfly` starts.
- A place or enrich item never makes prey. A
  place item's home is the place's own home
  (the survey's `home_from` stem), carried into
  the brief with the region id. When none of
  its food is in the laid place, the minset
  says `no_food` and the report carries it.
- Tests: a new-shape start from
  `harbour_porpoise` reaches its first build;
  the lab whale replayed makes no herring and
  reports `no_food` (its recording runs out in
  the whale's model loop, past everything
  fixed here).

## 2026-10-31: inbox #174, #176, #179 (builder b08c507, 287e095, c878393)

- An enrich item ships itself: the `ship` stage
  (#174.1, #174.2) writes its Finnish (open host
  when set, else Muse Spark), runs the
  child-safety review, gates the world's own
  checks (not-built models unknown, #173.3),
  the render review (marks all shown, none
  unmakable; a new shape's final review
  unblocked), the reef file rules (typical
  adult length within 20%, the research's
  winter and in-season migration dates, hunts
  only reviewed prey tags, a big fish fewer
  than the small fish, the camera behind the
  body and under the water), the English and
  Finnish key for key, and the additions-only
  region change (#174.1, #173.1). A failed
  check fails the item with the check and its
  message, in the report and the progress
  entry; `checks/ship.json` carries them all.
- The pack (#176.1, #176.3, #179) is spec 02
  §8b's shape: the animal file, its `.glb` and
  preview, its English and Finnish texts, one
  `additions/` file per hand-made place
  (top-level lists only, fish exactly kind and
  count), and the §8b manifest. The pre-upload
  check mirrors the game's `files_error`,
  `file_error`, size-and-hash download check
  and `addition_error`. Versions count up; a
  version never rewrites.
- The worker runs `ship` last for enrich
  items, and the review step remakes on the
  gates instead of a person's verdict (#176.2,
  #174.1): the job goes `published` with
  `pack` and the remade tiles' `versions`.
  Old reviewed jobs still remake the old way.
- Verified beyond the builder suite: a
  builder-made `packs/animal_hammerhead/1/`
  installs through the game's real `LivePacks`
  path on fresh game main (headless Godot):
  version 1, the reef gains the hammerhead in
  `playable` and `fish`, everything else
  byte for byte, playable on the reef, clean exit.
- Two judgment calls, both logged in code:
  hunts must be tags a shipped animal already
  hunts (new predator-prey relations go
  through a person), and length within 20% of
  the researched typical adult.
- Not done here: the hammerhead recording
  (#176.4's exact replay input) sits in cloud
  Storage, out of reach with no credentials,
  so those tests run the same ship code with
  an equivalent whale-free item. Plant enrich
  items stop the ship with "ships by hand":
  packs have no plant shape yet.

## 2026-11-01: inbox #177 (builder 6121ec0, ba5f97f, 5a34077)

- Open-sea cells are code tiles now, not
  `blocked (open_water)`: biome and home
  `open_sea`, no ecoregion, coast or blend.
  `land` is the nearest coast's land style
  from `lands/index.json` (the Gulf of
  Finland is `boreal`), `land_share` the
  grid's own land for the cell. `sea` comes
  from the baked `data/seas.json.gz` (every
  named open-ocean cell, smallest NE area
  wins); `name` is the table's `{en, fi}`
  exactly. Unnamed water stays `open_water`.
- Species by code, no Muse Spark: animals
  with `open_sea: true` and a GBIF key,
  counted by the density fetch over the cell
  and its neighbours, kept by the world's
  converter and food rules. Sources cite
  `spec04:open_ocean`, the marine-polys
  table, `gbif:density` and the coast's
  ecoregion number.
- The plan lists them `code_only` and never
  starts a survey for them. The worker makes
  them only within 3 cells of a made tile, a
  hand-made place, or a tile with border
  notes (`border_requests` with notes > 0);
  anything farther fails `too_far` before
  the maker runs.
- Remade `n_414_481` and `n_415_480`
  locally (world 5579041, no live run):
  both Gulf of Finland / Suomenlahti,
  `boreal`, herring gull playable, and the
  world's `check_tile.py` passes both. The
  `n` and `w` edges of `n_415_480` match
  the worked example byte for byte.
- Three judgment calls, all reported: the
  level stays 5 (the sea formula, under the
  water 8), not the example's 2, because
  the shared edges with the published
  neighbours only agree that way; the
  example's `land_share` 0.03 and `e`/`s`
  edges are its own hand values, where the
  remake writes the grid's 0.0 and computed
  edges; "around noted tiles" counts the
  same 3 cells as the made-tile rule.

## 2026-11-01: inbox #180 (builder 8d291e7, e00b7dd, 5978ccd, 42a6354, b12a383, 4402f0f, bf065c5)

- The cap counted stale runs, not open
  reservations (#180.1): every `--record`
  run of an item shared one `replay/<item>`
  ledger id, so the provider counted the
  old $0.61 with the run's $0.29 (the
  $0.90; the settle mechanics reconcile
  exactly, nothing leaks). Record runs now
  mint a run-unique ledger id, so the cap
  compares this run's real spend plus the
  one reservation in flight. The worker's
  stable job ids are untouched.
- A rerun continues stopped model rounds
  (#180.2): minimum says make when the
  animal file is made but its model spec
  is not built yet, and the model loop
  resumes from the last `rounds/<id>/<n>`
  (spec, review issues, check errors,
  now persisted per round), rebuilding
  and re-reviewing an interrupted round
  when its review never ran. A loop that
  used its round limit still stops.
- The plants stage builds nothing for an
  animal item's brief, for $0 (#180.3);
  home and plant runs are unchanged.
- Draft regions leave out treeless
  `"none"` densities, which the schema
  wants as a number (#180.4). Named
  densities WITH trees (mangrove dense
  and kin) still break the schema and
  need a number mapping: saying nothing
  here instead of guessing it.
- The whale's box comes from its last
  built round's spec even when the loop
  never passed, best effort (#180.5);
  the review prompt now checks whale
  head width, one-piece flippers, the
  stalk merging into the fluke, and one
  even blue-grey, and never asks a whale
  for legs.
