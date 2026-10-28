import { after, before, beforeEach, describe, it } from "node:test";
import {
  assertFails,
  assertSucceeds,
} from "@firebase/rules-unit-testing";
import {
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  setDoc,
  updateDoc,
} from "firebase/firestore";
import {
  agoSecs,
  areaDoc,
  grownUp,
  judgeBuildDoc,
  makeEnv,
  player,
  requestDoc,
  seed,
} from "./helper.js";

const AT = () => agoSecs(5);

describe("border requests", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["border_requests/n_413_296", requestDoc()],
      ["admins/chief", {}],
    ]);
  });

  it("admins read them, nobody else does", async () => {
    await assertSucceeds(
      getDoc(doc(grownUp(env, "chief").firestore(), "border_requests/n_413_296")),
    );
    await assertFails(
      getDoc(doc(player(env, "kid").firestore(), "border_requests/n_413_296")),
    );
    await assertFails(
      getDoc(
        doc(env.unauthenticatedContext().firestore(), "border_requests/n_413_296"),
      ),
    );
  });

  it("no client writes them, not even admins", async () => {
    const chief = grownUp(env, "chief").firestore();
    await assertFails(
      setDoc(doc(chief, "border_requests/n_413_297"), requestDoc()),
    );
    await assertFails(
      updateDoc(doc(chief, "border_requests/n_413_296"), { notes: 4 }),
    );
    await assertFails(deleteDoc(doc(chief, "border_requests/n_413_296")));
    const kid = player(env, "kid").firestore();
    await assertFails(setDoc(doc(kid, "border_requests/n_413_297"), requestDoc()));
  });
});

describe("areas", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [["areas/n_413_296", areaDoc()]]);
  });

  it("anyone signed in reads them, visitors do not", async () => {
    await assertSucceeds(
      getDoc(doc(player(env, "kid").firestore(), "areas/n_413_296")),
    );
    await assertSucceeds(
      getDoc(doc(grownUp(env, "grown").firestore(), "areas/n_413_296")),
    );
    await assertFails(
      getDoc(doc(env.unauthenticatedContext().firestore(), "areas/n_413_296")),
    );
  });

  it("no client writes them", async () => {
    const db = player(env, "kid").firestore();
    await assertFails(setDoc(doc(db, "areas/n_413_297"), areaDoc()));
    await assertFails(
      updateDoc(doc(db, "areas/n_413_296"), { status: "ready" }),
    );
  });
});

describe("judge builds and judges", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["judge_builds/b1", judgeBuildDoc("judge1")],
      ["judges/judge1", { cap_usd: 5 }],
      ["admins/chief", {}],
    ]);
  });

  it("only that judge and admins read the build", async () => {
    await assertSucceeds(
      getDoc(doc(grownUp(env, "judge1").firestore(), "judge_builds/b1")),
    );
    await assertSucceeds(
      getDoc(doc(grownUp(env, "chief").firestore(), "judge_builds/b1")),
    );
    await assertFails(
      getDoc(doc(grownUp(env, "judge2").firestore(), "judge_builds/b1")),
    );
    await assertFails(
      getDoc(doc(player(env, "kid").firestore(), "judge_builds/b1")),
    );
  });

  it("only that judge and admins read the judge record", async () => {
    await assertSucceeds(
      getDoc(doc(grownUp(env, "judge1").firestore(), "judges/judge1")),
    );
    await assertFails(
      getDoc(doc(grownUp(env, "judge2").firestore(), "judges/judge1")),
    );
    await assertFails(
      getDocs(collection(grownUp(env, "judge1").firestore(), "judges")),
    );
  });

  it("no client writes them", async () => {
    const db = grownUp(env, "judge1").firestore();
    await assertFails(setDoc(doc(db, "judge_builds/b2"), judgeBuildDoc("judge1")));
    await assertFails(
      updateDoc(doc(db, "judge_builds/b1"), { status: "ready_for_judge" }),
    );
    await assertFails(setDoc(doc(db, "judges/judge1"), { cap_usd: 10 }));
  });
});

describe("jobs and config", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["jobs/j1", { tile: "n_413_296" }],
      ["config/builder", { enabled: true, accepting_requests: true }],
      ["admins/chief", {}],
    ]);
  });

  it("jobs stay hidden from every client", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(getDoc(doc(db, "jobs/j1")));
    await assertFails(setDoc(doc(db, "jobs/j2"), { tile: "n_413_296" }));
  });

  it("anyone signed in reads the off switch, nobody flips it", async () => {
    await assertSucceeds(
      getDoc(doc(player(env, "anon").firestore(), "config/builder")),
    );
    await assertFails(
      getDoc(doc(env.unauthenticatedContext().firestore(), "config/builder")),
    );
    await assertFails(
      updateDoc(doc(grownUp(env, "grown").firestore(), "config/builder"), {
        enabled: false,
      }),
    );
    await assertFails(
      updateDoc(doc(grownUp(env, "chief").firestore(), "config/builder"), {
        enabled: false,
      }),
    );
  });
});

