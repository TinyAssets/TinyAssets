/**
 * A test's helper that re-exports another Worker's source, as `doors/test/entry.ts` does: the
 * isolation test's probe (`isolation.test.ts`) reaches a Worker's source through it.
 */
export type { OwnerDO } from "../../../owner/src/owner-do";
