// Seeds the Auth emulator's test grown-up for the web app's
// emulator mode (?emulator=1). Needs the emulators running:
//
//   firebase emulators:start --project demo-animundo \
//       --only auth,firestore,storage
//   node firebase/seed-emulator.mjs
//
// No dependencies: plain HTTP against the emulator's REST
// surface, plus its admin access for the verified flag (a user
// token cannot set it). Idempotent: run it as often as you like.

const AUTH = "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1";
const ADMIN = "http://127.0.0.1:9099/v1/projects/demo-animundo/accounts:update";
const EMAIL = "test@example.org";
const PASSWORD = "grown-up-1";

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

async function localId() {
  try {
    const created = await call("accounts:signUp", {
      email: EMAIL,
      password: PASSWORD,
      returnSecureToken: true,
    });
    return [created.localId, created.idToken];
  } catch (error) {
    if (!String(error).includes("EMAIL_EXISTS")) {
      throw error;
    }
    const signedIn = await call("accounts:signInWithPassword", {
      email: EMAIL,
      password: PASSWORD,
      returnSecureToken: true,
    });
    return [signedIn.localId, signedIn.idToken];
  }
}

const [uid, token] = await localId();
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
console.log(`test grown-up ready: ${EMAIL} (email verified)`);
