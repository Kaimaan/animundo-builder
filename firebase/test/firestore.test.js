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
  draftDoc,
  grownUp,
  inSecs,
  makeEnv,
  player,
  seed,
} from "./helper.js";

const AT = () => agoSecs(5);

describe("drafts create", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => env.clearFirestore());

  it("a grown-up starts a draft for their own account", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(
      setDoc(
        doc(db, "drafts/d1"),
        draftDoc("grown", {
          first_explorer_species: "red_deer",
          created_at: AT(),
        }),
      ),
    );
  });

  it("an animal home starts with a species key and a picked place", async () => {
    const db = grownUp(env, "grown").firestore();
    const place = {
      wikidata_id: "Q1815268",
      name: "Nuuksio National Park",
      fi: "Nuuksion kansallispuisto",
      kind: "park",
      country: "FI",
    };
    const home = {
      kind: "animal_home",
      species_key: 2435240,
      place,
      owner_uid: "grown",
      family_uids: ["grown"],
      status: "requested",
    };
    await assertSucceeds(setDoc(doc(db, "drafts/h1"), home));
    await assertSucceeds(
      setDoc(
        doc(db, "drafts/h1b"),
        { ...home, place: { ...place, fi: null, country: null } },
      ),
    );
    await assertSucceeds(
      setDoc(doc(db, "drafts/h2"), { ...home, created_at: AT() }),
    );
    await assertFails(setDoc(doc(db, "drafts/h3"), { ...home, species_key: 0 }));
    await assertFails(
      setDoc(doc(db, "drafts/h4"), { ...home, species_key: "2435240" }),
    );
    await assertFails(
      setDoc(
        doc(db, "drafts/h5"),
        { ...home, place: { ...place, wikidata_id: "here" } },
      ),
    );
    await assertFails(
      setDoc(doc(db, "drafts/h5b"), { ...home, place: "Q1815268" }),
    );
    await assertFails(
      setDoc(
        doc(db, "drafts/h5c"),
        { ...home, place: { ...place, cell: "61.2_26.9" } },
      ),
    );
    await assertFails(
      setDoc(doc(db, "drafts/h6"), { ...home, area_id: "fi_repovesi" }),
    );
    await assertSucceeds(
      setDoc(
        doc(db, "drafts/h7"),
        {
          ...home,
          place_id: "fi_nuuksio",
          place_name: "Nuuksio National Park",
        },
      ),
    );
    await assertFails(
      setDoc(doc(db, "drafts/h7b"), { ...home, place_id: 7 }),
    );
    await assertFails(
      setDoc(doc(db, "drafts/h7c"), { ...home, place_id: "K8j5PJ8oSZJix8PqYV8P" }),
    );
    await assertFails(
      setDoc(doc(db, "drafts/h7d"), { ...home, place_id: "fi_nuuksio!" }),
    );
    await assertFails(
      setDoc(
        doc(db, "drafts/h7e"),
        { ...home, place_id: `fi_${"n".repeat(62)}` },
      ),
    );
    await assertSucceeds(
      setDoc(
        doc(db, "drafts/h7f"),
        { ...home, place_id: `fi_${"n".repeat(61)}` },
      ),
    );
    await assertFails(
      setDoc(
        doc(db, "drafts/h7g"),
        { ...home, place_name: "N".repeat(81) },
      ),
    );
    await assertSucceeds(
      setDoc(
        doc(db, "drafts/h7h"),
        { ...home, place_name: "N".repeat(80) },
      ),
    );
    await assertFails(
      setDoc(doc(db, "drafts/h8"), { ...home, country: "FI" }),
    );
    await assertFails(
      setDoc(doc(db, "drafts/h9"), { ...home, country_name: "Finland" }),
    );
  });

  it("an anonymous player cannot start one", async () => {
    const db = player(env, "anon").firestore();
    await assertFails(setDoc(doc(db, "drafts/d1"), draftDoc("anon")));
  });

  it("a signed-out visitor cannot start one", async () => {
    const db = env.unauthenticatedContext().firestore();
    await assertFails(setDoc(doc(db, "drafts/d1"), draftDoc("grown")));
  });

  it("the owner must be the signer", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(setDoc(doc(db, "drafts/d1"), draftDoc("someone-else")));
  });

  it("the family starts as the owner alone", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(
      setDoc(doc(db, "drafts/d1"), draftDoc("grown", { family_uids: [] })),
    );
    await assertFails(
      setDoc(
        doc(db, "drafts/d1"),
        draftDoc("grown", { family_uids: ["grown", "kid"] }),
      ),
    );
  });

  it("a draft starts requested, as a new place, with an area", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(
      setDoc(doc(db, "drafts/d1"), draftDoc("grown", { status: "ready" })),
    );
    await assertFails(
      setDoc(doc(db, "drafts/d1"), draftDoc("grown", { kind: "improve" })),
    );
    const { area_id, ...noArea } = draftDoc("grown");
    await assertFails(setDoc(doc(db, "drafts/d1"), noArea));
  });

  it("no worker fields on create", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(
      setDoc(doc(db, "drafts/d1"), draftDoc("grown", { stage: "land" })),
    );
    await assertFails(
      setDoc(doc(db, "drafts/d1"), draftDoc("grown", { round: 1 })),
    );
  });
});

