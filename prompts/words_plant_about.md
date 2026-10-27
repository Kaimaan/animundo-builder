# Write the plant's about texts

## Job

Write the four short English texts a child reads about $NAME
($LATIN) in the game: what it looks like, where it grows, one
striking fact, and its year. Short sentences, the game's own warm
tone, true to the spec below.

## Input

The plant's spec (kind, year, colours):

```
$SPEC_JSON
```

How the game's own English sounds (the bilberry's texts):

```
$EXAMPLES
```

## Output

Strict JSON only: `about_looks`, `about_grows`, `about_fact` and
`about_year`, each one or two sentences. No fences, no commentary.

## Tools

None. Write from the input only.

## Hard rules

- Exactly these four keys, none empty.
- True to the spec: its real shape, colours, seasons and year.
- Never invent a use, a danger, or a place it grows.

$FOR_CHILDREN

## Stop

Write once. If it comes back with errors, fix exactly those.
