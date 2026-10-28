"use strict";
/* Animundo Builder: the grown-ups' web app.
 *
 * Static files only (the game's copy publishes this folder at
 * animundo.app/build). Animal first: the grown-up picks an animal
 * from a static list, taps its home on GBIF's occurrence map, and
 * the worker builds the home. Talks to Firestore and Storage with
 * the signed-in grown-up's own account, so the security rules are
 * what lets each button work: the app never does what the rules
 * forbid, and it never sees another family's drafts.
 *
 * English only during the event. The park flow (areas, area cards,
 * wishes) is parked, not deleted: areas.json stays vendored.
 */

const FIREBASE_CONFIG = {
  // Public by design (the same key the game ships): Firebase web
  // keys are restricted by domain, not by secrecy. Allowlisted in
  // .gitleaks.toml for exactly this key in exactly this file.
  apiKey: "AIzaSyAXX6Gz5aN5QQ2txFQMkUGHX9D1fppyEWA",
  authDomain: "animundo-dev.firebaseapp.com",
  projectId: "animundo-dev",
  storageBucket: "animundo-dev.firebasestorage.app",
  messagingSenderId: "330774612272",
  appId: "1:330774612272:web:89a96bec0b1258775ea0f5",
};

// Local emulators, from firebase/firebase.json. Only used with ?emulator=1.
const EMULATORS = {
  auth: "http://127.0.0.1:9099",
  firestore: ["127.0.0.1", 8080],
  storage: ["127.0.0.1", 9199],
};
const TEST_GROWN_UP = { email: "test@example.org", password: "grown-up-1" };

// GBIF's free occurrence tiles for the picked animal (the style name
// is from GBIF's maps API docs), over the Natural Earth map. Hexagon
// bins, not points: a range reads as a filled area, and even one
// record draws a fingertip-sized cell instead of a 1 px dot (#74).
const GBIF_TILES =
  "https://api.gbif.org/v2/map/occurrence/density/{z}/{x}/{y}@1x.png" +
  "?taxonKey={key}&style=orange.marker&bin=hex&hexPerTile=8";
const GBIF_OCCURRENCES = "https://api.gbif.org/v1/occurrence/search";
const GAME_URL = "https://animundo.app/";

const state = {
  lang: "en",
  strings: {},
  user: null,
  animalList: [],
  animalHomes: [],
  notYet: {},
  picked: null,
  pickedHome: null,
  pickedShort: null,
  pickedHabitat: null,
  gbifLayer: null,
  adminShapes: null,
  urbanShapes: null,
  selectedCell: null,
  cellCheck: 0,
  drafts: {},
  feedUnsubs: {},
  feedRetry: {},
  feedTries: {},
  map: null,
  base110: null,
  base50: null,
  base50Loading: false,
  base10: null,
  base10Loading: false,
};

const $ = (id) => document.getElementById(id);

function t(key, args) {
  let text = state.strings[key] || key;
  for (const [name, value] of Object.entries(args || {})) {
    text = text.replace(`{${name}}`, String(value));
  }
  return text;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[ch]);
}

function showError(message) {
  const box = $("app-error");
  box.textContent = message || t("error.generic");
  box.hidden = false;
}

function clearError() {
  $("app-error").hidden = true;
}

/* -- language: English only during the event ------------------- */

async function loadStrings() {
  const response = await fetch("i18n/en.json");
  state.strings = await response.json();
  document.documentElement.lang = "en";
}

function applyStaticStrings() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.getAttribute("data-i18n"));
  }
  for (const el of document.querySelectorAll("[data-i18n-ph]")) {
    el.placeholder = t(el.getAttribute("data-i18n-ph"));
  }
}

/* -- auth ---------------------------------------------------- */

function firebaseAuth() {
  return firebase.auth();
}

function emulatorMode() {
  return new URLSearchParams(window.location.search).has("emulator");
}

async function signInTestGrownUp() {
  clearError();
  try {
    await firebaseAuth().signInWithEmailAndPassword(
      TEST_GROWN_UP.email,
      TEST_GROWN_UP.password,
    );
  } catch (error) {
    console.error(error);
    showError(t("auth.seed_first"));
  }
}

function returnUrl() {
  const url = new URL(window.location.href);
  const clean = `${url.origin}${url.pathname}`;
  return url.searchParams.has("emulator") ? `${clean}?emulator=1` : clean;
}

