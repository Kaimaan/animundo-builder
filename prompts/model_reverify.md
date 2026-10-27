# Verify the earlier issues are fixed

## Job

Last round's review flagged blocking issues in this model. The
builder says it fixed them. Look at the new preview (the last
image; if there are two, the first is the reference sheet) and
check each issue below: fixed, or still broken. Then give the
whole review again, part by part and frame by frame as the review
prompt says.

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
