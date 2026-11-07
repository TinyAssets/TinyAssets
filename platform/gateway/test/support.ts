/**
 * Shared test helpers: keyrings, and source records like the ones the binder will pass.
 */
import { keyring } from "../../ci/fixtures";
import type { Credential } from "../src/credential";
import type { Source } from "../../contract/src/egress-scope";
import { type Binding, seal } from "../src/keyring";

export { ANTHROPIC, OPENAI } from "../../ci/fixtures";

export type Env = { GATEWAY_KEYRING: string };

/** A keyring env holding `kids` (each a fresh random key) with `active` active. */
export function ring(active: string, kids: readonly string[] = [active],
                     keys: Record<string, string> = {}): Env {
  return { GATEWAY_KEYRING: keyring(active, kids, keys) };
}

/** The same keys as `env`, with `active` active. */
export function rotate(env: Env, active: string, extra: readonly string[] = []): Env {
  const parsed = JSON.parse(env.GATEWAY_KEYRING) as { keys: Record<string, string> };
  return ring(active, [...Object.keys(parsed.keys), ...extra], parsed.keys);
}

export const ALICE: Binding = Object.freeze({ owner: "alice", connection: "c1", version: 1 });

export const GOOGLE: Source = Object.freeze({
  upstream: { provider: "google", api: "google-generative-ai",
              base_url: "https://generativelanguage.googleapis.com/v1beta",
              auth: { header: "x-goog-api-key", scheme: "" } },
  endpoints: [{ origin: "https://generativelanguage.googleapis.com", paths: ["/v1beta/models/*"],
                methods: ["POST"] }],
});

/** An API-key credential for `record`. */
export function apiKey(secret: string, record: Source): Credential {
  return { kind: "api_key", secret, upstream: record.upstream, endpoints: record.endpoints,
           sealed_at: Date.now() };
}

export async function sealed(env: Env, bound: Binding, secret: string,
                             record: Source): Promise<string> {
  return (await seal(env, bound, apiKey(secret, record))).blob;
}
