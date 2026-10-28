extends SceneTree
## Builder mode's globe (spec 06): for every status a draft can have, in both
## languages, the look and the plain sentence; the grown-up and places
## buttons; the Builder screen's loading, signed-out and failure states; and
## Play and Report through a stub Cloud (no net, in or out of the game).
## Run: godot --headless --path <project> --script
##      res://addons/animundo_builder/tests/check_explore_states.gd

const ADDON := "res://addons/animundo_builder"
const PACK := "user://packs/zzrd1/1/"
const STRINGS_FOLDER := "user://packs/_addon/1/"

var errors: Array[String] = []
var game := false
var I18nAPI = null


func _init() -> void:
	game = ResourceLoader.exists("res://scripts/builder_hooks.gd")
	if game:
		I18nAPI = load("res://scripts/i18n.gd")
		I18nAPI.start("en")
	_check_pure()
	_run.call_deferred()


func _check_pure() -> void:
	for lang in BuilderStrings.LANGS:
		var strings := BuilderStrings.new()
		if not strings.load_lang(lang):
			errors.append("the %s words can't be read" % lang)
			continue
		for status in ExploreStates.STATUSES:
			var words := ExploreStates.words_for(status, strings)
			if words == "" or words == "explore.state." + status:
				errors.append("%s has no %s sentence" % [status, lang])
	var look: Callable = ExploreStates.look_for
	for args in [["ready", true, true, "your_draft"],
			["ready", true, false, "being_explored"],
			["in_review", true, true, "your_draft"],
			["not_accepted", true, false, "being_explored"],
			["requested", true, false, "being_explored"],
			["building", true, false, "being_explored"],
			["improving", true, false, "being_explored"],
			["not_possible_yet", true, false, ""],
			["public", true, true, ""], ["public", false, false, ""],
			["failed", true, false, ""], ["expired", true, false, ""],
			["ready", false, false, ""], ["mystery", true, true, ""]]:
		if look.call(args[0], args[1], args[2]) != args[3]:
			errors.append("look for %s (family %s, installed %s) isn't %s" % args)


func _run() -> void:
	var hooks = load("res://scripts/builder_hooks.gd") if game else StubHooks.new()
	var cloud := StubCloud.new()
	cloud.uid = "grownup1"
	cloud.drafts = [{
		"id": "zzrd1",
		"fields": {
			"status": "ready", "family_uids": ["grownup1"],
			"owner_uid": "grownup1", "area_id": "fi_repovesi",
			"draft_place_id": "draft_zzrd1", "place_name": "Repovesi",
			"round": 2, "pack_version": 1,
			"center": {"lat": 61.18, "lon": 26.88},
		},
	}]
	_write_pack()
	var Mode = load(ADDON + "/builder_mode.gd")
	var mode = Mode.new()
	mode.boot(hooks, cloud)
	await process_frame
	await process_frame
	_check_booted(mode, hooks)
	await _check_screen(mode, cloud)
	await _check_failures(mode, hooks, cloud)
	_check_game_text(hooks)
	mode.free()
	_cleanup(hooks)
	for e in errors:
		printerr("ERROR: ", e)
	print("explore states check: %d errors" % errors.size())
	quit(1 if errors.size() > 0 else 0)


func _check_booted(mode: Node, hooks: Variant) -> void:
	var grownup: Array = hooks.grownup_buttons()
	if not grownup.any(func(b): return b.label_key == "explore.builder_mode"):
		errors.append("no grown-up button for Builder mode")
	var places: Array = hooks.places_buttons()
	if not places.any(func(b): return b.label_key == "explore.ask_place"):
		errors.append("no places button for a new place")
	var folders: PackedStringArray = hooks.content_folders()
	if not STRINGS_FOLDER in folders:
		errors.append("the addon's words aren't a content folder")
	if not PACK in folders:
		errors.append("the installed pack wasn't registered")
	var looks: Dictionary = hooks.place_looks()
	if looks.get("draft_zzrd1", {}).get("look", "") != "your_draft":
		errors.append("the installed draft isn't your_draft on the globe: %s" % [looks])


func _wait_idle(mode: Variant) -> void:
	for i in 60:
		if not mode._loading:
			return
		await process_frame
	errors.append("the Builder screen never finished loading")


func _check_screen(mode: Node, cloud: StubCloud) -> void:
	mode.open_builder()
	await _wait_idle(mode)
	var screen := _screen_of(mode)
	if screen == null:
		errors.append("the Builder screen didn't open")
		return
	var make := screen.get_meta("make") as Button
	if make == null or make.text != mode._strings.t("explore.screen.make_home"):
		errors.append("the Builder screen has no Make a new home button")
	var list := screen.get_meta("list") as VBoxContainer
	if list == null or list.get_child_count() != 1:
		errors.append("the Builder screen shows no drafts list")
	cloud.uid = ""
	screen.queue_free()
	await process_frame
	mode.open_builder()
	await _wait_idle(mode)
	screen = _screen_of(mode)
	if screen == null:
		errors.append("the Builder screen didn't reopen signed out")
	elif _status_of(screen) != mode._strings.t("explore.screen.signed_out"):
		errors.append("signed out shows drafts instead of the sign-in words")
	cloud.uid = "grownup1"
	if screen != null:
		screen.queue_free()
		await process_frame


