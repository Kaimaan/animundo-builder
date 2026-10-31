# Running the worker

The worker plans border requests and builds the jobs the game
creates. It holds no policy: no routes, no approval, no caps beyond
each job's budget and the Meta caps.

One-time setup:

```sh
gcloud auth application-default login
```

That gives the worker its credentials; there is no key file.
Then, from this repository:

```sh
uv run builder worker --listen --world ~/dev/animundo-world \
    --project animundo-dev
```

What it does: every new or changed border request is planned
into `plans/{tile}` by code with no model call (T74), and again
when the world commit moves. A failing request is reported to
`builder_alerts`, never retried blindly. `config/builder.enabled`
is the off switch: false starts nothing, and a build in flight
stops at its next stage. Build jobs (T76) run each approved item
through the makers, `checking` with a build report per item;
`builder take <job>` lays the checked items out for review.
A `private` job also gets a versioned preview pack only its
judge opens (T84). When the game's review lands and the world
change reaches the checkout, shipped items make the tile again
and publish it.

Crashes and restarts are safe: every status is a guard, so only
events still waiting are replayed.

## Paused jobs wait for an operator (#171)

A job whose spend is uncertain pauses instead of spending
blind: `error` is `paused_spend_uncertain` and the item's
progress entry too. No restart, re-enable or document change
runs it on its own; the listener prints that it is waiting
for an operator. To move it, write `operator_action` on its
`build_jobs/{id}` document (the console or `admin.mjs`):

- `resume`: settle the held spend at its estimate, then run
  the paused items again from their unfinished stages.
- `give_up`: settle the held spend, record it under
  `uncertain_usd`, and fail the job as `gave_up`.

The worker clears the field once obeyed. Anything else with
nothing paused is ignored and cleared.

## Model hosts

Every `ctx.chat` call prefers the open-weights host when one is
set: the D7 edits and D8 choices forms, words, reviews and file
writing. What stays on the Meta Model API is search grounding
(`respond`), image drawing (`draw`) and SAM, which use other
methods. When the open host refuses a call (auth, spent credit,
throttle) or its caps stop it, the call falls back to Muse
Spark inside the Meta caps; the usage log's host field shows
which host ran it.

## On Modal (T77)

`deploy/modal_worker.py` runs the same listener in one always-on
container (`modal deploy deploy/modal_worker.py`; Kai's account,
Kai runs it). The image is Debian slim with Python 3.12, the
world CI's Blender libraries and Blender 5.2.2, the builder
installed editable from the repo copy (so the contracts,
schemas, prompts and `exclusions.json` next to it are found),
and the baked habitat grids; no keys and no job folders. Its
environment sets `BUILDER_OPEN_MODEL_HOST=baseten`, so the
live worker spends Baseten's $100 pool, and
`BUILDER_LEDGER_PATH=/jobs/spend-ledger.json`, so the spend
ledger sits on the `animundo-jobs` Volume with the job folders
and survives the daily restart. `BUILDER_SPECIES_CACHE_DIR`
keeps the tile species cache on the same Volume, so a restart
never re-asks answered tiles. Seed that ledger once from the
current total before the first live run (Kai's go): a JSON file
with the spent total, `{"jobs": {}, "days": {}, "total":
<spent so far>}`. The Volume is committed every few minutes
while the worker runs, not only when it stops.

Secrets are the Modal secrets `animundo-builder` (the
model keys) and `animundo-firebase`
(`FIREBASE_SERVICE_ACCOUNT_JSON`, written to a file only the
worker reads). The world is cloned fresh at container start,
and the worker pulls it before every tile it makes, so a merged
world change reaches the next tile within one pull.

At start the worker prints one line with the open host, the
ledger path and both pools' totals, the world commit and the
off switch, so a deploy is checked from the logs.
