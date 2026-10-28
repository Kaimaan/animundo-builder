# Running the worker

The worker listens on Firestore and builds what admins approve. The
grown-up draft flows are gone (phase 0); the border flows land in
phase 2.

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
but builds nothing yet. Phase 2 adds intake (border requests to
areas with plans), the makers per item, and shipping.

Crashes and restarts are safe: every status is a guard, so only
events still waiting are replayed.