async function sendLink() {
  clearError();
  const email = $("auth-email").value.trim();
  if (!email) {
    return;
  }
  try {
    localStorage.setItem("animundoBuildEmail", email);
  } catch (_) {
    /* private mode */
  }
  try {
    await firebaseAuth().sendSignInLinkToEmail(email, {
      url: returnUrl(),
      handleCodeInApp: true,
    });
    $("auth-note").textContent = t("auth.sent");
  } catch (error) {
    console.error(error);
    showError();
  }
}

async function finishLink() {
  if (!firebaseAuth().isSignInWithEmailLink(window.location.href)) {
    return false;
  }
  let email = null;
  try {
    email = localStorage.getItem("animundoBuildEmail");
  } catch (_) {
    /* private mode */
  }
  if (!email) {
    email = window.prompt("Email:") || "";
  }
  try {
    await firebaseAuth().signInWithEmailLink(email, window.location.href);
    window.history.replaceState({}, "", returnUrl());
    return true;
  } catch (error) {
    console.error(error);
    showError(t("auth.bad_link"));
    return false;
  }
}

function onUser(user) {
  state.user = user;
  $("auth-signed-out").hidden = !!user;
  $("auth-signed-in").hidden = !user;
  $("app").hidden = !user;
  // Your drafts shows from the first paint when signed in; only the
  // map waits for an animal (#90).
  $("drafts").hidden = !user;
  if (!user) {
    for (const unsub of Object.values(state.feedUnsubs)) {
      unsub();
    }
    state.feedUnsubs = {};
    for (const timer of Object.values(state.feedRetry)) {
      clearTimeout(timer);
    }
    state.feedRetry = {};
    state.feedTries = {};
    if (state.draftsUnsub) {
      state.draftsUnsub();
      state.draftsUnsub = null;
    }
    state.drafts = {};
    state.feeds = {};
    state.picked = null;
    state.pickedHome = null;
    state.pickedShort = null;
    state.pickedHabitat = null;
    state.selectedCell = null;
    setGbifLayer(null);
    clearPickMark();
    $("map-pane").hidden = true;
    $("drafts").hidden = true;
    return;
  }
  $("auth-who").textContent = t("auth.signed_in", { email: user.email || "" });
  void boot();
}

/* -- data ---------------------------------------------------- */

function db() {
  return firebase.firestore();
}

function storage() {
  return firebase.storage();
}

async function boot() {
  if (!state.dataLoaded) {
    const [animals, admin, urban] = await Promise.all([
      fetch("animal-list.json").then((r) => r.json()),
      fetch("vendor/natural-earth/ne_110m_admin_0_countries.geojson").then((r) => r.json()),
      fetch("vendor/natural-earth/ne_50m_urban_areas.geojson").then((r) => r.json()),
    ]);
    state.animalList = animals.animals || [];
    state.animalHomes = animals.homes || [];
    state.notYet = animals.not_yet || {};
    state.adminShapes = admin.features || [];
    state.urbanShapes = urban.features || [];
    state.dataLoaded = true;
  }
  if (state.draftsUnsub) {
    renderDrafts();
    return;
  }
  state.draftsUnsub = db().collection("drafts")
    .where("family_uids", "array-contains", state.user.uid)
    .onSnapshot(
      (snap) => {
        const seen = new Set();
        snap.forEach((doc) => {
          seen.add(doc.id);
          state.drafts[doc.id] = {
            id: doc.id,
            ...doc.data(),
            _synced: !doc.metadata.hasPendingWrites,
          };
        });
        for (const id of Object.keys(state.drafts)) {
          if (!seen.has(id)) {
            delete state.drafts[id];
            if (state.feedUnsubs[id]) {
              state.feedUnsubs[id]();
              delete state.feedUnsubs[id];
            }
            if (state.feedRetry[id]) {
              clearTimeout(state.feedRetry[id]);
              delete state.feedRetry[id];
            }
            delete state.feedTries[id];
          }
        }
        renderDrafts();
      },
      (error) => {
        console.error(error);
        showError();
      },
    );
}

/* -- map ----------------------------------------------------- */

