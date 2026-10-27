# Review the model preview

## Job

Judge the built model from knowledge, with no reference sheet. The
image is the preview sheet of the built model: front, side, back and
top views, four walk frames, and run, eat and sleep poses. Ask: does
this look like $TITLE in the game's low-poly style?

The build reports: $BUILT.

## Input

The image, the build line above, and this checklist:

- It is the real species; its field marks show from a few metres away.
- Its size is real (the build prints it).
- Flat colours only, no textures, no pure black or white.
- Nothing floats, has gaps or clips through other parts in any pose.
- It is within the triangle, bone and download budgets.
- A seasonal coat only if the real animal changes colour.

## Output

Strict JSON matching its schema: `species` (`$SPECIES`), `round`
($ROUND), and `issues`, each with `part`, `problem`, `severity` and
`fix`.

## Tools

None. Judge from the image only.

## Hard rules

- `blocking` means the model must change: floating or detached parts,
  gaps, wrong proportions, wrong colours, broken poses in any clip,
  over budget. `minor` means polish worth doing.
- Name the exact part and the exact fix for every issue.
- No `blocking` issue when the preview matches the species and the list.

## Stop

Review once per round.
