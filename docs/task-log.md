# Task log

What ran for each task, and where the evidence is. Newest first.
Job folders live under `jobs/` (local only, never committed).

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