function initMap() {
  if (state.map) {
    return;
  }
  state.map = L.map("map", { worldCopyJump: true }).setView([30, 10], 2);
  // The records draw above the land (#76): the Natural Earth layers
  // live in Leaflet's overlayPane (z-index 400) while plain tile
  // layers sit in the tilePane (200), so the opaque land would cover
  // every record on land. Taps pass through: the map keeps them.
  state.map.createPane("records");
  state.map.getPane("records").style.zIndex = 450;
  state.map.getPane("records").style.pointerEvents = "none";
  // Natural Earth land, vendored: no tile service, no terms, no limits.
  state.base110 = L.layerGroup().addTo(state.map);
  fetch("vendor/natural-earth/ne_110m_land.geojson")
    .then((response) => response.json())
    .then((land) => {
      L.geoJSON(land, LAND_STYLE).addTo(state.base110);
    })
    .catch((error) => console.error(error));
  fetch("vendor/natural-earth/ne_110m_coastline.geojson")
    .then((response) => response.json())
    .then((coast) => {
      L.geoJSON(coast, { style: COAST_STYLE }).addTo(state.base110);
    })
    .catch((error) => console.error(error));
  state.map.on("click", (event) => onMapTap(event.latlng));
  state.map.on("zoomend", updateBaseDetail);
  updateBaseDetail();
}

const LAND_STYLE = {
  style: {
    color: "#3b5544",
    weight: 1,
    fillColor: "#2c3a32",
    fillOpacity: 1,
  },
  attribution: "Natural Earth",
};
const COAST_STYLE = { color: "#c9c2ad", weight: 1, opacity: 0.4 };

// The coastline gains detail with the zoom (#75): the 110m base
// draws first, the 50m pair loads on the first zoom to 4, the
// 10m land on the first zoom to 6. While a tier loads, the map
// keeps showing the coarser one.
function updateBaseDetail() {
  const zoom = state.map.getZoom();
  if (zoom >= 6) {
    if (state.base10) {
      showBase(state.base10);
    } else if (!state.base10Loading) {
      state.base10Loading = true;
      fetch("vendor/natural-earth/ne_10m_land.geojson")
        .then((response) => response.json())
        .then((land) => {
          const group = L.layerGroup();
          L.geoJSON(land, LAND_STYLE).addTo(group);
          L.geoJSON(land, { style: { ...COAST_STYLE, fill: false } }).addTo(group);
          state.base10 = group;
          updateBaseDetail();
        })
        .catch((error) => {
          console.error(error);
          state.base10Loading = false;
        });
    }
    return;
  }
  if (zoom >= 4) {
    if (state.base50) {
      showBase(state.base50);
    } else if (!state.base50Loading) {
      state.base50Loading = true;
      Promise.all([
        fetch("vendor/natural-earth/ne_50m_land.geojson").then((r) => r.json()),
        fetch("vendor/natural-earth/ne_50m_coastline.geojson").then((r) => r.json()),
      ])
        .then(([land, coast]) => {
          const group = L.layerGroup();
          L.geoJSON(land, LAND_STYLE).addTo(group);
          L.geoJSON(coast, { style: COAST_STYLE }).addTo(group);
          state.base50 = group;
          updateBaseDetail();
        })
        .catch((error) => {
          console.error(error);
          state.base50Loading = false;
        });
    }
    return;
  }
  showBase(state.base110);
}

function showBase(layer) {
  for (const known of [state.base110, state.base50, state.base10]) {
    if (known && known !== layer) {
      state.map.removeLayer(known);
    }
  }
  state.map.addLayer(layer);
}

function setGbifLayer(gbifKey) {
  if (state.gbifLayer) {
    state.map.removeLayer(state.gbifLayer);
    state.gbifLayer = null;
  }
  if (!gbifKey) {
    return;
  }
  state.gbifLayer = L.tileLayer(GBIF_TILES.replace("{key}", gbifKey), {
    attribution: "GBIF.org",
    pane: "records",
  }).addTo(state.map);
}

// The picked animal's range, armed (#78): only an animal with no
// records anywhere says so; anything else gets its layer and a zoom.
async function armRange(animal) {
  let count = 0;
  try {
    const url = `${GBIF_OCCURRENCES}?taxon_key=${animal.gbif_key}&limit=0`;
    count = (await (await fetch(url)).json()).count || 0;
  } catch (error) {
    console.error(error);
  }
  if (!state.picked || state.picked.gbif_key !== animal.gbif_key) {
    return;
  }
  if (!count) {
    $("animal-msg").textContent = t("home.no_records", { name: animal.name });
    return;
  }
  setGbifLayer(animal.gbif_key);
  void zoomToRange(animal.gbif_key);
}

function clearPickMark() {
  if (state.pickMark) {
    state.map.removeLayer(state.pickMark);
    state.pickMark = null;
  }
}

/* -- the animal ---------------------------------------------- */

function animalName(key) {
  const found = state.animalList.find((a) => a.gbif_key === key);
  return found ? found.name : "";
}

