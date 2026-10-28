# Natural Earth, vendored

Public-domain shapes from
[naturalearthdata.com](https://www.naturalearthdata.com),
fetched October 2026 through the NACIS CDN mirror
(`naciscdn.org/naturalearth/`).

Only `ne_50m_urban_areas.geojson` remains: the place-list script
(`scripts/places_of_interest.py fetch`) drops list places inside
cities with it. The map is gone since D11, with its Leaflet copy
and land, coastline and country shapes.
