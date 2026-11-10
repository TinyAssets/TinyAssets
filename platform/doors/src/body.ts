/**
 * Request bodies the doors read themselves, each held to its door's bound
 * (`AUTHORIZE_BODY_MAX`, `APP_BODY_MAX`, `API_BODY_MAX`): a declared `Content-Length`
 * over it is refused before anything is read, and a body that grows past it while read
 * is refused there. Either is `413`, never a parse of the rest.
 */
import type { JsonBody } from "../../contract/src/owner";
import { principal } from "../../contract/src/text";

export const TOO_LARGE = "the request body is too large";

/**
 * The body as UTF-8 text, or null when it is over `max` bytes: decoded as it is read, so no
 * copy of its bytes is held beside the text.
 */
export async function boundedText(request: Request, max: number): Promise<string | null> {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && !(/^\d+$/.test(declared) && Number(declared) <= max)) {
    return null;
  }
  if (request.body === null) {
    return "";
  }
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  // Its parts joined once (a string built by `+=` is a rope its parse would copy again).
  const parts: string[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    size += value.byteLength;
    if (size > max) {
      await reader.cancel();
      return null;
    }
    parts.push(decoder.decode(value, { stream: true }));
  }
  parts.push(decoder.decode());
  return parts.join("");
}

/** The body's bytes, verbatim, or null when it is over `max` bytes. */
export async function boundedBytes(request: Request, max: number): Promise<Uint8Array | null> {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && !(/^\d+$/.test(declared) && Number(declared) <= max)) {
    return null;
  }
  if (request.body === null) {
    return new Uint8Array(0);
  }
  const reader = request.body.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    size += value.byteLength;
    if (size > max) {
      await reader.cancel();
      return null;
    }
    parts.push(value);
  }
  const whole = new Uint8Array(size);
  let at = 0;
  for (const part of parts) {
    whole.set(part, at);
    at += part.byteLength;
  }
  return whole;
}

/**
 * The owner a queue's `{owner}` message names (the ring's, the erasure's), or null when the
 * message is out of shape.
 */
export function queuedOwner(body: unknown): string | null {
  if (typeof body !== "object" || body === null || Object.keys(body).join() !== "owner") {
    return null;
  }
  try {
    return principal((body as { owner: unknown }).owner);
  } catch {
    return null;
  }
}

/** The body as one JSON object; `413` or `400` as a response when it is not one. */
export async function jsonObject(request: Request, max: number):
    Promise<JsonBody | Response> {
  const text = await boundedText(request, max);
  if (text === null) {
    return Response.json({ error: TOO_LARGE }, { status: 413 });
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    value = null;
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return Response.json({ error: "the request is one JSON object" }, { status: 400 });
  }
  return value as JsonBody;
}
