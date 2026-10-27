"use strict";
/* Animundo Builder: the admin panel (short T24).
 *
 * Static files only (the game's copy publishes this folder at
 * animundo.app/admin). Admins sign in with an email link; every
 * button only does what the Firestore and Storage rules allow:
 * read drafts, the inbox and packs, mark inbox items handled,
 * and ask the worker to publish, turn down, show again or remove
 * a draft through admin_actions. Draft ids only, never names or
 * emails. English only.
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
  "requested",
  "queued",
  "building",
  "ready",
  "improving",
  "in_review",
  "public",
  "not_accepted",
  "hidden",
  "removed",
  "expired",
  "not_possible_yet",
  "failed",
];

const state = {
  user: null,
  drafts: [],
};

function $(id) {
  return document.getElementById(id);
}

function db() {
  return firebase.firestore();
}

function storage() {
  return firebase.storage();
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
    localStorage.setItem("animundoAdminEmail", email);
  } catch (_) {
    /* private mode */
  }
  try {
    await firebaseAuth().sendSignInLinkToEmail(email, {
      url: returnUrl(),
      handleCodeInApp: true,
    });
    $("auth-note").textContent = "Check your email for the sign-in link.";
  } catch (error) {
    console.error(error);
    showError();
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

async function finishLink() {
  if (!firebaseAuth().isSignInWithEmailLink(window.location.href)) {
    return false;
  }
  let email = null;
  try {
    email = localStorage.getItem("animundoAdminEmail");
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
    showError("That sign-in link is bad or expired.");
    return false;
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
    storage().useEmulator(...EMULATORS.storage);
    $("auth-test").hidden = false;
  }
  $("auth-send").addEventListener("click", sendLink);
  $("auth-test").addEventListener("click", signInTestAdmin);
  $("auth-out").addEventListener("click", () => firebaseAuth().signOut());
  $("draft-filter").addEventListener("change", renderDrafts);
  for (const status of STATUSES) {
    const option = document.createElement("option");
    option.value = status;
    option.textContent = status;
    $("draft-filter").append(option);
  }
  await finishLink();
  firebaseAuth().onAuthStateChanged(onUser);
}

/* -- loading ------------------------------------------------- */

async function loadAll() {
  clearError();
  try {
    const snap = await db().collection("drafts").limit(200).get();
    state.drafts = snap.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
    state.drafts.sort((a, b) => stampOf(b) - stampOf(a));
  } catch (error) {
    console.error(error);
    showError();
    return;
  }
  renderDrafts();
  renderSubmitted();
  await loadProblems();
}

function stampOf(draft) {
  for (const key of ["updated_at", "created_at"]) {
    const value = draft[key];
    if (!value) {
      continue;
    }
    const date =
      typeof value.toDate === "function" ? value.toDate() : new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return date.getTime();
    }
  }
  return 0;
}

function draftTitle(draft) {
  return (
    draft.place_name ||
    (draft.kind === "animal_home"
      ? `home ${draft.cell || draft.id}`
      : draft.area_id || draft.id)
  );
}

/* -- places being built -------------------------------------- */

function renderDrafts() {
  const list = $("draft-list");
  list.innerHTML = "";
  const wanted = $("draft-filter").value;
  const drafts = wanted
    ? state.drafts.filter((draft) => draft.status === wanted)
    : state.drafts;
  if (!drafts.length) {
    list.append(el("p", "No drafts.", "muted"));
    return;
  }
  for (const draft of drafts) {
    list.append(draftCard(draft));
  }
}

function draftCard(draft) {
  const card = el("div", undefined, "draft");
  card.append(el("h3", `${draftTitle(draft)} · ${draft.id}`));
  const lines = [
    `Status: ${draft.status || "?"}`,
    `Stage: ${draft.stage_detail || draft.stage || "—"}`,
    `Round: ${draft.round || "—"}`,
    `Cost so far: ${fmtCost(draft.cost_usd)}`,
    `Started: ${fmtDate(draft.created_at)}`,
    `Last played: ${fmtDate(draft.last_played_at)}`,
  ];
  if (draft.error) {
    lines.push(`Reason: ${draft.error}`);
  }
  for (const line of lines) {
    card.append(el("p", line, "muted"));
  }
  if (draft.pack_version) {
    const species = el("p", "New species: loading…", "muted");
    card.append(species);
    fillSpecies(draft, species, card);
  }
  if (draft.round) {
    const reports = el("p", undefined, "muted");
    reports.append("Reports: ");
    card.append(reports);
    fillReports(draft, reports);
  }
  return card;
}

async function fillSpecies(draft, line, card) {
  let manifest = null;
  try {
    const url = await storage()
      .ref(`packs/${draft.id}/${draft.pack_version}/manifest.json`)
      .getDownloadURL();
    manifest = await (await fetch(url)).json();
  } catch (error) {
    console.error(error);
    line.textContent = "New species: the pack is gone.";
    return;
  }
  const fresh = manifest.new_species || [];
  const reused = manifest.reused_species || [];
  line.textContent =
    `New species: ${fresh.join(", ") || "none"}` +
    (reused.length ? ` (reusing ${reused.length})` : "");
  if (!fresh.length) {
    return;
  }
  const row = el("div", undefined, "previews");
  card.append(row);
  for (const species of fresh) {
    try {
      const url = await storage()
        .ref(`packs/${draft.id}/${draft.pack_version}/previews/${species}.png`)
        .getDownloadURL();
      const figure = document.createElement("figure");
      const img = document.createElement("img");
      img.src = url;
      img.alt = species;
      img.loading = "lazy";
      figure.append(img, species);
      row.append(figure);
    } catch (error) {
      console.error(error);
    }
  }
}

