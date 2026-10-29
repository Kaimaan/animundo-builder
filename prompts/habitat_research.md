# Habitat research

## Job

One short call about $HABITAT land near $LAT, $LON.$PLACE_LINE
Five searches at most. Answer with JSON only, no prose.

## Wanted

- `main_food`: what animals here eat most. An object with
  `name` (the plain English name, like "crowberries"),
  `kind`, and `size_cm` (body length) when it is an animal.
  `kind` is one of: prey, bark, berries, bottom_bugs,
  browse, carrion, carrot, clover, cones, grass, insects,
  mushrooms, nectar, nest, seeds, water_bugs, water_plants.
  Use `prey` for a hunted animal, a food kind otherwise.
- `other_foods`: up to 3 more foods eaten here, same shape
  as `main_food`, each with `size_cm` when it is an animal.
- Every food that is a species also carries `latin` (its
  binomial, like "Empetrum nigrum"); anything else (bird
  eggs, carrion, a nameless browse) leaves it out.
- `surroundings`: about 5 things here: trees, plants, the
  ground and water. Each has `name`, `kind` (tree, plant,
  ground or water), and `food_kind` when animals eat it
  (one of the food kinds above, not prey). A tree or plant
  that is a species also carries its `latin`.
- `sources`: the pages the facts came from, most important
  first.

Nothing else is researched. Every fact comes from a search;
never invent a name.

$FOR_CHILDREN
