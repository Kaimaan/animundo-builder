# Builder web app

The grown-ups' app at animundo.app/build (T26). Static files only:
the game's copy publishes this folder as it is.

- `index.html`, `app.js`, `styles.css`: the app.
- `areas.json`: the area index (short T20, spec/09's list). The
  worker reads the same file for intake, so records must satisfy
  the builder's area schema.
- `i18n/en.json`, `i18n/fi.json`: every UI string plus the live
  feed's `explore.feed.*` strings. Both files hold the same keys.
- `vendor/leaflet/`: Leaflet 1.9.4 (BSD-2-Clause), vendored so
  the app needs no JavaScript CDN besides Firebase itself.

Map data: OpenStreetMap tiles (© OpenStreetMap contributors,
ODbL) with the required attribution on the map. Ecoregion names
come from RESOLVE 2017 (Dinerstein et al.). Both are credited in
the page footer.

Firebase: the app signs grown-ups in with email links and reads
and writes Firestore and Storage with their own account on the
`animundo-dev` project (the same public web config the game
ships). `animundo.app` must be an authorized domain in the
Firebase console, or sign-in fails there.

Run it locally (localhost is authorized by default):

```sh
cd web/build
python3 -m http.server 8000
```

then open http://localhost:8000 in a browser. Opening
`index.html` straight from disk does not work: the page fetches
its JSON over HTTP.
