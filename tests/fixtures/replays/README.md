# Replay fixtures: the review jobs' real inputs, maker's stand-in answers

`home.json` and `records/research.jsonl` are copies of the real
saved jobs (`jobs/review-fox`, `jobs/review-lynx`,
`jobs/review-anaconda`, gitignored): the brief as intake wrote
it (run outputs stripped) and the recorded research answer. The
tests replay them with no key and run each build as far as it
goes (`tests/test_replays.py`).

The maker answers are stand-ins, adapted from real world files
the way the one-shot writer adapts its `start_from`:

- `lynx/oneshot.json`: the fox's animal file and spec, relabelled
  lynx (`Lynx lynx`, GBIF 2435240).
- `anaconda/oneshot-capybara.json`: the vole's animal file and
  spec, relabelled capybara.
- `anaconda/oneshot-anaconda.json`: the fox's animal file (diet
  carrion, hunts small mammals) and the grass-snake example
  spec, relabelled anaconda.
- `anaconda/newhome.json`: the boreal home with a rainforest id
  and name, food for the fox check plus the habitat's kinds,
  and a water-lettuce spec adapted from the bilberry.

When the reviewer records the live maker runs, these stand-ins
can be replaced with the real recordings.
