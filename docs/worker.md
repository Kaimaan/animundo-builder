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
`builder take <job>` lays the checked items out for review. Plant
items wait for plant builds. When the game's review lands and the
world change reaches the checkout, shipped items make the tile
again and publish it.

Crashes and restarts are safe: every status is a guard, so only
events still waiting are replayed.
