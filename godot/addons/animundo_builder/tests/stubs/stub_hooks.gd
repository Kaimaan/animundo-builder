class_name StubHooks
extends RefCounted
## BuilderHooks from memory: folders, looks, models and buttons, no game.
## Mirrors the real checks (folder pattern, look names, model files) so a
## check that passes here behaves the same against the game.

var folders := PackedStringArray()
var looks := {}
var models := {}
var grownup: Array = []
var places: Array = []
var opener := Callable()


func add_content_folder(path: String) -> bool:
	if RegEx.create_from_string("^user://packs/[a-z0-9_]+/[0-9]+/$").search(path) == null:
		return false
	if not DirAccess.dir_exists_absolute(path):
		return false
	if not path in folders:
		folders.append(path)
	return true


func remove_content_folder(path: String) -> void:
	if path in folders:
		folders.remove_at(folders.find(path))


func content_folders() -> PackedStringArray:
	return folders.duplicate()


func is_id(id: String) -> bool:
	return RegEx.create_from_string("^[a-z0-9_]+$").search(id) != null


func find_file(sub_dir: String, id: String, ext := ".json") -> String:
	if not is_id(id):
		return ""
	for folder in folders:
		var path := folder + sub_dir + "/" + id + ext
		if FileAccess.file_exists(path):
			return path
	return ""


func register_model(species: String, glb_path: String) -> bool:
	if species == "" or not glb_path.begins_with("user://") \
			or not glb_path.ends_with(".glb") or not FileAccess.file_exists(glb_path):
		return false
	models[species] = glb_path
	return true


func unregister_model(species: String) -> void:
	models.erase(species)


func set_place_look(key: String, look: String, lat := NAN, lon := NAN, label := "") -> bool:
	if key == "" or not look in ["being_explored", "your_draft", "not_possible_yet"]:
		return false
	var entry := {"look": look, "label": label}
	if not is_nan(lat) and not is_nan(lon):
		if absf(lat) > 90.0 or absf(lon) > 180.0:
			return false
		entry["lat"] = lat
		entry["lon"] = lon
	looks[key] = entry
	return true


func clear_place_look(key: String) -> void:
	looks.erase(key)


func place_looks() -> Dictionary:
	return looks.duplicate(true)


func add_grownup_button(label_key: String, open: Callable) -> void:
	for b in grownup:
		if b.label_key == label_key:
			b.open = open
			return
	grownup.append({"label_key": label_key, "open": open})


func remove_grownup_button(label_key: String) -> void:
	grownup = grownup.filter(func(b): return b.label_key != label_key)


func grownup_buttons() -> Array:
	return grownup.duplicate()


func add_places_button(label_key: String, open: Callable) -> void:
	for b in places:
		if b.label_key == label_key:
			b.open = open
			return
	places.append({"label_key": label_key, "open": open})


func remove_places_button(label_key: String) -> void:
	places = places.filter(func(b): return b.label_key != label_key)


func places_buttons() -> Array:
	return places.duplicate()


func set_draft_opener(open: Callable) -> void:
	opener = open


func draft_opener() -> Callable:
	return opener


var steps: Array = []  # [{step, ok, detail}]


func draft_step(step: String, ok: bool, detail := "") -> void:
	steps.append({"step": step, "ok": ok, "detail": detail})
