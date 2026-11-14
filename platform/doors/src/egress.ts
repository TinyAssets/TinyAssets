/**
 * The egress door (gateway research §5.4 step 4; tasks 4 G4):
 * `egress.tinyassets.io/r/<run>/<secret>/c/<connection>/*`, where a run's model clients send
 * their providers' own APIs with placeholder keys (the contract's `sandboxEnv`). The run in
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
import {
  type MeterOutcome, outcomeOf, type Reservation,
} from "../../contract/src/gateway";
import {
  EGRESS_BEAT_S, EGRESS_ORIGIN, egressDoor, GENERATION_HEADER, generationIn, type Outcome,
  RESEALED_HEADER, RUN_HEADER, RUNNER_HEADER, SANDBOX_HEADER, SYNC_HEADER, type SyncDoor,
} from "../../contract/src/egress";
import { HEX64 } from "../../contract/src/ids";
import type { JsonBody, SettleSent } from "../../contract/src/owner";
import { EGRESS_BODY_MAX, EGRESS_DEADLINE_S, SYNC_BODY_MAX } from "../../contract/src/limits";
import { EVENTS_BODY_MAX, eventsBounded } from "../../contract/src/run-events";
import { type EgressCode, faultOf, Refusal, refusalOf } from "../../contract/src/refusal";

import { type Bound, bindRun, bindSandbox, bindSync, type Presented, rebound } from "./binder";
import type { DoorsEnv } from "./env";
import { ownerRead } from "./control";
import { answer, jsonObject } from "./kit";
import { LEASE_LIVED, verifyToken } from "../../contract/src/routing-mac";
import { serveMcp } from "./mcp";
import { answered, asEgress, asEgressFault } from "./refused";


/** The run's own secret: its path's, else its header's, else none (refused by the object). */
function secretOf(request: Request, inPath: string | undefined): string | undefined {
  if (inPath !== undefined) {
    return inPath;
  }
  const header = request.headers.get(RUN_HEADER);
  return header !== null && HEX64.test(header) ? header : undefined;
}

const LENGTH = /^[0-9]{1,15}$/;
export const EGRESS_DEADLINE_MS = EGRESS_DEADLINE_S * 1000;
/** How long a completed answer's metered outcome is waited for before it is `unknown`. */
const OUTCOME_WAIT_MS = 10_000;

/**
 * The door's own refusal, by one of its codes (`EGRESS_CODES`), thrown and answered in the
 * gateway's own error shape (`refused.ts`, `asEgress`), its status and any `Retry-After` its
 * code's. The runner waits a `Retry-After` out, up to its heartbeat (`HEARTBEAT_MS`, 20 s,
 * which its posts keep the lease by): so `storage_busy`'s 30 s is posted again after 20 s.
 */
function refused(code: EgressCode): Refusal {
  return new Refusal(code);
}

/** How the door stopped a stream before its answer: its deadline, the client, the object. */
type Stop = "aborted" | "deadline" | "ended";

/** The gateway's metered outcome, read from its one line (`outcomeOf`), or `unknown`. */
async function spendOf(outcome: ReadableStream<Uint8Array> | null): Promise<MeterOutcome> {
  if (outcome === null) {
    return { state: "unknown" };
  }
  const read = new Response(outcome).text().then(outcomeOf,
                                                 (): MeterOutcome => ({ state: "unknown" }));
  const late = new Promise<MeterOutcome>((resolve) =>
    setTimeout(() => resolve({ state: "unknown" }), OUTCOME_WAIT_MS));
  return Promise.race([read, late]).catch((): MeterOutcome => ({ state: "unknown" }));
}

/** Settle through the stub that opened the call, then once more through a fresh one. */
async function settleOnce(env: DoorsEnv, bound: Bound, request: SettleSent):
    Promise<void> {
  try {
    await bound.stub.egressSettle(bound.binding, request);
  } catch (first) {
    if (refusalOf(first) !== null) {
      throw first;  // the object answered: nothing to retry
    }
    const again = rebound(env, bound);
    await again.stub.egressSettle(again.binding, request);
  }
}

/**
 * The egress token E2B's transform set, verified: its MAC first, so a forged, altered or
 * sync token reaches no object, not even the gate's; then its expiry, the network's
 * generation and the maintenance gate. Its owner, hash and generation, or the refusal.
 */
