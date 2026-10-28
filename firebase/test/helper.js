import fs from "node:fs";
import { Timestamp, doc, setDoc } from "firebase/firestore";
import { initializeTestEnvironment } from "@firebase/rules-unit-testing";

export async function makeEnv() {
  const root = new URL("..", import.meta.url);
  return initializeTestEnvironment({
    projectId: "demo-animundo-rules",
    firestore: {
      rules: fs.readFileSync(new URL("firestore.rules", root), "utf8"),
    },
    storage: {
      rules: fs.readFileSync(new URL("storage.rules", root), "utf8"),
    },
  });
}

// An admin: signed in by email and password.
export const grownUp = (env, uid) =>
  env.authenticatedContext(uid, {
    email: `${uid}@example.org`,
    email_verified: true,
    firebase: { sign_in_provider: "password" },
  });

// An anonymous player.
export const player = (env, uid) => env.authenticatedContext(uid);

export const agoSecs = (s) => Timestamp.fromMillis(Date.now() - s * 1000);
export const inSecs = (s) => Timestamp.fromMillis(Date.now() + s * 1000);

export function requestDoc(over = {}) {
  return {
    format: 1,
    tile: "n_413_296",
    reason: "no_home",
    habitat: "tundra",
    notes: 3,
    notes_by_day: { "2026-11-01": 3 },
    species_played: { fox: 2 },
    priority: 3,
    created_at: agoSecs(3600),
    last_note_at: agoSecs(60),
    ...over,
  };
}

export function areaDoc(over = {}) {
  return {
    format: 1,
    tile: "n_413_296",
    route: "admin",
    name: { en: "Forest north of Nuuksio", fi: "Metsa pohjoisessa" },
    status: "waiting",
    approval: "pending",
    plan: {},
    estimate_usd: 1.2,
    cost_usd: 0,
    coming: [],
    items: [],
    version: 1,
    tiles: ["n_413_296"],
    updated_at: agoSecs(60),
    ...over,
  };
}

export function judgeBuildDoc(judge, over = {}) {
  return {
    format: 1,
    judge_uid: judge,
    tile: "n_413_296",
    species: "fox",
    status: "being_built",
    items: [],
    pack_version: 0,
    cost_usd: 0,
    created_at: agoSecs(3600),
    updated_at: agoSecs(60),
    ...over,
  };
}

export async function seed(env, writes) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    for (const [path, data] of writes) {
      await setDoc(doc(db, ...path.split("/")), data);
    }
  });
}
