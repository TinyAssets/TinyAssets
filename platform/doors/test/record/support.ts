/**
 * What the doors' recorders share (`*.record.ts`, run by `ci/record.ts`):
 * an owner signed in to its app with its agent defined, and one answer's text with the ids a
 * run mints at random taken out, so base and head print the same lines for the same answer.
 */
import type { Binding } from "../../../contract/src/binding";
import { defineMain, OPEN_RECORD, signInApp } from "../support";

/** The agent every recorder's owner has: Claude Code, signed in inside its sandbox. */
export const SIGNED_IN = Object.freeze({ engine: "claude-code",
                                         credential: "vendor_login_in_sandbox", scopes: [] });

/** `text` with each id, token, digest and timestamp a run mints in its place. */
export function clean(text: string): string {
  return text
    .replace(/user_[0-9a-f]{32}/g, "<owner>")
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, "<uuid>")
    .replace(/[A-Za-z0-9_-]{40,}/g, "<tok>")
    .replace(/[0-9a-f]{16,}/g, "<hex>")
    .replace(/\b\d{10,}\b/g, "<n>");
}

/** Owner `who` signed in to its app, its agent `main` written as `SIGNED_IN` (`defineMain`). */
export async function owned(who: string) {
  const app = await signInApp({ owner: who });
  const binding: Binding = { owner: who, caller: who, origin: null, control: OPEN_RECORD };
  await defineMain(who, app, SIGNED_IN);
  return { who, app, binding };
}