describe("grownups country", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => env.clearFirestore());

  it("a grown-up saves their own country, nothing else", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(setDoc(doc(db, "grownups/grown"), { country: "FI" }));
    await assertSucceeds(
      updateDoc(doc(db, "grownups/grown"), { country: "CA" }),
    );
    await assertFails(
      setDoc(doc(db, "grownups/grown"), { country: "Finland" }),
    );
    await assertFails(setDoc(doc(db, "grownups/grown"), { country: "fi" }));
    await assertFails(
      setDoc(doc(db, "grownups/grown"), { country: "FI", name: "Bo" }),
    );
  });

  it("nobody saves another grown-up's country", async () => {
    const db = grownUp(env, "other").firestore();
    await assertFails(setDoc(doc(db, "grownups/grown"), { country: "FI" }));
    const anon = env.unauthenticatedContext().firestore();
    await assertFails(setDoc(doc(anon, "grownups/grown"), { country: "FI" }));
  });

  it("only the grown-up reads it", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(setDoc(doc(db, "grownups/grown"), { country: "FI" }));
    await assertSucceeds(getDoc(doc(db, "grownups/grown")));
    await assertFails(
      getDoc(doc(grownUp(env, "other").firestore(), "grownups/grown")),
    );
    await assertFails(
      getDocs(collection(grownUp(env, "grown").firestore(), "grownups")),
    );
  });
});

describe("drafts read", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["drafts/d1", draftDoc("grown", { family_uids: ["grown", "kid"] })],
    ]);
  });

  it("the owner and the family read it", async () => {
    await assertSucceeds(getDoc(doc(grownUp(env, "grown").firestore(), "drafts/d1")));
    await assertSucceeds(getDoc(doc(player(env, "kid").firestore(), "drafts/d1")));
  });

  it("strangers and visitors do not", async () => {
    await assertFails(getDoc(doc(grownUp(env, "other").firestore(), "drafts/d1")));
    await assertFails(
      getDoc(doc(env.unauthenticatedContext().firestore(), "drafts/d1")),
    );
  });

  it("admins read and list every draft", async () => {
    await seed(env, [["admins/chief", {}]]);
    const chief = grownUp(env, "chief").firestore();
    await assertSucceeds(getDoc(doc(chief, "drafts/d1")));
    await assertSucceeds(getDocs(collection(chief, "drafts")));
  });
});

describe("drafts improve", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["drafts/ready", draftDoc("grown", { status: "ready" })],
      ["drafts/building", draftDoc("grown", { status: "building" })],
    ]);
  });

  const improve = (db, id, choices, over = {}) =>
    updateDoc(doc(db, `drafts/${id}`), {
      improve: { choices, at: AT(), ...over },
    });

  it("the owner improves a ready draft", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(improve(db, "ready", [{ choice: "colours_off" }]));
  });

  it("animal_looks_wrong names its species, others do not", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(
      improve(db, "ready", [{ choice: "animal_looks_wrong", species: "red_deer" }]),
    );
    await assertFails(improve(db, "ready", [{ choice: "animal_looks_wrong" }]));
    await assertFails(
      improve(db, "ready", [{ choice: "colours_off", species: "red_deer" }]),
    );
    await assertFails(
      improve(db, "ready", [{ choice: "too_few_animals", species: "red_deer" }]),
    );
  });

  it("other may name its species, for the routing", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(
      improve(db, "ready", [{ choice: "other", species: "red_deer" }]),
    );
    await assertSucceeds(improve(db, "ready", [{ choice: "other" }]));
    await assertFails(
      improve(db, "ready", [{ choice: "other", species: "Red deer!" }]),
    );
    await assertFails(
      improve(db, "ready", [{ choice: "other", species: "r".repeat(65) }]),
    );
    await assertSucceeds(
      improve(db, "ready", [{ choice: "other", species: "r".repeat(64) }]),
    );
  });

  it("choices come from the fixed list, one to four", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(improve(db, "ready", [{ choice: "dragons" }]));
    await assertFails(improve(db, "ready", []));
    await assertFails(
      improve(db, "ready", [
        { choice: "colours_off" },
        { choice: "other" },
        { choice: "too_few_animals" },
        { choice: "colours_off" },
        { choice: "other" },
      ]),
    );
  });

  it("the note holds 200 characters", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(
      improve(db, "ready", [{ choice: "other" }], { note: "x".repeat(200) }),
    );
    await assertFails(
      improve(db, "ready", [{ choice: "other" }], { note: "x".repeat(201) }),
    );
  });

  it("only while ready, only the owner, only the request", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(improve(db, "building", [{ choice: "colours_off" }]));
    const family = grownUp(env, "grown2").firestore();
    await env.withSecurityRulesDisabled(async (ctx) => {
      await updateDoc(doc(ctx.firestore(), "drafts/ready"), {
        family_uids: ["grown", "grown2"],
      });
    });
    await assertFails(improve(family, "ready", [{ choice: "colours_off" }]));
    await assertFails(
      updateDoc(doc(db, "drafts/ready"), {
        improve: { choices: [{ choice: "colours_off" }], at: AT() },
        status: "building",
      }),
    );
  });
});

