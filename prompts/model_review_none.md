# Review the model preview

## Job

Judge the built model from knowledge, with no reference sheet. The
image is the preview sheet of the built model: front, side, back and
top views, four walk frames, and run, eat, sleep and fly poses. Ask:
does this look like $TITLE in the game's low-poly style?

The build reports: $BUILT.

## Input

The image, the build line above, and these examinations, in this
order. Judge each one from what the real species looks like; a part
that is wrong is an issue naming that part:

- Silhouette: the right body plan and proportions for the species.
- Wings: present, folded plausibly at rest, the right size and place.
- Tail: present, the right length, shape and markings.
- Legs and feet: the right count, length and covering; feet on the
  ground in standing frames.
- Head, beak and ears: the right shape and size for the species.
- Colours and markings: the species' real colours, flat only, no
  textures, no pure black or white.

Then check every animation frame on its own:

- Fly: the body tips forward about 45 degrees as it flies, wings
  unfolded mid-beat (one frame of the cycle), tail trailing, feet
  tucked. Low over the ground is correct: the game's birds fly low,
  they do not soar. Blocking only if the body lies past level,
  rolls onto its side, or a wing stays folded.
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

None. Judge from the image only.

## Hard rules

- `blocking` means the model must change: a wrong part, a broken
  frame in any clip, floating or detached parts, gaps, wrong
  colours, over budget. `minor` means polish worth doing.
- Examine every part and every frame above; skipping a listed check
  is a miss, not a pass.
- Name the exact part and the exact fix for every issue.
- No `blocking` issue when the preview matches the species and the list.

## Stop

Review once per round.
