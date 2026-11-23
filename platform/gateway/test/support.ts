/**
 * Shared test helpers: keyrings, source records like the ones the binder will pass, and the
 * egress door's side of a metered call.
 */
import { keyring } from "../../ci/fixtures";
import { builtinCatalog } from "../src/catalog";
import type { Credential } from "../src/credential";
import type { Meter, Source } from "../../contract/src/egress-scope";
import type { Metering, Reserve } from "../../contract/src/gateway";
import { EGRESS_BODY_MAX } from "../../contract/src/limits";
import { type Binding, type KeyringEnv, seal } from "../src/keyring";
import { learnMeter, meteredRecord } from "../src/meter";

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
  upstream: { provider: "google",
              apis: [{ api: "google-generative-ai",
                       base_url: "https://generativelanguage.googleapis.com/v1beta" }],
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

/** The first model of `record`'s provider and first API whose call pi-ai caps, so it is metered. */
export async function meterable(record: Source, key: string):
    Promise<{ id: string; meter: Meter }> {
  const catalog = builtinCatalog();
  const [row] = record.upstream.apis;
  for (const candidate of catalog.provider(record.upstream.provider)!.getModels()
    .filter((model) => model.api === row!.api)) {
    const meter = await learnMeter(catalog, record.upstream, row!, key,
                                   meteredRecord(catalog, record.upstream, row!, candidate.id)!);
    if (meter !== null) {
      return { id: candidate.id, meter };
    }
  }
  throw new Error(`no metered ${row!.api} model in the catalog`);
}

/** A reserve that grants each call it is asked for, as asked, recording each ask. */
export function granting(asked: unknown[] = [], fields: Partial<Metering> = {}): Reserve {
  return async (call) => {
    asked.push(call);
    return { metering: { max_output_tokens: 100, input_bytes: EGRESS_BODY_MAX, model: call.model,
                         tier: call.tier, routed: false, ...fields } };
  };
}

/** A model call's `POST`, its `Content-Length` declared as the egress door hands it on. */
export function posted(path: string, body: string, headers: Record<string, string> = {}):
    Request {
  return new Request(`https://egress${path}`, {
    method: "POST", body,
    headers: { "content-length": String(new TextEncoder().encode(body).byteLength),
               ...headers } });
}

/** A forwarded call's outcome, once its answer ended. */
export async function outcome(answer: { outcome: ReadableStream<Uint8Array> }):
    Promise<unknown> {
  return JSON.parse(await new Response(answer.outcome).text());
}
