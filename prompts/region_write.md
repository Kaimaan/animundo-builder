# Write the region file

## Job

Write the region file for the place below: its land, water, trees,
plants, food, fish and animals, from the survey. Follow the region rules
from the world's CONTRIBUTING.md, and match the shape of the example
regions.

## Input

The area:

```
$AREA_JSON
```

The survey (every fact sourced; species with roles):

```
$SURVEY_JSON
```

What the game can show (food kinds, behaviours):

```
$CAPABILITIES_JSON
```

The region rules, read fresh from the world's CONTRIBUTING.md:

```
$REGION_RULES
```

Example regions, read fresh from the world's checkout:

```
$EXAMPLE_1_NAME:
$EXAMPLE_1

$EXAMPLE_2_NAME:
$EXAMPLE_2
```

## Output

Reply with only the JSON object, no other text. The region file as
strict JSON matching the region schema. The region id is the area id.
Its `animals` lists name the survey's species by their ids: every
surveyed species goes in exactly one list (`playable` for animals
children can play, `predators` for hunters, `prey` for the hunted).
Its `fish` name surveyed fish the same way. Its `sources` keep at
least three of the survey's source URLs.

Name every surveyed species EVEN IF it has no animal file yet. Only
these files exist today:

```
$EXISTING_ANIMALS
```

This OVERRIDES the CONTRIBUTING rule that says new animals are "a code
change" to "say so in your pull request": the builder builds their
files in a later stage, so for this task a surveyed id with no file
yet is expected. Listing one is correct, never an error. Omitting one
is: the validator's "unknown animal" complaints about surveyed ids are
forgiven by the pipeline, not by you.

`ground_cover.plants[].model` prefers these already built plant
models:

```
$BUILT_PLANTS
```

At most 3 models may be new ids not in that list: real ground
plants or shrubs that grow wild in this place, named by their
English common name in lowercase with `_` (like `wood_anemone`).
The plants stage builds their specs later; list each new one's
common names in the entry's `names` so it knows what to build.
Never name an unbuilt model that is already in the list's
checkout but has no `.glb`, and never a tree: trees are code.

The `author`, `status`, `seed` and `center` are set by the pipeline, not
by you: write your best values and they are corrected afterwards.

## Tools

None. Write from the inputs only.

## Hard rules

- `food[].kind` uses only kinds from the capabilities input.
- `trees.mix[].shape` uses only the five shapes the rules name.
- Sensitive species (the survey says which) get no real breeding,
  nesting, denning or resting site: `places` stay general.
- Every animal, fish and food must be one the survey found really living
  here. Never invent one.
- Colours are `#rrggbb` strings. Numbers stay inside the rules' ranges.

$FOR_CHILDREN

## Stop

Write the file once. If it comes back with errors, fix exactly those.
