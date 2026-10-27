# Write the plant spec

## Job

Write the spec for $NAMES, a real wild ground plant or shrub
growing in $REGION_NAME ($REGION_COUNTRY), spec id `$PLANT_ID`.
Blender builds it into the low-poly game model from your spec, so
sculpt with overlapping simple solids: every number below is in
metres. Simple, not realistic: the right shape, rough size, main
colours and the one or two telling marks are enough.

## Input

What grows there (the survey's own words about this place's plants):

```
$FACTS
```

Two finished specs to follow for shape: a bush first, then a
ground plant. Follow the closer one; a small plant keeps its
leaves near the ground, never stacked up a stalk:

```
$EXAMPLES
```

The rules (the community's plant rules and the model guide's
Plants section, read from the checkout):

```
$RULES
```

## Output

Strict JSON only: the whole spec, no fences, no commentary.

## Tools

None. Write from the input only.

## Hard rules

- A real wild species: its true `latin` name, never a garden
  variety or a made-up plant. Real looks and real size from a
  field guide, named in `sources` (links or book titles).
- Its real year: `leaves` evergreen or deciduous, `flowers` and
  `fruit` saying when (or "none"). Leaves that fall, flowers and
  fruit get `show` dates; changing colours get `seasons`.
- `kind` is `ground` (under 50 triangles) or `bush` (under 300);
  about 120 for a common plant. Cheap leaves: `detail` 0.5 or
  less, berries 0.3 or less.
- Parts overlap generously; the plant stands on `y = 0`. Colours
  from the real plant, no pure black or white.
- `id` is `$PLANT_ID`, `author` is `$AUTHOR`.
- No fields beyond the spec format: never add `warning`,
  `about_safety` or any other note field. If animals eat it, or
  it is poisonous to people, say so in one sentence in
  `year.notes` (an expert reads these later).

## Stop

Write the spec once. If it comes back with errors, fix exactly those.
