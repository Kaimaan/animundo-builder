# Builder rules

Firestore and Storage rules for drafts, the live feed, public
places, jobs, the admin inbox, wishes and the off switch
(spec 05). The game's deploy copies the marked
`// BEGIN animundo-builder` … `// END animundo-builder` block
from each file into its own rules; only that block is merged,
so it holds nothing but `match` sections on builder paths.

Run the emulator tests (needs Java; no account, no network
beyond npm on first install):

```sh
cd firebase
npm ci
npm test
```
