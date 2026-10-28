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

const REPORTABLE := ["ready", "in_review", "not_accepted"]
const NOT_RIGHT_REASONS: Array[String] = ["wrong_animals", "looks_wrong",
	"not_like_the_real_place", "other"]
const PROBLEM_REASONS: Array[String] = ["unsuitable_text", "scary_or_upsetting",
	"wrong_safety_advice", "other"]

var _hooks ## BuilderHooks, or a stub in the checks.
var _cloud: BuilderCloud
var _strings := BuilderStrings.new()
var _drafts: Array = []
var _screen: CanvasLayer
var _explorer := ""
var _look_keys: Array[String] = []
var _loading := false
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
	_strings.load_lang(BuilderStrings.player_lang())
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
	# The game's language, read fresh: the player may have changed it
	# since boot, and the game's settings are the one place to choose (#71).
	_strings.load_lang(BuilderStrings.player_lang())
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
	var lat := _center(fields, "lat")
	var lon := _center(fields, "lon")
	if lat == null or lon == null:
		return ""
	var label := str(fields.get("place_name", ""))
	_hooks.set_place_look(str(fields.get("area_id", draft_id)), look, lat, lon, label)
	return str(fields.get("area_id", draft_id))


func _center(fields: Dictionary, axis: String) -> Variant:
	if fields.get("center") is Dictionary and fields["center"].has(axis):
		return float(fields["center"][axis])
	return null


static func _read_json(path: String) -> Variant:
	if not FileAccess.file_exists(path):
		return {}
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
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
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.13, 0.17, 0.14)
	style.set_corner_radius_all(12)
	panel.add_theme_stylebox_override("panel", style)
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
	var close := Button.new()
	close.text = "✕"
	close.tooltip_text = _strings.t("explore.screen.close")
	close.pressed.connect(_screen.queue_free)
	title.add_child(close)
	var make := Button.new()
	make.text = _strings.t("explore.screen.make_home")
	make.pressed.connect(open_web_app)
	box.add_child(make)
	_screen.set_meta("make", make)
	var list := VBoxContainer.new()
	list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(list)
	_screen.set_meta("list", list)
	var status := Label.new()
	status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(status)
	_screen.set_meta("status", status)


func _reload() -> void:
	if not is_instance_valid(_screen) or _loading:
		return
	_loading = true
	var status: Label = _screen.get_meta("status")
	var list: VBoxContainer = _screen.get_meta("list")
	for row in list.get_children():
		list.remove_child(row)
		row.queue_free()
	if not _cloud.signed_in():
		status.text = _strings.t("explore.screen.signed_out")
		_loading = false
		return
	status.text = _strings.t("explore.screen.loading")
	_drafts = await _cloud.family_drafts()
	if not is_instance_valid(_screen):
		_loading = false
		return
	list.add_child(_drafts_list())
	status.text = ""
	_loading = false


func _drafts_list() -> Control:
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
	return draft_id


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
