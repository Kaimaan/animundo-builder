# Builder mode (the game-side addon)

The small half of the builder (spec 06, decision D9): drafts live in the
web app until they are published. Both its buttons just open
animundo.app/build, and opening animundo.app/?draft=\<id\> has the game
await the draft opener, which installs that draft's pack for the session.

- `builder_mode.gd` — the addon. Starts as the `BuilderMode` autoload; uses
  only `BuilderHooks` and the generic `Cloud` calls, plus the engine.
- `builder_cloud.gd` — one draft, its pack, and playing it, through Cloud.
- `pack_rules.gd` — what a pack must look like before the game touches it.
- `locale/en.json`, `fi.json` — the two buttons' words, read by the game's
  own menus through a content folder.
- `tests/` — the headless checks, run here and in the game after each copy.

The game copies this folder in from a tagged release (`tools/sync_builder.sh`
there): GDScript, scenes and data only, and `builder_mode.gd` must exist.