async function sandboxToken(request: Request, env: DoorsEnv): Promise<Sandbox> {
  const token = request.headers.get(SANDBOX_HEADER);
  if (token === null || token === "") {
    throw refused("egress_token_missing");
  }
  const parsed = verifyToken(env.ROUTING_MAC_KEY, "egress", token);
  if (parsed === null) {
    throw refused("egress_token_unknown");
  }
  // A lease's token lives while its lease is renewed, which the object checks; one minted
  // with a time of its own is refused past it, reaching no object.
  if (parsed.expires !== LEASE_LIVED && parsed.expires <= Date.now() / 1000) {
    throw refused("egress_token_expired");
  }
  const generation = generationIn(request.headers.get(GENERATION_HEADER));
  if (generation === null) {
    throw refused("egress_policy_stale");
  }
  await ownerRead(env, parsed.owner);
  return { owner: parsed.owner, hash: parsed.hash, generation };
}

/** The runner's secret its call presents, or the refusal: none, or not 64 hex. */
function runnerOf(request: Request): Presented {
  const runner = request.headers.get(RUNNER_HEADER);
  if (runner === null || !HEX64.test(runner)) {
    throw refused("runner_token_missing");
  }
  return { runner };
}

/** What a sandbox's egress token verified: its owner, hash and network generation. */
interface Sandbox {
  readonly owner: string;
  readonly hash: string;
  readonly generation: number;
}

/**
 * The sandbox's JSON doors' one preamble: only the sandbox calls them, so an `Origin` (a
 * browser's) is refused; `POST`; `bound`, the door's own credential checked before the egress
 * token it is bound with; then the JSON body, to `max`. `call`'s answer, as JSON, or the
 * object's refusal with its whole message (as every refusal here), so the runner and its sync
 * agent can say what was refused and why (the used and the total of a full storage among
 * it); a path missing or of the wrong kind, or one the sandbox may not see, is its code
 * (`404`).
 */
async function sandboxDoor(request: Request, max: number, bound: () => Promise<Bound>,
                           call: (bound: Bound, body: JsonBody) => Promise<unknown>):
    Promise<Response> {
  if (request.headers.get("Origin") !== null) {
    throw refused("not_found");
  }
  if (request.method !== "POST") {
    throw refused("method_not_allowed");
  }
  const held = await bound();
  const body = await jsonObject(request, max);
  if (body instanceof Response) {
    return body;
  }
  try {
    return answer(200, await call(held, body));
  } catch (error) {
    const fault = faultOf(error);
    if (fault === null) {
      throw error;
    }
    return asEgressFault(fault);
  }
}

/** A runner's door's binding: the runner's secret, then the egress token, bound by `bind`. */
async function runnerBound(request: Request, env: DoorsEnv,
                           bind: (sandbox: Sandbox, presented: Presented) => Bound):
    Promise<Bound> {
  const presented = runnerOf(request);
  return bind(await sandboxToken(request, env), presented);
}

/** A frames post held to its frames' bounds (`eventsBounded`), or refused. */
function framesOf(body: JsonBody): JsonBody {
  if (!eventsBounded(body)) {
    throw refused("events_too_large");
  }
  return body;
}

/**
 * A run's MCP door (ADR-034 decisions 6 and 10): the table's tools but the run and mailbox
 * tools, every one held by the object to the run's grant (the `run` profile), by the run's
 * own secret. Only the engine in the sandbox calls it, so an `Origin` is refused.
 */
async function serveRunMcp(request: Request, env: DoorsEnv, run: string, secret: string):
    Promise<Response> {
  if (request.headers.get("Origin") !== null) {
    throw refused("not_found");
  }
  const sandbox = await sandboxToken(request, env);
  return serveMcp(request, bindRun(env, sandbox.owner, sandbox.hash, sandbox.generation, run,
                                   { secret }), env);
}

/**
 * A run's event door (ADR-034 decision 10), the runner's `POST` of its frames and its end
 * (`runEvents`), and a sign-in's (slice SIGNIN), the runner's `ta.signin` frames of the
 * vendor's own sign-in it drives (`signinEvents`): each by the runner's secret, each frame
 * and the post bounded (`EVENTS_BODY_MAX`, `EVENT_FRAMES_MAX`, `FRAME_UNITS_MAX`). For a
 * sign-in the object keeps the latest prompt, renews the lease and relays each frame to the
 * owner's app sockets.
 */
