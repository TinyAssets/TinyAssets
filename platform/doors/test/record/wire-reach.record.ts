/**
 * The wire recorder's second half (`npm run record`, `ci/record.ts`): inbound hooks reaching
 * the owner, subscriptions reaching the owner, and refusals crossing a real service binding,
 * each answer one `REC` line, ids taken out (`clean`).
 */
import { createHmac } from "node:crypto";

import { env, runInDurableObject } from "cloudflare:test";
import { beforeEach, describe, it } from "vitest";

import { now } from "../../src/config";
import { mcpOrigin } from "../../src/binder";
import { Refusal, refusalOf } from "../../../contract/src/refusal";
import {
  appPost, call, connect, legacyPost, OPEN, rpcBody, registerClient, serveFirstPartyDocument,
  setControl, url,
} from "../support";
import { clean, owned as ownedBy } from "./support";

let restore: () => void = () => undefined;
beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

async function rec(label: string, pending: Response | Promise<Response>): Promise<void> {
  let response: Response;
  try {
    response = await pending;
  } catch (error) {
    console.log(`REC\t${label}\tTHROW\t${clean(String((error as Error)?.name))}\t${
      clean(String((error as Error)?.message)).slice(0, 300)}`);
    return;
  }
  const text = await response.text();
  console.log(`REC\t${label}\t${response.status}\tRA=${response.headers.get("Retry-After")}\t${
    clean(text).slice(0, 500)}`);
}

function recRaw(label: string, value: unknown): void {
  console.log(`REC\t${label}\t${clean(JSON.stringify(value)).slice(0, 500)}`);
}

let counter = 0;
function owned() {
  counter += 1;
  return ownedBy(`user_${counter.toString(16).padStart(32, "a")}`);
}

describe("inbound hooks reaching the owner", () => {
  it("capability URLs", async () => {
    await setControl({ ...OPEN, inbound: true });
    const { app } = await owned();
    const createdResponse = await appPost("hooks/create", app.access_token, app.session,
                                          { agent: "main" });
    const createdText = await createdResponse.text();
    recRaw("hooks create", { status: createdResponse.status, body: createdText.slice(0, 80) });
    const made = JSON.parse(createdText) as { url: string; binding_id: string };
    const secret = "whsec_0123456789abcdef";
    const proofed = await (await appPost("hooks/create", app.access_token, app.session, {
      agent: "main", secret, profile: {
        method: "hmac-sha256", signature_header: "x-hub-signature-256", encoding: "hex",
        prefix: "sha256=" } })).json<{ url: string }>();
    const event = (at: string, id: string | null, headers: Record<string, string> = {},
                   body = "{\"x\":1}", stamp: number = now()) => new Request(at, {
      method: "POST", body, headers: {
        ...(id === null ? {} : { "Idempotency-Key": id }),
        "X-Event-Timestamp": String(stamp), ...headers } });
    await rec("inbound ok", call(event(made.url, "e1")));
    await rec("inbound replay", call(event(made.url, "e1")));
    await rec("inbound no id", call(event(made.url, null)));
    await rec("inbound unknown token", call(event(url(`/hooks/inbound/${"A".repeat(43)}`), "e")));
    const body = "{\"action\":\"opened\"}";
    const good = createHmac("sha256", secret).update(body).digest("hex");
    await rec("inbound good proof", call(event(proofed.url, "g1",
                                               { "X-Hub-Signature-256": `sha256=${good}` }, body)));
    await rec("inbound bad proof", call(event(proofed.url, "p1",
                                              { "X-Hub-Signature-256": "sha256=00" }, body)));
    await rec("inbound no proof", call(event(proofed.url, "p2", {}, body)));
    await rec("inbound huge", call(event(made.url, "e3", {}, "x".repeat(2_000_000))));
    await rec("inbound old stamp", call(event(made.url, "e4", {}, "{}", 1)));
    await rec("inbound bad utf8 body", call(new Request(made.url, {
      method: "POST", body: new Uint8Array([0xff, 0xfe, 0x00]),
      headers: { "Idempotency-Key": "e6", "X-Event-Timestamp": String(now()) } })));
    for (let n = 0; n < 3; n++) {
      await rec(`inbound burst ${n}`, call(event(made.url, `b${n}`, {}, `{"n":${n}}`)));
    }
    await rec("hooks revoke", appPost("hooks/revoke", app.access_token, app.session,
                                      { binding_id: made.binding_id }));
    await rec("inbound revoked", call(event(made.url, "e5")));
  });
});

