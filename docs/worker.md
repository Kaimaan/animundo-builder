# Running the worker on your Mac (milestone 1)

The worker listens on Firestore and builds drafts. For milestone 1
it runs in your own terminal, against the real Firestore and
Storage, so Blender has the GPU.

One-time setup:

```sh
gcloud auth application-default login
```

That gives the worker its credentials; there is no key file.
Then, from this repository:

```sh
uv run builder worker --listen --world ~/dev/animundo-world \
    --project animundo-dev --areas web/build/areas.json
```

`--areas` is the short test-place list; it lands with the web app
(T26). Until it exists the worker refuses every draft with
`unknown_area`.

What it does: a new draft is limits-checked (2 active per account,
3 new places a week, the daily budget) and built round by round,
writing the live feed as it goes; the pack lands in Storage at
`packs/<draft>/<version>/` and the report at
`reports/<draft>/<round>/report.html`. Submits move to `in_review`
with a Submitted inbox item; feedback hides or removes; the admin
panel's Send on / Published / Not accepted / Show again / Remove
buttons arrive as `admin_actions` and the worker carries them out
(Send on runs `builder submit` with your own `gh` login).

Crashes and restarts are safe: every status is a guard, so only
events still waiting are replayed. Drafts stuck in `queued`,
`building` or `improving` after a crash are left alone and named
at startup; set one back to `requested` in the console to rebuild
it, or to `failed` to let it go.
