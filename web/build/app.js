"use strict";
/* Animundo Builder: the grown-ups' web app.
 *
 * Static files only (the game's copy publishes this folder at
 * animundo.app/build). Your animals first: one card per draft, and
 * New animal starts the flow — the grown-up picks an animal from
 * a static list, then one of its suggested places of interest (or
 * a searched one), and the worker builds the home. Talks to
 * Firestore and Storage with the signed-in grown-up's own
 * account, so the security rules are what lets each button work:
 * the app never does what the rules forbid, and it never sees
 * another family's drafts.
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

// GBIF's free occurrence search: one count per searched place, live
// in the browser. No map, no tiles since D11.
const GBIF_OCCURRENCES = "https://api.gbif.org/v1/occurrence/search";
const GAME_URL = "https://animundo.app/";
// Only a place with this many wild sightings can be built (#87.3).
const MIN_SIGHTINGS = 5;
// Suggestion cards on the picker step.
const MAX_CARDS = 6;
// Search matches shown; each unconfirmed one costs one GBIF count.
const MAX_SEARCH = 8;

// The list's kinds in words, for the cards (#87.3).
const KIND_WORDS = {
  park: "National park",
  marine_area: "Marine area",
  marine_park: "Marine park",
  protected_area: "Protected area",
  island: "Island",
  lake: "Lake",
  mountain: "Mountain",
  bay: "Bay",
  cape: "Cape",
  reef: "Reef",
  desert: "Desert",
  forest: "Forest",
  wetland: "Wetland",
  glacier: "Glacier",
  valley: "Valley",
  waterfall: "Waterfall",
  volcano: "Volcano",
  peninsula: "Peninsula",
  delta: "Delta",
};

const state = {
  lang: "en",
  strings: {},
  user: null,
  animalList: [],
  animalHomes: [],
  places: [],
  notYet: {},
  view: "animals",
  picked: null,
  pickedHome: null,
  pickerLoaded: false,
  pickerLoading: null,
  interest: [],
  byQid: {},
  animalPlaces: {},
  tzZones: {},
  countryNames: {},
  aliases: {},
  pickerCountry: undefined,
  savedCountry: undefined,
  suggestions: [],
  selectedPlace: null,
  searchCounts: {},
  searchSeq: 0,
  drafts: {},
  draftsSeen: false,
  feedUnsubs: {},
  feedRetry: {},
  feedTries: {},
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

/* -- language: English, with a Finnish judge overlay -------------- */
// English only during the event, except the judge sign-in (#104): a
// Finnish browser loads i18n/fi.json over the English strings, so
// every other string still falls back to English.

async function loadStrings() {
  const response = await fetch("i18n/en.json");
  state.strings = await response.json();
  state.lang = "en";
  document.documentElement.lang = "en";
  try {
    if ((navigator.language || "").toLowerCase().startsWith("fi")) {
      const overlay = await (await fetch("i18n/fi.json")).json();
      state.strings = { ...state.strings, ...overlay };
      state.lang = "fi";
      document.documentElement.lang = "fi";
    }
  } catch (_) {
    /* English it is */
  }
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
  const params = new URLSearchParams();
  if (url.searchParams.has("emulator")) {
    params.set("emulator", "1");
  }
  // A judge who signs out lands back on the judge form (#104.2).
  if (url.searchParams.get("judge") === "1") {
    params.set("judge", "1");
  }
  const query = params.toString();
  return query ? `${clean}?${query}` : clean;
}

function judgeMode() {
  return new URLSearchParams(window.location.search).get("judge") === "1";
}

function applyJudgeMode() {
  $("auth-judge").hidden = !judgeMode();
}

