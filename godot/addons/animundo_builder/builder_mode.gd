extends Node
## Builder mode: the game-side addon (spec 06).
##
## Installed as the "BuilderMode" autoload by tools/sync_builder.sh. It uses
## only the game's plug-in points (BuilderHooks, the generic Cloud calls) and
## the engine: content folders for installed packs, globe looks for the
## family's drafts, one button on the account screen and one in the places
## menu. The backend is injected through boot(), so the headless checks run
## the same code against stubs with no game and no net.
##
## What it does not do: it never signs in (the game and the web app do that),
## never builds (the worker does that), and never starts gameplay in a draft
## (the plug-in points can't switch scenes: Play installs the pack and the
## family opens it from the places menu).

const WEB_BUILD := "https://animundo.app/build"
const PACKS_DIR := "user://packs"
const STRINGS_FOLDER := "user://packs/_addon/1/"
const LANG_FILE := "user://builder_lang.txt"

const REPORTABLE := ["ready", "in_review", "not_accepted"]
const NOT_RIGHT_REASONS: Array[String] = ["wrong_animals", "looks_wrong",
	"not_like_the_real_place", "other"]
const PROBLEM_REASONS: Array[String] = ["unsuitable_text", "scary_or_upsetting",
	"wrong_safety_advice", "other"]

var _hooks ## BuilderHooks, or a stub in the checks.
var _cloud: BuilderCloud
var _strings := BuilderStrings.new()
var _areas: Array = []
var _drafts: Array = []
var _screen: CanvasLayer
var _explorer := ""
var _look_keys: Array[String] = []
var _loading := false
var _booted := false


func _ready() -> void:
	# In the game the hooks exist; with no game (checks, a bare project)
	# the node stays dormant until boot() is called with stubs.
	if FileAccess.file_exists("res://scripts/builder_hooks.gd"):
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
	_strings.load_lang(_saved_lang())
	_areas = _read_json(BuilderStrings.ADDON_DIR + "/areas.json")
	_install_strings_folder()
	_reinstall_packs()
	_hooks.add_grownup_button("explore.builder_mode", open_builder)
	_hooks.add_places_button("explore.ask_place", open_web_app)
	_booted = true
	refresh_globe()


## The family's drafts get their globe looks. Needs the net; failures keep
## the globe as it was.
func refresh_globe() -> void:
	if not _booted or not _cloud.signed_in():
		return
	var drafts: Array = await _cloud.family_drafts()
	_drafts = drafts
	var keys: Array[String] = []
	for row in drafts:
		if not row is Dictionary:
			continue
		var fields: Dictionary = row.get("fields", {})
		var key := _globe_key(str(row.get("id", "")), fields)
		if key == "":
			continue
		keys.append(key)
	for old in _look_keys:
		if not old in keys:
			_hooks.clear_place_look(old)
	_look_keys = keys


## Opens the Builder screen (the grown-up button's callable).
func open_builder() -> void:
	if not _booted or is_instance_valid(_screen):
		return
	_build_screen()
	_reload()


## Opens the builder web app (the places button's callable).
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


# --- boot ---------------------------------------------------------------

func _saved_lang() -> String:
	if FileAccess.file_exists(LANG_FILE):
		var saved := FileAccess.get_file_as_string(LANG_FILE).strip_edges()
		if saved in BuilderStrings.LANGS:
			return saved
	return BuilderStrings.player_lang()


## The addon's own words as a content folder, so the game's menus can show
## the grown-up and places buttons in the player's language.
func _install_strings_folder() -> void:
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "locale/en")
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "locale/fi")
	# Empty folders the game's readers expect; the folder holds only words.
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "regions")
	DirAccess.make_dir_recursive_absolute(STRINGS_FOLDER + "animals")
	for lang in BuilderStrings.LANGS:
		var src := BuilderStrings.ADDON_DIR + "/locale/" + lang + ".json"
		var dst := STRINGS_FOLDER + "locale/" + lang + "/explore.json"
		var same := FileAccess.file_exists(dst) \
			and FileAccess.get_file_as_string(src) == FileAccess.get_file_as_string(dst)
		if not same:
			DirAccess.copy_absolute(src, dst)
	_hooks.add_content_folder(STRINGS_FOLDER)


## Every installed pack back into the game: checked again, then its folder
## and its models registered. A pack that fails is left out, not removed.
func _reinstall_packs() -> void:
	for draft_id in _pack_ids():
		for version in _pack_versions(draft_id):
			var folder := "%s/%s/%d/" % [PACKS_DIR, draft_id, version]
			if _install_pack(draft_id, version):
				continue
			# A pack that fails any check is ignored, not half installed.
			_hooks.remove_content_folder(folder)
			for entry in _pack_models(folder):
				_hooks.unregister_model(entry)


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


