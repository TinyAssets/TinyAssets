/**
 * The egress door (gateway research §5.4 step 4; tasks 4 G4):
 * `egress.tinyassets.io/c/<connection>/*`, where the sandbox's model clients send their
 * providers' own APIs with placeholder keys (`network.ts`'s `sandboxEnv`).
 *
 * 1. **The identity.** E2B's transform sets the sandbox's egress token (`x-ta-sandbox`)
 *    and the egress generation its network was made under (`x-ta-network-generation`)
 *    outside the VM. The token names its owner and carries a MAC under the routing key
 *    (`routing-mac.ts`), verified here before any stub call: a missing, malformed, forged
 *    or altered one is refused before any object is reached, and the placeholder key is
 *    never read. No directory is read: the owner's object checks the secret's hash, its
 *    lease and the lease's expiry, strongly consistent. Then the maintenance gate.
 * 2. **Admission and the reservation**, at the owner's object (`egressOpen`): the lease,
 *    the generations, the connection, its sealed endpoints, the metered call, the rate and a
 *    model reservation by the loop's own rules; or a refusal's fixed code and receipt.
 * 3. **Forward**: the request, its path after `/c/<connection>`, to the gateway's egress
 *    entrypoint (`EGRESS.forward`), metered: held to the reservation's output cap and the
 *    body length it was reserved for, its every inbound credential (the placeholder with
 *    them) stripped and the sealed one injected. The provider's answer streams back as it
 *    comes, but for the gateway's own headers (`x-ta-resealed`, which goes to the settle).
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
import { EGRESS_DEADLINE_S, type EgressCode, GENERATION_HEADER, type Outcome,
         SANDBOX_HEADER } from "../../owner/src/network";

import { type Bound, bindSandbox, rebound } from "./binder";
import { type DoorsEnv, EGRESS_ORIGIN } from "./config";
import { ownerGate } from "./control";
import { verifyToken } from "./routing-mac";
import { refusal } from "./tools";

/** The door's one route: a connection id, then the provider's own path. */
const ROUTE = /^\/c\/(c[0-9a-f]{24})(\/.*)$/;
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
  egress_lease_closed: 401, egress_policy_stale: 409, egress_network_stale: 409,
  egress_connection_refused: 403, egress_endpoint_refused: 403, egress_unmetered: 403,
  egress_length_required: 411, egress_rate: 429, egress_spend_refused: 402,
});

type Spend = { state: "succeeded"; tokens: number; cost: number } | { state: "failed" }
  | { state: "unknown" };

/** A refusal in the gateway's own error shape, so a provider SDK shows its code. */
function refused(code: string, status: number, receipt?: string | null): Response {
  return Response.json({ error: { code, message: code },
                         ...(receipt === undefined || receipt === null ? {} : { receipt }) },
                       { status, headers: { "Cache-Control": "no-store",
                                            ...(status === 429 ? { "Retry-After": "1" } : {}) } });
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
export async function serveEgress(request: Request, env: DoorsEnv, ctx: ExecutionContext,
                                  deadlineMs: number = EGRESS_DEADLINE_MS): Promise<Response> {
  const url = new URL(request.url);
  const route = ROUTE.exec(url.pathname);
  if (url.origin !== EGRESS_ORIGIN || route === null) {
    return refused("not_found", 404);
  }
  const token = request.headers.get(SANDBOX_HEADER);
  if (token === null || token === "") {
    return refused("egress_token_missing", 401);
  }
  // The MAC first: a forged, altered or sync token reaches no object, not even the gate's.
  const parsed = verifyToken(env.ROUTING_MAC_KEY, "egress", token);
  if (parsed === null) {
    return refused("egress_token_unknown", 401);
  }
  if (parsed.expires <= Date.now() / 1000) {
    return refused("egress_token_expired", 401);  // the run is over: no object is reached
  }
  const generation = request.headers.get(GENERATION_HEADER) ?? "";
  if (!GENERATION.test(generation)) {
    return refused("egress_policy_stale", 409);
  }
  const gated = await ownerGate(env, parsed.owner);
  if (gated !== null) {
    return gated;
  }
  const bound = bindSandbox(env, parsed.owner, parsed.hash, Number(generation));
  const { binding, stub } = bound;
  const connection = route[1]!;
  const path = `${route[2]!}${url.search}`;
  const length = request.headers.get("content-length");
  let opened: Awaited<ReturnType<typeof stub.egressOpen>>;
  try {
    opened = await stub.egressOpen(binding, {
      connection, method: request.method, path,
      bytes_hint: length !== null && LENGTH.test(length) ? Number(length) : null });
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
  const finish = (end: Outcome): Promise<void> => {
    if (settled === null) {
      clearTimeout(timer);
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

  const forwarded = new Request(`${EGRESS_ORIGIN}${path}`, {
    method: request.method, headers: request.headers, redirect: "manual",
    body: request.method === "GET" || request.method === "HEAD" ? null : request.body,
  });
  const sent = env.EGRESS.forward(binding.owner, connection, version, blob, forwarded,
                                  opened.metering, halt.readable);
  let answer: { response: Response; outcome: ReadableStream<Uint8Array> } | Outcome;
  try {
    answer = await Promise.race([sent, halted]);
  } catch {
    await finish("failed");
    return refused("egress_gateway_failed", 502);
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
