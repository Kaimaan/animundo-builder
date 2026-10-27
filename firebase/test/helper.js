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

// A grown-up: signed in by email link, like contributor_codes.
// The token spells out every claim the rules read.
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

export function draftDoc(owner, over = {}) {
  return {
    kind: "new_place",
    area_id: "fi_repovesi",
    owner_uid: owner,
    family_uids: [owner],
    status: "requested",
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
