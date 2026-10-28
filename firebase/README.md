# Builder rules

Storage rules for the builder's own paths: published tiles, review
batches and private judge previews. All Firestore rules are the
game's. The game's deploy copies the marked
`// BEGIN animundo-builder` … `// END animundo-builder` block
from `storage.rules` into its own rules; only that block is merged,
so it holds nothing but `match` sections on builder paths.

Run the emulator tests (needs Java; no account, no network
beyond npm on first install):

```sh
cd firebase
npm ci
npm test
```
