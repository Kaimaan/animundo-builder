# Builder web app

The grown-ups' app at animundo.app/build (T26). Static files only:
the game's copy publishes this folder as it is. English only
during the event; the park flow (areas, area cards, wishes) is
parked, not deleted: `areas.json` stays vendored but unused.

- `index.html`, `app.js`, `styles.css`: the app.
- `animal-list.json`: the static animal list (name, latin,
  GBIF key, body plan, can_make, habitat), synced with the
  world's file for file (same shape, tested).
- `places-of-interest.json`: ~12,000 well-known natural places
  from Wikidata (short keys, 4 decimals), written by
  `scripts/places_of_interest.py fetch`. Loaded only on the
  place-picker step.
- `animal-places.json`: confirmed places per animal
  (`{animal_id: [[place_id, sightings], ...]}`), written by
  `scripts/places_of_interest.py suggest`. Loaded with the
  picker.
- `tz-country.json`, `country-names.json`: the time-zone table
  (IANA `zone1970.tab`) and ISO-to-name table, written by
  `scripts/places_of_interest.py tables`. The country is
  guessed from the time zone, never the location prompt.
- `place-aliases.json`: hand-kept list ids that count as a
  built place (the lake Saimaa is `fi_saimaa`).
- `places.json`: the game's built places, synced from the
  world like the animal list.
- `i18n/en.json`: every UI string plus the live feed's
  `explore.feed.*` strings.

No map since D11: the grown-up picks an animal, then one of
its suggested places (or a searched one). A searched place
outside the suggestions gets one live GBIF occurrence count
in the browser; only places with 5+ wild sightings build.
The stylesheet follows the game site's own look (dark
forest, cream, sun yellow).

Firebase: the app signs grown-ups in with email links and reads
and writes Firestore and Storage with their own account on the
`animundo-dev` project (the same public web config the game
ships). The key is public by design; its HTTP-referrer
restriction lives in Google Cloud, and `animundo.app` must be an
authorized domain in the Firebase console, or sign-in fails
there.

Run it locally (localhost is authorized by default):

```sh
cd web/build
python3 -m http.server 8000
```

then open http://localhost:8000 in a browser. Opening
`index.html` straight from disk does not work: the page fetches
its JSON over HTTP.

Run it against the emulators instead (no real account, no real
data): in one terminal,

```sh
cd firebase && firebase emulators:start --project demo-animundo \
    --only auth,firestore,storage
```

in another, from the repository root,

```sh
node firebase/seed-emulator.mjs
```

then open http://localhost:8000/?emulator=1 and press **Use the
test grown-up**. The seed script creates `test@example.org`
with a verified email; the email-link flow also works in
emulator mode (the link lands in the emulator output).
