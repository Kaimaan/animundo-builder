# Tiles lab, Oct 28 2026 (T80)

`builder tiles lab` over the five phase-1 spots plus sixteen across
the continents, at $0: no model calls. `counts.json` is the run's
result per spot; `globe.png` plots them (green ready, red
`no_animal`, orange `no_home`).

Two spots are ready (Nuuksio and Lapland, both taiga in ecoregion
717 with homes and filed animals). The Amazon, the Serengeti and
the Wales sea cell have homes but no animal files yet, so they are
honestly `no_animal`. Everything else is `no_home`: no home lists
its ecoregion yet. All ready tiles pass the world's `check_tile.py`.
