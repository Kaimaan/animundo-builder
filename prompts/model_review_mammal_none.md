# Review the model preview

## Job

Judge whether the new render is as good as the game's own
$CLOSEST_TITLE, and whether a child would recognise $TITLE, from
knowledge, with no reference sheet. The images, in order: the
game's own animal, and the new render last.

The build reports: $BUILT.

## Input

Two images. First: the game's own $CLOSEST_TITLE, the bar to meet.
Last: the new render of $TITLE (front, side, back and top views,
four walk frames, and run, eat and sleep poses).

The question: is the new render as good as the game's own animal?
Pass the first round that meets that bar; don't polish further.
This animal has no wings and no fly clip; do not ask for them.

Check each part against the game's animal and what the real
species looks like, in this order; a part that falls short is an
issue naming that part:

- Silhouette: the right body plan and proportions for the species.
- Legs and feet: the right count, all reaching the ground, hooves
  or paws reading clearly.
- Head, muzzle, ears, and antlers or horns if the species has them:
  the right shape, size and place.
- Tail: present when the species has one, the right length.
- Coat colours and markings: the species' real colours, flat only,
  no textures, no pure black or white.

Then check every animation frame on its own:

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
