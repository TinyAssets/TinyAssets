/**
 * The egress door (gateway research §5.4 step 4; tasks 4 G4):
 * `egress.tinyassets.io/r/<run>/<secret>/c/<connection>/*`, where a run's model clients send
 * their providers' own APIs with placeholder keys (`network.ts`'s `sandboxEnv`). The run in
 * the path is what the object holds the call to (that run's own connection, slice R), and
 * the run's own secret beside it is what lets a call act as that run: only the run's CLI
 * holds its URLs. The same host serves each run's MCP door (`/r/<run>/<secret>/mcp`), and
 * the runner's own doors, by the runner's secret (`x-ta-runner`), which no CLI holds: a
 * run's event door (`/r/<run>/events`) and the engines' sign-in report (`/signin`), all by
 * the same egress token.
 *
 * 1. **The identity.** E2B's transform sets the sandbox's egress token (`x-ta-sandbox`)
 *    and the egress generation its network was made under (`x-ta-network-generation`)
 *    outside the VM. The token names its owner and carries a MAC under the routing key
 *    (`routing-mac.ts`), verified here before any stub call: a missing, malformed, forged
 *    or altered one is refused before any object is reached, and the placeholder key is
 *    never read. No directory is read: the owner's object checks the secret's hash, its
 *    lease and the lease's expiry, strongly consistent. Then the maintenance gate.
 * 2. **Admission**, at the owner's object (`egressOpen`): the lease, the generations, the
 *    connection, its sealed endpoints, the metered call, its declared length and the rate;
 *    or a refusal's fixed code and receipt. The door reads none of the body.
 * 3. **Forward**: the request, its path after `/c/<connection>` and its body as the stream
 *    it came as, to the gateway's egress entrypoint (`EGRESS.forward`), metered: the gateway
 *    parses a model call's body once and calls back `reserve` (egress shape research, A′),
 *    which the door answers with the object's `egressReserve`, the model reservation by the
 *    loop's own rules for the model and tier that parse read; the call is then held to the
 *    reservation's output cap and the body length it was admitted by, its every inbound
 *    credential (the placeholder with them) stripped and the sealed one injected. A refused
 *    reserve is answered with the object's own code and receipt. The provider's answer
 *    streams back as it comes, but for the gateway's own headers (`x-ta-resealed`, which goes
 *    to the settle).
 * 4. **Settle** (`egressSettle`), once, on every end, with the bytes this door streamed and
 *    the gateway's metered outcome (`unknown` unless the answer completed): `complete`,
 *    `aborted` (the client stopped reading, or left before the answer), `failed` (the
 *    gateway failed), `deadline` (`EGRESS_DEADLINE_S`, the gateway's own bound, kept here
 *    too) or `ended` (the object's end signal: a policy write, a suspension, a revoke, the
 *    lease's close or the owner's erasure; a signal that errors is an end as well). The
 *    settle is registered with `waitUntil` the moment the call is admitted, so no end is
 *    dropped, and goes through a fresh stub, with one retry, so an object that reset
 *    mid-stream still settles. Every stop aborts the provider's request too.
 *
 * The door bills CPU only while the bytes flow; the owner's object does no work meanwhile,
 * but stays resident while it holds the stream's end signal. A sandbox token reaches no
 * other door: the other doors serve `tinyassets.io` alone, and the object refuses the
 * sandbox binding at every door but the egress doors.
 */
import type { Reservation } from "../../gateway/src/forward";
import { EGRESS_BEAT_S, EGRESS_BODY_MAX, EGRESS_DEADLINE_S, type EgressCode, GENERATION_HEADER,
         type Outcome, SANDBOX_HEADER } from "../../owner/src/network";
import { EVENTS_BODY_MAX, eventsBounded } from "../../owner/src/run-events";

import { type Bound, bindRun, bindSandbox, bindSync, type Presented, rebound } from "./binder";
import { jsonObject } from "./body";
import { type DoorsEnv, EGRESS_ORIGIN } from "./config";
import { ownerGate } from "./control";
import { LEASE_LIVED, verifyToken } from "./routing-mac";
import { serveMcp } from "./mcp";
import { refusal } from "./tools";

