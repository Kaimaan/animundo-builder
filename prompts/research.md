# Animal research

## Job

One short call about $NAME ($LATIN), living near $LAT, $LON.
Five searches at most. Answer with JSON only, no prose.

## Wanted

- `main_food`: what it eats most. An object with `name` (the
  plain English name, like "mountain hare"), `kind`, and
  `size_cm` (body length) when it is an animal. `kind` is one
  of: prey, bark, berries, bottom_bugs, browse, carrion,
  carrot, clover, cones, grass, insects, mushrooms, nectar,
  nest, seeds, water_bugs, water_plants. Use `prey` for an
  animal it hunts, a food kind otherwise.
- `other_foods`: up to 3 more foods it eats, same shape as
  `main_food`, each with `size_cm` when it is an animal.
- Every food that is a species also carries `latin` (its
  binomial, like "Lepus timidus"); anything else (bird eggs,
  carrion, a nameless browse) leaves it out.
- `surroundings`: about 5 things around it where it lives:
  trees, plants, the ground and water. Each has `name`, `kind`
  (tree, plant, ground or water), and `food_kind` when animals
  eat it (one of the food kinds above, not prey). A tree or
  plant that is a species also carries its `latin`.
- `size`: its own body, `length_cm` (required) and
  `weight_kg` when known.
- `looks`: what it looks like, so a sculptor gets it right.
  `shoulder_cm` (or `height_cm` for birds and fish),
  `tail_cm`, `coat` (`main` and `belly` or chest colour in
  plain words plus `main_hex` and `belly_hex` like
  "#7a4a2b") and `marks` (the 2-3 field-guide marks that
  tell the species apart, like "ear tufts" or "short
  black-tipped tail"). Each with its `source`. The main
  colour is the one a child would name when looking at the
  animal, as a field guide's plate shows it — not the most
  striking variant.
- `year`: how it lives through the seasons: `winter` (active,
  hibernates or migrates), `shelter` (one or two sentences),
  `young` (one or two sentences) and `food` (one sentence
  about what it eats through the year).
- `sources`: the pages the facts came from, most important
  first.

Nothing else is researched. Every fact comes from a search;
never invent a name or a size.

$FOR_CHILDREN