async function fillReports(draft, line) {
  let any = false;
  for (let round = 1; round <= (draft.round || 0); round++) {
    try {
      const url = await storage()
        .ref(`reports/${draft.id}/${round}/report.html`)
        .getDownloadURL();
      const link = document.createElement("a");
      link.href = url;
      link.textContent = `round ${round}`;
      link.target = "_blank";
      link.rel = "noopener";
      if (any) {
        line.append(" · ");
      }
      line.append(link);
      any = true;
    } catch (error) {
      console.error(error);
    }
  }
  if (!any) {
    line.append("none yet");
  }
}

/* -- submitted ----------------------------------------------- */

function renderSubmitted() {
  const list = $("submitted-list");
  list.innerHTML = "";
  const drafts = state.drafts.filter((draft) => draft.status === "in_review");
  if (!drafts.length) {
    list.append(el("p", "Nothing waiting.", "muted"));
    return;
  }
  for (const draft of drafts) {
    list.append(submittedCard(draft));
  }
}

function submittedCard(draft) {
  const card = el("div", undefined, "item");
  card.append(el("h3", `${draftTitle(draft)} · ${draft.id}`));
  const cmd = el("code", `builder submit ${draft.id}`, "cmd");
  const copy = el("button", "Copy");
  copy.type = "button";
  copy.classList.add("ghost");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(`builder submit ${draft.id}`);
      copy.textContent = "Copied";
    } catch (error) {
      console.error(error);
    }
  });
  const cmdRow = el("p");
  cmdRow.append(cmd, " ", copy);
  card.append(cmdRow);
  if (draft.pr_url) {
    const row = el("p");
    const link = document.createElement("a");
    link.href = draft.pr_url;
    link.textContent = "Pull request";
    link.target = "_blank";
    link.rel = "noopener";
    row.append(link);
    card.append(row);
  } else {
    card.append(el("p", "Not sent on yet.", "muted"));
  }
  const answer = document.createElement("input");
  answer.type = "text";
  answer.placeholder = "Reason (to turn it down)";
  answer.maxLength = 300;
  const publish = el("button", "Published");
  publish.type = "button";
  publish.addEventListener("click", () => sendAction(draft.id, "published", {}));
  const refuse = el("button", "Not accepted");
  refuse.type = "button";
  refuse.classList.add("ghost");
  refuse.addEventListener("click", () => {
    if (!answer.value.trim()) {
      showError("Give a reason the family will see.");
      return;
    }
    sendAction(draft.id, "not_accepted", { reason: answer.value.trim() });
  });
  const row = el("div", undefined, "draft answer-row");
  row.append(answer, publish, refuse);
  card.append(row);
  return card;
}

/* -- reported problems --------------------------------------- */

async function loadProblems() {
  const list = $("problem-list");
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
  const problems = items.filter((item) => item.kind === "problem");
  problems.sort(
    (a, b) =>
      Number(!!a.handled) - Number(!!b.handled) ||
      Number(!!b.urgent) - Number(!!a.urgent),
  );
  if (!problems.length) {
    list.append(el("p", "Nothing reported.", "muted"));
    return;
  }
  const byId = Object.fromEntries(state.drafts.map((draft) => [draft.id, draft]));
  for (const item of problems) {
    list.append(problemCard(item, byId[item.draft_id]));
  }
}

function problemCard(item, draft) {
  const card = el("div", undefined, "item");
  const head = el("h3", `Report on ${item.draft_id}`);
  if (item.urgent && !item.handled) {
    head.append(el("span", "urgent", "urgent-tag"));
  }
  card.append(head);
  card.append(el("p", `Reason: ${item.reason || "—"}`));
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
  done.addEventListener("click", () => markHandled(item, card));
  card.append(done);
  if (draft && draft.status === "hidden") {
    const answer = document.createElement("input");
    answer.type = "text";
    answer.placeholder = "Answer the family sees";
    answer.maxLength = 300;
    const show = el("button", "Show again");
    show.type = "button";
    show.addEventListener("click", () =>
      sendAction(draft.id, "show_again", withAnswer(answer)),
    );
    const remove = el("button", "Remove");
    remove.type = "button";
    remove.classList.add("ghost");
    remove.addEventListener("click", () => {
      if (!answer.value.trim()) {
        showError("Give an answer the family will see.");
        return;
      }
      sendAction(draft.id, "remove", { answer: answer.value.trim() });
    });
    const row = el("div", undefined, "draft answer-row");
    row.append(answer, show, remove);
    card.append(row);
  }
  return card;
}

function withAnswer(input) {
  return input.value.trim() ? { answer: input.value.trim() } : {};
}

/* -- actions ------------------------------------------------- */

async function sendAction(draftId, action, extra) {
  clearError();
  try {
    await db()
      .collection("admin_actions")
      .add({
        admin_uid: state.user.uid,
        draft_id: draftId,
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

async function markHandled(item, card) {
  clearError();
  try {
    await db().collection("admin_inbox").doc(item.id).update({
      handled: true,
      handled_at: new Date().toISOString(),
    });
    await loadProblems();
  } catch (error) {
    console.error(error);
    showError();
  }
}

boot();
