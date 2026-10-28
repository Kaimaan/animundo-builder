"use strict";
/* Animundo Builder: the admin panel.
 *
 * Static files only (the game's copy publishes this folder at
 * animundo.app/admin). Admins sign in with email and password;
 * every button only does what the Firestore rules allow: read
 * border requests, areas, judge builds and the inbox, mark inbox
 * items handled, and ask the worker to approve, turn down, ship,
 * refuse or reopen through admin_actions. Tile ids only, never
 * names or emails. English only.
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
const TEST_ADMIN = { email: "admin@example.org", password: "admin-1" };

const STATUSES = [
  "waiting",
  "being_built",
  "being_checked",
  "ready",
  "turned_down",
  "failed",
];

const TURN_DOWN_REASONS = ["too_costly", "odd_request", "not_now", "quality", "other"];

const state = {
  user: null,
  areas: [],
  requests: {},
};

function $(id) {
  return document.getElementById(id);
}

function db() {
  return firebase.firestore();
}

function showError(text) {
  const box = $("app-error");
  box.textContent = text || "Something went wrong. Try again.";
  box.hidden = false;
}

function clearError() {
  $("app-error").hidden = true;
}

function fmtDate(value) {
  if (!value) {
    return "—";
  }
  const date = typeof value.toDate === "function" ? value.toDate() : new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function fmtCost(value) {
  return typeof value === "number" ? `$${value.toFixed(2)}` : "—";
}

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) {
    node.textContent = text;
  }
  if (cls) {
    node.className = cls;
  }
  return node;
}

/* -- auth ---------------------------------------------------- */

function firebaseAuth() {
  return firebase.auth();
}

function emulatorMode() {
  return new URLSearchParams(window.location.search).has("emulator");
}

async function signIn() {
  clearError();
  const email = $("auth-email").value.trim();
  const password = $("auth-password").value;
  if (!email || !password) {
    showError("Type your email and password.");
    return;
  }
  try {
    await firebaseAuth().signInWithEmailAndPassword(email, password);
  } catch (error) {
    console.error(error);
    showError("Wrong email or password.");
  }
}

async function signInTestAdmin() {
  clearError();
  try {
    await firebaseAuth().signInWithEmailAndPassword(
      TEST_ADMIN.email,
      TEST_ADMIN.password,
    );
  } catch (error) {
    console.error(error);
    showError("Seed the emulators first: node firebase/seed-emulator.mjs");
  }
}

async function onUser(user) {
  state.user = user;
  $("auth-signed-out").hidden = !!user;
  $("auth-signed-in").hidden = !user;
  $("app").hidden = true;
  $("not-admin").hidden = true;
  if (!user) {
    return;
  }
  $("auth-who").textContent = `Signed in as ${user.email || user.uid}`;
  try {
    const snap = await db().collection("admins").doc(user.uid).get();
    if (!snap.exists) {
      $("not-admin").hidden = false;
      return;
    }
  } catch (error) {
    console.error(error);
    showError();
    return;
  }
  $("app").hidden = false;
  await loadAll();
}

async function boot() {
  firebase.initializeApp(FIREBASE_CONFIG);
  if (emulatorMode()) {
    firebaseAuth().useEmulator(EMULATORS.auth);
    db().useEmulator(...EMULATORS.firestore);
    $("auth-test").hidden = false;
  }
  $("auth-signin").addEventListener("click", signIn);
  $("auth-test").addEventListener("click", signInTestAdmin);
  $("auth-out").addEventListener("click", () => firebaseAuth().signOut());
  $("area-filter").addEventListener("change", renderAreas);
  for (const status of STATUSES) {
    const option = document.createElement("option");
    option.value = status;
    option.textContent = status;
    $("area-filter").append(option);
  }
  firebaseAuth().onAuthStateChanged(onUser);
}

/* -- loading ------------------------------------------------- */

async function loadAll() {
  clearError();
  try {
    const snap = await db().collection("areas").limit(200).get();
    state.areas = snap.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
    state.areas.sort((a, b) => stampOf(b) - stampOf(a));
    const reqs = await db().collection("border_requests").limit(200).get();
    state.requests = Object.fromEntries(
      reqs.docs.map((doc) => [doc.id, doc.data()]),
    );
  } catch (error) {
    console.error(error);
    showError();
    return;
  }
  renderQueue();
  renderAreas();
  await loadInbox();
}

function stampOf(area) {
  const value = area.updated_at;
  if (!value) {
    return 0;
  }
  const date =
    typeof value.toDate === "function" ? value.toDate() : new Date(value);
  return Number.isNaN(date.getTime()) ? 0 : date.getTime();
}

/* -- request queue ------------------------------------------- */

function renderQueue() {
  const list = $("queue-list");
  list.innerHTML = "";
  const pending = state.areas.filter(
    (area) => area.route === "admin" && area.approval === "pending",
  );
  if (!pending.length) {
    list.append(el("p", "Nothing waiting.", "muted"));
    return;
  }
  for (const area of pending) {
    list.append(queueCard(area));
  }
}

function planText(plan) {
  if (!plan) {
    return "—";
  }
  const parts = [];
  const expand = plan.expand || {};
  if (expand.home) {
    parts.push(`home ${expand.home}`);
  }
  if (expand.animal) {
    parts.push(`animal ${expand.animal}`);
  }
  if (expand.food && expand.food.length) {
    parts.push(`food ${expand.food.join(", ")}`);
  }
  if (expand.find) {
    parts.push(`find ${expand.find}`);
  }
  if (plan.improve && plan.improve.length) {
    parts.push(`improve ${plan.improve.join(", ")}`);
  }
  return parts.join("; ") || "nothing missing";
}

