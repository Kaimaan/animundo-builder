# Write the animal file

## Job

Write the animal file for one species: its body, behaviour, food, year
and English text, in the shape of the example animals.

## Input

The surveyed species:

```
$SPECIES_JSON
```

Its GBIF taxon, already decided (copy the block exactly):

```
$TAXON_JSON
```

Its role in this region: $ROLE_IN_REGION.

The region's food kinds (every `diet` kind must be one of these):

```
$REGION_FOODS
```

The region's cast:

```
$REGION_CAST
```

Tags the cast's existing animals carry (every `hunts` entry must be
one of these; `hunts` names prey tags, never animal ids):

```
$CAST_TAGS
```

Tags the cast's hunters need that no existing cast animal carries
(a new species must carry the ones that fit it):

```
$NEEDED_TAGS
```

Tags the existing animals carry (`tag: files`):

```
$EXISTING_TAGS
```

The archetypes to choose from: $ARCHETYPES.

Its model file, already decided (T16 builds it; write the path exactly):

```
$MODEL_PATH
```

The animal rules, read fresh from the world's CONTRIBUTING.md:

```
$ANIMAL_RULES
```

Example animals, read fresh from the world's checkout:

```
$EXAMPLE_1_NAME:
$EXAMPLE_1

$EXAMPLE_2_NAME:
$EXAMPLE_2
```

## Output

Reply with only the JSON object, no other text. The animal file as
strict JSON matching the animal schema, with English `text`.

## Tools

None. Write from the inputs only.

## Hard rules

- Food first: every `diet` kind is in the region's food above, and
  every `hunts` entry is a prey tag from the cast tags above, never an
  animal id. Never invent a food or a tag to fill a gap. Carry every
  needed tag above that fits this animal, so the cast's hunters find
  their prey.
- `latin` is exactly the surveyed Latin name, and `taxon` is exactly
  the block above. `model.file` is exactly the path above, with the
  same `anims` names as the examples.
- Behaviour comes from the surveyed species and the archetype, never
  from the example animal: `digs`, `hunts`, `territorial`, `family`
  and the rest follow what the survey says this animal does. When it
  does not do it, set the flag false outright (the archetype may say
  true). Every true flag needs its text: `digs` needs `dig`, `hunts`
  needs `safe_tag` (plus `bite` and `caught_you` per prey),
  `territorial` needs `rival_bite` and `rival_caught_you`.
- Text sounds like the examples: plain words, short sentences, present
  tense, what a child sees. Never a real breeding, nesting, denning or
  resting site for a sensitive species.
- `text` is English only. Never write `translations`: Finnish
  comes later, from the reviewer, and the animal file must not
  carry it.
- Numbers stay inside the rules' ranges.

$FOR_CHILDREN

## Stop

Write the file once. If it comes back with errors, fix exactly those.
