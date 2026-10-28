/**
 * The trusted message ledger (ADR-025, S30), in the owner's own object (ADR-032).
 *
 * Ported from `tinyassets/broker/message_ledger.py` (S3a.1, S3a.2, S3b) and the
 * `message_bodies` half of `tinyassets/broker/conversations.py` (origin/main 8b415f569),
 * whose rules it keeps: who sent what to whom, and who read it.
 *
 * - The agent registry (`outside_agents`): one outside identity is at most one enabled
 *   agent. It registers only from the verified origin the binder passes, as an `api_key`
 *   or an `mcp` source; delivery is `pull` or `mcp_event`. Its wake limits are owner
 *   policy, set by the owner alone and never by a registration. The owner may delete an
 *   agent, which purges its mail, wakes and receipts.
 * - ROOM_POST: the sender is the binder's origin (none means the owner), never a field.
 *   The room is `dm:<agent>`. A resend with the same client message id and body hash
 *   returns the original; another hash is a `conflict`. One transaction writes the
 *   message, its body, its recipient, the parent's `replied` state, the `reply` receipt
 *   and the recipient's wake.
 * - The idempotency window: a resend is matched against the messages the ledger holds.
 *   A message is evicted only once every recipient has acked it and the ledger is at a
 *   ceiling, so a resend is deduplicated at least until its recipient acks; after its
 *   eviction the same client message id is a new message. There is no tombstone: a
 *   ledger of evicted keys would need its own ceiling and eviction, which only moves the
 *   same window.
 * - MAILBOX_RECEIPT: a read pages whole messages by bytes, at least one, and marks and
 *   receipts only the ones it returns; an ack never moves back and never passes the
 *   reader's own newest message, and an ack at or behind the cursor is a no-op, so a
 *   retried ack succeeds. A body whose hash no longer matches is withheld.
 * - Wakes (S3b, W3) are data on the object's one schedule (`ledger_wakes`): one open
 *   wake per ringing recipient (`one_open_wake`), claimed under a lease, settled from the
 *   doorbell's answer, and never replayed once a byte may have left. Status comes only
 *   from receipts.
 * - Revokes are fenced like claims. Each `mailbox.message` subscribe opens the agent's
 *   next subscription generation, which the doorbell tags its callback with. Disabling
 *   an agent, moving it off `mcp_event` or deleting it, when it has subscribed since its
 *   last revoke, schedules one revoke carrying that generation, and the doorbell erases
 *   only callbacks at or before it: a stale revoke still erases the old callback but
 *   never a re-enabled agent's newer one. A revoke is safe to repeat. A deleted agent
 *   whose revoke has not run stays as a tombstone in the registry (so the registry bound
 *   bounds pending revokes) until its revoke settles.
 *
 * - A wake is sent only while its agent's outside client still holds a grant
 *   (`GrantReader.live`), and a changed or revoked grant ends the client's agents' open
 *   wakes and revokes their callbacks (`suppressClient`, P0-3's M3).
 *
 * Every change runs in one `transactionSync`, so a refusal leaves nothing behind. The
 * object is its owner, so the Python's `owner` columns are gone. The owner's session
 * check and the refusal receipt of a refused register, disable or delete are the
 * object's (`owner-do.ts`, `sessions.ts`, `policy.ts`), as the Python's router op ran
 * them around the ledger.
 */
import { digest } from "./collections";
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import { codePoints, isPrintable, utf8Bytes } from "./text";

/** The reader and sender id of the object's owner. An agent id is `agent_` + hex. */
export const OWNER = "owner";
export const DELIVERY_METHODS: readonly string[] =
  Object.freeze(["mcp_event", "routine_fire", "webhook", "a2a", "pull"]);
/** S3b admits the `mailbox.message` doorbell beside pull. Routine and webhook wakes wait
 * for the credential-vault reshape (S9); a2a for its own slice. */
const ADMITTED_DELIVERY: readonly string[] = Object.freeze(["pull", "mcp_event"]);
/** The admitted methods that ring; a pull agent is never woken (W3). */
const WAKING: readonly string[] = Object.freeze(["mcp_event"]);
/** A claimed send's lease: past it, the may-have-sent mark decides whether a byte left. */
export const WAKE_LEASE_S = 60;
export const HOUR_S = 3600;
export const MAX_BODY = 16384;
const KIB = 1024;
const MIB = 1024 * KIB;
/** The open schedule rows: the `wakes_open` index's own predicate, so the planner uses it. */
const OPEN = "state IN ('pending', 'rate_limited')";
/** The states a wake never leaves: its outcome is known, or unknown and never replayed. */
const SETTLED: readonly string[] = Object.freeze(["sent", "unknown", "failed", "suppressed"]);
/** The receipt each settled wake leaves; `wake_sent` alone means the outcome is unknown. */
const WAKE_RECEIPTS: Readonly<Record<string, string>> = Object.freeze({
  sent: "wake_accepted", rate_limited: "rate_limited", failed: "failed", unknown: "wake_sent",
});
const HEX64 = /^[0-9a-f]{64}$/;
const KEY_ID = /^[0-9a-f]{16}$/;
const REASON = /^[a-z_]{1,32}$/;
const SECRETISH = /(ta_key_|bearer|eyJ|sk-|sk_|https?[:/]|token[=:])/i;
const LONE_SURROGATE = /\p{Cs}/u;

/**
 * Per-agent wake limits (W3): owner policy, stored per agent with the codespec's
 * defaults and changed only by the owner (`policyWakeLimits`). The plan of record ("Doors,
 * wakes and outside agents") has owner policy set per-agent caps and batching, 12 an
 * hour by default: Claude's scheduled-task fire allows 30 an hour per task, and a woken
 * assistant pulls its whole mailbox anyway, so a minute's batch costs nothing and folds
 * a burst into one ring. The schema's column defaults are these numbers.
 */
export const WAKE_DEFAULTS = Object.freeze({ maxWakesPerHour: 12, batchWindowS: 60 });
export const WAKE_SETTINGS = Object.freeze({
  maxWakesPerHour: Object.freeze({ low: 1, high: 60 }),
  batchWindowS: Object.freeze({ low: 0, high: 3600 }),
});
/** A 429's Retry-After, clamped to 1 s to one day; an unreadable one waits a minute. */
export const RETRY_AFTER = Object.freeze({ low: 1, high: 86_400, unreadable: 60 });

