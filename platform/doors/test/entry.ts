/**
 * The test Worker: the doors, served with the fake upstream sign-in and the bundled
 * source records plus one test OAuth source (`TEST_OAUTH`, an RFC 6749 server the fake
 * providers run), and the real `OwnerDO` beside them, so the config's OWNER (by
 * script_name in production) is bound to the real object in one script
 * (vitest.config.ts).
 */
import type { DoorsEnv } from "../src/config";
import { serve } from "../src/doors";
import { bundledRecords, catalogOf } from "../src/sources";
import { FAKE_UPSTREAM } from "./fake-upstream";

export { OwnerDO } from "../../owner/src/owner-do";
export { ThreadStore } from "../../owner/src/thread-store";

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
