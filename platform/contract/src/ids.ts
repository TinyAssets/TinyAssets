/**
 * The ids the owner's object mints and every other Worker reads back, and their one maker:
 * random bytes as lowercase hex, after a kind's prefix. A door, the runner or the sandbox
 * Worker checks an id by its grammar here, never by a copy of it.
 */

/** 256 bits as hex: a digest, a secret, a client's or a family's id. */
export const HEX64 = /^[0-9a-f]{64}$/;
/** 128 bits as hex: an inbound binding's, a lease's, a files namespace's id. */
export const HEX32 = /^[0-9a-f]{32}$/;
/** 64 bits as hex: an API key's id, a generation's. */
export const HEX16 = /^[0-9a-f]{16}$/;
/** A run: `run_` and 32 hex. */
export const RUN_ID = /^run_[0-9a-f]{32}$/;
/** A conversation: `cv_` and 32 hex. */
export const CONVERSATION_ID = /^cv_[0-9a-f]{32}$/;
/** A schedule: `sch_` and 32 hex. */
export const SCHEDULE_ID = /^sch_[0-9a-f]{32}$/;
/** An engine's sign-in: `si_` and 32 hex. */
export const SIGNIN_ID = /^si_[0-9a-f]{32}$/;
/** A model connection: `c` and 24 hex. */
export const CONNECTION_ID = /^c[0-9a-f]{24}$/;
/** A doorbell subscription: `sub_` and 32 hex of a SHA-256, derived by the server, never sent. */
export const SUBSCRIPTION_ID = /^sub_[0-9a-f]{32}$/;
/**
 * Whose authority a sandbox runs under: the owner's own work (`owner`), or an outside
 * client's, by its 64-hex id. One sandbox an authority (`contract/src/sandbox.ts`).
 */
export const AUTHORITY = /^(?:owner|[0-9a-f]{64})$/;

/** Where a namespaced issuer's owners start (the doors' `ownerOf`). */
export const FOREIGN_OWNER = "iss-";
/** Where the cutover import's rehearsals' owners start (`rehearsalName`). */
export const REHEARSAL_OWNER = "rehearsal-";
/**
 * The owner names no owner issuer's subject may take, since the platform makes them
 * itself: a foreign issuer's, and a rehearsal's (whose discard is no erasure). Kept
 * reserved after the cutover, at no cost.
 */
export const RESERVED_OWNER_PREFIXES: readonly string[] = Object.freeze([FOREIGN_OWNER,
                                                                         REHEARSAL_OWNER]);

/** Whether `name` starts as only a name the platform makes does. */
export function reservedOwner(name: string): boolean {
  return RESERVED_OWNER_PREFIXES.some((prefix) => name.startsWith(prefix));
}

/** `bytes` random bytes as lowercase hex (twice as many characters). */
export function randomHex(bytes: number): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(bytes)),
                    (byte) => byte.toString(16).padStart(2, "0")).join("");
}
