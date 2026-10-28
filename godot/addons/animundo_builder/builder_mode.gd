extends Node
## Builder mode: the game-side addon for judge previews.
##
## Installed as the "BuilderMode" autoload by tools/sync_builder.sh. It uses
## only the game's plug-in points (BuilderHooks, the generic Cloud calls) and
## the engine. It adds no buttons and no looks: opening
## animundo.app/?draft=<id> has the game await the draft opener, which
## installs that build's pack for this session only. The backend is injected
## through boot(), so the headless checks run the same code against stubs
## with no game and no net.
##
## What it does not do: it never signs in (the game does that), never
## builds (the worker does that), never lists builds, and never starts
## gameplay itself (the game starts the opened place once the pack is in).

const PACKS_DIR := "user://packs"

var _hooks ## BuilderHooks, or a stub in the checks.
var _cloud: BuilderCloud
var _booted := false
var _last_error := ""


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
	_hooks.set_draft_opener(open_draft)
	_booted = true


## The reason the last opening failed (which step, with its HTTP status
## when the backend gave one), or "" when the last opening worked (#100).
## The game shows it on its debug line once it reads this plug-in point
## (proposed game-side name: `BuilderHooks.draft_opener_error()`).
func last_error() -> String:
	return _last_error


## Opens a draft from the address (D9): the game awaits this with the draft
## id and starts the returned place and animal for this session. The pack
## downloads, checks and installs; playing is recorded but the opening
## doesn't depend on it (#91). Every step goes through the game's hook
## (#98.2), so ?debug=1 shows the first failure on screen. A missing
## draft, a failed download, a failed install or a place id the game
## can't use (#93) returns "" and leaves nothing started.
func open_draft(draft_id: String) -> Variant:
	if not _booted:
		return ""
	_last_error = ""
	_step("booted", true)
	if not _cloud.signed_in():
		_step("signed in", false, "no grown-up is signed in")
		return ""
	_step("signed in", true)
	var draft := await _cloud.get_draft(draft_id)
	if draft.is_empty():
		_step("drafts/" + draft_id + " read", false,
			"not this family's, or missing" + _code_suffix())
		return ""
	_step("drafts/" + draft_id + " read", true)
	var version := int(draft.get("pack_version", 0))
	if version < 1:
		_step("pack_version", false, "no pack built yet")
		return ""
	_step("pack_version", true, str(version))
	if not await _download_pack(draft_id, version):
		return ""
	if not _install_pack(draft_id, version):
		return ""
	if not await _cloud.record_played(draft_id):
		_step("record_played", false, "fine to fail" + _code_suffix())
	else:
		_step("record_played", true)
	var folder := pack_folder(draft_id, version)
	var manifest: Variant = _read_json(folder + "manifest.json")
	var place_id := str(manifest.get("draft_place_id", ""))
	if not _hooks.is_id(place_id):
		_step("place id", false, "not a game id: " + place_id.left(60))
		return ""
	_step("place id", true, place_id)
	_last_error = ""
	return {"place": place_id, "animal": _draft_animal(draft, folder, manifest)}


## One step through the game's hook, kept as the last error when it fails.
## Never tokens or emails: ids and statuses only.
func _step(step: String, ok: bool, detail := "") -> void:
	_hooks.draft_step(step, ok, detail)
	if not ok:
		_last_error = step if detail == "" else "%s: %s" % [step, detail]


## The backend's status for the last call, or "" when none came.
func _code_suffix() -> String:
	if _cloud.last_code == 0:
		return ""
	return " (%d)" % _cloud.last_code


