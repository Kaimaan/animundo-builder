# The game, as Builder mode sees it

Builder mode is the small game-side half of the builder (spec 06). It lives
in this repository at `godot/addons/animundo_builder/` and the game copies it
in from a tagged release (`tools/sync_builder.sh` there): GDScript, scenes
and data only. The copy script starts `builder_mode.gd` as the `BuilderMode`
autoload; the game works the same without it.

Drafts live in the web app until they are published (decision D9): there is
no panel, no draft list and no draft look in the game. Both buttons just
open `animundo.app/build`, and playing a draft starts from the web app's
Play in the game.

## What it may touch

Only the game's plug-in points (`BuilderHooks`, all static) and the generic
`Cloud` calls. Nothing else in the game knows it exists. The checks fail if
any addon file names another game class directly.

| Call | Used for |
|---|---|
| `BuilderHooks.add_content_folder` | Installed packs (`user://packs/<safe-id>_<hash>/<n>/`) and the addon's own words (`user://packs/_addon/1/`, so the menus can label its buttons) |
| `BuilderHooks.remove_content_folder` | Taking a pack away again |
| `BuilderHooks.register_model` / `unregister_model` | A pack's `.glb` as its species' model |
| `BuilderHooks.set_place_look` / `clear_place_look` | The game offers them; the addon sets no looks (D9) |
| `BuilderHooks.add_grownup_button` | `explore.builder_mode`: opens `animundo.app/build` (behind the grown-up check) |
| `BuilderHooks.add_places_button` | `explore.ask_place`: opens `animundo.app/build` (behind the grown-up check) |
| `BuilderHooks.set_draft_opener` | `open_draft`: the game awaits it with the `?draft=` id and starts the returned place for the session |
| `Cloud.uid` | Who is signed in (`""` when nobody is) |
| `Cloud.firestore_query` | The game offers it; the addon reads one draft, never a list (D9) |
| `Cloud.firestore_get` | One draft, fresh: its `pack_version` |
| `Cloud.firestore_patch` | `last_played_at` after playing |
| `Cloud.storage_get` | The pack's `manifest.json` and files |

## What it does

At boot it installs its words, adds its two buttons, and registers the
draft opener. It installs nothing at boot: packs arrive through the opener
only, and only for the session.

Play in the game opens `animundo.app/?draft=<id>`. The game checks the id
and awaits the opener, which reads the draft, downloads its pack (every
file checked against the manifest before anything lands on disk), checks
and installs it, records playing, and returns the pack's `draft_place_id`.
Anything failing returns `""` and leaves nothing registered, and the game
falls back to its title screen. The pack's folder is the draft id made
safe for the game's lowercase-only folder pattern, with a hash so two ids
never share one. The web app opens at `animundo.app/build`.

## Where reality differs from the spec

1. `Cloud` has no `state()`: signed in means `uid != ""`.
2. Models register as `register_model(species, glb_path)`, not
   `register_animal_model(species)`.
3. The game has no `ContentPacks.FORMAT` yet, so the addon enforces pack
   format 1 itself; a newer format fails the open.
4. The game has no `send_report` on `Cloud`, so a pack that fails its
   checks is only refused, never reported.
5. The game can't lend its `I18n` through the plug-in points, so the addon
   carries its buttons' words (`locale/en.json`, `fi.json`) as a content
   folder the game's own menus read.
6. The web link carries no `?explorer=<species>`: the game doesn't pass its
   current animal through the plug-in points. `set_explorer()` is ready
   for when it does.
7. `feedback`, `ask_person` and the build report stay in the web app; the
   game never sends them.

## Checks

`tests/check_draft_opener.gd` covers boot (the opener, both buttons, the
words), a stub draft opening its place, and every failure returning `""`.
`tests/check_content_packs.gd` covers the pack rules, newer-wins, and, in
the game, built-in-wins and the saved life.
`tests/check_home_pack.gd` installs the fox's real pack
(`tests/fixture/fox-pack/`, from `jobs/review-fox`) and checks it
lists and loads like any draft place. All three run here in a
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
