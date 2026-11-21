/**
 * The egress memory leg's own Worker (`egress.mjs`, egress shape research §5; b23 research
 * question 5), beside the real doors and gateway, each bundled as a deploy bundles it, in
 * isolates of their own. Nothing here runs inside either: the leg reads both from outside,
 * through the inspector. Its entrypoints, by what the doors and the gateway reach:
 * - `LegOwner`: the doors' `OWNER` namespace, as far as the egress door asks of it: every
 *   call admitted, its connection the blob `/seal` held, and reserved as asked (the owner's
 *   own rules have their own tests);
 * - `Control`: the doors' `CONTROL` namespace, a KV service answering the control record
 *   with every door open;
 * - `Provider`: the gateway's outbound, the provider: it reads each model call's body,
 *   awaiting the leg's driver after each MiB (`upload`: the gateway is then waiting on its
 *   upload's backpressure) and once the body has arrived (`held`: the door and the gateway
 *   are then waiting for the first token), then answers;
 * - the default: the leg's tools. `/seal` seals the provider's key through the gateway's own
 *   `ConnectGateway`, holds it in `LegOwner` and answers a model the meter can cap (pi-ai's
 *   call for it sets its output cap); `/token` mints the egress token the door verifies.
 */
import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";

import { ANTHROPIC, OPEN } from "../../ci/fixtures";
import { CONTROL_KEY } from "../../contract/src/control";
import { EGRESS_HOST } from "../../contract/src/egress";
import { source } from "../../contract/src/egress-scope";
import type { ConnectService } from "../../contract/src/gateway";
import type { DoorAnswer, DoorLoose } from "../../contract/src/owner";
import type { OwnerDoors } from "../../contract/src/owner-doors";
import { LEASE_LIVED, mintToken } from "../../contract/src/routing-mac";
import { builtinCatalog } from "../src/catalog";
import { learnMeter, meteredRecord } from "../src/meter";

interface Env {
  readonly DRIVER: Fetcher;
  readonly CONNECT: ConnectService;
  readonly OWNER: DurableObjectNamespace<LegOwner>;
  readonly ROUTING_MAC_KEY: string;
}

const MIB = 1024 * 1024;
const OWNER = `user_${"a".repeat(32)}`;
const CONNECTION = `c${"b".repeat(24)}`;
const RUN = `run_${"c".repeat(32)}`;
const SECRET = "d".repeat(64);
const KEY = "sk-probe";

async function checkpoint(env: Env, phase: string, call: string): Promise<void> {
  await (await env.DRIVER.fetch(`http://driver/checkpoint?phase=${phase}&call=${call}`)).text();
}

type EgressDoors = Pick<OwnerDoors, "egressOpen" | "egressReserve" | "egressBeat"
                                    | "egressSettle">;

export class LegOwner extends DurableObject<Env> implements EgressDoors {
  async hold(blob: string): Promise<void> {
    await this.ctx.storage.put("blob", blob);
  }

  async egressOpen(): Promise<DoorAnswer<"egressOpen">> {
    const blob = await this.ctx.storage.get<string>("blob");
    if (blob === undefined) {
      throw new Error("no blob held: the leg seals one (`/seal`) before any call");
    }
    return { ok: true, reservation: "e".repeat(32), connection: CONNECTION, blob, version: 1,
             generation: 1, metered: true, ended: new IdentityTransformStream().readable };
  }

  async egressReserve(_binding: unknown, ...[call]: DoorLoose<"egressReserve">):
      Promise<DoorAnswer<"egressReserve">> {
    return { ok: true, metering: { max_output_tokens: 1000, input_bytes: 64 * MIB,
                                   model: call.model, tier: call.tier } };
  }

  async egressBeat(): Promise<DoorAnswer<"egressBeat">> {
    return { beat: true };
  }

  async egressSettle(): Promise<DoorAnswer<"egressSettle">> {
    return { settled: true };
  }
}

/** The control record, the one key the egress door reads (`GET /<key>`, workerd's KV form). */
export class Control extends WorkerEntrypoint<Env> {
  override async fetch(request: Request): Promise<Response> {
    const key = decodeURIComponent(new URL(request.url).pathname.slice(1));
    return request.method === "GET" && key === CONTROL_KEY
      ? new Response(JSON.stringify(OPEN)) : new Response(null, { status: 404 });
  }
}

export class Provider extends WorkerEntrypoint<Env> {
  override async fetch(request: Request): Promise<Response> {
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
        await checkpoint(this.env, "upload", call);
        next += MIB;
      }
    }
    await checkpoint(this.env, "held", call);
    const event = (type: string, data: object) =>
      `event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`;
    return new Response(event("message_start", { message: {
      id: "m", type: "message", role: "assistant", model: "probe", content: [],
      stop_reason: null, usage: { input_tokens: 10, output_tokens: 1 } } })
      + event("message_stop", {}), { headers: { "content-type": "text/event-stream" } });
  }
}

export default class Tools extends WorkerEntrypoint<Env> {
  override async fetch(request: Request): Promise<Response> {
    if (new URL(request.url).pathname === "/token") {
      return Response.json({
        host: EGRESS_HOST, path: `/r/${RUN}/${SECRET}/c/${CONNECTION}/v1/messages`,
        token: mintToken(this.env.ROUTING_MAC_KEY, "egress", OWNER, LEASE_LIVED).token });
    }
    const sealed = await this.env.CONNECT.seal(OWNER, CONNECTION, 1, KEY, ANTHROPIC);
    await this.env.OWNER.getByName(OWNER).hold(sealed.blob);
    const { upstream } = source(ANTHROPIC, false);
    const catalog = builtinCatalog();
    for (const record of catalog.provider("anthropic")!.getModels()
      .filter((candidate) => candidate.api === "anthropic-messages")) {
      if (await learnMeter(catalog, upstream, KEY,
                           meteredRecord(catalog, upstream, record.id)!) !== null) {
        return Response.json({ model: record.id });
      }
    }
    throw new Error("no Anthropic model the meter can cap");
  }
}
