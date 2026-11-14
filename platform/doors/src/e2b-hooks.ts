/**
 * E2B's signed sandbox lifecycle webhook (slice P; docs.e2b.dev/sandbox/lifecycle-events-
 * webhooks, read 2026-11-03): the source of truth for each owner's sandbox time, which E2B
 * bills by the second while a sandbox runs. The sandbox Worker's own log stays the estimate
 * on each run's receipt, and the fallback (`owner/src/sandbox-time.ts`).
 *
 * `POST /hooks/e2b/lifecycle`, the one hook this door serves (`/hooks/inbound/*` is
 * `inbound.ts`'s):
 * 1. **Verified first.** The `e2b-signature` header is E2B's: base64 of SHA-256 over the
 *    registration's signature secret and the raw body, its padding stripped. It is
 *    compared in constant time against `E2B_WEBHOOK_SECRET` (a Worker secret, set and
 *    registered with E2B by the deploy kit); no secret, and every delivery is refused
 *    `503`. It is tamper evidence over E2B's own scheme, not an HMAC.
 * 2. **Parsed strictly.** The body is read through a byte count that stops at
 *    `HOOK_BODY_MAX`, whatever `Content-Length` says or omits (an unauthenticated path reads
 *    no more); only `sandbox.lifecycle.paused` and `.killed` carry `event_data.execution`
 *    (its start, vCPUs, memory and the milliseconds billed), and only those are delivered,
 *    with the event's id, its signed `timestamp`, and the sandbox's and the execution's ids;
 *    every other event type answers `200`, so E2B does not retry it.
 * 3. **Routed by the sealed route** E2B echoes from the sandbox's metadata (`ta_route`,
 *    `routing-mac.ts`): the owner's object (`sandboxBilled`, which grants nothing), which
 *    counts each execution once by its execution id, and only while the event's own signed
 *    time is inside its window (`BILLED_WINDOW_S`): the signature carries no time of its own,
 *    so a delivery replayed later is refused by the time it signs, and one replayed sooner
 *    by the execution it names. A sandbox with no route of ours answers `200`, logged.
 */
import { timingSafeEqual } from "node:crypto";

import { sha256 } from "../../contract/src/digest";
import { HOOK_BODY_MAX } from "../../contract/src/limits";
import { utf8Text } from "../../contract/src/text";

import { ownerObject } from "./binder";
import type { DoorsEnv } from "./env";
import type { BilledEvent } from "../../contract/src/owner";
import { openRoute } from "../../contract/src/routing-mac";

import { answer, boundedBytes } from "./kit";

/** Whether `signature` is E2B's over `body` under `secret`, compared in constant time. */
export function signed(secret: string, body: string, signature: string | null): boolean {
  if (signature === null) {
    return false;
  }
  const expected = sha256(secret + body).toString("base64").replace(/=+$/, "");
  const given = Buffer.from(signature.replace(/=+$/, ""), "utf8");
  const wanted = Buffer.from(expected, "utf8");
  return given.byteLength === wanted.byteLength && timingSafeEqual(given, wanted);
}

const TYPES = Object.freeze(["sandbox.lifecycle.paused", "sandbox.lifecycle.killed"]);
const ID = /^[A-Za-z0-9._:-]{1,128}$/;

function plain(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function count(value: unknown, most: number): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= most
    ? value : null;
}

/**
 * A delivered event, checked: the billed execution and the sealed route it names, `ignored`
 * for a type that carries no billing, or null for one out of shape.
 */
export function lifecycleEvent(body: string):
    (Omit<BilledEvent, "authority"> & { route: unknown }) | "ignored" | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  if (!plain(parsed) || typeof parsed["id"] !== "string" || !ID.test(parsed["id"])
      || typeof parsed["type"] !== "string") {
    return null;
  }
  if (!TYPES.includes(parsed["type"])) {
    return "ignored";
  }
  const at = typeof parsed["timestamp"] === "string" ? Date.parse(parsed["timestamp"]) : NaN;
  const execution_id = parsed["sandbox_execution_id"];
  const sandbox_id = parsed["sandbox_id"];
  if (!Number.isFinite(at) || at < 0 || typeof execution_id !== "string"
      || !ID.test(execution_id) || typeof sandbox_id !== "string" || !ID.test(sandbox_id)) {
    return null;
  }
  const data = parsed["event_data"];
  const execution = plain(data) ? data["execution"] : undefined;
  const metadata = plain(data) ? data["sandbox_metadata"] : undefined;
  if (!plain(execution) || !plain(metadata) || typeof execution["started_at"] !== "string") {
    return null;
  }
  const started = Date.parse(execution["started_at"]);
  const ms = count(execution["execution_time"], 31 * 86_400_000);
  const vcpu = count(execution["vcpu_count"], 1024);
  const memory = count(execution["memory_mb"], 16 * 1024 * 1024);
  if (!Number.isFinite(started) || started < 0 || ms === null || vcpu === null
      || memory === null) {
    return null;
  }
  return { id: parsed["id"], execution_id, sandbox_id, at,
           kind: parsed["type"].endsWith("paused") ? "paused" : "killed",
           route: metadata["ta_route"], started_at: started, execution_ms: ms, vcpu,
           memory_mb: memory };
}

/** The webhook door: verified, parsed, routed to the owner's object as the platform. */
export async function e2bLifecycle(request: Request, env: DoorsEnv): Promise<Response> {
  if (request.method !== "POST") {
    return answer(405, { error: "method not allowed" });
  }
  const secret = env.E2B_WEBHOOK_SECRET;
  if (typeof secret !== "string" || secret.length < 16) {
    console.error("e2b_webhook_unconfigured: no signature secret is set");
    return answer(503, { error: "e2b_webhook_unconfigured" });
  }
  const bytes = await boundedBytes(request, HOOK_BODY_MAX);
  if (bytes === null) {
    return answer(413, { error: "the event is too large" });
  }
  let body: string;
  try {
    body = utf8Text(bytes, "keep");
  } catch {
    return answer(400, { error: "the event is out of shape" });
  }
  if (!signed(secret, body, request.headers.get("e2b-signature"))) {
    return answer(401, { error: "the event's signature does not verify" });
  }
  const event = lifecycleEvent(body);
  if (event === null) {
    return answer(400, { error: "the event is out of shape" });
  }
  if (event === "ignored") {
    return answer(200, { ignored: true });
  }
  const route = openRoute(env.ROUTING_MAC_KEY, event.route);
  if (route === null) {
    console.error("an E2B lifecycle event named no sandbox of ours");  // no ids in the log
    return answer(200, { unrouted: true });
  }
  const { route: _route, ...billed } = event;
  return answer(200, await ownerObject(env, route.owner).sandboxBilled(
    { ...billed, authority: route.authority }));
}
