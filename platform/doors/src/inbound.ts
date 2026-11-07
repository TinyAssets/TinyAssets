/**
 * `/hooks/inbound/<token>` (tasks 2; slice L), ported from the Python's `owner_router.inbound`
 * and `broker/inbound_verification.py`: an outside service's event, proved and routed by
 * the binder, accepted by the owner's object (`inboundAccept`, `owner/src/inbound.ts`).
 *
 * Order, before any object is reached:
 * 1. The `inbound` gate of the platform's control record (`CONTROL`): off or missing, the
 *    door does not exist (`404`, as the Python's switch); malformed or unreadable, `503`.
 * 2. `POST`, one path segment, and a body within the platform's one request ceiling
 *    (`INBOUND_BODY_MAX`); the `Cookie` header is gone before this sees it (`doors.ts`).
 * 3. The two shapes:
 *    - **a shared app** (`shared-apps.json`, data: Slack's today), when the segment is an
 *      app's endpoint: its signature checked against the platform's signing secret, which
 *      the record names (`inbound-proof.ts`: Slack's `v0` HMAC over the timestamp and the
 *      raw bytes, five minutes' skew, so a replay past it is refused); its URL challenge
 *      answered; a single installation's event routed by the directory's `i:` entry; any
 *      other envelope an authenticated no-op (`200`), so the app's failure budget with its
 *      vendor is never spent on what the owner did not install.
 *    - **a capability URL**: the token's SHA-256 routed by the directory's `h:` entry; the
 *      event's identity from `Idempotency-Key` and `X-Event-Timestamp`.
 * 4. The owner's maintenance gate, then the object, which holds the route's revision
 *    against its own, checks an owner-held proof, dedupes and mails the agent.
 *
 * A route the directory does not hold is `404` (a capability URL) or a no-op (a shared
 * app's install no owner made); the directory's lag can only route a dead entry to the
 * object's own refusal (`binding_withdrawn`).
 */
import { utf8Decoder } from "../../contract/src/text";
import { sha256Hex } from "../../contract/src/digest";

import { type Profile, profile as checkedProfile, proves } from "../../contract/src/inbound-proof";
import type { InboundAnswer } from "../../owner/src/owner-do";

import apps from "../shared-apps.json" with { type: "json" };

import { bindInbound, type Bound, withTiers } from "./binder";
import { boundedBytes } from "./body";
import {
  type DoorsEnv, INBOUND_BODY_MAX, INBOUND_PATH, namedSecret, now, PUBLIC_ORIGIN,
} from "./config";
import { type Control, deleteRoute, putRoute, readControl, readRoute } from "./directory";
import { bundledSources } from "../../sources/src/catalog";
import { signinsOn } from "../../sources/src/signin";
import { refusal } from "./tools";

const TOKEN = /^[A-Za-z0-9_-]{1,128}$/;
const STAMP = /^[0-9]{1,12}$/;
/** The headers an event carries to the object (its owner-held proof may name any). */
const HEADERS_MAX = 64;
const HEADER_VALUE_MAX = 8 * 1024;

/** A shared app, as `shared-apps.json` declares it. */
export interface SharedApp {
  readonly id: string;
  readonly endpoint: string;
  readonly secret: string;
  readonly profile: Profile;
  readonly envelope: {
    readonly setup_type: string;
    readonly challenge: string;
    readonly event_type: string;
    readonly require_false: readonly string[];
    readonly event_id: string;
    readonly event_time: string;
    readonly installations: string;
    readonly enterprise_flag: string;
    readonly enterprise_id: string;
    readonly workspace_id: string;
  };
}

/** The shared apps, each checked whole: a broken record fails loudly here. */
export function sharedApps(declared: unknown = apps): readonly SharedApp[] {
  const list = (declared as { apps?: unknown } | null)?.apps;
  if (!Array.isArray(list)) {
    throw new Error("shared-apps.json holds an apps list");
  }
  return list.map((app: Record<string, unknown>) => {
    const { id, endpoint, secret, profile, envelope } = app;
    const fields = envelope as Record<string, unknown> | null;
    if (Object.keys(app).sort().join() !== "endpoint,envelope,id,profile,secret"
        || typeof id !== "string" || !/^[a-z][a-z0-9_]{0,31}$/.test(id)
        || typeof endpoint !== "string" || !/^[a-z][a-z0-9_-]{0,31}$/.test(endpoint)
        || typeof secret !== "string" || !/^[A-Z][A-Z0-9_]{0,63}$/.test(secret)
        || typeof fields !== "object" || fields === null
        || !Array.isArray(fields["require_false"])
        || ["setup_type", "challenge", "event_type", "event_id", "event_time", "installations",
            "enterprise_flag", "enterprise_id", "workspace_id"]
          .some((key) => typeof fields[key] !== "string")) {
      throw new Error(`shared app ${JSON.stringify(id)} is malformed`);
    }
    return Object.freeze({ id, endpoint, secret, profile: checkedProfile(profile),
                           envelope: fields as unknown as SharedApp["envelope"] });
  });
}

