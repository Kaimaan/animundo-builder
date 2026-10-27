class_name BuilderStrings
extends RefCounted
## The addon's own words (locale/en.json, locale/fi.json), read straight from
## the addon's folder: the game can't lend its I18n through the plug-in
## points, so Builder mode carries its own. The same files are copied into
## the game as a content folder at boot, so the grown-up and places menus
## can show the addon's buttons in the player's language.

const ADDON_DIR := "res://addons/animundo_builder"
const LANGS: Array[String] = ["en", "fi"]

var lang := "en"
var _t := {}


## The player's language from the engine's locale ("fi" or "en").
static func player_lang() -> String:
	if TranslationServer.get_locale().begins_with("fi"):
		return "fi"
	return "en"


## Reads locale/<wanted>.json (English when it is missing). False when even
## English can't be read.
func load_lang(wanted: String) -> bool:
	lang = wanted if wanted in LANGS else "en"
	_t = _read(lang)
	if _t.is_empty() and lang != "en":
		lang = "en"
		_t = _read(lang)
	return not _t.is_empty()


## The word for `key`, or the key itself when it has none. {name} parts
## are filled from `args`.
func t(key: String, args := {}) -> String:
	var s := str(_t.get(key, key))
	for name in args:
		s = s.replace("{" + str(name) + "}", str(args[name]))
	return s


static func _read(wanted: String) -> Dictionary:
	var path := ADDON_DIR + "/locale/" + wanted + ".json"
	if not FileAccess.file_exists(path):
		return {}
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	return parsed if parsed is Dictionary else {}