// The judges' way in (#104): a password account from the submission's
// instructions. A failure says only that the pair isn't right — never
// Firebase's reason, and the password is never stored or logged.
async function signInJudge() {
  clearError();
  const box = $("auth-judge-error");
  box.hidden = true;
  const email = $("auth-judge-email").value.trim();
  const password = $("auth-judge-password").value;
  if (!email || !password) {
    return;
  }
  try {
    await firebaseAuth().signInWithEmailAndPassword(email, password);
    $("auth-judge-password").value = "";
  } catch (error) {
    console.error("judge sign-in failed");
    $("auth-judge-password").value = "";
    box.textContent = t("auth.judge_error");
    box.hidden = false;
  }
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

function showView(view) {
  state.view = view;
  $("animals-pane").hidden = view !== "animals";
  $("new-pane").hidden = view !== "new";
}

function onUser(user) {
  state.user = user;
  $("auth-signed-out").hidden = !!user;
  $("auth-signed-in").hidden = !user;
  $("app").hidden = !user;
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
    state.draftsSeen = false;
    state.feeds = {};
    state.picked = null;
    state.pickedHome = null;
    state.suggestions = [];
    state.selectedPlace = null;
    state.searchCounts = {};
    state.savedCountry = undefined;
    state.pickerCountry = undefined;
    showView("animals");
    return;
  }
  $("auth-who").textContent = t("auth.signed_in", { email: user.email || "" });
  showView("animals");
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
    const [animals, places] = await Promise.all([
      fetch("animal-list.json").then((r) => r.json()),
      fetch("places.json").then((r) => r.json()),
    ]);
    state.places = places.places || [];
    state.animalList = animals.animals || [];
    state.animalHomes = animals.homes || [];
    state.notYet = animals.not_yet || {};
    state.dataLoaded = true;
  }
  if (state.draftsUnsub) {
    renderAnimals();
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
        renderAnimals();
        // With no drafts yet, straight to New animal, as the game's
        // Start does (#92.1). Only on the first snapshot: deleting
        // the last draft must not yank the page away.
        if (!state.draftsSeen) {
          state.draftsSeen = true;
          if (Object.keys(state.drafts).length === 0) {
            showView("new");
          }
        }
      },
      (error) => {
        console.error(error);
        showError();
      },
    );
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
  state.suggestions = [];
  state.selectedPlace = null;
  state.searchCounts = {};
  $("picked-card").hidden = true;
  $("place-picker").hidden = true;
  $("animal-suggest").innerHTML = "";
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
  const built = state.animalHomes.includes(home);
  $("animal-msg").textContent = built
    ? t("home.picked", { name: animal.name })
    : t("home.make_home_too");
  $("place-picker").hidden = false;
  void openPicker();
}

/* -- the place picker ----------------------------------------- */

// The big files load only on the picker step (#87.1): the 12,000
// places, the suggestions per animal, the time-zone table, the
// country names and the aliases.
async function ensurePickerData() {
  if (state.pickerLoaded) {
    return;
  }
  if (!state.pickerLoading) {
    state.pickerLoading = (async () => {
      const [interest, animalPlaces, tz, names, aliases] = await Promise.all([
        fetch("places-of-interest.json").then((r) => r.json()),
        fetch("animal-places.json").then((r) => r.json()),
        fetch("tz-country.json").then((r) => r.json()),
        fetch("country-names.json").then((r) => r.json()),
        fetch("place-aliases.json").then((r) => r.json()),
      ]);
      state.interest = interest.places || [];
      state.byQid = {};
      for (const place of state.interest) {
        state.byQid[place.i] = place;
      }
      state.animalPlaces = animalPlaces || {};
      state.tzZones = tz.zones || {};
      state.countryNames = names.names || {};
      state.aliases = aliases.aliases || {};
      state.pickerLoaded = true;
    })();
  }
  return state.pickerLoading;
}

// The country from the time zone, never the location prompt (#87.4).
// The grown-up's saved choice wins over it next time.
function countryFromTimeZone() {
  let zone = "";
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch (_) {
    /* no time zone */
  }
  return state.tzZones[zone] || null;
}

async function pickerCountry() {
  if (state.pickerCountry === undefined) {
    let saved = null;
    try {
      const snap = await db().collection("grownups").doc(state.user.uid).get();
      if (snap.exists) {
        saved = snap.data().country || null;
      }
    } catch (error) {
      console.error(error);
    }
    state.savedCountry = saved;
    state.pickerCountry = saved || countryFromTimeZone();
  }
  return state.pickerCountry;
}

