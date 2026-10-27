# Write the model spec

## Job

Write the model spec for $TITLE ($LATIN), spec id `$SPECIES`. A builder
turns it into the animated low-poly game model, so sculpt with
overlapping lumps of clay: every number below is in metres. Simple,
not realistic: the right body plan, rough proportions, main colours
and the one or two telling marks are enough.

## Input

The measurements, from the survey. Sourced sizes rule; the sheet only
guides shape and colours:

```
$MEASUREMENTS
```

What the adult looks like:

$DESCRIPTION

The closest existing spec, $CLOSEST_NAME. Adapt its bones and layout to
this species; do not copy its sizes. What to change from it:

$CHANGE

```
$CLOSEST_SPEC
```

The rules (the art guide and the model guide, read from the checkout):

```
$RULES
```

## Output

Strict JSON only: the whole spec, no fences, no commentary.

## Tools

None. Write from the input only.

## Hard rules

- Real size in metres. Lengths in the measurements are centimetres;
  divide by 100. The build prints the size; it must match the sourced
  body length.
- Colours from the measured colours, named after what they are. No pure
  black or white. Three to six colours.
- Marks big enough to see at game distance: each mark spans at least
  about 15% of the body's length or height (an ear tuft, a tail tip,
  a ruff), or is a colour on a whole part (a spotted coat is a second
  colour on body parts, not tiny balls).
- Bones where the joints are; at most 16 bones. Parts overlap
  generously; legs reach `y = 0`; eyes and beak sink a third in.
- Triangle budget 1000 (up to 1500 for a large, complex animal).
- Pose keys from the guide, not guesses: keep `fly_pitch` near the
  closest bird's (45 tips level; higher tips past level, so never
  raise it to fix a tipped frame — shorten the legs instead). Set
  `fold`/`fold_length` so folded wings lie flat, and `sleep_*` so
  the bird reads asleep.
- A seasonal coat only if the real animal changes colour.
- `id` is `$SPECIES`, `author` is `$AUTHOR`.

## Stop

Write the spec once. If it comes back with errors, fix exactly those.
