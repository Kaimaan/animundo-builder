extends SceneTree
## Opening a draft from the address: boot registers the opener and no
## buttons; a stub draft installs and returns its place and animal; a
## bad hash, an unknown draft, no pack version, a failed download and
## a capital place id all return ""; a failed record_played still
## opens (#91); utc_now matches the game's timestamp pattern; every
## step goes through the hook with its reason (#98.2); last_error
## keeps the failed step with its HTTP status (#100); and no globe
## look is ever set. The id is mixed case with a hyphen, like a real
## Firestore id; its folder still fits the game's lowercase-only
## pattern. The game half runs only in the game.
## Run: godot --headless --path <project> --script
##      res://addons/animundo_builder/tests/check_draft_opener.gd

const ADDON := "res://addons/animundo_builder"
const DRAFT := "Zz-Rd1"
const PLACE := "draft_zzrd1"

var errors: Array[String] = []
var game := false
var RegionAPI = null


func _init() -> void:
	game = ResourceLoader.exists("res://scripts/builder_hooks.gd")
	if game:
		RegionAPI = load("res://scripts/region.gd")
	_run.call_deferred()


func _run() -> void:
	var hooks = load("res://scripts/builder_hooks.gd") if game else StubHooks.new()
	var cloud := StubCloud.new()
	cloud.uid = "grownup1"
	cloud.drafts = [{
		"id": DRAFT,
		"fields": {
			"status": "ready", "family_uids": ["grownup1"],
			"owner_uid": "grownup1", "place_name": "Repovesi",
			"pack_version": 1, "draft_place_id": PLACE,
			"species_key": 5219243,
		},
	}]
	cloud.packs[DRAFT + "/1/manifest.json"] = _manifest_bytes()
	cloud.packs[DRAFT + "/1/regions/" + PLACE + ".json"] = _region_bytes()
	cloud.packs[DRAFT + "/1/animals/fox.json"] = _fox_bytes()
	var Mode = load(ADDON + "/builder_mode.gd")
	_check_folders(Mode)
	_check_utc_now()
	var mode = Mode.new()
	mode.boot(hooks, cloud)
	await process_frame
	_check_booted(mode, hooks)
	await _check_opens(mode, hooks, cloud)
	mode.free()
	_cleanup(hooks, Mode)
	for e in errors:
		printerr("ERROR: ", e)
	print("draft opener check: %d errors" % errors.size())
	quit(1 if errors.size() > 0 else 0)


func _check_folders(Mode: GDScript) -> void:
	var pattern := "^user://packs/[a-z0-9_]+/[0-9]+/$"
	for draft_id in [DRAFT, "Ab-9_x", "UPPER", "a/b", "x y", "pää"]:
		var folder: String = Mode.pack_folder(draft_id, 1)
		if RegEx.create_from_string(pattern).search(folder) == null:
			errors.append("the folder for %s misses the pattern: %s" % [draft_id, folder])
	if Mode.pack_folder("a-b", 1) == Mode.pack_folder("a_b", 1):
		errors.append("two ids share one pack folder")


func _check_utc_now() -> void:
	var cloud_api = load(ADDON + "/builder_cloud.gd")
	var now: String = cloud_api.utc_now()
	# The game's Cloud sends a string as a timestamp only on this
	# pattern (cloud.gd `_is_timestamp`); a space made "played" fail.
	if RegEx.create_from_string("^\\d{4}-\\d{2}-\\d{2}T.*Z$").search(now) == null:
		errors.append("utc_now misses the game's timestamp pattern: " + now)
	if " " in now:
		errors.append("utc_now has a space: " + now)


func _check_booted(mode: Node, hooks: Variant) -> void:
	if hooks.draft_opener() != Callable(mode, "open_draft"):
		errors.append("boot registers no draft opener")
	if game:
		return
	var grownup: Array = hooks.grownup_buttons()
	if not grownup.is_empty():
		errors.append("boot adds a grown-up button")
	var places: Array = hooks.places_buttons()
	if not places.is_empty():
		errors.append("boot adds a places button")
	if not (hooks.content_folders() as PackedStringArray).is_empty():
		errors.append("boot installs a content folder")


