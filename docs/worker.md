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

What it does in phase 0: it listens and claims work atomically,
but builds nothing yet. Phase 2 adds the planner and the builders.

Crashes and restarts are safe: every status is a guard, so only
events still waiting are replayed.