describe("subscriptions reaching the owner", () => {
  it("events/subscribe with a real event", async () => {
    counter += 1;
    const alice = `user_${counter.toString(16).padStart(32, "b")}`;
    const registered = await registerClient();
    const tokens = await connect(registered, { owner: alice });
    const rpc = async (method: string, params: unknown) => rpcBody(await legacyPost(
      tokens.access_token, { jsonrpc: "2.0", id: 1, method, params }));
    recRaw("ev list", await rpc("events/list", {}));
    const WHSEC = "whsec_" + btoa(String.fromCharCode(...new Uint8Array(32).map((_, n) => n + 1)));
    const sub = (args: unknown, extra: Record<string, unknown> = {}) => rpc("events/subscribe", {
      name: "tinyassets.changed", arguments: args,
      delivery: { mode: "webhook", url: "https://callback.test/a", secret: WHSEC },
      cursor: null, ...extra });
    // No agent registered for the client yet: the owner refuses.
    recRaw("ev sub no agent", await sub({ command_center_id: "alpha", board: true }));
    const origin = await mcpOrigin(registered.clientId, registered.redirectUri);
    await runInDurableObject(env.OWNER.getByName(alice), (_i: unknown, state) => {
      state.storage.sql.exec(
        "INSERT INTO outside_agents (agent_id, command_center_id, outside_client, outside_family, " +
          "source, source_ref, display_name, delivery_method, recipe_id, revision, created_at) " +
          "VALUES ('agent_00000000000000aa', 'alpha', ?, ?, 'mcp', ?, 'Dot', 'mcp_event', NULL, " +
          "1, 0)", origin.client, origin.family, registered.clientId);
    });
    recRaw("ev sub bad args", await sub({ command_center_id: 7 }));
    recRaw("ev sub other cc", await sub({ command_center_id: "beta", board: true }));
    recRaw("ev sub bad url", await sub({ command_center_id: "alpha", board: true },
                                       { delivery: { mode: "webhook", url: "http://x", secret: WHSEC } }));
    recRaw("ev sub bad secret", await sub({ command_center_id: "alpha", board: true },
                                          { delivery: { mode: "webhook", url: "https://callback.test/b",
                                                        secret: "nope" } }));
    for (let n = 0; n < 12; n++) {
      recRaw(`ev sub ${n}`, await rpc("events/subscribe", {
        name: "tinyassets.changed", arguments: { command_center_id: "alpha", board: true },
        delivery: { mode: "webhook", url: `https://callback.test/p${n}`, secret: WHSEC },
        cursor: null }));
    }
    recRaw("ev unsub other", await rpc("events/unsubscribe", {
      name: "tinyassets.changed", arguments: { command_center_id: "beta", board: true },
      delivery: { mode: "webhook", url: "https://callback.test/zz" } }));
  });
});

/** A refusal as it crossed a service binding: its own fields, and the refusal it reads as. */
function crossed(error: unknown) {
  const e = error as Error & Record<string, unknown>;
  const own = Object.fromEntries(Object.getOwnPropertyNames(e).filter((k) => k !== "stack").sort()
    .map((k) => [k, e[k]]));
  const rebuilt = refusalOf(e);
  return { e, own, rebuilt: rebuilt === null ? null : { code: rebuilt.code, status: rebuilt.status } };
}

describe("refusals across a real service binding", () => {
  it("the gateway Worker's refusals reach the doors", async () => {
    const gateway = (env as unknown as { GATEWAY: Record<string, (...a: unknown[]) => Promise<unknown>> })
      .GATEWAY;
    const probes: [string, () => Promise<unknown>][] = [
      ["verifyDoorbell bad input", () => gateway["verifyDoorbell"]!(`user_${"c".repeat(32)}`,
                                                                    "wsub_x", 1, { a: 1 })],
      ["sealDoorbell bad subscription", () => gateway["sealDoorbell"]!(`user_${"c".repeat(32)}`,
                                                                       "bad", 1, {})],
      ["seal bad binding", () => gateway["seal"]!("nope", "nope", 1, "k", {})],
      ["verify bad blob", () => gateway["verify"]!(`user_${"c".repeat(32)}`, `c${"0".repeat(24)}`,
                                                   1, "v1.x.y.z")],
    ];
    for (const [label, probe] of probes) {
      try {
        await probe();
        recRaw(`svc ${label}`, "resolved");
      } catch (error) {
        const { e, own, rebuilt } = crossed(error);
        recRaw(`svc ${label}`, { name: e.name, message: e.message, own,
                                 instanceofRefusal: e instanceof Refusal, rebuilt });
      }
    }
  });

  it("the files Worker's refusals reach the doors", async () => {
    const files = (env as unknown as { FILES: Record<string, (...a: unknown[]) => Promise<unknown>> })
      .FILES;
    for (const [label, probe] of [
      ["putChunks bad namespace", () => files["putChunks"]!("bad ns", "k", [])],
      ["putChunks bad hash", () => files["putChunks"]!(`ns_${"0".repeat(32)}`, "k",
                                                       [{ sha256: "zz", bytes: new Uint8Array(1) }])],
    ] as [string, () => Promise<unknown>][]) {
      try {
        await probe();
        recRaw(`files ${label}`, "resolved");
      } catch (error) {
        const { e, own, rebuilt } = crossed(error);
        recRaw(`files ${label}`, { name: e.name, message: e.message, own, rebuilt });
      }
    }
  });
});
