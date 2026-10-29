# Describe the model's edits

## Job

Describe how $TITLE ($LATIN) differs from the $CLOSEST_NAME model,
as a short list of edits. Code scales the reference to the real
size and applies the edits; it moves positions and sizes together,
so parts keep overlapping. Never write positions or sizes: only
colours, factors, marks and repaints.

## Input

What the adult looks like:

$LOOKS

The reference table's line for its kind:

$REFERENCE_LINE

The reference spec's parts (name, colour, bone role):

$PART_LIST

## Output

Strict JSON only: the edits object, no fences, no commentary.
The schema arrives with the call; its shape:

- `colors`: every reference colour name to its hex on this
  animal, like "#7a4a2b". Keep a colour when the survey names
  none.
- `new_colors`: more colours the animal needs, as
  `{"name", "hex"}` pairs; usually empty.
- Seven factors, numbers 0.4 to 2.5, 1 meaning as the
  reference: `head` (the head, whole), `muzzle` (the face in
  front of the eyes), `ears`, `leg_thickness` (legs' width,
  never length), `paws` (the lowest part of each leg),
  `body_girth` (the body's width and depth), `tail_width`.
  `muzzle` is a mammal's face; a bird's is its `bill`. A bird
  reference offers three more, only when the schema names them:
  `neck` (the neck's length; the head rides along), `bill` (the
  bill's length from the head), `bill_depth` (the bill's height,
  to 4).
- `repaint`: parts to paint another colour, as
  `{"part", "color"}` with the part and colour names above.
- `unmakable`: marks the animal has that no edit above can
  make (stripes, a mane, antlers), as plain words. They go to
  the build report for the reviewer to add to the tool; never
  fake them with factors or repaints.

Marks need no entry: code adds ear tufts, ruffs and tail tips
from the research's own words.

## Tools

None. Write from the input only.

## Hard rules

- Factors near 1: change only what the looks move. A factor
  of 1 keeps the reference's shape, which already reads as an
  animal. But lean towards the marks: the species' telling
  differences from the reference read clear at game distance
  (big paws and thick legs want 1.4–1.6, not 1.2). Small
  factors keep the reference's look.
- Never invent marks, and never fake one with factors or
  repaints: what the research names, code adds; the rest goes
  in `unmakable`.

## Stop

Write the edits once. If they come back with errors, fix
exactly those.