/** A run's model call: its run, a connection id, then the provider's own path. */
const ROUTE = /^\/r\/(run_[0-9a-f]{32})(?:\/([0-9a-f]{64}))?\/c\/(c[0-9a-f]{24})(\/.*)$/;
/** A run's MCP door, by its own secret. */
const MCP_DOOR = /^\/r\/(run_[0-9a-f]{32})(?:\/([0-9a-f]{64}))?\/mcp$/;
/**
 * The run's own secret as a header, where its CLI sends one (`run_secret_env`, its MCP
 * file's `headers`); the path carries it otherwise. Never forwarded (`x-ta-*`).
 */
export const RUN_HEADER = "x-ta-run";
const SECRET = /^[0-9a-f]{64}$/;

/** The run's own secret: its path's, else its header's, else none (refused by the object). */
function secretOf(request: Request, inPath: string | undefined): string | undefined {
  if (inPath !== undefined) {
    return inPath;
  }
  const header = request.headers.get(RUN_HEADER);
  return header !== null && SECRET.test(header) ? header : undefined;
}
/** A run's event door, the runner's. */
const EVENTS_DOOR = /^\/r\/(run_[0-9a-f]{32})\/events$/;
/** The runner's own secret, beside the egress token, on its event and sign-in posts. */
export const RUNNER_HEADER = "x-ta-runner";
const RUNNER = /^[0-9a-f]{64}$/;
/** The runner's report of the engines' sign-ins, for the sandbox as a whole. */
const SIGNIN_DOOR = "/signin";
const GENERATION = /^[1-9][0-9]{0,14}$/;
const LENGTH = /^[0-9]{1,15}$/;
/** The gateway's own header on its answer, for the object alone. */
const RESEALED = "x-ta-resealed";
export const EGRESS_DEADLINE_MS = EGRESS_DEADLINE_S * 1000;
/** How long a completed answer's metered outcome is waited for before it is `unknown`. */
const OUTCOME_WAIT_MS = 10_000;

/** The HTTP status each refusal is answered with. */
const STATUS: Readonly<Record<EgressCode, number>> = Object.freeze({
  egress_token_missing: 401, egress_token_unknown: 401, egress_token_expired: 401,
  egress_lease_closed: 401, egress_run_closed: 401, egress_run_connection: 403,
  egress_policy_stale: 409,
  egress_network_stale: 409,
  egress_connection_refused: 403, egress_endpoint_refused: 403, egress_unmetered: 403,
  egress_length_required: 411, egress_rate: 429, egress_spend_refused: 402,
  egress_unknown_model: 404, egress_unpriced_option: 403, egress_too_large: 413,
});
type Spend = { state: "succeeded"; tokens: number; cost: number; served?: string }
  | { state: "failed" }
  | { state: "unknown" };

/** A refusal in the gateway's own error shape, so a provider SDK shows its code. */
function refused(code: string, status: number, receipt?: string | null,
                 message: string = code): Response {
  return Response.json({ error: { code, message },
                         ...(receipt === undefined || receipt === null ? {} : { receipt }) },
                       { status, headers: { "Cache-Control": "no-store",
                                            ...(status === 429 || status === 503
                                              ? { "Retry-After": "1" } : {}) } });
}

/**
 * The status a run door's refusal goes back with: the object's held posts full
 * (`events_busy`), or another batch of the run landing between a post's units
 * (`events_raced`), is a 503 the runner retries after `Retry-After`; any other, its own.
 */
export function refusedStatus(code: string, status: number): number {
  return code === "events_busy" || code === "events_raced" ? 503 : status;
}

/** A refusal's code from the object's message: its fixed prefix, as the doors keep them. */
function codeOf(message: string): string {
  return /^[a-z][a-z_]{0,63}(?=:)/.exec(message)?.[0] ?? "egress_refused";
}

/** The gateway's metered outcome, read from its one line, or `unknown`. */
async function spendOf(outcome: ReadableStream<Uint8Array> | null): Promise<Spend> {
  if (outcome === null) {
    return { state: "unknown" };
  }
  const read = new Response(outcome).text().then((text): Spend => {
    const parsed = JSON.parse(text) as Spend;
    return parsed.state === "succeeded" || parsed.state === "failed" ? parsed
      : { state: "unknown" };
  }, (): Spend => ({ state: "unknown" }));
  const late = new Promise<Spend>((resolve) =>
    setTimeout(() => resolve({ state: "unknown" }), OUTCOME_WAIT_MS));
  return Promise.race([read, late]).catch((): Spend => ({ state: "unknown" }));
}