async function openPicker() {
  const animal = state.picked;
  $("place-msg").textContent = t("place.loading");
  $("place-cards").innerHTML = "";
  $("place-search").innerHTML = "";
  try {
    await ensurePickerData();
  } catch (error) {
    console.error(error);
    showError();
    return;
  }
  if (state.picked !== animal) {
    return;
  }
  await pickerCountry();
  if (state.picked !== animal) {
    return;
  }
  renderCountrySelect();
  // renderPlaceCards clears the loading line itself, or says the
  // range is unknown when there is nothing to show.
  renderPlaceCards();
  renderPlaceSearch();
}

function renderCountrySelect() {
  const select = $("place-country");
  select.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = t("place.everywhere");
  select.appendChild(empty);
  const codes = Object.keys(state.countryNames).sort((a, b) =>
    state.countryNames[a].localeCompare(state.countryNames[b])
  );
  for (const code of codes) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = state.countryNames[code];
    select.appendChild(option);
  }
  select.value = state.pickerCountry || "";
}

async function onCountryChange() {
  clearError();
  state.pickerCountry = $("place-country").value || null;
  if (state.pickerCountry) {
    try {
      await db().collection("grownups").doc(state.user.uid).set({
        country: state.pickerCountry,
      });
      state.savedCountry = state.pickerCountry;
    } catch (error) {
      console.error(error);
      showError();
    }
  }
  renderPlaceCards();
}

// The lab's rank: log(sightings + 1) x links.
function placeRank(seen, links) {
  return Math.log(seen + 1) * links;
}

// The built place a list place counts as: a direct Wikidata match,
// or the hand-kept alias (the lake Saimaa counts as fi_saimaa).
function builtFor(qid) {
  const direct = (state.places || []).find((p) => p.wikidata === qid) || null;
  if (direct) {
    return { built: direct, direct: true };
  }
  const aliasId = state.aliases[qid];
  if (aliasId) {
    const built = (state.places || []).find((p) => p.id === aliasId) || null;
    if (built) {
      return { built, direct: false };
    }
  }
  return { built: null, direct: false };
}

// Both exist: the animal is in the library and the place already has it
// playable — no draft, no worker, just Play (#82.2).
function bothExist(animal, place) {
  return Boolean(
    animal && animal.in_library && place &&
    (place.animals.playable || []).includes(animal.id),
  );
}

// Up to 6 cards: ready places in the grown-up's country first
// ("Ready, go and play"), then the 2 best in their country, then
// ready places elsewhere, then the best elsewhere, ranked as the
// lab does (#87.3). Ready stays global in spirit, but a Canadian
// picking a moose sees Banff before Finland's ready Saimaa (#87.9):
// near-you outranks foreign-ready. Direct built matches come
// before alias ones. Country-less places never count as near-you.
function suggestFor(animal, country) {
  const confirmed = state.animalPlaces[animal.id] || [];
  const entries = [];
  const seenBuilt = new Set();
  for (const [qid, seen] of confirmed) {
    const place = state.byQid[qid];
    if (!place) {
      continue;
    }
    const { built, direct } = builtFor(qid);
    const ready = Boolean(built && bothExist(animal, built));
    if (built) {
      seenBuilt.add(built.id);
    }
    entries.push({ qid, place, seen, built, direct, ready });
  }
  // Built places with the animal but no confirmed row (Nuuksio's fox
  // never made the lab's top 40): they still open ready to play.
  for (const built of state.places || []) {
    if (!bothExist(animal, built) || seenBuilt.has(built.id)) {
      continue;
    }
    entries.push({
      qid: built.wikidata,
      place: state.byQid[built.wikidata] || null,
      seen: null,
      built,
      direct: true,
      ready: true,
    });
  }
  const rank = (entry) =>
    entry.seen == null
      ? Infinity
      : placeRank(entry.seen, entry.place ? entry.place.l : 0);
  const byRank = (a, b) => rank(b) - rank(a);
  const byReady = (a, b) => b.direct - a.direct || byRank(a, b);
  const inCountry = (entry) =>
    entry.place && country != null && entry.place.c === country;
  const readyMine = entries.filter((e) => e.ready && inCountry(e)).sort(byReady);
  const readyMineSet = new Set(readyMine);
  const mine = entries
    .filter((e) => !e.ready && inCountry(e))
    .sort(byRank)
    .slice(0, 2);
  const readyElse = entries
    .filter((e) => e.ready && !readyMineSet.has(e))
    .sort(byReady);
  const shown = new Set([...readyMine, ...mine, ...readyElse]);
  const elsewhere = entries.filter((e) => !shown.has(e)).sort(byRank);
  return [...readyMine, ...mine, ...readyElse, ...elsewhere].slice(0, MAX_CARDS);
}

