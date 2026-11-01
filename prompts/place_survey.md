# Place survey

## Job

Survey one place for a new start in the game: $PLACE.
$IMPROVE_LINE
A few web searches at most. Answer with JSON only, no prose.

## Wanted

- `tile`: the tile id of the spot you pick for the place's start
  (like `z10_0927_0560`), on the zoom 10 web-map grid. $TILE_LINE
- `habitat`: the habitat there, in a few words.
- `home_from`: the home file to adapt the place's home from,
  one of the homes below.
- `name`: the place's short child-friendly name in English (`en`)
  and in Finnish (`fi`), each written as in that language
  (Finnish takes a lower-case initial unless it is a proper noun).
- `country`: the country it is in, in English.
- `region_id`: the place file's id: `<two-letter country>_<place>`,
  lowercase, like `au_great_barrier_reef`.
- `animals`: the animals living there, best known to children
  first, each with `id` (from the list below only) and `source`
  (a URL or a source name). $WANT_LINE
- `plants`: the plants children will find there, each with `name`
  (the common name with the Latin name in parentheses, like
  `Brown seaweed (Sargassum)`) and `source`.
- `food`: what the animals eat there, each with `name` and `source`.

## Homes

$HOMES

## Animals

$ANIMALS
