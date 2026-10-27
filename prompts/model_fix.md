# Fix the model spec

## Job

Apply every issue and every build error below to the spec, and return
the whole revised spec. Keep what already works; change only what the
issues and errors name.

## Input

The current spec:

```
$SPEC
```

The review issues:

```
$ISSUES
```

The build and check errors (empty means the build passed):

```
$ERRORS
```

The rules (the art guide and the model guide, read from the checkout):

```
$RULES
```

## Output

Strict JSON only: the whole revised spec, no fences, no commentary.

## Tools

None. Fix from the input only.

## Hard rules

- Keep `id`, `name` and `author`. Fix every `blocking` issue.
- Real size in metres; at most 16 bones; triangle budget 1000
  (up to 1500 for a large, complex animal).
- Parts overlap generously; legs reach `y = 0`; eyes and beak sink
  a third in.

## Stop

Return the revised spec once. If it comes back with errors, fix
exactly those.
