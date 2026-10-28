class_name BuilderCloud
extends RefCounted
## The game's Cloud, seen by Builder mode: one draft, its pack, playing it.
##
## The backend is injected (the game's Cloud autoload in the game, a stub in
## the checks), and only the generic calls from spec 06 are used: the signed
## in grown-up's uid, one document, one patch, one file. Every call can
## fail; failures come back as empty values, never crashes.

var _cloud


func _init(cloud = null) -> void:
	_cloud = cloud


func uid() -> String:
	if _cloud == null:
		return ""
	return str(_cloud.uid)


func signed_in() -> bool:
	return uid() != ""


## One draft's plain fields, or {} when it isn't there or can't be read.
func get_draft(draft_id: String) -> Dictionary:
	if not signed_in() or not is_draft_id(draft_id):
		return {}
	return await _cloud.firestore_get("drafts/" + draft_id)


## Records that the family played the draft. Timestamps are UTC ISO strings,
## which the game's Cloud sends as Firestore timestamps.
func record_played(draft_id: String) -> bool:
	if not signed_in() or not is_draft_id(draft_id):
		return false
	return await _cloud.firestore_patch("drafts/" + draft_id, {
		"last_played_at": utc_now(), "updated_at": utc_now()})


## One pack file's bytes, or empty when it can't be read.
func read_pack_file(draft_id: String, version: int, rel_path: String, max_bytes: int) -> PackedByteArray:
	if not signed_in() or not is_draft_id(draft_id) or version < 1:
		return PackedByteArray()
	if rel_path != "manifest.json" and PackRules.file_error(rel_path, 0) != "":
		return PackedByteArray()
	return await _cloud.storage_get(
		"packs/%s/%d/%s" % [draft_id, version, rel_path], max_bytes)


static func utc_now() -> String:
	# No space: the game's Cloud only sends `...T...Z` as a timestamp (#91).
	return Time.get_datetime_string_from_system(true, false) + "Z"


static func is_draft_id(draft_id: String) -> bool:
	return RegEx.create_from_string("^[A-Za-z0-9_\\-]+$").search(draft_id) != null
