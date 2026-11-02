/**
 * Shared test helpers: keyrings, and source records like the ones the binder will pass.
 */
import type { Credential, Source } from "../src/credential";
import { type Binding, seal } from "../src/keyring";

export type Env = { GATEWAY_KEYRING: string };

function randomKey(): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
}

/** A keyring env holding `kids` (each a fresh random key) with `active` active. */
export function ring(active: string, kids: readonly string[] = [active],
                     keys: Record<string, string> = {}): Env {
  const all: Record<string, string> = {};
  for (const kid of kids) {
    all[kid] = keys[kid] ?? randomKey();
  }
  return { GATEWAY_KEYRING: JSON.stringify({ active, keys: all }) };
}

/** The same keys as `env`, with `active` active. */
export function rotate(env: Env, active: string, extra: readonly string[] = []): Env {
  const parsed = JSON.parse(env.GATEWAY_KEYRING) as { keys: Record<string, string> };
  return ring(active, [...Object.keys(parsed.keys), ...extra], parsed.keys);
}

export const ALICE: Binding = Object.freeze({ owner: "alice", connection: "c1", version: 1 });

export const ANTHROPIC: Source = Object.freeze({
  upstream: { provider: "anthropic", api: "anthropic-messages", base_url: "https://api.anthropic.com",
              auth: { header: "x-api-key", scheme: "" },
              verify_url: "https://api.anthropic.com/v1/models" },
  endpoints: [{ origin: "https://api.anthropic.com", paths: ["/v1/messages", "/v1/models"],
                methods: ["GET", "POST"] }],
});

export const OPENAI: Source = Object.freeze({
  upstream: { provider: "openai", api: "openai-responses", base_url: "https://api.openai.com/v1",
              auth: { header: "authorization", scheme: "Bearer" } },
  endpoints: [{ origin: "https://api.openai.com", paths: ["/v1/responses", "/v1/models"],
                methods: ["GET", "POST"] }],
});

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
