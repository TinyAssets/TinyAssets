/**
 * The source records the tests of an RFC 6749 source serve (`connect.test.ts`, the browser
 * test's harness `e2e-entry.ts`): no record the platform bundles is one, so no config gives
 * the doors' own entry one, and these tests call the doors' router with this catalog.
 */
import { bundledRecords, catalogOf } from "../../sources/src/catalog";

/** An RFC 6749 source, run by the fake providers (`auth.test`). */
const TEST_OAUTH = Object.freeze({
  id: "test_oauth",
  display_name: "Test OAuth",
  connect: ["oauth"],
  provider: "openai",
  apis: [{ api: "openai-responses", base_url: "https://api.openai.com/v1" }],
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

/** The bundled records and the test OAuth source beside them. */
export const TEST_SOURCES = catalogOf([...bundledRecords(), TEST_OAUTH]);
