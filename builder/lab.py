"""The $0 lab run: tiles for the five spots and a few per continent.

`builder tiles lab` makes each spot's tile (or records why it is
blocked), writes the counts, and draws a globe picture showing
ready and blocked cells by reason. No model calls, no cost.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .grid import GridName, center, tile_at
from .habitats import HabitatGrid
from .tiles import make_tile

#: The five lab spots (spec 04 T59), then a few per continent.
SPOTS: list[tuple[str, float, float]] = [
    ("nuuksio", 60.45, 24.40),
    ("lapland", 68.40, 23.60),
    ("amazon", -1.00, -75.50),
    ("savanna", -2.33, 34.83),
    ("coast", 52.00, -4.50),
    ("wales-sea", 52.077, -5.007),
    ("great-plains", 45.00, -100.00),
    ("alaska", 62.00, -150.00),
    ("sonora", 30.00, -112.00),
    ("pampas", -35.00, -62.00),
    ("patagonia", -50.00, -73.00),
    ("bavaria", 48.00, 11.00),
    ("sahara", 23.00, 15.00),
    ("kalahari", -25.00, 22.00),
    ("siberia", 62.00, 100.00),
    ("tibet", 32.00, 88.00),
    ("deccan", 15.00, 78.00),
    ("outback", -25.00, 133.00),
    ("antarctica", -80.00, 0.00),
    ("atlantic", 30.00, -40.00),
    ("pacific", -30.00, -120.00),
]

COLORS = {
    "ready": (106, 207, 130),
    "no_home": (235, 171, 88),
    "no_animal": (235, 110, 110),
    "excluded": (150, 150, 150),
}

WIDTH = 1440
HEIGHT = 720


def run_lab(
    grids: dict[GridName, HabitatGrid],
    world: Path,
    builder_root: Path | None = None,
    species_source: Any | None = None,
) -> list[dict[str, Any]]:
    """Make every spot's tile; blocked cells carry their reason."""
    results: list[dict[str, Any]] = []
    for name, lat, lon in SPOTS:
        try:
            tile = tile_at(lat, lon)
        except ValueError as error:
            results.append({"spot": name, "status": "error", "detail": str(error)})
            continue
        try:
            made, blocked = make_tile(
                tile,
                grids,
                world,
                builder_root=builder_root,
                species_source=species_source,
            )
        except RuntimeError as error:
            results.append(
                {"spot": name, "tile": tile, "status": "error", "detail": str(error)}
            )
            continue
        if made is not None:
            results.append(
                {
                    "spot": name,
                    "tile": tile,
                    "status": "ready",
                    "habitat": made["habitat"],
                    "playable": made["species"]["playable"][:3],
                }
            )
        else:
            results.append(
                {
                    "spot": name,
                    "tile": tile,
                    "status": "blocked",
                    "reason": (blocked or {}).get("reason"),
                    "habitat": (blocked or {}).get("habitat"),
                }
            )
    return results


def counts(results: list[dict[str, Any]]) -> dict[str, Any]:
    """The run's counts: ready and blocked by reason."""
    out: dict[str, Any] = {"ready": 0, "blocked": {}}
    for entry in results:
        if entry.get("status") == "ready":
            out["ready"] += 1
        else:
            reason = entry.get("reason", "error")
            out["blocked"][reason] = out["blocked"].get(reason, 0) + 1
    return out


def _project(lat: float, lon: float) -> tuple[float, float]:
    x = (lon + 180) / 360 * WIDTH
    y = (90 - lat) / 180 * HEIGHT
    return x, y


def draw_globe(
    results: list[dict[str, Any]], land_path: Path | None = None
) -> Image.Image:
    """Equirectangular globe with the run's cells by status."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (18, 28, 24))
    draw = ImageDraw.Draw(img)
    if land_path is not None and land_path.is_file():
        _draw_land(draw, land_path)
    for entry in results:
        tile = entry.get("tile")
        if tile is None:
            continue
        lat, lon = center(tile)
        x, y = _project(lat, lon)
        if entry.get("status") == "ready":
            color = COLORS["ready"]
        else:
            color = COLORS.get(entry.get("reason", ""), (150, 150, 150))
        radius = 9
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            fill=color,
            outline=(255, 255, 255),
            width=2,
        )
    _draw_legend(draw, results)
    return img


def _draw_land(draw: ImageDraw.ImageDraw, land_path: Path) -> None:
    import fiona
    from shapely.geometry import shape

    with fiona.open(land_path) as src:
        for feature in src:
            geom = feature["geometry"]
            if geom is None:
                continue
            parsed = shape(geom)
            polys = getattr(parsed, "geoms", [parsed])
            for poly in polys:
                rings = [poly.exterior, *poly.interiors]
                for ring in rings:
                    points = [_project(lat, lon) for lon, lat in ring.coords[::10]]
                    if len(points) > 2:
                        draw.polygon(points, fill=(44, 58, 50))


def _draw_legend(draw: ImageDraw.ImageDraw, results: list[dict[str, Any]]) -> None:
    tallies = counts(results)
    lines = [f"ready {tallies['ready']}"]
    for reason in ("no_home", "no_animal", "excluded", "error"):
        n = tallies["blocked"].get(reason, 0)
        if n:
            lines.append(f"{reason} {n}")
    y = 16
    for line in lines:
        color = COLORS.get(line.split()[0], (150, 150, 150))
        draw.ellipse([16, y, 32, y + 16], fill=color)
        draw.text((40, y), line, fill=(230, 230, 230))
        y += 24
    day = datetime.now(UTC).date().isoformat()
    draw.text((16, HEIGHT - 24), f"tiles lab {day}", fill=(150, 150, 150))


def write_lab(
    out_dir: Path,
    results: list[dict[str, Any]],
    land_path: Path | None = None,
) -> tuple[Path, Path]:
    """Write counts.json and globe.png for one lab run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts_path = out_dir / "counts.json"
    counts_path.write_text(
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "counts": counts(results),
                "spots": results,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    globe_path = out_dir / "globe.png"
    draw_globe(results, land_path).save(globe_path)
    return counts_path, globe_path