/**
 * Owner-wide bounds on the ledger's growth (the Python's owed L5). Each is a ceiling a
 * write is refused at, or a retention the ledger prunes to inside the same transaction,
 * so no sweeper is needed and no table grows without bound. Each is read from a counter
 * the schema's triggers keep (`ledger_usage`, `ledger_readers`, `outside_agents`) or an
 * index range, never a scan.
 *
 * Unacked mail is reserved, not shared. The owner's mailbox holds at most
 * `maxOwnerUnacked` messages and `maxOwnerUnackedBytes` unacked; each enabled agent's
 * at most `maxAgentUnacked` and `maxAgentUnackedBytes`; and `maxAgents` enabled agents'
 * mailboxes plus the owner's fit inside the ceilings (`checkLimits`). Disabling an agent
 * releases its unacked mail, and deleting one purges it, so only enabled agents hold
 * any. Hence a post that passes its own mailbox's bound always finds an acked message to
 * evict when the ledger is at a ceiling: a full mailbox refuses only posts to it, never
 * the owner's mail to another agent. One agent's share of the owner's mailbox is
 * `maxSenderUnacked` and `maxSenderUnackedBytes`, a quarter of it: a few busy agents can
 * fill it, which holds agent mail until the owner acks, back-pressure only the owner can
 * clear and always can. Per agent the same holds the other way: an agent that rarely
 * reads refuses the owner's posts to it after about 7 maximum-size bodies (480 KiB of
 * 64 KiB) or 80 short ones, until it acks.
 *
 * How they were chosen. The plan of record costs an owner at about 10 MB of trusted
 * state; that is the typical case, short text that is acked and evicted. These are
 * ceilings, what one owner can be made to hold: 10,000 messages and 64 MiB of bodies
 * (the collections' owner-wide bounds), and 50,000 receipts (five per message's worth,
 * about 10 MB). All of it held is about seven times the plan's typical state, and far
 * under the Durable Object storage limit of 10 GB per object, which bounds every object
 * whatever is chosen here (the plan's decision changes if an owner needs more). The
 * mailboxes split the ceilings: a quarter of the bytes for the owner's, the rest across
 * 100 enabled agents, 80 messages and 480 KiB each, room for several dozen 16 kB bodies
 * an agent has not acked yet. A settled wake's receipt is its record, so the row is kept
 * a week (enough to debug a doorbell) and at most 1,000 of them; the hourly cap reads
 * its own one-hour `ledger_rings` instead.
 */
export interface LedgerLimits {
  /** Enabled agents: what the mailboxes are reserved for. */
  readonly maxAgents: number;
  /** Registered agents, enabled or not; the owner deletes a disabled one to make room. */
  readonly maxAgentRows: number;
  /** Messages held; past it the oldest message no recipient holds is evicted. */
  readonly maxMessages: number;
  /** Body bytes held, under the same eviction. */
  readonly maxBodyBytes: number;
  /** Messages, and their bytes, the owner has not acked. */
  readonly maxOwnerUnacked: number;
  readonly maxOwnerUnackedBytes: number;
  /** Messages, and their bytes, one enabled agent has not acked. */
  readonly maxAgentUnacked: number;
  readonly maxAgentUnackedBytes: number;
  /** One outside agent's share of the owner's mailbox. */
  readonly maxSenderUnacked: number;
  readonly maxSenderUnackedBytes: number;
  /** Receipt rows kept, newest first. */
  readonly maxReceipts: number;
  /** How long a settled wake row is kept; its receipt is the record. */
  readonly settledWakeRetentionS: number;
  /** Settled wake rows kept, newest first. */
  readonly maxSettledWakes: number;
  readonly maxRead: number;
}

export const LEDGER_LIMITS: LedgerLimits = Object.freeze({
  maxAgents: 100,
  maxAgentRows: 1_000,
  maxMessages: 10_000,
  maxBodyBytes: 64 * MIB,
  maxOwnerUnacked: 2_000,
  maxOwnerUnackedBytes: 16 * MIB,
  maxAgentUnacked: 80,
  maxAgentUnackedBytes: 480 * KIB,
  maxSenderUnacked: 500,
  maxSenderUnackedBytes: 4 * MIB,
  maxReceipts: 50_000,
  settledWakeRetentionS: 7 * 24 * HOUR_S,
  maxSettledWakes: 1_000,
  maxRead: 100,
});

/**
 * Refuse limits whose mailboxes do not fit the ceilings: with them, unacked mail alone
 * could fill the ledger and eviction would stall.
 */
export function checkLimits(limits: LedgerLimits): LedgerLimits {
  const problems: string[] = [];
  if (limits.maxAgents * limits.maxAgentUnacked + limits.maxOwnerUnacked > limits.maxMessages) {
    problems.push("the mailboxes' messages exceed maxMessages");
  }
  if (limits.maxAgents * limits.maxAgentUnackedBytes + limits.maxOwnerUnackedBytes
      > limits.maxBodyBytes) {
    problems.push("the mailboxes' bytes exceed maxBodyBytes");
  }
  if (limits.maxAgents > limits.maxAgentRows) {
    problems.push("maxAgents exceeds maxAgentRows");
  }
  if (problems.length) {
    throw new Error(`invalid ledger limits: ${problems.join("; ")}`);
  }
  return limits;
}

/** A verified outside client: what the binder resolved the caller's credential to. */
export interface Origin {
  readonly client: string;
  readonly family: string;
}

/** How the binder verified an origin: an API key (`ref` is its key id) or an MCP client. */
export interface Source {
  readonly kind: "api_key" | "mcp";
  readonly ref: string;
}

export type Agent = {
  readonly agent_id: string;
  readonly command_center_id: string;
  readonly outside_client: string;
  readonly outside_family: string;
  readonly source: string;
  readonly source_ref: string;
  readonly display_name: string;
  readonly delivery_method: string;
  readonly recipe_id: string | null;
  readonly revision: number;
  readonly disabled_reason: string | null;
  readonly created_at: number;
  readonly max_wakes_per_hour: number;
  readonly batch_window_s: number;
  /** Messages this agent sent that the owner has not acked yet, and their bytes. */
  readonly unacked_to_owner: number;
  readonly unacked_to_owner_bytes: number;
  /** The newest subscription generation, and the newest one a revoke was scheduled for. */
  readonly subscription: number;
  readonly revoked_through: number;
  readonly deleted_at: number | null;
};

export interface Registration {
  readonly command_center_id: unknown;
  readonly display_name: unknown;
  readonly delivery_method: unknown;
  readonly recipe_id: unknown;
}

export interface Posted {
  readonly message_id: string;
  readonly recipients: string[];
  readonly held: boolean;
  readonly paused_reason: string;
}

export type Integrity = "ok" | "tampered" | "missing";

export interface Mail {
  readonly seq: number;
  readonly message_id: string;
  readonly room_id: string;
  readonly sender_kind: string;
  readonly sender_id: string;
  readonly parent_id: string | null;
  readonly chain_depth: number;
  readonly created_at: number;
  readonly reply_s: number | null;
  readonly body: string | null;
  readonly integrity: Integrity;
}

export interface MailPage {
  readonly reader: string;
  readonly cursor: number;
  readonly messages: Mail[];
  readonly more: boolean;
}

/** A row of the object's schedule: an outside agent's wake, or a revoke of its callbacks. */
export type Wake = {
  readonly wake_id: string;
  readonly agent_id: string;
  readonly kind: "wake" | "revoke";
  readonly state: string;
  readonly due: number;
  readonly claim_id: string | null;
};

/** What a doorbell may carry: identifiers and the mailbox cursor, never a body (S31). */
export interface WakeTarget {
  readonly wake_id: string;
  readonly claim_id: string;
  readonly agent_id: string;
  readonly command_center_id: string;
  readonly client: string;
  readonly family: string;
  readonly cursor: number;
  readonly through: number;
}

/**
 * A claimed revoke: erase this agent's callbacks at or before `generation`, never a
 * newer one, then settle it `sent`.
 */
export interface RevokeTarget {
  readonly wake_id: string;
  readonly claim_id: string;
  readonly agent_id: string;
  readonly generation: number;
}

export type Claim =
  | { readonly outcome: "claimed"; readonly target: WakeTarget }
  | { readonly outcome: "revoke"; readonly target: RevokeTarget }
  /** The agent's hourly cap is spent; the wake is due again at `due`. */
  | { readonly outcome: "capped"; readonly due: number }
  /** The agent is gone, disabled or no longer rings: its open wakes are suppressed. */
  | { readonly outcome: "suppressed"; readonly agent_id: string }
  | { readonly outcome: "not_due" };