function renderSuggestions() {
  const box = $("animal-suggest");
  const query = $("animal-q").value.trim().toLowerCase();
  box.innerHTML = "";
  $("animal-msg").textContent = "";
  if (!query) {
    return;
  }
  const matches = state.animalList.filter((animal) =>
    animal.name.toLowerCase().includes(query) ||
    animal.latin.toLowerCase().includes(query)
  ).slice(0, 6);
  if (!matches.length) {
    $("animal-msg").textContent = t("home.no_match");
    return;
  }
  for (const animal of matches) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.innerHTML = `${escapeHtml(animal.name)} <small>${escapeHtml(animal.latin)}</small>`;
    button.addEventListener("click", () => pickAnimal(animal));
    box.appendChild(button);
  }
}

function pickAnimal(animal) {
  state.picked = null;
  state.pickedHome = null;
  state.pickedShort = null;
  state.pickedHabitat = null;
  state.selectedCell = null;
  $("cell-card").hidden = true;
  $("animal-suggest").innerHTML = "";
  setGbifLayer(null);
  clearPickMark();
  if (!animal.can_make) {
    $("animal-msg").textContent =
      state.notYet[animal.not_yet] || t("home.cannot", { name: animal.name });
    return;
  }
  // The worker resolves the same home the same way: the animal's
  // first habitat with a built home, else its first habitat, made
  // to order (#42).
  const habitats = animal.habitats || [];
  const home = habitats.find((h) => state.animalHomes.includes(h)) || habitats[0];
  if (!home) {
    $("animal-msg").textContent = t("home.no_home");
    return;
  }
  state.picked = animal;
  state.pickedHome = home;
  state.pickedShort =
    animal.name.split(" ").pop().replace(/^./, (c) => c.toUpperCase());
  state.pickedHabitat = home.split("_").pop();
  void armRange(animal);
  const built = state.animalHomes.includes(home);
  $("animal-msg").textContent = built
    ? t("home.picked", { name: animal.name })
    : t("home.make_home_too");
  $("map-pane").hidden = false;
  $("drafts").hidden = false;
  initMap();
  state.map.invalidateSize();
}

/* -- the spot ------------------------------------------------ */

function snapCell(lat, lng) {
  const norm = ((((lng + 180) % 360) + 360) % 360) - 180;
  const south = Math.floor(lat * 10) / 10;
  const west = Math.floor(norm * 10) / 10;
  return {
    id: `${south.toFixed(1)}_${west.toFixed(1)}`,
    south,
    west,
  };
}

function ringHas(ring, lon, lat) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function shapeHas(geometry, lon, lat) {
  if (!geometry) {
    return false;
  }
  if (geometry.type === "Polygon") {
    return ringHas(geometry.coordinates[0], lon, lat);
  }
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.some((poly) => ringHas(poly[0], lon, lat));
  }
  return false;
}

function countryAt(lat, lon) {
  for (const feature of state.adminShapes) {
    if (shapeHas(feature.geometry, lon, lat)) {
      return {
        name: feature.properties.NAME || "",
        iso: feature.properties.ISO_A2 || "",
      };
    }
  }
  return null;
}

function urbanAt(lat, lon) {
  return state.urbanShapes.some((feature) => shapeHas(feature.geometry, lon, lat));
}

async function occurrencesInBox(gbifKey, west, south, east, north) {
  const box = `POLYGON((${west} ${south}, ${east} ${south}, ` +
    `${east} ${north}, ${west} ${north}, ${west} ${south}))`;
  const url = `${GBIF_OCCURRENCES}?taxon_key=${gbifKey}` +
    `&geometry=${encodeURIComponent(box)}&limit=0`;
  const response = await fetch(url);
  const data = await response.json();
  return (data.count || 0) > 0;
}

async function occurrencesInCell(gbifKey, cell) {
  return occurrencesInBox(
    gbifKey, cell.west, cell.south, cell.west + 0.1, cell.south + 0.1,
  );
}

// The nearest 0.1° cell with a record within about 50 km (#74):
// one box call rejects empty ground, then expanding rings (each
// ring checked together) find the closest recorded cell.
async function recordsNear(gbifKey, lat, lng) {
  if (!await occurrencesInBox(gbifKey, lng - 0.5, lat - 0.5, lng + 0.5, lat + 0.5)) {
    return null;
  }
  for (let ring = 0; ring <= 5; ring++) {
    const found = await ringWithRecords(gbifKey, lat, lng, ring);
    if (found.length) {
      found.sort((a, b) => cellDistance(a, lat, lng) - cellDistance(b, lat, lng));
      return found[0];
    }
  }
  return null;
}

