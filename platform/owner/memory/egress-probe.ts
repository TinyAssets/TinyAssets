/**
 * The workerd memory leg's egress probe (`egress.mjs`, egress shape research §5): the real
 * egress door (`serveEgress`) and the real gateway (`EgressGateway`) in their own isolates,
 * as production runs them, and a provider in a third, which reads each model call's body and
 * holds its answer.
 *
 * **Checkpoints.** The provider awaits the leg's driver after each MiB of a body it reads
 * (`upload`: the gateway is then waiting on its upload's backpressure) and once the body has
 * arrived (`held`: the gateway and the door are then waiting for the first token), so the
 * driver collects the door's and the gateway's isolates and reads them while both wait, with
 * none of their code running between the collection and the reading.
 *
 * Its three Workers, each its own entry of this one bundle, by `PROBE_ROLE`:
 * - `doors`: `serveEgress` itself, its owner's object a fake that admits every call and
 *   reserves it as asked (the owner's own rules have their own tests), its control record
 *   open; and `/hoard`, the control, which reads a body whole and holds it;
 * - `gateway`: the gateway's own `EgressGateway`, the blob sealed here (`/seal`), its pool's
 *   state (`/pool`), and `/parse`: a body read by the gateway's own `readHeld`, weighed by its
 *   own `parseWeight`, then parsed, serialized and encoded as the metered path does, a
 *   checkpoint after each (review H1: the weight must cover each phase);
 * - `upstream`: the provider.
 */
import { WorkerEntrypoint } from "cloudflare:workers";

import { OPEN } from "../../ci/fixtures";
import { EGRESS_ORIGIN } from "../src/network";
import { serveEgress } from "../../doors/src/egress";
import { LEASE_LIVED, mintToken } from "../../doors/src/routing-mac";
import { builtinCatalog } from "../../gateway/src/catalog";
import { secret, source } from "../../gateway/src/credential";
import { GATEWAY_POOL_SLOTS, weighedBody } from "../../gateway/src/held";
import { binding, seal } from "../../gateway/src/keyring";
import { learnMeter, meteredRecord } from "../../gateway/src/meter";
import { isolatePool } from "../src/slots";

export { EgressGateway } from "../../gateway/src/index";

declare function gc(): void;

interface Env {
  readonly PROBE_ROLE: "doors" | "gateway" | "upstream";
  readonly DRIVER: Fetcher;
  readonly EGRESS?: unknown;
  readonly GATEWAY_KEYRING?: string;
  readonly ROUTING_MAC_KEY?: string;
}

const MIB = 1024 * 1024;
const OWNER = `user_${"a".repeat(32)}`;
const CONNECTION = `c${"b".repeat(24)}`;
const RUN = `run_${"c".repeat(32)}`;
const SECRET = "d".repeat(64);

/** The provider's record: Anthropic's Messages API, its key sealed by this gateway. */
const ANTHROPIC = Object.freeze({
  upstream: { provider: "anthropic", api: "anthropic-messages",
              base_url: "https://api.anthropic.com",
              auth: { header: "x-api-key", scheme: "" } },
  endpoints: [{ origin: "https://api.anthropic.com", paths: ["/v1/messages"], methods: ["POST"] }],
});

async function collected(): Promise<Response> {
  // A full collection, a moment for the freed backing stores to be swept, then another.
  gc();
  await scheduler.wait(5);
  gc();
  return new Response("gc");
}

async function checkpoint(env: Env, phase: string, call: string): Promise<void> {
  await (await env.DRIVER.fetch(`http://driver/checkpoint?phase=${phase}&call=${call}`)).text();
}

/** The door's owner object, as far as the egress door asks of it: every call admitted. */
function owner(blob: string) {
  return {
    egressOpen: async () => ({
      ok: true, reservation: "e".repeat(32), connection: CONNECTION, blob, version: 1,
      generation: 1, metered: true, ended: new IdentityTransformStream().readable }),
    egressReserve: async (_binding: unknown, call: { model: string; tier: string | null }) => ({
      ok: true, metering: { max_output_tokens: 1000, input_bytes: 64 * MIB, model: call.model,
                            tier: call.tier } }),
    egressBeat: async () => ({ beat: true }),
    egressSettle: async () => ({ settled: true }),
  };
}

