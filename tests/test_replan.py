"""Tests for the blocked-request re-plan (#280.5)."""

from __future__ import annotations

from fake_store import FakeStore

from builder.replan import blocked_tiles, needs_replan, replan


def test_needs_replan_matches_resolver_reasons() -> None:
    assert needs_replan("no_home")
    assert needs_replan("no_land")
    assert needs_replan("world_check: taiga fails boreal_forest")
    assert not needs_replan(None)
    assert not needs_replan("no_wild")
    assert not needs_replan("open_water")
    assert not needs_replan("coast")
    assert not needs_replan("excluded")


def test_blocked_tiles_skips_unblocked_and_foreign() -> None:
    store = FakeStore()
    store.requests = {
        "t1": {"tile": "t1"},
        "t2": {"tile": "t2"},
        "t3": {"tile": "t3"},
        "t4": {"tile": "t4"},
    }
    store.plans = {
        "t1": {"blocked": "no_home"},
        "t2": {"blocked": "no_wild"},
        "t4": {"blocked": "world_check: taiga fails boreal_forest"},
    }
    assert [tile for tile, _ in blocked_tiles(store)] == ["t1", "t4"]


def test_replan_replans_and_tallies() -> None:
    store = FakeStore()
    store.requests = {"t1": {"tile": "t1"}, "t4": {"tile": "t4"}}
    store.plans = {"t1": {"blocked": "no_home"}, "t4": {"blocked": "no_land"}}
    seen: list[str] = []

    def plan_fn(tile: str, request: dict) -> dict:
        seen.append(tile)
        if tile == "t1":
            return {"blocked": None, "items": [{"kind": "animal"}]}
        return {"blocked": "no_home"}

    result = replan(store, plan_fn, [tile for tile, _ in blocked_tiles(store)])
    assert seen == ["t1", "t4"]
    assert result["planned"] == ["t1"]
    assert result["blocked"] == {"no_home": ["t4"]}
    assert result["errors"] == {}
    assert store.plans["t1"]["items"] == [{"kind": "animal"}]


def test_replan_keeps_going_past_errors() -> None:
    store = FakeStore()
    store.requests = {"t1": {"tile": "t1"}, "t4": {"tile": "t4"}}
    store.plans = {"t1": {"blocked": "no_home"}, "t4": {"blocked": "no_land"}}

    def plan_fn(tile: str, request: dict) -> dict:
        if tile == "t1":
            raise ValueError("no survey")
        return {"blocked": None}

    result = replan(store, plan_fn, ["t1", "t4"])
    assert result["planned"] == ["t4"]
    assert result["blocked"] == {}
    assert list(result["errors"]) == ["t1"]
    assert store.plans["t1"] == {"blocked": "no_home"}