function ringCells(lat, lng, ring) {
  const middle = snapCell(lat, lng);
  const cells = [];
  for (let di = -ring; di <= ring; di++) {
    for (let dj = -ring; dj <= ring; dj++) {
      if (Math.max(Math.abs(di), Math.abs(dj)) !== ring) {
        continue;
      }
      const south = Math.round((middle.south + di * 0.1) * 10) / 10;
      if (south < -90 || south >= 90) {
        continue;
      }
      let west = Math.round((middle.west + dj * 0.1) * 10) / 10;
      if (west < -180) {
        west += 360;
      } else if (west >= 180) {
        west -= 360;
      }
      cells.push({ id: `${south.toFixed(1)}_${west.toFixed(1)}`, south, west });
    }
  }
  return cells;
}

async function ringWithRecords(gbifKey, lat, lng, ring) {
  const cells = ringCells(lat, lng, ring);
  const hits = await Promise.all(
    cells.map(async (cell) =>
      (await occurrencesInCell(gbifKey, cell)) ? cell : null
    ),
  );
  return hits.filter((cell) => cell);
}

function cellDistance(cell, lat, lng) {
  const dLat = cell.south + 0.05 - lat;
  const dLng = cell.west + 0.05 - lng;
  return dLat * dLat + dLng * dLng;
}

// Fit the map to where the animal lives (#74): a sample of its
// records gives the bounding area.
async function zoomToRange(gbifKey) {
  try {
    const url = `${GBIF_OCCURRENCES}?taxon_key=${gbifKey}` +
      "&has_coordinate=true&has_geospatial_issue=false&limit=300";
    const data = await (await fetch(url)).json();
    const points = (data.results || []).filter(
      (row) => row.decimalLatitude != null && row.decimalLongitude != null,
    );
    if (!points.length || !state.map) {
      return;
    }
    const lats = points.map((row) => row.decimalLatitude);
    const lons = points.map((row) => row.decimalLongitude);
    state.map.fitBounds(
      [
        [Math.min(...lats), Math.min(...lons)],
        [Math.max(...lats), Math.max(...lons)],
      ],
      { padding: [20, 20] },
    );
  } catch (error) {
    console.error(error);
  }
}

async function onMapTap(latlng) {
  clearError();
  if (!state.picked) {
    $("animal-msg").textContent = t("home.pick_first");
    return;
  }
  const animal = state.picked;
  const check = ++state.cellCheck;
  const card = $("cell-card");
  card.hidden = false;
  $("cell-make").hidden = true;
  $("cell-msg").textContent = "";
  $("cell-name").textContent = "";
  $("cell-facts").textContent = "";
  $("cell-state").textContent = t("home.checking");
  let cell = null;
  try {
    cell = await recordsNear(animal.gbif_key, latlng.lat, latlng.lng);
  } catch (error) {
    console.error(error);
    if (check !== state.cellCheck) {
      return;
    }
  }
  if (check !== state.cellCheck) {
    return;
  }
  // The picked cell decides: a tap near the shore can land across
  // the water, and cities stay out even with records in them. A tap
  // with nothing near makes no card at all — only a pick does (#78).
  const at = cell ? { lat: cell.south + 0.05, lng: cell.west + 0.05 } : null;
  const country = at ? countryAt(at.lat, at.lng) : null;
  if (!cell || !country || !country.name || urbanAt(at.lat, at.lng)) {
    card.hidden = true;
    clearPickMark();
    state.selectedCell = null;
    return;
  }
  const title =
    `${state.pickedShort} ${state.pickedHabitat}, ${country.name}`;
  $("cell-name").textContent = title;
  $("cell-facts").textContent = `${animal.latin} · ${cell.id}`;
  state.selectedCell = { ...cell, title, country, animal };
  $("cell-state").textContent = t("home.about");
  $("cell-make").hidden = false;
  clearPickMark();
  state.pickMark = L.rectangle(
    [[cell.south, cell.west], [cell.south + 0.1, cell.west + 0.1]],
    { color: "#f2b84b", weight: 2, fill: false },
  ).addTo(state.map);
}

function homeForCell(speciesKey, cellId) {
  return Object.values(state.drafts).find((d) =>
    d.kind === "animal_home" && d.species_key === speciesKey && d.cell === cellId
  ) || null;
}

