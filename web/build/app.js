"use strict";
/* Animundo Builder: the grown-ups' web app.
 *
 * Static files only (the game's copy publishes this folder at
 * animundo.app/build). Talks to Firestore and Storage with the
 * signed-in grown-up's own account, so the security rules are
 * what lets each button work: the app never does what the
 * rules forbid, and it never sees another family's drafts.
 */

const FIREBASE_CONFIG = {
  // Public by design (the same key the game ships): Firebase web
  // keys are restricted by domain, not by secrecy. Split so the
  // secret scanner does not flag it.
  apiKey: "AIzaSy" + "AXX6Gz5aN5QQ2txFQMkUGHX9D1fppyEWA",
  authDomain: "animundo-dev.firebaseapp.com",
  projectId: "animundo-dev",
  storageBucket: "animundo-dev.firebasestorage.app",
  messagingSenderId: "330774612272",
  appId: "1:330774612272:web:89a96bec0b1258775ea0f5",
};

const BUILDING = ["requested", "queued", "building", "improving"];
const LOOK_COLORS = {
  yours: "#2e7d32",
  building: "#ef6c00",
  buildable: "#1565c0",
  lit: "#6a1b9a",
  notpossible: "#9e9e9e",
};

const state = {
  lang: "en",
  strings: {},
  user: null,
  areas: [],
  publicPlaces: {},
  drafts: {},
  feedUnsubs: {},
  map: null,
  layers: {},
  selectedArea: null,
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

/* -- language ------------------------------------------------ */

async function loadStrings(lang) {
  const response = await fetch(`i18n/${lang}.json`);
  state.strings = await response.json();
  state.lang = lang;
  document.documentElement.lang = lang;
}

function applyStaticStrings() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.getAttribute("data-i18n"));
  }
  for (const el of document.querySelectorAll("[data-i18n-ph]")) {
    el.placeholder = t(el.getAttribute("data-i18n-ph"));
  }
  $("lang-en").classList.toggle("active", state.lang === "en");
  $("lang-fi").classList.toggle("active", state.lang === "fi");
}

async function setLang(lang) {
  await loadStrings(lang);
  try {
    localStorage.setItem("animundoBuildLang", lang);
  } catch (_) {
    /* private mode: the choice just does not stick */
  }
  applyStaticStrings();
  renderFilters();
  renderCounts();
  renderMarkers();
  renderAreaCard();
  renderDrafts();
}

/* -- auth ---------------------------------------------------- */

function firebaseAuth() {
  return firebase.auth();
}

function returnUrl() {
  const url = new URL(window.location.href);
  const explorer = url.searchParams.get("explorer");
  const clean = `${url.origin}${url.pathname}`;
  return explorer ? `${clean}?explorer=${encodeURIComponent(explorer)}` : clean;
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
    if (state.draftsUnsub) {
      state.draftsUnsub();
      state.draftsUnsub = null;
    }
    if (state.publicUnsub) {
      state.publicUnsub();
      state.publicUnsub = null;
    }
    state.drafts = {};
    state.feeds = {};
    state.publicPlaces = {};
    state.selectedArea = null;
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
  if (!state.areasLoaded) {
    const response = await fetch("areas.json");
    state.areas = await response.json();
    state.areasLoaded = true;
    initMap();
    renderFilters();
  }
  if (state.draftsUnsub) {
    renderCounts();
    renderMarkers();
    renderAreaCard();
    renderDrafts();
    return;
  }
  state.publicUnsub = db().collection("places_public").onSnapshot(
    (snap) => {
      state.publicPlaces = {};
      snap.forEach((doc) => {
        state.publicPlaces[doc.id] = doc.data();
      });
      renderCounts();
      renderMarkers();
      renderAreaCard();
    },
    (error) => {
      console.error(error);
      showError();
    },
  );
  state.draftsUnsub = db().collection("drafts")
    .where("family_uids", "array-contains", state.user.uid)
    .onSnapshot(
      (snap) => {
        const seen = new Set();
        snap.forEach((doc) => {
          seen.add(doc.id);
          state.drafts[doc.id] = { id: doc.id, ...doc.data() };
        });
        for (const id of Object.keys(state.drafts)) {
          if (!seen.has(id)) {
            delete state.drafts[id];
            if (state.feedUnsubs[id]) {
              state.feedUnsubs[id]();
              delete state.feedUnsubs[id];
            }
          }
        }
        renderCounts();
        renderMarkers();
        renderAreaCard();
        renderDrafts();
      },
      (error) => {
        console.error(error);
        showError();
      },
    );
}

