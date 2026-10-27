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
// is from GBIF's maps API docs), over the Natural Earth map.
const GBIF_TILES =
  "https://api.gbif.org/v2/map/occurrence/density/{z}/{x}/{y}@1x.png" +
  "?taxonKey={key}&style=orange.marker";
const GBIF_OCCURRENCES = "https://api.gbif.org/v1/occurrence/search";

const state = {
  lang: "en",
  strings: {},
  user: null,
  animalList: [],
  animalHomes: [],
  notYet: {},
  picked: null,
  pickedHome: null,
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
    state.selectedCell = null;
    setGbifLayer(null);
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
    initMap();
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
  // Natural Earth land, vendored: no tile service, no terms, no limits.
  fetch("vendor/natural-earth/ne_110m_land.geojson")
    .then((response) => response.json())
    .then((land) => {
      L.geoJSON(land, {
        style: { color: "#3b5544", weight: 1, fillColor: "#2c3a32", fillOpacity: 1 },
        attribution: "Natural Earth",
      }).addTo(state.map);
    })
    .catch((error) => console.error(error));
  fetch("vendor/natural-earth/ne_110m_coastline.geojson")
    .then((response) => response.json())
    .then((coast) => {
      L.geoJSON(coast, {
        style: { color: "#c9c2ad", weight: 1, opacity: 0.4 },
      }).addTo(state.map);
    })
    .catch((error) => console.error(error));
  state.map.on("click", (event) => onMapTap(event.latlng));
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
  }).addTo(state.map);
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
  state.selectedCell = null;
  $("cell-card").hidden = true;
  $("animal-suggest").innerHTML = "";
  setGbifLayer(null);
  if (!animal.can_make) {
    $("animal-msg").textContent =
      state.notYet[animal.not_yet] || t("home.cannot", { name: animal.name });
    return;
  }
  // The worker resolves the same home the same way: the animal's
  // first habitat with a built home.
  const home = (animal.habitats || []).find((h) => state.animalHomes.includes(h));
  if (!home) {
    $("animal-msg").textContent = t("home.no_home");
    return;
  }
  state.picked = animal;
  state.pickedHome = home;
  setGbifLayer(animal.gbif_key);
  $("animal-msg").textContent = t("home.picked", { name: animal.name });
}

/* -- the spot ------------------------------------------------ */

function snapCell(lat, lng) {
  const south = Math.floor(lat * 10) / 10;
  const west = Math.floor(lng * 10) / 10;
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

async function occurrencesInCell(gbifKey, cell) {
  const box = `POLYGON((${cell.west} ${cell.south}, ${cell.west + 0.1} ${cell.south}, ` +
    `${cell.west + 0.1} ${cell.south + 0.1}, ${cell.west} ${cell.south + 0.1}, ` +
    `${cell.west} ${cell.south}))`;
  const url = `${GBIF_OCCURRENCES}?taxon_key=${gbifKey}` +
    `&geometry=${encodeURIComponent(box)}&limit=0`;
  const response = await fetch(url);
  const data = await response.json();
  return (data.count || 0) > 0;
}

async function onMapTap(latlng) {
  clearError();
  if (!state.picked) {
    $("animal-msg").textContent = t("home.pick_first");
    return;
  }
  const animal = state.picked;
  const homeWords = state.pickedHome.replace(/_/g, " ");
  const cell = snapCell(latlng.lat, latlng.lng);
  const check = ++state.cellCheck;
  const card = $("cell-card");
  card.hidden = false;
  $("cell-make").hidden = true;
  $("cell-msg").textContent = "";
  const country = countryAt(latlng.lat, latlng.lng);
  if (!country || !country.name || urbanAt(latlng.lat, latlng.lng)) {
    $("cell-name").textContent = "";
    $("cell-facts").textContent = "";
    $("cell-state").textContent = t(country ? "home.in_city" : "home.outside", {
      name: animal.name,
    });
    state.selectedCell = null;
    return;
  }
  const title = `${animal.name} ${homeWords}, ${country.name}`;
  $("cell-name").textContent = title;
  $("cell-facts").textContent = `${animal.latin} · ${cell.id}`;
  $("cell-state").textContent = t("home.checking");
  let recorded = false;
  try {
    recorded = await occurrencesInCell(animal.gbif_key, cell);
  } catch (error) {
    console.error(error);
    if (check !== state.cellCheck) {
      return;
    }
    $("cell-state").textContent = t("home.check_failed");
    state.selectedCell = null;
    return;
  }
  if (check !== state.cellCheck) {
    return;
  }
  if (!recorded) {
    $("cell-state").textContent = t("home.outside", { name: animal.name });
    state.selectedCell = null;
    return;
  }
  state.selectedCell = { ...cell, title, country, animal };
  $("cell-state").textContent = t("home.about");
  $("cell-make").hidden = false;
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
  const fields = {
    kind: "animal_home",
    species_key: state.picked.gbif_key,
    cell: selected.id,
    place_name: selected.title,
    owner_uid: state.user.uid,
    family_uids: [state.user.uid],
    status: "requested",
    created_at: firebase.firestore.FieldValue.serverTimestamp(),
  };
  if (selected.country.iso) {
    fields.country = selected.country.iso;
  }
  fields.country_name = selected.country.name;
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
  if (!drafts.length) {
    list.innerHTML = `<p class="muted">${escapeHtml(t("drafts.none"))}</p>`;
    return;
  }
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
    if (action === "improve") {
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