async function makeHome() {
  clearError();
  const selected = state.selectedCell;
  if (!selected || !state.picked) {
    return;
  }
  if (homeForCell(state.picked.gbif_key, selected.id)) {
    $("cell-msg").textContent = t("home.exists");
    return;
  }
  // The worker writes the name and the country from the cell (#41).
  const fields = {
    kind: "animal_home",
    species_key: state.picked.gbif_key,
    cell: selected.id,
    owner_uid: state.user.uid,
    family_uids: [state.user.uid],
    status: "requested",
    created_at: firebase.firestore.FieldValue.serverTimestamp(),
  };
  try {
    await db().collection("drafts").add(fields);
    $("cell-msg").textContent = t("home.started");
    $("cell-make").hidden = true;
  } catch (error) {
    console.error(error);
    showError();
  }
}

/* -- drafts --------------------------------------------------- */

function feedText(entry) {
  return t(entry.text_key || "", entry.args || {});
}

function stampMillis(value) {
  if (!value) {
    return 0;
  }
  if (typeof value.toDate === "function") {
    return value.toDate().getTime();
  }
  return Date.parse(value) || 0;
}

function renderDrafts() {
  const list = $("draft-list");
  const drafts = Object.values(state.drafts).sort(
    (a, b) =>
      stampMillis(b.updated_at || b.created_at) -
      stampMillis(a.updated_at || a.created_at),
  );
  list.innerHTML = "";
  for (const draft of drafts) {
    list.appendChild(renderDraft(draft));
    // The feed rule reads the draft, so a locally-written draft starts
    // no listener until the server has it.
    if (draft._synced) {
      watchFeed(draft);
    }
    renderFeed(draft.id);
  }
}

function draftTitle(draft) {
  if (draft.place_name) {
    return draft.place_name;
  }
  if (draft.kind === "animal_home") {
    const name = animalName(draft.species_key);
    return name ? t("home.draft_title", { animal: name }) : t("home.untitled");
  }
  return draft.area_id || draft.id;
}

function renderDraft(draft) {
  const card = document.createElement("article");
  card.className = "draft";
  card.dataset.id = draft.id;
  const statusKey = `draft.status.${draft.status}`;
  const status = state.strings[statusKey] || t("draft.status.failed");
  let html = `<h3>${escapeHtml(draftTitle(draft))}</h3>`;
  html += `<p class="muted">${escapeHtml(status)} · ${
    escapeHtml(t("draft.round", { round: draft.round || 1 }))
  }</p>`;
  if (draft.admin_answer) {
    html += `<p class="answer">${escapeHtml(t("draft.answer", { answer: draft.admin_answer }))}</p>`;
  }
  if (draft.review_result && draft.review_result.reason) {
    html += `<p class="muted">${
      escapeHtml(t("draft.review_reason", { reason: draft.review_result.reason }))
    }</p>`;
  }
  if (draft.round) {
    html += `<p><a href="#" data-report="${escapeHtml(draft.id)}">${
      escapeHtml(t("draft.report"))
    }</a></p>`;
  }
  html += `<details><summary>${escapeHtml(t("draft.watch"))}</summary>` +
    `<div class="feed" data-feed="${escapeHtml(draft.id)}"></div></details>`;
  html += draftActions(draft);
  card.innerHTML = html;
  wireDraft(card, draft);
  return card;
}

