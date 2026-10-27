# Builder web app

The grown-ups' app at animundo.app/build (T26). Static files only:
the game's copy publishes this folder as it is. English only
during the event; the park flow (areas, area cards, wishes) is
parked, not deleted: `areas.json` stays vendored but unused.

- `index.html`, `app.js`, `styles.css`: the app.
- `animal-list.json`: the static animal list (name, latin,
  GBIF key, body plan, can_make, habitat). A stub of 8 now;
  the reviewer's ~200-animal list replaces it file for file
  (same shape, tested).
- `i18n/en.json`: every UI string plus the live feed's
  `explore.feed.*` strings.
- `vendor/leaflet/`: Leaflet 1.9.4 (BSD-2-Clause), vendored so
  the app needs no JavaScript CDN besides Firebase itself.

Map data: Natural Earth land and coastline (public domain,
1:110m), vendored under `vendor/natural-earth/` and drawn as
GeoJSON in the game's dark colours. Countries (110m, slimmed
to NAME + ISO_A2) name the tapped spot; urban areas (50m,
geometry only) refuse cities. The only tile layer is GBIF's
free occurrence density for the picked animal
(`api.gbif.org/v2/map/occurrence/density`, `orange.marker`
style); the tapped square is confirmed with one GBIF
occurrence count query. The stylesheet follows the game
site's own look (dark forest, cream, sun yellow).

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
