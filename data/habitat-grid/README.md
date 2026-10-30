# The habitat grid

Which habitat each tile cell holds, baked from public maps with no AI.
Two files, one per grid: `ease2_n25.json.gz` and `ease2_s25.json.gz`.
Each maps `"col,row"` to
`[eco1, share1, eco2, share2, biome, land, water, elevation_m, coast]`:
the two most common RESOLVE ecoregion ids on land with their shares
of the land points, the biome of the first, the share of sample
points on land, the share of land points in a lake, the mean
elevation in metres over the cell's land points, and the 25 land
samples as a string in coast order (`1` land, `0` sea): row 0 runs
along the `n` side, character 0 is west. Lakes count as land in the
mask; `coast` is only land against sea.

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

Kai confirms each source and licence before any tile is published
(T57). Status: RESOLVE, Natural Earth and ETOPO 2022 all confirmed
by Kai on Oct 28, 2026.

## How it is baked

`builder habitats bake` reads the three shapefiles and the elevation
GeoTIFF from staging paths (passed as flags; the raw downloads live
outside the repo and are never committed), samples 5 by 5 points per
cell of both 720 by 720 grids, and writes these two files. Rock and
Ice is RESOLVE ecoregion id 0. The biome-to-habitat table lives in
`builder/habitats.py`.

```sh
uv run builder habitats bake \
  --resolve /tmp/habitat-staging/resolve/Ecoregions2017.shp \
  --land /tmp/habitat-staging/ne_land/ne_10m_land.shp \
  --lakes /tmp/habitat-staging/ne_lakes/ne_10m_lakes.shp \
  --dem /tmp/habitat-staging/ETOPO_2022_surface.tif \
  --out data/habitat-grid
```
