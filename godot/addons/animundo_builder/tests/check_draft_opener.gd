extends SceneTree
## Opening a draft from the address (D9): boot registers the opener and both
## buttons (both open the web app); a stub draft installs and returns its
## place id; a bad hash, an unknown draft, no pack version, a failed
## download and a capital place id all return ""; a failed record_played
## still opens (#91); utc_now matches the game's timestamp pattern; and
## no globe look is ever set. The id is mixed
## case with a hyphen, like a real Firestore id; its folder still fits the
## game's lowercase-only pattern. The game half runs only in the game.
## Run: godot --headless --path <project> --script
##      res://addons/animundo_builder/tests/check_draft_opener.gd

const ADDON := "res://addons/animundo_builder"
const DRAFT := "Zz-Rd1"
const PLACE := "draft_zzrd1"
const STRINGS_FOLDER := "user://packs/_addon/1/"

var errors: Array[String] = []
var game := false
var I18nAPI = null
var RegionAPI = null


func _init() -> void:
	game = ResourceLoader.exists("res://scripts/builder_hooks.gd")
	if game:
		I18nAPI = load("res://scripts/i18n.gd")
		RegionAPI = load("res://scripts/region.gd")
		I18nAPI.start("en")
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
		},
	}]
	cloud.packs[DRAFT + "/1/manifest.json"] = _manifest_bytes()
	cloud.packs[DRAFT + "/1/regions/" + PLACE + ".json"] = _region_bytes()
	var Mode = load(ADDON + "/builder_mode.gd")
	_check_folders(Mode)
	_check_utc_now()
	var mode = Mode.new()
	mode.boot(hooks, cloud)
	await process_frame
	_check_booted(mode, hooks)
	await _check_opens(mode, hooks, cloud)
	_check_game_text()
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
	var web := Callable(mode, "open_web_app")
	var grownup: Array = hooks.grownup_buttons()
	if not grownup.any(func(b): return b.label_key == "explore.builder_mode" and b.open == web):
		errors.append("the grown-up button doesn't open the web app")
	var places: Array = hooks.places_buttons()
	if not places.any(func(b): return b.label_key == "explore.ask_place" and b.open == web):
		errors.append("the places button doesn't open the web app")
	if not STRINGS_FOLDER in (hooks.content_folders() as PackedStringArray):
		errors.append("the addon's words aren't a content folder")


func _check_opens(mode: Node, hooks: Variant, cloud: StubCloud) -> void:
	var Mode = load(ADDON + "/builder_mode.gd")
	var folder: String = Mode.pack_folder(DRAFT, 1)
	# A bad hash returns "" and leaves nothing registered.
	cloud.packs[DRAFT + "/1/regions/" + PLACE + ".json"] = JSON.stringify(
		{"name": "WRONG"}).to_utf8_buffer()
	if await mode.open_draft(DRAFT) != "":
		errors.append("a pack with a wrong hash opens")
	elif folder in (hooks.content_folders() as PackedStringArray):
		errors.append("a pack with a wrong hash is registered")
	elif DirAccess.dir_exists_absolute(folder):
		errors.append("a pack with a wrong hash lands on disk")
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
	if await mode.open_draft(DRAFT) != PLACE:
		errors.append("the stub draft doesn't open its place")
	elif not folder in (hooks.content_folders() as PackedStringArray):
		errors.append("the opened pack's folder isn't registered")
	elif str(hooks.find_file("regions", PLACE)) != folder + "regions/" + PLACE + ".json":
		errors.append("the opened place isn't found")
	if game and not PLACE in (RegionAPI.list_ids() as PackedStringArray):
		errors.append("the opened place isn't listed in the game")
	var played: Array = cloud.patched.filter(
		func(p): return p.path == "drafts/" + DRAFT and p.fields.has("last_played_at"))
	if played.is_empty():
		errors.append("playing isn't recorded on the draft")
	if not (hooks.place_looks() as Dictionary).is_empty():
		errors.append("opening a draft set a globe look")
	# Anything else failing returns "" too.
	if await mode.open_draft("Zz-Nope") != "":
		errors.append("an unknown draft opens")
	cloud.drafts[0].fields.pack_version = 0
	if await mode.open_draft(DRAFT) != "":
		errors.append("a draft with no pack version opens")
	cloud.drafts[0].fields.pack_version = 1
	cloud.fail["storage_get"] = true
	if await mode.open_draft(DRAFT) != "":
		errors.append("a failed download opens")
	cloud.fail.erase("storage_get")
	# Playing doesn't depend on the bookkeeping: a failed record_played
	# still opens the place (#91).
	cloud.fail["firestore_patch"] = true
	if await mode.open_draft(DRAFT) != PLACE:
		errors.append("a failed record_played closes the draft")
	cloud.fail.erase("firestore_patch")


func _check_game_text() -> void:
	if not game:
		return
	if I18nAPI.t("explore.builder_mode") != "Builder mode":
		errors.append("the grown-up button has no English text in the game")
	I18nAPI.start("fi")
	if I18nAPI.t("explore.ask_place") != "Pyydä aikuista tekemään uusi paikka":
		errors.append("the places button has no Finnish text in the game")
	I18nAPI.start("en")


func _region_bytes() -> PackedByteArray:
	return JSON.stringify({"name": "Repovesi (draft)"}).to_utf8_buffer()


func _manifest_bytes() -> PackedByteArray:
	var region := _region_bytes()
	return JSON.stringify({
		"draft_id": DRAFT, "draft_place_id": PLACE,
		"place_id": "fi_repovesi", "version": 1, "format": 1,
		"files": [{
			"path": "regions/" + PLACE + ".json",
			"sha256": PackRules.sha256_hex(region), "bytes": region.size(),
		}],
		"new_species": [], "reused_species": [],
	}).to_utf8_buffer()


func _cleanup(hooks: Variant, Mode: GDScript) -> void:
	hooks.remove_content_folder(Mode.pack_folder(DRAFT, 1))
	hooks.remove_content_folder(STRINGS_FOLDER)
	hooks.set_draft_opener(Callable())
	hooks.remove_grownup_button("explore.builder_mode")
	hooks.remove_places_button("explore.ask_place")
	_remove_dir(Mode.pack_folder(DRAFT, 1).trim_suffix("/").get_base_dir())
	_remove_dir("user://packs/_addon")


func _remove_dir(path: String) -> void:
	if not DirAccess.dir_exists_absolute(path):
		return
	for d in DirAccess.get_directories_at(path):
		_remove_dir(path + "/" + d)
	for f in DirAccess.get_files_at(path):
		DirAccess.remove_absolute(path + "/" + f)
	DirAccess.remove_absolute(path)
