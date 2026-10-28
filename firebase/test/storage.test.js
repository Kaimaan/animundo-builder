import { after, before, beforeEach, describe, it } from "node:test";
import {
  assertFails,
  assertSucceeds,
} from "@firebase/rules-unit-testing";
import {
  deleteObject,
  getBytes,
  listAll,
  ref,
  uploadBytes,
} from "firebase/storage";
import { grownUp, jobDoc, makeEnv, player, seed } from "./helper.js";

const TILE = "tiles/v1/ease2_n25/51_37.json";
const INDEX = "tiles/v1/index.json";
const REVIEW = "review/job1/tundra-home/report.html";
const PREVIEW = "previews/job1/1/manifest.json";

describe("tiles and review batches", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await env.clearStorage();
    await seed(env, [["admins/chief", {}]]);
    await env.withSecurityRulesDisabled(async (ctx) => {
      const storage = ctx.storage();
      for (const file of [TILE, INDEX, REVIEW]) {
        await uploadBytes(ref(storage, file), new TextEncoder().encode("{}"));
      }
    });
  });

  it("anyone signed in reads tiles, visitors do not", async () => {
    await assertSucceeds(getBytes(ref(player(env, "kid").storage(), TILE)));
    await assertSucceeds(getBytes(ref(player(env, "kid").storage(), INDEX)));
    await assertSucceeds(
      getBytes(ref(grownUp(env, "grown").storage(), TILE)),
    );
    await assertFails(
      getBytes(ref(env.unauthenticatedContext().storage(), TILE)),
    );
  });

  it("only admins read review batches", async () => {
    await assertSucceeds(getBytes(ref(grownUp(env, "chief").storage(), REVIEW)));
    await assertFails(getBytes(ref(player(env, "kid").storage(), REVIEW)));
    await assertFails(
      getBytes(ref(grownUp(env, "grown").storage(), REVIEW)),
    );
  });

  it("nobody writes or lists from a client", async () => {
    const storage = grownUp(env, "chief").storage();
    await assertFails(
      uploadBytes(ref(storage, TILE), new TextEncoder().encode("{}")),
    );
    await assertFails(deleteObject(ref(storage, TILE)));
    await assertFails(listAll(ref(storage, "tiles/v1")));
    await assertFails(listAll(ref(storage, "review/job1")));
  });
});

describe("private previews", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await env.clearStorage();
    await seed(env, [
      ["build_jobs/job1", jobDoc("judge1")],
      ["admins/chief", {}],
    ]);
    await env.withSecurityRulesDisabled(async (ctx) => {
      await uploadBytes(ref(ctx.storage(), PREVIEW), new TextEncoder().encode("{}"));
    });
  });

  it("only that job's judge and admins read the preview", async () => {
    await assertSucceeds(getBytes(ref(grownUp(env, "judge1").storage(), PREVIEW)));
    await assertSucceeds(getBytes(ref(grownUp(env, "chief").storage(), PREVIEW)));
    await assertFails(getBytes(ref(grownUp(env, "judge2").storage(), PREVIEW)));
    await assertFails(getBytes(ref(player(env, "kid").storage(), PREVIEW)));
    await assertFails(
      getBytes(ref(env.unauthenticatedContext().storage(), PREVIEW)),
    );
  });

  it("a missing job is a clean deny", async () => {
    await assertFails(
      getBytes(ref(player(env, "kid").storage(), "previews/gone/1/f.json")),
    );
  });

  it("nobody writes or lists from a client", async () => {
    const storage = grownUp(env, "judge1").storage();
    await assertFails(
      uploadBytes(ref(storage, PREVIEW), new TextEncoder().encode("{}")),
    );
    await assertFails(deleteObject(ref(storage, PREVIEW)));
    await assertFails(listAll(ref(storage, "previews/job1/1")));
  });
});