func _check_failures(mode: Node, hooks: Variant, cloud: StubCloud) -> void:
	cloud.fail["firestore_query"] = true
	await mode.refresh_globe()
	await process_frame
	if not (hooks.place_looks() as Dictionary).is_empty():
		errors.append("a failed load leaves stale looks on the globe")
	cloud.fail.erase("firestore_query")
	await mode.refresh_globe()
	cloud.fail["storage_get"] = true
	mode.open_builder()
	await _wait_idle(mode)
	var screen := _screen_of(mode)
	if screen == null:
		errors.append("the Builder screen didn't reopen")
		return
	var play := Button.new()
	await mode._play("zzrd1", play)
	if _status_of(screen) != mode._strings.t("explore.screen.error"):
		errors.append("a failed download shows no error")
	cloud.fail.erase("storage_get")
	cloud.packs["zzrd1/1/manifest.json"] = _manifest_bytes()
	cloud.packs["zzrd1/1/regions/draft_zzrd1.json"] = _region_bytes()
	await mode._play("zzrd1", play)
	if _status_of(screen) != mode._strings.t("explore.screen.installed"):
		errors.append("a good download doesn't install: %s" % _status_of(screen))
	var played: Array = cloud.patched.filter(
		func(p): return p.path == "drafts/zzrd1" and p.fields.has("last_played_at"))
	if played.is_empty():
		errors.append("playing isn't recorded on the draft")
	mode._report("zzrd1", {})
	await process_frame
	var form: Control = screen.get_meta("form")
	var kinds := form.get_child(1) as OptionButton
	var reasons := form.get_child(2) as OptionButton
	var note := form.get_child(3) as LineEdit
	var send := (form.get_child(4) as HBoxContainer).get_child(0) as Button
	note.text = "  hello  "
	await mode._send_report("zzrd1", kinds, reasons, note, send)
	var fb: Array = cloud.patched.filter(
		func(p): return p.path == "drafts/zzrd1" and p.fields.has("feedback"))
	if fb.is_empty() or fb[0].fields.feedback.get("kind") != "not_right" \
			or fb[0].fields.feedback.get("reason") != "wrong_animals" \
			or fb[0].fields.feedback.get("note") != "hello":
		errors.append("the report isn't sent in the rules' shape: %s" % [fb])
	cloud.fail["firestore_patch"] = true
	mode._report("zzrd1", {})
	await process_frame
	form = screen.get_meta("form")
	kinds = form.get_child(1) as OptionButton
	reasons = form.get_child(2) as OptionButton
	note = form.get_child(3) as LineEdit
	send = (form.get_child(4) as HBoxContainer).get_child(0) as Button
	await mode._send_report("zzrd1", kinds, reasons, note, send)
	if _status_of(screen) != mode._strings.t("explore.report.send_failed"):
		errors.append("a failed report shows no error")
	cloud.fail.erase("firestore_patch")
	play.free()
	screen.queue_free()
	await process_frame


func _check_game_text(hooks: Variant) -> void:
	if not game:
		return
	if I18nAPI.t("explore.builder_mode") != "Builder mode":
		errors.append("the grown-up button has no English text in the game")
	I18nAPI.start("fi")
	if I18nAPI.t("explore.builder_mode") != "Rakentajatila":
		errors.append("the grown-up button has no Finnish text in the game")
	I18nAPI.start("en")
	var looks: Dictionary = hooks.place_looks()
	if not looks.has("draft_zzrd1"):
		errors.append("the draft's look didn't reach the game's globe")


func _screen_of(mode: Node) -> CanvasLayer:
	for child in mode.get_children():
		if child is CanvasLayer:
			return child
	return null


func _status_of(screen: CanvasLayer) -> String:
	return (screen.get_meta("status") as Label).text


func _region_bytes() -> PackedByteArray:
	return JSON.stringify({"name": "Repovesi (draft)"}).to_utf8_buffer()


func _manifest_bytes() -> PackedByteArray:
	var region := _region_bytes()
	return JSON.stringify({
		"draft_id": "zzrd1", "draft_place_id": "draft_zzrd1",
		"place_id": "fi_repovesi", "version": 1, "format": 1,
		"files": [{
			"path": "regions/draft_zzrd1.json",
			"sha256": PackRules.sha256_hex(region), "bytes": region.size(),
		}],
		"new_species": [], "reused_species": [],
	}).to_utf8_buffer()


func _write_pack() -> void:
	DirAccess.make_dir_recursive_absolute(PACK + "regions")
	# Empty folders the game's readers expect; a pack without them is still legal.
	DirAccess.make_dir_recursive_absolute(PACK + "animals")
	DirAccess.make_dir_recursive_absolute(PACK + "locale/en")
	DirAccess.make_dir_recursive_absolute(PACK + "locale/fi")
	_write_bytes(PACK + "manifest.json", _manifest_bytes())
	_write_bytes(PACK + "regions/draft_zzrd1.json", _region_bytes())


func _write_bytes(path: String, data: PackedByteArray) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		errors.append("can't write " + path)
	else:
		f.store_buffer(data)


func _cleanup(hooks: Variant) -> void:
	hooks.remove_content_folder(PACK)
	hooks.remove_content_folder(STRINGS_FOLDER)
	for key in hooks.place_looks():
		hooks.clear_place_look(key)
	hooks.remove_grownup_button("explore.builder_mode")
	hooks.remove_places_button("explore.ask_place")
	_remove_dir("user://packs/zzrd1")
	_remove_dir("user://packs/_addon")


func _remove_dir(path: String) -> void:
	if not DirAccess.dir_exists_absolute(path):
		return
	for d in DirAccess.get_directories_at(path):
		_remove_dir(path + "/" + d)
	for f in DirAccess.get_files_at(path):
		DirAccess.remove_absolute(path + "/" + f)
	DirAccess.remove_absolute(path)
