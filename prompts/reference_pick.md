# Pick the reference-table row

## Job

Match this species to one row of the game's reference-models
table, the existing model its spec starts from.

## Input

The table, read from the world checkout (number, kind of animal,
starting spec, what to change):

```
$TABLE
```

The species:

```
$SPECIES_JSON
```

## Output

Strict JSON only: `{"row": <number>, "kind": "bird" or "mammal"}`.
`kind` is `bird` for birds and `mammal` for everything else (fish
and small animals use the mammal review until theirs exists).
Match by body plan and way of moving, not by name alone: an eagle
is a bird of prey, a deer starts from the moose. A species whose
row starts from `none` cannot be built yet; still return its row.

## Tools

None. Match from the input only.

## Stop

Match once.
