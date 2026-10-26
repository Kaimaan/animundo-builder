# Write the species plan

## Job

Write the species plan: for every surveyed species, WHY it is reused or
new (the decision itself is fixed, see the match table), whether the
food web holds, and anything a person must decide.

## Input

The survey:

```
$SURVEY_JSON
```

The Latin match, fixed by the pipeline (decision per species id):

```
$MATCH_TABLE
```

## Output

The species plan as strict JSON matching its schema: one entry per
surveyed species with its fixed `decision` and your `reason`, plus
`food_web_ok` and `missing_food`.

## Tools

None. Write from the inputs only.

## Hard rules

- Keep every `decision` exactly as the match table says. Your job is the
  `reason`: for reuse, name the existing file and the shared Latin name;
  for new, say what is new about it here.
- Food first: every `predator` needs at least one `prey` species in this
  same cast. If one hunts with nothing to hunt, `food_web_ok` is false
  and `missing_food` names what is missing (for example
  "prey for gray_wolf").
- Flag in `reason` anything a person must decide at review: a herded or
  domestic species, a sensitive species, a species whose role is unclear.
- Never move a species between reuse and new.

## Stop

Write the plan once. If it comes back with errors, fix exactly those.
