class_name StubCloud
extends RefCounted
## A Cloud that answers from memory: drafts, packs and failures, no net.
## The headless checks drive the addon through this; the game's real Cloud
## is never touched (its calls would reach the network).

var uid := ""
var drafts: Array = []  # [{id, fields}]
var packs := {}  # "draft_id/version/rel_path" -> PackedByteArray
var fail := {}  # "firestore_query", "firestore_get", "firestore_patch", "storage_get"
var patched: Array = []  # [{path, fields}]


func firestore_query(collection: String, field: String, op: String, value: Variant,
		_limit := 100) -> Array:
	if bool(fail.get("firestore_query", false)):
		return []
	var out: Array = []
	if collection != "drafts":
		return out
	for d in drafts:
		var uids: Variant = d.get("fields", {}).get("family_uids", [])
		if field == "family_uids" and op == "array-contains" \
				and uids is Array and value in uids:
			out.append({"id": d["id"], "path": "drafts/" + d["id"],
				"fields": d["fields"]})
	return out


func firestore_get(path: String) -> Dictionary:
	if bool(fail.get("firestore_get", false)):
		return {}
	for d in drafts:
		if "drafts/" + d["id"] == path:
			return d["fields"]
	return {}


func firestore_patch(path: String, fields: Dictionary) -> bool:
	if bool(fail.get("firestore_patch", false)):
		return false
	patched.append({"path": path, "fields": fields})
	return true


func storage_get(path: String, max_bytes := 8 * 1024 * 1024) -> PackedByteArray:
	if bool(fail.get("storage_get", false)):
		return PackedByteArray()
	var data: PackedByteArray = packs.get(path.trim_prefix("packs/"), PackedByteArray())
	return data.slice(0, max_bytes)
