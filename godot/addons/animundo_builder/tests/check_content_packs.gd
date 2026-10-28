extends SceneTree
## Builder mode's packs (spec 01, 06): a wrong hash, an unknown file type, a
## too-big pack and a newer format are all refused; a newer version wins; a
## built-in model, place and text always win; and a saved life loads with its
## pack and is kept while the pack is missing. The game half runs only in the
## game; the rules half runs everywhere.
## Run: godot --headless --path <project> --script
##      res://addons/animundo_builder/tests/check_content_packs.gd

const ADDON := "res://addons/animundo_builder"
const DRAFT := "zzpk1"

var errors: Array[String] = []
var game := false
var RegionAPI = null
var SpeciesAPI = null
var SavesAPI = null
var V1 := ""
var V2 := ""


func _init() -> void:
	game = ResourceLoader.exists("res://scripts/builder_hooks.gd")
	if game:
		RegionAPI = load("res://scripts/region.gd")
		SpeciesAPI = load("res://scripts/species.gd")
		SavesAPI = load("res://scripts/saves.gd")
		load("res://scripts/i18n.gd").start("en")
	_check_rules()
	_run.call_deferred()


func _good_manifest() -> Dictionary:
	var region := JSON.stringify({"name": "Pack place"}).to_utf8_buffer()
	return {
		"draft_id": "zzpk1", "draft_place_id": "draft_zzpk1",
		"place_id": "fi_repovesi", "version": 1, "format": 1,
		"files": [{
			"path": "regions/draft_zzpk1.json",
			"sha256": PackRules.sha256_hex(region), "bytes": region.size(),
		}],
		"new_species": [], "reused_species": [],
	}


func _check_rules() -> void:
	if PackRules.manifest_error(_good_manifest()) != "":
		errors.append("a good manifest is refused")
	if PackRules.sha256_hex("abc".to_utf8_buffer()) \
			!= "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad":
		errors.append("sha256 is wrong")
	var bad := [
		["not an object", [1, 2]],
		["missing key", _without(_good_manifest(), "place_id")],
		["bad draft_place_id", _with(_good_manifest(), "draft_place_id", "zzpk1")],
		["version 0", _with(_good_manifest(), "version", 0)],
		["newer format", _with(_good_manifest(), "format", 99)],
		["no files", _with(_good_manifest(), "files", [])],
		["bad sha256", _with_file(_good_manifest(), "sha256", "xyz")],
		["bad size", _with_file(_good_manifest(), "bytes", -5)],
		["climbing path", _with_file(_good_manifest(), "path", "../saves/x.json")],
		["absolute path", _with_file(_good_manifest(), "path", "/etc/x.json")],
		["wrong folder", _with_file(_good_manifest(), "path", "saves/x.json")],
		["unknown type", _with_file(_good_manifest(), "path", "regions/x.gd")],
		["big glb", _with_file(_good_manifest(), "path", "models/x.glb",
			PackRules.MAX_GLB_BYTES + 1)],
	]
	for c in bad:
		if PackRules.manifest_error(c[1]) == "":
			errors.append("a manifest with %s is accepted" % c[0])
	var big := _good_manifest()
	big["files"] = [{"path": "models/x.glb",
		"sha256": "ab".repeat(32), "bytes": PackRules.MAX_GLB_BYTES + 1}]
	if PackRules.manifest_error(big) == "":
		errors.append("a too-big .glb is accepted")
	var huge := _good_manifest()
	var entries: Array = []
	for i in 9:
		entries.append({"path": "models/m%d.glb" % i,
			"sha256": "cd".repeat(32), "bytes": 1024 * 1024})
	huge["files"] = entries
	if PackRules.manifest_error(huge) == "":
		errors.append("a too-big pack is accepted")
	if PackRules.file_error("models/x.glb", 10) != "":
		errors.append("a good pack file is refused")


func _with(m: Dictionary, key: String, value: Variant) -> Dictionary:
	var out := m.duplicate(true)
	out[key] = value
	return out


