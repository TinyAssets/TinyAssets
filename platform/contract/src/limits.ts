/**
 * The size and time limits more than one Worker holds a request to: each written once, here,
 * and read by every side (the owner's object, the doors, the gateway, the files Worker, the
 * sandbox Worker and the runner). Each is a technical ceiling with its reason beside it
 * (`limits-audit.md`).
 *
 * And, in their own section below, the bounds the owner's frozen migrations spell
 * (`owner/src/schema.ts`): frozen with that SQL text.
 */

/**
 * A chunk's most bytes, the files plane's unit: the owner's chunker cuts files at exactly
 * this size, the files Worker stores them so, the sync carries them so, and an isolate's
 * pool counts its holds in it (`slots.ts`). Frozen: the owner's schema spells it.
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
 * the owner is told (`owner/src/backup.ts`). Frozen: the owner's schema spells it.
 */
export const MANIFEST_PARTS = 16_384;

/**
 * Files this size or smaller are kept inline in the owner's object, so reading `AGENTS.md`
 * or a skill touches no bucket (a placed body's own threshold is `PLACE_BYTES`, ADR-035);
 * the sync writes a larger one through the files plane's upload.
 * Frozen: the owner's schema spells it.
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
 * calls and an inbound event, a row's or a message's body at half of it (`BODY_BYTES_MAX`,
 * room for its JSON escapes). A technical ceiling: the egress door
 * streams a model call's body and holds none of it, and the gateway, which parses it once,
 * weighs each call by its bytes and its structural tokens in slots of its pool
 * (`gateway/src/held.ts`, measured in workerd and asserted by the memory leg), so an ASCII
 * body of 16 MiB fits the pool and one with a non-ASCII character fits to 15 MiB, a heavier
 * one refused `egress_too_large`. Providers take more (Anthropic's Messages API, 128 MB),
 * past what one pool holds.
 */
export const EGRESS_BODY_MAX = 16 * 1024 * 1024;
/**
 * A request's body on the MCP door, a technical ceiling: the MCP SDK's own default
 * (`maxRequestBodySize`, 4 MiB), kept because the SDK materializes a JSON-RPC message whole,
 * so its ceiling is what the doors' isolate holds a few at a time; bodies up to
 * `BODY_BYTES_MAX` (8 MiB) go by `/api` (memshape_research.md §4).
 */
export const MCP_BODY_MAX = 4 * 1024 * 1024;
/** The consent form's body: its handle and decision, a few short fields. */
export const AUTHORIZE_BODY_MAX = 4 * 1024;
/** An E2B lifecycle event's body: a few hundred bytes in practice, the metadata's included. */
export const HOOK_BODY_MAX = 64 * 1024;

/**
 * An owner's protected session's life, in seconds (the Python's 28 800): the object's
 * session row expires so, and the doors' session cookie's `Max-Age` is the same.
 */
export const SESSION_TTL_S = 28_800;

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

/** Chunks one upload intent may hold: a 40 GiB file, policy's highest `max_file_bytes`. */
export const MAX_INTENT_CHUNKS = 10_000;

/**
 * A pasted sign-in code, one line typed to the CLI's terminal: at most what a Linux terminal
 * line holds (`N_TTY_BUF_SIZE`, 4096 bytes, its newline among them), so a longer one is no
 * code. The owner's object refuses one past it, and the runner types no more.
 */
export const SIGNIN_PASTE_MAX = 4095;

/**
 * An outside agent's `command_center_id`, in characters: an agent's registration
 * (`owner/src/ledger.ts`) and an MCP Events subscription's arguments (`doors/src/events.ts`).
 */
export const COMMAND_CENTER_MAX = 512;

// The owner's frozen bounds. Each number below is spelled in the owner's migrations, whose
// SQL text is frozen from its first deploy (`owner/src/schema.ts`, pinned by
// `owner/migrations.json`), and each module that enforces it in code reads it from here.
// So a bound is frozen too: changing one here is a new migration that changes the SQL with
// it, never an edit of this line alone. The owner's frozen-bounds guard holds each one equal
// to every place the SQL spells it (`owner/guards/frozen-bounds.test.ts`), so one changed
// without the other fails. Each is a technical ceiling of the owner's database, its 10 GB of
// which the owner-bound guard holds the worst case under half (`limits-audit.md`). Three
// frozen numbers live elsewhere: `RUNS_CEILING`, in `control.ts`, the control record's bound
// on a tier; `SEGMENT_LEAST_BYTES`, in `owner/src/segments.ts`,
// which the segments' own seal derives; and `SHARE_BYTES`, the manifest's share charge.

/** An outside client's sealed credential blob (`connections.ts`), in characters. */
export const CONNECTION_BLOB_MAX = 64 * 1024;
/**
 * The models a connection keeps, as JSON, in bytes. The largest record of pi-ai 1.4.0's
 * built-in catalogs, whole, is 607 bytes, so `maxModels` of the largest are about 607 KiB
 * (the largest catalog is about 142 KB for its 386 models).
 */