/** A doorbell's answer. `retry_after` is a 429's, in seconds. */
export interface Outcome {
  readonly state: "sent" | "rate_limited" | "failed" | "unknown" | "suppressed";
  readonly status?: number;
  readonly retry_after?: number | null;
  readonly reason?: string;
}

type Settle = Outcome | { readonly state: "pending"; readonly reason?: string };

type Usage = {
  agents: number;
  enabled_agents: number;
  messages: number;
  body_bytes: number;
  settled_wakes: number;
  tombstones: number;
};

type StoredMessage = {
  seq: number;
  message_id: string;
  command_center_id: string;
  room_id: string;
  sender_kind: string;
  sender_id: string;
  client_message_id: string;
  body_sha256: string;
  parent_id: string | null;
  chain_root: string;
  chain_depth: number;
  created_at: number;
  body_bytes: number;
  unacked: number;
};

type StoredWake = {
  wake_id: string;
  agent_id: string;
  kind: "wake" | "revoke";
  state: string;
  due: number;
  claim_id: string | null;
  attempts: number;
  marked: number;
  through_seq: number | null;
  generation: number | null;
};

export function clock(): number {
  return Date.now() / 1000;
}

function token(bytes: number): string {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Python's `json.dumps(detail, sort_keys=True)` for a flat detail. */
function canonical(detail: Record<string, unknown>): string {
  return JSON.stringify(Object.fromEntries(
    Object.entries(detail).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))));
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/** 1 to `limit` printable characters, as the Python's `_text`. */
function text(value: unknown, limit: number, what: string): string {
  if (typeof value !== "string" || !value || codePoints(value) > limit || !isPrintable(value)) {
    throw new Invalid(`${what} is 1 to ${limit} printable characters`);
  }
  return value;
}

function count(value: unknown, low: number, high: number): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= low
    && value <= high;
}

/** An enabled agent that rings: the only kind a wake is sent to. */
function rings(agent: Agent): boolean {
  return agent.disabled_reason === null && WAKING.includes(agent.delivery_method);
}

/** `connected_agents.display_name`: untrusted metadata, never a place to keep a secret. */
export function displayName(value: unknown): string {
  if (typeof value !== "string" || codePoints(value) > 80 || SECRETISH.test(value)) {
    return "";
  }
  return Array.from(value.replace(/[^\p{L}\p{N}_ .-]/gu, "").trim()).slice(0, 80).join("");
}

/** The outside identity an API key resolves to (`api_keys._client`, `_family`). */
export function apiKeyOrigin(keyId: string): Origin {
  return {
    client: digest("tinyassets-api-key-client\0" + keyId),
    family: digest("tinyassets-api-key-family\0" + keyId),
  };
}

/** A binder's origin: null (the owner) or exactly a 64-hex client and family. */
export function outsideOrigin(value: unknown): Origin | null {
  if (value === null) {
    return null;
  }
  if (typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join() !== "client,family") {
    throw new Invalid("invalid outside origin");
  }
  const { client, family } = value as Record<string, unknown>;
  if (typeof client !== "string" || typeof family !== "string" || !HEX64.test(client)
      || !HEX64.test(family)) {
    throw new Invalid("invalid outside origin");
  }
  return { client, family };
}

/**
 * The registered identity, as the binder verified it (`_registrant`). An MCP agent's is
 * its verified origin. An API-key agent's is the key's own origin: the binder, which
 * alone holds the key store, checked the key is the owner's live key, and the object
 * re-derives the origin from the key id so the two can never disagree.
 */
export function registrant(source: unknown, origin: Origin | null): Source {
  if (typeof source !== "object" || source === null || Array.isArray(source)
      || Object.keys(source).sort().join() !== "kind,ref") {
    throw new Invalid("source is api_key or mcp");
  }
  const { kind, ref } = source as Record<string, unknown>;
  if (kind !== "api_key" && kind !== "mcp") {
    throw new Invalid("source is api_key or mcp");
  }
  if (origin === null) {
    throw new Refused("an agent registers only from its verified outside identity");
  }
  if (kind === "api_key") {
    if (typeof ref !== "string" || !KEY_ID.test(ref)) {
      throw new Invalid("an api_key source_ref is the key id");
    }
    const derived = apiKeyOrigin(ref);
    if (derived.client !== origin.client || derived.family !== origin.family) {
      throw new Refused("the origin is not this API key's");
    }
  }
  return { kind, ref: text(ref, 512, "source_ref") };
}

/** A doorbell's answer as the sender gave it, or refused. */
export function outcome(value: unknown): Outcome {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Invalid("invalid wake outcome");
  }
  const o = value as Record<string, unknown>;
  const keys = Object.keys(o);
  const states = ["sent", "rate_limited", "failed", "unknown", "suppressed"];
  if (!states.includes(o["state"] as string)
      || !keys.every((k) => ["state", "status", "retry_after", "reason"].includes(k))
      || ("status" in o && !count(o["status"], 100, 599))
      || ("retry_after" in o && o["retry_after"] !== null && typeof o["retry_after"] !== "number")
      || ("reason" in o && (typeof o["reason"] !== "string" || !REASON.test(o["reason"])))) {
    throw new Invalid("invalid wake outcome");
  }
  return o as unknown as Outcome;
}

/** A 429's wait in seconds, clamped to 1 s to one day; unreadable waits a minute. */
export function retryAfter(value: number | null | undefined): number {
  const seconds = typeof value === "number" && Number.isFinite(value)
    ? value : RETRY_AFTER.unreadable;
  return Math.min(Math.max(seconds, RETRY_AFTER.low), RETRY_AFTER.high);
}

/** Whether an outside client's grant still stands: the object's grants (`grants.ts`). */
export interface GrantReader {
  live(client: string): boolean;
}

export class Ledger {
  readonly #storage: OwnerStorage;
  readonly #grants: GrantReader;
  readonly #limits: LedgerLimits;

