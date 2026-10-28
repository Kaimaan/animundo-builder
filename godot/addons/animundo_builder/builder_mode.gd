extends Node
## Builder mode: the game-side addon (spec 06, decision D9).
##
## Installed as the "BuilderMode" autoload by tools/sync_builder.sh. It uses
## only the game's plug-in points (BuilderHooks, the generic Cloud calls) and
## the engine. Drafts live in the web app until they are published: the
## grown-up button and the places button both just open animundo.app/build,
## and opening animundo.app/?draft=<id> (the web app's Play in the game) has
## the game await the draft opener, which installs that draft's pack for this
## session only. The backend is injected through boot(), so the headless
## checks run the same code against stubs with no game and no net.
##
## What it does not do: it never signs in (the game and the web app do that),
## never builds (the worker does that), never lists drafts, and never starts
## gameplay itself (the game starts the opened place once the pack is in).

const WEB_BUILD := "https://animundo.app/build"
const PACKS_DIR := "user://packs"
const STRINGS_FOLDER := "user://packs/_addon/1/"
const STRINGS_SRC := "res://addons/animundo_builder/locale"
const STRINGS_LANGS: Array[String] = ["en", "fi"]

var _hooks ## BuilderHooks, or a stub in the checks.
var _cloud: BuilderCloud
var _explorer := ""
var _booted := false


func _ready() -> void:
	# In the game the hooks exist; with no game (checks, a bare project)
	# the node stays dormant until boot() is called with stubs.
	# ResourceLoader follows the export remap (.gd to .gdc); FileAccess
	# would never see the script in an exported game (#68).
	if ResourceLoader.exists("res://scripts/builder_hooks.gd"):
		var game_cloud := get_node_or_null("/root/Cloud")
		if game_cloud != null:
			boot(load("res://scripts/builder_hooks.gd"), game_cloud)


func booted() -> bool:
	return _booted


## Starts Builder mode on `hooks` (BuilderHooks or a stub) and `cloud`
## (the game's Cloud autoload or a stub).
func boot(hooks: Variant, cloud_backend: Variant) -> void:
	_hooks = hooks
	_cloud = BuilderCloud.new(cloud_backend)
	_install_strings_folder()
	_hooks.add_grownup_button("explore.builder_mode", open_web_app)
	_hooks.add_places_button("explore.ask_place", open_web_app)
	_hooks.set_draft_opener(open_draft)
	_booted = true


## Opens the builder web app (both buttons' callable).
func open_web_app() -> void:
	var url := WEB_BUILD
	if _explorer != "":
		url += "?explorer=" + _explorer.uri_encode()
	OS.shell_open(url)


## The animal the web app starts exploring with. The game doesn't pass its
## current animal through the plug-in points yet, so this stays empty until
## it does (docs/game-interface.md).
func set_explorer(species: String) -> void:
	_explorer = species


## Opens a draft from the address (D9): the game awaits this with the draft
## id and starts the returned place for this session. The pack downloads,
## checks and installs; playing is recorded but the opening doesn't depend
## on it (#91). A missing draft, a failed download, a failed install or a
## place id the game can't use (#93) returns "" and leaves nothing started.
func open_draft(draft_id: String) -> String:
	if not _booted:
		return ""
	var draft := await _cloud.get_draft(draft_id)
	var version := int(draft.get("pack_version", 0))
	if version < 1:
		return ""
	if not await _download_pack(draft_id, version):
		return ""
	if not _install_pack(draft_id, version):
		return ""
	if not await _cloud.record_played(draft_id):
		push_warning("BuilderMode: record_played failed for " + draft_id)
	var manifest: Variant = _read_json(pack_folder(draft_id, version) + "manifest.json")
	var place_id := str(manifest.get("draft_place_id", ""))
	if not _hooks.is_id(place_id):
		push_warning("BuilderMode: bad draft_place_id for " + draft_id)
		return ""
	return place_id