describe("drafts submit", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["drafts/ready", draftDoc("grown", { status: "ready" })],
      [
        "drafts/played",
        draftDoc(
          "grown",
          { status: "ready", last_played_at: agoSecs(60) },
        ),
      ],
      [
        "drafts/rejected",
        draftDoc(
          "grown",
          { status: "not_accepted", last_played_at: agoSecs(60) },
        ),
      ],
      [
        "drafts/building",
        draftDoc(
          "grown",
          { status: "building", last_played_at: agoSecs(60) },
        ),
      ],
    ]);
  });

  const submit = (db, id, over = {}) =>
    updateDoc(doc(db, `drafts/${id}`), { submit: { at: AT(), ...over } });

  it("a played draft is submitted, ready or not accepted", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(submit(db, "played"));
    await assertSucceeds(submit(db, "rejected"));
  });

  it("an unplayed draft is never submitted", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(submit(db, "ready"));
  });

  it("not while building, not by the family, nothing extra", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(submit(db, "building"));
    await env.withSecurityRulesDisabled(async (ctx) => {
      await updateDoc(doc(ctx.firestore(), "drafts/played"), {
        family_uids: ["grown", "kid"],
      });
    });
    await assertFails(submit(player(env, "kid").firestore(), "played"));
    await assertFails(submit(db, "played", { note: "no" }));
  });
});

describe("drafts last_played_at", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      [
        "drafts/d1",
        draftDoc(
          "grown",
          {
            status: "ready",
            family_uids: ["grown", "kid"],
            last_played_at: agoSecs(3600),
          },
        ),
      ],
    ]);
  });

  it("the family moves it forward, never back or ahead", async () => {
    const kid = player(env, "kid").firestore();
    await assertSucceeds(
      updateDoc(doc(kid, "drafts/d1"), { last_played_at: agoSecs(5) }),
    );
    await assertFails(
      updateDoc(doc(kid, "drafts/d1"), { last_played_at: agoSecs(7200) }),
    );
    await assertFails(
      updateDoc(doc(kid, "drafts/d1"), { last_played_at: inSecs(3600) }),
    );
  });

  it("strangers do not touch it", async () => {
    const db = player(env, "other").firestore();
    await assertFails(
      updateDoc(doc(db, "drafts/d1"), { last_played_at: agoSecs(5) }),
    );
  });
});

