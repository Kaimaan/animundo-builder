# Survey a place for a place job

## Job

Research $PLACE as a new playable place and answer strict
JSON. The research decides everything below; code checks it.

$WANT_LINE

## Tiles

The rectangle of z10 grid tiles the place covers: 1 tile, 2
side by side, or a 2 by 2 block. Tile ids look like
`z10_0581_0294` (z10, four digits, four digits). The
rectangle is fully covered: never 3 tiles, never a scattered
four.

## Biome

The id of the `biomes/` file the place grows on, one of:

$BIOMES

When none fits the land, name the closest land in words in
`biome` anyway; the job stops and a person makes the biome.

## Life

Animals from the list below only, each with a source URL
where it is found in this place. Plants by name, each with
a source URL. Sources are real pages, never made-up links.

$ANIMALS

## Name

The place's name in English, Finnish and Finnish genitive,
and its Wikidata id (like `Q123`).

## Output

Strict JSON only, exactly these keys: `centre` with `lat`
and `lon`, `tiles`, `biome`, `name` with `en`, `fi` and
`fi_gen`, `wikidata`, `animals` with `id` and `source`,
`plants` with `name` and `source`.
