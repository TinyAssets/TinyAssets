/**
 * Test fixtures more than one workspace uses, in Node and in workerd alike (no runtime
 * import): the founder's tier numbers and the control records the tests write, keyrings,
 * and the model sources the gateway's fake upstream answers for.
 */
import type { Source } from "../contract/src/egress-scope";

/** The founder's tier numbers (ADR-034 decision 8), as the control record holds them. */
export const TIERS = Object.freeze({
  free: Object.freeze({ runs: 5, storage_bytes: 25_000_000_000 }),
  paid: Object.freeze({ runs: 10, storage_bytes: 250_000_000_000 }),
});

/** The control record's gates with every door open: everyone outside, no canary list. */
export const OPEN_GATES = Object.freeze({ outside: "on", ordinary: "all", canary: [],
                                          inbound: false });

/** The control record with every door open, and the founder's tiers. */
export const OPEN = Object.freeze({ ...OPEN_GATES, tiers: TIERS });

/** A fresh random 32-byte key, base64, as a keyring holds it. */
export function ringKey(): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
}

/**
 * A keyring's secret (`{active, keys}`, the shape `gateway/src/keyring.ts` and
 * `files/src/datakey.ts` read): `kids`, each a fresh key unless `keys` gives one, `active`
 * the one that seals. A rotated ring lists an older kid before the active one.
 */
export function keyring(active = "k1", kids: readonly string[] = [active],
                        keys: Readonly<Record<string, string>> = {}): string {
  return JSON.stringify({ active, keys: Object.fromEntries(
    kids.map((kid) => [kid, keys[kid] ?? ringKey()])) });
}

/** Anthropic's API, as a source record gives it to the gateway. */
export const ANTHROPIC: Source = Object.freeze({
  upstream: { provider: "anthropic", api: "anthropic-messages", base_url: "https://api.anthropic.com",
              auth: { header: "x-api-key", scheme: "" },
              verify_url: "https://api.anthropic.com/v1/models" },
  endpoints: [{ origin: "https://api.anthropic.com", paths: ["/v1/messages", "/v1/models"],
                methods: ["GET", "POST"] }],
});

/** OpenAI's API, as a source record gives it to the gateway. */
export const OPENAI: Source = Object.freeze({
  upstream: { provider: "openai", api: "openai-responses", base_url: "https://api.openai.com/v1",
              auth: { header: "authorization", scheme: "Bearer" } },
  endpoints: [{ origin: "https://api.openai.com", paths: ["/v1/responses", "/v1/models"],
                methods: ["GET", "POST"] }],
});
