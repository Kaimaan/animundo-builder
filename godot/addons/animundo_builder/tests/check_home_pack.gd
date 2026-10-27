extends SceneTree
## A home's real pack installs like any draft place (#52.3).
##
## The fixture is the fox's pack from jobs/review-fox, replayed intake to
## pack: a region-only manifest, nothing new, 16 reused species. Its ids
## are normalized to review_fox (see fixture/fox-pack/README.md). The rules
## half runs everywhere; the game half runs only in the game.
## Run: godot --headless --path <project> --script
##      res://addons/animundo_builder/tests/check_home_pack.gd

const ADDON := "res://addons/animundo_builder"
const FIXTURE := ADDON + "/tests/fixture/fox-pack"
const FOLDER := "user://packs/review_fox/1/"
const DRAFT_PLACE := "draft_review_fox"

var errors: Array[String] = []
var game := false
var RegionAPI = null


func _init() -> void:
	game = FileAccess.file_exists("res://scripts/builder_hooks.gd")
	if game:
		RegionAPI = load("res://scripts/region.gd")
		load("res://scripts/i18n.gd").start("en")
	_check_fixture()
	_run.call_deferred()


func _read_json(path: String) -> Variant:
	if not FileAccess.file_exists(path):
		return null
	return JSON.parse_string(FileAccess.get_file_as_string(path))


func _check_fixture() -> void:
	var manifest: Variant = _read_json(FIXTURE + "/manifest.json")
	if PackRules.manifest_error(manifest) != "":
		errors.append("the fox manifest is refused: %s" % PackRules.manifest_error(manifest))
		return
	if manifest["draft_place_id"] != DRAFT_PLACE:
		errors.append("the fixture is for %s, not %s" % [manifest["draft_place_id"], DRAFT_PLACE])
	if not manifest["new_species"].is_empty():
		errors.append("the fox pack should make nothing new")
	if manifest["reused_species"].is_empty():
		errors.append("the fox pack reuses nothing")
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		var bytes := FileAccess.get_file_as_bytes(FIXTURE + "/" + rel)
		if bytes.size() != int(entry["bytes"]):
			errors.append("fixture %s has the wrong size" % rel)
		elif PackRules.sha256_hex(bytes) != str(entry["sha256"]):
			errors.append("fixture %s has the wrong hash" % rel)
	var region: Variant = _read_json(FIXTURE + "/regions/" + DRAFT_PLACE + ".json")
	if not region is Dictionary or str(region.get("id", "")) != DRAFT_PLACE:
		errors.append("the fixture region isn't the draft place")


func _run() -> void:
	if not errors.is_empty():
		for e in errors:
			printerr("ERROR: ", e)
		print("home pack check: %d errors" % errors.size())
		quit(1)
		return
	var hooks = load("res://scripts/builder_hooks.gd") if game else StubHooks.new()
	_install_fixture()
	var Mode = load(ADDON + "/builder_mode.gd")
	var mode = Mode.new()
	mode.boot(hooks, StubCloud.new())
	_check_installed(hooks)
	if game:
		_check_game()
	mode.free()
	_cleanup(hooks)
	for e in errors:
		printerr("ERROR: ", e)
	print("home pack check: %d errors" % errors.size())
	quit(1 if errors.size() > 0 else 0)


func _install_fixture() -> void:
	var manifest: Variant = _read_json(FIXTURE + "/manifest.json")
	DirAccess.make_dir_recursive_absolute(FOLDER + "regions")
	# Empty folders the game's readers expect; a pack without them is still legal.
	DirAccess.make_dir_recursive_absolute(FOLDER + "locale/en")
	DirAccess.make_dir_recursive_absolute(FOLDER + "locale/fi")
	_copy_bytes(FIXTURE + "/manifest.json", FOLDER + "manifest.json")
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		DirAccess.make_dir_recursive_absolute(FOLDER + rel.get_base_dir())
		_copy_bytes(FIXTURE + "/" + rel, FOLDER + rel)


func _copy_bytes(src: String, dest: String) -> void:
	var f := FileAccess.open(dest, FileAccess.WRITE)
	if f == null:
		errors.append("can't write " + dest)
	else:
		f.store_buffer(FileAccess.get_file_as_bytes(src))


func _check_installed(hooks: Variant) -> void:
	var folders: PackedStringArray = hooks.content_folders()
	if not FOLDER in folders:
		errors.append("the fox pack isn't registered: %s" % [folders])
		return
	var found := str(hooks.find_file("regions", DRAFT_PLACE))
	if found != FOLDER + "regions/" + DRAFT_PLACE + ".json":
		errors.append("the fox place isn't found: %s" % found)
		return
	var region: Variant = _read_json(found)
	if not region is Dictionary or str(region.get("id", "")) != DRAFT_PLACE:
		errors.append("the installed region isn't the draft place")
	elif str(region.get("name", "")) != "Fox forest, Finland":
		errors.append("the fox place has the wrong name: %s" % region.get("name", ""))


func _check_game() -> void:
	var ids: PackedStringArray = RegionAPI.list_ids()
	if not DRAFT_PLACE in ids:
		errors.append("the fox place isn't listed")
	elif RegionAPI.load_region(DRAFT_PLACE).title() != "Fox forest, Finland":
		errors.append("the fox place has the wrong title")


func _cleanup(hooks: Variant) -> void:
	hooks.remove_content_folder(FOLDER)
	hooks.remove_content_folder("user://packs/_addon/1/")
	hooks.remove_grownup_button("explore.builder_mode")
	hooks.remove_places_button("explore.ask_place")
	_remove_dir("user://packs/review_fox")
	_remove_dir("user://packs/_addon")


func _remove_dir(path: String) -> void:
	if not DirAccess.dir_exists_absolute(path):
		return
	for d in DirAccess.get_directories_at(path):
		_remove_dir(path + "/" + d)
	for f in DirAccess.get_files_at(path):
		DirAccess.remove_absolute(path + "/" + f)
	DirAccess.remove_absolute(path)