function draftActions(draft) {
  const status = draft.status;
  let html = `<div class="actions" data-actions="${escapeHtml(draft.id)}">`;
  if ((draft.pack_version || 0) >= 1) {
    html += `<button type="button" data-act="play">${
      escapeHtml(t("draft.play"))
    }</button>`;
  }
  if (status === "failed") {
    html += `<p>${escapeHtml(t("draft.failed_words"))}</p>` +
      `<button type="button" data-act="try_again">${escapeHtml(t("draft.try_again"))}</button>` +
      `<button type="button" data-act="remove">${escapeHtml(t("draft.remove"))}</button>`;
  }
  const canImprove = status === "ready" && (draft.round || 1) < 4;
  const needsPerson = status === "ready" && (draft.round || 1) >= 4;
  if (canImprove) {
    html += `<div class="improve"><h4>${escapeHtml(t("draft.improve"))}</h4>` +
      ["animal_looks_wrong", "too_few_animals", "colours_off", "other"].map((choice) =>
        `<label><input type="checkbox" name="choice" value="${choice}"> ` +
        `${escapeHtml(t(`choice.${choice}`))}</label>`
      ).join("") +
      `<input type="text" name="species" maxlength="80" placeholder="${
        escapeHtml(t("choice.species_ph"))
      }">` +
      `<input type="text" name="note" maxlength="200" placeholder="${
        escapeHtml(t("draft.improve_note_ph"))
      }">` +
      `<button type="button" data-act="improve">${escapeHtml(t("draft.improve_send"))}</button></div>`;
  }
  if (needsPerson) {
    html += `<p class="muted">${escapeHtml(t("draft.ask_person"))}</p>` +
      `<button type="button" data-act="ask_person">${escapeHtml(t("draft.ask_person_btn"))}</button>`;
  }
  if ((status === "ready" || status === "not_accepted") && draft.last_played_at) {
    html += `<button type="button" data-act="submit">${escapeHtml(t("draft.submit"))}</button>`;
  } else if (status === "ready" || status === "not_accepted") {
    html += `<p class="muted">${escapeHtml(t("draft.play_first"))}</p>`;
  }
  if (["ready", "in_review", "not_accepted"].includes(status)) {
    html += `<div class="feedback"><h4>${escapeHtml(t("draft.not_right"))}</h4>` +
      `<select name="nr_reason">` +
      ["wrong_animals", "looks_wrong", "not_like_the_real_place", "other"].map((reason) =>
        `<option value="${reason}">${escapeHtml(t(`reason.${reason}`))}</option>`
      ).join("") + `</select>` +
      `<button type="button" data-act="not_right">${escapeHtml(t("draft.send"))}</button></div>`;
    html += `<div class="feedback"><h4>${escapeHtml(t("draft.report_problem"))}</h4>` +
      `<select name="pb_reason">` +
      ["unsuitable_text", "scary_or_upsetting", "wrong_safety_advice", "other"].map((reason) =>
        `<option value="${reason}">${escapeHtml(t(`reason.${reason}`))}</option>`
      ).join("") + `</select>` +
      `<input type="text" name="pb_note" maxlength="300" placeholder="${
        escapeHtml(t("draft.problem_note_ph"))
      }">` +
      `<button type="button" data-act="problem">${escapeHtml(t("draft.send"))}</button></div>`;
  }
  html += `<p class="muted" data-msg></p></div>`;
  return html;
}

function wireDraft(card, draft) {
  const reportLink = card.querySelector("[data-report]");
  if (reportLink) {
    reportLink.addEventListener("click", (event) => {
      event.preventDefault();
      openReport(draft, reportLink);
    });
  }
  for (const button of card.querySelectorAll("[data-act]")) {
    button.addEventListener("click", () => actOnDraft(draft, button));
  }
}

async function openReport(draft, link) {
  clearError();
  try {
    const url = await storage()
      .ref(`reports/${draft.id}/${draft.round}/report.html`)
      .getDownloadURL();
    window.open(url, "_blank", "noopener");
  } catch (error) {
    console.error(error);
    link.textContent = t("draft.report") + " (—)";
  }
}

function draftMessage(draftId, text) {
  const box = document.querySelector(`[data-actions="${draftId}"] [data-msg]`);
  if (box) {
    box.textContent = text;
  }
}

async function actOnDraft(draft, button) {
  clearError();
  const action = button.getAttribute("data-act");
  const card = button.closest("[data-actions]");
  const stamp = firebase.firestore.FieldValue.serverTimestamp();
  try {
    if (action === "play") {
      window.open(`${GAME_URL}?draft=${encodeURIComponent(draft.id)}`, "_blank");
    } else if (action === "improve") {
      const choices = [...card.querySelectorAll('input[name="choice"]:checked')]
        .map((box) => {
          const entry = { choice: box.value };
          if (box.value === "animal_looks_wrong") {
            const species = card.querySelector('input[name="species"]').value.trim();
            if (!species) {
              draftMessage(draft.id, t("choice.species_ph"));
              throw new Error("species");
            }
            entry.species = species;
          }
          return entry;
        });
      if (!choices.length) {
        return;
      }
      const improve = { choices, at: stamp };
      const note = card.querySelector('input[name="note"]').value.trim();
      if (note) {
        improve.note = note.slice(0, 200);
      }
      await db().collection("drafts").doc(draft.id).update({ improve });
      draftMessage(draft.id, t("draft.improve_sent"));
    } else if (action === "submit") {
      await db().collection("drafts").doc(draft.id).update({
        submit: { at: stamp },
      });
      draftMessage(draft.id, t("draft.submit_sent"));
    } else if (action === "not_right") {
      await db().collection("drafts").doc(draft.id).update({
        feedback: {
          kind: "not_right",
          reason: card.querySelector('select[name="nr_reason"]').value,
          at: stamp,
        },
      });
      draftMessage(draft.id, t("draft.sent"));
    } else if (action === "problem") {
      const feedback = {
        kind: "problem",
        reason: card.querySelector('select[name="pb_reason"]').value,
        at: stamp,
      };
      const note = card.querySelector('input[name="pb_note"]').value.trim();
      if (note) {
        feedback.note = note.slice(0, 300);
      }
      await db().collection("drafts").doc(draft.id).update({ feedback });
      draftMessage(draft.id, t("draft.sent"));
    } else if (action === "ask_person") {
      await db().collection("drafts").doc(draft.id).update({
        feedback: { kind: "ask_person", reason: "still_wrong", at: stamp },
      });
      draftMessage(draft.id, t("draft.sent"));
    } else if (action === "try_again") {
      await db().collection("drafts").doc(draft.id).update({ status: "requested" });
      draftMessage(draft.id, t("draft.try_again_sent"));
    } else if (action === "remove") {
      if (!window.confirm(t("draft.remove_confirm"))) {
        return;
      }
      await db().collection("drafts").doc(draft.id).delete();
    }
  } catch (error) {
    console.error(error);
    showError();
  }
}

