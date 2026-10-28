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
    wkt = cell_wkt("n_413_478")
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
    assert fetch_raw("n_413_478", animals) == {"fox": 21, "lynx": 11}


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
    assert fixture["tile"] == "n_413_478"
    counts = fixture["counts"]
    assert len(counts) >= 20
    assert counts.get("blue_tit", 0) > 100
    assert counts.get("fox", 0) > 0
