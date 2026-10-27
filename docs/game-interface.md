# The game, as Builder mode sees it

Builder mode is the small game-side half of the builder (spec 06). It lives
in this repository at `godot/addons/animundo_builder/` and the game copies it
in from a tagged release (`tools/sync_builder.sh` there): GDScript, scenes
and data only. The copy script starts `builder_mode.gd` as the `BuilderMode`
autoload; the game works the same without it.

## What it may touch

Only the game's plug-in points (`BuilderHooks`, all static) and the generic
`Cloud` calls. Nothing else in the game knows it exists. The checks fail if
any addon file names another game class directly.

| Call | Used for |
|---|---|
| `BuilderHooks.add_content_folder` | Installed packs (`user://packs/<draft>/<n>/`) and the addon's own words (`user://packs/_addon/1/`, so the menus can label its buttons) |
| `BuilderHooks.remove_content_folder` | Taking a pack away again |
| `BuilderHooks.register_model` / `unregister_model` | A pack's `.glb` as its species' model |
| `BuilderHooks.set_place_look` / `clear_place_look` | The family's drafts on the globe |
| `BuilderHooks.add_grownup_button` | `explore.builder_mode`: opens the Builder screen (behind the grown-up check) |
| `BuilderHooks.add_places_button` | `explore.ask_place`: opens `animundo.app/build` (behind the grown-up check) |
| `Cloud.uid` | Who is signed in (`""` when nobody is) |
| `Cloud.firestore_query` | The drafts whose `family_uids` hold that uid |
| `Cloud.firestore_get` | One draft, fresh, before playing it |
| `Cloud.firestore_patch` | `last_played_at` after playing; `feedback` when reporting |
| `Cloud.storage_get` | The pack's `manifest.json` and files |

## What it does

At boot it installs its words, re-checks and re-registers every installed
pack, marks the family's drafts on the globe, and adds its two buttons.
Installed family drafts are `your_draft` at their region; drafts the family
waits for are `being_explored` at the draft's position. Published places
are the game's own lit places, strangers' drafts are never shown, and a
`not_possible_yet` draft stays unmarked (the look stays unused; the
screen explains). A load that fails clears the draft looks
and leaves the screen empty: the Cloud answers success or nothing, so an
empty list and a broken load look the same.

The Builder screen lists the 12 test areas and the family's drafts. Play
downloads the draft's pack, checks every file against the manifest, installs
it, and records playing; the family then opens the place from the places
menu. Report sends `feedback` in the shape the security rules accept, once,
owner only. The web app opens at `animundo.app/build`.

## Where reality differs from the spec

1. `Cloud` has no `state()`: signed in means `uid != ""`.
2. Models register as `register_model(species, glb_path)`, not
   `register_animal_model(species)`.
3. The game has no `ContentPacks.FORMAT` yet, so the addon enforces pack
   format 1 itself; a newer format is ignored and its place shows as
   being explored.
4. The game has no `send_report` on `Cloud`, so a pack that fails its
   checks is only ignored, never reported.
5. The game can't lend its `I18n` through the plug-in points, so the addon
   carries its own words (`locale/en.json`, `fi.json`); the reason labels
   mirror the web app's `reason.*` word for word (tested).
6. The player's language comes from the engine's locale, with a button to
   switch; the choice is kept in `user://builder_lang.txt`.
7. The web link carries no `?explorer=<species>`: the game doesn't pass its
   current animal through the plug-in points. `set_explorer()` is ready
   for when it does.
8. Playing never starts gameplay in the draft: the plug-in points can't
   switch scenes.
9. In the game, Report offers `not_right` and `problem`; `ask_person`
   stays in the web app.

## Checks

`tests/check_explore_states.gd` covers every draft status in both languages,
the buttons, the screen, Play and Report through a stub Cloud.
`tests/check_content_packs.gd` covers the pack rules, newer-wins, and, in
the game, built-in-wins and the saved life.
`tests/check_home_pack.gd` installs the fox's real pack
(`tests/fixture/fox-pack/`, from `jobs/review-fox`) and checks it
lists and loads like any draft place. All three run here in a
scratch project (`.github/workflows/check.yml`) and in the game
after each copy:

```
godot --headless --path <project> --script \
  res://addons/animundo_builder/tests/check_explore_states.gd
godot --headless --path <project> --script \
  res://addons/animundo_builder/tests/check_content_packs.gd
godot --headless --path <project> --script \
  res://addons/animundo_builder/tests/check_home_pack.gd
```
