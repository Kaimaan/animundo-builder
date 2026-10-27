# Write the home template

## Job

Write the home template for one habitat: its land, trees,
plants, water, food kinds and fish, in the shape of the example
home. A home is a region without a place: no real place names,
no center, no animals.

## Input

The habitat: $HABITAT.

What grows and lies around there (the research's surroundings):

```
$SURROUNDINGS
```

The closest existing home, $CLOSEST_NAME. Follow its shape and
fill every section it has; change its land, trees, plants,
water, food and fish to this habitat:

```
$CLOSEST_HOME
```

Plant specs the game already has (a new plant only when none of
these fits; at most $MAX_PLANTS new ones):

$EXISTING_PLANTS

Tree shapes the game draws: $TREE_SHAPES. There are no tree
specs yet, so every tree uses a shape, never a spec id. A tree
`plant` id must be one of these pages, and named in the
surroundings above; otherwise leave `plant` out:

$TREE_PLANTS

Floating and water plants never become specs. The game already
draws them on water from `lake_life`: `reeds`, `lilies` and
`pondweed`, each 0 to 1. A floating plant from the surroundings
(water lettuce, water lilies) sets one of those densities and
gets no spec; specs stand on the ground.

Two finished plant specs to follow for shape, a bush first, then
a ground plant:

```
$PLANT_EXAMPLES
```

The region rules, read fresh from the world's CONTRIBUTING.md:

```
$REGION_RULES
```

The plant rules (the community's plant rules and the model
guide's Plants section, read from the checkout):

```
$PLANT_RULES
```

## Output

Reply with one JSON object, nothing else: {"home": <the home
template>, "plants": {<id>: <plant spec>}}. `plants` is `{}`
when the existing specs cover it.

## Tools

None. Write from the inputs only.

## Hard rules

- `home.id` is exactly `$HABITAT`. No real place names
  anywhere: `country` is empty, `center` is 0, 0, and
  `based_on` stays out unless a shipped region truly
  resembles this land (then only its id).
- Every tree is one of the shapes above. Every
  `ground_cover.plants[].model` is an existing plant above
  or one of the new specs, whose `id` matches its key. At
  most $MAX_PLANTS new specs.
- Nothing living carries over from the example home: only
  its land, sky and colour structure do. Every fish, ground
  plant and tree plant id must be a species the surroundings
  name, or a new spec written here. Anything left empty
  stays empty: a river with no named fish keeps no fish.
- New plants are real wild species with true `latin` names
  and real sizes, like the examples. `author` is `$AUTHOR`.
- `food` covers berries, insects and carrion (the check
  plays a fox there) plus what this land offers.
- Seasons only where the place has them.

$FOR_CHILDREN

## Stop

Write the home once. If it comes back with errors, fix exactly those.
