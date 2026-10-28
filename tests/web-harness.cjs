// Behavioral checks for web/build/app.js, run by tests/test_web.py.
//
// The app is a classic script, so this loads its source, stubs the browser
// (DOM, fetch, Firebase, Leaflet), and drives the real functions: the card
// dates (#82.7), the 25 km place match (#82.1), the Ready card that makes no
// draft (#82.7), and the ?place=&animal= address (#82.2). Real data files
// (places, animals, strings, country shapes) load from web/build.
"use strict";

const fs = require("fs");
const path = require("path");

const WEB = path.join(__dirname, "..", "web", "build");
const failures = [];
let booted = false;

function check(name, actual, expected) {
  if (actual === expected) {
    console.log(`ok ${name}`);
  } else {
    failures.push(name);
    console.error(`FAIL ${name}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`);
  }
}

function readJson(rel) {
  return JSON.parse(fs.readFileSync(path.join(WEB, rel), "utf-8"));
}

// --- stubs ---------------------------------------------------------------

function makeEl() {
  return {
    hidden: true,
    textContent: "",
    innerHTML: "",
    value: "",
    disabled: false,
    dataset: {},
    onclick: null,
    lang: "",
    addEventListener() {},
    appendChild() {},
    setAttribute() {},
    getAttribute() { return ""; },
    querySelectorAll: () => [],
    classList: { add() {}, remove() {}, toggle() {} },
  };
}

const elements = new Map();
global.document = {
  getElementById: (id) => {
    if (!elements.has(id)) {
      elements.set(id, makeEl());
    }
    return elements.get(id);
  },
  querySelectorAll: () => [],
  createElement: () => makeEl(),
  documentElement: makeEl(),
};

const opened = [];
global.window = {
  location: { search: "", href: "http://localhost/" },
  open: (url, target) => opened.push([url, target]),
};
global.localStorage = { getItem: () => null, setItem() {} };

global.__fetchImpl = async () => ({ json: async () => ({}) });
global.fetch = (url, opts) => global.__fetchImpl(url, opts);

const fakeAuth = {
  onAuthStateChanged() {},
  isSignInWithEmailLink: () => false,
  signOut: async () => {},
  useEmulator() {},
};
function firestoreFn() {
  return {
    collection: () => ({
      add: async () => ({}),
      onSnapshot: () => {},
      where: () => ({ onSnapshot: () => {} }),
    }),
  };
}
firestoreFn.FieldValue = { serverTimestamp: () => ({}), delete: () => ({}) };
global.firebase = {
  initializeApp() {},
  auth: () => fakeAuth,
  firestore: firestoreFn,
  storage: () => ({ ref: () => ({}) }),
};
const layerStub = { addTo: () => {} };
global.L = {
  map: () => layerStub,
  tileLayer: () => layerStub,
  geoJSON: () => layerStub,
  layerGroup: () => layerStub,
  rectangle: () => ({ addTo: () => ({}) }),
};

// --- load the app ----------------------------------------------------------

const src = fs.readFileSync(path.join(WEB, "app.js"), "utf-8");
const bootFetch = async (url) => {
  if (String(url).endsWith("i18n/en.json")) {
    return { json: async () => readJson("i18n/en.json") };
  }
  return { json: async () => ({}) };
};

