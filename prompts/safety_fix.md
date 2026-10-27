# Fix flagged game texts

## Job

Rewrite each text below so the reviewer's notes are fixed and
nothing else changes. You are the writer; the reviewer judges
your rewrite afterwards.

## Input

The texts with their notes, as JSON (`key`, English, Finnish,
`notes`):

```
$ITEMS_JSON
```

How the game's own texts sound (match this tone):

```
$EXAMPLES
```

## Output

Strict JSON only: the same keys, each mapping to `{"en": ...}`
plus `"fi": ...` when the input had Finnish. No fences, no
commentary.

## Tools

None. Rewrite from the input only.

## Hard rules

- Exactly the input keys, each with the same languages it had.
- Fix every note; keep every `{placeholder}` exactly as written.
- Short sentences, the game's warm tone, true to the English
  meaning in both languages.
- Never add new facts, names, advice or links.

## Stop

Rewrite once.