function answer(status: number, body: unknown, retry = false): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store",
                                                  ...(retry ? { "Retry-After": "5" } : {}) } });
}

function sha256(text: string): string {
  return sha256Hex(text);
}

/** The request's headers for the object: lowercase names, bounded (its `Cookie` is gone). */
function headersOf(request: Request): Record<string, string> | null {
  const out: Record<string, string> = {};
  let count = 0;
  for (const [name, value] of request.headers) {
    if (++count > HEADERS_MAX || value.length > HEADER_VALUE_MAX) {
      return null;
    }
    out[name] = value;
  }
  return out;
}

/** A JSON object from an uncompressed UTF-8 JSON body, or null (`_json_object`). */
function envelopeOf(request: Request, body: Uint8Array): Record<string, unknown> | null {
  const type = (request.headers.get("Content-Type") ?? "").split(";")[0]!.trim().toLowerCase();
  const encoding = (request.headers.get("Content-Encoding") ?? "identity").toLowerCase();
  if (type !== "application/json" || encoding !== "identity") {
    return null;
  }
  try {
    const value: unknown = JSON.parse(utf8Decoder("strip")
      .decode(body));
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? value as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

type Routed =
  | { readonly kind: "answer"; readonly response: Response }
  | { readonly kind: "event"; readonly route: string; readonly shared: boolean;
      readonly eventId: string; readonly eventAt: number };

/** A shared app's request: proved, then its envelope read (`extract_single_installation`). */
function sharedEvent(app: SharedApp, request: Request, body: Uint8Array,
                     headers: Record<string, string>, env: DoorsEnv): Routed {
  const secret = namedSecret(env, app.secret);
  if (secret === null) {
    console.error(`the shared app ${app.id} has no signing secret (${app.secret})`);
    return { kind: "answer", response: answer(503, { error: "inbound_app_unconfigured" }, true) };
  }
  if (!proves(app.profile, headers, body, secret, now())) {
    return { kind: "answer", response: answer(401, { error: "invalid_signature" }) };
  }
  const shape = app.envelope;
  const envelope = envelopeOf(request, body);
  const noOp = { kind: "answer", response: answer(200, { state: "no_op" }) } as const;
  if (envelope === null) {
    return noOp;
  }
  if (envelope["type"] === shape.setup_type) {
    const challenge = envelope[shape.challenge];
    return typeof challenge === "string" && challenge !== "" && challenge.length <= 1024
      ? { kind: "answer", response: answer(200, { challenge }) } : noOp;
  }
  if (envelope["type"] !== shape.event_type
      || shape.require_false.some((field) => envelope[field] !== false)) {
    return noOp;
  }
  const installs = envelope[shape.installations];
  if (!Array.isArray(installs) || installs.length !== 1 || typeof installs[0] !== "object"
      || installs[0] === null) {
    return noOp;
  }
  const install = installs[0] as Record<string, unknown>;
  const enterprise = install[shape.enterprise_flag];
  if (typeof enterprise !== "boolean") {
    return noOp;
  }
  const identity = install[enterprise ? shape.enterprise_id : shape.workspace_id];
  if (typeof identity !== "string" || identity === "" || identity.length > 256) {
    return noOp;
  }
  const eventId = envelope[shape.event_id];
  const eventAt = envelope[shape.event_time];
  if (typeof eventId !== "string" || !eventId.trim() || eventId.length > 256
      || typeof eventAt !== "number" || !Number.isSafeInteger(eventAt)
      || !STAMP.test(String(eventAt))) {
    // An authenticated event the platform cannot key is a no-op, never a 400 that spends
    // the app's failure budget on an event its vendor retries.
    return { kind: "answer", response: answer(200, { state: "refused",
                                                     error: "event_identity_required" }) };
  }
  return { kind: "event", shared: true, eventId, eventAt,
           route: `i:${app.id}:${enterprise ? "enterprise" : "workspace"}:${sha256(identity)}` };
}

/** A capability URL's request: its token's route and the event's identity headers. */
function capabilityEvent(token: string, request: Request): Routed {
  const eventId = request.headers.get("Idempotency-Key") ?? "";
  const stamp = request.headers.get("X-Event-Timestamp") ?? "";
  if (!eventId.trim() || eventId.length > 256 || !STAMP.test(stamp)) {
    return { kind: "answer", response: answer(400, { error: "event_identity_required" }) };
  }
  return { kind: "event", shared: false, eventId, eventAt: Number(stamp),
           route: `h:${sha256(token)}` };
}

/** The object's answer as the sender gets it: a shared app's never spends its budget. */
function delivered(shared: boolean, accepted: InboundAnswer): Response {
  if (accepted.state === "admitted") {
    return answer(202, accepted);
  }
  if (accepted.state === "no_op") {
    return answer(200, accepted);
  }
  if (accepted.retry === true) {
    return answer(429, accepted, true);
  }
  return shared ? answer(200, accepted)
    : answer(accepted.error === "too_large" ? 413 : 409, accepted);
}

function gateOpen(control: Control, owner: string): boolean {
  return control.ordinary === "all"
    || (control.ordinary === "canary" && control.canary.includes(owner));
}

/** `POST /hooks/inbound/<token>`. */
export async function serveInbound(request: Request, env: DoorsEnv): Promise<Response> {
  const read = await readControl(env.CONTROL);
  if (read.kind === "malformed" || read.kind === "unreadable") {
    return answer(503, { error: "unavailable" }, true);
  }
  if (read.kind === "missing" || !read.control.inbound) {
    return answer(404, { error: "not_found" });
  }
  const token = new URL(request.url).pathname.slice(INBOUND_PATH.length);
  if (!TOKEN.test(token)) {
    return answer(404, { error: "not_found" });
  }
  if (request.method !== "POST") {
    return answer(405, { error: "method_not_allowed" });
  }
  const body = await boundedBytes(request, INBOUND_BODY_MAX);
  if (body === null) {
    return answer(413, { error: "too_large" });
  }
  const headers = headersOf(request);
  if (headers === null) {
    return answer(400, { error: "too_many_headers" });
  }
  const app = sharedApps().find((shared) => shared.endpoint === token);
  const routed = app !== undefined ? sharedEvent(app, request, body, headers, env)
    : capabilityEvent(token, request);
  if (routed.kind === "answer") {
    return routed.response;
  }
  const entry = await readRoute(env, routed.route);
  if (entry === null) {
    return routed.shared ? answer(200, { state: "no_op" }) : answer(404, { error: "not_found" });
  }
  if (!gateOpen(read.control, entry.o)) {
    return answer(503, { error: "maintenance" }, true);
  }
  // The run an event starts is held to the switched-on sign-ins as a door's is (slice SIGNIN).
  const { binding, stub } = withTiers(bindInbound(env, entry), {
    tiers: read.control.tiers, signins: signinsOn(bundledSources(), read.control.signins) });
  try {
    return delivered(routed.shared, await stub.inboundAccept(binding, {
      event_id: routed.eventId, event_at: routed.eventAt, body, headers }));
  } catch (error) {
    const refused = refusal(error);
    if (refused === null) {
      throw error;
    }
    if (routed.shared) {
      return answer(200, { state: "refused", error: refused.message.split(":")[0] });
    }
    const code = refused.message.split(":")[0]!;
    return answer(code === "inbound_proof_invalid" ? 401 : 404, { error: code });
  }
}

/**
 * The owner surface's inbound routes (`app.ts`), each the object's door and then the
 * directory's entry for the binding at its new revision:
 * - `hooks/create`: a capability URL, its token minted here (256 random bits) and shown
 *   once; its SHA-256 alone routes it (`h:`), and the object keeps only that route.
 * - `hooks/set`: the owner's change, which moves the revision the entry must carry.
 * - `hooks/revoke`: the binding and its entry gone.
 * - `hooks/list`: the owner's bindings, never a secret or a token.
 */
export const HOOK_ROUTES: Readonly<Record<string, (env: DoorsEnv, bound: Bound,
                                                   request: Record<string, unknown>) =>
                                                   Promise<unknown>>> = Object.freeze({
  "hooks/create": async (env, { binding, stub }, request) => {
    const token = Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64url");
    const route = `h:${sha256(token)}`;
    const made = await stub.inboundCreate(binding, { ...request, kind: "capability",
                                                     route } as never);
    await putRoute(env, made.route, { o: binding.owner, b: made.binding_id, r: made.revision });
    return { ...made, url: `${PUBLIC_ORIGIN}${INBOUND_PATH}${token}` };
  },
  "hooks/set": async (env, { binding, stub }, request) => {
    const set = await stub.inboundSet(binding, request as never);
    await putRoute(env, set.route, { o: binding.owner, b: set.binding_id, r: set.revision });
    return set;
  },
  "hooks/revoke": async (env, { binding, stub }, request) => {
    const gone = await stub.inboundRevoke(binding, request as never);
    if (gone.route !== null) {
      await deleteRoute(env, gone.route);
    }
    return gone;
  },
  "hooks/list": async (_env, { binding, stub }) => stub.inboundList(binding),
});