/** Settle through the stub that opened the call, then once more through a fresh one. */
async function settleOnce(env: DoorsEnv, bound: Bound, request: Record<string, unknown>):
    Promise<void> {
  try {
    await bound.stub.egressSettle(bound.binding, request);
  } catch (first) {
    if (refusal(first) !== null) {
      throw first;  // the object answered: nothing to retry
    }
    const again = rebound(env, bound);
    await again.stub.egressSettle(again.binding, request);
  }
}

/** Serve one sandbox call; `deadlineMs` is the door's own bound on its stream. */
/**
 * The egress token E2B's transform set, verified: its MAC first, so a forged, altered or
 * sync token reaches no object, not even the gate's; then its expiry, the network's
 * generation and the maintenance gate. Its owner, hash and generation, or the refusal.
 */
async function sandboxToken(request: Request, env: DoorsEnv):
    Promise<Response | { owner: string; hash: string; generation: number }> {
  const token = request.headers.get(SANDBOX_HEADER);
  if (token === null || token === "") {
    return refused("egress_token_missing", 401);
  }
  const parsed = verifyToken(env.ROUTING_MAC_KEY, "egress", token);
  if (parsed === null) {
    return refused("egress_token_unknown", 401);
  }
  // A lease's token lives while its lease is renewed, which the object checks; one minted
  // with a time of its own is refused past it, reaching no object.
  if (parsed.expires !== LEASE_LIVED && parsed.expires <= Date.now() / 1000) {
    return refused("egress_token_expired", 401);
  }
  const generation = request.headers.get(GENERATION_HEADER) ?? "";
  if (!GENERATION.test(generation)) {
    return refused("egress_policy_stale", 409);
  }
  const gated = await ownerGate(env, parsed.owner);
  if (gated !== null) {
    return gated;
  }
  return { owner: parsed.owner, hash: parsed.hash, generation: Number(generation) };
}

/** The runner's secret its call presents, or the refusal: none, or not 64 hex. */
function runnerOf(request: Request): Response | Presented {
  const runner = request.headers.get(RUNNER_HEADER);
  return runner !== null && RUNNER.test(runner) ? { runner }
    : refused("runner_token_missing", 401);
}

/**
 * A run's own doors (ADR-034 decisions 6 and 10): its MCP door, the table's tools but the
 * run and mailbox tools, every one held by the object to the run's grant (the `run`
 * profile), by the run's own secret; and its event door, the runner's `POST` of its frames
 * and its end
 * (`runEvents`), by the runner's secret, each frame and the post bounded (`EVENTS_BODY_MAX`,
 * `EVENT_FRAMES_MAX`, `FRAME_UNITS_MAX`). Only the engine and the runner in the sandbox call
 * them, so an `Origin` (a browser's) is refused.
 */