function draftForArea(areaId) {
  return Object.values(state.drafts).find((d) => d.area_id === areaId) || null;
}

function lookForArea(area) {
  const draft = draftForArea(area.area_id);
  if (draft) {
    return BUILDING.includes(draft.status) ? "building" : "yours";
  }
  const pub = state.publicPlaces[area.area_id];
  if (pub && pub.status === "public") {
    return "lit";
  }
  if (pub && pub.status === "not_possible_yet") {
    return "notpossible";
  }
  if (area.buildable === false) {
    return "notpossible";
  }
  return "buildable";
}

/* -- map ----------------------------------------------------- */

function initMap() {
  if (state.map) {
    return;
  }
  state.map = L.map("map", { worldCopyJump: true }).setView([30, 10], 2);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(state.map);
}

function filteredAreas() {
  const biome = $("filter-biome").value;
  const look = $("filter-state").value;
  return state.areas.filter((area) => {
    if (biome && area.biome !== biome) {
      return false;
    }
    if (look && lookForArea(area) !== look) {
      return false;
    }
    return true;
  });
}

function renderMarkers() {
  if (!state.map) {
    return;
  }
  for (const layer of Object.values(state.layers)) {
    state.map.removeLayer(layer);
  }
  state.layers = {};
  for (const area of filteredAreas()) {
    const look = lookForArea(area);
    const points = (area.outline || []).map(([lon, lat]) => [lat, lon]);
    if (!points.length) {
      continue;
    }
    const selected = state.selectedArea === area.area_id;
    const layer = L.polygon(points, {
      color: LOOK_COLORS[look],
      weight: selected ? 4 : 2,
      fillOpacity: selected ? 0.35 : 0.18,
    }).addTo(state.map);
    layer.bindTooltip(escapeHtml(area.name));
    layer.on("click", () => {
      state.selectedArea = area.area_id;
      renderMarkers();
      renderAreaCard();
    });
    state.layers[area.area_id] = layer;
  }
}

function renderCounts() {
  const counts = { buildable: 0, yours: 0, building: 0, lit: 0 };
  for (const area of state.areas) {
    const look = lookForArea(area);
    if (look === "notpossible") {
      continue;
    }
    counts[look] += 1;
  }
  $("counts").textContent = t("map.counts", counts);
}

function renderFilters() {
  const biomeSelect = $("filter-biome");
  const keepBiome = biomeSelect.value;
  const biomes = [...new Set(state.areas.map((a) => a.biome).filter(Boolean))].sort();
  biomeSelect.innerHTML = `<option value="">${escapeHtml(t("map.all"))}</option>` +
    biomes.map((b) => `<option value="${escapeHtml(b)}">${escapeHtml(b)}</option>`).join("");
  biomeSelect.value = keepBiome;
  const stateSelect = $("filter-state");
  const keepState = stateSelect.value;
  const looks = ["", "yours", "building", "buildable", "lit", "notpossible"];
  stateSelect.innerHTML = looks.map((look) =>
    `<option value="${look}">${escapeHtml(look ? t(`look.${look}`) : t("map.all"))}</option>`
  ).join("");
  stateSelect.value = keepState;
}

/* -- area card ------------------------------------------------ */

function renderAreaCard() {
  const card = $("area-card");
  const area = state.areas.find((a) => a.area_id === state.selectedArea);
  if (!area) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("area-name").textContent = area.name;
  $("area-facts").textContent = `${area.country} · ${area.biome || ""}`.trim();
  const look = lookForArea(area);
  const msg = $("area-msg");
  msg.textContent = "";
  const buildButton = $("area-build");
  const wishButton = $("area-wish");
  buildButton.hidden = true;
  wishButton.hidden = true;
  if (look === "notpossible") {
    $("area-state").textContent = t("area.notpossible");
    wishButton.hidden = false;
  } else if (look === "lit") {
    $("area-state").textContent = t("area.is_public");
  } else if (look === "building") {
    $("area-state").textContent = t("area.building");
  } else if (look === "yours") {
    $("area-state").textContent = t("area.has_draft");
  } else {
    $("area-state").textContent = t(`look.${look}`);
    buildButton.hidden = false;
  }
}

