/**
 * A test's helper outside every Worker's source, re-exporting the contract: the isolation
 * test's probe (`isolation.test.ts`) reaches the contract through it, read from disk.
 */
export type { Refusal } from "../../../contract/src/refusal";