func _without(m: Dictionary, key: String) -> Dictionary:
	var out := m.duplicate(true)
	out.erase(key)
	return out


func _with_file(m: Dictionary, key: String, value: Variant, size := -1) -> Dictionary:
	var out := m.duplicate(true)
	out["files"][0][key] = value
	if size >= 0:
		out["files"][0]["bytes"] = size
	return out


func _run() -> void:
	var hooks = load("res://scripts/builder_hooks.gd") if game else StubHooks.new()
	var Mode = load(ADDON + "/builder_mode.gd")
	V1 = Mode.pack_folder(DRAFT, 1)
	V2 = Mode.pack_folder(DRAFT, 2)
	_write_version(V1, "Pack place one")
	_write_version(V2, "Pack place two")
	var mode = Mode.new()
	mode.boot(hooks, StubCloud.new())
	_check_booted(hooks)
	_check_installed(mode, hooks)
	if game:
		_check_game(hooks)
	mode.free()
	_cleanup(hooks)
	for e in errors:
		printerr("ERROR: ", e)
	print("content packs check: %d errors" % errors.size())
	quit(1 if errors.size() > 0 else 0)


## Boot installs nothing: packs arrive through the draft opener only (D9).
func _check_booted(hooks: Variant) -> void:
	var folders: PackedStringArray = hooks.content_folders()
	if V1 in folders or V2 in folders:
		errors.append("boot registered packs: %s" % [folders])


func _check_installed(mode: Node, hooks: Variant) -> void:
	if not mode._install_pack(DRAFT, 2):
		errors.append("the newer version doesn't install")
	if not mode._install_pack(DRAFT, 1):
		errors.append("the older version doesn't install")
	var folders: PackedStringArray = hooks.content_folders()
	if not V2 in folders or not V1 in folders:
		errors.append("the installed versions aren't registered: %s" % [folders])
	elif folders.find(V2) > folders.find(V1):
		errors.append("the older version wins: %s" % [folders])
	var found := str(hooks.find_file("regions", "draft_zzpk1"))
	if found != V2 + "regions/draft_zzpk1.json":
		errors.append("the newer version's place isn't found: %s" % found)
	# A corrupted file fails the install and unregisters its version.
	FileAccess.open(V2 + "regions/draft_zzpk1.json", FileAccess.WRITE).store_string("junk")
	if mode._install_pack(DRAFT, 2):
		errors.append("a pack with a wrong hash installs")
	elif V2 in (hooks.content_folders() as PackedStringArray):
		errors.append("a pack with a wrong hash stays registered")