function placeDisplayName(place) {
  if (state.lang === "fi" && place.f) {
    return place.f;
  }
  return place.n;
}

function kindWord(place) {
  return KIND_WORDS[place.k] || "";
}

// Antarctica shows, the open sea shows nothing (#89.2).
function countryLabel(place) {
  if (place.c) {
    return state.countryNames[place.c] || place.c;
  }
  return place.la < -60 ? "Antarctica" : "";
}

function cardFacts(place) {
  const kind = kindWord(place);
  const country = countryLabel(place);
  if (kind && country) {
    return `${kind} · ${country}`;
  }
  return kind || country;
}

function renderPlaceCards() {
  const box = $("place-cards");
  box.innerHTML = "";
  if (!state.picked) {
    return;
  }
  const animal = state.picked;
  state.suggestions = suggestFor(animal, state.pickerCountry || null);
  if (!state.suggestions.length) {
    $("place-msg").textContent = t("home.no_records", { name: animal.name });
    return;
  }
  $("place-msg").textContent = "";
  for (const entry of state.suggestions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    const name = entry.place
      ? placeDisplayName(entry.place)
      : entry.built.name.en;
    const facts = entry.place ? cardFacts(entry.place) : "";
    const ready = entry.ready ? ` <small>${escapeHtml(t("place.ready_go"))}</small>` : "";
    button.innerHTML = `${escapeHtml(name)} <small>${escapeHtml(facts)}</small>${ready}`;
    button.addEventListener("click", () => pickPlace(entry));
    box.appendChild(button);
  }
}

// The lab's circle: 10 km without an area, clamped to 5–50 km.
function radiusKm(area) {
  if (!area || area <= 0) {
    return 10;
  }
  return Math.max(5, Math.min(50, Math.sqrt(area / Math.PI)));
}

// One GBIF count from the browser, for a searched place outside the
// suggestions (#87.3). Cached: typing must not recount.
async function liveCount(gbifKey, place) {
  const params = new URLSearchParams({
    taxon_key: String(gbifKey),
    limit: "0",
    year: "1996,2026",
    hasCoordinate: "true",
    occurrenceStatus: "PRESENT",
    geoDistance: `${place.la},${place.lo},${Math.round(radiusKm(place.a))}km`,
  });
  params.append("basisOfRecord", "HUMAN_OBSERVATION");
  params.append("basisOfRecord", "MACHINE_OBSERVATION");
  const data = await (await fetch(`${GBIF_OCCURRENCES}?${params}`)).json();
  return data.count || 0;
}

function topPlaces(animalId, count) {
  const confirmed = state.animalPlaces[animalId] || [];
  return confirmed
    .map(([qid, seen]) => ({ place: state.byQid[qid], seen }))
    .filter((row) => row.place)
    .sort((a, b) => placeRank(b.seen, b.place.l) - placeRank(a.seen, a.place.l))
    .slice(0, count);
}

function entryFor(animal, qid, seen) {
  const { built, direct } = builtFor(qid);
  return {
    qid,
    place: state.byQid[qid],
    seen,
    built,
    direct,
    ready: Boolean(built && bothExist(animal, built)),
  };
}

function renderPlaceSearch() {
  const box = $("place-search");
  box.innerHTML = "";
  const query = $("place-q").value.trim().toLowerCase();
  if (!query || !state.picked || !state.pickerLoaded) {
    return;
  }
  const seq = ++state.searchSeq;
  const animal = state.picked;
  const matches = state.interest
    .filter(
      (p) =>
        p.n.toLowerCase().includes(query) ||
        (p.f || "").toLowerCase().includes(query),
    )
    .slice(0, MAX_SEARCH);
  const confirmed = new Map(state.animalPlaces[animal.id] || []);
  for (const place of matches) {
    const row = document.createElement("p");
    const name = document.createElement("strong");
    name.textContent = `${placeDisplayName(place)} — ${cardFacts(place)}`;
    row.appendChild(name);
    const status = document.createElement("span");
    status.textContent = " …";
    row.appendChild(status);
    box.appendChild(row);
    if (confirmed.has(place.i)) {
      status.textContent = ` ${t("place.seen_here", { name: animal.name })}`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = entryFor(animal, place.i, confirmed.get(place.i)).ready
        ? t("place.ready_go")
        : t("home.make");
      button.addEventListener("click", () =>
        pickPlace(entryFor(animal, place.i, confirmed.get(place.i)))
      );
      row.appendChild(document.createTextNode(" "));
      row.appendChild(button);
    } else {
      void fillSearchCount(seq, animal, place, row, status);
    }
  }
}

