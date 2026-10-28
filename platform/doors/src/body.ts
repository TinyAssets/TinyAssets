/**
 * Request bodies the doors read themselves, each held to its door's bound
 * (`AUTHORIZE_BODY_MAX`, `APP_BODY_MAX`, `API_BODY_MAX`): a declared `Content-Length`
 * over it is refused before anything is read, and a body that grows past it while read
 * is refused there. Either is `413`, never a parse of the rest.
 */
export const TOO_LARGE = "the request body is too large";

/** The body as UTF-8 text, or null when it is over `max` bytes. */
export async function boundedText(request: Request, max: number): Promise<string | null> {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && !(/^\d+$/.test(declared) && Number(declared) <= max)) {
    return null;
  }
  if (request.body === null) {
    return "";
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
  return new TextDecoder().decode(whole);
}

/** The body as one JSON object; `413` or `400` as a response when it is not one. */
export async function jsonObject(request: Request, max: number):
    Promise<Record<string, unknown> | Response> {
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
  return value as Record<string, unknown>;
}