func _check_game(hooks: Variant) -> void:
	# The pack's place and text show up...
	var ids: PackedStringArray = RegionAPI.list_ids()
	if not "draft_zzpk1" in ids:
		errors.append("the pack's place isn't listed")
	elif RegionAPI.load_region("draft_zzpk1").title() != "Pack place one":
		errors.append("the pack's place has the wrong text")
	# ...but a built-in place, animal and text always win.
	var builtin_region: String = (RegionAPI.list_ids() as PackedStringArray)[0]
	var shadow := {"name": "PACK WINS"}
	_write_bytes(V1 + "regions/" + builtin_region + ".json",
		JSON.stringify(shadow).to_utf8_buffer())
	if not RegionAPI.file(builtin_region).begins_with("res://"):
		errors.append("a pack shadows the built-in place " + builtin_region)
	var builtin_species := ""
	for id in SpeciesAPI.list_ids():
		if SpeciesAPI.file(id).begins_with("res://"):
			builtin_species = id
			break
	if builtin_species == "":
		errors.append("the game has no built-in animal to shadow")
	else:
		DirAccess.make_dir_recursive_absolute(V1 + "animals")
		_write_bytes(V1 + "animals/" + builtin_species + ".json",
			JSON.stringify({"note": "PACK WINS"}).to_utf8_buffer())
		if not SpeciesAPI.file(builtin_species).begins_with("res://"):
			errors.append("a pack shadows the built-in animal " + builtin_species)
	var I18nAPI = load("res://scripts/i18n.gd")
	DirAccess.make_dir_recursive_absolute(V1 + "locale/en")
	_write_bytes(V1 + "locale/en/pack.json",
		JSON.stringify({"ui.location.world": "PACK WINS"}).to_utf8_buffer())
	I18nAPI.reload_text(true)
	if I18nAPI.t("ui.location.world") != "World":
		errors.append("a pack shadows the built-in text ui.location.world")
	# A saved life loads with its pack and is kept while the pack is missing.
	DirAccess.make_dir_recursive_absolute(V1 + "animals")
	_write_bytes(V1 + "animals/zzpk_deer.json",
		JSON.stringify({"name": "Check deer"}).to_utf8_buffer())
	SpeciesAPI.clear_cache()
	if not SpeciesAPI.exists("zzpk_deer"):
		errors.append("the pack's animal isn't known")
		return
	var save_file := "/tmp/animundo-pack-check-%d.json" % OS.get_process_id()
	var old_saves: String = SavesAPI.file_path
	SavesAPI.file_path = save_file
	var life: Dictionary = SavesAPI.create("zzpk_deer", "draft_zzpk1", "safe", "Check")
	var listed: Array = SavesAPI.list()
	if not listed.any(func(a): return a.id == life.id):
		errors.append("a saved life with the pack doesn't load")
	hooks.remove_content_folder(V1)
	SpeciesAPI.clear_cache()
	if (SavesAPI.list() as Array).any(func(a): return a.id == life.id):
		errors.append("a missing pack's life still loads")
	hooks.add_content_folder(V1)
	SpeciesAPI.clear_cache()
	if not (SavesAPI.list() as Array).any(func(a): return a.id == life.id):
		errors.append("the saved life wasn't kept while its pack was missing")
	SavesAPI.remove(life.id)
	SavesAPI.file_path = old_saves
	DirAccess.remove_absolute(save_file)


func _write_version(folder: String, title: String) -> void:
	var region := JSON.stringify({"name": title}).to_utf8_buffer()
	DirAccess.make_dir_recursive_absolute(folder + "regions")
	# Empty folders the game's readers expect; a pack without them is still legal.
	DirAccess.make_dir_recursive_absolute(folder + "animals")
	DirAccess.make_dir_recursive_absolute(folder + "locale/en")
	DirAccess.make_dir_recursive_absolute(folder + "locale/fi")
	_write_bytes(folder + "regions/draft_zzpk1.json", region)
	_write_bytes(folder + "manifest.json", JSON.stringify({
		"draft_id": "zzpk1", "draft_place_id": "draft_zzpk1",
		"place_id": "fi_repovesi",
		"version": 1 if folder == V1 else 2, "format": 1,
		"files": [{
			"path": "regions/draft_zzpk1.json",
			"sha256": PackRules.sha256_hex(region), "bytes": region.size(),
		}],
		"new_species": [], "reused_species": [],
	}).to_utf8_buffer())


func _write_bytes(path: String, data: PackedByteArray) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		errors.append("can't write " + path)
	else:
		f.store_buffer(data)


func _cleanup(hooks: Variant) -> void:
	hooks.remove_content_folder(V1)
	hooks.remove_content_folder(V2)
	hooks.remove_content_folder("user://packs/_addon/1/")
	hooks.set_draft_opener(Callable())
	hooks.remove_grownup_button("explore.builder_mode")
	hooks.remove_places_button("explore.ask_place")
	_remove_dir(V1.trim_suffix("/").get_base_dir())
	_remove_dir("user://packs/_addon")


func _remove_dir(path: String) -> void:
	if not DirAccess.dir_exists_absolute(path):
		return
	for d in DirAccess.get_directories_at(path):
		_remove_dir(path + "/" + d)
	for f in DirAccess.get_files_at(path):
		DirAccess.remove_absolute(path + "/" + f)
	DirAccess.remove_absolute(path)