## The draft's folder: its id, made safe for the folder pattern (lowercase
## only), with a hash so two ids never share one ("a-b" and "a_b" differ).
static func pack_folder(draft_id: String, version: int) -> String:
	var safe := RegEx.create_from_string("[^a-z0-9]").sub(
		draft_id.to_lower(), "_", true)
	var tag := PackRules.sha256_hex(draft_id.to_utf8_buffer()).left(8)
	return "%s/%s_%s/%d/" % [PACKS_DIR, safe, tag, version]


# --- boot ---------------------------------------------------------------

## The addon's own words as a content folder, so the game's menus can show
## its two buttons in the player's language.
func _install_strings_folder() -> void:
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "locale/en")
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "locale/fi")
	# Empty folders the game's readers expect; the folder holds only words.
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "regions")
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "animals")
	for lang in STRINGS_LANGS:
		var src := STRINGS_SRC + "/" + lang + ".json"
		var dst := STRINGS_FOLDER + "locale/" + lang + "/explore.json"
		var same := FileAccess.file_exists(dst) \
			and FileAccess.get_file_as_string(src) == FileAccess.get_file_as_string(dst)
		if not same:
			DirAccess.copy_absolute(src, dst)
	_hooks.add_content_folder(STRINGS_FOLDER)


# --- packs ----------------------------------------------------------------

## Downloads the draft's pack: the manifest first, then every file it names.
## Everything is checked against its size and hash before anything lands on
## disk, so a failed download leaves the folder (and any earlier install)
## exactly as it was.
func _download_pack(draft_id: String, version: int) -> bool:
	var raw := await _cloud.read_pack_file(draft_id, version, "manifest.json", 65536)
	if raw.is_empty():
		return false
	var manifest: Variant = JSON.parse_string(raw.get_string_from_utf8())
	if PackRules.manifest_error(manifest) != "":
		return false
	var files := {}
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		var data := await _cloud.read_pack_file(
			draft_id, version, rel, int(entry["bytes"]))
		if data.size() != int(entry["bytes"]):
			return false
		if PackRules.sha256_hex(data) != str(entry["sha256"]):
			return false
		files[rel] = data
	var folder := pack_folder(draft_id, version)
	DirAccess.make_dir_recursive_absolute(folder)
	if not _write_file(folder + "manifest.json", raw):
		return false
	for rel in files:
		DirAccess.make_dir_recursive_absolute(folder + rel.get_base_dir())
		if not _write_file(folder + rel, files[rel]):
			return false
	return true


## Checks an installed pack again and registers it: its folder, then its
## models. A pack that fails any check is left out, not half installed.
func _install_pack(draft_id: String, version: int) -> bool:
	var folder := pack_folder(draft_id, version)
	var manifest: Variant = _read_json(folder + "manifest.json")
	if PackRules.manifest_error(manifest) != "":
		_remove_pack(folder)
		return false
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		var bytes := FileAccess.get_file_as_bytes(folder + rel)
		if bytes.size() != int(entry["bytes"]) \
			or PackRules.sha256_hex(bytes) != str(entry["sha256"]):
			_remove_pack(folder)
			return false
	if not _hooks.add_content_folder(folder):
		_remove_pack(folder)
		return false
	for entry in manifest["files"]:
		var path := str(entry["path"])
		if path.begins_with("models/") and path.ends_with(".glb"):
			_hooks.register_model(path.get_file().get_basename(), folder + path)
	return true


## A failed pack leaves nothing registered: its folder out, its models off.
func _remove_pack(folder: String) -> void:
	_hooks.remove_content_folder(folder)
	for species in _pack_models(folder):
		_hooks.unregister_model(species)


## The species a pack folder claims models for (from its manifest).
func _pack_models(folder: String) -> Array:
	var manifest: Variant = _read_json(folder + "manifest.json")
	if PackRules.manifest_error(manifest) != "":
		return []
	var out: Array = []
	for entry in manifest["files"]:
		var path := str(entry["path"])
		if path.begins_with("models/") and path.ends_with(".glb"):
			out.append(path.get_file().get_basename())
	return out


static func _read_json(path: String) -> Variant:
	if not FileAccess.file_exists(path):
		return {}
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	return parsed if parsed is Dictionary else {}


static func _write_file(path: String, data: PackedByteArray) -> bool:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		return false
	f.store_buffer(data)
	return true