func _install_pack(draft_id: String, version: int) -> bool:
	var folder := "%s/%s/%d/" % [PACKS_DIR, draft_id, version]
	var manifest: Variant = _read_json(folder + "manifest.json")
	if PackRules.manifest_error(manifest) != "":
		return false
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		var bytes := FileAccess.get_file_as_bytes(folder + rel)
		if bytes.size() != int(entry["bytes"]):
			return false
		if PackRules.sha256_hex(bytes) != str(entry["sha256"]):
			return false
	if not _hooks.add_content_folder(folder):
		return false
	for entry in manifest["files"]:
		var path := str(entry["path"])
		if path.begins_with("models/") and path.ends_with(".glb"):
			_hooks.register_model(path.get_file().get_basename(), folder + path)
	return true


func _pack_ids() -> PackedStringArray:
	if not DirAccess.dir_exists_absolute(PACKS_DIR):
		return PackedStringArray()
	var ids := PackedStringArray()
	for d in DirAccess.get_directories_at(PACKS_DIR):
		if d != "_addon" and BuilderCloud.is_draft_id(d):
			ids.append(d)
	return ids


func _pack_versions(draft_id: String) -> Array[int]:
	var out: Array[int] = []
	for d in DirAccess.get_directories_at(PACKS_DIR + "/" + draft_id):
		if d.is_valid_int() and int(d) >= 1:
			out.append(int(d))
	# Newest first: the first folder added wins.
	out.sort()
	out.reverse()
	return out


func _globe_key(draft_id: String, fields: Dictionary) -> String:
	var status := str(fields.get("status", ""))
	var version := int(fields.get("pack_version", 0))
	var installed := version >= 1 and FileAccess.file_exists(
		"%s/%s/%d/manifest.json" % [PACKS_DIR, draft_id, version])
	var look := ExploreStates.look_for(status, true, installed)
	if look == "":
		return ""
	if installed and str(fields.get("draft_place_id", "")) != "":
		var key := str(fields["draft_place_id"])
		_hooks.set_place_look(key, look)
		return key
	var area := _area(str(fields.get("area_id", "")))
	var lat := _center(fields, area, "lat")
	var lon := _center(fields, area, "lon")
	if lat == null or lon == null:
		return ""
	var label := str(fields.get("place_name", ""))
	if label == "" and not area.is_empty():
		label = str(area.get("name", ""))
	_hooks.set_place_look(str(fields.get("area_id", draft_id)), look, lat, lon, label)
	return str(fields.get("area_id", draft_id))


func _center(fields: Dictionary, area: Dictionary, axis: String) -> Variant:
	if fields.get("center") is Dictionary and fields["center"].has(axis):
		return float(fields["center"][axis])
	return area.get(axis)


func _area(area_id: String) -> Dictionary:
	for area in _areas:
		if area is Dictionary and str(area.get("area_id", "")) == area_id:
			return area
	return {}


static func _read_json(path: String) -> Variant:
	if not FileAccess.file_exists(path):
		return [] if path.ends_with("areas.json") else {}
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if path.ends_with("areas.json"):
		return parsed if parsed is Array else []
	return parsed if parsed is Dictionary else {}


# --- the Builder screen --------------------------------------------------

func _build_screen() -> void:
	_screen = CanvasLayer.new()
	_screen.layer = 100
	add_child(_screen)
	var back := ColorRect.new()
	back.color = Color(0.09, 0.13, 0.11, 0.92)
	back.set_anchors_preset(Control.PRESET_FULL_RECT)
	_screen.add_child(back)
	var panel := PanelContainer.new()
	panel.set_anchors_preset(Control.PRESET_CENTER)
	panel.custom_minimum_size = Vector2(620, 460)
	panel.position -= panel.custom_minimum_size / 2
	_screen.add_child(panel)
	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 20)
	margin.add_theme_constant_override("margin_right", 20)
	margin.add_theme_constant_override("margin_top", 16)
	margin.add_theme_constant_override("margin_bottom", 16)
	panel.add_child(margin)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 10)
	margin.add_child(box)
	_screen.set_meta("box", box)
	var title := HBoxContainer.new()
	box.add_child(title)
	var name := Label.new()
	name.text = _strings.t("explore.screen.title")
	title.add_child(name)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.add_child(spacer)
	var lang := Button.new()
	lang.text = _strings.t("explore.screen.lang")
	lang.pressed.connect(_toggle_lang)
	title.add_child(lang)
	var close := Button.new()
	close.text = _strings.t("explore.screen.close")
	close.pressed.connect(_screen.queue_free)
	title.add_child(close)
	var tabs := TabContainer.new()
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(tabs)
	_screen.set_meta("tabs", tabs)
	var status := Label.new()
	status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(status)
	_screen.set_meta("status", status)