async function main() {
  global.__fetchImpl = bootFetch;
  eval(src + "\n;globalThis.__app = { state, t, dayLabel, placeAt, bothExist, draftTitle, draftState, playPlaceAnimal, onMapTap };");
  const app = globalThis.__app;
  // Let start() settle; it only wires listeners with these stubs.
  await new Promise((resolve) => setTimeout(resolve, 50));
  const errBox = elements.get("app-error");
  check("boot shows no error", !errBox || errBox.hidden, true);
  booted = true;

  const stringsOk = app.state.strings["draft.in_place"] === "{animal} in {place}";
  check("real strings loaded", stringsOk, true);

  // Day labels (#82.7): today, yesterday, 3 days ago.
  const stamp = (daysAgo) => ({
    toDate: () => new Date(Date.now() - daysAgo * 24 * 3600 * 1000),
  });
  check("date today", app.dayLabel(stamp(0)), "today");
  check("date yesterday", app.dayLabel(stamp(1)), "yesterday");
  check("date 3 days ago", app.dayLabel(stamp(3)), "3 days ago");

  // 25 km places (#82.1), against the synced file.
  app.state.places = readJson("places.json").places;
  check("synced places", app.state.places.map((p) => p.id).join(","), "fi_nuuksio,fi_saimaa");
  const nuuksio = app.placeAt(60.3007, 24.5263);
  check("nuuksio centre matches", nuuksio && nuuksio.id, "fi_nuuksio");
  check("ocean matches nothing", app.placeAt(0, 0), null);
  check("33 km north matches nothing", app.placeAt(60.6007, 24.5263), null);
  check("saimaa centre matches", app.placeAt(62.115, 28.508).id, "fi_saimaa");

  // Titles and states (#82.5).
  app.state.animalList = readJson("animal-list.json").animals;
  const fox = app.state.animalList.find((a) => a.id === "fox");
  const lynx = app.state.animalList.find((a) => a.id === "lynx");
  check(
    "title in place",
    app.draftTitle({ kind: "animal_home", species_key: fox.gbif_key, place_name: "Nuuksio National Park" }),
    "Red fox in Nuuksio National Park",
  );
  check(
    "title without place",
    app.draftTitle({ kind: "animal_home", species_key: fox.gbif_key }),
    "Red fox home",
  );
  check("making state", app.draftState({ status: "requested" }), "making… about 40 min");
  check(
    "ready state",
    app.draftState({ status: "ready", created_at: stamp(1) }),
    "Ready to play · yesterday",
  );
  check("failed state", app.draftState({ status: "failed" }), "Failed, try again");

  // Both exist (#82.2): fox in Nuuksio plays, lynx makes.
  check("fox both-exists", app.bothExist(fox, nuuksio), true);
  check("lynx needs a draft", app.bothExist(lynx, nuuksio), false);

  // The full tap: a fox tap near Nuuksio shows Ready and makes no draft (#82.7).
  app.state.adminShapes = readJson("vendor/natural-earth/ne_110m_admin_0_countries.geojson").features;
  app.state.urbanShapes = [];
  app.state.map = { removeLayer() {} };
  global.__fetchImpl = async (url) => {
    if (String(url).includes("occurrence/search")) {
      return { json: async () => ({ count: 12 }) };
    }
    return { json: async () => ({}) };
  };
  app.state.picked = fox;
  app.state.pickedShort = "Fox";
  app.state.pickedHabitat = "forest";
  await app.onMapTap({ lat: 60.35, lng: 24.55 });
  check("ready card shown", elements.get("cell-card").hidden, false);
  check(
    "ready card names the place",
    elements.get("cell-name").textContent,
    "Ready: Nuuksio National Park",
  );
  check("ready card plays", elements.get("cell-play").hidden, false);
  check("ready card makes nothing", elements.get("cell-make").hidden, true);
  elements.get("cell-play").onclick();
  check(
    "play opens place and animal",
    opened.length === 1 ? opened[0][0] : "",
    "https://animundo.app/?place=fi_nuuksio&animal=fox",
  );

  // A lynx tap in the same spot makes a draft carrying the place (#82.3 wiring).
  app.state.picked = lynx;
  app.state.pickedShort = "Lynx";
  app.state.pickedHabitat = "forest";
  await app.onMapTap({ lat: 60.35, lng: 24.55 });
  check("lynx card makes", elements.get("cell-make").hidden, false);
  check("lynx card plays nothing", elements.get("cell-play").hidden, true);
  check("lynx tap keeps the place", app.state.selectedCell.place.id, "fi_nuuksio");
}

main().then(() => {
  if (!booted || failures.length > 0) {
    process.exit(1);
  }
  console.log(`harness: all checks passed`);
}).catch((error) => {
  console.error(`harness crashed: ${error && error.stack || error}`);
  process.exit(1);
});
