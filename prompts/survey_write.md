# Write the survey file

## Job

Turn the research below into the survey file. Every fact keeps the URL of
a source that supports it.

## Input

The area:

```
$AREA_JSON
```

The land research:

```
$LAND_TEXT
```

The species research:

```
$SPECIES_TEXT
```

$GAPS_TEXT

## Output

The survey file, as strict JSON matching its schema: `area_id`, `facts`
(each with `topic`, `text` and at least one `source` URL), and `species`
(each with `species`, `role`, `evidence`, at least one `source` URL,
`conservation` and `sensitive` where known, a `description` of what an
adult looks like, and `measurements`: `body_cm`, `tail_cm`, `weight_kg`,
each a `value` with its `source` URL, plus `wingspan_cm` for birds or
`shoulder_cm` for mammals and fish, also with its source).

## Tools

None. Write from the research only.

## Hard rules

- Every `source` URL must come from the research above. Never invent, fix
  up, shorten or guess a URL.
- If a fact has no supporting source, leave the fact out.
- Species ids are short lower-case codes (`red_deer`); keep the Latin name
  in `latin`.
- Mark `sensitive: true` for every species the research calls sensitive.
- Every measurement number comes from the research, with the URL of the
  source that states it. Never guess a size.
- Lengths in centimetres, weight in kilograms. For fish, `shoulder_cm`
  means body depth.

## Stop

Write the file once. If it comes back with errors, fix exactly those.