  constructor(storage: OwnerStorage, grants: GrantReader,
              limits: LedgerLimits = LEDGER_LIMITS) {
    this.#storage = storage;
    this.#grants = grants;
    this.#limits = checkLimits(limits);
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** The owner-wide counters; only migration 2's triggers write them. */
  #usage(): Usage {
    return this.#sql.exec<Usage>(
      "SELECT agents, enabled_agents, messages, body_bytes, settled_wakes, tombstones " +
        "FROM ledger_usage",
    ).one();
  }

  /** A registered agent, enabled or not; never a deleted agent's tombstone. */
  #agent(agentId: string): Agent | undefined {
    return this.#sql.exec<Agent>(
      "SELECT * FROM outside_agents WHERE agent_id = ? AND deleted_at IS NULL", agentId,
    ).toArray()[0];
  }

  // -- the agent registry ---------------------------------------------------

  /** Every agent for the owner; an outside caller sees only its own identity's. */
  agents(origin: Origin | null): Agent[] {
    if (origin === null) {
      return this.#sql.exec<Agent>(
        "SELECT * FROM outside_agents WHERE deleted_at IS NULL ORDER BY created_at, agent_id",
      ).toArray();
    }
    return this.#sql.exec<Agent>(
      "SELECT * FROM outside_agents WHERE outside_client = ? AND outside_family = ? " +
        "AND deleted_at IS NULL ORDER BY created_at, agent_id", origin.client, origin.family,
    ).toArray();
  }

  register(origin: Origin | null, source: unknown, request: Registration,
           now: number = clock()): Agent {
    const verified = registrant(source, origin);
    const client = origin!.client;
    const family = origin!.family;
    const commandCenter = text(request.command_center_id, 512, "command_center_id");
    const recipe = request.recipe_id === null ? null : text(request.recipe_id, 128, "recipe_id");
    const name = displayName(request.display_name);
    if (!name) {
      throw new Invalid("display_name is required: letters, digits, spaces, dots, dashes");
    }
    const method = request.delivery_method;
    if (typeof method !== "string" || !DELIVERY_METHODS.includes(method)) {
      throw new Invalid("unknown delivery method");
    }
    if (!ADMITTED_DELIVERY.includes(method)) {
      throw new Refused("only pull and mcp_event delivery are available yet");
    }
    if (verified.kind === "api_key" && WAKING.includes(method)) {
      throw new Refused("an API-key agent reads by pull; mcp_event is for MCP clients");
    }
    const sql = this.#sql;
    const limits = this.#limits;
    return this.#storage.transactionSync(() => {
      const row = sql.exec<Agent>(
        "SELECT * FROM outside_agents WHERE command_center_id = ? " +
          "AND outside_client = ? AND outside_family = ?", commandCenter, client, family,
      ).toArray()[0];
      // One outside identity is one enabled agent; a second would lock both out.
      if (sql.exec(
        "SELECT 1 FROM outside_agents WHERE outside_client = ? AND outside_family = ? " +
          "AND disabled_reason IS NULL AND command_center_id != ?", client, family, commandCenter,
      ).toArray().length) {
        throw new Refused("this outside identity is already an enabled agent");
      }
      const usage = this.#usage();
      if ((row === undefined || row.disabled_reason !== null)
          && usage.enabled_agents >= limits.maxAgents) {
        throw new Refused("the owner's enabled agents are at their limit");
      }
      // A registration never sets wake limits: they are owner policy (`policyWakeLimits`).
      const fields = [verified.kind, verified.ref, name, method, recipe];
      let agentId: string;
      if (row === undefined) {
        if (usage.agents >= limits.maxAgentRows) {
          // A deleted agent's tombstone holds its row until its callback revoke settles.
          throw new Refused(`the owner's agent registry is full (${usage.tombstones} of its ` +
                            "rows are deleted agents waiting on a pending callback revoke)");
        }
        agentId = "agent_" + token(8);
        sql.exec(
          "INSERT INTO outside_agents (agent_id, command_center_id, outside_client, " +
            "outside_family, source, source_ref, display_name, delivery_method, recipe_id, " +
            "revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
          agentId, commandCenter, client, family, ...fields, now,
        );
      } else {
        // The same outside identity again: re-enabled, one revision on. A deleted agent's
        // tombstone comes back as itself, its mail gone but its subscription generation
        // kept, so its pending revoke still fences only the callbacks it was made for.
        agentId = row.agent_id;
        if (!WAKING.includes(method)) {
          this.#revokeSubscribed(row, now);
        }
        sql.exec(
          "UPDATE outside_agents SET source = ?, source_ref = ?, display_name = ?, " +
            "delivery_method = ?, recipe_id = ?, disabled_reason = NULL, deleted_at = NULL, " +
            "revision = revision + 1 WHERE agent_id = ?",
          ...fields, agentId,
        );
      }
      if (!WAKING.includes(method)) {
        this.#suppress(agentId, "delivery_method", now);
      }
      return this.#agent(agentId)!;
    });
  }

  /**
   * A disabled agent can neither read nor ack, so its unacked mail is released: it no
   * longer holds the ledger and is evicted in turn (a re-enabled agent may still read
   * what is left). Its open wakes are suppressed and its callbacks revoked. The owner
   * disables any agent; an outside caller (`origin`) only its own.
   */
  disable(agentId: unknown, reason: unknown, origin: Origin | null = null,
          now: number = clock()): Agent {
    text(reason, 200, "reason");
    if (typeof agentId !== "string") {
      throw new Invalid("agent_id is an agent id");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const agent = this.#agent(agentId);
      if (agent === undefined) {
        throw new Refused("agent not found");
      }
      if (origin !== null && (origin.client !== agent.outside_client
                              || origin.family !== agent.outside_family)) {
        throw new Refused("an outside caller disables only its own agent");
      }
      this.#revokeSubscribed(agent, now);
      sql.exec("UPDATE outside_agents SET disabled_reason = ?, revision = revision + 1 " +
                 "WHERE agent_id = ?", reason, agentId);
      sql.exec("UPDATE ledger_recipients SET unacked = 0 WHERE agent_id = ? AND unacked = 1",
               agentId);
      this.#suppress(agentId, "disabled", now);
      return this.#agent(agentId)!;
    });
  }

  /**
   * The owner deletes an agent: every message in its dm room with their bodies and
   * recipients, its cursor, wakes, rings and receipts, the owner's own read and reply
   * receipts for those messages, and its registry row. While a revoke of its callbacks
   * is still to run, the row stays as a tombstone until that revoke settles. Each step is
   * an index range of that agent's rows.
   */
  remove(agentId: unknown, now: number = clock()): { agent_id: string; messages: number } {
    if (typeof agentId !== "string") {
      throw new Invalid("agent_id is an agent id");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const agent = this.#agent(agentId);
      if (agent === undefined) {
        throw new Refused("agent not found");
      }
      this.#revokeSubscribed(agent, now);
      const ids = sql.exec<{ message_id: string }>(
        "SELECT message_id FROM ledger_recipients WHERE agent_id = ? " +
          "UNION SELECT message_id FROM ledger_messages WHERE sender_id = ?", agentId, agentId,
      ).toArray().map((r) => r.message_id);
      const list = JSON.stringify(ids);
      const inList = "IN (SELECT value FROM json_each(?))";
      sql.exec("DELETE FROM ledger_receipts WHERE agent_id = ?", agentId);
      sql.exec(`DELETE FROM ledger_receipts WHERE kind IN ('read', 'reply') AND ref ${inList}`,
               list);
      // Recipients before their messages: their triggers read the message's size.
      sql.exec(`DELETE FROM ledger_recipients WHERE message_id ${inList}`, list);
      sql.exec(`DELETE FROM message_bodies WHERE message_id ${inList}`, list);
      sql.exec(`DELETE FROM ledger_messages WHERE message_id ${inList}`, list);
      sql.exec("DELETE FROM ledger_readers WHERE reader = ?", agentId);
      sql.exec("DELETE FROM ledger_wakes WHERE agent_id = ? AND kind = 'wake'", agentId);
      sql.exec("DELETE FROM ledger_rings WHERE agent_id = ?", agentId);
      if (this.#revokePending(agentId)) {
        sql.exec("UPDATE outside_agents SET disabled_reason = 'deleted', deleted_at = ?, " +
                   "revision = revision + 1 WHERE agent_id = ?", now, agentId);
      } else {
        sql.exec("DELETE FROM outside_agents WHERE agent_id = ?", agentId);
      }
      return { agent_id: agentId, messages: ids.length };
    });
  }

  /** Owner policy (W3): an agent's hourly wake cap and batch window. */
  setWakeLimits(agentId: unknown, maxWakesPerHour: unknown, batchWindowS: unknown): Agent {
    if (typeof agentId !== "string") {
      throw new Invalid("agent_id is an agent id");
    }
    const { low: hourLow, high: hourHigh } = WAKE_SETTINGS.maxWakesPerHour;
    if (!count(maxWakesPerHour, hourLow, hourHigh)) {
      throw new Invalid(`max_wakes_per_hour is ${hourLow} to ${hourHigh}`);
    }
    const { low: batchLow, high: batchHigh } = WAKE_SETTINGS.batchWindowS;
    if (!count(batchWindowS, batchLow, batchHigh)) {
      throw new Invalid(`batch_window_s is ${batchLow} to ${batchHigh}`);
    }
    return this.#storage.transactionSync(() => {
      if (!this.#sql.exec(
        "UPDATE outside_agents SET max_wakes_per_hour = ?, batch_window_s = ?, " +
          "revision = revision + 1 WHERE agent_id = ? AND deleted_at IS NULL RETURNING agent_id",
        maxWakesPerHour, batchWindowS, agentId,
      ).toArray().length) {
        throw new Refused("agent not found");
      }
      return this.#agent(agentId)!;
    });
  }

  /** The reader an origin is: the owner, or its one enabled agent. */
  #reader(origin: Origin | null): string {
    if (origin === null) {
      return OWNER;
    }
    const rows = this.#sql.exec<{ agent_id: string }>(
      "SELECT agent_id FROM outside_agents WHERE outside_client = ? AND outside_family = ? " +
        "AND disabled_reason IS NULL", origin.client, origin.family,
    ).toArray();
    if (rows.length !== 1) {
      throw new Refused("this outside identity is not one enabled agent");
    }
    return rows[0]!.agent_id;
  }

  #receipt(agentId: string, kind: string, ref: string, at: number,
           detail: Record<string, unknown> = {}): void {
    const sql = this.#sql;
    sql.exec("INSERT INTO ledger_receipts (agent_id, kind, ref, at, detail_json) " +
               "VALUES (?, ?, ?, ?, ?)", agentId, kind, ref, at, canonical(detail));
    // Newest first: the receipt table holds at most maxReceipts rows.
    sql.exec("DELETE FROM ledger_receipts WHERE seq <= " +
               "(SELECT MAX(seq) FROM ledger_receipts) - ?", this.#limits.maxReceipts);
  }

  // -- ROOM_POST (the 1:1 subset) -------------------------------------------

  post(origin: Origin | null, roomId: unknown, clientMessageId: unknown, body: unknown,
       parentId: unknown, now: number = clock()): Posted {
    const room = text(roomId, 128, "room_id");
    const cmid = text(clientMessageId, 128, "client_message_id");
    const parentKey = parentId === null ? null : text(parentId, 128, "parent_id");
    if (typeof body !== "string") {
      throw new Invalid("body is text");
    }
    if (!body || codePoints(body) > MAX_BODY) {
      throw new Invalid(`a message body is 1 to ${MAX_BODY} characters`);
    }
    if (LONE_SURROGATE.test(body)) {
      throw new Invalid("a message body is well-formed text");
    }
    const sha = digest(body);
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const agent = room.startsWith("dm:") ? sql.exec<Agent>(
        "SELECT * FROM outside_agents WHERE agent_id = ? AND disabled_reason IS NULL",
        room.slice(3),
      ).toArray()[0] : undefined;
      if (agent === undefined) {
        throw new Refused("room is not an enabled agent's dm");
      }
      let kind: string;
      let sender: string;
      let recipient: string;
      if (origin === null) {
        [kind, sender, recipient] = [OWNER, OWNER, agent.agent_id];
      } else if (origin.client === agent.outside_client
                 && origin.family === agent.outside_family) {
        [kind, sender, recipient] = ["outside", agent.agent_id, OWNER];
      } else {
        throw new Refused("an outside agent posts only in its own dm room");
      }
      const same = sql.exec<StoredMessage>(
        "SELECT * FROM ledger_messages WHERE sender_id = ? AND client_message_id = ?",
        sender, cmid,
      ).toArray()[0];
      if (same !== undefined) {
        if (same.body_sha256 !== sha || same.room_id !== room || same.parent_id !== parentKey) {
          throw new Refused("conflict");
        }
        return {
          message_id: same.message_id,
          recipients: sql.exec<{ agent_id: string }>(
            "SELECT agent_id FROM ledger_recipients WHERE message_id = ? ORDER BY agent_id",
            same.message_id,
          ).toArray().map((r) => r.agent_id),
          held: false,
          paused_reason: "",
        };
      }
      let parent: StoredMessage | undefined;
      if (parentKey !== null) {
        parent = sql.exec<StoredMessage>(
          "SELECT m.* FROM ledger_messages m JOIN ledger_recipients r " +
            "ON r.message_id = m.message_id AND r.agent_id = ? " +
            "WHERE m.message_id = ? AND m.room_id = ?", sender, parentKey, room,
        ).toArray()[0];
        if (parent === undefined) {
          throw new Refused("parent is not a message to this sender in this room");
        }
      }
      const size = utf8Bytes(body);
      this.#makeRoom(recipient, kind === "outside" ? agent : null, size);
      const messageId = "msg_" + token(16);
      const depth = kind === OWNER ? 0 : (parent?.chain_depth ?? 0) + 1;
      const seq = sql.exec<{ seq: number }>(
        "INSERT INTO ledger_messages (message_id, command_center_id, room_id, sender_kind, " +
          "sender_id, client_message_id, body_sha256, parent_id, chain_root, chain_depth, " +
          "created_at, body_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING seq",
        messageId, agent.command_center_id, room, kind, sender, cmid, sha, parentKey,
        parent?.chain_root ?? messageId, depth, now, size,
      ).one().seq;
      sql.exec("INSERT INTO message_bodies VALUES (?, ?, ?, ?)", messageId, room, body, now);
      sql.exec("INSERT INTO ledger_recipients (message_id, agent_id, seq, state, changed_at) " +
                 "VALUES (?, ?, ?, 'queued', ?)", messageId, recipient, seq, now);
      // One open wake per ringing recipient; never one for the message's own sender. A
      // new wake waits out the agent's batch window, so a burst of mail rings once (W3).
      if (WAKING.includes(agent.delivery_method) && recipient === agent.agent_id
          && recipient !== sender) {
        sql.exec(
          "INSERT INTO ledger_wakes (wake_id, agent_id, state, due, created_at) " +
            "SELECT ?, ?, 'pending', ?, ? WHERE NOT EXISTS " +
            `(SELECT 1 FROM ledger_wakes WHERE agent_id = ? AND kind = 'wake' AND ${OPEN})`,
          "wake_" + token(16), recipient, now + agent.batch_window_s, now, recipient,
        );
      }
      if (parent !== undefined) {
        sql.exec("UPDATE ledger_recipients SET state = 'replied', changed_at = ? " +
                   "WHERE message_id = ? AND agent_id = ?", now, parentKey, sender);
        const read = sql.exec<{ at: number | null }>(
          "SELECT MIN(at) AS at FROM ledger_receipts WHERE agent_id = ? AND kind = 'read' " +
            "AND ref = ?", sender, parentKey,
        ).one().at;
        this.#receipt(sender, "reply", messageId, now, {
          parent_id: parentKey,
          reply_s: round3(now - parent.created_at),
          read_s: read === null ? null : round3(read - parent.created_at),
        });
      }
      return { message_id: messageId, recipients: [recipient], held: false, paused_reason: "" };
    });
  }

  /** A reader's mailbox counters: its unacked messages and their bytes. */
  #mailbox(reader: string): { unacked: number; unacked_bytes: number } {
    return this.#sql.exec<{ unacked: number; unacked_bytes: number }>(
      "SELECT unacked, unacked_bytes FROM ledger_readers WHERE reader = ?", reader,
    ).toArray()[0] ?? { unacked: 0, unacked_bytes: 0 };
  }

  /**
   * Refuse, or make room for, one more message of `size` body bytes to `recipient`.
   *
   * An outside sender is held to its share of the owner's mailbox, and every recipient
   * to its own mailbox (see {@link LedgerLimits}). Past the owner-wide ceilings the
   * oldest message no recipient holds is evicted with its body and recipients. The
   * mailboxes fit inside the ceilings, so one is always there; none is a broken
   * invariant, which fails loudly.
   */
  #makeRoom(recipient: string, outsideSender: Agent | null, size: number): void {
    const sql = this.#sql;
    const limits = this.#limits;
    if (outsideSender !== null
        && (outsideSender.unacked_to_owner + 1 > limits.maxSenderUnacked
            || outsideSender.unacked_to_owner_bytes + size > limits.maxSenderUnackedBytes)) {
      throw new Refused("this agent's unacked mail to the owner is full");
    }
    const box = this.#mailbox(recipient);
    const [most, mostBytes, full] = recipient === OWNER
      ? [limits.maxOwnerUnacked, limits.maxOwnerUnackedBytes, "the owner's mailbox is full"]
      : [limits.maxAgentUnacked, limits.maxAgentUnackedBytes, "this agent's mailbox is full"];
    if (box.unacked + 1 > most || box.unacked_bytes + size > mostBytes) {
      throw new Refused(full);
    }
    for (;;) {
      const held = this.#usage();
      if (held.messages + 1 <= limits.maxMessages
          && held.body_bytes + size <= limits.maxBodyBytes) {
        return;
      }
      const done = sql.exec<{ message_id: string }>(
        "SELECT message_id FROM ledger_messages WHERE unacked = 0 ORDER BY seq LIMIT 1",
      ).toArray()[0];
      if (done === undefined) {
        throw new Error("the ledger's mailboxes no longer fit its ceilings: nothing to evict");
      }
      for (const table of ["ledger_recipients", "message_bodies", "ledger_messages"]) {
        sql.exec(`DELETE FROM ${table} WHERE message_id = ?`, done.message_id);
      }
    }
  }

  // -- MAILBOX_RECEIPT ------------------------------------------------------

  /**
   * Messages to this reader past its cursor; a reread returns the same message_ids.
   *
   * `maxBytes` bounds the page: whole messages (each measured as returned, plus
   * `padBytes` for the caller's own wrapping) while they fit, and at least one; a body is
   * never cut. Only a returned message is marked read and receipted, so a held-back one
   * stays unread until a read returns it.
   */
  read(origin: Origin | null, limit: unknown, maxBytes: unknown = null, padBytes: unknown = 0,
       now: number = clock()): MailPage {
    const maxRead = this.#limits.maxRead;
    if (!count(limit, 1, maxRead)) {
      throw new Invalid(`limit is 1 to ${maxRead}`);
    }
    if (maxBytes !== null && !count(maxBytes, 1, Number.MAX_SAFE_INTEGER)) {
      throw new Invalid("max_bytes is a positive integer");
    }
    if (!count(padBytes, 0, Number.MAX_SAFE_INTEGER)) {
      throw new Invalid("pad_bytes is a non-negative integer");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const reader = this.#reader(origin);
      const cursor = this.#cursor(reader);
      const rows = sql.exec<StoredMessage & { state: string }>(
        "SELECT m.*, r.state FROM ledger_recipients r JOIN ledger_messages m " +
          "ON m.message_id = r.message_id WHERE r.agent_id = ? AND r.seq > ? " +
          "ORDER BY r.seq LIMIT ?", reader, cursor, limit,
      ).toArray();
      const ids = JSON.stringify(rows.map((r) => r.message_id));
      const bodies = new Map(sql.exec<{ message_id: string; body: string }>(
        "SELECT message_id, body FROM message_bodies WHERE message_id IN " +
          "(SELECT value FROM json_each(?))", ids,
      ).toArray().map((b) => [b.message_id, b.body]));
      // A reply's own receipt already holds its time to reply; show it, never recompute.
      const replied = new Map(sql.exec<{ ref: string; detail_json: string }>(
        "SELECT ref, detail_json FROM ledger_receipts WHERE kind = 'reply' AND ref IN " +
          "(SELECT value FROM json_each(?))", ids,
      ).toArray().map((r) => [r.ref, JSON.parse(r.detail_json).reply_s as number]));
      const messages: Mail[] = [];
      let used = 0;
      for (const row of rows) {
        const stored = bodies.get(row.message_id);
        const [body, integrity]: [string | null, Integrity] = stored === undefined
          ? [null, "missing"]
          : digest(stored) !== row.body_sha256 ? [null, "tampered"] : [stored, "ok"];
        const message: Mail = {
          seq: row.seq, message_id: row.message_id, room_id: row.room_id,
          sender_kind: row.sender_kind, sender_id: row.sender_id, parent_id: row.parent_id,
          chain_depth: row.chain_depth, created_at: row.created_at,
          reply_s: replied.get(row.message_id) ?? null, body, integrity,
        };
        used += (padBytes as number) + utf8Bytes(JSON.stringify(message));
        if (messages.length && maxBytes !== null && used > (maxBytes as number)) {
          break;
        }
        messages.push(message);
        if (["queued", "held_cap", "woken"].includes(row.state)) {
          sql.exec("UPDATE ledger_recipients SET state = 'read', changed_at = ? " +
                     "WHERE message_id = ? AND agent_id = ?", now, row.message_id, reader);
          this.#receipt(reader, "read", row.message_id, now);
        }
      }
      return { reader, cursor, messages, more: messages.length < rows.length };
    });
  }

  #cursor(reader: string): number {
    return this.#sql.exec<{ through_seq: number }>(
      "SELECT through_seq FROM ledger_readers WHERE reader = ?", reader,
    ).toArray()[0]?.through_seq ?? 0;
  }

  /** The newest seq of this reader's own mail: never another reader's count. */
  #newest(reader: string): number {
    return this.#sql.exec<{ top: number }>(
      "SELECT COALESCE(MAX(seq), 0) AS top FROM ledger_recipients WHERE agent_id = ?", reader,
    ).one().top;
  }

  ack(origin: Origin | null, through: unknown, now: number = clock()):
      { reader: string; through: number } {
    if (!count(through, 0, Number.MAX_SAFE_INTEGER)) {
      throw new Invalid("through is a ledger seq");
    }
    return this.#storage.transactionSync(() => {
      const reader = this.#reader(origin);
      const before = this.#cursor(reader);
      // At or behind the cursor is a retry: a no-op success, even after the acked mail
      // was evicted. Only an ack that moves the cursor leaves a receipt, so repeating
      // one cannot flood the receipts.
      if (through <= before) {
        return { reader, through: before };
      }
      if (through > this.#newest(reader)) {
        throw new Refused("ack is past this reader's mail");
      }
      const sql = this.#sql;
      sql.exec("INSERT INTO ledger_readers (reader, through_seq, acked_at) VALUES (?, ?, ?) " +
                 "ON CONFLICT (reader) DO UPDATE SET through_seq = excluded.through_seq, " +
                 "acked_at = excluded.acked_at", reader, through, now);
      this.#receipt(reader, "ack", String(through), now);
      // Each newly acked recipient row stops holding its message (an index range of this
      // reader's unacked mail); its triggers free the mailbox and the sender's share.
      sql.exec("UPDATE ledger_recipients SET unacked = 0 WHERE agent_id = ? AND unacked = 1 " +
                 "AND seq <= ?", reader, through);
      return { reader, through };
    });
  }

  /**
   * W4: a `mailbox.message` subscribe names the caller's own ringing agent. It opens
   * that agent's next subscription generation, which the doorbell tags the callback
   * with, so a revoke made before it can never erase it.
   */
  subscriber(origin: Origin | null, commandCenterId: unknown, agentId: unknown): number {
    const rows = origin === null || typeof commandCenterId !== "string"
        || typeof agentId !== "string" ? [] : this.#sql.exec<{ subscription: number }>(
      "UPDATE outside_agents SET subscription = subscription + 1 WHERE agent_id = ? AND " +
        "command_center_id = ? AND outside_client = ? AND outside_family = ? AND " +
        "disabled_reason IS NULL AND delivery_method = 'mcp_event' RETURNING subscription",
      agentId, commandCenterId, origin.client, origin.family,
    ).toArray();
    if (!rows.length) {
      throw new Refused("not this caller's mcp_event agent");
    }
    return rows[0]!.subscription;
  }

  /**
   * A changed or revoked grant (P0-3's M3): every agent of that outside client has its
   * open wakes suppressed and its callbacks revoked, so it subscribes again under the
   * new grant. Its ids; an index range of the client's agents.
   */
  suppressClient(client: string, now: number): string[] {
    return this.#storage.transactionSync(() => {
      const agents = this.#sql.exec<Agent>(
        "SELECT * FROM outside_agents WHERE outside_client = ? AND deleted_at IS NULL", client,
      ).toArray();
      for (const agent of agents) {
        this.#suppress(agent.agent_id, "grant", now);
        this.#revokeSubscribed(agent, now);
      }
      return agents.map((agent) => agent.agent_id);
    });
  }

  // -- the schedule: wakes and revokes (S3b, W3) ------------------------------

  #suppress(agentId: string, reason: string, now: number): void {
    this.#sql.exec(
      "UPDATE ledger_wakes SET state = 'suppressed', receipt_json = ?, settled_at = ? " +
        `WHERE agent_id = ? AND kind = 'wake' AND ${OPEN}`, canonical({ reason }), now, agentId,
    );
    this.#prune(now);
  }

  /**
   * Schedule a revoke of the agent's callbacks through its newest subscription, when it
   * has subscribed since its last revoke. At most one is open per agent: a new one raises
   * the open one's generation instead.
   */
  #revokeSubscribed(agent: Agent, now: number): void {
    if (agent.subscription <= agent.revoked_through) {
      return;
    }
    const sql = this.#sql;
    const generation = agent.subscription;
    sql.exec("UPDATE outside_agents SET revoked_through = ? WHERE agent_id = ?", generation,
             agent.agent_id);
    if (!sql.exec(
      "UPDATE ledger_wakes SET generation = MAX(generation, ?) WHERE agent_id = ? " +
        `AND kind = 'revoke' AND ${OPEN} RETURNING wake_id`, generation, agent.agent_id,
    ).toArray().length) {
      sql.exec(
        "INSERT INTO ledger_wakes (wake_id, agent_id, kind, state, due, generation, " +
          "created_at) VALUES (?, ?, 'revoke', 'pending', ?, ?, ?)",
        "revoke_" + token(16), agent.agent_id, now, generation, now,
      );
    }
  }

  /** Whether a revoke of the agent's callbacks is still open or being sent. */
  #revokePending(agentId: string): boolean {
    return this.#sql.exec(
      "SELECT 1 FROM ledger_wakes WHERE agent_id = ? AND kind = 'revoke' " +
        "AND state IN ('pending', 'rate_limited', 'sending')", agentId,
    ).toArray().length > 0;
  }

  /** Settled schedule rows kept for the retention, and at most `maxSettledWakes`. */
  #prune(now: number): void {
    const sql = this.#sql;
    sql.exec("DELETE FROM ledger_wakes WHERE settled_at IS NOT NULL AND settled_at < ?",
             now - this.#limits.settledWakeRetentionS);
    const over = this.#usage().settled_wakes - this.#limits.maxSettledWakes;
    if (over > 0) {
      sql.exec(
        "DELETE FROM ledger_wakes WHERE wake_id IN (SELECT wake_id FROM ledger_wakes " +
          "WHERE settled_at IS NOT NULL ORDER BY settled_at, rowid LIMIT ?)", over,
      );
    }
  }

  /**
   * Open schedule rows that are due, oldest first: what the `wakes` lane's claim pass takes.
   * The object is its owner, so there is no cross-owner fairness pass.
   */
  dueOpen(now: number, limit: number): Wake[] {
    return this.#sql.exec<Wake>(
      "SELECT wake_id, agent_id, kind, state, due, claim_id FROM ledger_wakes " +
        `WHERE ${OPEN} AND due <= ? ORDER BY due LIMIT ?`, now, limit,
    ).toArray();
  }

  /** Claims whose lease has lapsed, oldest first: what the lane's recover pass takes. */
  lapsedClaims(now: number, limit: number): Wake[] {
    return this.#sql.exec<Wake>(
      "SELECT wake_id, agent_id, kind, state, due, claim_id FROM ledger_wakes " +
        "WHERE state = 'sending' AND due <= ? ORDER BY due LIMIT ?", now, limit,
    ).toArray();
  }

  /**
   * Before any byte: the claimed agent's IDs and mailbox cursor, or a revoke's agent.
   *
   * A wake whose agent is gone, disabled or no longer rings is suppressed instead, and
   * a revoke of its callbacks scheduled. A wake whose agent has rung `max_wakes_per_hour`
   * times in the last hour is pushed to when the oldest of those rings leaves the hour,
   * its queued mail held at `held_cap` (W3). The claim is unmarked: the in-process sender
   * calls `mark()` before its first byte.
   */
  claim(wakeId: unknown, now: number): Claim {
    if (typeof wakeId !== "string") {
      throw new Invalid("wake_id is a wake id");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync((): Claim => {
      const wake = sql.exec<StoredWake>(
        `SELECT * FROM ledger_wakes WHERE wake_id = ? AND ${OPEN} AND due <= ?`, wakeId, now,
      ).toArray()[0];
      if (wake === undefined) {
        return { outcome: "not_due" };
      }
      const claimId = "claim_" + token(16);
      if (wake.kind === "revoke") {
        sql.exec("UPDATE ledger_wakes SET state = 'sending', claim_id = ?, " +
                   "attempts = attempts + 1, due = ? WHERE wake_id = ?",
                 claimId, now + WAKE_LEASE_S, wakeId);
        return { outcome: "revoke", target: { wake_id: wakeId, claim_id: claimId,
                                              agent_id: wake.agent_id,
                                              generation: wake.generation! } };
      }
      const agent = this.#agent(wake.agent_id);
      const live = agent !== undefined && rings(agent);
      if (!live || !this.#grants.live(agent.outside_client)) {
        this.#suppress(wake.agent_id, live ? "grant" : "agent", now);
        if (agent !== undefined) {
          this.#revokeSubscribed(agent, now);
        }
        return { outcome: "suppressed", agent_id: wake.agent_id };
      }
      sql.exec("DELETE FROM ledger_rings WHERE agent_id = ? AND at <= ?", agent.agent_id,
               now - HOUR_S);
      const rung = sql.exec<{ at: number }>(
        "SELECT at FROM ledger_rings WHERE agent_id = ? ORDER BY at", agent.agent_id,
      ).toArray();
      if (rung.length >= agent.max_wakes_per_hour) {
        const due = rung[rung.length - agent.max_wakes_per_hour]!.at + HOUR_S;
        sql.exec("UPDATE ledger_wakes SET due = MAX(due, ?) WHERE wake_id = ?", due, wakeId);
        sql.exec("UPDATE ledger_recipients SET state = 'held_cap', changed_at = ? " +
                   "WHERE agent_id = ? AND state = 'queued'", now, agent.agent_id);
        return { outcome: "capped", due };
      }
      sql.exec("INSERT INTO ledger_rings VALUES (?, ?)", agent.agent_id, now);
      const cursor = this.#cursor(agent.agent_id);
      const through = this.#newest(agent.agent_id);
      sql.exec(
        "UPDATE ledger_wakes SET state = 'sending', claim_id = ?, attempts = attempts + 1, " +
          "due = ?, marked = 0, through_seq = ? WHERE wake_id = ?",
        claimId, now + WAKE_LEASE_S, through, wakeId,
      );
      return {
        outcome: "claimed",
        target: {
          wake_id: wakeId, claim_id: claimId, agent_id: agent.agent_id,
          command_center_id: agent.command_center_id, client: agent.outside_client,
          family: agent.outside_family, cursor, through,
        },
      };
    });
  }

  /**
   * A row whose pass failed in process, held back to `until` (never earlier than it was),
   * so a row that keeps failing waits in its own row instead of looping the alarm.
   */
  defer(wakeId: string, until: number): void {
    this.#sql.exec("UPDATE ledger_wakes SET due = MAX(due, ?) WHERE wake_id = ? AND state IN " +
                     "('pending', 'rate_limited', 'sending')", until, wakeId);
  }

  /** The durable may-have-sent mark, written before the doorbell's first byte. */
  mark(wakeId: string, claimId: string): boolean {
    return this.#sql.exec(
      "UPDATE ledger_wakes SET marked = 1 WHERE wake_id = ? AND claim_id = ? " +
        "AND state = 'sending' RETURNING wake_id", wakeId, claimId,
    ).toArray().length > 0;
  }

  /**
   * A claim abandoned past its lease: a wake is unknown if a byte may have left, else due
   * again; a revoke is due again, since erasing callbacks twice is harmless.
   */
  recover(wakeId: string, now: number): string | null {
    const wake = this.#sql.exec<StoredWake>(
      "SELECT * FROM ledger_wakes WHERE wake_id = ? AND state = 'sending' AND due <= ?",
      wakeId, now,
    ).toArray()[0];
    if (wake === undefined || wake.claim_id === null) {
      return null;
    }
    const state = wake.kind === "wake" && wake.marked ? "unknown" : "pending";
    this.settle(wake.wake_id, wake.claim_id, { state, reason: "recovered" }, now);
    return state;
  }

  /**
   * A claimed row's outcome and its receipt: a wake's messages `woken` once accepted, a
   * revoke `revoked` once its callbacks are erased.
   *
   * Only the claim that holds the row settles it, so a sender whose lease lapsed cannot
   * overwrite a later claim.
   */
  settle(wakeId: unknown, claimId: unknown, answer: Settle, now: number): boolean {
    if (typeof wakeId !== "string" || typeof claimId !== "string") {
      throw new Invalid("invalid wake settle");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const wake = sql.exec<StoredWake>(
        "SELECT * FROM ledger_wakes WHERE wake_id = ? AND claim_id = ? AND state = 'sending'",
        wakeId, claimId,
      ).toArray()[0];
      if (wake === undefined) {
        return false;
      }
      const revoke = wake.kind === "revoke";
      if (revoke && answer.state !== "sent" && answer.state !== "pending") {
        throw new Invalid("a revoke settles sent, or pending again");
      }
      let state: string = answer.state;
      const kind = revoke ? (state === "sent" ? "revoked" : undefined) : WAKE_RECEIPTS[state];
      let detail: Record<string, unknown> = revoke
        ? { ...answer, generation: wake.generation } : { ...answer, through: wake.through_seq };
      delete detail["state"];
      let due = now;
      if (state === "rate_limited") {
        due = now + retryAfter(answer.state === "rate_limited" ? answer.retry_after : null);
        detail = { ...detail, retry_after: due - now };
      }
      // A row of this kind opened for the agent meanwhile: fold into that one, which then
      // waits out any Retry-After.
      if ((state === "pending" || state === "rate_limited") && sql.exec(
        "UPDATE ledger_wakes SET due = MAX(due, ?), state = CASE WHEN ? = 'rate_limited' " +
          "THEN 'rate_limited' ELSE state END, generation = MAX(generation, ?) " +
          `WHERE agent_id = ? AND kind = ? AND ${OPEN} RETURNING wake_id`,
        due, state, wake.generation, wake.agent_id, wake.kind,
      ).toArray().length) {
        state = "suppressed";
        detail = { ...detail, folded: true };
      }
      const settled = SETTLED.includes(state);
      sql.exec(
        "UPDATE ledger_wakes SET state = ?, due = ?, receipt_json = ?, settled_at = ? " +
          "WHERE wake_id = ?", state, due, canonical(detail), settled ? now : null, wakeId,
      );
      if (state === "sent" && !revoke) {
        sql.exec(
          "UPDATE ledger_recipients SET state = 'woken', changed_at = ? WHERE agent_id = ? " +
            "AND seq <= ? AND state IN ('queued', 'held_cap')",
          now, wake.agent_id, wake.through_seq ?? 0,
        );
      }
      if (kind !== undefined) {
        this.#receipt(wake.agent_id, kind, wakeId, now, detail);
      }
      // A deleted agent's tombstone goes once no revoke of its callbacks is left to run.
      if (revoke && !this.#revokePending(wake.agent_id)) {
        sql.exec("DELETE FROM outside_agents WHERE agent_id = ? AND deleted_at IS NOT NULL",
                 wake.agent_id);
      }
      this.#prune(now);
      return true;
    });
  }

  /**
   * When the `wakes` lane next has work, never before `now`; null for none.
   *
   * A claim's lease always counts: its recovery needs no sender. Open rows count only
   * when `ringing` (the object has a doorbell to send them); without one they stay
   * pending and give the lane no job, so no alarm fires for work it cannot do. Work already due
   * arms at `now`, which every pass shrinks.
   */
  nextDue(now: number, ringing: boolean): number | null {
    const sql = this.#sql;
    const earliest = (where: string) => sql.exec<{ due: number | null }>(
      `SELECT MIN(due) AS due FROM ledger_wakes WHERE ${where}`).one().due;
    const times = [earliest("state = 'sending'"), ringing ? earliest(OPEN) : null]
      .filter((due): due is number => due !== null);
    return times.length ? Math.max(Math.min(...times), now) : null;
  }
}
