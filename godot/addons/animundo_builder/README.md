# Builder mode (the game-side addon)

The small half of the builder (spec 06): it shows the family's drafts on the
game's globe, plays installed packs, and reports problems. Building itself
happens in the web app and the worker.

- `builder_mode.gd` — the addon. Starts as the `BuilderMode` autoload; uses
  only `BuilderHooks` and the generic `Cloud` calls, plus the engine.
- `builder_cloud.gd` — drafts, packs and reports through the game's Cloud.
- `pack_rules.gd` — what a pack must look like before the game touches it.
- `explore_states.gd` — each draft status's globe look and plain sentence.
- `builder_strings.gd` — the addon's own words (`locale/en.json`, `fi.json`).
- `tests/` — the headless checks, run here and in the game after each copy.

The game copies this folder in from a tagged release (`tools/sync_builder.sh`
there): GDScript, scenes and data only, and `builder_mode.gd` must exist.
