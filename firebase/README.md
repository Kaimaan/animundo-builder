# Builder rules

Firestore and Storage rules for border requests, areas, judge
builds, jobs, the admin inbox, admin actions and the off switch.
The game's deploy copies the marked
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
