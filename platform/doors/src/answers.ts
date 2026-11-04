/**
 * The doors' requests and answers under the isolate's one memory budget
 * (`owner/src/memory.ts`): a tool call on `/mcp`, `/api` or the owner app holds its request
 * body, its parse, the call's arguments as they are sent, and its answer, under one grant
 * taken before the body is read and released when the answer is made. A grant that does not
 * fit is answered `503` with `Retry-After`, the request unread: nothing is lost. What the
 * runtime holds of an answer it is still sending is the runtime's, bounded by
 * `RESULT_CHARS`.
 *
 * Each answer is its result's JSON, once: measured first (`jsonBytes`, nothing built); past
 * `RESULT_CHARS` it is a bug the owner's pages (`owner/src/pages.ts`) should have made
 * impossible, logged in full and failed as `internal error` (Fact 8), never cut.
 */
import { jsonBytes, RESULT_CHARS } from "../../owner/src/encoded";
import { BUSY, grant, isBusy } from "../../owner/src/memory";

/**
 * What a request holds a byte of its body, at its worst: the text decoded as it is read
 * (`boundedText`) and the arguments parsed from it, then those arguments and their
 * serialized copy as the call sends them; two bytes a byte for a body of two-byte
 * characters, as measured in workerd (`limits-audit.md`; `owner/test/memory-peaks.mjs`
 * holds the measurement to it).
 */
export const REQUEST_FACTOR = 2;
/**
 * What one answer holds: its result as the call returned it, its JSON text (two bytes a
 * unit), the JSON-RPC envelope escaping that text again (at most six bytes a byte) and its
 * bytes, all within `RESULT_CHARS`.
 */
export const ANSWER_GRANT = 16 * RESULT_CHARS;

/**
 * The grant a request with a body of at most `max` bytes takes, with an answer of at most
 * `answer` (one tool answer's, unless its door answers more).
 */
export function requestGrant(request: Request, max: number,
                             answer: number = ANSWER_GRANT): number {
  const declared = request.headers.get("Content-Length");
  // A declared length past the door's ceiling is refused `413` before a byte is read.
  const bytes = declared === null ? max : /^\d+$/.test(declared) && Number(declared) <= max
    ? Number(declared) : 0;
  return REQUEST_FACTOR * bytes + answer;
}

/** `serve` under the request's grant; `503` when the isolate's memory has no room now. */
export async function answered(request: Request, max: number,
                               serve: () => Promise<Response>,
                               answer: number = ANSWER_GRANT): Promise<Response> {
  let held;
  try {
    held = grant(requestGrant(request, max, answer), "a request and its answer");
  } catch (error) {
    if (!isBusy(error)) {
      throw error;
    }
    return Response.json({ error: BUSY }, {
      status: 503, headers: { "Retry-After": "1", "Cache-Control": "no-store" } });
  }
  try {
    return await serve();
  } finally {
    held.release();
  }
}

/** A tool's result's one text, its JSON, held to `RESULT_CHARS`. */
export function resultText(name: string, result: unknown): string {
  const bytes = jsonBytes(result);
  if (bytes > RESULT_CHARS) {
    console.error(`the ${name} tool answered ${bytes} bytes of JSON, past the ` +
                  `${RESULT_CHARS} one answer holds`);
    throw new Error("internal error");
  }
  return JSON.stringify(result);
}

/** A tool's result as a JSON response (`/api`, the owner app's `tools/<name>`). */
export function answerJson(name: string, result: unknown): Response {
  return new Response(resultText(name, result),
                      { headers: { "Content-Type": "application/json",
                                   "Cache-Control": "no-store" } });
}
