/**
 * The egress door (gateway research §5.4 step 4; tasks 4 G4):
 * `egress.tinyassets.io/c/<connection>/*`, where the sandbox's model clients send their
 * providers' own APIs with placeholder keys (`network.ts`'s `sandboxEnv`).
 *
 * 1. **The identity.** E2B's transform sets the sandbox's lease token (`x-ta-sandbox`) and
 *    the policy generation its network was made under (`x-ta-policy-generation`) outside
 *    the VM. The token is resolved through the directory's `l:` entry: a missing,
 *    malformed, unknown or expired token is refused before any object is reached, and the
 *    placeholder key is never read. Then the maintenance gate.
 * 2. **Admission**, at the owner's object (`egressOpen`): the lease and its token's hash,
 *    the policy and network generations, the connection, its sealed endpoints and the
 *    rate, then a reservation; or a refusal's fixed code and receipt.
 * 3. **Forward**: the request, its path after `/c/<connection>`, to the gateway's egress
 *    entrypoint (`EGRESS.forward`), which opens the blob, holds the request to the sealed
 *    endpoints, strips every inbound credential (the placeholder with them) and injects the
 *    sealed one. The provider's answer streams back as it comes, its status and headers
 *    but the gateway's own (`x-ta-resealed`, which goes to the settle).
 * 4. **Settle** (`egressSettle`), once, on every end, with the bytes this door streamed:
 *    `complete`, `aborted` (the client stopped reading), `failed` (the gateway failed, or
 *    its stream did), `deadline` (`EGRESS_DEADLINE_S`, the gateway's own bound, kept here
 *    too) or `ended` (the object's end signal: a policy write, a suspension, a revoke, the
 *    lease's close or the owner's erasure; a signal that errors is an end as well).
 *
 * The door bills CPU only while the bytes flow; the owner's object does nothing meanwhile.
 * A lease token reaches no other door: the other doors serve `tinyassets.io` alone, and the
 * object refuses the sandbox binding at every door but the egress doors.
 */
import { EGRESS_DEADLINE_S, type EgressCode, GENERATION_HEADER, type Outcome,
         SANDBOX_HEADER } from "../../owner/src/network";

import { bindSandbox } from "./binder";
import { type DoorsEnv, EGRESS_ORIGIN, now } from "./config";
import { ownerGate } from "./control";
import { readLease } from "./directory";
import { LEASE_TOKEN, leaseTokenHash } from "./sandbox";
import { refusal } from "./tools";

/** The door's one route: a connection id, then the provider's own path. */
const ROUTE = /^\/c\/(c[0-9a-f]{24})(\/.*)$/;
const GENERATION = /^[1-9][0-9]{0,14}$/;
const LENGTH = /^[0-9]{1,15}$/;
/** The gateway's own header on its answer, for the object alone. */
const RESEALED = "x-ta-resealed";
export const EGRESS_DEADLINE_MS = EGRESS_DEADLINE_S * 1000;

/** The HTTP status each refusal is answered with. */
const STATUS: Readonly<Record<EgressCode, number>> = Object.freeze({
  egress_token_missing: 401, egress_token_unknown: 401, egress_token_expired: 401,
  egress_lease_closed: 401, egress_policy_stale: 409, egress_network_stale: 409,
  egress_connection_refused: 403, egress_endpoint_refused: 403, egress_rate: 429,
});

/** A refusal in the gateway's own error shape, so a provider SDK shows its code. */
function refused(code: string, status: number, receipt?: string): Response {
  return Response.json({ error: { code, message: code },
                         ...(receipt === undefined ? {} : { receipt }) },
                       { status, headers: { "Cache-Control": "no-store",
                                            ...(status === 429 ? { "Retry-After": "1" } : {}) } });
}

/** A refusal's code from the object's message: its fixed prefix, as the doors keep them. */
function codeOf(message: string): string {
  return /^[a-z][a-z_]{0,63}(?=:)/.exec(message)?.[0] ?? "egress_refused";
}

/** Serve one sandbox request; `deadlineMs` is the door's own bound on its stream. */
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
  if (!LEASE_TOKEN.test(token)) {
    return refused("egress_token_unknown", 401);
  }
  const hash = await leaseTokenHash(token);
  const entry = await readLease(env, hash);
  if (entry === null) {
    return refused("egress_token_unknown", 401);
  }
  if (entry.e <= now()) {
    return refused("egress_token_expired", 401);
  }
  const generation = request.headers.get(GENERATION_HEADER) ?? "";
  if (!GENERATION.test(generation)) {
    return refused("egress_policy_stale", 409);
  }
  const gated = await ownerGate(env, entry.o);
  if (gated !== null) {
    return gated;
  }
  const { binding, stub } = bindSandbox(env, hash, entry, Number(generation));
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

  // From here every end settles the reservation, once.
  const started = Date.now();
  let status: number | null = null;
  let bytes = 0;
  let resealed: unknown;
  let settled: Promise<void> | null = null;
  let stopped: Outcome | null = null;
  let upstream: ReadableStreamDefaultReader<Uint8Array> | null = null;
  const signal = opened.ended.getReader();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const finish = (outcome: Outcome): Promise<void> => {
    if (settled === null) {
      clearTimeout(timer);
      signal.cancel().catch(() => undefined);
      settled = stub.egressSettle(binding, {
        reservation, outcome, status, bytes, ...(resealed === undefined ? {} : { resealed }),
      }).then(() => undefined, (error: unknown) => {
        console.error("egress settle failed; the object settles it as lost", error);
      });
      ctx.waitUntil(settled);
    }
    return settled;
  };
  // The object's end signal and the door's deadline stop the stream, settled at once.
  let stop: (outcome: Outcome) => void = () => undefined;
  const halted = new Promise<Outcome>((resolve) => {
    stop = (outcome) => {
      if (stopped === null && settled === null) {
        stopped = outcome;
        resolve(outcome);
        upstream?.cancel().catch(() => undefined);
        void finish(outcome);
      }
    };
  });
  signal.read().then(() => stop("ended"), () => stop("ended"));
  timer = setTimeout(() => stop("deadline"), deadlineMs);

  const forwarded = new Request(`${EGRESS_ORIGIN}${path}`, {
    method: request.method, headers: request.headers, redirect: "manual",
    body: request.method === "GET" || request.method === "HEAD" ? null : request.body,
  });
  const sent = env.EGRESS.forward(binding.owner, connection, version, blob, forwarded);
  let answer: Response | Outcome;
  try {
    answer = await Promise.race([sent, halted]);
  } catch {
    await finish("failed");
    return refused("egress_gateway_failed", 502);
  }
  if (typeof answer === "string") {
    sent.then((late) => late.body?.cancel(), () => undefined);
    await finish(answer);
    return refused(`egress_${answer}`, answer === "deadline" ? 504 : 503);
  }
  status = answer.status;
  const headers = new Headers(answer.headers);
  const reseal = headers.get(RESEALED);
  headers.delete(RESEALED);
  try {
    resealed = reseal === null ? undefined : JSON.parse(reseal);
  } catch {
    resealed = undefined;  // the gateway's own header, malformed: nothing to keep
  }
  if (answer.body === null) {
    await finish("complete");
    return new Response(null, { status: answer.status, statusText: answer.statusText, headers });
  }
  const reader = answer.body.getReader();
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
        const outcome = stopped ?? (Date.now() - started >= deadlineMs ? "deadline" : "failed");
        await finish(outcome);
        controller.error(new Error(`egress_${outcome}`));
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
  return new Response(body, { status: answer.status, statusText: answer.statusText, headers });
}
