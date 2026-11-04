# Check the render's marks

## Job

Look at the render of $TITLE and say which of its key
marks show. These marks make the species known; a child
must see them at game distance. Judge only what the
render shows, never what the species should have. Our
models are simple, never realistic: a mark is met when a
child would see its shape, not its fine detail. But
shape words still bind: hollow rings are not solid dots,
one named spot is not many, and a mark named on a part
shows only there. The new marks set their shapes:
"rosettes", "ring spots" and "open rings" are round rings
with the coat's colour inside (rosettes), never solid
dots; "blotches", "saddles" and "patches" on a snake or
lizard are spaced patches down the back and sides
(blotches); a "crest", "dorsal spines" or "spiky ridge"
is one row of spikes along the middle of the back
(crest).

## Input

The key marks:

$MARKS

The render is attached: four views of the model, FRONT,
SIDE, BACK 3/4 and TOP. Judge each mark in the views that
show it: a head's outline in the TOP view, a head-on mark
in FRONT and TOP. A mark shows only when visible in its
views.

## Output

Strict JSON only: the review object, no fences, no
commentary. The schema arrives with the call; its shape:

- `shown`: the key marks the render shows, in the input's
  words.
- `missing`: the key marks the render lacks, in the input's
  words.

Every key mark lands in exactly one of the two. A mark
that shows faintly or partly still shows. When unsure
whether a mark shows at all, it is missing.

## Stop

Write the review once.