func _toggle_lang() -> void:
	var next := "fi" if _strings.lang != "fi" else "en"
	_strings.load_lang(next)
	var f := FileAccess.open(LANG_FILE, FileAccess.WRITE)
	if f != null:
		f.store_string(next)
	_screen.queue_free()
	_build_screen()
	_reload()


func _reload() -> void:
	if not is_instance_valid(_screen) or _loading:
		return
	_loading = true
	var status: Label = _screen.get_meta("status")
	var tabs: TabContainer = _screen.get_meta("tabs")
	for tab in tabs.get_children():
		tabs.remove_child(tab)
		tab.queue_free()
	if not _cloud.signed_in():
		status.text = _strings.t("explore.screen.signed_out")
		_loading = false
		return
	status.text = _strings.t("explore.screen.loading")
	_drafts = await _cloud.family_drafts()
	if not is_instance_valid(_screen):
		_loading = false
		return
	tabs.add_child(_areas_tab())
	tabs.add_child(_drafts_tab())
	status.text = ""
	_loading = false


func _areas_tab() -> Control:
	var scroll := ScrollContainer.new()
	scroll.name = _strings.t("explore.screen.areas")
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 8)
	scroll.add_child(box)
	for area in _areas:
		if not area is Dictionary:
			continue
		var row := VBoxContainer.new()
		box.add_child(row)
		var head := HBoxContainer.new()
		row.add_child(head)
		var name := Label.new()
		name.text = "%s (%s)" % [area.get("name", "?"), area.get("country", "?")]
		name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		head.add_child(name)
		var draft := _draft_for_area(str(area.get("area_id", "")))
		if draft.is_empty():
			var build := Button.new()
			build.text = _strings.t("explore.screen.build_web")
			build.pressed.connect(open_web_app)
			head.add_child(build)
		else:
			var words := Label.new()
			words.text = ExploreStates.words_for(str(draft.get("status", "")), _strings)
			words.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			row.add_child(words)
	return scroll


func _drafts_tab() -> Control:
	var scroll := ScrollContainer.new()
	scroll.name = _strings.t("explore.screen.drafts")
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 12)
	scroll.add_child(box)
	if _drafts.is_empty():
		var none := Label.new()
		none.text = _strings.t("explore.screen.no_drafts")
		none.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		box.add_child(none)
	for row in _drafts:
		if row is Dictionary:
			box.add_child(_draft_row(row))
	return scroll


func _draft_row(row: Dictionary) -> Control:
	var fields: Dictionary = row.get("fields", {})
	var card := VBoxContainer.new()
	card.add_theme_constant_override("separation", 4)
	var title := Label.new()
	title.text = "%s  ·  %s" % [_draft_name(str(row.get("id", "")), fields),
		_strings.t("explore.screen.round", {"n": int(fields.get("round", 1))})]
	card.add_child(title)
	var words := Label.new()
	words.text = ExploreStates.words_for(str(fields.get("status", "")), _strings)
	words.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	card.add_child(words)
	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 8)
	card.add_child(buttons)
	if int(fields.get("pack_version", 0)) >= 1:
		var play := Button.new()
		play.text = _strings.t("explore.screen.play")
		play.pressed.connect(_play.bind(str(row.get("id", "")), play))
		buttons.add_child(play)
	if _reportable(str(row.get("id", "")), fields):
		var report := Button.new()
		report.text = _strings.t("explore.screen.report")
		report.pressed.connect(_report.bind(str(row.get("id", "")), fields))
		buttons.add_child(report)
	var web := Button.new()
	web.text = _strings.t("explore.screen.open_web")
	web.pressed.connect(open_web_app)
	buttons.add_child(web)
	return card


func _draft_name(draft_id: String, fields: Dictionary) -> String:
	if str(fields.get("place_name", "")) != "":
		return str(fields["place_name"])
	var area := _area(str(fields.get("area_id", "")))
	if not area.is_empty():
		return str(area.get("name", draft_id))
	return draft_id


func _draft_for_area(area_id: String) -> Dictionary:
	for row in _drafts:
		if row is Dictionary and str(row.get("fields", {}).get("area_id", "")) == area_id:
			return row["fields"]
	return {}


func _reportable(draft_id: String, fields: Dictionary) -> bool:
	return draft_id != "" and str(fields.get("status", "")) in REPORTABLE \
		and str(fields.get("owner_uid", "")) == _cloud.uid() \
		and not fields.has("feedback")