function watchFeed(draft) {
  if (state.feedUnsubs[draft.id] || state.feedRetry[draft.id]) {
    return;
  }
  const start = () => {
    delete state.feedRetry[draft.id];
    state.feedUnsubs[draft.id] = db().collection("drafts").doc(draft.id)
      .collection("feed").orderBy("n").onSnapshot(
        (snap) => {
          const entries = [];
          snap.forEach((doc) => entries.push(doc.data()));
          state.feeds = state.feeds || {};
          state.feeds[draft.id] = entries;
          delete state.feedTries[draft.id];
          renderFeed(draft.id);
        },
        (error) => {
          console.error(error);
          state.feedUnsubs[draft.id]();
          delete state.feedUnsubs[draft.id];
          const tries = (state.feedTries[draft.id] || 0) + 1;
          if (tries > 3) {
            delete state.feedTries[draft.id];
            return;
          }
          state.feedTries[draft.id] = tries;
          state.feedRetry[draft.id] = setTimeout(() => {
            const current = state.drafts[draft.id];
            if (current && current._synced) {
              start();
            } else {
              delete state.feedRetry[draft.id];
            }
          }, [3000, 10000, 30000][tries - 1]);
        },
      );
  };
  start();
}

function renderFeed(draftId) {
  const box = document.querySelector(`[data-feed="${draftId}"]`);
  if (!box) {
    return;
  }
  box.innerHTML = "";
  for (const entry of (state.feeds || {})[draftId] || []) {
    const line = document.createElement("p");
    line.textContent = feedText(entry);
    box.appendChild(line);
    if (entry.kind === "preview" && entry.image) {
      const img = document.createElement("img");
      img.alt = feedText(entry);
      img.loading = "lazy";
      box.appendChild(img);
      storage().ref(entry.image).getDownloadURL().then(
        (url) => {
          img.src = url;
        },
        (error) => {
          console.error(error);
          img.remove();
        },
      );
    }
  }
}

/* -- boot ------------------------------------------------------ */

async function start() {
  const params = new URLSearchParams(window.location.search);
  // Emulator mode runs under a demo- project, which can never
  // reach production; start and seed the emulators with the same id.
  const config = params.has("emulator")
    ? { ...FIREBASE_CONFIG, projectId: "demo-animundo" }
    : FIREBASE_CONFIG;
  firebase.initializeApp(config);
  if (emulatorMode()) {
    firebaseAuth().useEmulator(EMULATORS.auth, { disableWarnings: true });
    db().useEmulator(...EMULATORS.firestore);
    storage().useEmulator(...EMULATORS.storage);
    $("auth-test").hidden = false;
  }
  await loadStrings();
  applyStaticStrings();
  $("auth-send").addEventListener("click", sendLink);
  $("auth-test").addEventListener("click", signInTestGrownUp);
  $("auth-out").addEventListener("click", () => firebaseAuth().signOut());
  $("animal-q").addEventListener("input", renderSuggestions);
  $("cell-make").addEventListener("click", makeHome);
  await finishLink();
  firebaseAuth().onAuthStateChanged(onUser);
}

start().catch((error) => {
  console.error(error);
  showError();
});
