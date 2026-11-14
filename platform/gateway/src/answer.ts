/**
 * The gateway's one bounded reader (untangle B7): the only answer bodies it reads whole, an
 * OAuth endpoint's (`oauth.ts`) and a doorbell's verification echo (`doorbell.ts`), each
 * read to a bound of its own. A provider's model answer is never read whole: it streams
 * through (`forward.ts`).
 */
import { utf8Decoder } from "../../contract/src/text";

/**
 * `answer`'s body as JSON, read to at most `max` bytes and its stream released either way:
 * `undefined` for no body, one past `max`, or one that is not strict UTF-8 JSON. A body
 * whose read fails rejects.
 */
export async function boundedJson(answer: Response, max: number): Promise<unknown> {
  const reader = answer.body?.getReader();
  if (reader === undefined) {
    return undefined;
  }
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      size += value.byteLength;
      if (size > max) {
        return undefined;
      }
      parts.push(value);
    }
  } finally {
    // A stream read to its end, or errored, has nothing left to release.
    await reader.cancel().catch(() => undefined);
  }
  const decoder = utf8Decoder("strip");
  try {
    return JSON.parse(parts.map((part) => decoder.decode(part, { stream: true })).join("")
                      + decoder.decode());
  } catch {
    return undefined;
  }
}
