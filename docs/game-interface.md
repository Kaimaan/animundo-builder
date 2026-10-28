# The game, as Builder mode sees it

Builder mode is the small game-side half of the builder for judge
previews. It lives in this repository at
`godot/addons/animundo_builder/` and the game copies it in from a
tagged release (`tools/sync_builder.sh` there): GDScript, scenes
and data only. The copy script starts `builder_mode.gd` as the
`BuilderMode` autoload; the game works the same without it.

The addon adds no buttons and no looks. It only opens judge
previews: opening `animundo.app/?draft=<id>` has the game await
the opener, which installs that build's pack for the session.

## What it may touch

Only the game's plug-in points (`BuilderHooks`, all static) and the generic
`Cloud` calls. Nothing else in the game knows it exists. The checks fail if
any addon file names another game class directly.

| Call | Used for |
|---|---|
| `BuilderHooks.add_content_folder` | Installed packs (`user://packs/<safe-id>_<hash>/<n>/`) |
| `BuilderHooks.remove_content_folder` | Taking a pack away again |
| `BuilderHooks.register_model` / `unregister_model` | A pack's `.glb` as its species' model |
| `BuilderHooks.set_draft_opener` | `open_draft`: the game awaits it with the `?draft=` id and starts the returned place for the session |
| `Cloud.uid` | Who is signed in (`""` when nobody is) |
| `Cloud.firestore_get` | One build, fresh: its `pack_version` |
| `Cloud.firestore_patch` | `last_played_at` after playing |
| `Cloud.storage_get` | The pack's `manifest.json` and files |

## What it does

At boot it registers the draft opener. It installs nothing at boot:
packs arrive through the opener only, and only for the session.

Play in the game opens `animundo.app/?draft=<id>`. The game checks the id
and awaits the opener, which reads the build, downloads its pack (every
file checked against the manifest before anything lands on disk), checks
and installs it, records playing, and returns the pack's `draft_place_id`.
Anything failing returns `""` and leaves nothing registered, and the game
falls back to its title screen. The pack's folder is the build id made
safe for the game's lowercase-only folder pattern, with a hash so two ids
never share one.

## Where reality differs from the spec

1. `Cloud` has no `state()`: signed in means `uid != ""`.
2. Models register as `register_model(species, glb_path)`, not
   `register_animal_model(species)`.
3. The game has no `ContentPacks.FORMAT` yet, so the addon enforces pack
   format 1 itself; a newer format fails the open.
4. The game has no `send_report` on `Cloud`, so a pack that fails its
   checks is only refused, never reported.

## Checks

`tests/check_draft_opener.gd` covers boot (the opener, no buttons, no
content folder), a stub build opening its place, and every failure
returning `""`.
`tests/check_content_packs.gd` covers the pack rules, newer-wins, and, in
the game, built-in-wins and the saved life.
`tests/check_home_pack.gd` installs the fox's real pack
(`tests/fixture/fox-pack/`, from `jobs/review-fox`) and checks it
lists and loads like any preview place. All three run here in a
scratch project (`.github/workflows/check.yml`) and in the game
after each copy:

```
godot --headless --path <project> --script \
  res://addons/animundo_builder/tests/check_draft_opener.gd
godot --headless --path <project> --script \
  res://addons/animundo_builder/tests/check_content_packs.gd
godot --headless --path <project> --script \
  res://addons/animundo_builder/tests/check_home_pack.gd
```