func _check_opens(mode: Node, hooks: Variant, cloud: StubCloud) -> void:
	var Mode = load(ADDON + "/builder_mode.gd")
	var folder: String = Mode.pack_folder(DRAFT, 1)
	var want := {"place": PLACE, "animal": "fox"}
	# A bad hash returns "" and leaves nothing registered.
	cloud.packs[DRAFT + "/1/regions/" + PLACE + ".json"] = JSON.stringify(
		{"name": "WRONG"}).to_utf8_buffer()
	if await mode.open_draft(DRAFT) != "":
		errors.append("a pack with a wrong hash opens")
	elif folder in (hooks.content_folders() as PackedStringArray):
		errors.append("a pack with a wrong hash is registered")
	elif DirAccess.dir_exists_absolute(folder):
		errors.append("a pack with a wrong hash lands on disk")
	elif not str(mode.last_error()).begins_with("packs/" + DRAFT + "/1/regions/"):
		errors.append("a wrong hash names no file: " + str(mode.last_error()))
	cloud.packs[DRAFT + "/1/regions/" + PLACE + ".json"] = _region_bytes()
	# A place id with capitals fails at the check, not after installing (#93).
	var bad := JSON.parse_string(_manifest_bytes().get_string_from_utf8())
	bad["draft_place_id"] = "draft_ZzPk1"
	cloud.packs[DRAFT + "/1/manifest.json"] = JSON.stringify(bad).to_utf8_buffer()
	if await mode.open_draft(DRAFT) != "":
		errors.append("a capital place id opens")
	elif folder in (hooks.content_folders() as PackedStringArray):
		errors.append("a capital place id is registered")
	elif DirAccess.dir_exists_absolute(folder):
		errors.append("a capital place id lands on disk")
	cloud.packs[DRAFT + "/1/manifest.json"] = _manifest_bytes()
	if await mode.open_draft(DRAFT) != want:
		errors.append("the stub draft doesn't open its place and animal")
	elif not folder in (hooks.content_folders() as PackedStringArray):
		errors.append("the opened pack's folder isn't registered")
	elif str(hooks.find_file("regions", PLACE)) != folder + "regions/" + PLACE + ".json":
		errors.append("the opened place isn't found")
	if game and not PLACE in (RegionAPI.list_ids() as PackedStringArray):
		errors.append("the opened place isn't listed in the game")
	if str(mode.last_error()) != "":
		errors.append("a worked opening keeps an error: " + str(mode.last_error()))
	_check_steps(hooks, ["booted", "signed in", "drafts/" + DRAFT + " read",
		"pack_version", "packs/" + DRAFT + "/1/manifest.json",
		"packs/" + DRAFT + "/1/regions/" + PLACE + ".json",
		"packs/" + DRAFT + "/1/animals/fox.json",
		"content folder added", "record_played", "place id"], true)
	var played: Array = cloud.patched.filter(
		func(p): return p.path == "drafts/" + DRAFT and p.fields.has("last_played_at"))
	if played.is_empty():
		errors.append("playing isn't recorded on the draft")
	if not (hooks.place_looks() as Dictionary).is_empty():
		errors.append("opening a draft set a globe look")
	# Without the manifest's animal, the region's playable species with
	# the draft's taxon key starts (old packs, like Kai's, #98.1).
	var old := JSON.parse_string(_manifest_bytes().get_string_from_utf8())
	old.erase("animal")
	cloud.packs[DRAFT + "/1/manifest.json"] = JSON.stringify(old).to_utf8_buffer()
	if await mode.open_draft(DRAFT) != want:
		errors.append("an old pack doesn't fall back to its taxon key")
	cloud.packs[DRAFT + "/1/manifest.json"] = _manifest_bytes()
	# Anything else failing returns "" too.
	if await mode.open_draft("Zz-Nope") != "":
		errors.append("an unknown draft opens")
	elif not str(mode.last_error()).begins_with("drafts/Zz-Nope read"):
		errors.append("an unknown draft names no read: " + str(mode.last_error()))
	cloud.drafts[0].fields.pack_version = 0
	if await mode.open_draft(DRAFT) != "":
		errors.append("a draft with no pack version opens")
	cloud.drafts[0].fields.pack_version = 1
	cloud.fail["storage_get"] = true
	if await mode.open_draft(DRAFT) != "":
		errors.append("a failed download opens")
	elif mode.last_error() != "packs/%s/1/manifest.json: 403" % DRAFT:
		errors.append("a failed download names no status: " + str(mode.last_error()))
	cloud.fail.erase("storage_get")
	# Playing doesn't depend on the bookkeeping: a failed record_played
	# still opens the place (#91).
	cloud.fail["firestore_patch"] = true
	if await mode.open_draft(DRAFT) != want:
		errors.append("a failed record_played closes the draft")
	cloud.fail.erase("firestore_patch")


func _check_steps(hooks: Variant, names: Array, ok: bool) -> void:
	if game:
		return  # the game prints its own lines; the stub records them
	var steps: Array = hooks.steps
	var found := {}
	for entry in steps:
		found[entry.step] = entry.ok
	for name in names:
		if not found.has(name):
			errors.append("no step logged for " + name)
		elif bool(found[name]) != ok:
			errors.append("step %s is %s" % [name, found[name]])


func _region_bytes() -> PackedByteArray:
	return JSON.stringify({
		"name": "Repovesi (draft)", "animals": {"playable": ["fox"]},
	}).to_utf8_buffer()


func _fox_bytes() -> PackedByteArray:
	return JSON.stringify({
		"id": "fox", "taxon": {"gbif_key": 5219243},
	}).to_utf8_buffer()


func _manifest_bytes() -> PackedByteArray:
	var region := _region_bytes()
	var fox := _fox_bytes()
	return JSON.stringify({
		"draft_id": DRAFT, "draft_place_id": PLACE,
		"place_id": "fi_repovesi", "version": 1, "format": 1,
		"animal": "fox",
		"files": [{
			"path": "regions/" + PLACE + ".json",
			"sha256": PackRules.sha256_hex(region), "bytes": region.size(),
		}, {
			"path": "animals/fox.json",
			"sha256": PackRules.sha256_hex(fox), "bytes": fox.size(),
		}],
		"new_species": [], "reused_species": ["fox"],
	}).to_utf8_buffer()


func _cleanup(hooks: Variant, Mode: GDScript) -> void:
	hooks.remove_content_folder(Mode.pack_folder(DRAFT, 1))
	hooks.set_draft_opener(Callable())
	_remove_dir(Mode.pack_folder(DRAFT, 1).trim_suffix("/").get_base_dir())


func _remove_dir(path: String) -> void:
	if not DirAccess.dir_exists_absolute(path):
		return
	for d in DirAccess.get_directories_at(path):
		_remove_dir(path + "/" + d)
	for f in DirAccess.get_files_at(path):
		DirAccess.remove_absolute(path + "/" + f)
	DirAccess.remove_absolute(path)
