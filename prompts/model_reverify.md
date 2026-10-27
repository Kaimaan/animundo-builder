# Verify the earlier issues are fixed

## Job

Last round's review flagged blocking issues in this model. The
builder says it fixed them. Judge the new preview on its own
first (the last image; the game's own animal and the reference
sheet, when there is one, come before it), part by part and frame
by frame as the review prompt says; then confirm each earlier
issue is gone, and re-raise only what still visibly stands.

## Input

The earlier blocking issues:

$ISSUES

A still-broken issue is blocking again, under the same part name.

## Output

Strict JSON matching its schema: `species` (`$SPECIES`), `round`
($ROUND), and `issues`, each with `part`, `problem`, `severity` and
`fix`.

## Tools

None. Judge from the images only.

## Stop

Verify once.
