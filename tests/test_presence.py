"""Species presence: facets, smoothing, and filters. No network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from builder.presence import (
    animals_for_habitat,
    cell_wkt,
    fetch_raw,
    smooth,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_cell_wkt_is_a_closed_counter_clockwise_ring() -> None:
    wkt = cell_wkt("z10_0581_0295")
    assert wkt.startswith("POLYGON ((") and wkt.endswith("))")
    ring = wkt[len("POLYGON ((") : -len("))")].split(", ")
    assert len(ring) == 5
    assert ring[0] == ring[-1]
    coords = [tuple(map(float, pair.split(" "))) for pair in ring]
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(coords, coords[1:]))
    assert area > 0


def test_animals_for_habitat_filters() -> None:
    animals = [
        {"id": "fox", "habitats": ["boreal_forest", "grassland"]},
        {"id": "camel", "habitats": ["desert"]},
        {"id": "mystery"},
    ]
    assert [a["id"] for a in animals_for_habitat(animals, "boreal_forest")] == ["fox"]


def test_match_kingdom_reads_the_gbif_match(monkeypatch: Any) -> None:
    """#146.2: a Latin name resolves to its GBIF kingdom, or nothing."""
    import builder.presence as presence

    def fake_gbif(params: dict[str, Any], base: str) -> dict[str, Any]:
        assert base == presence.GBIF_MATCH
        if params["name"] == "Sargassum":
            return {"matchType": "EXACT", "kingdom": "Chromista"}
        return {"matchType": "NONE"}

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    assert presence.match_kingdom("Sargassum") == "Chromista"
    assert presence.match_kingdom("Not a name") is None


def test_fetch_raw_reads_species_facets(monkeypatch: Any) -> None:
    import builder.presence as presence

    def fake_gbif(params: dict[str, Any]) -> dict[str, Any]:
        assert params["facet"] == "speciesKey"
        assert params["limit"] == 0
        return {
            "count": 32,
            "facets": [
                {
                    "field": "SPECIES_KEY",
                    "counts": [
                        {"name": "5219243", "count": 21},
                        {"name": "2435240", "count": 11},
                        {"name": "999", "count": 5},
                    ],
                }
            ],
        }

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    animals = [
        {"id": "fox", "gbif_key": 5219243},
        {"id": "lynx", "gbif_key": 2435240},
    ]
    assert fetch_raw("z10_0581_0295", animals) == {"fox": 21, "lynx": 11}


def test_smooth_weights_the_centre_triple() -> None:
    out = smooth({"fox": 9}, [{"fox": 3}, {"fox": 0}, {"lynx": 6}])
    assert out["fox"] == (27 + 3 + 0) / 6
    assert out["lynx"] == 6 / 6


def test_smooth_discounts_next_door_only() -> None:
    out = smooth({"fox": 4}, [{"lynx": 11}])
    assert out["fox"] > out["lynx"]
    out = smooth({"fox": 1}, [{"lynx": 12}])
    assert out["lynx"] > out["fox"]


def test_nuuksio_fixture_has_boreal_birds() -> None:
    fixture = json.loads((FIXTURES / "presence-nuuksio.json").read_text())
    assert fixture["tile"] == "z10_0581_0294"
    counts = fixture["counts"]
    assert len(counts) >= 20
    assert counts.get("blue_tit", 0) > 100
    assert counts.get("fox", 0) > 0


def test_match_key_reads_the_gbif_species_key(monkeypatch: Any) -> None:
    """#196.3: a Latin name resolves to its GBIF key, or nothing."""
    import builder.presence as presence

    def fake_gbif(params: dict[str, Any], base: str) -> dict[str, Any]:
        assert base == presence.GBIF_MATCH
        if params["name"] == "Vaccinium myrtillus":
            return {"matchType": "EXACT", "speciesKey": 2886162, "usageKey": 7}
        if params["name"] == "Genus only":
            return {"matchType": "HIGHERRANK", "usageKey": 42}
        return {"matchType": "NONE"}

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    assert presence.match_key("Vaccinium myrtillus") == 2886162
    assert presence.match_key("Genus only") == 42
    assert presence.match_key("Not a name") is None


def test_has_records_near_counts_wild_records(monkeypatch: Any) -> None:
    """#196.3: a key counts as near with a wild record in the grown box."""
    import builder.presence as presence

    seen: dict[str, Any] = {}

    def fake_gbif(
        params: dict[str, Any], base: str = presence.GBIF_SEARCH
    ) -> dict[str, Any]:
        assert base == presence.GBIF_SEARCH
        seen.update(params)
        return {"count": 2 if params["taxon_key"] == "2886162" else 0}

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    assert presence.has_records_near(2886162, "z10_0581_0294") is True
    assert presence.has_records_near(1, "z10_0581_0294") is False
    assert seen["taxon_key"] == "1"
    assert seen["limit"] == 0
    assert seen["geometry"].startswith("POLYGON ((")


