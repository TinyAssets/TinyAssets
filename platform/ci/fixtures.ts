/**
 * Test fixtures more than one workspace uses, in Node and in workerd alike: the founder's
 * tier numbers and the control records the tests write, keyrings, and the model sources the
 * gateway's fake upstream answers for. Its runtime imports are the sealed envelope, the
 * control record's module and the dark record, by their file names, which import only by file
 * name too, so Node's type stripping runs them (`doors/test/app.e2e.mjs` and the owner's
 * memory leg import this file so).
 */
import { CONTROL_KEY } from "../contract/src/control.ts";
import type { Source } from "../contract/src/egress-scope";
import { base64, fromBase64, KEY_BYTES, ringText } from "../contract/src/envelope.ts";
import dark from "../deploy/control.json" with { type: "json" };

/**
 * The control record a first deploy writes when there is none (`deploy/control.json`): every
 * outside door closed, no canary owner, and the founder's tier numbers.
 */
export const DARK = Object.freeze(dark);

/** The founder's tier numbers (ADR-034 decision 8), as the dark control record holds them. */
export const TIERS = Object.freeze(dark.tiers);

/**
 * How long a test waits for a call to reach a gate (`ci/gate.ts` `GateControl.held`): well
 * inside a test's budget, so a hold the code never reaches fails as an empty list, not as a
 * test timeout.
 */
export const HELD_WAIT_MS = 2_000;

/** The control record's gates with every door open: everyone outside, no canary list. */
export const OPEN_GATES = Object.freeze({ outside: "on", ordinary: "all", canary: [],
                                          inbound: false });

/** The control record with every door open, and the founder's tiers. */
export const OPEN = Object.freeze({ ...OPEN_GATES, tiers: TIERS });

/**
 * The control record written to its namespace (`store`), as the founder writes it: a string
 * as it is (a value that is no JSON), anything else as JSON; null removes it.
 */
export async function writeControl(store: Pick<KVNamespace, "put" | "delete">,
                                   record: unknown): Promise<void> {
  if (record === null) {
    await store.delete(CONTROL_KEY);
  } else {
    await store.put(CONTROL_KEY, typeof record === "string" ? record : JSON.stringify(record));
  }
}

/** A fresh random 32-byte key, base64, as a keyring holds it. */
export function ringKey(): string {
  return base64(crypto.getRandomValues(new Uint8Array(KEY_BYTES)));
}

/**
 * A keyring's secret, as the sealed envelope writes one (`contract/src/envelope.ts`, the
 * ring `gateway/src/keyring.ts` and `files/src/datakey.ts` read): `kids`, each a fresh key
 * unless `keys` gives one, `active` the one that seals. A rotated ring lists an older kid
 * before the active one.
 */
export function keyring(active = "k1", kids: readonly string[] = [active],
                        keys: Readonly<Record<string, string>> = {}): string {
  return ringText({ active, keys: new Map(
    kids.map((kid) => [kid, fromBase64(keys[kid] ?? ringKey())])) });
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
