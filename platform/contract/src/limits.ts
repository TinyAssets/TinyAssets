/**
 * The size and time limits more than one Worker holds a request to: each written once, here,
 * and read by every side (the owner's object, the doors, the gateway, the files Worker, the
 * sandbox Worker and the runner). Each is a technical ceiling with its reason beside it
 * (`limits-audit.md`).
 */

/**
 * A chunk's most bytes, the files plane's unit: the owner's chunker cuts files at exactly
 * this size, the files Worker stores them so, the sync carries them so, and an isolate's
 * pool counts its holds in it (`slots.ts`).
 */
export const CHUNK_BYTES = 4 * 1024 * 1024;
/**
 * The most bytes one `FILES` call moves (a put or a get): two whole chunks, well under RPC's
 * 32 MiB, so no call holds much of the isolate's memory.
 */
export const BATCH_BYTES = 2 * CHUNK_BYTES;
/** R2's most keys in one delete, and so the most chunks one alarm run deletes. */
export const DELETE_BATCH = 1000;
/**
 * The most objects one backup generation has, its base parts and its log segments together:
 * 4 MiB each, 64 GiB. Past the worst case of the stream the owner's object can hold, escaping
 * included (`owner/guards/owner-bound.test.ts`, "the nightly manifest's stream"), and its
 * lists (`PART_ENTRY` each) fit a SQLite row's 2 MB. A generation past it is never retried:
 * the owner is told (`owner/src/backup.ts`).
 */
export const MANIFEST_PARTS = 16_384;

/**
 * Files this size or smaller are kept inline in the owner's object, so reading `AGENTS.md`
 * or a skill touches no bucket (a placed body's own threshold is `PLACE_BYTES`, ADR-035);
 * the sync writes a larger one through the files plane's upload.
 */
export const INLINE_BYTES = 4096;
/** Ops in one sync commit. */
export const MAX_SYNC_OPS = 1000;
/** The most rows one list or feed page holds. */
export const MAX_LIST = 1000;
/** An upload intent's id: this many random bytes, as hex. */
export const INTENT_ID_BYTES = 16;
/** A sandbox lease's life without a renewal; a run's heartbeat lapses as soon. */
export const LEASE_TTL_S = 60;
/**
 * A sync door's body: two 4 MiB chunks' base64 (the files plane's most a call,
 * `BATCH_BYTES`), or the object's read of them, with room for the request's own fields.
 */
export const SYNC_BODY_MAX = 12 * 1024 * 1024;

/**
 * The longest one sandbox request may stream, in seconds: a day, the sandbox's own most (an
 * E2B Pro session runs at most 24 hours, `engines-replan.md` §3), since a Worker's
 * subrequest has no time limit while its client stays connected (Cloudflare's Workers
 * limits). The egress door, the owner's object and the gateway's forward all hold a stream
 * to it, so a stream the gateway never ends is still settled.
 */
export const EGRESS_DEADLINE_S = 86_400;
/**
 * The most bytes one request body a Worker reads whole: the platform's one ceiling for it,
 * the egress door's and the gateway's model call, the API's and the owner surface's tool
 * calls (`API_BODY_MAX`, `APP_BODY_MAX`), a row's or a message's body at half of it
 * (`BODY_BYTES_MAX`, room for its JSON escapes). A technical ceiling: the egress door
 * streams a model call's body and holds none of it, and the gateway, which parses it once,
 * weighs each call by its bytes and its structural tokens in slots of its pool
 * (`gateway/src/held.ts`, measured in workerd and asserted by the memory leg), so an ASCII
 * body of 16 MiB fits the pool and one with a non-ASCII character fits to 15 MiB, a heavier
 * one refused `egress_too_large`. Providers take more (Anthropic's Messages API, 128 MB),
 * past what one pool holds.
 */
export const EGRESS_BODY_MAX = 16 * 1024 * 1024;

/**
 * How long a call waits for an isolate's pool units before it is refused `busy`: the queue's
 * own term. Neither Claude's nor ChatGPT's connectors document how long they wait for a tool
 * call (users report about 60 s to 5 minutes, 2026-11-04); the MCP SDK's default request
 * timeout is 60 s (`DEFAULT_REQUEST_TIMEOUT_MSEC`), the shortest a client is known to keep.
 * A waiting call is answered within a sixth of it, so the refusal is told and retried while
 * the client still waits, the rest left to the call's own work. The memory leg checks that a
 * full queue turns over well within it at the measured step.
 */
export const SLOT_WAIT_MS = 10_000;

/**
 * The live API keys an owner holds at most: its owner-set clients' bound
 * (`GRANT_LIMITS.maxOwnerClients`, which a test holds equal); the doors refuse a key past
 * it before they reach the owner's object.
 */
export const MAX_KEYS = 1_000;