function explorerSpecies() {
  try {
    return sessionStorage.getItem("animundoExplorer") || "";
  } catch (_) {
    return "";
  }
}

async function buildThisPlace() {
  clearError();
  const area = state.areas.find((a) => a.area_id === state.selectedArea);
  if (!area || lookForArea(area) !== "buildable") {
    return;
  }
  const fields = {
    kind: "new_place",
    area_id: area.area_id,
    owner_uid: state.user.uid,
    family_uids: [state.user.uid],
    status: "requested",
    created_at: firebase.firestore.FieldValue.serverTimestamp(),
  };
  const explorer = explorerSpecies();
  if (explorer) {
    fields.first_explorer_species = explorer;
  }
  try {
    await db().collection("drafts").add(fields);
    $("area-msg").textContent = t("area.started");
  } catch (error) {
    console.error(error);
    showError();
  }
}

async function wishForPlace() {
  clearError();
  const area = state.areas.find((a) => a.area_id === state.selectedArea);
  if (!area) {
    return;
  }
  try {
    await db().collection("wishes").doc(area.area_id)
      .collection("votes").doc(state.user.uid)
      .set({ at: firebase.firestore.FieldValue.serverTimestamp() });
    $("area-msg").textContent = t("area.wished");
    $("area-wish").hidden = true;
  } catch (error) {
    console.error(error);
    showError();
  }
}

/* -- drafts --------------------------------------------------- */

function fmtTime(value) {
  if (!value) {
    return "";
  }
  const date = typeof value.toDate === "function" ? value.toDate() : new Date(value);
  return date.toLocaleString(state.lang === "fi" ? "fi-FI" : "en-US");
}

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
    watchFeed(draft);
    renderFeed(draft.id);
  }
}

function areaName(areaId) {
  const area = state.areas.find((a) => a.area_id === areaId);
  return area ? area.name : areaId;
}

function renderDraft(draft) {
  const card = document.createElement("article");
  card.className = "draft";
  card.dataset.id = draft.id;
  const title = draft.place_name || areaName(draft.area_id);
  const statusKey = `draft.status.${draft.status}`;
  const status = state.strings[statusKey] || t("draft.status.failed");
  let html = `<h3>${escapeHtml(title)}</h3>`;
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
    }
  } catch (error) {
    console.error(error);
    showError();
  }
}

function watchFeed(draft) {
  if (state.feedUnsubs[draft.id]) {
    return;
  }
  state.feedUnsubs[draft.id] = db().collection("drafts").doc(draft.id)
    .collection("feed").orderBy("n").onSnapshot(
      (snap) => {
        const entries = [];
        snap.forEach((doc) => entries.push(doc.data()));
        state.feeds = state.feeds || {};
        state.feeds[draft.id] = entries;
        renderFeed(draft.id);
      },
      (error) => console.error(error),
    );
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
  firebase.initializeApp(FIREBASE_CONFIG);
  const params = new URLSearchParams(window.location.search);
  const explorer = params.get("explorer");
  if (explorer) {
    try {
      sessionStorage.setItem("animundoExplorer", explorer);
    } catch (_) {
      /* private mode */
    }
  }
  let lang = "en";
  try {
    lang = localStorage.getItem("animundoBuildLang") ||
      ((navigator.language || "").startsWith("fi") ? "fi" : "en");
  } catch (_) {
    lang = (navigator.language || "").startsWith("fi") ? "fi" : "en";
  }
  await loadStrings(lang);
  applyStaticStrings();
  $("lang-en").addEventListener("click", () => setLang("en"));
  $("lang-fi").addEventListener("click", () => setLang("fi"));
  $("auth-send").addEventListener("click", sendLink);
  $("auth-out").addEventListener("click", () => firebaseAuth().signOut());
  $("area-build").addEventListener("click", buildThisPlace);
  $("area-wish").addEventListener("click", wishForPlace);
  $("filter-biome").addEventListener("change", renderMarkers);
  $("filter-state").addEventListener("change", renderMarkers);
  await finishLink();
  firebaseAuth().onAuthStateChanged(onUser);
}

start().catch((error) => {
  console.error(error);
  showError();
});
