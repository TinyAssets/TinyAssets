/**
 * Raw passthrough for sandbox egress (research §5.4 step 4.3), and the credential check
 * a connection makes when it is set up (`verify`).
 *
 * The sandbox's model clients speak each provider's own API to our egress door with
 * placeholder keys; the binder admits the request at the owner's object, which hands it
 * the blob, and calls `forward`. The gateway opens the blob, requires the request's
 * method, origin and path to be among the sealed endpoints, strips every credential the
 * request carried (headers and the API-key query parameters), injects the sealed one by
 * the sealed auth template, and sends it to the sealed origin. The provider's own answer
 * streams back, status and body, so the client sees its own API; only the secret is
 * redacted from the body, in case a provider quotes it back. A call is cut at
 * `FORWARD_DEADLINE_MS`, and redirects are never followed.
 *
 * No pi-ai here: user CLIs speak their provider's wire format, and this path only moves
 * bytes. The gateway measures nothing it would keep: the binder counts the bytes it
 * streams and settles the owner's reservation.
 */
import { type Catalog, modelInfos } from "./catalog";
import { allows, bearer, type Credential } from "./credential";
import type { ModelRecord } from "./model-record";
import { type Code, codeOf, Failure, failureResponse, upstreamCode } from "./failure";
import { nativeFetch } from "./egress";
import { type Binding, type KeyringEnv, open, type Opened, type Resealed } from "./keyring";

/** The longest one forwarded request may run (§6: a policy write's bound on a stream). */
export const FORWARD_DEADLINE_MS = 5 * 60 * 1000;

/** How long the setup check waits for the provider. */
const VERIFY_DEADLINE_MS = 15 * 1000;

/** Headers that carry a credential, or route, in any provider's or proxy's convention. */
const CREDENTIAL_HEADERS: readonly string[] = Object.freeze([
  "authorization", "proxy-authorization", "x-api-key", "api-key", "x-goog-api-key", "cookie",
  "host", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
  // A method override would let a sealed `POST` act as a method that is not sealed.
  "x-http-method-override", "x-http-method", "x-method-override",
]);
/** Query parameters that carry an API key (Google's `key` among them). */
const CREDENTIAL_PARAMS: readonly string[] = Object.freeze(["key", "api_key", "access_token"]);
/** Headers that belong to one hop, never forwarded either way. */
const HOP_HEADERS: readonly string[] = Object.freeze([
  "connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade", "content-length",
]);

/** Whether a header is ours or Cloudflare's, never passed on either way. */
function internal(name: string): boolean {
  return name.startsWith("x-ta-") || name.startsWith("cf-");
}

/** The header value the sealed template makes of the credential's secret. */
export function authValue(credential: Credential): string {
  const scheme = credential.upstream.auth.scheme;
  return scheme === "" ? bearer(credential) : `${scheme} ${bearer(credential)}`;
}

const REDACTED = "[redacted]";

/**
 * Each secret as the provider could write it back: as it is, and JSON-escaped (a JSON
 * writer may escape `/` as `\/`).
 */
function spellings(secrets: readonly string[]): string[] {
  const all = secrets.filter((secret) => secret.length > 0).flatMap((secret) => [
    secret, secret.replace(/\\/g, "\\\\").replace(/"/g, "\\\"").replace(/\//g, "\\/"),
  ]);
  return [...new Set(all)].sort((a, b) => b.length - a.length);
}

/** `value` with every spelling of each of `secrets` replaced by `[redacted]`. */
export function redactText(value: string, secrets: readonly string[]): string {
  let out = value;
  for (const spelling of spellings(secrets)) {
    out = out.split(spelling).join(REDACTED);
  }
  return out;
}

/**
 * Replaces every occurrence of each spelling of each of `secrets` in a byte stream with
 * `[redacted]`, across chunk boundaries: it holds back the last `longest - 1` bytes of
 * each chunk until the next one shows whether they start a secret.
 */
export function redact(body: ReadableStream<Uint8Array>,
                       secrets: readonly string[]): ReadableStream<Uint8Array> {
  const needles = spellings(secrets).map((secret) => new TextEncoder().encode(secret));
  const longest = Math.max(0, ...needles.map((needle) => needle.byteLength));
  if (longest === 0) {
    return body;
  }
  const mark = new TextEncoder().encode(REDACTED);
  let held = new Uint8Array(0);
  const scrub = (bytes: Uint8Array, final: boolean): Uint8Array[] => {
    const out: Uint8Array[] = [];
    let start = 0;
    let at = 0;
    const limit = final ? bytes.byteLength : Math.max(0, bytes.byteLength - (longest - 1));
    while (at < limit) {
      const hit = needles.find((needle) => needle.byteLength <= bytes.byteLength - at
        && needle.every((byte, index) => bytes[at + index] === byte));
      if (hit === undefined) {
        at++;
        continue;
      }
      out.push(bytes.subarray(start, at), mark);
      at += hit.byteLength;
      start = at;
    }
    out.push(bytes.subarray(start, Math.max(start, limit)));
    held = bytes.slice(Math.max(start, limit));
    return out;
  };
  return body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      const joined = new Uint8Array(held.byteLength + chunk.byteLength);
      joined.set(held);
      joined.set(chunk, held.byteLength);
      for (const part of scrub(joined, false)) {
        if (part.byteLength > 0) {
          controller.enqueue(part);
        }
      }
    },
    flush(controller) {
      for (const part of scrub(held, true)) {
        if (part.byteLength > 0) {
          controller.enqueue(part);
        }
      }
    },
  }));
}