def test_near_wkt_grows_the_cell_and_clamps_at_edges() -> None:
    """#196.3: ±2 cells by default; poles clamp, the date line wraps (#272.8)."""
    import builder.presence as presence

    assert presence.near_wkt("z10_0581_0294", span=0) == presence.cell_wkt(
        "z10_0581_0294"
    )
    grown = presence.near_wkt("z10_0581_0294")
    cell = presence.cell_wkt("z10_0581_0294")

    def corners(wkt: str) -> list[tuple[float, float]]:
        ring = wkt[len("POLYGON ((") : -len("))")].split(", ")
        return [tuple(map(float, pair.split(" "))) for pair in ring]  # type: ignore[misc]

    grown_lons = [lon for lon, _ in corners(grown)]
    grown_lats = [lat for _, lat in corners(grown)]
    cell_lons = [lon for lon, _ in corners(cell)]
    cell_lats = [lat for _, lat in corners(cell)]
    assert min(grown_lons) < min(cell_lons)
    assert max(grown_lons) > max(cell_lons)
    assert min(grown_lats) < min(cell_lats)
    assert max(grown_lats) > max(cell_lats)
    edge = presence.near_wkt("z10_0000_0000")
    assert edge.startswith("MULTIPOLYGON")
    assert "-180.000000" in edge and "180.000000" in edge
    pole = presence.near_wkt("z10_0512_0000")
    assert pole.startswith("POLYGON")


def _fish_world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "animals").mkdir(parents=True)
    (world / "animals" / "flying_fish.json").write_text(
        json.dumps({"archetype": "fish", "open_sea": True})
    )
    (world / "animals" / "yellowfin_tuna.json").write_text(
        json.dumps({"archetype": "fish", "open_sea": True})
    )
    (world / "animals" / "humpback_whale.json").write_text(
        json.dumps({"archetype": "land_mammal", "open_sea": True})
    )
    return world


def test_fetch_taxon_counts_reads_one_taxon(monkeypatch: Any) -> None:
    """#277.1: one taxon, counts only, one call per cell."""
    import builder.presence as presence

    seen: list[dict[str, Any]] = []

    def fake_gbif(
        params: dict[str, Any], base: str = presence.GBIF_SEARCH
    ) -> dict[str, Any]:
        assert base == presence.GBIF_SEARCH
        seen.append(params)
        return {"count": 4}

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    out = presence.fetch_taxon_counts(5219243, ["z10_0581_0294", "z10_0582_0294"])
    assert out == {"z10_0581_0294": 4, "z10_0582_0294": 4}
    assert len(seen) == 2
    for params in seen:
        assert params["taxon_key"] == "5219243"
        assert params["limit"] == 0
        assert params["geometry"].startswith("POLYGON")


def test_family_key_reads_and_caches(monkeypatch: Any) -> None:
    """#301.2: the family key comes from GBIF's species API, once."""
    import builder.presence as presence

    calls: list[str] = []

    def fake_gbif(params: dict[str, Any], base: str = "") -> dict[str, Any]:
        calls.append(base)
        return {"familyKey": 2968}

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    presence._FAMILY_KEYS.clear()
    assert presence.family_key(2368648) == 2968
    assert presence.family_key(2368648) == 2968
    assert len(calls) == 1
    assert calls[0].endswith("/species/2368648")


def test_family_sea_fish_seats_four_reef_tiles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#301.2: the flying fish presents by family past the reef."""
    import builder.presence as presence
    from builder.tiles import _family_sea_fish

    def fake_gbif(params: dict[str, Any], base: str = "") -> dict[str, Any]:
        if base.endswith("/species/2368648"):
            return {"familyKey": 2968}
        return {"count": 5 if params.get("taxon_key") == "2968" else 0}

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    presence._FAMILY_KEYS.clear()
    world = _fish_world(tmp_path)
    candidates = [
        {"id": "flying_fish", "gbif_key": 2368648},
        {"id": "yellowfin_tuna", "gbif_key": 2374013},
    ]
    for tile in (
        "z10_0929_0562",
        "z10_0930_0562",
        "z10_0935_0563",
        "z10_0940_0565",
    ):
        out = _family_sea_fish(tile, candidates, world, {"yellowfin_tuna": 0.09})
        assert out == {"flying_fish": 0.05}, tile


def test_family_sea_fish_skips_non_fish(tmp_path: Path, monkeypatch: Any) -> None:
    """#301.2: a non-fish open-sea animal never presents by family."""
    import builder.presence as presence
    from builder.tiles import _family_sea_fish

    calls: list[str] = []

    def fake_gbif(params: dict[str, Any], base: str = "") -> dict[str, Any]:
        calls.append(base)
        return {"count": 9}

    monkeypatch.setattr(presence, "_gbif", fake_gbif)
    presence._FAMILY_KEYS.clear()
    world = _fish_world(tmp_path)
    out = _family_sea_fish(
        "z10_0929_0562", [{"id": "humpback_whale", "gbif_key": 1234}], world, {}
    )
    assert out == {}
    assert calls == []
