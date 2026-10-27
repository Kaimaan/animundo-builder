# Review the model preview

## Job

Judge whether the new render is as good as the game's own
$CLOSEST_TITLE, and whether a child would recognise $TITLE. The
images, in order: the reference sheet, the game's own animal, and
the new render last.

The build reports: $BUILT.

## Input

Three images. First: the reference sheet (side and front views,
drawn for this species) — use it only for proportions, colours and
markings, never for detail the builder can't make (feather texture,
an exact beak curve). Second: the game's own $CLOSEST_TITLE, the
bar to meet. Last: the new render of $TITLE (front, side, back and
top views, four walk frames, and run, eat, sleep and fly poses).

The question: is the new render as good as the game's own animal?
Pass the first round that meets that bar; don't polish further.

Check each part against the game's animal and the sheet's
proportions, in this order; a part that falls short is an issue
naming that part:

- Silhouette: the same body plan, the same proportions.
- Wings: present, folded plausibly at rest, the right size and place.
- Tail: present, the right length, shape and markings.
- Legs and feet: the right count, length and covering; feet on the
  ground in standing frames.
- Head, beak and ears: the right shape and size for the species.
- Colours and markings: the sheet's main colours, in the right
  places; flat colours only, no textures, no pure black or white.

Then check every animation frame on its own:

- Fly: judge it by the wings and the body angle, never by height:
  the preview draws every clip standing on the ground, fly too (in
  the game the bird flies at height). Expect the body tipped
  forward about 45 degrees, wings unfolded mid-beat (one frame of
  the cycle), tail trailing, feet tucked. Blocking only if the body
  lies past level, rolls onto its side, or a wing stays folded.
- Run, eat, sleep: the pose reads clearly; nothing floats, detaches
  or clips through another part.
- Walk frames: the four frames cohere; legs reach the ground.
- Front, side, back, top: the same animal from every side.

And the standing checklist:

- It is the real species; its field marks show from a few metres away.
- Its size is real (the build prints it).
- It is within the triangle, bone and download budgets.
- A seasonal coat only if the real animal changes colour.

## Output

Strict JSON matching its schema: `species` (`$SPECIES`), `round`
($ROUND), and `issues`, each with `part`, `problem`, `severity` and
`fix`.

## Tools

None. Judge from the images only.

## Hard rules

- `blocking` means the model must change: a part that falls short
  of the game's own animal, a broken frame in any clip, floating or
  detached parts, gaps, wrong colours, over budget. `minor` means
  polish worth doing.
- Examine every part and every frame above; skipping a listed check
  is a miss, not a pass.
- Name the exact part and the exact fix for every issue.
- No `blocking` issue once the render meets the bar and the list.

## Stop

Review once per round.