async function fillSearchCount(seq, animal, place, row, status) {
  if (state.searchCounts[place.i] === undefined) {
    try {
      state.searchCounts[place.i] = await liveCount(animal.gbif_key, place);
    } catch (error) {
      console.error(error);
      if (seq !== state.searchSeq || state.picked !== animal) {
        return;
      }
      status.textContent = "";
      return;
    }
  }
  if (seq !== state.searchSeq || state.picked !== animal) {
    return;
  }
  const seen = state.searchCounts[place.i];
  if (seen >= MIN_SIGHTINGS) {
    status.textContent = ` ${t("place.seen_here", { name: animal.name })}`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.textContent = entryFor(animal, place.i, seen).ready
      ? t("place.ready_go")
      : t("home.make");
    button.addEventListener("click", () => pickPlace(entryFor(animal, place.i, seen)));
    row.appendChild(document.createTextNode(" "));
    row.appendChild(button);
    return;
  }
  status.textContent = ` ${t("place.none_seen", { name: animal.name.toLowerCase() })}`;
  const best = topPlaces(animal.id, 3);
  if (best.length) {
    row.appendChild(document.createTextNode(` ${t("place.try")}`));
    for (const { place: top, seen: topSeen } of best) {
      row.appendChild(document.createTextNode(" "));
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = placeDisplayName(top);
      button.addEventListener("click", () =>
        pickPlace(entryFor(animal, top.i, topSeen))
      );
      row.appendChild(button);
    }
  }
}

function pickPlace(entry) {
  if (!state.picked) {
    return;
  }
  state.selectedPlace = { ...entry, animal: state.picked };
  const card = $("picked-card");
  card.hidden = false;
  $("picked-make").hidden = true;
  $("picked-play").hidden = true;
  $("picked-msg").textContent = "";
  if (entry.place) {
    $("picked-name").textContent = placeDisplayName(entry.place);
    $("picked-facts").textContent = cardFacts(entry.place);
  } else {
    $("picked-name").textContent = entry.built.name.en;
    $("picked-facts").textContent = "";
  }
  if (entry.ready) {
    $("picked-state").textContent = t("place.ready_go");
    $("picked-play").hidden = false;
    $("picked-play").onclick = () =>
      playPlaceAnimal(entry.built.id, state.picked.id);
  } else {
    $("picked-state").textContent = t("place.seen_here", {
      name: state.picked.name,
    });
    $("picked-make").hidden = false;
  }
}

// In emulator mode Play opens the local game, never production (#98.3).
function gameBase() {
  // location.origin has no trailing slash; GAME_URL has one.
  return emulatorMode() ? `${location.origin}/` : GAME_URL;
}

function cloudSuffix() {
  return emulatorMode() ? "&cloud=emulator" : "";
}

// Both exist: straight into the game, no draft (#82.2, game c685a04).
function playPlaceAnimal(placeId, animalId) {
  const url = `${gameBase()}?place=${encodeURIComponent(placeId)}` +
    `&animal=${encodeURIComponent(animalId)}${cloudSuffix()}`;
  window.open(url, "_blank");
}

function homeForPlace(speciesKey, qid) {
  return Object.values(state.drafts).find((d) =>
    d.kind === "animal_home" && d.species_key === speciesKey &&
    d.place && d.place.wikidata_id === qid
  ) || null;
}