function queueCard(area) {
  const card = el("div", undefined, "item");
  const name = (area.name && area.name.en) || area.tile;
  card.append(el("h3", `${name} · ${area.tile}`));
  const request = state.requests[area.tile] || {};
  const species = Object.entries(request.species_played || {})
    .map(([id, n]) => `${id} ${n}`)
    .join(", ");
  for (const line of [
    `Route: ${area.route || "?"}`,
    `Plan: ${planText(area.plan)}`,
    `Estimate: ${fmtCost(area.estimate_usd)}`,
    `Notes: ${request.notes || "—"} (priority ${request.priority ?? "—"})`,
    `Animals played: ${species || "—"}`,
  ]) {
    card.append(el("p", line, "muted"));
  }
  const reason = document.createElement("select");
  for (const value of TURN_DOWN_REASONS) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    reason.append(option);
  }
  const approve = el("button", "Approve");
  approve.type = "button";
  approve.addEventListener("click", () =>
    sendAction(area.tile, "approve_request", {}),
  );
  const turnDown = el("button", "Turn down");
  turnDown.type = "button";
  turnDown.classList.add("ghost");
  turnDown.addEventListener("click", () =>
    sendAction(area.tile, "turn_down_request", { reason: reason.value }),
  );
  const row = el("div", undefined, "draft answer-row");
  row.append(reason, approve, turnDown);
  card.append(row);
  return card;
}

/* -- areas --------------------------------------------------- */

function renderAreas() {
  const list = $("area-list");
  list.innerHTML = "";
  const wanted = $("area-filter").value;
  const areas = wanted
    ? state.areas.filter((area) => area.status === wanted)
    : state.areas;
  if (!areas.length) {
    list.append(el("p", "No areas.", "muted"));
    return;
  }
  for (const area of areas) {
    list.append(areaCard(area));
  }
}

function areaCard(area) {
  const card = el("div", undefined, "draft");
  const name = (area.name && area.name.en) || area.tile;
  card.append(el("h3", `${name} · ${area.tile}`));
  for (const line of [
    `Status: ${area.status || "?"}`,
    `Approval: ${area.approval || "?"}`,
    `Cost so far: ${fmtCost(area.cost_usd)}`,
    `Updated: ${fmtDate(area.updated_at)}`,
  ]) {
    card.append(el("p", line, "muted"));
  }
  for (const item of area.items || []) {
    card.append(
      el(
        "p",
        `${item.kind} ${item.what}: ${item.status}`,
        "muted",
      ),
    );
    if (item.status === "checking") {
      card.append(itemReviewRow(area.tile, item));
    }
  }
  if (area.status === "turned_down" || area.status === "failed") {
    const reopen = el("button", "Reopen");
    reopen.type = "button";
    reopen.classList.add("ghost");
    reopen.addEventListener("click", () =>
      sendAction(area.tile, "reopen_request", {}),
    );
    card.append(reopen);
  }
  return card;
}

function itemReviewRow(tile, item) {
  const row = el("div", undefined, "draft answer-row");
  const reason = document.createElement("select");
  for (const value of TURN_DOWN_REASONS) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    reason.append(option);
  }
  const ship = el("button", `Ship ${item.what}`);
  ship.type = "button";
  ship.addEventListener("click", () =>
    sendAction(tile, "ship_item", { item: item.id }),
  );
  const refuse = el("button", "Not accepted");
  refuse.type = "button";
  refuse.classList.add("ghost");
  refuse.addEventListener("click", () =>
    sendAction(tile, "not_accepted_item", {
      item: item.id,
      reason: reason.value,
    }),
  );
  row.append(reason, ship, refuse);
  return row;
}

/* -- inbox --------------------------------------------------- */

async function loadInbox() {
  const list = $("inbox-list");
  list.innerHTML = "";
  let items = [];
  try {
    const snap = await db()
      .collection("admin_inbox")
      .orderBy("at", "desc")
      .limit(50)
      .get();
    items = snap.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
  } catch (error) {
    console.error(error);
    showError();
    return;
  }
  if (!items.length) {
    list.append(el("p", "Nothing here.", "muted"));
    return;
  }
  for (const item of items) {
    list.append(inboxCard(item));
  }
}

function inboxCard(item) {
  const card = el("div", undefined, "item");
  const head = el("h3", `${item.kind || "note"} · ${item.tile || item.id}`);
  if (item.urgent && !item.handled) {
    head.append(el("span", "urgent", "urgent-tag"));
  }
  card.append(head);
  if (item.reason) {
    card.append(el("p", `Reason: ${item.reason}`));
  }
  if (item.note) {
    card.append(el("p", `Note: ${item.note}`));
  }
  card.append(el("p", `At: ${fmtDate(item.at)}`, "muted"));
  if (item.handled) {
    card.append(el("p", "Handled.", "muted"));
    return card;
  }
  const done = el("button", "Mark handled");
  done.type = "button";
  done.classList.add("ghost");
  done.addEventListener("click", () => markHandled(item));
  card.append(done);
  return card;
}

/* -- actions ------------------------------------------------- */

async function sendAction(tile, action, extra) {
  clearError();
  try {
    await db()
      .collection("admin_actions")
      .add({
        admin_uid: state.user.uid,
        tile,
        action,
        ...extra,
        at: firebase.firestore.FieldValue.serverTimestamp(),
      });
    await loadAll();
  } catch (error) {
    console.error(error);
    showError();
  }
}

async function markHandled(item) {
  clearError();
  try {
    await db().collection("admin_inbox").doc(item.id).update({
      handled: true,
      handled_at: new Date().toISOString(),
    });
    await loadInbox();
  } catch (error) {
    console.error(error);
    showError();
  }
}

boot();
