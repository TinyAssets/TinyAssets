/**
 * The doors' request kit: what every door reads a request and answers it with, written once.
 *
 * A body is read to its door's bound (`contract/src/limits.ts`): a declared `Content-Length`
 * over it is refused before anything is read, and a body that grows past it while read is
 * refused there. Either is `413`, never a parse of the rest.
 *
 * Every JSON and text answer is `Cache-Control: no-store`: each is one owner's, or one
 * request's, and no cache may keep it.
 */
import type { JsonBody } from "../../contract/src/owner";

export const TOO_LARGE = "the request body is too large";

export const NO_STORE: Readonly<Record<string, string>> =
  Object.freeze({ "Cache-Control": "no-store" });

function noStore(headers: HeadersInit): Headers {
  const out = new Headers(headers);
  out.set("Cache-Control", "no-store");
  return out;
}

/** A JSON answer. */
export function answer(status: number, body: unknown, headers: HeadersInit = {}): Response {
  return Response.json(body, { status, headers: noStore(headers) });
}

/** A JSON failure: `{error: <message>}`. */
export function failure(status: number, message: string, headers: HeadersInit = {}): Response {
  return answer(status, { error: message }, headers);
}

/** A plain-text answer. */
export function plain(status: number, message: string, headers: HeadersInit = {}): Response {
  const out = noStore(headers);
  out.set("Content-Type", "text/plain; charset=utf-8");
  return new Response(message, { status, headers: out });
}

/** A `302` to `location`. */
export function redirect(location: string, headers: HeadersInit = {}): Response {
  const out = noStore(headers);
  out.set("Location", location);
  return new Response(null, { status: 302, headers: out });
}

/** The media type of `Content-Type`, lowercased, without parameters; "" when absent. */
export function mediaType(request: Request): string {
  return (request.headers.get("Content-Type") ?? "").split(";")[0]!.trim().toLowerCase();
}

/** The `Authorization: Bearer` token, or null. */
export function bearer(request: Request): string | null {
  const match = /^Bearer ([^\s]+)$/.exec(request.headers.get("Authorization") ?? "");
  return match === null ? null : match[1]!;
}

/** Whether `value` is a plain object of every key of `required`, and any of `optional`. */
export function exactKeys(value: unknown, required: readonly string[],
                          optional: readonly string[] = []): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const keys = Object.keys(value);
  return required.every((key) => keys.includes(key))
    && keys.every((key) => required.includes(key) || optional.includes(key));
}

/** `bytes` random bytes, base64url, unpadded: a token a URL or a header carries as it is. */
export function randomToken(bytes: number): string {
  return Buffer.from(crypto.getRandomValues(new Uint8Array(bytes))).toString("base64url");
}

/**
 * The body read to `max` bytes, each part handed to `take`: false once it is over, nothing
 * past `max` read.
 */
async function bounded(request: Request, max: number,
                       take: (part: Uint8Array) => void): Promise<boolean> {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && !(/^\d+$/.test(declared) && Number(declared) <= max)) {
    return false;
  }
  if (request.body === null) {
    return true;
  }
  const reader = request.body.getReader();
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      return true;
    }
    size += value.byteLength;
    if (size > max) {
      await reader.cancel();
      return false;
    }
    take(value);
  }
}

/**
 * The body as UTF-8 text, or null when it is over `max` bytes: decoded as it is read, so no
 * copy of its bytes is held beside the text.
 */
export async function boundedText(request: Request, max: number): Promise<string | null> {
  const decoder = new TextDecoder();
  // Its parts joined once (a string built by `+=` is a rope its parse would copy again).
  const parts: string[] = [];
  if (!await bounded(request, max, (part) => parts.push(decoder.decode(part, { stream: true })))) {
    return null;
  }
  parts.push(decoder.decode());
  return parts.join("");
}

/** The body's bytes, verbatim, or null when it is over `max` bytes. */
export async function boundedBytes(request: Request, max: number): Promise<Uint8Array | null> {
  const parts: Uint8Array[] = [];
  if (!await bounded(request, max, (part) => parts.push(part))) {
    return null;
  }
  const whole = new Uint8Array(parts.reduce((size, part) => size + part.byteLength, 0));
  let at = 0;
  for (const part of parts) {
    whole.set(part, at);
    at += part.byteLength;
  }
  return whole;
}

/** The body as one JSON object; `413` or `400` as a response when it is not one. */
export async function jsonObject(request: Request, max: number):
    Promise<JsonBody | Response> {
  const text = await boundedText(request, max);
  if (text === null) {
    return failure(413, TOO_LARGE);
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    value = null;
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return failure(400, "the request is one JSON object");
  }
  return value as JsonBody;
}
