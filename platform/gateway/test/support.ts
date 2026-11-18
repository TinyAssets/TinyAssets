/**
 * Gateway test fixtures for keyrings, sources and the fake upstream's observations.
 */
import { until } from "../../ci/workerd";
import { keyring } from "../../ci/fixtures";
import type { Credential } from "../src/credential";
import type { Source } from "../../contract/src/egress-scope";
import { type Binding, type KeyringEnv, seal } from "../src/keyring";

export { ANTHROPIC, OPENAI } from "../../ci/fixtures";

/** A keyring env holding `kids` (each a fresh random key) with `active` active. */
export function ring(active: string, kids: readonly string[] = [active],
                     keys: Record<string, string> = {}): KeyringEnv {
  return { GATEWAY_KEYRING: keyring(active, kids, keys) };
}

/** The same keys as `env`, with `active` active. */
export function rotate(env: KeyringEnv, active: string,
                       extra: readonly string[] = []): KeyringEnv {
  const parsed = JSON.parse(env.GATEWAY_KEYRING) as { keys: Record<string, string> };
  return ring(active, [...Object.keys(parsed.keys), ...extra], parsed.keys);
}

/** Two model connections' ids, as the owner's object mints them (`contract/src/ids.ts`). */
export const C1 = `c${"1".repeat(24)}`;
export const C2 = `c${"2".repeat(24)}`;

export const ALICE: Binding = Object.freeze({ owner: "alice", connection: C1, version: 1 });

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

export async function sealed(env: KeyringEnv, bound: Binding, secret: string,
                             record: Source): Promise<string> {
  return (await seal(env, bound, apiKey(secret, record))).blob;
}

/** Wait on the fake upstream's observation of this request. Cancellation keeps its two-second polling budget. */
export function waitForProvider(id: string, observation: "arrived" | "cancelled"): Promise<void> {
  return until(async () => {
    const answer = await fetch(`https://status.test/cancelled?id=${encodeURIComponent(id)}`);
    const state = await answer.json() as { arrived: boolean; cancelled: boolean | null };
    return state[observation] === true;
  }, `provider ${observation}: ${id}`, observation === "cancelled" ? 200 : undefined);
}
