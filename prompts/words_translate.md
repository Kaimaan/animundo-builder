# Translate the game's words

## Job

Translate these English game strings into $TARGET_LANG. They are
what a child reads while playing, so they must sound natural, not
translated: short sentences, the game's own warm tone, no stiff or
bookish words.

## Input

The strings, as JSON (`key: English`):

```
$STRINGS_JSON
```

How the game's own $TARGET_LANG sounds (English / $TARGET_LANG):

```
$EXAMPLES
```

## Output

Strict JSON only: the same keys, each with its $TARGET_LANG
translation. No fences, no commentary.

## Tools

None. Translate from the input only.

## Hard rules

- Exactly the input keys: no dropped keys, no added keys, no
  renamed keys.
- Keep every `{placeholder}` exactly as written, in the place the
  sentence needs it.
- Names of real places stay as they are; translate only what the
  game would say in $TARGET_LANG.
- A name or title alone (one or two words) gets its usual
  $TARGET_LANG name, never a description.

$FOR_CHILDREN

## Stop

Translate once. If it comes back with errors, fix exactly those.
