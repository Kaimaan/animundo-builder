# Builder mode (the game-side addon)

The small half of the builder for judge previews: it adds no buttons
and no looks. Opening animundo.app/?draft=\<id\> has the game await
the draft opener, which installs that build's pack for the session.

- `builder_mode.gd` — the addon. Starts as the `BuilderMode` autoload; uses
  only `BuilderHooks` and the generic `Cloud` calls, plus the engine.
- `builder_cloud.gd` — one build, its pack, and playing it, through Cloud.
- `pack_rules.gd` — what a pack must look like before the game touches it.
- `tests/` — the headless checks, run here and in the game after each copy.

The game copies this folder in from a tagged release (`tools/sync_builder.sh`
there): GDScript, scenes and data only, and `builder_mode.gd` must exist.