describe("admins and inbox", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["admins/chief", {}],
      ["admin_inbox/i1", { kind: "failed", tile: "n_413_296" }],
    ]);
  });

  it("an account reads only its own admin entry", async () => {
    await assertSucceeds(getDoc(doc(grownUp(env, "chief").firestore(), "admins/chief")));
    await assertFails(getDoc(doc(grownUp(env, "grown").firestore(), "admins/chief")));
    await assertFails(
      getDocs(collection(grownUp(env, "chief").firestore(), "admins")),
    );
    await assertFails(
      setDoc(doc(grownUp(env, "grown").firestore(), "admins/grown"), {}),
    );
  });

  it("admins read the inbox, nobody else does", async () => {
    await assertSucceeds(
      getDoc(doc(grownUp(env, "chief").firestore(), "admin_inbox/i1")),
    );
    await assertFails(
      getDoc(doc(grownUp(env, "grown").firestore(), "admin_inbox/i1")),
    );
    await assertFails(
      setDoc(doc(grownUp(env, "chief").firestore(), "admin_inbox/i2"), {}),
    );
  });

  it("admins mark items handled and nothing else", async () => {
    const chief = grownUp(env, "chief").firestore();
    await assertSucceeds(
      updateDoc(doc(chief, "admin_inbox/i1"), {
        handled: true,
        handled_at: AT(),
      }),
    );
    await assertFails(
      updateDoc(doc(chief, "admin_inbox/i1"), { kind: "other" }),
    );
    await assertFails(
      updateDoc(doc(grownUp(env, "grown").firestore(), "admin_inbox/i1"), {
        handled: true,
      }),
    );
  });
});

describe("admin actions", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [["admins/chief", {}]]);
  });

  it("admins approve, turn down, ship, refuse and reopen", async () => {
    const chief = grownUp(env, "chief").firestore();
    const ask = (id, data) => setDoc(doc(chief, `admin_actions/${id}`), data);
    await assertSucceeds(
      ask("a1", {
        admin_uid: "chief",
        action: "approve_request",
        tile: "n_413_296",
        at: AT(),
      }),
    );
    await assertSucceeds(
      ask("a2", {
        admin_uid: "chief",
        action: "turn_down_request",
        tile: "n_413_296",
        reason: "not_now",
        at: AT(),
      }),
    );
    await assertSucceeds(
      ask("a3", {
        admin_uid: "chief",
        action: "ship_item",
        tile: "n_413_296",
        item: "tundra-home",
        at: AT(),
      }),
    );
    await assertSucceeds(
      ask("a4", {
        admin_uid: "chief",
        action: "not_accepted_item",
        tile: "n_413_296",
        item: "tundra-home",
        reason: "quality",
        note: "The ground sits under water.",
        at: AT(),
      }),
    );
    await assertSucceeds(
      ask("a5", {
        admin_uid: "chief",
        action: "reopen_request",
        tile: "n_413_296",
        at: AT(),
      }),
    );
  });

  it("ship and refuse name the item, turn down names the reason", async () => {
    const chief = grownUp(env, "chief").firestore();
    const ask = (id, data) => setDoc(doc(chief, `admin_actions/${id}`), data);
    await assertFails(
      ask("b1", {
        admin_uid: "chief",
        action: "ship_item",
        tile: "n_413_296",
        at: AT(),
      }),
    );
    await assertFails(
      ask("b2", {
        admin_uid: "chief",
        action: "not_accepted_item",
        tile: "n_413_296",
        item: "tundra-home",
        at: AT(),
      }),
    );
    await assertFails(
      ask("b3", {
        admin_uid: "chief",
        action: "turn_down_request",
        tile: "n_413_296",
        at: AT(),
      }),
    );
  });

  it("actions, tiles, reasons and notes come from fixed shapes", async () => {
    const chief = grownUp(env, "chief").firestore();
    const ask = (id, data) => setDoc(doc(chief, `admin_actions/${id}`), data);
    await assertFails(
      ask("c1", {
        admin_uid: "chief",
        action: "launch",
        tile: "n_413_296",
        at: AT(),
      }),
    );
    await assertFails(
      ask("c2", {
        admin_uid: "chief",
        action: "approve_request",
        tile: "413_296",
        at: AT(),
      }),
    );
    await assertFails(
      ask("c3", {
        admin_uid: "chief",
        action: "turn_down_request",
        tile: "n_413_296",
        reason: "tomorrow",
        at: AT(),
      }),
    );
    await assertFails(
      ask("c4", {
        admin_uid: "chief",
        action: "approve_request",
        tile: "n_413_296",
        note: "x".repeat(301),
        at: AT(),
      }),
    );
    await assertFails(
      ask("c5", {
        admin_uid: "chief",
        action: "approve_request",
        tile: "n_413_296",
        draft_id: "d1",
        at: AT(),
      }),
    );
    await assertFails(
      ask("c6", {
        admin_uid: "grown",
        action: "approve_request",
        tile: "n_413_296",
        at: AT(),
      }),
    );
  });

  it("only admins read them, nobody rewrites them", async () => {
    await seed(env, [
      [
        "admin_actions/a1",
        {
          admin_uid: "chief",
          action: "approve_request",
          tile: "n_413_296",
          at: agoSecs(60),
        },
      ],
    ]);
    await assertSucceeds(
      getDoc(doc(grownUp(env, "chief").firestore(), "admin_actions/a1")),
    );
    await assertFails(
      getDoc(doc(grownUp(env, "grown").firestore(), "admin_actions/a1")),
    );
    await assertFails(
      setDoc(doc(grownUp(env, "grown").firestore(), "admin_actions/a2"), {
        admin_uid: "grown",
        action: "approve_request",
        tile: "n_413_296",
        at: AT(),
      }),
    );
    const chief = grownUp(env, "chief").firestore();
    await assertFails(
      updateDoc(doc(chief, "admin_actions/a1"), { action: "reopen_request" }),
    );
    await assertFails(deleteDoc(doc(chief, "admin_actions/a1")));
  });
});
