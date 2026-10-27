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
- `marks`: the reference's kind of marks this animal truly
  has: `ear_tufts`, `ruff`, `tail_tip`. Only those three exist.
- `repaint`: parts to paint another colour, as
  `{"part", "color"}` with the part and colour names above.
- `unmakable`: marks the animal has that no edit above can
  make (stripes, a mane, antlers), as plain words. They go to
  the build report for the reviewer to add to the tool; never
  fake them with factors or repaints.

## Tools

None. Write from the input only.

## Hard rules

- Factors near 1: change only what the looks move. A factor
  of 1 keeps the reference's shape, which already reads as an
  animal.
- Marks the animal does not have stay out; marks no edit can
  make go in `unmakable`, never in `marks`.

## Stop

Write the edits once. If they come back with errors, fix
exactly those.
