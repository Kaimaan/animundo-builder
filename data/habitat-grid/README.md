# The habitat grid

Which habitat each tile cell holds, baked from public maps with no AI.
Two files, one per grid: `ease2_n25.json.gz` and `ease2_s25.json.gz`.
Each maps `"col,row"` to
`[eco1, share1, eco2, share2, biome, land, water, elevation_m, coast]`:
the two most common RESOLVE ecoregion ids on land with their shares
of the land points, the biome of the first, the share of sample
points on land, the share of the cell's land area under any
HydroLAKES lake, the mean elevation in metres over the cell's land
points, and the 25 samples as a string in coast order (`1` dry
land, `0` sea or 1:10m lake): row 0 runs along the `n` side,
character 0 is west. Lakes count as land for every field but the
mask; `coast` is land against water, sea or lake.

## Sources and licence

- **RESOLVE Ecoregions 2017** (Dinerstein et al., *BioScience* 2017),
  via [ecoregions.world](https://ecoregions.world), data at
  `https://storage.googleapis.com/teow2016/Ecoregions2017.zip`.
  Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
  (confirmed at ecoregions.world and in the shapefile's own LICENSE
  field on Oct 28, 2026). Attribution: "RESOLVE Ecoregions 2017
  (Dinerstein et al. 2017), CC BY 4.0".
- **Natural Earth** 1:10m lakes (5.0.0) and land (5.1.1), public
  domain ([terms](https://www.naturalearthdata.com/about/)), via the
  NACIS CDN (`https://naciscdn.org/naturalearth/10m/physical/`).
- **ETOPO 2022** 60 arc-second Ice Surface grid (NOAA National
  Centers for Environmental Information), via
  [ncei.noaa.gov](https://www.ncei.noaa.gov/products/etopo-global-relief-model).
  Public domain: its ISO metadata states "Produced by the NOAA
  National Centers for Environmental Information. Not subject to
  copyright protection within the United States." (confirmed at the
  metadata record `gov.noaa.ngdc.mgg.dem:etopo_2022` on Oct 28,
  2026). Citation: "NOAA National Centers for Environmental
  Information. 2022: ETOPO 2022 15 Arc-Second Global Relief Model.
  NOAA National Centers for Environmental Information.
  https://doi.org/10.25921/fd45-gt74".
- **HydroLAKES** v1.0 (Messager et al., *Nature Communications*
  2016), polygons of every lake of 10 ha or more, via
  [hydrosheds.org](https://www.hydrosheds.org/products/hydrolakes).
  Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Attribution: "HydroLAKES v1.0 (Messager et al. 2016), CC BY 4.0".
- **Wikidata** lake labels (CC0,
  [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)):
  `lakes.json` joins the 1:10m lakes by `ne_id` with English and
  Finnish labels from each feature's `wikidataid`, else the
  Natural Earth name. Fetched once for #204, not per tile.
- **Complete GBR Reef and Island Features V1b** (Lawrey & Stewart
  2016, NESP TWQ 3.13, AIMS/TSRA/GBRMPA): `reef.json.gz` maps
  `"col,row"` to the reef-covered share (0–1, 3 decimals) for
  239 z10 cells, from the 7,063 `LEVEL_1 == "Reef"` polygons.
  Licensed under [CC BY 3.0
  Australia](https://creativecommons.org/licenses/by/3.0/au/)
  (dataset record [10.26274/vhj5-gr60](https://doi.org/10.26274/vhj5-gr60),
  confirmed Nov 2, 2026). Attribution: "Lawrey, E. P., Stewart M.
  (2016) Complete Great Barrier Reef (GBR) Reef and Island Feature
  boundaries including Torres Strait (NESP TWQ 3.13, AIMS, TSRA,
  GBRMPA) [Dataset]. eAtlas. https://doi.org/10.26274/vhj5-gr60"
  and "© Great Barrier Reef Marine Park Authority 2014" for the
  GBR Features part, as the record requests.

Kai confirms each source and licence before any tile is published
(T57). Status: RESOLVE, Natural Earth and ETOPO 2022 all confirmed
by Kai on Oct 28, 2026; HydroLAKES ordered for #202 on Nov 1, 2026;
Wikidata labels (CC0) ordered for #204 on Nov 1, 2026; the GBR reef
features (CC BY 3.0 AU) baked for #226 on Nov 2, 2026, awaiting
Kai's confirmation before anything ships with it.

## How it is baked

`builder habitats bake` reads the four shapefiles and the elevation
GeoTIFF from staging paths (passed as flags; the raw downloads live
outside the repo and are never committed), samples 5 by 5 points per
cell of both 720 by 720 grids, and writes these two files. Rock and
Ice is RESOLVE ecoregion id 0. The biome-to-habitat table lives in
`builder/habitats.py`. The water share is HydroLAKES lake area over
land area per cell, exact ellipsoidal areas; the mask's zeroes are
sea points and 1:10m lake points.

```sh
uv run builder habitats bake \
  --resolve /tmp/habitat-staging/resolve/Ecoregions2017.shp \
  --land /tmp/habitat-staging/ne_land/ne_10m_land.shp \
  --lakes /tmp/habitat-staging/ne_lakes/ne_10m_lakes.shp \
  --hydrolakes /tmp/habitat-staging/hydrolakes/HydroLAKES_polys_v10.shp \
  --dem /tmp/habitat-staging/ETOPO_2022_surface.tif \
  --out data/habitat-grid
```

`reef.json.gz` was baked separately for #226 (one-shot script, not
committed): the shapefile's `LEVEL_1 == "Reef"` polygons clipped
exactly against each candidate z10 cell box (Sutherland–Hodgman,
signed ring areas), the share being reef area over cell-box area;
cells under 0.0005 share were dropped. A cell at or above
`REEF_TRACT_SHARE` (0.2, calibrated on the live Green Island
tile's 0.211) builds from the `coral_reef` home; below that the
reef blends in. The grid habitat stays the sea it is, so live
places keep their tiles; reef-built tiles name the map in
`sources.habitat` (`gbrmpa:torres_strait_features_v1b`).