describe("drafts feedback", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["drafts/ready", draftDoc("grown", { status: "ready" })],
      ["drafts/review", draftDoc("grown", { status: "in_review" })],
      ["drafts/rejected", draftDoc("grown", { status: "not_accepted" })],
      ["drafts/building", draftDoc("grown", { status: "building" })],
      [
        "drafts/heard",
        draftDoc(
          "grown",
          {
            status: "ready",
            feedback: { kind: "problem", reason: "other", at: agoSecs(60) },
          },
        ),
      ],
    ]);
  });

  const feedback = (db, id, kind, reason, over = {}) =>
    updateDoc(doc(db, `drafts/${id}`), {
      feedback: { kind, reason, at: AT(), ...over },
    });

  it("each kind goes with its reasons", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(feedback(db, "ready", "not_right", "looks_wrong"));
    await assertSucceeds(feedback(db, "review", "problem", "scary_or_upsetting"));
    await assertSucceeds(feedback(db, "rejected", "ask_person", "still_wrong"));
  });

  it("once per draft, never while building", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(feedback(db, "heard", "problem", "other"));
    await assertFails(feedback(db, "building", "problem", "other"));
  });

  it("reasons stay with their kind, the note holds 300", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(feedback(db, "ready", "problem", "looks_wrong"));
    await assertFails(feedback(db, "ready", "ask_person", "other"));
    await assertFails(feedback(db, "ready", "complaint", "other"));
    await assertSucceeds(
      feedback(db, "ready", "not_right", "other", { note: "x".repeat(300) }),
    );
    await assertFails(
      feedback(db, "review", "problem", "other", { note: "x".repeat(301) }),
    );
  });

  it("only the owner reports", async () => {
    await env.withSecurityRulesDisabled(async (ctx) => {
      await updateDoc(doc(ctx.firestore(), "drafts/ready"), {
        family_uids: ["grown", "kid"],
      });
    });
    await assertFails(
      feedback(player(env, "kid").firestore(), "ready", "problem", "other"),
    );
  });
});

describe("drafts try again", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["drafts/d1", draftDoc("grown", { status: "failed", error: "build_failed" })],
      ["drafts/d2", draftDoc("grown", { status: "ready" })],
      ["drafts/d3", draftDoc("grown", { status: "failed", error: "build_failed" })],
    ]);
  });

  it("the owner retries a failed draft, nothing else", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(updateDoc(doc(db, "drafts/d1"), { status: "requested" }));
    await assertFails(updateDoc(doc(db, "drafts/d2"), { status: "requested" }));
    await assertFails(updateDoc(doc(db, "drafts/d3"), { status: "ready" }));
  });

  it("strangers and family do not retry", async () => {
    await assertFails(
      updateDoc(doc(player(env, "other").firestore(), "drafts/d1"), {
        status: "requested",
      }),
    );
    await assertFails(
      updateDoc(doc(player(env, "kid").firestore(), "drafts/d1"), {
        status: "requested",
      }),
    );
  });
});

describe("drafts delete", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["drafts/ready", draftDoc("grown", { status: "ready" })],
      ["drafts/review", draftDoc("grown", { status: "in_review" })],
      ["drafts/gone", draftDoc("grown", { status: "failed" })],
    ]);
  });

  it("the owner deletes, except in review", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertSucceeds(deleteDoc(doc(db, "drafts/ready")));
    await assertSucceeds(deleteDoc(doc(db, "drafts/gone")));
    await assertFails(deleteDoc(doc(db, "drafts/review")));
    await assertFails(deleteDoc(doc(grownUp(env, "other").firestore(), "drafts/ready")));
  });
});