export const MODELS_JSON_MAX = 640 * 1024;
/**
 * Where a connection's credential may be forwarded, as JSON, in bytes. A source record's
 * endpoints are a few short paths (the largest bundled one is under 1 KiB), so a scope past
 * it is refused at commit, loudly, rather than kept half.
 */
export const EGRESS_JSON_MAX = 16 * 1024;
/** A connection flow's OAuth redirect URI, in characters (`connections.ts`). */
export const REDIRECT_URI_MAX = 2048;
/** The longest path an egress request names, as its row keeps it: visible ASCII. */
export const EGRESS_PATH_MAX = 2048;
/** An API key's name, in code points (`owner/src/keys.ts`; the doors say the same). */
export const KEY_NAME_MAX = 80;
/** An inbound binding's owner-held proof secret, in characters (`owner/src/inbound.ts`). */
export const INBOUND_SECRET_MAX = 4096;
/** An inbound binding's directory route (`h:<64 hex>` or `i:<app>:<kind>:<64 hex>`). */
export const INBOUND_ROUTE_MAX = 160;
/** The characters a notice's detail keeps (`owner/src/notices.ts`). */
export const NOTICE_DETAIL = 1024;
/** A stored file path's UTF-8 bytes at most (`owner/src/paths.ts`), a starter receipt's too. */
export const MAX_PATH_BYTES = 4096;
/** A grant's scopes' JSON, in UTF-8 bytes, as its row keeps it (`GRANT_LIMITS`). */
export const SCOPES_JSON_MAX = 64 * 1024;
/** A held registration's request, in characters (`CARD_LIMITS`). */
export const CARD_REQUEST_MAX = 4096;

/**
 * The placement rule's threshold (ADR-035): a collection row's, a history entry's or a
 * message's body of more UTF-8 bytes than this is never kept in the object's SQLite but in
 * R2, through the files plane. A rowid table keeps a record of at most 4,061 bytes in place
 * (`owner/src/manifest.ts` `TABLE_KEPT`); past it the record spills onto overflow pages read
 * with every read of the row. Half of that room is the body's, the other half the row's own
 * names, ids and numbers (a collection row's collection, row id, writer, creator, etag,
 * content id and files scope take up to about 1,700 bytes at the names a caller usually
 * picks), so a row at the threshold keeps in its page. Past it a body costs an R2 put ($4.50
 * a million) and get ($0.36 a million), which its SQLite storage ($0.20 a GB-month against
 * R2's $0.015) pays for within a year at 2 KiB.
 */
export const PLACE_BYTES = 2048;
/**
 * How long a garbage chunk waits before it is deleted (the schema's trigger sets it): 30
 * days, the object's own point-in-time recovery window (Durable Objects restore "to any point
 * in the past 30 days"), so a restored object, or a manifest, never names a chunk the sweep
 * took. A garbage chunk counts in no owner's storage; once an owner's garbage in its grace
 * passes the owner's storage figure, the oldest is collected early (`files.ts` `collect`),
 * never one the newest manifest names, so what the platform keeps uncounted for an owner is
 * bounded by what it counts. What the owner is told: "Deleted files are kept up to 30 days,
 * within your storage; past that the oldest go first."
 */
export const GC_GRACE_S = 30 * 86_400;
/** A backup part's entry in its generation's list: its hash, a colon, its size, a comma. */
export const PART_ENTRY = 64 + 1 + 7 + 1;
/** A segment's entry: its span (two seqs and a dash), a colon, its hash, size and comma. */
export const SEGMENT_ENTRY = 16 + 1 + 16 + 1 + 64 + 1 + 7 + 1;
/**
 * An export cursor's most characters: past the widest key it can hold (a file entry's parent
 * path, at most 4 KiB, and its name), as base64url JSON.
 */
export const CURSOR_CHARS = 16_384;
/**
 * The unshipped change log the object holds at most, in bytes: a quarter of the room under
 * the owner database's half once every other table is at its worst, an equal share with
 * each slice still to land (`limits-audit.test.ts` derives it: the largest whole MiB within
 * that share). The room is there so a generation's held changes survive an R2 outage; it
 * binds only while R2 refuses puts, or when one unit changes more than it.
 */
export const LOG_BYTES = 68 * 1024 * 1024;
/** A change log row's own bytes past its line, as the log counts it: its seq and record. */
export const LOG_ROW_BYTES = 64;
/**
 * The widest row image the change log takes, under a Durable Object row's 2 MB: derived from
 * the widest real row (review L5-1), every exported table's at its ceiling escaped at JSON's
 * worst, a connection's models list at its 640 KiB among them, rounded up to a quarter MiB
 * (the owner-bound guard holds it equal). So no change, a token refresh's included, ever
 * writes the wide mark (`changelog.ts` `LOG_WIDE`); that mark stays the backstop should a
 * later column outgrow it before the guard catches it, failing the generation, never the
 * write.
 */
export const IMAGE_MAX = 1536 * 1024;
/** A schedule's cron expression's most characters (`owner/src/cron.ts`). */
export const CRON_MAX_CHARS = 256;
/**
 * Model calls open at once (`reserved` or `dispatched`), past which a reservation is
 * refused (`model_calls_full`).
 */