function serveEvents(request: Request, env: DoorsEnv,
                     door: { run: string } | { signin: string }): Promise<Response> {
  return sandboxDoor(request, EVENTS_BODY_MAX, () => runnerBound(request, env,
    (sandbox, presented) => "run" in door
      ? bindRun(env, sandbox.owner, sandbox.hash, sandbox.generation, door.run, presented)
      : bindSandbox(env, sandbox.owner, sandbox.hash, sandbox.generation, presented)),
  async ({ binding, stub }, body) => {
    const frames = framesOf(body);
    return "run" in door ? stub.runEvents(binding, frames)
      : stub.signinEvents(binding, { id: door.signin, frames: frames["frames"],
                                     ...(frames["end"] === undefined ? {}
                                       : { end: frames["end"] }) });
  });
}

/**
 * The runner's report of the engines' sign-ins (`signinReport`): each engine that signs in
 * with the vendor's own login, whether its record's `status_argv` succeeded, and the models
 * it offers. Presence only; the runner alone calls it, so an `Origin` is refused.
 */
function serveSignin(request: Request, env: DoorsEnv): Promise<Response> {
  return sandboxDoor(request, EGRESS_BODY_MAX, () => runnerBound(request, env,
    (sandbox, presented) => bindSandbox(env, sandbox.owner, sandbox.hash, sandbox.generation,
                                        presented)),
  ({ binding, stub }, body) => stub.signinReport(binding, body));
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

function base64(bytes: Uint8Array): string {
  return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength).toString("base64");
}

/** Base64 as its bytes, or null for anything else, which the object's own check refuses. */
function bytesOf(text: unknown): Uint8Array | null {
  return typeof text === "string" && /^[A-Za-z0-9+/]*={0,2}$/.test(text)
    ? new Uint8Array(Buffer.from(text, "base64")) : null;
}

/** Each item's `field` decoded from base64; anything else as sent (the object checks it). */
function decoded(items: unknown, field: string): unknown {
  if (!Array.isArray(items)) {
    return items;
  }
  return items.map((item: unknown) =>
    typeof item !== "object" || item === null || !(field in item) ? item
      : Object.fromEntries(Object.entries(item).map(([key, value]) =>
        [key, key === field ? bytesOf(value) : value])));
}

/** The sync token, then the egress token of the same owner's sandbox, bound as the syncer. */
async function syncBound(request: Request, env: DoorsEnv): Promise<Bound> {
  const token = request.headers.get(SYNC_HEADER);
  if (token === null || token === "") {
    throw refused("sync_token_missing");
  }
  const parsed = verifyToken(env.ROUTING_MAC_KEY, "sync", token);
  if (parsed === null) {
    throw refused("sync_token_unknown");
  }
  // As the egress token: a lease's lives while its lease is renewed, which the object checks.
  if (parsed.expires !== LEASE_LIVED && parsed.expires <= Date.now() / 1000) {
    throw refused("sync_token_expired");
  }
  // From inside the sandbox: its egress token, verified as the egress doors verify it (its
  // MAC, its expiry, its generation and the gate), naming the same owner.
  const inside = await sandboxToken(request, env);
  if (inside.owner !== parsed.owner) {
    throw refused("sync_token_unknown");
  }
  return bindSync(env, parsed.owner, parsed.hash, inside.hash);
}

function serveSync(request: Request, env: DoorsEnv, door: SyncDoor): Promise<Response> {
  return sandboxDoor(request, SYNC_BODY_MAX, () => syncBound(request, env),
                     async ({ binding, stub }, body) => {
    switch (door) {
      case "changes":
        return stub.fileChanges(binding, body);
      case "list":
        return stub.fileList(binding, body);
      case "read": {
        const read = await stub.fileRead(binding, body);
        return { ...read, ...(read.inline === undefined ? {} : { inline: base64(read.inline) }) };
      }
      case "chunks": {
        const read = await stub.fileReadChunks(binding, body);
        return { chunks: read.chunks.map(base64) };
      }
      case "commit":
        return stub.fileSyncCommit(binding, { ...body, ops: decoded(body["ops"], "inline") });
      case "upload_begin":
        return stub.fileSyncUploadBegin(binding, body);
      case "upload_chunks":
        return stub.fileSyncUploadChunks(binding, {
          ...body, chunks: decoded(body["chunks"], "bytes") });
      case "upload_abort":
        return stub.fileSyncUploadAbort(binding, body);
    }
  });
}

