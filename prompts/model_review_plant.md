# Review the plant preview

## Job

Judge whether this $TITLE preview is a game-ready plant a child
would recognise, and whether it sits well next to the game's own
$BAR_TITLE. Two images: the game's own plant first, the new
render last (front, side, 3/4 and top views, silhouette, a patch,
scale next to a red squirrel, game distance, and the four seasons
through the year).

Simple, not realistic: the game is simple and blocky. The plant
needs only the right shape, rough size, main colours, and the one
or two marks that tell the species apart — never realism or fine
detail.

The build reports: $BUILT.

## Input

What the real plant looks like and does through the year:

```
$YEAR_JSON
```

Check the render against it, in this order; a part that falls
short is an issue naming that part:

- Shape: the same shape as the real plant (bush, tuft, mat,
  single stem); every part present in every view.
- Size: right against the squirrel; the build prints the height.
- Leaves, flowers, fruit: present when the year says so, in the
  right colours; nothing floating or detached.
- Seasons: the four frames follow the year (flowers and fruit
  only in season; autumn colours; bare or evergreen winter).
- Colours: the real plant's main colours; flat colours only, no
  textures, no pure black or white.

## Output

Strict JSON matching its schema: `species` (`$SPECIES`), `round`
($ROUND), and `issues`, each with `part`, `problem`, `severity` and
`fix`. The `species` is the plant id.

## Tools

None. Judge from the images only.

## Hard rules

- `blocking` means only what breaks a child's recognition: a
  missing or wrong-sized part, a wrong main colour, a broken
  season (flowers in winter, leaves that should have fallen).
  Small parts — petals, berries, leaf tips — are fine when
  roughly the right shape and place. Detail short of realism is
  never `blocking`: at most `minor`, and usually nothing at all.
- Examine every view and every season above; skipping a listed
  check is a miss, not a pass.
- Name the exact part and the exact fix for every issue.
- No `blocking` issue once the render meets the bar and the list.

## Stop

Review once per round.