## The draft's animal for the game to start (#98.1): the manifest's
## `animal` on new packs, else the species in the region's playable
## whose taxon key is the draft's species key (old packs, like Kai's),
## else "" and the game starts the first one awake.
func _draft_animal(draft: Dictionary, folder: String, manifest: Dictionary) -> String:
	var named := str(manifest.get("animal", ""))
	if named != "":
		return named
	var key := int(draft.get("species_key", 0))
	if key < 1:
		return ""
	var region: Variant = _read_json(
		folder + "regions/" + str(manifest.get("draft_place_id", "")) + ".json")
	var playable: Array = region.get("animals", {}).get("playable", [])
	for species in playable:
		var path: String = _hooks.find_file("animals", str(species))
		if path == "":
			continue
		var animal: Variant = _read_json(path)
		if int(animal.get("taxon", {}).get("gbif_key", 0)) == key:
			return str(species)
	return ""


## The draft's folder: its id, made safe for the folder pattern (lowercase
## only), with a hash so two ids never share one ("a-b" and "a_b" differ).
static func pack_folder(draft_id: String, version: int) -> String:
	var safe := RegEx.create_from_string("[^a-z0-9]").sub(
		draft_id.to_lower(), "_", true)
	var tag := PackRules.sha256_hex(draft_id.to_utf8_buffer()).left(8)
	return "%s/%s_%s/%d/" % [PACKS_DIR, safe, tag, version]


# --- packs ----------------------------------------------------------------

## Downloads the draft's pack: the manifest first, then every file it names.
## Everything is checked against its size and hash before anything lands on
## disk, so a failed download leaves the folder (and any earlier install)
## exactly as it was.
func _download_pack(draft_id: String, version: int) -> bool:
	var base := "packs/%s/%d/" % [draft_id, version]
	var raw := await _cloud.read_pack_file(draft_id, version, "manifest.json", 65536)
	if raw.is_empty():
		_step(base + "manifest.json", false, _download_reason())
		return false
	_step(base + "manifest.json", true, "%d bytes" % raw.size())
	var manifest: Variant = JSON.parse_string(raw.get_string_from_utf8())
	var manifest_err := PackRules.manifest_error(manifest)
	if manifest_err != "":
		_step("manifest checks", false, manifest_err)
		return false
	var files := {}
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		var data := await _cloud.read_pack_file(
			draft_id, version, rel, int(entry["bytes"]))
		if data.size() != int(entry["bytes"]):
			_step(base + rel, false,
				"got %d bytes, wanted %d" % [data.size(), int(entry["bytes"])])
			return false
		if PackRules.sha256_hex(data) != str(entry["sha256"]):
			_step(base + rel, false, "hash mismatch")
			return false
		_step(base + rel, true, "%d bytes" % data.size())
		files[rel] = data
	var folder := pack_folder(draft_id, version)
	DirAccess.make_dir_recursive_absolute(folder)
	if not _write_file(folder + "manifest.json", raw):
		_step(base + "manifest.json", false, "could not write")
		return false
	for rel in files:
		DirAccess.make_dir_recursive_absolute(folder + rel.get_base_dir())
		if not _write_file(folder + rel, files[rel]):
			_step(base + rel, false, "could not write")
			return false
	return true


## Why a download came back empty: its HTTP status, or "failed".
func _download_reason() -> String:
	if _cloud.last_code == 0:
		return "failed"
	return str(_cloud.last_code)


## Checks an installed pack again and registers it: its folder, then its
## models. A pack that fails any check is left out, not half installed.
func _install_pack(draft_id: String, version: int) -> bool:
	var folder := pack_folder(draft_id, version)
	var manifest: Variant = _read_json(folder + "manifest.json")
	var manifest_err := PackRules.manifest_error(manifest)
	if manifest_err != "":
		_step("content folder added", false, manifest_err)
		_remove_pack(folder)
		return false
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		var bytes := FileAccess.get_file_as_bytes(folder + rel)
		if bytes.size() != int(entry["bytes"]) \
			or PackRules.sha256_hex(bytes) != str(entry["sha256"]):
			_step("content folder added", false, "hash mismatch " + rel)
			_remove_pack(folder)
			return false
	if not _hooks.add_content_folder(folder):
		_step("content folder added", false, "the game refused it")
		_remove_pack(folder)
		return false
	_step("content folder added", true, folder)
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