/**
 * Serve one sandbox call; `deadlineMs` is the door's own bound on its stream. Every refusal,
 * the door's own and the object's, is answered in the gateway's own shape (`asEgress`).
 */
export function serveEgress(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                            deadlineMs: number = EGRESS_DEADLINE_MS): Promise<Response> {
  return answered(asEgress, () => egress(request, env, ctx, deadlineMs));
}

async function egress(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                      deadlineMs: number): Promise<Response> {
  const url = new URL(request.url);
  if (url.origin !== EGRESS_ORIGIN) {
    throw refused("not_found");
  }
  const door = egressDoor(url.pathname);
  if (door !== null && door.door !== "model" && url.search !== "") {
    throw refused("not_found");
  }
  switch (door?.door) {
    case "mcp": {
      const secret = secretOf(request, door.secret);
      if (secret === undefined) {
        throw refused("run_secret_missing");
      }
      return serveRunMcp(request, env, door.run, secret);
    }
    case "events":
      return serveEvents(request, env, { run: door.run });
    case "signin":
      return serveSignin(request, env);
    case "signin_events":
      return serveEvents(request, env, { signin: door.signin });
    case "sync":
      return serveSync(request, env, door.sync);
    case "model":
      break;
    case undefined:
      throw refused("not_found");
  }
  const verified = await sandboxToken(request, env);
  const secret = secretOf(request, door.secret);
  const bound = bindSandbox(env, verified.owner, verified.hash, verified.generation,
                            secret === undefined ? {} : { secret });
  const { binding, stub } = bound;
  const { run, connection } = door;
  const path = `${door.path}${url.search}`;
  const length = request.headers.get("content-length");
  const bytesHint = length !== null && LENGTH.test(length) ? Number(length) : null;
  // The door reads none of the body: it streams to the gateway, which parses a model call's
  // once and asks the object to reserve it (`reserve`). Its declared length is what the
  // call is admitted by, within the gateway's own bound.
  const carries = request.method !== "GET" && request.method !== "HEAD";
  if (carries && bytesHint !== null && bytesHint > EGRESS_BODY_MAX) {
    throw refused("egress_too_large");
  }
  const opened = await stub.egressOpen(binding, {
    run, connection, method: request.method, path, bytes_hint: bytesHint });
  if (!opened.ok) {
    return asEgress(refused(opened.code), opened.receipt);
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
  let stopped: Stop | null = null;
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
        : Promise.resolve<MeterOutcome>({ state: "unknown" }))
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
  let stop: (end: Stop) => void = () => undefined;
  const halted = new Promise<Stop>((resolve) => {
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
      return Promise.resolve({ refused: `egress_${stopped}` });
    }
    const asked = bound.stub.egressReserve(binding, { reservation, model: call.model,
                                                      tier: call.tier })
      .then((reserved): Reservation => {
        if (reserved.ok) {
          return { metering: reserved.metering };
        }
        kept.rejected = asEgress(refused(reserved.code), reserved.receipt);
        return { refused: reserved.code };
      }, (error: unknown): Reservation => {
        const answer = refusalOf(error);
        if (answer === null) {
          throw error;
        }
        kept.rejected = asEgress(answer);
        return { refused: answer.code };
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
  let answer: { response: Response; outcome: ReadableStream<Uint8Array> } | Stop;
  try {
    answer = await Promise.race([sent, halted]);
  } catch {
    await finish("failed");
    throw refused("egress_gateway_failed");
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
    throw refused(`egress_${answer}`);
  }
  const { response } = answer;
  outcome = answer.outcome;
  status = response.status;
  const headers = new Headers(response.headers);
  const reseal = headers.get(RESEALED_HEADER);
  headers.delete(RESEALED_HEADER);
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
          controller.error(new Error(`egress_${stopped}`));
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