async function doors(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/gc") {
    return collected();
  }
  if (url.pathname === "/token") {
    return Response.json({ token: mintToken(env.ROUTING_MAC_KEY, "egress", OWNER, LEASE_LIVED)
      .token, path: `/r/${RUN}/${SECRET}/c/${CONNECTION}/v1/messages` });
  }
  if (url.pathname === "/hoard") {
    // The control: the body read whole and held, as the door did before it streamed.
    const kept = await request.arrayBuffer();
    await checkpoint(env, "held", request.headers.get("x-probe-call") ?? "0");
    return Response.json({ length: kept.byteLength });
  }
  const blob = request.headers.get("x-ta-probe-blob") ?? "";
  const doorsEnv = { ...env, CONTROL: { get: async () => OPEN },
                     OWNER: { getByName: () => owner(blob) } };
  return serveEgress(new Request(`${EGRESS_ORIGIN}${url.pathname}`, request),
                     doorsEnv as never, ctx);
}

async function upstream(request: Request, env: Env): Promise<Response> {
  if (new URL(request.url).pathname === "/gc") {
    return collected();
  }
  const call = request.headers.get("x-probe-call") ?? "0";
  const reader = request.body!.getReader();
  let read = 0;
  let next = MIB;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    read += value.byteLength;
    if (read >= next) {
      await checkpoint(env, "upload", call);
      next += MIB;
    }
  }
  await checkpoint(env, "held", call);
  const event = (type: string, data: unknown) =>
    `event: ${type}\ndata: ${JSON.stringify({ type, ...data as object })}\n\n`;
  return new Response(event("message_start", { message: {
    id: "m", type: "message", role: "assistant", model: "probe", content: [],
    stop_reason: null, usage: { input_tokens: 10, output_tokens: 1 } } })
    + event("message_stop", {}), { headers: { "content-type": "text/event-stream",
                                              "x-probe-read": String(read) } });
}

export default class Probe extends WorkerEntrypoint<Env> {
  override async fetch(request: Request): Promise<Response> {
    if (this.env.PROBE_ROLE === "doors") {
      return doors(request, this.env, this.ctx);
    }
    if (this.env.PROBE_ROLE === "upstream") {
      return upstream(request, this.env);
    }
    const url = new URL(request.url);
    if (url.pathname === "/gc") {
      return collected();
    }
    if (url.pathname === "/parse") {
      const call = url.searchParams.get("call") ?? "0";
      // As `prepared` holds them: the text and the tree, then the tree and the output, then
      // the output and its bytes; the read's own result let go once its text is taken.
      const read = await weighedBody(request, Number(request.headers.get("content-length")),
                                     null, new AbortController().signal);
      let text: string | null = read.text;
      read.text = null;
      let tree: unknown = JSON.parse(text!);
      await checkpoint(this.env, "parse", call);
      text = null;
      let out: string | null = JSON.stringify(tree);
      await checkpoint(this.env, "stringify", call);
      tree = null;
      const bytes = new TextEncoder().encode(out);
      out = null;
      await checkpoint(this.env, "encode", call);
      return Response.json({ ...read, sent: bytes.byteLength,
                             dropped: text === null && tree === null && out === null });
    }
    if (url.pathname === "/pool") {
      return Response.json(isolatePool(GATEWAY_POOL_SLOTS).state());
    }
    // `/seal`: the provider's key sealed for the door's connection, and a model the meter
    // can cap (pi-ai's call for it sets its output cap).
    const checked = source(ANTHROPIC, false);
    const sealed = await seal(this.env, binding(OWNER, CONNECTION, 1), {
      kind: "api_key", secret: secret("sk-probe"), upstream: checked.upstream,
      endpoints: checked.endpoints, sealed_at: Date.now() });
    const catalog = builtinCatalog();
    for (const record of catalog.provider("anthropic")!.getModels()
      .filter((candidate) => candidate.api === "anthropic-messages")) {
      if (await learnMeter(catalog, checked.upstream, "sk-probe",
                           meteredRecord(catalog, checked.upstream, record.id)!) !== null) {
        return Response.json({ blob: sealed.blob, model: record.id });
      }
    }
    throw new Error("no Anthropic model the meter can cap");
  }
}
