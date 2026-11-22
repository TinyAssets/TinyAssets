/**
 * A credential's secrets taken out of what an upstream sends back, headers and body, each
 * replaced by `[redacted]`, for every sealed key alike (a model source's or a service's).
 *
 * An upstream may write a secret back in any spelling it was handed or makes: as it is, in
 * JSON (a writer may escape `/` as `\/`, Go's `<`, `>` and `&` as `<`), URL-encoded (a
 * redirect's `Location` or an error quoting the request URL, its hex in either case), and
 * base64 (a `Basic` credential echoed back, a token or body carried as base64). A secret
 * inside a base64 text takes one of three spellings by where it starts, so each is redacted
 * at all three, in both base64 alphabets, trimmed to the characters only the secret decides
 * (GitHub Actions' log masker masks the same three shifts: its `Base64StringEscapeShift1/2`).
 * Each spelling is redacted in JSON too, so a base64 or URL-encoded copy quoted in a JSON body
 * is redacted as well.
 */

const REDACTED = "[redacted]";

/** `value` as JSON writers write it inside a string, each writer's way. */
function jsonSpellings(value: string): string[] {
  const plain = JSON.stringify(value).slice(1, -1);
  return [plain, plain.replace(/\//g, "\\/"),
          plain.replace(/[<>&]/g, (char) => `\\u00${char.charCodeAt(0).toString(16)}`)];
}

/** `value` percent-encoded, as a URL component and as a form writes it, in either hex case. */
function urlSpellings(value: string): string[] {
  const forms = [encodeURIComponent(value), new URLSearchParams({ v: value }).toString().slice(2)];
  return forms.flatMap((form) => [form, form.replace(/%[0-9A-F]{2}/g, (hex) => hex.toLowerCase())]);
}

/**
 * The characters of `value`'s base64 that only `value` decides wherever it starts in the
 * encoded bytes: for each of the three starts, the characters its neighbours share dropped
 * at each end; in the standard alphabet and the URL-safe one.
 */
function base64Spellings(value: string): string[] {
  const bytes = new TextEncoder().encode(value);
  return [0, 1, 2].flatMap((shift) => {
    const placed = new Uint8Array(shift + bytes.byteLength);
    placed.set(bytes, shift);
    const encoded = btoa(String.fromCharCode(...placed));
    const lead = [0, 2, 3][shift]!;
    const trail = [0, 3, 2][placed.byteLength % 3]!;
    const core = encoded.slice(lead, encoded.length - trail);
    return [core, core.replace(/\+/g, "-").replace(/\//g, "_")];
  });
}

/** Every spelling of each secret, longest first, so a longer one is matched before its part. */
function spellings(secrets: readonly string[]): string[] {
  const all = secrets.filter((secret) => secret.length > 0).flatMap((secret) =>
    [secret, ...urlSpellings(secret), ...base64Spellings(secret)].flatMap(jsonSpellings));
  return [...new Set(all)].filter((spelling) => spelling.length > 0)
    .sort((a, b) => b.length - a.length);
}

/** One answer's redaction: its header values (`text`) and its body (`stream`). */
export interface Redaction {
  text(value: string): string;
  stream(body: ReadableStream<Uint8Array>): ReadableStream<Uint8Array>;
}

export function redaction(secrets: readonly string[]): Redaction {
  const all = spellings(secrets);
  const needles = all.map((spelling) => new TextEncoder().encode(spelling));
  // Each needle under its first byte, so a byte that starts none is passed at one look.
  const starting = new Map<number, Uint8Array[]>();
  for (const needle of needles) {
    starting.set(needle[0]!, [...(starting.get(needle[0]!) ?? []), needle]);
  }
  const longest = Math.max(0, ...needles.map((needle) => needle.byteLength));
  const mark = new TextEncoder().encode(REDACTED);
  return {
    text: (value) => all.reduce((out, spelling) => out.split(spelling).join(REDACTED), value),
    stream: (body) => longest === 0 ? body : scrubbed(body, starting, longest, mark),
  };
}

/**
 * `body` with every needle replaced by `mark`, across chunk boundaries: the last
 * `longest - 1` bytes of each chunk are held back until the next one shows whether they
 * start a needle.
 */
function scrubbed(body: ReadableStream<Uint8Array>, starting: Map<number, Uint8Array[]>,
                  longest: number, mark: Uint8Array): ReadableStream<Uint8Array> {
  let held = new Uint8Array(0);
  const scrub = (bytes: Uint8Array, final: boolean): Uint8Array[] => {
    const out: Uint8Array[] = [];
    let start = 0;
    let at = 0;
    const limit = final ? bytes.byteLength : Math.max(0, bytes.byteLength - (longest - 1));
    while (at < limit) {
      const hit = starting.get(bytes[at]!)?.find((needle) =>
        needle.byteLength <= bytes.byteLength - at
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
