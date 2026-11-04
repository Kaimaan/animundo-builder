"""Re-plan border requests the home resolver blocked (#280.5).

The resolver changes which requests can plan: blocked `no_home`,
`no_land`, and `world_check:` plans may plan now. Anything else
(`no_wild`, `open_water`, `coast`, the border's own reasons) stays
put: re-planning it would only bill another survey.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Blocked reasons the resolver owns; re-planning may free them.
REASONS = ("no_home", "no_land")
WORLD_CHECK = "world_check:"


def needs_replan(blocked: str | None) -> bool:
    """True when a blocked plan is worth planning again."""
    if blocked is None:
        return False
    return blocked in REASONS or blocked.startswith(WORLD_CHECK)


def blocked_tiles(store: Any) -> list[tuple[str, dict[str, Any]]]:
    """Requests whose plan the resolver blocked, oldest first."""
    out = []
    for tile, request in store.list_requests():
        plan = store.get_plan(tile)
        if plan is not None and needs_replan(plan.get("blocked")):
            out.append((tile, request))
    return out


def replan(
    store: Any,
    plan_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
    tiles: list[str],
) -> dict[str, Any]:
    """Plan each tile, store the plan, and tally the outcomes.

    A tile whose new plan has no `blocked` landed in `planned`;
    one still blocked lands under its reason. A tile that raises
    keeps its old plan and lands in `errors`, and the rest still
    run.
    """
    planned: list[str] = []
    blocked: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for tile in tiles:
        request = store.get_request(tile)
        if request is None:
            continue
        try:
            plan = plan_fn(tile, request)
        except (ValueError, RuntimeError, FileNotFoundError, ConnectionError) as error:
            errors[tile] = str(error)[:200]
            continue
        store.set_plan(tile, plan)
        reason = plan.get("blocked")
        if reason:
            blocked.setdefault(reason, []).append(tile)
        else:
            planned.append(tile)
    return {"planned": planned, "blocked": blocked, "errors": errors}
