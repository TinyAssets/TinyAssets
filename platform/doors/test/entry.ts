/**
 * The test Worker: the doors, served with the fake upstream sign-in and the bundled
 * source records plus one test OAuth source (`TEST_OAUTH`, an RFC 6749 server the fake
 * providers run), and the real `OwnerDO` beside them, so the config's OWNER (by
 * script_name in production) is bound to the real object in one script
 * (vitest.config.ts), which binds its SANDBOX to the owner's fake sandbox Worker.
 */
import type { DoorsEnv } from "../src/env";
import { serve } from "../src/doors";
import { bundledRecords, catalogOf } from "../../sources/src/catalog";
import { FAKE_UPSTREAM } from "./fake-upstream";

import { OwnerDO as RealOwnerDO } from "../../owner/src/owner-do";
import type { Refusal } from "../../contract/src/refusal";

/** The owner object's env, as its class takes it. */
type OwnerEnv = ConstructorParameters<typeof RealOwnerDO>[1];

/** The doors a test may have refused once, as the object's own. */
type Refusable = "outsideAgents" | "toolProfile" | "fileUploadTarget" | "erasureOutside"
  | "runEvents";

/**
 * An owner's next call of a door (`agents_list`, the tool profile, an upload's target),
 * refused with the refusal kept here, thrown in the object and carried across its RPC to
 * the doors (as a full slot pool refuses it `busy`, `contract/src/slots.ts`): the tests of
 * the doors' statuses, `Retry-After` and the refusal's transport, on each surface.
 */
const REFUSE_ONCE = new Map<string, Refusal>();

export function refuseOnce(door: Refusable, owner: string, refusal: Refusal): void {
  REFUSE_ONCE.set(`${door} ${owner}`, refusal);
}

/** The refusal kept for `owner`'s next call of `door`, taken once. */
function refusedOnce(door: Refusable, owner: string): Refusal | undefined {
  const refusal = REFUSE_ONCE.get(`${door} ${owner}`);
  REFUSE_ONCE.delete(`${door} ${owner}`);
  return refusal;
}

export class OwnerDO extends RealOwnerDO {
  override outsideAgents(...args: Parameters<RealOwnerDO["outsideAgents"]>):
      ReturnType<RealOwnerDO["outsideAgents"]> {
    const refusal = refusedOnce("outsideAgents", args[0].owner);
    return refusal !== undefined ? Promise.reject(refusal) : super.outsideAgents(...args);
  }

  override toolProfile(...args: Parameters<RealOwnerDO["toolProfile"]>):
      ReturnType<RealOwnerDO["toolProfile"]> {
    const refusal = refusedOnce("toolProfile", args[0].owner);
    return refusal !== undefined ? Promise.reject(refusal) : super.toolProfile(...args);
  }

  override erasureOutside(...args: Parameters<RealOwnerDO["erasureOutside"]>):
      ReturnType<RealOwnerDO["erasureOutside"]> {
    const refusal = refusedOnce("erasureOutside", args[0].owner);
    return refusal !== undefined ? Promise.reject(refusal) : super.erasureOutside(...args);
  }

  override runEvents(...args: Parameters<RealOwnerDO["runEvents"]>):
      ReturnType<RealOwnerDO["runEvents"]> {
    const refusal = refusedOnce("runEvents", args[0].owner);
    return refusal !== undefined ? Promise.reject(refusal) : super.runEvents(...args);
  }

  override fileUploadTarget(...args: Parameters<RealOwnerDO["fileUploadTarget"]>):
      ReturnType<RealOwnerDO["fileUploadTarget"]> {
    const refusal = refusedOnce("fileUploadTarget", args[0].owner);
    return refusal !== undefined ? Promise.reject(refusal) : super.fileUploadTarget(...args);
  }

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    // The owner Worker's FILES, the files Worker's whole service: this script's FILES is the
    // doors', its upload entrypoint alone (review M5-1).
    const files = (env as unknown as { OWNER_FILES: OwnerEnv["FILES"] }).OWNER_FILES;
    super(ctx, { ...env, FILES: files });
  }
}

/** An RFC 6749 source: no bundled record is one, so the flow is tested on this one. */
export const TEST_OAUTH = Object.freeze({
  id: "test_oauth",
  display_name: "Test OAuth",
  connect: ["oauth"],
  provider: "openai",
  api: "openai-responses",
  base_url: "https://api.openai.com/v1",
  auth: { header: "authorization", scheme: "Bearer" },
  verify_url: "https://api.openai.com/v1/models",
  engines: [],
  endpoints: [{ origin: "https://api.openai.com", paths: ["/v1/responses"], methods: ["POST"] },
              { origin: "https://api.openai.com", paths: ["/v1/models"], methods: ["GET"] }],
  oauth: {
    protocol: "rfc6749",
    authorization_endpoint: "https://auth.test/authorize",
    token_endpoint: "https://auth.test/token",
    client_id: "tinyassets-test",
    client_auth: { method: "none" },
    scope: "models.read",
    revocation_endpoint: "https://auth.test/revoke",
    issuer: "https://auth.test",
    authorization_response_iss_parameter_supported: true,
    authorize_params: { prompt: "consent" },
  },
});

export const TEST_SOURCES = catalogOf([...bundledRecords(), TEST_OAUTH]);

export default {
  fetch: (request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> =>
    serve(request, env, ctx, FAKE_UPSTREAM, TEST_SOURCES),
};
