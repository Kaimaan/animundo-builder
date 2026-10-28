# Natural Earth, vendored

Public-domain coast and country shapes from
[naturalearthdata.com](https://www.naturalearthdata.com),
fetched October 2026 through the NACIS CDN mirror
(`naciscdn.org/naturalearth/`). No tile service, no terms, no
rate limits; the map draws these straight into Leaflet.

| File | Used for |
|---|---|
| `ne_110m_land.geojson`, `ne_110m_coastline.geojson` | World view (zoom 0–3) |
| `ne_50m_land.geojson`, `ne_50m_coastline.geojson` | Range view (zoom 4–5), loaded on first zoom-in |
| `ne_10m_land.geojson` | Close view (zoom 6+), loaded on first zoom-in |
| `ne_110m_admin_0_countries.geojson` | Place names only; borders are never drawn |
| `ne_50m_urban_areas.geojson` | Cities stay unpickable |

Processing, all in `git` as converted: shapefiles to GeoJSON,
coordinate precision cut (50m to 3 decimals, 10m land to 2,
about 1 km), properties stripped to `{}` — the page styles by
layer, never by feature. The 10m coastline file is deliberately
not vendored: at the close tier the land polygon's own outline
draws the shore, and the second file would add 6 MB for the
same line.
