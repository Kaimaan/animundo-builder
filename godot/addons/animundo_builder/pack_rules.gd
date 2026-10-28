class_name PackRules
extends RefCounted
## What a content pack must look like before the game touches it.
##
## Mirrors the worker's pack-manifest.schema.json and the rules in spec 01:
## data only (JSON, .glb, .png), every file hashed, a .glb at most 2 MB, a
## whole pack at most 8 MB, and no format newer than this game reads. Pure
## static functions, so the headless checks run them with no game and no net.

const PACK_FORMAT := 1
const MAX_GLB_BYTES := 2 * 1024 * 1024
const MAX_PACK_BYTES := 8 * 1024 * 1024
## The game's id pattern (BuilderHooks.ID_PATTERN): a region the game
## can't find must fail here, not after installing (#93).
const PLACE_ID_PATTERN := "^[a-z0-9_]+$"
const DATA_DIRS: Array[String] = ["regions/", "animals/", "models/", "locale/", "previews/"]
const DATA_EXTS: Array[String] = [".json", ".glb", ".png"]


## "" when the parsed manifest is usable, else why it isn't.
static func manifest_error(manifest: Variant) -> String:
	if not manifest is Dictionary:
		return "manifest is not an object"
	for key in ["draft_id", "draft_place_id", "place_id", "version", "format",
			"files", "new_species", "reused_species"]:
		if not manifest.has(key):
			return "manifest misses " + key
	if str(manifest["draft_id"]) == "" or str(manifest["place_id"]) == "":
		return "manifest has an empty id"
	if RegEx.create_from_string(PLACE_ID_PATTERN).search(
			str(manifest["draft_place_id"])) == null:
		return "draft_place_id is not a game id"
	if not _is_int(manifest["version"]) or int(manifest["version"]) < 1:
		return "version is not a positive integer"
	if not _is_int(manifest["format"]) or int(manifest["format"]) < 1:
		return "format is not a positive integer"
	if int(manifest["format"]) > PACK_FORMAT:
		return "pack format is newer than this game reads"
	for key in ["new_species", "reused_species"]:
		if not manifest[key] is Array:
			return key + " is not an array"
	var files: Variant = manifest["files"]
	if not files is Array or files.is_empty():
		return "files is not a non-empty array"
	var total := 0
	for entry in files:
		if not entry is Dictionary:
			return "a file entry is not an object"
		for key in ["path", "sha256", "bytes"]:
			if not entry.has(key):
				return "a file entry misses " + key
		if not _is_hex64(str(entry["sha256"])):
			return "bad sha256 for " + str(entry.get("path", "?"))
		if not _is_int(entry["bytes"]):
			return "bad size for " + str(entry["path"])
		var err := file_error(str(entry["path"]), int(entry["bytes"]))
		if err != "":
			return err
		total += int(entry["bytes"])
	if total > MAX_PACK_BYTES:
		return "pack is bigger than 8 MB"
	return ""


## "" when one pack file may be used, else why it may not.
static func file_error(path: String, size: int) -> String:
	if path == "" or path.begins_with("/") or ".." in path:
		return "bad path " + path
	var in_dir := false
	for dir in DATA_DIRS:
		if path.begins_with(dir):
			in_dir = true
	if not in_dir:
		return "file outside the pack folders: " + path
	var is_data := false
	for ext in DATA_EXTS:
		if path.ends_with(ext):
			is_data = true
	if not is_data:
		return "not data: " + path
	if size < 0:
		return "negative size: " + path
	if path.ends_with(".glb") and size > MAX_GLB_BYTES:
		return ".glb bigger than 2 MB: " + path
	return ""


static func sha256_hex(data: PackedByteArray) -> String:
	var h := HashingContext.new()
	h.start(HashingContext.HASH_SHA256)
	h.update(data)
	var digest: PackedByteArray = h.finish()
	return digest.hex_encode()


## JSON numbers arrive as floats; accept whole ones too.
static func _is_int(v: Variant) -> bool:
	return (v is int or v is float) and int(v) == v


static func _is_hex64(s: String) -> bool:
	if s.length() != 64:
		return false
	for c in s:
		var n := c.unicode_at(0)
		var digit := n >= 48 and n <= 57
		var hex := n >= 97 and n <= 102
		if not digit and not hex:
			return false
	return true
