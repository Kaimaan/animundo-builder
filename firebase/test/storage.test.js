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
import { draftDoc, grownUp, makeEnv, player, seed } from "./helper.js";

const FILE = "packs/d1/1/regions/draft_d1.json";

describe("packs", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await env.clearStorage();
    await seed(env, [["drafts/d1", draftDoc("grown", { family_uids: ["grown", "kid"] })]]);
    await env.withSecurityRulesDisabled(async (ctx) => {
      await uploadBytes(ref(ctx.storage(), FILE), new TextEncoder().encode("{}"));
    });
  });

  it("the family reads its pack, nobody else does", async () => {
    await assertSucceeds(getBytes(ref(grownUp(env, "grown").storage(), FILE)));
    await assertSucceeds(getBytes(ref(player(env, "kid").storage(), FILE)));
    await assertFails(getBytes(ref(player(env, "other").storage(), FILE)));
    await assertFails(
      getBytes(ref(env.unauthenticatedContext().storage(), FILE)),
    );
  });

  it("admins read packs, and a missing draft is a clean deny", async () => {
    await seed(env, [["admins/chief", {}]]);
    await assertSucceeds(getBytes(ref(grownUp(env, "chief").storage(), FILE)));
    await assertFails(
      getBytes(ref(player(env, "other").storage(), "packs/gone/1/f.json")),
    );
  });

  it("nobody writes or lists from a client", async () => {
    const storage = grownUp(env, "grown").storage();
    await assertFails(
      uploadBytes(ref(storage, FILE), new TextEncoder().encode("{}")),
    );
    await assertFails(deleteObject(ref(storage, FILE)));
    await assertFails(listAll(ref(storage, "packs/d1/1")));
  });
});
