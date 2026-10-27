// Seeds the Auth emulator's test grown-up (for the web app's
// emulator mode) and test admin (for the admin panel's). Needs
// the emulators running:
//
//   firebase emulators:start --project demo-animundo \
//       --only auth,firestore,storage
//   node firebase/seed-emulator.mjs
//
// No dependencies: plain HTTP against the emulator's REST
// surface, plus its admin access for the verified flag (a user
// token cannot set it). Idempotent: run it as often as you like.
//
// The admin's uid is printed: add admins/{uid} in the Firestore
// emulator UI (any field) to make it an admin, the same way the
// console does in production.

const AUTH = "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1";
const ADMIN =
  "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1/projects/demo-animundo/accounts:update";
const USERS = [
  { email: "test@example.org", password: "grown-up-1", label: "test grown-up" },
  { email: "admin@example.org", password: "admin-1", label: "test admin" },
];

async function call(method, body) {
  const response = await fetch(`${AUTH}/${method}?key=fake-api-key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const message = (data.error && data.error.message) || response.status;
    throw new Error(`${method}: ${message}`);
  }
  return data;
}

async function localId(email, password) {
  try {
    const created = await call("accounts:signUp", {
      email,
      password,
      returnSecureToken: true,
    });
    return [created.localId, created.idToken];
  } catch (error) {
    if (!String(error).includes("EMAIL_EXISTS")) {
      throw error;
    }
    const signedIn = await call("accounts:signInWithPassword", {
      email,
      password,
      returnSecureToken: true,
    });
    return [signedIn.localId, signedIn.idToken];
  }
}

async function seedUser({ email, password, label }) {
  const [uid, token] = await localId(email, password);
  const updated = await fetch(ADMIN, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer owner",
    },
    body: JSON.stringify({ localId: uid, emailVerified: true }),
  });
  if (!updated.ok) {
    throw new Error(`accounts:update: ${updated.status}`);
  }
  const lookup = await call("accounts:lookup", { idToken: token });
  const user = (lookup.users || [])[0] || {};
  if (user.emailVerified !== true) {
    throw new Error("the emulator did not keep emailVerified; see the README");
  }
  console.log(`${label} ready: ${email} (email verified, uid ${uid})`);
}

for (const user of USERS) {
  await seedUser(user);
}
