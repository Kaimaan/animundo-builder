# Write the species plan

## Job

Rank the surveyed species the pipeline asks you to rank, best first,
and write WHY every species is reused, new, food, or saved for later.
The reuse and food decisions are fixed (see the match table); the
pipeline builds your first $MAX_NEW new ones and saves the rest.

## Input

The survey:

```
$SURVEY_JSON
```

The GBIF match, fixed by the pipeline (decision per species id).
Some reuse lines name existing animals the survey missed but GBIF
records at the place; list them like any surveyed species.

```
$MATCH_TABLE
```

## Output

The species plan as strict JSON matching its schema: one entry per
match-table species with its `decision`, your `reason`, and the `taxon`
copied from its match table line (`food_kind` copied the same way for
food), plus `food_web_ok`, `missing_food`, and `sensitive_species`.
The pipeline replaces both with the matcher's own values.

## Tools

None. Write from the inputs only.

## Hard rules

- Keep every fixed decision exactly as the match table says: `reuse`
  stays reuse, `food` stays food. Rank-me species are `new` or
  `candidate`, best first: food first (what the place's web needs),
  then what a child would most like to play.
- `wild` is false for a herded, domestic, farmed, or stocked species,
  true otherwise. A species that is not wild is `candidate`, never
  `new`: the pipeline will not build it as a wild animal.
- Rank hunters by what this cast can feed: a hunter whose prey is
  abundant here outranks one whose prey is scarce, and prey that feeds
  the cast's hunters outranks one more large hunter.
- Reasons name the GBIF key: for reuse, the existing file and the
  shared key; for new, what is new about it here; for food, that it is
  eaten, not built; for candidate, why it waits.
- Food first: every `predator` in the final cast (reuse + new) needs
  at least one `prey` species in the same cast. If one hunts with
  nothing to hunt, `food_web_ok` is false and `missing_food` names
  what is missing (for example "prey for gray_wolf").
- `sensitive_species` lists every surveyed id the sensitive rule
  covers: no real breeding, nesting, denning, or resting site for it
  in the region file. Include the survey's flagged ones and any you
  spot (for example a nesting raptor).
- Flag in `reason` anything else a person must decide at review: a
  herded or domestic species, a species whose role is unclear.
- Never move a species between reuse, food, and the ranked group.

## Stop

Write the plan once. If it comes back with errors, fix exactly those.
