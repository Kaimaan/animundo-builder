import fs from "node:fs";
import { doc, setDoc } from "firebase/firestore";
import { initializeTestEnvironment } from "@firebase/rules-unit-testing";

export async function makeEnv() {
  const root = new URL("..", import.meta.url);
  return initializeTestEnvironment({
    projectId: "demo-animundo-rules",
    firestore: {
      // The game owns all Firestore rules; the emulator needs a
      // file here only so Storage rules can read job documents.
      rules: `rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{all=**} {
      allow read, write: if false;
    }
  }
}`,
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

export function jobDoc(privateTo) {
  return {
    format: 1,
    tile: "n_413_296",
    kind: "build",
    items: ["tundra-home"],
    budget_usd: 1.2,
    visibility: "private",
    private_to: privateTo,
    created_by: "policy",
    status: "checking",
    cost_usd: 0.5,
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