/** The secrets a credential holds, each redacted from what the provider sends back. */
function secretsOf(credential: Credential): string[] {
  if (credential.kind === "api_key") {
    return [credential.secret];
  }
  const { access, refresh, client_auth: auth } = credential.oauth;
  return [access, ...(refresh === undefined ? [] : [refresh]),
          ...(auth.method === "none" ? [] : [auth.secret])];
}

function refusal(code: Code, resealed?: Resealed): Response {
  return failureResponse(code, resealed === undefined
    ? undefined : { "x-ta-resealed": JSON.stringify(resealed) });
}

/**
 * Forwards `request` (its method, path, query, headers and body; its host is ignored) to
 * the credential's sealed origin under `bound`. Refusals are a fixed-code JSON answer the
 * sandbox client sees as an error; the provider's own answers pass through.
 */
export async function forward(env: KeyringEnv, bound: Binding, blob: unknown,
                              request: Request,
                              deadlineMs: number = FORWARD_DEADLINE_MS): Promise<Response> {
  let opened: Opened;
  try {
    opened = await open(env, bound, blob);
  } catch (error) {
    return refusal(codeOf(error, "internal"));
  }
  const { credential, resealed } = opened;
  let incoming: URL;
  try {
    incoming = new URL(request.url);
  } catch {
    return refusal("invalid_request", resealed);
  }
  const target = new URL(`${incoming.pathname}${incoming.search}`,
                         new URL(credential.upstream.base_url).origin);
  for (const name of CREDENTIAL_PARAMS) {
    target.searchParams.delete(name);
  }
  if (target.origin !== new URL(credential.upstream.base_url).origin
      || !allows(credential.endpoints, request.method, target)) {
    return refusal("endpoint_refused", resealed);
  }
  const headers = new Headers();
  for (const [name, value] of request.headers) {
    if (!CREDENTIAL_HEADERS.includes(name) && !HOP_HEADERS.includes(name) && !internal(name)) {
      headers.append(name, value);
    }
  }
  // `set` replaces every value the request sent under the sealed header's name.
  headers.set(credential.upstream.auth.header, authValue(credential));
  // Plain bytes back, so the redaction reads what the provider wrote.
  headers.set("accept-encoding", "identity");
  const deadline = AbortSignal.timeout(deadlineMs);
  const signal = AbortSignal.any([request.signal, deadline]);
  let upstream: Response;
  try {
    upstream = await nativeFetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? null : request.body,
      redirect: "manual",
      signal,
    });
  } catch {
    return refusal(deadline.aborted ? "deadline" : "upstream_unreachable", resealed);
  }
  const answer = new Headers();
  const secrets = secretsOf(credential);
  for (const [name, value] of upstream.headers) {
    if (!HOP_HEADERS.includes(name) && !internal(name)) {
      answer.append(name, redactText(value, secrets));
    }
  }
  // The body is rewritten when redacted, so its encoding headers no longer describe it.
  answer.delete("content-encoding");
  if (resealed !== undefined) {
    answer.set("x-ta-resealed", JSON.stringify(resealed));
  }
  const body = upstream.body === null ? null : redact(upstream.body, secrets);
  return new Response(body, { status: upstream.status, statusText: upstream.statusText,
                              headers: answer });
}

/** What a setup check found: the credential works, and the models it can be asked for. */
export interface Verified {
  readonly models: readonly ModelRecord[];
  readonly resealed?: Resealed;
}

/**
 * Checks an opened credential against its sealed `verify_url` (a `GET` among its
 * endpoints, sent like `forward` sends): a 2xx answer is a working credential, and the
 * models are pi-ai's catalog for its provider (or its declared models), with their
 * metadata, which the binder hands the owner object's `connectionCommit`. Anything else is
 * the upstream outcome's fixed code; the answer's body is never read.
 */
export async function verify(env: KeyringEnv, bound: Binding, blob: unknown,
                             catalog: Catalog): Promise<Verified> {
  const { credential, resealed } = await open(env, bound, blob);
  const url = credential.upstream.verify_url;
  if (url === undefined) {
    throw new Failure("unsupported_source");
  }
  // A sealed `verify_url` is one of the endpoints (`credential.ts`, on every open).
  const target = new URL(url);
  let answer: Response;
  try {
    answer = await nativeFetch(target, {
      method: "GET",
      headers: new Headers([[credential.upstream.auth.header, authValue(credential)],
                            ["accept", "application/json"]]),
      redirect: "manual",
      signal: AbortSignal.timeout(VERIFY_DEADLINE_MS),
    });
  } catch {
    throw new Failure("upstream_unreachable");
  }
  await answer.body?.cancel();
  if (answer.status < 200 || answer.status >= 300) {
    throw new Failure(upstreamCode(answer.status));
  }
  return Object.freeze({ models: Object.freeze(modelInfos(catalog, credential.upstream)),
                         ...(resealed === undefined ? {} : { resealed }) });
}
