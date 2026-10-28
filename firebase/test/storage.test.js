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
import { grownUp, judgeBuildDoc, makeEnv, player, seed } from "./helper.js";

const FILE = "previews/b1/1/manifest.json";

describe("previews", () => {
  let env;
  before(async () => (env = await makeEnv()));
  after(async () => env.cleanup());
  beforeEach(async () => {
    await env.clearFirestore();
    await env.clearStorage();
    await seed(env, [
      ["judge_builds/b1", judgeBuildDoc("judge1")],
      ["admins/chief", {}],
    ]);
    await env.withSecurityRulesDisabled(async (ctx) => {
      await uploadBytes(ref(ctx.storage(), FILE), new TextEncoder().encode("{}"));
    });
  });

  it("only that judge and admins read the preview", async () => {
    await assertSucceeds(getBytes(ref(grownUp(env, "judge1").storage(), FILE)));
    await assertSucceeds(getBytes(ref(grownUp(env, "chief").storage(), FILE)));
    await assertFails(getBytes(ref(grownUp(env, "judge2").storage(), FILE)));
    await assertFails(getBytes(ref(player(env, "kid").storage(), FILE)));
    await assertFails(
      getBytes(ref(env.unauthenticatedContext().storage(), FILE)),
    );
  });

  it("a missing build is a clean deny", async () => {
    await assertFails(
      getBytes(ref(player(env, "kid").storage(), "previews/gone/1/f.json")),
    );
  });

  it("nobody writes or lists from a client", async () => {
    const storage = grownUp(env, "judge1").storage();
    await assertFails(
      uploadBytes(ref(storage, FILE), new TextEncoder().encode("{}")),
    );
    await assertFails(deleteObject(ref(storage, FILE)));
    await assertFails(listAll(ref(storage, "previews/b1/1")));
  });
});