describe("feed, public places, jobs, config", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["drafts/d1", draftDoc("grown", { family_uids: ["grown", "kid"] })],
      ["drafts/d1/feed/1", { round: 1, stage: "land" }],
      ["places_public/fi_repovesi", { status: "public", place_id: "fi_repovesi" }],
      ["jobs/j1", { draft_id: "d1" }],
      ["config/builder", { enabled: true, accepting_requests: true, message: "" }],
      ["admins/chief", {}],
    ]);
  });

  it("the family reads the feed, nobody writes it", async () => {
    await assertSucceeds(
      getDoc(doc(player(env, "kid").firestore(), "drafts/d1/feed/1")),
    );
    await assertSucceeds(
      getDoc(doc(grownUp(env, "chief").firestore(), "drafts/d1/feed/1")),
    );
    await assertFails(
      getDoc(doc(player(env, "other").firestore(), "drafts/d1/feed/1")),
    );
    const db = grownUp(env, "grown").firestore();
    await assertFails(setDoc(doc(db, "drafts/d1/feed/2"), { round: 1 }));
    await assertFails(updateDoc(doc(db, "drafts/d1/feed/1"), { round: 2 }));
    await assertFails(deleteDoc(doc(db, "drafts/d1/feed/1")));
  });

  it("a feed without its draft is a clean deny", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(getDoc(doc(db, "drafts/gone/feed/1")));
  });

  it("anyone signed in reads public places, nobody writes them", async () => {
    await assertSucceeds(
      getDoc(doc(player(env, "anon").firestore(), "places_public/fi_repovesi")),
    );
    await assertFails(
      getDoc(
        doc(env.unauthenticatedContext().firestore(), "places_public/fi_repovesi"),
      ),
    );
    const db = grownUp(env, "grown").firestore();
    await assertFails(
      setDoc(doc(db, "places_public/fi_x"), { status: "public" }),
    );
    await assertFails(
      updateDoc(doc(db, "places_public/fi_repovesi"), { status: "public" }),
    );
  });

  it("jobs stay hidden from every client", async () => {
    const db = grownUp(env, "grown").firestore();
    await assertFails(getDoc(doc(db, "jobs/j1")));
    await assertFails(setDoc(doc(db, "jobs/j2"), { draft_id: "d1" }));
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

describe("admins, inbox, wishes", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await seed(env, [
      ["admins/chief", {}],
      ["admin_inbox/i1", { kind: "submitted", draft_id: "d1" }],
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

  it("one vote per account and area", async () => {
    await assertSucceeds(
      setDoc(doc(grownUp(env, "grown").firestore(), "wishes/fi_x/votes/grown"), {
        at: AT(),
      }),
    );
    await assertSucceeds(
      setDoc(doc(player(env, "anon").firestore(), "wishes/fi_x/votes/anon"), {
        at: AT(),
      }),
    );
    await assertFails(
      setDoc(doc(grownUp(env, "grown").firestore(), "wishes/fi_x/votes/other"), {
        at: AT(),
      }),
    );
    await assertFails(
      setDoc(
        doc(env.unauthenticatedContext().firestore(), "wishes/fi_x/votes/x"),
        { at: AT() },
      ),
    );
    await assertFails(
      setDoc(doc(grownUp(env, "grown").firestore(), "wishes/fi_x/votes/grown"), {
        at: AT(),
        extra: 1,
      }),
    );
  });

  it("admins ask the worker through admin actions", async () => {
    const chief = grownUp(env, "chief").firestore();
    const ask = (id, data) => setDoc(doc(chief, `admin_actions/${id}`), data);
    await assertSucceeds(
      ask("a1", {
        admin_uid: "chief",
        draft_id: "d1",
        action: "send_on",
        at: AT(),
      }),
    );
    await assertSucceeds(
      ask("a2", {
        admin_uid: "chief",
        draft_id: "d1",
        action: "not_accepted",
        reason: "Too close to a public place.",
        at: AT(),
      }),
    );
    await assertSucceeds(
      ask("a3", {
        admin_uid: "chief",
        draft_id: "d1",
        action: "remove",
        answer: "Thank you, this place is gone now.",
        at: AT(),
      }),
    );
    await assertFails(
      ask("a4", {
        admin_uid: "chief",
        draft_id: "d1",
        action: "not_accepted",
        at: AT(),
      }),
    );
    await assertFails(
      ask("a5", {
        admin_uid: "chief",
        draft_id: "d1",
        action: "remove",
        at: AT(),
      }),
    );
    await assertFails(
      ask("a6", {
        admin_uid: "chief",
        draft_id: "d1",
        action: "launch",
        at: AT(),
      }),
    );
    await assertFails(
      ask("a7", {
        admin_uid: "chief",
        draft_id: "d1",
        action: "not_accepted",
        reason: "x".repeat(301),
        at: AT(),
      }),
    );
    await assertFails(
      ask("a8", {
        admin_uid: "grown",
        draft_id: "d1",
        action: "send_on",
        at: AT(),
      }),
    );
  });

  it("only admins read admin actions, nobody rewrites them", async () => {
    await seed(env, [
      [
        "admin_actions/a1",
        { admin_uid: "chief", draft_id: "d1", action: "send_on", at: agoSecs(60) },
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
        draft_id: "d1",
        action: "send_on",
        at: AT(),
      }),
    );
    const chief = grownUp(env, "chief").firestore();
    await assertFails(
      updateDoc(doc(chief, "admin_actions/a1"), { action: "published" }),
    );
    await assertFails(deleteDoc(doc(chief, "admin_actions/a1")));
  });

  it("votes never change, only admins count them", async () => {
    await seed(env, [["wishes/fi_x/votes/grown", { at: agoSecs(60) }]]);
    const db = grownUp(env, "grown").firestore();
    await assertFails(
      updateDoc(doc(db, "wishes/fi_x/votes/grown"), { at: AT() }),
    );
    await assertFails(deleteDoc(doc(db, "wishes/fi_x/votes/grown")));
    await assertSucceeds(
      getDoc(doc(grownUp(env, "chief").firestore(), "wishes/fi_x/votes/grown")),
    );
    await assertFails(getDoc(doc(db, "wishes/fi_x/votes/grown")));
  });
});