async function serveRun(request: Request, env: DoorsEnv, run: string,
                        door: { mcp: string } | "events"): Promise<Response> {
  if (request.headers.get("Origin") !== null) {
    return refused("not_found", 404);
  }
  const presented = door === "events" ? runnerOf(request) : { secret: door.mcp };
  if (presented instanceof Response) {
    return presented;
  }
  const verified = await sandboxToken(request, env);
  if (verified instanceof Response) {
    return verified;
  }
  const bound = bindRun(env, verified.owner, verified.hash, verified.generation, run,
                        presented);
  if (door !== "events") {
    return serveMcp(request, bound, env);
  }
  if (request.method !== "POST") {
    return refused("method_not_allowed", 405);
  }
  const body = await jsonObject(request, EVENTS_BODY_MAX);
  if (body instanceof Response) {
    return body;
  }
  if (!eventsBounded(body)) {
    return refused("events_too_large", 413);
  }
  try {
    return Response.json(await bound.stub.runEvents(bound.binding, body as never),
                         { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    const answer = refusal(error);
    if (answer === null) {
      throw error;
    }
    return refused(codeOf(answer.message), refusedStatus(codeOf(answer.message),
                                                       answer.status));
  }
}

/** A sign-in's event door's path: the sign-in's id. */
const SIGNIN_EVENTS_DOOR = /^\/signin\/(si_[0-9a-f]{32})\/events$/;

/**
 * A sign-in's event door (slice SIGNIN): the runner's `ta.signin` frames of the vendor's own
 * sign-in it drives, by the egress token and the runner's secret of the lease that holds it,
 * each frame and the post bounded as a run's (`EVENTS_BODY_MAX`). The object keeps the
 * latest prompt, renews the lease and relays each frame to the owner's app sockets.
 */
async function serveSigninEvents(request: Request, env: DoorsEnv, id: string):
    Promise<Response> {
  if (request.headers.get("Origin") !== null) {
    return refused("not_found", 404);
  }
  if (request.method !== "POST") {
    return refused("method_not_allowed", 405);
  }
  const presented = runnerOf(request);
  if (presented instanceof Response) {
    return presented;
  }
  const verified = await sandboxToken(request, env);
  if (verified instanceof Response) {
    return verified;
  }
  const bound = bindSandbox(env, verified.owner, verified.hash, verified.generation,
                            presented);
  const body = await jsonObject(request, EVENTS_BODY_MAX);
  if (body instanceof Response) {
    return body;
  }
  if (!eventsBounded(body)) {
    return refused("events_too_large", 413);
  }
  try {
    return Response.json(await bound.stub.signinEvents(bound.binding, {
      id, frames: body["frames"], ...(body["end"] === undefined ? {} : { end: body["end"] }) }),
                         { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    const answer = refusal(error);
    if (answer === null) {
      throw error;
    }
    return refused(codeOf(answer.message), refusedStatus(codeOf(answer.message),
                                                       answer.status));
  }
}

/**
 * The runner's report of the engines' sign-ins (`signinReport`): each engine that signs in
 * with the vendor's own login, whether its record's `status_argv` succeeded, and the models
 * it offers. Presence only; the runner alone calls it, so an `Origin` is refused.
 */
async function serveSignin(request: Request, env: DoorsEnv): Promise<Response> {
  if (request.headers.get("Origin") !== null) {
    return refused("not_found", 404);
  }
  if (request.method !== "POST") {
    return refused("method_not_allowed", 405);
  }
  const presented = runnerOf(request);
  if (presented instanceof Response) {
    return presented;
  }
  const verified = await sandboxToken(request, env);
  if (verified instanceof Response) {
    return verified;
  }
  const bound = bindSandbox(env, verified.owner, verified.hash, verified.generation,
                            presented);
  const body = await jsonObject(request, EGRESS_BODY_MAX);
  if (body instanceof Response) {
    return body;
  }
  try {
    return Response.json(await bound.stub.signinReport(bound.binding, body as never),
                         { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    const answer = refusal(error);
    if (answer === null) {
      throw error;
    }
    return refused(codeOf(answer.message), answer.status);
  }
}

/**
 * The in-VM sync agent's doors (slice S's runner), on the egress host by its sync token
 * (`x-ta-sync`, the lease's `sync` kind) together with the egress token E2B's transform sets
 * on every HTTPS call out of that sandbox (review M-5): the sync token alone, taken out of
 * the VM, reaches nothing. The doors: the change feed (`changes`), a listing for a feed
 * reset (`list`), a file's entry and bytes (`read`, `chunks`), the upload of a file past an
 * inline write (`upload_begin`, `upload_chunks`, `upload_abort`: the files plane's own
 * upload) and the fenced commit (`commit`). The object holds each to its live lease and,
 * for an outside client's sandbox, to the readable set the feed is filtered by
 * (`#syncer`), so a content door never shows a path the feed would not. Bytes cross as base64; a body is bounded
 * (`SYNC_BODY_MAX`), so the runner sends more calls, not bigger ones.
 */
const SYNC_DOOR =
  /^\/sync\/(changes|list|read|chunks|commit|upload_begin|upload_chunks|upload_abort)$/;
type SyncDoor = "changes" | "list" | "read" | "chunks" | "commit" | "upload_begin"
  | "upload_chunks" | "upload_abort";
export const SYNC_HEADER = "x-ta-sync";
/**
 * A sync door's body: two 4 MiB chunks' base64 (the files plane's most a call,
 * `BATCH_BYTES`), or the object's read of them, with room for the request's own fields.
 */
export const SYNC_BODY_MAX = 12 * 1024 * 1024;

function base64(bytes: Uint8Array): string {
  return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength).toString("base64");
}

/** A commit's ops with each `inline` decoded from base64; anything else as sent. */
function decodedOps(ops: unknown): unknown {
  if (!Array.isArray(ops)) {
    return ops;
  }
  return ops.map((op: unknown) => {
    if (typeof op !== "object" || op === null || !("inline" in op)) {
      return op;
    }
    const inline = (op as { inline: unknown }).inline;
    if (typeof inline !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(inline)) {
      return { ...op, inline: null };  // refused by the object's own check
    }
    return { ...op, inline: new Uint8Array(Buffer.from(inline, "base64")) };
  });
}

/** An upload's chunks with each `bytes` decoded from base64; anything else as sent. */
function decodedChunks(chunks: unknown): unknown {
  if (!Array.isArray(chunks)) {
    return chunks;
  }
  return chunks.map((chunk: unknown) => {
    if (typeof chunk !== "object" || chunk === null || !("bytes" in chunk)) {
      return chunk;
    }
    const bytes = (chunk as { bytes: unknown }).bytes;
    if (typeof bytes !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(bytes)) {
      return { ...chunk, bytes: null };  // refused by the object's own check
    }
    return { ...chunk, bytes: new Uint8Array(Buffer.from(bytes, "base64")) };
  });
}

async function serveSync(request: Request, env: DoorsEnv, door: SyncDoor): Promise<Response> {
  if (request.headers.get("Origin") !== null) {
    return refused("not_found", 404);
  }
  if (request.method !== "POST") {
    return refused("method_not_allowed", 405);
  }
  const token = request.headers.get(SYNC_HEADER);
  if (token === null || token === "") {
    return refused("sync_token_missing", 401);
  }
  const parsed = verifyToken(env.ROUTING_MAC_KEY, "sync", token);
  if (parsed === null) {
    return refused("sync_token_unknown", 401);
  }
  // As the egress token: a lease's lives while its lease is renewed, which the object checks.
  if (parsed.expires !== LEASE_LIVED && parsed.expires <= Date.now() / 1000) {
    return refused("sync_token_expired", 401);
  }
  // From inside the sandbox: its egress token, verified as the egress doors verify it (its
  // MAC, its expiry, its generation and the gate), naming the same owner.
  const inside = await sandboxToken(request, env);
  if (inside instanceof Response) {
    return inside;
  }
  if (inside.owner !== parsed.owner) {
    return refused("sync_token_unknown", 401);
  }
  const { binding, stub } = bindSync(env, parsed.owner, parsed.hash, inside.hash);
  const body = await jsonObject(request, SYNC_BODY_MAX);
  if (body instanceof Response) {
    return body;
  }
  const answer = (value: unknown) => Response.json(value, {
    headers: { "Cache-Control": "no-store" } });
  try {
    switch (door) {
      case "changes":
        return answer(await stub.fileChanges(binding, body as never));
      case "list":
        return answer(await stub.fileList(binding, body as never));
      case "read": {
        const read = await stub.fileRead(binding, body as never);
        return answer({ ...read, ...(read.inline === undefined ? {}
          : { inline: base64(read.inline) }) });
      }
      case "chunks": {
        const read = await stub.fileReadChunks(binding, body as never);
        return answer({ chunks: read.chunks.map(base64) });
      }
      case "commit":
        return answer(await stub.fileSyncCommit(binding, {
          ...body, ops: decodedOps(body["ops"]) } as never));
      case "upload_begin":
        return answer(await stub.fileSyncUploadBegin(binding, body as never));
      case "upload_chunks":
        return answer(await stub.fileSyncUploadChunks(binding, {
          ...body, chunks: decodedChunks(body["chunks"]) } as never));
      case "upload_abort":
        return answer(await stub.fileSyncUploadAbort(binding, body as never));
    }
  } catch (error) {
    if (error instanceof Error && error.name === "FileFault") {
      // A path missing or of the wrong kind, or one the sandbox may not see: its code.
      return refused(/^[a-z_]{1,32}/.exec(error.message)?.[0] ?? "not_found", 404);
    }
    const refusedAs = refusal(error);
    if (refusedAs === null) {
      throw error;
    }
    // The object's whole message, so the sync agent can say what was refused and why (the
    // used and the total of a full storage among it); its code first, as every door's.
    return refused(codeOf(refusedAs.message), refusedAs.status, null, refusedAs.message);
  }
}

export async function serveEgress(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                                  deadlineMs: number = EGRESS_DEADLINE_MS): Promise<Response> {
  const url = new URL(request.url);
  if (url.origin !== EGRESS_ORIGIN) {
    return refused("not_found", 404);
  }
  const mcp = MCP_DOOR.exec(url.pathname);
  if (mcp !== null && url.search === "") {
    const secret = secretOf(request, mcp[2]);
    if (secret === undefined) {
      return refused("run_secret_missing", 401);
    }
    return serveRun(request, env, mcp[1]!, { mcp: secret });
  }
  const events = EVENTS_DOOR.exec(url.pathname);
  if (events !== null && url.search === "") {
    return serveRun(request, env, events[1]!, "events");
  }
  if (url.pathname === SIGNIN_DOOR && url.search === "") {
    return serveSignin(request, env);
  }
  const signinEvents = SIGNIN_EVENTS_DOOR.exec(url.pathname);
  if (signinEvents !== null && url.search === "") {
    return serveSigninEvents(request, env, signinEvents[1]!);
  }
  const sync = SYNC_DOOR.exec(url.pathname);
  if (sync !== null && url.search === "") {
    return serveSync(request, env, sync[1] as SyncDoor);
  }
  const route = ROUTE.exec(url.pathname);
  if (route === null) {
    return refused("not_found", 404);
  }
  const verified = await sandboxToken(request, env);
  if (verified instanceof Response) {
    return verified;
  }
  const bound = bindSandbox(env, verified.owner, verified.hash, verified.generation,
                            secretOf(request, route[2]) === undefined ? {}
                              : { secret: secretOf(request, route[2])! });
  const { binding, stub } = bound;
  const run = route[1]!;
  const connection = route[3]!;
  const path = `${route[4]!}${url.search}`;
  const length = request.headers.get("content-length");
  const bytesHint = length !== null && LENGTH.test(length) ? Number(length) : null;
  // The door reads none of the body: it streams to the gateway, which parses a model call's
  // once and asks the object to reserve it (`reserve`). Its declared length is what the
  // call is admitted by, within the gateway's own bound.
  const carries = request.method !== "GET" && request.method !== "HEAD";
  if (carries && bytesHint !== null && bytesHint > EGRESS_BODY_MAX) {
    return refused("egress_too_large", STATUS.egress_too_large);
  }
  let opened: Awaited<ReturnType<typeof stub.egressOpen>>;
  try {
    opened = await stub.egressOpen(binding, {
      run, connection, method: request.method, path, bytes_hint: bytesHint });
  } catch (error) {
    const answer = refusal(error);
    if (answer === null) {
      throw error;
    }
    return refused(codeOf(answer.message), answer.status);
  }
  if (!opened.ok) {
    return refused(opened.code, STATUS[opened.code], opened.receipt);
  }
  const { reservation, blob, version } = opened;

  // From here every end settles the reservation, once, and the settle outlives the client.
  let settledDone: () => void = () => undefined;
  ctx.waitUntil(new Promise<void>((resolve) => {
    settledDone = resolve;
  }));
  const started = Date.now();
  let status: number | null = null;
  let bytes = 0;
  let resealed: unknown;
  let outcome: ReadableStream<Uint8Array> | null = null;
  let settled: Promise<void> | null = null;
  let stopped: Outcome | null = null;
  let upstream: ReadableStreamDefaultReader<Uint8Array> | null = null;
  const signal = opened.ended.getReader();
  const halt = new IdentityTransformStream();
  const halter = halt.writable.getWriter();
  let timer: ReturnType<typeof setTimeout> | undefined;
  // The door beats its open reservation while it streams, so the object settles it lost
  // within minutes once the beats stop (a deploy, a restart), never a day on.
  const beat = setInterval(() => {
    bound.stub.egressBeat(bound.binding, { reservation }).catch(() => undefined);
  }, EGRESS_BEAT_S * 1000);
  const finish = (end: Outcome): Promise<void> => {
    if (settled === null) {
      clearTimeout(timer);
      clearInterval(beat);
      signal.cancel().catch(() => undefined);
      halter.close().catch(() => undefined);  // the provider's request ends with the stream
      // Only a completed answer's meter can be read; any other end may have cost anything up
      // to the reservation, which `unknown` keeps whole.
      settled = (end === "complete" ? spendOf(outcome)
        : Promise.resolve<Spend>({ state: "unknown" }))
        .then((spend) => settleOnce(env, bound, {
          reservation, outcome: end, status, bytes, spend,
          ...(resealed === undefined ? {} : { resealed }) }))
        .then(() => undefined, (error: unknown) => {
          console.error("egress settle failed; the object settles it as lost", error);
        })
        .finally(() => settledDone());
    }
    return settled;
  };
  // The object's end signal, the door's deadline and the client's leaving stop the stream.
  let stop: (end: Outcome) => void = () => undefined;
  const halted = new Promise<Outcome>((resolve) => {
    stop = (end) => {
      if (stopped === null && settled === null) {
        stopped = end;
        resolve(end);
        upstream?.cancel().catch(() => undefined);
        void finish(end);
      }
    };
  });
  signal.read().then(() => stop("ended"), () => stop("ended"));
  timer = setTimeout(() => stop("deadline"), deadlineMs);
  request.signal?.addEventListener("abort", () => stop("aborted"));

  // A model call's reserve, which the gateway calls back (a stub to this function) once its
  // one parse read the body's model and tier; a refusal is kept, so the door answers the
  // object's own code and receipt, whatever the gateway or the ended stream says.
  let reserving: Promise<unknown> | null = null;
  const kept: { rejected: Response | null } = { rejected: null };
  const reserve = (call: { model: string; tier: string | null }): Promise<Reservation> => {
    if (stopped !== null) {
      // A call the door stopped is never reserved: nothing will be sent for it.
      return Promise.resolve({ refused: `egress_${stopped as Outcome}` });
    }
    const asked = bound.stub.egressReserve(binding, { reservation, model: call.model,
                                                      tier: call.tier })
      .then((reserved): Reservation => {
        if (reserved.ok) {
          return { metering: reserved.metering };
        }
        kept.rejected = refused(reserved.code, STATUS[reserved.code], reserved.receipt);
        return { refused: reserved.code };
      }, (error: unknown): Reservation => {
        const answer = refusal(error);
        if (answer === null) {
          throw error;
        }
        kept.rejected = refused(codeOf(answer.message), answer.status);
        return { refused: codeOf(answer.message) };
      });
    reserving = asked.catch(() => undefined);
    return asked;
  };
  const forwarded = new Request(`${EGRESS_ORIGIN}${path}`, {
    method: request.method, headers: request.headers, redirect: "manual",
    body: carries ? request.body : null,
  });
  const sent = env.EGRESS.forward(binding.owner, connection, version, blob, forwarded,
                                  opened.metered ? reserve : null, halt.readable);
  let answer: { response: Response; outcome: ReadableStream<Uint8Array> } | Outcome;
  try {
    answer = await Promise.race([sent, halted]);
  } catch {
    await finish("failed");
    return refused("egress_gateway_failed", 502);
  }
  await reserving;
  if (kept.rejected !== null) {
    // The object refused the reserve: its receipt is the call's, and nothing was sent.
    sent.then((late) => late.response.body?.cancel(), () => undefined);
    await finish(typeof answer === "string" ? answer : "failed");
    return kept.rejected;
  }
  if (typeof answer === "string") {
    sent.then((late) => late.response.body?.cancel(), () => undefined);
    await finish(answer);
    return refused(`egress_${answer}`, answer === "deadline" ? 504 : 503);
  }
  const { response } = answer;
  outcome = answer.outcome;
  status = response.status;
  const headers = new Headers(response.headers);
  const reseal = headers.get(RESEALED);
  headers.delete(RESEALED);
  try {
    resealed = reseal === null ? undefined : JSON.parse(reseal);
  } catch {
    resealed = undefined;  // the gateway's own header, malformed: nothing to keep
  }
  if (response.body === null) {
    await finish("complete");
    return new Response(null, { status: response.status, statusText: response.statusText,
                                headers });
  }
  const reader = response.body.getReader();
  upstream = reader;
  if (stopped !== null) {
    reader.cancel().catch(() => undefined);
  }
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch {
        const end = stopped ?? (Date.now() - started >= deadlineMs ? "deadline" : "failed");
        await finish(end);
        controller.error(new Error(`egress_${end}`));
        return;
      }
      if (chunk.done || stopped !== null) {
        await finish(stopped ?? "complete");
        if (stopped === null) {
          controller.close();
        } else {
          controller.error(new Error(`egress_${stopped as Outcome}`));
        }
        return;
      }
      bytes += chunk.value.byteLength;
      controller.enqueue(chunk.value);
    },
    async cancel() {
      reader.cancel().catch(() => undefined);
      await finish(stopped ?? "aborted");
    },
  }, { highWaterMark: 0 });
  return new Response(body, { status: response.status, statusText: response.statusText,
                              headers });
}