export const CALLS_OPEN = 1000;
/**
 * Spend counters kept (`usage.ts`'s): a day's or a month's per source, for the days and
 * months the usage module keeps (40 and 3), each at a hundred sources, past which a new
 * source's first call in a period is refused (`model_spend_full`).
 */
export const SPEND_KEPT = 4300;
/**
 * Socket tickets outstanding at once, each single-use and at most `SOCKET_TICKET_S` long
 * (`socket-shape.ts`); past it a mint is refused (`socket_tickets_full`).
 */
export const SOCKET_TICKETS = 1024;
/**
 * A subscription's canonical arguments' characters at most (`wake-records.ts`, beside its
 * callback URL's and secret's): the subscriptions' share of the owner's database.
 */
export const ARGUMENTS_CHARS = 1024;
/**
 * Doorbell subscriptions one agent holds (its client's dots and Work chats, or the owner's
 * pasted tokens), and a sealed blob's characters at most (a callback's URL and secret,
 * sealed and encoded): each enabled agent's share of the owner's database (`maxAgents` times
 * this, the owner-bound guard). Per agent, so no client fills another agent's room, nor the
 * owner's own.
 */
export const SUBSCRIPTIONS_PER_AGENT = 16;
export const DOORBELL_BLOB_CHARS = 8192;

// The runs' rows (`owner/src/runs.ts`, migration 12):
// - `RESULT_BYTES`: a run's result text as its receipt keeps it; a longer one is kept to
//   this many bytes and marked cut (`result_cut`), the whole in the engine's transcript;
// - `ERROR_BYTES`: a run's first error, as its receipt keeps it, cut the same way;
// - `CONVERSATIONS_KEPT`: conversations kept, the least recently run one with no live run
//   forgotten first (its transcript stays in the owner's files; only its resume id goes);
// - `SCHEDULES_KEPT`: the `runs` lane's schedules, past which a new one is refused with its
//   way out (cancel one);
// - `INPUT_BYTES`: a schedule's stored input. A door's input is stored only while it waits
//   for its conversation (`QUEUE_ROWS`, `QUEUE_BYTES`); otherwise it goes to the sandbox
//   with the run's start, bounded by the door's own body bound;
// - `ENGINE_SIGNINS`: one sign-in row an engine, past every CLI record.

/** `run_` and 32 hex (`ids.ts`'s `RUN_ID`). */
export const RUN_ID_CHARS = 36;
/** `cv_` and 32 hex (`CONVERSATION_ID`). */
export const CONVERSATION_ID_CHARS = 35;
/** `sch_` and 32 hex (`SCHEDULE_ID`). */
export const SCHEDULE_ID_CHARS = 36;
export const RESULT_BYTES = 16384;
export const ERROR_BYTES = 1024;
export const CONVERSATIONS_KEPT = 10000;
export const SCHEDULES_KEPT = 1000;
export const INPUT_BYTES = 16384;
/**
 * Inputs waiting for their conversation's live run, kept beside it: at most this many, and
 * this many bytes of input in all, past which a new one is refused `run_inputs_full`. Sized
 * to what the owner-bound guard leaves with every proposed raise applied (a waiting run's
 * row is counted at a run's widest too); see `limits-audit.md`.
 */
export const QUEUE_ROWS = 256;
export const QUEUE_BYTES = 6_291_456;
/** Engines whose sign-in the runner reports, one row each: past every CLI record. */
export const ENGINE_SIGNINS = 64;
/** A sign-in report's models, as JSON: visible ASCII. */
export const SIGNIN_MODELS_CHARS = 2048;
/**
 * A client's sandbox's signed-in engines, comma-joined on its lease's row: each one a
 * served record that signs in, so far past what the records' ids join to; the lease row
 * stays within its page share at it (the owner-bound guard costs the leases at it).
 */
export const SIGNIN_LIST_CHARS = 256;
/**
 * A sign-in's vendor URL, visible ASCII: RFC 9110 section 4.1 has senders and recipients
 * support URIs of at least 8000 octets, so a longer one is not the vendor's page.
 */
export const SIGNIN_URL_MAX = 8000;
/**
 * The longest code a sign-in shows the owner: device codes are short by design (RFC 8628
 * section 6.1 recommends 8 characters for the user to type; the records' shapes, `XXXX-XXXXX`
 * and Kimi's, are within 16), so a longer "code" is not the vendor's prompt.
 */
export const SIGNIN_CODE_MAX = 16;
/**
 * The mail that starts runs (slice ID), which the schema checks without the ledger's code:
 * a ledger message's id (`msg_` and 32 hex), the ledger id of one of the owner's own agents
 * (`ta_` and 16 hex), and a message's authority as JSON (an outside client's id and family,
 * 64 hex each, its grant generation and its sign-in time, and their keys).
 */
export const MESSAGE_ID_CHARS = 36;
export const NATIVE_ID_CHARS = 19;
export const AUTHORITY_CHARS = 240;
