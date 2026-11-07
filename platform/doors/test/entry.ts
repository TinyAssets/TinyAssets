/**
 * The test Worker: the doors, served with the fake upstream sign-in and the bundled
 * source records plus one test OAuth source (`TEST_OAUTH`, an RFC 6749 server the fake
 * providers run), and the real `OwnerDO` beside them, so the config's OWNER (by
 * script_name in production) is bound to the real object in one script
 * (vitest.config.ts).
 */
import type { DoorsEnv } from "../src/config";
import { serve } from "../src/doors";
import { bundledRecords, catalogOf } from "../../sources/src/catalog";
import { FAKE_UPSTREAM } from "./fake-upstream";

import { type OwnerEnv, OwnerDO as RealOwnerDO, type SandboxLeases } from "../../owner/src/owner-do";
import type { Refusal } from "../../contract/src/refusal";
import { RecordingErasures } from "../../owner/test/erasure-fake";
import { RecordingSandbox } from "../../owner/test/sandbox-fake";

/**
 * The real owner object, with its sandbox's lease API (owner-internal) in the tests' reach,
 * and a recording fake behind its `SANDBOX` binding (tests only: slice S binds the real
 * sandbox Worker).
 */
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

  readonly sandbox: SandboxLeases;
  readonly recorder: RecordingSandbox;
  /** The erasure queue, recording only: the tests run the doors' consumer on what it sent. */
  readonly erasures: RecordingErasures;

  constructor(ctx: DurableObjectState, env: OwnerEnv) {
    const made: { sandbox?: SandboxLeases } = {};
    const recorder = new RecordingSandbox();
    const erasures = new RecordingErasures();
    // The owner Worker's FILES, the files Worker's whole service: this script's FILES is the
    // doors', its upload entrypoint alone (review M5-1).
    const files = (env as unknown as { OWNER_FILES: OwnerEnv["FILES"] }).OWNER_FILES;
    super(ctx, { ...env, FILES: files, SANDBOX: recorder, ERASURES: erasures },
          { sandbox: (leases) => { made.sandbox = leases; }, putGraceMs: 0, eraseRetryMs: 500 });
    this.erasures = erasures;
    this.sandbox = made.sandbox!;
    this.recorder = recorder;
  }
}

/**
 * A served RFC 6749 source: the bundled records' only one is Sign in with ChatGPT,
 * unwired until OpenAI's approval, so the flow is tested on this one.
 */
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
    refresh_with: "rfc6749",
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
