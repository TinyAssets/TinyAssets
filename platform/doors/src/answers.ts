/**
 * The doors' tool answers: a tool's result is its JSON once, measured first (`jsonBytes`,
 * nothing built); past `RESULT_CHARS` it is a bug the owner's pages (`owner/src/pages.ts`)
 * should have made impossible, logged in full and failed as `internal error` (Fact 8),
 * never cut.
 */
import { jsonBytes, RESULT_CHARS } from "../../contract/src/encoded";

import { NO_STORE } from "./kit";

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
                      { headers: { "Content-Type": "application/json", ...NO_STORE } });
}
