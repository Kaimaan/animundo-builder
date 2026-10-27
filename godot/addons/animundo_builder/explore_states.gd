class_name ExploreStates
extends RefCounted
## What the globe shows for a draft: its look and its plain sentence.
##
## The statuses are the draft contract's (contracts/draft.schema.json); the
## looks are the game's (BuilderHooks.LOOKS). Pure static functions, so the
## headless checks run them with no game and no net.

## Every status a draft can have, in the contract's order.
const STATUSES: Array[String] = ["requested", "queued", "building", "ready",
	"improving", "in_review", "public", "not_accepted", "expired",
	"not_possible_yet", "failed"]


## The globe look for a draft: "your_draft" for an installed family draft,
## "being_explored" while the family waits for one, else "" (the globe stays
## as it was: published places are the game's own lit places, strangers'
## drafts aren't shown, and not_possible_yet stays unmarked — the look
## stays unused and the screen explains).
static func look_for(status: String, is_family: bool, installed: bool) -> String:
	if not is_family:
		return ""
	match status:
		"ready", "in_review", "not_accepted":
			return "your_draft" if installed else "being_explored"
		"requested", "queued", "building", "improving":
			return "being_explored"
	return ""


## The plain sentence for a status, in the player's language.
static func words_for(status: String, strings: BuilderStrings) -> String:
	return strings.t("explore.state." + status)
