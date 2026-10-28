// Behavioral checks for web/build/app.js, run by tests/test_web.py.
//
// The app is a classic script, so this loads its source, stubs the browser
// (DOM, fetch, Firebase), and drives the real functions: the picker order
// (#87.9), the Saimaa alias (#89.1), the card dates, the Your animals page
// (#92.6) and the ?place=&animal= address (#82.2). Real data files (places
// of interest, suggestions, animals, built places, strings, tables) load
// from web/build.
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
  const el = {
    hidden: true,
    textContent: "",
    value: "",
    disabled: false,
    dataset: {},
    onclick: null,
    lang: "",
    className: "",
    children: [],
    src: "",
    addEventListener() {},
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    setAttribute() {},
    getAttribute() { return ""; },
    querySelector: () => makeEl(),
    querySelectorAll: () => [],
    classList: { add() {}, remove() {}, toggle() {} },
  };
  let html = "";
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    // Like the DOM: setting markup replaces the children.
    set: (value) => {
      html = value;
      el.children = [];
    },
  });
  return el;
}

const elements = new Map();
global.document = {
  getElementById: (id) => {
    if (!elements.has(id)) {
      elements.set(id, makeEl());
    }
    return elements.get(id);
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: () => makeEl(),
  createTextNode: (text) => ({ textContent: text }),
  documentElement: makeEl(),
};

const opened = [];
global.window = {
  location: { search: "", href: "http://localhost/", origin: "http://localhost:8080" },
  open: (url, target) => opened.push([url, target]),
};
global.location = global.window.location;
global.localStorage = { getItem: () => null, setItem() {} };

global.__fetchImpl = async () => ({ json: async () => ({}) });
global.fetch = (url, opts) => global.__fetchImpl(url, opts);

const fakeAuth = {
  onAuthStateChanged() {},
  isSignInWithEmailLink: () => false,
  signOut: async () => {},
  useEmulator() {},
};
const addedDocs = [];
let grownupsCountry = null;
function firestoreFn() {
  return {
    collection: (name) => ({
      add: async (fields) => {
        addedDocs.push([name, fields]);
        return {};
      },
      onSnapshot: () => {},
      where: () => ({ onSnapshot: () => {} }),
      doc: () => ({
        get: async () =>
          grownupsCountry === null
            ? { exists: false }
            : { exists: true, data: () => ({ country: grownupsCountry }) },
        set: async (fields) => {
          grownupsCountry = fields.country;
        },
        update: async () => {},
        delete: async () => {},
        collection: () => ({ orderBy: () => ({ onSnapshot: () => {} }) }),
      }),
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

// --- load the app ----------------------------------------------------------

const src = fs.readFileSync(path.join(WEB, "app.js"), "utf-8");
const bootFetch = async (url) => {
  if (String(url).endsWith("i18n/en.json")) {
    return { json: async () => readJson("i18n/en.json") };
  }
  return { json: async () => ({}) };
};

function primaryActs(html) {
  const withoutFeedback = html.replace(
    /<div class="feedback">.*?<\/div>/gs,
    "",
  );
  return [...withoutFeedback.matchAll(/data-act="([a-z_]+)"/g)].map((m) => m[1]);
}

async function main() {
  global.__fetchImpl = bootFetch;
  eval(src + "\n;globalThis.__app = { state, t, dayLabel, bothExist, draftTitle, draftState, draftActions, playUrl, playPlaceAnimal, suggestFor, builtFor, pickPlace, makeHome, renderAnimals, renderPlaceCards, renderPlaceSearch, pickerCountry, countryFromTimeZone, cardFacts, countryLabel, showView };");
  const app = globalThis.__app;
  // Let start() settle; it only wires listeners with these stubs.
  await new Promise((resolve) => setTimeout(resolve, 50));
  const errBox = elements.get("app-error");
  check("boot shows no error", !errBox || errBox.hidden, true);
  booted = true;

  check("real strings loaded", app.state.strings["draft.in_place"], "{animal} · {place}");

  // Day labels: today, yesterday, 3 days ago.
  const stamp = (daysAgo) => ({
    toDate: () => new Date(Date.now() - daysAgo * 24 * 3600 * 1000),
  });
  check("date today", app.dayLabel(stamp(0)), "today");
  check("date yesterday", app.dayLabel(stamp(1)), "yesterday");
  check("date 3 days ago", app.dayLabel(stamp(3)), "3 days ago");

  // Real data files, as ensurePickerData loads them.
  const interest = readJson("places-of-interest.json").places;
  const animalPlaces = readJson("animal-places.json");
  app.state.animalList = readJson("animal-list.json").animals;
  app.state.places = readJson("places.json").places;
  app.state.interest = interest;
  app.state.byQid = {};
  for (const p of interest) {
    app.state.byQid[p.i] = p;
  }
  app.state.animalPlaces = animalPlaces;
  app.state.tzZones = readJson("tz-country.json").zones;
  app.state.countryNames = readJson("country-names.json").names;
  app.state.aliases = readJson("place-aliases.json").aliases;
  app.state.pickerLoaded = true;
  const fox = app.state.animalList.find((a) => a.id === "fox");
  const moose = app.state.animalList.find((a) => a.id === "moose");
  check("suggestions cover fox and moose", Boolean(animalPlaces.fox && animalPlaces.moose), true);

  // The time zone table (#87.9).
  check("helsinki is FI", app.state.tzZones["Europe/Helsinki"], "FI");
  check("toronto is CA", app.state.tzZones["America/Toronto"], "CA");

  // Cards: the name, the kind in words, the country's name (#87.3).
  check("saimaa card facts", app.cardFacts(app.state.byQid["Q192770"]), "Lake · Finland");
  check("nuuksio card facts", app.cardFacts(app.state.byQid["Q1815268"]), "National park · Finland");
  check("cape crozier shows antarctica", app.countryLabel(app.state.byQid["Q547012"]), "Antarctica");

  // Both exist (#82.2, #89.1): fox in Nuuksio plays, the lake Saimaa
  // with a moose counts as built fi_saimaa.
  const nuuksio = app.state.places.find((p) => p.id === "fi_nuuksio");
  check("fox both-exists", app.bothExist(fox, nuuksio), true);
  const lynx = app.state.animalList.find((a) => a.id === "lynx");
  check("lynx needs a draft", app.bothExist(lynx, nuuksio), false);
  const saimaaBuilt = app.builtFor("Q192770");
  check("saimaa lake aliases fi_saimaa", saimaaBuilt.built && saimaaBuilt.built.id, "fi_saimaa");
  check("saimaa alias is not direct", saimaaBuilt.direct, false);
  check("moose both-exists in saimaa", app.bothExist(moose, saimaaBuilt.built), true);

  // The picker for a fox with FI: Nuuksio first (built), and only
  // places from animal-places.json besides the built ones (#87.9).
  const foxFI = app.suggestFor(fox, "FI");
  check("fox FI first is nuuksio", foxFI[0].built && foxFI[0].built.id, "fi_nuuksio");
  check("fox FI first is ready", foxFI[0].ready, true);
  check("fox FI fits six cards", foxFI.length <= 6, true);
  const foxIds = new Set(animalPlaces.fox.map(([qid]) => qid));
  check(
    "fox FI beyond built is confirmed",
    foxFI.filter((e) => e.seen == null).length,
    1,
  );
  check(
    "fox FI cards are known",
    foxFI.every((e) => e.seen == null || foxIds.has(e.qid)),
    true,
  );

  // The picker for a moose with CA: Banff first (#87.9).
  const mooseCA = app.suggestFor(moose, "CA");
  check("moose CA first is banff", mooseCA[0].qid, "Q41858");
  check("moose CA first is not ready", mooseCA[0].ready, false);

  // Unknown country: no near-you cards, just the best places (#87.4).
  const mooseAny = app.suggestFor(moose, null);
  check("moose anywhere has cards", mooseAny.length > 0, true);

  // The full pick: the lake Saimaa with a moose shows Ready and
  // makes no draft, and Play opens fi_saimaa (#89.1).
  app.state.picked = moose;
  app.pickPlace(app.suggestFor(moose, "FI").find((e) => e.qid === "Q192770"));
  check("ready card shown", elements.get("picked-card").hidden, false);
  check(
    "ready card says go and play",
    elements.get("picked-state").textContent,
    "Ready, go and play",
  );
  check("ready card plays", elements.get("picked-play").hidden, false);
  check("ready card makes nothing", elements.get("picked-make").hidden, true);
  elements.get("picked-play").onclick();
  check(
    "ready play opens the built place",
    opened.length === 1 ? opened[0][0] : "",
    "https://animundo.app/?place=fi_saimaa&animal=moose",
  );

  // A searched place outside the suggestions gets one live GBIF count:
  // a moose search for Serengeti shows no sightings here (#87.9).
  global.__fetchImpl = async (url) => {
    if (String(url).includes("occurrence/search")) {
      return { json: async () => ({ count: 0 }) };
    }
    return { json: async () => ({}) };
  };
  elements.get("place-q").value = "Serengeti";
  app.state.searchCounts = {};
  app.renderPlaceSearch();
  await new Promise((resolve) => setTimeout(resolve, 50));
  const rows = elements.get("place-search").children;
  const serengeti = rows.find((row) =>
    row.children[0].textContent.includes("Serengeti"),
  );
  check("serengeti found", Boolean(serengeti), true);
  check(
    "no moose seen here yet",
    serengeti.children[1].textContent.includes("No moose seen here yet."),
    true,
  );
  check(
    "try offers three places",
    serengeti.children.filter((c) => c.textContent && c.textContent !== serengeti.children[0].textContent).length >= 3,
    true,
  );

  // A live count above the gate builds.
  global.__fetchImpl = async (url) => {
    if (String(url).includes("occurrence/search")) {
      return { json: async () => ({ count: 9 }) };
    }
    return { json: async () => ({}) };
  };
  elements.get("place-q").value = "Serengeti";
  app.state.searchCounts = {};
  app.renderPlaceSearch();
  await new Promise((resolve) => setTimeout(resolve, 50));
  const rows2 = elements.get("place-search").children;
  const serengeti2 = rows2.find((row) =>
    row.children[0].textContent.includes("Serengeti"),
  );
  check(
    "nine sightings are seen here",
    serengeti2.children[1].textContent.includes("Moose are seen here"),
    true,
  );

  // Titles and states (#82.5, #92.2).
  check(
    "title in place",
    app.draftTitle({ kind: "animal_home", species_key: fox.gbif_key, place_name: "Nuuksio National Park" }),
    "Red fox · Nuuksio National Park",
  );
  check(
    "title without place",
    app.draftTitle({ kind: "animal_home", species_key: fox.gbif_key }),
    "Red fox home",
  );
  check("making state", app.draftState({ status: "requested" }), "Making… about 40 min");
  check(
    "ready state",
    app.draftState({ status: "ready", created_at: stamp(1) }),
    "Ready to play · yesterday",
  );
  check("failed state", app.draftState({ status: "failed" }), "Failed, try again");
  check(
    "submitted state",
    app.draftState({ status: "in_review", created_at: stamp(1) }),
    "We're looking at it · yesterday",
  );
  check(
    "published state",
    app.draftState({ status: "public", created_at: stamp(3) }),
    "In the game for everyone · 3 days ago",
  );

  // Your animals: New animal first, the newer draft second (#92.6).
  app.state.feeds = {};
  app.state.drafts = {
    old: { id: "old", kind: "animal_home", species_key: fox.gbif_key, status: "requested", created_at: stamp(3) },
    new: { id: "new", kind: "animal_home", species_key: fox.gbif_key, status: "requested", created_at: stamp(0) },
  };
  app.renderAnimals();
  const cards = elements.get("animal-list").children;
  check("new animal first", cards[0].className.includes("new-animal"), true);
  check("newer draft second", cards[1].dataset.id, "new");
  check("older draft third", cards[2].dataset.id, "old");

  // Each state shows only its buttons (#92.6).
  const played = { status: "ready", round: 1, pack_version: 1, last_played_at: stamp(0) };
  check("ready buttons", app.draftActions(played).includes('data-act="play"'), true);
  check(
    "ready primary buttons",
    JSON.stringify(primaryActs(app.draftActions(played))),
    JSON.stringify(["play", "improve", "submit", "remove"]),
  );
  check(
    "ready without play cannot submit",
    JSON.stringify(primaryActs(app.draftActions({ status: "ready", round: 1, pack_version: 1 }))),
    JSON.stringify(["play", "improve", "remove"]),
  );
  check(
    "improve shows rounds left",
    app.draftActions({ status: "ready", round: 2, pack_version: 1 }).includes("2 of 3 rounds left"),
    true,
  );
  check(
    "making has no buttons",
    JSON.stringify(primaryActs(app.draftActions({ status: "building" }))),
    JSON.stringify([]),
  );
  check(
    "failed buttons",
    JSON.stringify(primaryActs(app.draftActions({ status: "failed" }))),
    JSON.stringify(["try_again", "remove"]),
  );
  check(
    "submitted shows play only",
    JSON.stringify(primaryActs(app.draftActions({ status: "in_review", pack_version: 1 }))),
    JSON.stringify(["play"]),
  );
  check(
    "published shows play only",
    JSON.stringify(primaryActs(app.draftActions({ status: "public", pack_version: 2 }))),
    JSON.stringify(["play"]),
  );

  // Published Play opens the normal place; the rest open the draft (#92.3).
  check(
    "published play opens place and animal",
    app.playUrl({ id: "d1", status: "public", species_key: fox.gbif_key, draft_place_id: "fi_nuuksio" }),
    "https://animundo.app/?place=fi_nuuksio&animal=fox",
  );
  check(
    "ready play opens the draft",
    app.playUrl({ id: "d1", status: "ready", species_key: fox.gbif_key }),
    "https://animundo.app/?draft=d1",
  );

  // In emulator mode Play opens the local game, never production (#98.3).
  global.window.location.search = "?emulator=1";
  check(
    "emulator play opens localhost",
    app.playUrl({ id: "d1", status: "ready", species_key: fox.gbif_key }),
    "http://localhost:8080/?draft=d1&cloud=emulator",
  );
  check(
    "emulator published play opens localhost",
    app.playUrl({ id: "d1", status: "public", species_key: fox.gbif_key, draft_place_id: "fi_nuuksio" }),
    "http://localhost:8080/?place=fi_nuuksio&animal=fox&cloud=emulator",
  );
  const openedBefore = opened.length;
  app.playPlaceAnimal("fi_nuuksio", "fox");
  check(
    "emulator place link opens localhost",
    opened[opened.length - 1][0],
    "http://localhost:8080/?place=fi_nuuksio&animal=fox&cloud=emulator",
  );
  check("emulator opened once", opened.length, openedBefore + 1);
  global.window.location.search = "";

  // A fresh build ends back on Your animals with its card on top (#92.4).
  app.state.picked = moose;
  app.state.view = "new";
  app.state.user = { uid: "grown" };
  app.state.drafts = {};
  const banff = app.state.byQid["Q41858"];
  app.pickPlace({ qid: "Q41858", place: banff, seen: 97, built: null, direct: false, ready: false });
  check("banff card makes", elements.get("picked-make").hidden, false);
  await app.makeHome();
  check("draft created", addedDocs.length, 1);
  const fields = addedDocs[0][1];
  check("draft carries the picked place", fields.place.wikidata_id, "Q41858");
  check("draft place has five keys", Object.keys(fields.place).sort().join(","), "country,fi,kind,name,wikidata_id");
  check("draft names the place", fields.place_name, "Banff National Park");
  check("back on your animals", app.state.view, "animals");
  check(
    "making message",
    elements.get("picked-msg").textContent,
    "Making this home. Watch Your animals.",
  );

  // The same home twice warns instead of duplicating.
  app.state.drafts = {
    d1: { kind: "animal_home", species_key: moose.gbif_key, place: { wikidata_id: "Q41858" } },
  };
  elements.get("picked-make").hidden = false;
  await app.makeHome();
  check("draft still once", addedDocs.length, 1);
  check(
    "duplicate warns",
    elements.get("picked-msg").textContent.includes("already making"),
    true,
  );

  // The saved country wins over the time zone (#87.4).
  grownupsCountry = "CA";
  app.state.pickerCountry = undefined;
  check("saved country wins", await app.pickerCountry(), "CA");
  app.state.tzZones = {};
  check("unknown zone is nothing", app.countryFromTimeZone(), null);
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
