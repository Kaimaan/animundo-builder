# Check the render's marks

## Job

Look at the render of $TITLE and say which of its key
marks show. These marks make the species known; a child
must see them at game distance. Judge only what the
render shows, never what the species should have.

## Input

The key marks:

$MARKS

The render is attached.

## Output

Strict JSON only: the review object, no fences, no
commentary. The schema arrives with the call; its shape:

- `shown`: the key marks the render shows, in the input's
  words.
- `missing`: the key marks the render lacks, in the input's
  words.

Every key mark lands in exactly one of the two. A mark
that shows faintly or partly still shows.

## Stop

Write the review once.