## Downloads the draft's pack, checks it, installs it and records playing.
func _play(draft_id: String, play: Button) -> void:
	if not is_instance_valid(_screen):
		return
	var status: Label = _screen.get_meta("status")
	play.disabled = true
	status.text = _strings.t("explore.screen.playing")
	var draft := await _cloud.get_draft(draft_id)
	var version := int(draft.get("pack_version", 0))
	var ok := version >= 1 and await _download_pack(draft_id, version) \
		and _install_pack(draft_id, version)
	if ok:
		refresh_globe()
		if not await _cloud.record_played(draft_id):
			ok = false
	status.text = _strings.t("explore.screen.installed") if ok \
		else _strings.t("explore.screen.error")
	play.disabled = false


func _download_pack(draft_id: String, version: int) -> bool:
	var raw := await _cloud.read_pack_file(draft_id, version, "manifest.json", 65536)
	if raw.is_empty():
		return false
	var manifest: Variant = JSON.parse_string(raw.get_string_from_utf8())
	if PackRules.manifest_error(manifest) != "":
		return false
	var folder := "%s/%s/%d/" % [PACKS_DIR, draft_id, version]
	DirAccess.make_dir_recursive_absolute(folder)
	if not _write_file(folder + "manifest.json", raw):
		return false
	for entry in manifest["files"]:
		var rel := str(entry["path"])
		var data := await _cloud.read_pack_file(
			draft_id, version, rel, int(entry["bytes"]))
		if data.size() != int(entry["bytes"]):
			return false
		if PackRules.sha256_hex(data) != str(entry["sha256"]):
			return false
		DirAccess.make_dir_recursive_absolute(folder + rel.get_base_dir())
		if not _write_file(folder + rel, data):
			return false
	return true


static func _write_file(path: String, data: PackedByteArray) -> bool:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		return false
	f.store_buffer(data)
	return true


# --- the report form ---------------------------------------------------

func _report(draft_id: String, _fields: Dictionary) -> void:
	if not is_instance_valid(_screen):
		return
	if _screen.has_meta("form") and is_instance_valid(_screen.get_meta("form")):
		(_screen.get_meta("form") as Control).queue_free()
	var box: VBoxContainer = _screen.get_meta("box")
	var form := VBoxContainer.new()
	form.add_theme_constant_override("separation", 6)
	box.add_child(form)
	_screen.set_meta("form", form)
	var title := Label.new()
	title.text = _strings.t("explore.report.title")
	form.add_child(title)
	var kinds := OptionButton.new()
	kinds.add_item(_strings.t("explore.report.kind_not_right"), 0)
	kinds.add_item(_strings.t("explore.report.kind_problem"), 1)
	form.add_child(kinds)
	var reasons := OptionButton.new()
	form.add_child(reasons)
	var fill := func(_i := -1):
		reasons.clear()
		var keys := NOT_RIGHT_REASONS if kinds.selected == 0 else PROBLEM_REASONS
		for key in keys:
			reasons.add_item(_strings.t("explore.reason." + key))
	kinds.item_selected.connect(fill)
	fill.call()
	var note := LineEdit.new()
	note.placeholder_text = _strings.t("explore.report.note_hint")
	note.max_length = 300
	form.add_child(note)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	form.add_child(row)
	var send := Button.new()
	send.text = _strings.t("explore.report.send")
	send.pressed.connect(_send_report.bind(draft_id, kinds, reasons, note, send))
	row.add_child(send)
	var cancel := Button.new()
	cancel.text = _strings.t("explore.screen.close")
	cancel.pressed.connect(form.queue_free)
	row.add_child(cancel)


func _send_report(draft_id: String, kinds: OptionButton, reasons: OptionButton,
		note: LineEdit, send: Button) -> void:
	if not is_instance_valid(_screen):
		return
	var status: Label = _screen.get_meta("status")
	send.disabled = true
	var keys := NOT_RIGHT_REASONS if kinds.selected == 0 else PROBLEM_REASONS
	var kind := "not_right" if kinds.selected == 0 else "problem"
	var ok := await _cloud.send_feedback(
		draft_id, kind, keys[reasons.selected], note.text)
	if not is_instance_valid(_screen):
		return
	if ok:
		status.text = _strings.t("explore.report.sent")
		if _screen.has_meta("form") and is_instance_valid(_screen.get_meta("form")):
			(_screen.get_meta("form") as Control).queue_free()
		_reload()
	else:
		status.text = _strings.t("explore.report.send_failed")
		send.disabled = false