async function makeHome() {
  clearError();
  const selected = state.selectedPlace;
  if (!selected || !state.picked || !selected.place) {
    return;
  }
  if (homeForPlace(state.picked.gbif_key, selected.qid)) {
    $("picked-msg").textContent = t("home.exists");
    return;
  }
  const place = selected.place;
  const fields = {
    kind: "animal_home",
    species_key: state.picked.gbif_key,
    place: {
      wikidata_id: selected.qid,
      name: place.n,
      fi: place.f || null,
      kind: place.k,
      country: place.c || null,
    },
    owner_uid: state.user.uid,
    family_uids: [state.user.uid],
    status: "requested",
    created_at: firebase.firestore.FieldValue.serverTimestamp(),
    // The rules bound the card title to 80 characters (#97); the
    // worker titles from its own list anyway.
    place_name: place.n.slice(0, 80),
  };
  if (selected.built && !selected.ready) {
    fields.place_id = selected.built.id;
  }
  try {
    await db().collection("drafts").add(fields);
    $("picked-msg").textContent = t("home.started");
    $("picked-make").hidden = true;
    // A fresh build ends back on Your animals with its card on top (#92.4).
    showView("animals");
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

function sortedDrafts() {
  return Object.values(state.drafts).sort(
    (a, b) =>
      stampMillis(b.updated_at || b.created_at) -
      stampMillis(a.updated_at || a.created_at),
  );
}

function renderAnimals() {
  const list = $("animal-list");
  list.innerHTML = "";
  // New animal first, then one card per draft, newest first (#92.1).
  const fresh = document.createElement("article");
  fresh.className = "draft new-animal";
  fresh.innerHTML = `<button type="button" data-new>${
    escapeHtml(t("animals.new"))
  }</button>`;
  fresh.querySelector("[data-new]").addEventListener("click", () => showView("new"));
  list.appendChild(fresh);
  for (const draft of sortedDrafts()) {
    list.appendChild(renderDraft(draft));
    // The feed rule reads the draft, so a locally-written draft starts
    // no listener until the server has it.
    if (draft._synced) {
      watchFeed(draft);
    }
    renderFeed(draft.id);
    fillPicture(draft);
  }
}

// The draft's preview render when it has one, else nothing (#92.2).
function fillPicture(draft) {
  const box = document.querySelector(`[data-pic="${draft.id}"]`);
  if (!box || box.dataset.done) {
    return;
  }
  const image = draftPreview(draft);
  if (!image) {
    return;
  }
  box.dataset.done = "1";
  storage().ref(image).getDownloadURL().then(
    (url) => {
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.src = url;
      box.appendChild(img);
    },
    (error) => {
      console.error(error);
    },
  );
}

function draftPreview(draft) {
  for (const entry of (state.feeds || {})[draft.id] || []) {
    if (entry.kind === "preview" && entry.image) {
      return entry.image;
    }
  }
  return null;
}

function draftTitle(draft) {
  if (draft.kind === "animal_home") {
    const name = animalName(draft.species_key);
    if (name && draft.place_name) {
      return t("draft.in_place", { animal: name, place: draft.place_name });
    }
    if (name) {
      return t("home.draft_title", { animal: name });
    }
    return t("home.untitled");
  }
  if (draft.place_name) {
    return draft.place_name;
  }
  return draft.area_id || draft.id;
}

// Today, yesterday, or N days ago, in local calendar days (#82.5).
function dayLabel(stamp) {
  const at = stampMillis(stamp);
  if (!at) {
    return "";
  }
  const day = 24 * 60 * 60 * 1000;
  const startOf = (time) => {
    const date = new Date(time);
    date.setHours(0, 0, 0, 0);
    return date.getTime();
  };
  const days = Math.round((startOf(Date.now()) - startOf(at)) / day);
  if (days <= 0) {
    return t("draft.today");
  }
  if (days === 1) {
    return t("draft.yesterday");
  }
  return t("draft.days_ago", { n: days });
}

function draftState(draft) {
  const status = draft.status;
  if (status === "failed") {
    return t("draft.failed_retry");
  }
  if (status === "ready") {
    return `${t("draft.ready_to_play")} · ${dayLabel(draft.created_at)}`;
  }
  if (["requested", "queued", "building", "improving"].includes(status)) {
    return t("draft.making");
  }
  const words = state.strings[`draft.status.${status}`] || t("draft.status.failed");
  return `${words} · ${dayLabel(draft.created_at)}`;
}

function renderDraft(draft) {
  const card = document.createElement("article");
  card.className = "draft";
  card.dataset.id = draft.id;
  let html = `<div class="pic" data-pic="${escapeHtml(draft.id)}"></div>`;
  html += `<h3>${escapeHtml(draftTitle(draft))}</h3>`;
  html += `<p class="muted">${escapeHtml(draftState(draft))}</p>`;
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
  // Ready: Play, Improve, Submit, Delete. Making: none. Failed: Try
  // again, Delete. Submitted: Play only. Published: Play (#92.3).
  const packed = (draft.pack_version || 0) >= 1;
  if (packed && ["ready", "in_review", "public", "not_accepted"].includes(status)) {
    html += `<button type="button" data-act="play">${
      escapeHtml(t("draft.play"))
    }</button>`;
  }
  if (status === "failed") {
    html += `<p>${escapeHtml(t("draft.failed_words"))}</p>` +
      `<button type="button" data-act="try_again">${escapeHtml(t("draft.try_again"))}</button>`;
  }
  const canImprove = status === "ready" && (draft.round || 1) < 4;
  const needsPerson = status === "ready" && (draft.round || 1) >= 4;
  if (canImprove) {
    html += `<div class="improve"><h4>${escapeHtml(
      t("draft.improve_left", { n: 4 - (draft.round || 1) })
    )}</h4>` +
      ["animal_looks_wrong", "too_few_animals", "colours_off", "other"].map((choice) =>
        `<label><input type="checkbox" name="choice" value="${choice}"> ` +
        `${escapeHtml(t(`choice.${choice}`))}</label>`
      ).join("") +
      `<input type="text" name="species" maxlength="64" placeholder="${
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
  if (status === "ready" || status === "failed") {
    html += `<button type="button" data-act="remove">${escapeHtml(t("draft.remove"))}</button>`;
  }
  if (["ready", "not_accepted"].includes(status)) {
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

// Published drafts open as the normal place; everything else opens
// the draft for this session only (#92.3, #92.5).
function playUrl(draft) {
  if (draft.status === "public" && draft.draft_place_id) {
    const animal = state.animalList.find((a) => a.gbif_key === draft.species_key);
    if (animal) {
      return `${gameBase()}?place=${encodeURIComponent(draft.draft_place_id)}` +
        `&animal=${encodeURIComponent(animal.id)}${cloudSuffix()}`;
    }
  }
  return `${gameBase()}?draft=${encodeURIComponent(draft.id)}${cloudSuffix()}`;
}

async function actOnDraft(draft, button) {
  clearError();
  const action = button.getAttribute("data-act");
  const card = button.closest("[data-actions]");
  const stamp = firebase.firestore.FieldValue.serverTimestamp();
  try {
    if (action === "play") {
      window.open(playUrl(draft), "_blank");
    } else if (action === "improve") {
      const choices = [...card.querySelectorAll('input[name="choice"]:checked')]
        .map((box) => {
          const entry = { choice: box.value };
          if (box.value === "animal_looks_wrong" || box.value === "other") {
            const species = card.querySelector('input[name="species"]').value.trim();
            if (box.value === "animal_looks_wrong" && !species) {
              draftMessage(draft.id, t("choice.species_ph"));
              throw new Error("species");
            }
            // "Something else" routes by the animal too, when one is
            // named (#82.6).
            if (species) {
              entry.species = species;
            }
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
  fillPicture({ id: draftId });
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
  applyJudgeMode();
  $("auth-send").addEventListener("click", sendLink);
  $("auth-test").addEventListener("click", signInTestGrownUp);
  $("auth-judge-signin").addEventListener("click", signInJudge);
  $("auth-out").addEventListener("click", () => firebaseAuth().signOut());
  $("animal-q").addEventListener("input", renderSuggestions);
  $("back-animals").addEventListener("click", () => showView("animals"));
  $("place-country").addEventListener("change", onCountryChange);
  $("place-q").addEventListener("input", renderPlaceSearch);
  $("picked-make").addEventListener("click", makeHome);
  await finishLink();
  firebaseAuth().onAuthStateChanged(onUser);
}

start().catch((error) => {
  console.error(error);
  showError();
});
