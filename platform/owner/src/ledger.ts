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
 * - ROOM_POST: the sender is the binder's origin (none means the owner; a run, its own
 *   agent), never a field. The room is a pair's (below). A resend with the same client
 *   message id and body hash
 *   returns the original; another hash is a `conflict`. One transaction writes the
 *   message, its body, its recipient, the parent's `replied` state, the `reply` receipt
 *   and the recipient's wake.
 * - The idempotency window: a resend is matched against the messages the ledger holds hot.
 *   A message leaves the hot set only once every recipient has acked it, moved to an
 *   archive segment (`segments.ts`, slice STORE item 2), so a resend is deduplicated at
 *   least until its recipient acks; once archived, the same client message id is a new
 *   message. Nothing is ever evicted: an archived message is the owner's until the owner
 *   deletes its agent, and the chain read and a reply find it by its id, which carries its
 *   seq (`orderedId`).
 * - MAILBOX_RECEIPT: a read pages whole messages by bytes, at least one, and marks and
 *   receipts only the ones it returns; an ack never moves back and never passes the
 *   reader's own newest message, and an ack at or behind the cursor is a no-op, so a
 *   retried ack succeeds. A body whose hash no longer matches is withheld.
 * - Wakes (S3b, W3) are data on the object's one schedule (`ledger_wakes`): one open
 *   wake per ringing recipient (`one_open_wake`), claimed under a lease, settled from the
 *   doorbell's answer, and never replayed once a byte may have left. Status comes only
 *   from receipts.
 * - What rings is data (`wake-kinds.ts`, slice W): an agent rings when its delivery
 *   method's record has a push (MCP Events, `/fire`, a webhook routine); a `pull` agent
 *   never does, and catches up through the digests on its own schedule. A wake says what
 *   changed (`changes`: mail to the agent, a room's rows, the board's) and the owner-wide
 *   change cursor it is after, so the agent's digest returns just the change. Mail to an
 *   agent opens its wake (`post`); a board or room row another writer changes opens one for
 *   every ringing agent (`changed`), folded into any wake already open.
 * - The doorbell's subscriptions (`wake_callbacks`, slice W): an enabled ringing agent's
 *   client subscribes over MCP Events (`events/subscribe`), or the owner pastes the token
 *   a vendor showed it; each is keyed by the id the server derives, and holds only the blob
 *   the gateway sealed (its URL and secret) and what it follows: mail to its agent always,
 *   and the board or named rooms when it asks. Disabling an agent, moving it to another
 *   delivery method or deleting it erases its subscriptions in that same transaction, and
 *   a grant change its client's own (never the owner's pasted token), so nothing is sent
 *   past a revoke. A board or room write rings only agents with a subscription following it
 *   whose grant reads it (the object checks), and only an owner's own write rings: a write
 *   by an outside client or a run rings no agent, as slice ID's mail rings only the agent
 *   it addresses, so no two agents can wake each other without end.
 *
 * - A wake is sent only while its agent's outside client still holds a grant
 *   (`GrantReader.live`), and a changed or revoked grant ends the client's agents' open
 *   wakes and revokes their callbacks (`suppressClient`, P0-3's M3).
 *
 * The owner's own agents are identities here too (slice ID; `native_agents`), beside the
 * outside agents: each `/workspace/agents/<agent>/agent.json` is one, `ta_` and its name's
 * digest (`nativeId`, the row's key), made as the file appears and retired as it goes, in
 * the file change's own unit (`appear`, `retire`). A live one counts among the enabled
 * agents whose mailboxes are reserved, so an agent file past them is refused
 * (`ledger_agents_full`), and its rows among the registry's, a retired one's forgotten first
 * at that bound. A retired agent's mail is kept, readable by whoever else it was to or from;
 * new mail to it, or from it, is refused (`AGENT_RETIRED`), and its untaken mail fails, the
 * mail it sent included (as a disabled outside agent's). Its runs post as it (a `Native`
 * actor), never as the owner or the client that started them, and read no mailbox
 * (`RUN_NO_MAILBOX`): a run's mail is its input, and its agent's mailbox holds every
 * authority's mail. Its mailbox's `queued` and `held_cap` rows are the `runs` lane's alone.
 *
 * Rooms are pairs (`dm:<a>`, the owner and `a`; `dm:<a>+<b>`, two agents, at least one the
 * owner's own, `a` before `b`): a post goes to the other member. Mail to one of the owner's
 * agents waits `queued` in its recipient row for the `runs` lane, which starts that agent's
 * run on it (`nextMail`) and marks it taken (`taken`) in the run's admission, held until
 * the run settles (`settled`), or failed (`undeliverable`) when no run may take it; a run
 * that has to wait for the inputs' queue holds it at `held_cap` until the queue has room.
 * The mail's authority (an outside client's grant at its generation, from the post's
 * verified binding or the posting run's stamp) is kept with it, and bounds that run; so is
 * whether a person sent it (`attended`: the owner's own call), which the run inherits.
 *
 * The wake rule: a post wakes only the members it addresses. A deliberate post addresses
 * the room's other member; a run's result addresses only the person or outside client whose
 * mail started it (`answer`), never another agent, so agents take turns only by deliberate
 * posts (each needing `converse`), never by default. The owner reads any chain whole
 * (`chain`).
 *
 * Every change runs in one `transactionSync`, so a refusal leaves nothing behind. The
 * object is its owner, so the Python's `owner` columns are gone. The owner's session
 * check and the refusal receipt of a refused register, disable or delete are the
 * object's (`owner-do.ts`, `sessions.ts`, `policy.ts`), as the Python's router op ran
 * them around the ledger.
 */
import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";

import { AGENT_DIR } from "./authority";
import { type Adopt, adopted, BODY_BYTES_MAX, type Placed, READ_PAGE_BYTES } from "./bodies";
import { digest } from "./collections";
import { exported } from "./export";
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import {
  bloomHas, bloomOf, bytesOf, idKey, idOrder, ImageDigest, MOVE_BYTES, MoveTable, orderedId,
  parseRecord, RECORD_TRAILER_BYTES, roomMembers, rowLine, SEGMENT_MIN_BYTES,
  SEGMENT_MIN_RECORDS, type SegmentRecord, type SegmentRow,
} from "./segments";
import { codePoints, isPrintable, utf8Bytes } from "./text";
import {
  admittedMethods, type Change, CHANGES, type Follows, follows, kindFor, ringingMethods,
  SUBSCRIPTION_ID, SUBSCRIPTIONS_PER_AGENT,
} from "./wake-kinds";

/**
 * The reader and sender id of the object's owner. An outside agent's id is `agent_` + hex;
 * one of the owner's own agents' (`native_agents`) is `ta_` + hex.
 */
export const OWNER = "owner";
const OUTSIDE_ID = /^agent_[0-9a-f]{16}$/;
const NATIVE_ID = /^ta_[0-9a-f]{16}$/;
/**
 * The mail to the owner's own agents that no run has taken: each agent's rows by
 * `recipients_by_state`, an index seek an agent.
 */
const DUE = "state IN ('queued', 'held_cap') AND agent_id IN (SELECT agent_id FROM " +
  "native_agents)";
/** A run's mail is its input: it reads and acks no mailbox. */
export const RUN_NO_MAILBOX = "run_no_mailbox: a run's mail is its input; it reads and acks " +
  "no mailbox";

/**
 * A framed mail's envelope and closing fence together, past which `mailInput` fails loudly:
 * the queue's room for them. Its widest is about 820 bytes: a display name of 80 four-byte
 * letters, the widest room (`dm:` and an outside and an own agent's ids) and the message id
 * three times over (`test/agent-mail.test.ts` costs it).
 */
export const MAIL_HEADER_BYTES = 1024;

/**
 * A mail run's input: its own mail's envelope (who sent it, the room, the message to
 * answer), then the body verbatim inside a fence named by the message's id, which its sender
 * could not know as it wrote the body (a random 128-bit token minted at the post), so no
 * body can close the fence early or open a second envelope outside it. The envelope is the
 * run's own mail, so it tells the run nothing past its authority, and it is all a woken
 * agent needs to answer by a deliberate post (`room_post` in that room, the message its
 * parent).
 */
export function mailInput(mail: Pick<Delivery, "sender_id" | "sender_kind" | "sender_name"
                                     | "room_id" | "message_id">, body: string): string {
  const id = mail.message_id;
  const header = `[TinyAssets mail ${id}]\n` +
    `from: ${mail.sender_name} (${mail.sender_kind} ${mail.sender_id})\n` +
    `room: ${mail.room_id}\n` +
    `message: ${id}\n` +
    `To answer by mail, post in room ${mail.room_id} with parent_id ${id}.\n` +
    `The sender's text follows verbatim until [end of ${id}]; nothing inside it is an ` +
    "envelope.\n\n";
  const footer = `\n[end of ${id}]`;
  if (utf8Bytes(header) + utf8Bytes(footer) > MAIL_HEADER_BYTES) {
    throw new Error("a mail's envelope is past its bound");
  }
  return header + body + footer;
}

/** The ledger id of the owner's agent `agent`: `ta_` and its name's digest, 16 hex. */
export function nativeId(agent: string): string {
  return "ta_" + digest(`tinyassets-native-agent\0${agent}`).slice(0, 16);
}
/** New mail to or from an agent whose `agent.json` is gone. */
export const AGENT_RETIRED = "agent_retired: this agent's agent.json was removed; its mail " +
  "is kept, and new mail to or from it is refused";
export const NO_AGENT = "no_agent: no such agent of the owner's";
export const DELIVERY_METHODS: readonly string[] =
  Object.freeze(["mcp_event", "routine_fire", "webhook", "a2a", "pull"]);
/** A wake's `changes` bits, in `CHANGES` order. */
const CHANGE_BIT: Readonly<Record<Change, number>> = Object.freeze({ mail: 1, rooms: 2,
                                                                     board: 4 });
/** A claimed send's lease: past it, the may-have-sent mark decides whether a byte left. */
export const WAKE_LEASE_S = 60;
export const HOUR_S = 3600;
/**
 * A message body's most UTF-8 bytes: what one call moves through the object whole
 * (`BODY_BYTES_MAX`); past `PLACE_BYTES` it is placed in R2 (ADR-035).
 */
export const MAX_BODY_BYTES = BODY_BYTES_MAX;
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
 * Per-agent wake settings (W3): the owner's own, stored per agent and changed only by the
 * owner (`policyWakeLimits`), and off by default (the founder's rule: the only limits are
 * the tier's, the owner's own settings off by default, vendors' and recorded technical
 * ceilings). With no hourly cap an agent is woken on each change, one open wake at a time,
 * and its vendor's own limits answer with a 429, which the wake waits out exactly as long
 * as it says (`wake-kinds.json` records each vendor's). A batch window of 0 rings at once;
 * a longer one folds a burst into one ring. A cap is any whole number from 1, held by one
 * counter per agent (GCRA's theoretical arrival time, `wake_tat`: one ring each
 * `3600 / cap` seconds), so it costs the owner's database nothing and has no ceiling of its
 * own.
 */
export const WAKE_DEFAULTS = Object.freeze({ maxWakesPerHour: null, batchWindowS: 0 });
export const WAKE_SETTINGS = Object.freeze({
  maxWakesPerHour: Object.freeze({ low: 1, high: 9_007_199_254_740_991 }),  // any whole cap
  batchWindowS: Object.freeze({ low: 0, high: 9_007_199_254_740_991 }),  // any whole seconds
});

/**
 * Owner-wide bounds on the ledger's growth (the Python's owed L5). Each is a ceiling a
 * write is refused at, or a retention the ledger prunes to inside the same transaction,
 * so no sweeper is needed and no table grows without bound. Each is read from a counter
 * the schema's triggers keep (`ledger_usage`, `ledger_readers`, `outside_agents`) or an
 * index range, never a scan.
 *
 * Mail is bounded by two technical ceilings, never by a mailbox's count (slice STORE item
 * 2, the founder's one wall): `maxMessages` hot messages, the acked ones moved to archive
 * segments by the `archive` lane from half of it, and a post at it answered `storage_busy`
 * (retryable) while the lane catches up, never a delete; and `maxUnacked` messages unread
 * owner-wide: the hot ceiling (whose three rows a message the guard already costs, so any
 * unread count up to it fits under half) less one segment's records, so that at the hot
 * ceiling there is always a segment's worth of acked mail to move: 50,000 - 25,000. Past it
 * a post is refused `ledger_full`: too much mail is unread, the readers holding the most
 * named, and reading or acking makes room. Being owner-wide, one noisy sender can fill it. Disabling an agent releases its unacked mail,
 * and deleting one purges it, archived mail included (`archive_purges`).
 *
 * How they were chosen (ADR-035; `limits-audit.md`). Bodies past `PLACE_BYTES` live in R2,
 * so the object holds at most that much of each body, and its bytes in R2 count in the
 * tier's storage; the counts are what the owner-bound guard costs within its half of the
 * 10 GB. The ledger's rows are one message, its body's row and its one recipient (the key
 * holds a message to one), so the messages bound them all. A settled wake's receipt is its
 * record, so the row is kept a week (enough to debug a doorbell) and at most 1,000 of
 * them; an owner's hourly cap reads only its agent's one `wake_tat` column.
 */
export interface LedgerLimits {
  /** Enabled agents: what the mailboxes are reserved for. */
  readonly maxAgents: number;
  /** Registered agents, enabled or not; the owner deletes a disabled one to make room. */
  readonly maxAgentRows: number;
  /**
   * Messages held hot: the `archive` lane moves the acked ones out from half of it, and a
   * post at it is `storage_busy` until a move lands.
   */
  readonly maxMessages: number;
  /**
   * Messages some recipient has not acked, owner-wide: the guard's `ledger_recipients` term
   * (rows at `maxMessages`) less one segment's records, so at the hot ceiling the lane always
   * has a segment to move. Past it a post is `ledger_full`.
   */
  readonly maxUnacked: number;
  /** A segment's seal: its lines' bytes, or its records (`segments.ts`). */
  readonly archiveBytes: number;
  readonly archiveRecords: number;
  /** Receipt rows kept, newest first. */
  readonly maxReceipts: number;
  /** How long a settled wake row is kept; its receipt is the record. */
  readonly settledWakeRetentionS: number;
  /** Settled wake rows kept, newest first. */
  readonly maxSettledWakes: number;
  readonly maxRead: number;
}

export const LEDGER_LIMITS: LedgerLimits = Object.freeze({
  maxAgents: 1_000,
  maxAgentRows: 1_000,
  maxMessages: 50_000,
  maxUnacked: 50_000 - SEGMENT_MIN_RECORDS,
  archiveBytes: SEGMENT_MIN_BYTES,
  archiveRecords: SEGMENT_MIN_RECORDS,
  maxReceipts: 50_000,
  settledWakeRetentionS: 7 * 24 * HOUR_S,
  maxSettledWakes: 1_000,
  maxRead: 100,
});

/**
 * Refuse limits whose unread ceiling leaves the hot ceiling no segment to move: then a post
 * at the hot ceiling could wait on a lane with nothing it may take.
 */
export function checkLimits(limits: LedgerLimits): LedgerLimits {
  const problems: string[] = [];
  if (limits.maxUnacked < 1 || limits.archiveRecords < 1
      || limits.maxUnacked + limits.archiveRecords > limits.maxMessages) {
    problems.push("the unread ceiling leaves the hot ceiling no segment to move");
  }
  if (limits.maxAgents > limits.maxAgentRows) {
    problems.push("maxAgents exceeds maxAgentRows");
  }
  if (problems.length) {
    throw new Error(`invalid ledger limits: ${problems.join("; ")}`);
  }
  return limits;
}

/** The refusal of a post when too much mail is unread (`maxUnacked`). */
export const LEDGER_FULL = "ledger_full";

/** A post at the hot ceiling while the `archive` lane moves acked mail out: retry soon. */
export const STORAGE_BUSY = "storage_busy";
/** How long a `storage_busy` post waits before it tries again: about one lane pass. */
export const STORAGE_BUSY_RETRY_S = 30;

/** A message body as a post takes it, or refused; the door checks it before placing it. */
export function checkMessage(body: unknown): asserts body is string {
  if (typeof body !== "string") {
    throw new Invalid("body is text");
  }
  if (!body || utf8Bytes(body) > MAX_BODY_BYTES) {
    throw new Invalid(`a message body is 1 to ${MAX_BODY_BYTES} bytes`);
  }
  if (LONE_SURROGATE.test(body)) {
    throw new Invalid("a message body is well-formed text");
  }
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

/** A run of one of the owner's own agents, which acts in the ledger as that agent. */
export interface Native {
  readonly native: string;
}

/** Who acts in the ledger: the owner (null), an outside client, or an agent's run. */
export type Actor = Origin | Native | null;

function isNative(actor: Actor): actor is Native {
  return actor !== null && "native" in actor;
}

/**
 * The authority mail is sent under: an outside client's grant at its generation and the
 * sign-in it was verified by, and where a scoped grant acts (its universe, null for a full
 * grant's). None (null) is the owner's own.
 */
export interface MailAuthority {
  readonly client: string;
  readonly family: string;
  readonly generation: number;
  readonly authenticated_at: number | null;
  readonly universe: string | null;
}

/** One of the owner's own agents as the ledger knows it. */
export type NativeAgent = {
  readonly agent: string;
  readonly agent_id: string;
  readonly created_at: number;
  readonly retired_at: number | null;
};

/** Mail to one of the owner's own agents that no run has taken yet. */
export interface Delivery {
  readonly message_id: string;
  readonly agent: string;
  readonly agent_id: string;
  readonly room_id: string;
  /** The thread's first message: the conversation key. */
  readonly thread: string;
  readonly sender_id: string;
  readonly sender_kind: string;
  /** Who sent it, by name: the owner, an outside agent's display name, an agent's name. */
  readonly sender_name: string;
  readonly body: string | null;
  readonly integrity: Integrity;
  /** A placed body (ADR-035) this read was not handed: the door reads it and asks again. */
  readonly content_id: string | null;
  readonly authority: MailAuthority | null;
  /** Whether a person sent it (the owner's own call): the run it starts is attended. */
  readonly attended: boolean;
}

/** A message of a mail chain, as the owner reads the chain: who it went to, and how far. */
export type ChainMail = Mail & {
  readonly recipients: readonly { readonly agent_id: string; readonly state: string }[];
};

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
  /** The owner's hourly cap on this agent's wakes; null, the default, for none. */
  readonly max_wakes_per_hour: number | null;
  readonly batch_window_s: number;
  /** Messages this agent sent that the owner has not acked yet. */
  readonly unacked_to_owner: number;
  /** The hourly cap's counter (GCRA's theoretical arrival time), or null. */
  readonly wake_tat: number | null;
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
  readonly state: string;
  readonly due: number;
  readonly claim_id: string | null;
};

/**
 * What a doorbell may carry: identifiers, the mailbox cursor, what kind of change it is
 * and the owner-wide change cursor it is after; never a body (S31).
 */
export interface WakeTarget {
  readonly wake_id: string;
  readonly claim_id: string;
  readonly agent_id: string;
  readonly command_center_id: string;
  readonly client: string;
  readonly family: string;
  readonly cursor: number;
  readonly through: number;
  readonly changes: readonly Change[];
  readonly after: number;
  /** The first change's own time, in seconds: the event's timestamp. */
  readonly at: number;
  /** How many changes folded into it since it opened: a subscription is owed each fold. */
  readonly fold: number;
}

/** A live subscription, as the doorbell sends to it: its sealed blob and what it follows. */
export interface LiveCallback extends Follows {
  readonly subscription_id: string;
  readonly agent_id: string;
  readonly principal: string;
  readonly kind: string;
  readonly blob: string;
  readonly kid: string;
  readonly version: number;
  /** The secret a refresh replaced, while it still signs beside the new one. */
  readonly previous: { readonly blob: string; readonly version: number } | null;
  /** The wake (`<wake_id>.<fold>`) it was last sent, or null. */
  readonly rung: string | null;
  /** Its last answer's status, or null. */
  readonly status: number | null;
}

/**
 * A subscription to store, from the binder: its server-derived id (from the caller, URL,
 * event and arguments), what it follows, the blob the gateway sealed for it (`kid`,
 * `version`), and when it lapses (seconds, or null for no expiry). From an agent's client,
 * its `command_center_id` names the agent; from the owner, `agent_id` and the `kind` it
 * pasted a token for.
 */
export interface Subscription extends Follows {
  readonly subscription_id: string;
  readonly command_center_id?: string;
  readonly agent_id?: string;
  readonly kind?: string;
  readonly blob: string;
  readonly kid: string;
  readonly version: number;
  readonly expires_at: number | null;
}

type CallbackRow = {
  subscription_id: string;
  agent_id: string;
  principal: string;
  kind: string;
  board: number;
  rooms: string;
  blob: string;
  kid: string;
  version: number;
  previous_blob: string | null;
  previous_version: number | null;
  previous_until: number | null;
  rung: string | null;
  status: number | null;
  expires_at: number | null;
  delivery_method: string;
};

export type Claim =
  | { readonly outcome: "claimed"; readonly target: WakeTarget }
  /** The agent's hourly cap is spent; the wake is due again at `due`. */
  | { readonly outcome: "capped"; readonly due: number }
  /** The agent is gone, disabled or no longer rings: its open wakes are suppressed. */
  | { readonly outcome: "suppressed"; readonly agent_id: string }
  /** The owner is not admitted (`require_work`): nothing is claimed, the row waits. */
  | { readonly outcome: "held" }
  | { readonly outcome: "not_due" };

/**
 * A doorbell's answer. `retry_after` is a 429's, in seconds; `accepted`, on a rate-limited
 * wake, that another of its subscriptions took it, so its mail is woken.
 */
export interface Outcome {
  readonly state: "sent" | "rate_limited" | "failed" | "unknown" | "suppressed";
  readonly status?: number;
  readonly retry_after?: number | null;
  readonly reason?: string;
  readonly accepted?: boolean;
}

type Settle = Outcome | { readonly state: "pending"; readonly reason?: string };

type Usage = {
  agents: number;
  enabled_agents: number;
  messages: number;
  settled_wakes: number;
  unacked: number;
  acked_bytes: number;
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
  authority: string | null;
  attended: number;
  line_bytes: number;
};

type StoredNative = NativeAgent & { unacked_to_owner: number };

/** A room's member, resolved: the owner, an enabled outside agent, or a live own agent. */
type Member =
  | { readonly kind: "owner"; readonly id: string }
  | { readonly kind: "outside"; readonly id: string; readonly agent: Agent }
  | { readonly kind: "native"; readonly id: string; readonly native: StoredNative };

type StoredWake = {
  wake_id: string;
  agent_id: string;
  state: string;
  due: number;
  claim_id: string | null;
  attempts: number;
  marked: number;
  through_seq: number | null;
  changes: number;
  after: number;
  fold: number;
  created_at: number;
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

/** A mail authority as kept with its message, or null; `universe` is its message's own. */
function authorityOf(stored: string | null, universe: string): MailAuthority | null {
  if (stored === null) {
    return null;
  }
  const kept = JSON.parse(stored) as Omit<MailAuthority, "universe">;
  return { ...kept, universe: universe === "" ? null : universe };
}

/** An enabled agent that rings: the only kind a wake is sent to. */
function rings(agent: Agent): boolean {
  return agent.disabled_reason === null && ringingMethods().includes(agent.delivery_method);
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
      || !keys.every((k) => ["state", "status", "retry_after", "reason", "accepted"].includes(k))
      || ("accepted" in o && (typeof o["accepted"] !== "boolean" || o["state"] !== "rate_limited"))
      || ("status" in o && !count(o["status"], 100, 599))
      || ("retry_after" in o && o["retry_after"] !== null && typeof o["retry_after"] !== "number")
      || ("reason" in o && (typeof o["reason"] !== "string" || !REASON.test(o["reason"])))) {
    throw new Invalid("invalid wake outcome");
  }
  return o as unknown as Outcome;
}

/**
 * How long a wake waits before it goes again, in seconds: a 429's `Retry-After` exactly as
 * the vendor sent it; with none it could read, or none in the future (zero, or a past
 * date, which would keep the object busy at the callback's word), the lease doubled for
 * each attempt the wake has made (exponential backoff from the lane's own smallest
 * interval, never a constant).
 */
export function retryAfter(value: number | null | undefined, attempts: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value
    : WAKE_LEASE_S * 2 ** Math.max(0, attempts - 1);
}

/** Whether an outside client's grant still stands: the object's grants (`grants.ts`). */
export interface GrantReader {
  live(client: string): boolean;
}

/** A message as its archive segment holds it: its row, its body copied in, its recipients. */
export interface ArchivedMail {
  readonly message: StoredMessage;
  readonly body: string;
  readonly recipients: readonly { readonly agent_id: string; readonly state: string }[];
}

/** A segment's fence row (`archive_segments`): where it is and what it spans. */
export type Fence = {
  readonly segment: number;
  readonly first_key: number;
  readonly last_key: number;
  readonly first_at: number;
  readonly last_at: number;
  readonly records: number;
  readonly bytes: number;
  readonly content_id: string;
  readonly trailer_offset: number;
  readonly trailer_bytes: number;
  readonly members: string;
};

/** A row cut to the columns its table exports, in their order: its export line's row. */
function exportRow(table: string, row: Readonly<Record<string, unknown>>):
    Record<string, unknown> {
  return Object.fromEntries(exported()[table]!.columns.map((column) =>
    [column, row[column] ?? null]));
}

/**
 * A message's record, as a segment holds it: its row, its body's row with the body's bytes
 * raw beside it (never escaped, review N3), its recipients.
 */
export function mailRecord(message: Readonly<Record<string, unknown>>,
                           body: Readonly<Record<string, unknown>>,
                           recipients: readonly Readonly<Record<string, unknown>>[],
                           raw: readonly Uint8Array[]): SegmentRecord {
  return { key: message["seq"] as number, chain: message["chain_root"] as string,
           rows: [["ledger_messages", exportRow("ledger_messages", message)],
                  ["message_bodies", { ...exportRow("message_bodies", body), body: null }, raw],
                  ...recipients.map((each) =>
                    ["ledger_recipients", exportRow("ledger_recipients", each)] as const)] };
}

/**
 * The bytes a new message takes as its segment will hold it (D2): its lines, its body raw
 * (`body_bytes`) and its newline, and its trailer share. Its own `line_bytes` and its body's
 * `meta_bytes` are in its lines, so settled to a fixed point; the body is never copied here.
 */
function mailLineBytes(message: Readonly<Record<string, unknown>>,
                       body: Readonly<Record<string, unknown>>,
                       recipient: Readonly<Record<string, unknown>>): number {
  const size = body["body_bytes"] as number;
  const of = (rows: readonly SegmentRow[]): number => rows.reduce((sum, [table, row, raw]) =>
    sum + (raw === undefined ? bytesOf(rowLine(table, row))
      : bytesOf(rowLine(table, row, size)) + size + 1), 0);
  let bytes = 0;
  for (let i = 0; i < 6; i++) {
    const next = of(mailRecord({ ...message, line_bytes: bytes },
                               { ...body, meta_bytes: Math.max(0, bytes - size) },
                               [recipient], []).rows) + RECORD_TRAILER_BYTES;
    if (next === bytes) {
      return bytes;
    }
    bytes = next;
  }
  return bytes;
}

/** An archived record's bytes, read back as its message, its raw body decoded. */
export function archivedMail(bytes: Uint8Array): ArchivedMail {
  let message: StoredMessage | undefined;
  let body: string | undefined;
  const recipients: { agent_id: string; state: string }[] = [];
  for (const [table, row, raw] of parseRecord(bytes)) {
    if (table === "ledger_messages") {
      message = row as unknown as StoredMessage;
    } else if (table === "message_bodies" && raw !== undefined) {
      body = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw[0]);
    } else if (table === "ledger_recipients") {
      recipients.push({ agent_id: row["agent_id"] as string, state: row["state"] as string });
    }
  }
  if (message === undefined || body === undefined) {
    throw new Error("an archived mail record lacks its message or its body");
  }
  recipients.sort((a, b) => (a.agent_id < b.agent_id ? -1 : a.agent_id > b.agent_id ? 1 : 0));
  return { message, body, recipients };
}

/**
 * A hot message's bytes as its segment record holds them, but its body: its row's line and
 * its body row's (the body raw after it, the row naming no content: the segment holds it),
 * its inline body's bytes if it has no placed one, and the newline after the body with its
 * recipients' lines (`mailRecord`'s, exactly).
 */
export interface MailParts {
  readonly head: Uint8Array;
  readonly inline: Uint8Array | null;
  readonly tail: Uint8Array;
}

/** A hot message's parts (`MailParts`) from its rows. */
function mailParts(message: Readonly<Record<string, unknown>>,
                   body: Readonly<Record<string, unknown>>,
                   recipients: readonly Readonly<Record<string, unknown>>[]): MailParts {
  const encoder = new TextEncoder();
  const size = body["body_bytes"] as number;
  return {
    head: encoder.encode(rowLine("ledger_messages", exportRow("ledger_messages", message))
      + rowLine("message_bodies", { ...exportRow("message_bodies", body), body: null,
                                    content_id: null }, size)),
    inline: body["content_id"] === null ? encoder.encode(body["body"] as string) : null,
    tail: encoder.encode("\n" + recipients.map((each) =>
      rowLine("ledger_recipients", exportRow("ledger_recipients", each))).join("")),
  };
}

/**
 * A record a segment holds, kept as it is, appended to a move's table: its range of that
 * segment's content (`ref`), and its first line (its message's row, the only part read) for
 * its room, its chain and its time.
 */
export function keptRecord(table: MoveTable, key: number, offset: number, length: number,
                           ref: number, line: Uint8Array): number {
  const [row] = parseRecord(line);
  if (row === undefined || row[0] !== "ledger_messages") {
    throw new Error("an archived mail record lacks its message");
  }
  const message = row[1];
  return table.push(key, message["chain_root"] as string, message["room_id"] as string,
                    message["created_at"] as number, length, MoveTable.KEPT, ref, offset);
}

/** A fence row's members bloom, as bytes. */
export function fenceBloom(fence: Fence): Uint8Array {
  return Uint8Array.from(Buffer.from(fence.members, "hex"));
}

/** The hot messages a move reads at once, its rows and its lines (review N5). */
const IMAGE_GROUP = 512;

/** The archive chain read's segments a page reads at most. */
export const CHAIN_SEGMENTS = 8;

export class Ledger {
  readonly #storage: OwnerStorage;
  readonly #grants: GrantReader;
  readonly #limits: LedgerLimits;
  /** Whether the owner is admitted (`admission.ts`'s `require_work`), read in each claim. */
  readonly #admitted: () => boolean;

  /** The owner-wide change cursor (`Collections.clock`): what a mail wake is after. */
  readonly #changes: () => number;

  /** How a posting unit adopts a placed body's content (`Bodies.adopt`); none, none placed. */
  readonly #adopt: Adopt | undefined;

  constructor(storage: OwnerStorage, grants: GrantReader,
              limits: LedgerLimits = LEDGER_LIMITS, admitted: () => boolean = () => true,
              changes: () => number = () => 0, adopt?: Adopt) {
    this.#storage = storage;
    this.#grants = grants;
    this.#limits = checkLimits(limits);
    this.#admitted = admitted;
    this.#changes = changes;
    this.#adopt = adopt;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** The owner-wide counters; only migration 2's triggers write them. */
  #usage(): Usage {
    return this.#sql.exec<Usage>(
      "SELECT agents, enabled_agents, messages, settled_wakes, unacked, acked_bytes " +
        "FROM ledger_usage",
    ).one();
  }

  /** A registered agent, enabled or not. */
  #agent(agentId: string): Agent | undefined {
    return this.#sql.exec<Agent>(
      "SELECT * FROM outside_agents WHERE agent_id = ?", agentId,
    ).toArray()[0];
  }

  // -- the agent registry ---------------------------------------------------

  /** Every agent for the owner; an outside caller sees only its own identity's. */
  agents(origin: Origin | null): Agent[] {
    if (origin === null) {
      return this.#sql.exec<Agent>(
        "SELECT * FROM outside_agents ORDER BY created_at, agent_id",
      ).toArray();
    }
    return this.#sql.exec<Agent>(
      "SELECT * FROM outside_agents WHERE outside_client = ? AND outside_family = ? " +
        "ORDER BY created_at, agent_id", origin.client, origin.family,
    ).toArray();
  }

  // -- the owner's own agents: identities that follow their files -----------------

  /** The owner's own agents: every one known for the owner, the live ones for anyone else. */
  natives(all: boolean): NativeAgent[] {
    return this.#sql.exec<NativeAgent>(
      "SELECT agent, agent_id, created_at, retired_at FROM native_agents " +
        `${all ? "" : "WHERE retired_at IS NULL "}ORDER BY agent`).toArray();
  }

  #nativeNamed(agent: string): StoredNative | undefined {
    return this.#sql.exec<StoredNative>("SELECT * FROM native_agents WHERE agent_id = ?",
                                        nativeId(agent)).toArray()[0];
  }

  /** A live own agent by name, or refused: gone (`AGENT_RETIRED`) or never one (`NO_AGENT`). */
  #liveNative(agent: string): StoredNative {
    const row = this.#nativeNamed(agent);
    if (row === undefined) {
      throw new Refused(NO_AGENT);
    }
    if (row.retired_at !== null) {
      throw new Refused(AGENT_RETIRED);
    }
    return row;
  }

  /**
   * In the file change's unit: `agent`'s `agent.json` appeared, so it is an identity, made
   * or live again as itself (its id and mail kept). A live one takes one of the reserved
   * mailboxes, refused past them; a new row past the registry's rows forgets the oldest
   * retired one first (its mail stays). A name that is no agent id is no identity.
   */
  appear(agent: string, now: number = clock()): void {
    if (!AGENT_DIR.test(agent)) {
      return;
    }
    const sql = this.#sql;
    const limits = this.#limits;
    this.#storage.transactionSync(() => {
      const row = this.#nativeNamed(agent);
      if (row !== undefined && row.retired_at === null) {
        return;
      }
      const usage = this.#usage();
      if (usage.enabled_agents >= limits.maxAgents) {
        throw new Refused(`ledger_agents_full: the owner's agents are at the ledger's ${
          limits.maxAgents} enabled agents, each a reserved mailbox`);
      }
      if (row !== undefined) {
        sql.exec("UPDATE native_agents SET retired_at = NULL WHERE agent_id = ?", row.agent_id);
        return;
      }
      if (usage.agents >= limits.maxAgentRows) {
        // At most `maxAgentRows` rows to look through, and only when the registry is full.
        // Agents retired at one instant (the runtime's clock stands still in a unit) go in
        // their names' order, never their digests'.
        const forgotten = sql.exec<{ agent_id: string }>(
          "DELETE FROM native_agents WHERE agent_id = (SELECT agent_id FROM native_agents " +
            "WHERE retired_at IS NOT NULL ORDER BY retired_at, agent LIMIT 1) " +
            "RETURNING agent_id",
        ).toArray()[0];
        if (forgotten === undefined) {
          throw new Refused("ledger_agents_full: the owner's agent registry is full");
        }
        sql.exec("DELETE FROM ledger_readers WHERE reader = ?", forgotten.agent_id);
      }
      sql.exec("INSERT INTO native_agents (agent_id, agent, created_at) VALUES (?, ?, ?)",
               nativeId(agent), agent, now);
    });
  }

  /**
   * In the file change's unit: `agent`'s `agent.json` is gone, so its identity is retired.
   * Its mail is kept; what it held unacked is released, and what no run took yet fails,
   * the mail it sent another agent included.
   */
  retire(agent: string, now: number = clock()): void {
    const sql = this.#sql;
    this.#storage.transactionSync(() => {
      const row = sql.exec<{ agent_id: string }>(
        "UPDATE native_agents SET retired_at = ? WHERE agent_id = ? AND retired_at IS NULL " +
          "RETURNING agent_id", now, nativeId(agent)).toArray()[0];
      if (row === undefined) {
        return;
      }
      sql.exec("UPDATE ledger_recipients SET state = 'failed', changed_at = ? WHERE " +
                 "agent_id = ? AND state IN ('queued', 'held_cap')", now, row.agent_id);
      sql.exec("UPDATE ledger_recipients SET unacked = 0 WHERE agent_id = ? AND unacked = 1",
               row.agent_id);
      this.#failSent(row.agent_id, now);
    });
  }

  /**
   * The mail `sender` sent the owner's agents that no run took yet: failed, with a receipt
   * (its sender retired or disabled, it starts nothing). An index range of its messages.
   */
  #failSent(sender: string, now: number): void {
    for (const { message_id: messageId, agent_id: agentId } of this.#sql.exec<
      { message_id: string; agent_id: string }>(
      "UPDATE ledger_recipients SET state = 'failed', unacked = 0, changed_at = ? WHERE " +
        `${DUE} AND message_id IN (SELECT message_id FROM ledger_messages WHERE ` +
        "sender_id = ?) RETURNING message_id, agent_id", now, sender).toArray()) {
      this.#receipt(agentId, "failed", messageId, now, { reason: "sender_gone" });
    }
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
    if (!admittedMethods().includes(method)) {
      throw new Refused(`only ${admittedMethods().join(", ")} delivery are available yet`);
    }
    // A client subscribes its own callback over the MCP door; an API key has no such door.
    if (verified.kind === "api_key" && kindFor(method)?.push?.callback === "subscribe") {
      throw new Refused(`an API-key agent cannot subscribe; ${method} is for MCP clients`);
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
          throw new Refused("the owner's agent registry is full; delete a disabled agent " +
                            "to make room");
        }
        agentId = "agent_" + token(8);
        sql.exec(
          "INSERT INTO outside_agents (agent_id, command_center_id, outside_client, " +
            "outside_family, source, source_ref, display_name, delivery_method, recipe_id, " +
            "revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
          agentId, commandCenter, client, family, ...fields, now,
        );
      } else {
        // The same outside identity again: re-enabled, one revision on.
        agentId = row.agent_id;
        if (method !== row.delivery_method) {
          this.#erase(row.agent_id, true);  // a subscription of another kind never rings it
        }
        sql.exec(
          "UPDATE outside_agents SET source = ?, source_ref = ?, display_name = ?, " +
            "delivery_method = ?, recipe_id = ?, disabled_reason = NULL, " +
            "revision = revision + 1 WHERE agent_id = ?",
          ...fields, agentId,
        );
      }
      if (!ringingMethods().includes(method)) {
        this.#suppress(agentId, "delivery_method", now);
      }
      return this.#agent(agentId)!;
    });
  }

  /**
   * A disabled agent can neither read nor ack, so its unacked mail is released: it no
   * longer holds the ledger and is evicted in turn (a re-enabled agent may still read
   * what is left). Its open wakes are suppressed and its subscriptions erased. The owner
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
      this.#erase(agentId, true);
      sql.exec("UPDATE outside_agents SET disabled_reason = ?, revision = revision + 1 " +
                 "WHERE agent_id = ?", reason, agentId);
      sql.exec("UPDATE ledger_recipients SET unacked = 0 WHERE agent_id = ? AND unacked = 1",
               agentId);
      this.#failSent(agentId, now);
      this.#suppress(agentId, "disabled", now);
      return this.#agent(agentId)!;
    });
  }

  /**
   * The owner deletes an agent: every message in its dm room with their bodies and
   * recipients, its cursor, wakes, subscriptions and receipts, the owner's own read and
   * reply receipts for those messages, and its registry row. Each step is an index range of
   * that agent's rows.
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
      this.#erase(agentId, true);
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
      sql.exec("DELETE FROM ledger_wakes WHERE agent_id = ?", agentId);
      sql.exec("DELETE FROM outside_agents WHERE agent_id = ?", agentId);
      // Its archived mail: dropped from every read and the export at once, and rewritten
      // out of each segment that may hold it by the `archive` lane (`archive_purges`).
      if (sql.exec("SELECT 1 FROM archive_segments LIMIT 1").toArray().length > 0) {
        // At most `maxAgentRows` purges wait (the guard's term): past them the lane is still
        // rewriting, and a deletion waits for it, refused in plain words, never lost.
        if (sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM archive_purges").one().n
            >= this.#limits.maxAgentRows) {
          throw new Refused(`${STORAGE_BUSY}: earlier deleted agents' mail is still being ` +
                            `removed from the archive; try again in ${STORAGE_BUSY_RETRY_S} ` +
                            `seconds. ${JSON.stringify({ retry_after_s: STORAGE_BUSY_RETRY_S })}`);
        }
        sql.exec("INSERT INTO archive_purges (agent_id, at) VALUES (?, ?) ON CONFLICT " +
                   "(agent_id) DO NOTHING", agentId, now);
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
    if (maxWakesPerHour !== null && !count(maxWakesPerHour, hourLow, hourHigh)) {
      throw new Invalid(`max_wakes_per_hour is a whole number from ${hourLow}, or null for none`);
    }
    const { low: batchLow, high: batchHigh } = WAKE_SETTINGS.batchWindowS;
    if (!count(batchWindowS, batchLow, batchHigh)) {
      throw new Invalid(`batch_window_s is a whole number of seconds from ${batchLow}`);
    }
    return this.#storage.transactionSync(() => {
      // A changed cap counts afresh: its counter goes with the old cap.
      if (!this.#sql.exec(
        "UPDATE outside_agents SET max_wakes_per_hour = ?, batch_window_s = ?, wake_tat = NULL, " +
          "revision = revision + 1 WHERE agent_id = ? RETURNING agent_id",
        maxWakesPerHour, batchWindowS, agentId,
      ).toArray().length) {
        throw new Refused("agent not found");
      }
      return this.#agent(agentId)!;
    });
  }

  /** The reader an actor is: the owner, an origin's one enabled agent, or a live own agent. */
  #reader(origin: Actor): string {
    if (origin === null) {
      return OWNER;
    }
    if (isNative(origin)) {
      throw new Refused(RUN_NO_MAILBOX);
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

  /**
   * A room's two members, resolved: `dm:<a>` is the owner and `a`; `dm:<a>+<b>` two agents,
   * `a` before `b`, at least one of them the owner's own. An outside member is an enabled
   * agent; an own one a live one (`AGENT_RETIRED`, `NO_AGENT`).
   */
  #room(room: string): [Member, Member] {
    const ids = room.startsWith("dm:") ? room.slice(3).split("+") : [];
    const member = (id: string): Member => {
      if (OUTSIDE_ID.test(id)) {
        const agent = this.#sql.exec<Agent>(
          "SELECT * FROM outside_agents WHERE agent_id = ? AND disabled_reason IS NULL", id,
        ).toArray()[0];
        if (agent === undefined) {
          throw new Refused("room is not an enabled agent's dm");
        }
        return { kind: "outside", id, agent };
      }
      if (NATIVE_ID.test(id)) {
        const native = this.#sql.exec<StoredNative>(
          "SELECT * FROM native_agents WHERE agent_id = ?", id).toArray()[0];
        if (native === undefined) {
          throw new Refused(NO_AGENT);
        }
        if (native.retired_at !== null) {
          throw new Refused(AGENT_RETIRED);
        }
        return { kind: "native", id, native };
      }
      throw new Refused("room is not an enabled agent's dm");
    };
    if (ids.length === 1) {
      return [{ kind: "owner", id: OWNER }, member(ids[0]!)];
    }
    if (ids.length !== 2 || !(ids[0]! < ids[1]!) || !ids.some((id) => NATIVE_ID.test(id))) {
      throw new Refused("room is not an enabled agent's dm");
    }
    return [member(ids[0]!), member(ids[1]!)];
  }

  /** The member an actor is in a room, or refused: it posts only in its own rooms. */
  #sender(actor: Actor, members: readonly Member[]): Member {
    if (actor === null) {
      const owner = members.find((m) => m.kind === "owner");
      if (owner === undefined) {
        throw new Refused("the owner posts only in its own dm rooms");
      }
      return owner;
    }
    if (isNative(actor)) {
      const own = this.#liveNative(actor.native);
      const found = members.find((m) => m.id === own.agent_id);
      if (found === undefined) {
        throw new Refused("an agent posts only in its own dm rooms");
      }
      return found;
    }
    const found = members.find((m) => m.kind === "outside"
      && m.agent.outside_client === actor.client && m.agent.outside_family === actor.family);
    if (found === undefined) {
      throw new Refused("an outside agent posts only in its own dm room");
    }
    return found;
  }

  /**
   * Who a post by `actor` in `room` would go to, as the post itself resolves it: the
   * recipient's id, and its agent's name when it is one of the owner's own. A read.
   */
  recipient(actor: Actor, roomId: unknown): { id: string; native: string | null } {
    const members = this.#room(text(roomId, 128, "room_id"));
    const sender = this.#sender(actor, members);
    const to = members.find((m) => m !== sender)!;
    return { id: to.id, native: to.kind === "native" ? to.native.agent : null };
  }

  /** The message the owner posted under `clientMessageId`, if any: an inbound replay's. */
  ownerPosted(clientMessageId: string): string | null {
    return this.#sql.exec<{ message_id: string }>(
      "SELECT message_id FROM ledger_messages WHERE sender_id = ? AND client_message_id = ?",
      OWNER, clientMessageId).toArray()[0]?.message_id ?? null;
  }

  // -- ROOM_POST: a pair's room -----------------------------------------------

  /**
   * Whether a post would be answered as a resend, its original returned, or refused, read
   * before its body is placed (ADR-035): the room, the sender, the parent and the mailboxes'
   * caps checked, nothing written, so no body goes to R2 for a post its write refuses or
   * answers from what is held. Null for a new post.
   */
  prepare(origin: Actor, roomId: unknown, clientMessageId: unknown, body: unknown,
          parentId: unknown, archived: ArchivedMail | null = null): Posted | null {
    checkMessage(body);
    const addressed = this.#addressed(origin, roomId, clientMessageId, body, parentId, archived);
    if ("resend" in addressed) {
      return addressed.resend;
    }
    this.#roomForOne();
    return null;
  }

  /** A post's room, members, parent and caps' share, or its original when it is a resend. */
  #addressed(origin: Actor, roomId: unknown, clientMessageId: unknown, body: string,
             parentId: unknown, archived: ArchivedMail | null = null):
      { resend: Posted } | { room: string; cmid: string; parentKey: string | null; sha: string;
                             members: [Member, Member]; from: Member; to: Member;
                             kind: string; sender: string; recipient: string;
                             parent: StoredMessage | undefined; parentArchived: boolean } {
    const room = text(roomId, 128, "room_id");
    const cmid = text(clientMessageId, 128, "client_message_id");
    const parentKey = parentId === null ? null : text(parentId, 128, "parent_id");
    const sha = digest(body);
    const sql = this.#sql;
    const members = this.#room(room);
    const from = this.#sender(origin, members);
    const to = members.find((m) => m !== from)!;
    const [kind, sender, recipient] = [from.kind, from.id, to.id];
    const same = sql.exec<StoredMessage>(
      "SELECT * FROM ledger_messages WHERE sender_id = ? AND client_message_id = ?",
      sender, cmid,
    ).toArray()[0];
    if (same !== undefined) {
      if (same.body_sha256 !== sha || same.room_id !== room || same.parent_id !== parentKey) {
        throw new Refused("conflict");
      }
      return { resend: {
        message_id: same.message_id,
        recipients: sql.exec<{ agent_id: string }>(
          "SELECT agent_id FROM ledger_recipients WHERE message_id = ? ORDER BY agent_id",
          same.message_id,
        ).toArray().map((r) => r.agent_id),
        held: false,
        paused_reason: "",
      } };
    }
    let parent: StoredMessage | undefined;
    let parentArchived = false;
    if (parentKey !== null) {
      parent = sql.exec<StoredMessage>(
        "SELECT m.* FROM ledger_messages m JOIN ledger_recipients r " +
          "ON r.message_id = m.message_id AND r.agent_id = ? " +
          "WHERE m.message_id = ? AND m.room_id = ?", sender, parentKey, room,
      ).toArray()[0];
      // An archived parent (acked, so its reply marks nothing): read from its segment by
      // the door beforehand, and held to the same: to this sender, in this room.
      if (parent === undefined && archived !== null && archived.message.message_id === parentKey
          && archived.message.room_id === room && !this.#purged(archived.message.room_id)
          && archived.recipients.some((each) => each.agent_id === sender)) {
        parent = archived.message;
        parentArchived = true;
      }
      if (parent === undefined) {
        throw new Refused("parent is not a message to this sender in this room");
      }
    }
    return { room, cmid, parentKey, sha, members, from, to, kind, sender, recipient, parent,
             parentArchived };
  }

  /**
   * Post `body` in a pair's room. A body past `PLACE_BYTES` comes `placed` (ADR-035, the
   * door's `Bodies.begin`): its content is adopted in this unit, in its writer's files scope
   * (`filer`: the owner's, an outside client's, or a visiting owner's), and the body's row
   * names it. A resend's original is answered without it.
   */
  post(origin: Actor, roomId: unknown, clientMessageId: unknown, body: unknown,
       parentId: unknown, now: number = clock(), authority: MailAuthority | null = null,
       placed: Placed | null = null, filer = "owner", archived: ArchivedMail | null = null):
      Posted {
    checkMessage(body);
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const addressed = this.#addressed(origin, roomId, clientMessageId, body, parentId,
                                        archived);
      if ("resend" in addressed) {
        return addressed.resend;
      }
      const { room, cmid, parentKey, sha, members, to, kind, sender, recipient,
              parent, parentArchived } = addressed;
      const size = utf8Bytes(body);
      this.#roomForOne();
      return adopted(body, placed, filer, this.#adopt, (contentId) => {
      // The seq is given here, and the id carries it (`orderedId`): an archived message is
      // found by its id with no row for it.
      const seq = sql.exec<{ last_seq: number }>(
        "UPDATE ledger_usage SET last_seq = max(last_seq, coalesce((SELECT max(seq) FROM " +
          "ledger_messages), 0)) + 1 RETURNING last_seq").one().last_seq;
      const messageId = "msg_" + orderedId(this.#idKey(), seq);
      const depth = kind === OWNER ? 0 : (parent?.chain_depth ?? 0) + 1;
      // The owner's dm with an outside agent is in that agent's command center; any other
      // room's mail, in the universe its authority acts in.
      const outside = members.find((m) => m.kind === "outside");
      const center = members[0].kind === "owner" && outside?.kind === "outside"
        ? outside.agent.command_center_id : authority?.universe ?? "";
      const kept = authority === null ? null : JSON.stringify({
        authenticated_at: authority.authenticated_at, client: authority.client,
        family: authority.family, generation: authority.generation });
      const message = {
        seq, message_id: messageId, command_center_id: center, room_id: room,
        sender_kind: kind, sender_id: sender, client_message_id: cmid, body_sha256: sha,
        parent_id: parentKey, chain_root: parent?.chain_root ?? messageId, chain_depth: depth,
        created_at: now, body_bytes: size, unacked: 0, authority: kept,
        attended: origin === null ? 1 : 0, line_bytes: 0 };
      const lineBytes = mailLineBytes(message, {
        message_id: messageId, room_id: room, body, created_at: now, body_bytes: size,
        content_id: null, filer, meta_bytes: 0 },
                                      { message_id: messageId, agent_id: recipient, seq,
                                        state: "queued", changed_at: now, unacked: 0 });
      sql.exec(
        "INSERT INTO ledger_messages (seq, message_id, command_center_id, room_id, " +
          "sender_kind, sender_id, client_message_id, body_sha256, parent_id, chain_root, " +
          "chain_depth, created_at, body_bytes, authority, attended, line_bytes) VALUES (?, ?, " +
          "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        seq, messageId, center, room, kind, sender, cmid, sha, parentKey, message.chain_root,
        depth, now, size, kept, message.attended, lineBytes);
      sql.exec("INSERT INTO message_bodies (message_id, room_id, body, created_at, " +
                 "body_bytes, content_id, filer, meta_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               messageId, room, contentId === null ? body : "", now, size, contentId, filer,
               lineBytes - size);
      sql.exec("INSERT INTO ledger_recipients (message_id, agent_id, seq, state, changed_at) " +
                 "VALUES (?, ?, ?, 'queued', ?)", messageId, recipient, seq, now);
      // One open wake per ringing recipient; never one for the message's own sender. A
      // new wake waits out the agent's batch window, so a burst of mail rings once (W3).
      // Mail to one of the owner's own agents waits `queued` for the `runs` lane instead.
      if (to.kind === "outside" && ringingMethods().includes(to.agent.delivery_method)
          && recipient !== sender) {
        this.#open([to.agent.agent_id], CHANGE_BIT.mail, this.#changes(), now);
      }
      if (parent !== undefined) {
        if (!parentArchived) {
          sql.exec("UPDATE ledger_recipients SET state = 'replied', changed_at = ? " +
                     "WHERE message_id = ? AND agent_id = ?", now, parentKey, sender);
        }
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
    });
  }

  /**
   * In the settle's unit: a mail's run's result, posted as its agent's reply in that mail's
   * room, the mail its parent and `postId` (the run's id) its own id, so it posts once.
   *
   * A post wakes only the members it addresses, and a result addresses only the person or
   * outside client whose mail started the run: mail from another of the owner's agents gets
   * no automatic reply (null), so no agent's result ever wakes another agent. A further turn
   * between agents is a deliberate post, which needs `converse` (`roomPost`).
   */
  answer(actor: Native, mailId: string, postId: string, body: string, now: number,
         authority: MailAuthority | null, placed: Placed | null = null): Posted | null {
    const mail = this.#sql.exec<{ room_id: string; sender_kind: string }>(
      "SELECT room_id, sender_kind FROM ledger_messages WHERE message_id = ?", mailId)
      .toArray()[0];
    if (mail === undefined) {
      // Gone with its other side (an outside agent the owner deleted): nothing to answer.
      throw new Refused("the mail answered is no longer held");
    }
    if (mail.sender_kind === "native") {
      return null;
    }
    return this.post(actor, mail.room_id, postId, body, mailId, now, authority, placed,
                     authority === null ? "owner" : `client:${authority.client}`);
  }

  /**
   * Where a chain read starts: the chain holding `messageId`, its root and the root's seq,
   * when the message is hot; else the seq its id carries, for the door to find it archived.
   * A read.
   */
  chainStart(messageId: unknown): { root: string; root_seq: number } | { archived: number } {
    if (typeof messageId !== "string") {
      throw new Invalid("a chain read names a message id");
    }
    const row = this.#sql.exec<{ chain_root: string }>(
      "SELECT chain_root FROM ledger_messages WHERE message_id = ?", messageId).toArray()[0];
    if (row !== undefined) {
      const rootSeq = this.seqOf(row.chain_root);
      return { root: row.chain_root, root_seq: rootSeq ?? 0 };
    }
    const seq = this.seqOf(messageId);
    if (seq === null) {
      throw new Refused("no such message held");
    }
    return { archived: seq };
  }

  /**
   * The segments a chain read past `after` reads next: those whose keys run past it (and
   * past the root's seq), at most `CHAIN_SEGMENTS`, in key order, and the frontier: the next
   * segment's first key (no archived message of the chain below it is unread), null for
   * none. Segments never overlap in key range (`archive_segments_disjoint`), so key order is
   * one order.
   */
  chainFences(rootSeq: number, after: number): { fences: Fence[]; frontier: number | null } {
    const from = Math.max(after, rootSeq - 1);
    const fences = this.#sql.exec<Fence>(
      "SELECT * FROM archive_segments WHERE stream = 'mail' AND last_key > ? " +
        "ORDER BY last_key LIMIT ?", from, CHAIN_SEGMENTS + 1).toArray();
    return fences.length <= CHAIN_SEGMENTS ? { fences, frontier: null }
      : { fences: fences.slice(0, CHAIN_SEGMENTS), frontier: fences[CHAIN_SEGMENTS]!.first_key };
  }

  /**
   * The owner's view of a mail chain: the chain rooted at `root`, oldest first, past
   * `after`, each message with its recipients and how far each took it, its archived
   * messages (read from their segments by the door, `archived`) merged with the hot ones by
   * seq, below `frontier` when the door has more to read. A page holds at most `limit`
   * messages and, past its first, no more than `READ_PAGE_BYTES` of bodies, as a mailbox
   * read does (so a chain of large bodies is paged, never held whole). A read. `after` is
   * the cursor for the next page.
   */
  chain(root: string, after: unknown, limit: unknown,
        placed: ReadonlyMap<string, string> = new Map(),
        archived: { readonly mail: readonly ArchivedMail[]; readonly frontier: number | null } =
          { mail: [], frontier: null }):
      { root: string; messages: ChainMail[]; more: boolean; after: number; placed: string[] } {
    const maxRead = this.#limits.maxRead;
    if (!count(after, 0, Number.MAX_SAFE_INTEGER) || !count(limit, 1, maxRead)) {
      throw new Invalid(`a chain read names a message id, after a seq, and 1 to ${maxRead}`);
    }
    const sql = this.#sql;
    const below = archived.frontier ?? Number.MAX_SAFE_INTEGER;
    const rows = sql.exec<StoredMessage>(
      "SELECT * FROM ledger_messages WHERE seq > ? AND seq < ? AND chain_root = ? " +
        "ORDER BY seq LIMIT ?", after, below, root, (limit as number) + 1).toArray();
    const cold = archived.mail.filter((mail) => mail.message.seq > (after as number)
      && mail.message.seq < below && mail.message.chain_root === root
      && !this.#purged(mail.message.room_id));
    // The page, by seq, before any placed body is read: `limit` messages, and past the
    // first only while their bodies fit `READ_PAGE_BYTES`.
    const merged = [...rows.map((row) => ({ seq: row.seq, bytes: row.body_bytes, row })),
                    ...cold.map((mail) => ({ seq: mail.message.seq,
                                             bytes: mail.message.body_bytes, mail }))]
      .sort((a, b) => a.seq - b.seq);
    const page: typeof merged = [];
    let bytes = 0;
    for (const item of merged) {
      if (page.length >= (limit as number)
          || (page.length > 0 && bytes + item.bytes > READ_PAGE_BYTES)) {
        break;
      }
      page.push(item);
      bytes += item.bytes;
    }
    const unread: string[] = [];
    const messages = page.map((item): ChainMail => {
      if ("mail" in item) {
        return this.#chainMail(item.mail.message, item.mail.body, item.mail.recipients);
      }
      const row = item.row;
      const stored = sql.exec<{ body: string; content_id: string | null }>(
        "SELECT body, content_id FROM message_bodies WHERE message_id = ?", row.message_id)
        .toArray()[0];
      const kept = stored === undefined ? undefined
        : stored.content_id === null ? stored.body : placed.get(stored.content_id);
      if (stored?.content_id != null && kept === undefined) {
        unread.push(stored.content_id);
      }
      return this.#chainMail(row, kept, sql.exec<{ agent_id: string; state: string }>(
        "SELECT agent_id, state FROM ledger_recipients WHERE message_id = ? ORDER BY agent_id",
        row.message_id).toArray());
    });
    const more = merged.length > messages.length || archived.frontier !== null;
    const cursor = messages.length === merged.length && archived.frontier !== null
      ? Math.max(after as number, archived.frontier - 1)
      : messages.at(-1)?.seq ?? (after as number);
    // A placed body (ADR-035) the door did not hand in is named, for it to read and ask again.
    return { root, messages, more, after: cursor, placed: unread };
  }

  /** A chain's message as the owner reads it, its body checked against its hash. */
  #chainMail(row: StoredMessage, kept: string | undefined,
             recipients: readonly { agent_id: string; state: string }[]): ChainMail {
    const [body, integrity]: [string | null, Integrity] = kept === undefined
      ? [null, "missing"]
      : digest(kept) !== row.body_sha256 ? [null, "tampered"] : [kept, "ok"];
    return {
      seq: row.seq, message_id: row.message_id, room_id: row.room_id,
      sender_kind: row.sender_kind, sender_id: row.sender_id, parent_id: row.parent_id,
      chain_depth: row.chain_depth, created_at: row.created_at, reply_s: null, body,
      integrity, recipients: [...recipients],
    };
  }

  // -- THE ARCHIVE: acked mail moved to segments (slice STORE item 2, `segments.ts`) ----------

  /** The key message ids carry their seq under, made with the first message. In a unit. */
  #idKey(): Uint8Array {
    const held = this.#sql.exec<{ id_key: string }>("SELECT id_key FROM ledger_ids")
      .toArray()[0];
    if (held !== undefined) {
      return Uint8Array.from(Buffer.from(held.id_key, "hex"));
    }
    const made = idKey();
    this.#sql.exec("INSERT INTO ledger_ids (only, id_key) VALUES (1, ?)",
                   Buffer.from(made).toString("hex"));
    return made;
  }

  /** The seq a message id carries, or null for one the object never gave. A read. */
  seqOf(messageId: string): number | null {
    const held = this.#sql.exec<{ id_key: string }>("SELECT id_key FROM ledger_ids")
      .toArray()[0];
    if (held === undefined || !messageId.startsWith("msg_")) {
      return null;
    }
    return idOrder(Uint8Array.from(Buffer.from(held.id_key, "hex")), messageId.slice(4));
  }

  /** Whether a room's mail is a deleted agent's, its segments not yet all rewritten. A read. */
  #purged(room: string): boolean {
    const members = roomMembers(room);
    return members.length > 0 && this.#sql.exec(
      "SELECT 1 FROM archive_purges WHERE agent_id IN (SELECT value FROM json_each(?))",
      JSON.stringify(members)).toArray().length > 0;
  }

  /** The agents deleted while segments may still hold their mail. A read. */
  purgedAgents(): string[] {
    return this.#sql.exec<{ agent_id: string }>("SELECT agent_id FROM archive_purges")
      .toArray().map((row) => row.agent_id);
  }

  /**
   * Whether a segment's worth of acked mail waits to move: its records, or their own bytes
   * (`line_bytes` less each one's trailer share, as `pack` and the segment count them, so a
   * seal is never short of the least, review N4). A read of the counters.
   */
  #sealable(usage: Usage): boolean {
    const acked = usage.messages - usage.unacked;
    return acked >= this.#limits.archiveRecords
      || usage.acked_bytes - acked * RECORD_TRAILER_BYTES >= this.#limits.archiveBytes;
  }

  /**
   * The oldest acked hot message whose seq is below the archive's last key: acked after the
   * mail around it moved, so it goes into the segment its key falls in (`archivePick`), never
   * a new one that would overlap. A read of one index range.
   */
  #straggler(): number | null {
    return this.#sql.exec<{ seq: number | null }>(
      "SELECT min(seq) AS seq FROM ledger_messages WHERE unacked = 0 AND seq < (SELECT " +
        "coalesce(max(last_key), 0) FROM archive_segments WHERE stream = 'mail')").one().seq;
  }

  /**
   * Whether the `archive` lane has a move to make: hot mail past half the ceiling, and a
   * straggler to merge or a segment's worth of acked mail. A read.
   */
  archiveDue(): boolean {
    const usage = this.#usage();
    return usage.messages > this.#limits.maxMessages / 2
      && (this.#straggler() !== null || this.#sealable(usage));
  }

  /**
   * The next move's batch and the placed bodies to read for it; null when none waits. A
   * straggler's: every acked hot message whose key falls in the segment that holds the
   * oldest straggler's range or lies just past it (`into`), merged into it, so no two
   * segments ever overlap. Else the tail's: the oldest acked messages, every one past the
   * archive's last key, up to a seal (its lines' bytes or its records). A read.
   */
  archivePick(): { seqs: number[]; contents: string[]; into: Fence | null } | null {
    const limits = this.#limits;
    const sql = this.#sql;
    const straggler = this.#straggler();
    let into: Fence | null = null;
    let rows: { seq: number; line_bytes: number; content_id: string | null }[];
    if (straggler !== null) {
      into = sql.exec<Fence>(
        "SELECT * FROM archive_segments WHERE stream = 'mail' AND last_key >= ? " +
          "ORDER BY last_key LIMIT 1", straggler).one();
      rows = sql.exec<{ seq: number; line_bytes: number; content_id: string | null }>(
        "SELECT m.seq, m.line_bytes, b.content_id FROM ledger_messages m JOIN message_bodies b " +
          "ON b.message_id = m.message_id WHERE m.unacked = 0 AND m.seq >= ? AND m.seq <= ? " +
          "ORDER BY m.seq LIMIT ?", straggler, into.last_key, limits.archiveRecords).toArray();
    } else {
      if (!this.#sealable(this.#usage())) {
        return null;
      }
      rows = sql.exec<{ seq: number; line_bytes: number; content_id: string | null }>(
        "SELECT m.seq, m.line_bytes, b.content_id FROM ledger_messages m JOIN message_bodies b " +
          "ON b.message_id = m.message_id WHERE m.unacked = 0 ORDER BY m.seq LIMIT ?",
        limits.archiveRecords).toArray();
    }
    // The byte budget, in records' own bytes (their trailer shares out, review N4). A tail
    // move seals once they reach the seal (so it holds the least). A merge holds its segment
    // and its stragglers in the isolate at once, so it takes stragglers only while the two
    // fit `MOVE_BYTES` (review N3: the measured move's bound), and always at least one (so
    // the oldest straggler always moves); the rest merge on the next passes.
    const budget = into === null ? limits.archiveBytes : Math.max(0, MOVE_BYTES - into.bytes);
    const seqs: number[] = [];
    const contents: string[] = [];
    let bytes = 0;
    for (const row of rows) {
      const own = row.line_bytes - RECORD_TRAILER_BYTES;
      if (into !== null && seqs.length > 0 && bytes + own > budget) {
        break;
      }
      seqs.push(row.seq);
      if (row.content_id !== null) {
        contents.push(row.content_id);
      }
      bytes += own;
      if (into === null && bytes >= budget) {
        break;
      }
    }
    return seqs.length === 0 ? null : { seqs, contents, into };
  }

  /**
   * The batch's rows as the object holds them, in seq order, a group at a time (never the
   * batch held at once, review N5): each message's row, its body's and its recipients', as
   * `each` takes them; false when one is no longer hot and acked.
   */
  #images(seqs: readonly number[],
          each: (message: StoredMessage, body: Record<string, SqlStorageValue>,
                 recipients: Record<string, SqlStorageValue>[]) => void): boolean {
    for (let at = 0; at < seqs.length; at += IMAGE_GROUP) {
      const group = seqs.slice(at, at + IMAGE_GROUP);
      const messages = this.#sql.exec<StoredMessage>(
        "SELECT * FROM ledger_messages WHERE seq IN (SELECT value FROM json_each(?)) " +
          "AND unacked = 0 ORDER BY seq", JSON.stringify(group)).toArray();
      if (messages.length !== group.length) {
        return false;
      }
      for (const message of messages) {
        each(message, this.#sql.exec<Record<string, SqlStorageValue>>(
          "SELECT * FROM message_bodies WHERE message_id = ?", message.message_id).one(),
             this.#sql.exec<Record<string, SqlStorageValue>>(
               "SELECT * FROM ledger_recipients WHERE message_id = ? ORDER BY agent_id",
               message.message_id).toArray());
      }
    }
    return true;
  }

  /** The digest of the batch's rows as held (`ImageDigest`), or null when one is gone. */
  #imageDigest(seqs: readonly number[]): string | null {
    const images = new ImageDigest();
    return this.#images(seqs, (message, body, recipients) =>
      images.add({ message, body, recipients })) ? images.digest() : null;
  }

  /**
   * A batch planned into a move's table (review N5: from its rows' lengths, never their
   * bytes held; review N6: packed, never an object a record): each message's record, its
   * length, room, time, and its placed body's content (`place` names it in the table, with
   * the hash it must have as the move reads it) or its inline body, checked here; the digest
   * of its rows as held; null when it changed since it was picked. A read.
   */
  archiveBatch(seqs: readonly number[], table: MoveTable,
               place: (contentId: string, bytes: number, sha256: string) => number):
      { digest: string } | null {
    const images = new ImageDigest();
    let count = 0;
    const held = this.#images(seqs, (message, body, recipients) => {
      images.add({ message, body, recipients });
      const parts = mailParts(message, body, recipients);
      const placed = body["content_id"] as string | null;
      if (parts.inline !== null
          && createHash("sha256").update(parts.inline).digest("hex") !== message.body_sha256) {
        throw new Error("an archived body is not the body its message holds");
      }
      table.push(message.seq, message.chain_root, message.room_id, message.created_at,
                 parts.head.byteLength + message.body_bytes + parts.tail.byteLength,
                 placed === null ? MoveTable.INLINE : MoveTable.PLACED,
                 placed === null ? -1 : place(placed, message.body_bytes, message.body_sha256),
                 0);
      count++;
    });
    return !held || count === 0 ? null : { digest: images.digest() };
  }

  /**
   * Hot messages' parts (`MailParts`) by seq, read again for the chunk a move is making;
   * one missing (no longer hot and acked) is `archive_changed`. A read.
   */
  archiveParts(seqs: readonly number[]): Map<number, MailParts> {
    const parts = new Map<number, MailParts>();
    if (!this.#images(seqs, (message, body, recipients) =>
      parts.set(message.seq, mailParts(message, body, recipients)))) {
      throw new Refused("archive_changed: a message moved went from the hot set mid-move");
    }
    return parts;
  }

  /**
   * In a move's second unit, its pieces' contents adopted: the segments it `replaced` (a
   * merge's or a rewrite's), each still held as read, gone; the batch (`seqs`), unchanged
   * since it was read (its digest), gone from the hot set; and each piece's fence named, the
   * schema holding no two to overlap. The purges' cursors move past what it replaced, and a
   * blocked archive is unblocked. Refused `archive_changed` when anything changed, so the
   * unit rolls back whole and the next pass picks again.
   */
  archiveCommit(seqs: readonly number[], expected: string, replaced: readonly Fence[],
                pieces: readonly { content_id: string;
                                   fence: Omit<Fence, "segment" | "content_id"> }[],
                now: number): void {
    for (const old of replaced) {
      if (this.#sql.exec("DELETE FROM archive_segments WHERE segment = ? AND content_id = ? " +
                           "RETURNING segment", old.segment, old.content_id)
        .toArray().length === 0) {
        throw new Refused("archive_changed: a replaced segment went between the units");
      }
    }
    if (seqs.length > 0) {
      if (this.#imageDigest(seqs) !== expected) {
        throw new Refused("archive_changed: the batch changed between the move's units");
      }
      const list = JSON.stringify(seqs);
      const inList = "IN (SELECT message_id FROM ledger_messages WHERE seq IN " +
        "(SELECT value FROM json_each(?)))";
      // Recipients before their messages: their triggers read the message's size.
      this.#sql.exec(`DELETE FROM ledger_recipients WHERE message_id ${inList}`, list);
      this.#sql.exec(`DELETE FROM message_bodies WHERE message_id ${inList}`, list);
      this.#sql.exec(`DELETE FROM ledger_messages WHERE message_id ${inList}`, list);
    }
    for (const piece of pieces) {
      this.#fence(piece.content_id, piece.fence, now);
    }
    if (replaced.length > 0) {
      const last = Math.max(...replaced.map((old) => old.segment));
      this.#sql.exec("UPDATE archive_purges SET next_segment = ? WHERE next_segment < ?",
                     last, last);
    }
    this.#sql.exec("UPDATE ledger_usage SET archive_blocked = 0");
  }

  /** A segment's fence row, its content named (counted as a placed body's). In a unit. */
  #fence(contentId: string, fence: Omit<Fence, "segment" | "content_id">, now: number): number {
    return this.#sql.exec<{ segment: number }>(
      "INSERT INTO archive_segments (stream, first_key, last_key, first_at, last_at, records, " +
        "bytes, content_id, filer, trailer_offset, trailer_bytes, members, made_at) VALUES " +
        "('mail', ?, ?, ?, ?, ?, ?, ?, 'owner', ?, ?, ?, ?) RETURNING segment",
      fence.first_key, fence.last_key, fence.first_at, fence.last_at, fence.records,
      fence.bytes, contentId, fence.trailer_offset, fence.trailer_bytes, fence.members, now)
      .one().segment;
  }

  /** A segment's neighbour by key: the one just past it, else the one just before it. */
  fenceBeside(fence: Fence): Fence | null {
    return this.#sql.exec<Fence>(
      "SELECT * FROM archive_segments WHERE stream = 'mail' AND first_key > ? " +
        "ORDER BY first_key LIMIT 1", fence.last_key).toArray()[0]
      ?? this.#sql.exec<Fence>(
        "SELECT * FROM archive_segments WHERE stream = 'mail' AND last_key < ? " +
          "ORDER BY last_key DESC LIMIT 1", fence.first_key).toArray()[0] ?? null;
  }

  /** Whether the archive's last move was refused for the storage figure. A read. */
  archiveBlocked(): boolean {
    return this.#sql.exec<{ blocked: number }>(
      "SELECT archive_blocked AS blocked FROM ledger_usage").one().blocked === 1;
  }

  /** In a unit: the archive's move refused for the storage figure, so posts say so. */
  blockArchive(): void {
    this.#sql.exec("UPDATE ledger_usage SET archive_blocked = 1");
  }

  /**
   * In a unit, as an `archive` pass begins: the passes before it that never ended well and
   * the last one's error (null when it stopped before it could say), then this one counted
   * among them until it ends (review N3: kept in storage, so a pass killed with its isolate
   * still counts).
   */
  archiveBegin(): { fails: number; error: string | null } {
    const was = this.#sql.exec<{ fails: number; error: string | null }>(
      "SELECT archive_fails AS fails, archive_error AS error FROM ledger_usage").one();
    this.#sql.exec("UPDATE ledger_usage SET archive_fails = archive_fails + 1, " +
                     "archive_error = NULL");
    return was;
  }

  /** In a unit: an `archive` pass ended well (or for a cause it handles), so none failed. */
  archiveEnded(): void {
    this.#sql.exec("UPDATE ledger_usage SET archive_fails = 0, archive_error = NULL");
  }

  /** In a unit: an `archive` pass failed, as `error` says; it stays counted. */
  archiveFailed(error: string): void {
    this.#sql.exec("UPDATE ledger_usage SET archive_error = ?", error);
  }

  /** The segment whose key range holds `seq`, if any: segments never overlap, so one. */
  archiveFences(seq: number): Fence[] {
    return this.#sql.exec<Fence>(
      "SELECT * FROM archive_segments WHERE stream = 'mail' AND last_key >= ? AND " +
        "first_key <= ? ORDER BY last_key LIMIT 1", seq, seq).toArray();
  }

  /** A segment's fence row, if it is still held. A read. */
  fenceOf(segment: number): Fence | null {
    return this.#sql.exec<Fence>("SELECT * FROM archive_segments WHERE segment = ?", segment)
      .toArray()[0] ?? null;
  }

  /**
   * The next segment a deleted agent's mail may be in, for the lane to rewrite, the purges'
   * cursors moved past every segment whose bloom names none of them; null when none is left,
   * each purge then done. In a unit.
   */
  purgeNext(): { fence: Fence } | null {
    const sql = this.#sql;
    for (let batch = 0; batch < 64; batch++) {
      const purges = sql.exec<{ agent_id: string; next_segment: number }>(
        "SELECT agent_id, next_segment FROM archive_purges ORDER BY next_segment").toArray();
      if (purges.length === 0) {
        return null;
      }
      const from = purges[0]!.next_segment;
      const fences = sql.exec<Fence>(
        "SELECT * FROM archive_segments WHERE segment > ? ORDER BY segment LIMIT 256", from)
        .toArray();
      for (const fence of fences) {
        const bloom = fenceBloom(fence);
        if (purges.some((purge) => purge.next_segment < fence.segment
                        && bloomHas(bloom, purge.agent_id))) {
          sql.exec("UPDATE archive_purges SET next_segment = ? WHERE next_segment < ?",
                   fence.segment - 1, fence.segment - 1);
          return { fence };
        }
      }
      if (fences.length === 0) {
        sql.exec("DELETE FROM archive_purges WHERE next_segment >= ?", from);
        continue;
      }
      sql.exec("UPDATE archive_purges SET next_segment = ? WHERE next_segment < ?",
               fences.at(-1)!.segment, fences.at(-1)!.segment);
    }
    return null;
  }

  /** In a unit: past a segment whose bloom named a purged agent but held none of its mail. */
  purgeSkip(segment: number): void {
    this.#sql.exec("UPDATE archive_purges SET next_segment = ? WHERE next_segment < ?",
                   segment, segment);
  }

  /** A move begun (its first unit): its upload and its batch's digest, until it ends. */
  moveBegun(stream: "mail" | "rewrite", intentId: string, expected: string, now: number,
            storedBefore: number): void {
    this.#sql.exec("INSERT INTO archive_moves (stream, intent_id, digest, started_at, " +
                     "stored_before) VALUES (?, ?, ?, ?, ?) ON CONFLICT (stream) DO UPDATE SET " +
                     "intent_id = excluded.intent_id, digest = excluded.digest, started_at = " +
                     "excluded.started_at, stored_before = excluded.stored_before", stream,
                   intentId, expected, now, storedBefore);
  }

  /** A move ended, adopted or given up: its row gone (only its own, when `intentId`). */
  moveEnded(stream: "mail" | "rewrite", intentId: string | null = null): void {
    this.#sql.exec("DELETE FROM archive_moves WHERE stream = ? AND (? IS NULL OR intent_id = ?)",
                   stream, intentId, intentId);
  }

  /** The members bloom of a segment's records' rooms, as its fence row keeps it (hex). */
  static members(members: Iterable<string>): string {
    return Buffer.from(bloomOf(members)).toString("hex");
  }

  // -- MAIL TO THE OWNER'S OWN AGENTS: what the `runs` lane delivers -------------

  /**
   * Whether any mail waits for an agent's run: queued, or held while `room` (the inputs'
   * queue has room for any mail). A read of one index.
   */
  mailDue(room: boolean): boolean {
    return this.#sql.exec(
      `SELECT 1 FROM ledger_recipients WHERE ${DUE} AND (state = 'queued' OR ?) LIMIT 1`,
      room ? 1 : 0).toArray().length > 0;
  }

  /**
   * The oldest mail no run has taken, its body checked against its hash. A read. A body
   * placed in R2 (ADR-035) is the door's to read: handed in `placed` by content id, or
   * named (`content_id`) with no body, for the door to read and ask again.
   */
  nextMail(placed: ReadonlyMap<string, string> = new Map()): Delivery | null {
    // Driven from the agents (a `CROSS JOIN` keeps that order): each agent's queued rows
    // by `recipients_by_state`, an index seek an agent; only those rows are sorted.
    const row = this.#sql.exec<StoredMessage & { recipient: string; agent: string;
                                                 sender_name: string | null }>(
      "SELECT m.*, r.agent_id AS recipient, n.agent, coalesce(s.agent, o.display_name) " +
        "AS sender_name FROM native_agents n " +
        "CROSS JOIN ledger_recipients r ON r.agent_id = n.agent_id AND r.state = 'queued' " +
        "JOIN ledger_messages m ON m.message_id = r.message_id " +
        "LEFT JOIN native_agents s ON s.agent_id = m.sender_id " +
        "LEFT JOIN outside_agents o ON o.agent_id = m.sender_id " +
        "ORDER BY r.seq LIMIT 1").toArray()[0];
    if (row === undefined) {
      return null;
    }
    return this.#delivery(row, placed);
  }

  /**
   * A held mail to the owner's agent `agentId`, as `nextMail` reads it: a waiting run's
   * input, whose row keeps only the mail's reference (the run's `mail`) and reads the body
   * again as the run starts, a placed one (ADR-035) from R2 as `nextMail`'s. Null once the
   * mail is no longer held for it. A read.
   */
  mailFor(messageId: string, agentId: string,
          placed: ReadonlyMap<string, string> = new Map()): Delivery | null {
    const row = this.#sql.exec<StoredMessage & { recipient: string; agent: string;
                                                 sender_name: string | null }>(
      "SELECT m.*, r.agent_id AS recipient, n.agent, coalesce(s.agent, o.display_name) " +
        "AS sender_name FROM ledger_recipients r " +
        "JOIN native_agents n ON n.agent_id = r.agent_id " +
        "JOIN ledger_messages m ON m.message_id = r.message_id " +
        "LEFT JOIN native_agents s ON s.agent_id = m.sender_id " +
        "LEFT JOIN outside_agents o ON o.agent_id = m.sender_id " +
        "WHERE r.message_id = ? AND r.agent_id = ? AND r.unacked = 1",
      messageId, agentId).toArray()[0];
    return row === undefined ? null : this.#delivery(row, placed);
  }

  /** A mail row as its run reads it, its body checked against its hash. */
  #delivery(row: StoredMessage & { recipient: string; agent: string;
                                   sender_name: string | null },
            placed: ReadonlyMap<string, string>): Delivery {
    const stored = this.#sql.exec<{ body: string; content_id: string | null }>(
      "SELECT body, content_id FROM message_bodies WHERE message_id = ?", row.message_id)
      .toArray()[0];
    const kept = stored === undefined ? undefined
      : stored.content_id === null ? stored.body : placed.get(stored.content_id);
    const unread = stored?.content_id != null && kept === undefined ? stored.content_id : null;
    const [body, integrity]: [string | null, Integrity] = kept === undefined
      ? [null, "missing"]
      : digest(kept) !== row.body_sha256 ? [null, "tampered"] : [kept, "ok"];
    return { content_id: unread, message_id: row.message_id, agent: row.agent,
             agent_id: row.recipient, room_id: row.room_id, thread: row.chain_root,
             sender_id: row.sender_id, sender_kind: row.sender_kind,
             sender_name: row.sender_kind === OWNER ? OWNER : row.sender_name ?? "", body,
             integrity, authority: authorityOf(row.authority, row.command_center_id),
             attended: row.attended === 1 };
  }

  /**
   * In the run's admission unit: the mail taken by its agent's run, read. Its recipient row
   * still holds it, so it is never evicted before the run answers it (`settled`).
   */
  taken(messageId: string, agentId: string, now: number = clock()): void {
    const sql = this.#sql;
    if (sql.exec("UPDATE ledger_recipients SET state = 'read', changed_at = ? " +
                   `WHERE message_id = ? AND agent_id = ? AND ${DUE} RETURNING agent_id`, now,
                 messageId, agentId).toArray().length === 1) {
      this.#receipt(agentId, "read", messageId, now);
    }
  }

  /**
   * In a run's settle unit, however it settles: the mail it took no longer held by its
   * agent's recipient row.
   */
  settled(messageId: string, agent: string): void {
    this.#sql.exec("UPDATE ledger_recipients SET unacked = 0 WHERE message_id = ? AND " +
                     "agent_id = ? AND unacked = 1", messageId, nativeId(agent));
  }

  /** In a unit: mail no run may take (its refusal's code), failed and no longer held. */
  undeliverable(messageId: string, agentId: string, code: string, now: number = clock()): void {
    const sql = this.#sql;
    if (sql.exec("UPDATE ledger_recipients SET state = 'failed', unacked = 0, changed_at = ? " +
                   `WHERE message_id = ? AND agent_id = ? AND ${DUE} RETURNING agent_id`, now,
                 messageId, agentId).toArray().length === 1) {
      this.#receipt(agentId, "failed", messageId, now, { reason: code });
    }
  }

  /** In a unit: mail whose run has to wait for the inputs' queue, held until it has room. */
  hold(messageId: string, agentId: string, now: number = clock()): void {
    this.#sql.exec("UPDATE ledger_recipients SET state = 'held_cap', changed_at = ? WHERE " +
                     `message_id = ? AND agent_id = ? AND ${DUE}`, now, messageId, agentId);
  }

  /** In a unit, once the inputs' queue has room: every held mail queued again. */
  release(now: number = clock()): void {
    this.#sql.exec("UPDATE ledger_recipients SET state = 'queued', changed_at = ? " +
                     `WHERE ${DUE} AND state = 'held_cap'`, now);
  }

  /** A reader's mailbox counter: its unacked messages. */
  #mailbox(reader: string): { unacked: number } {
    return this.#sql.exec<{ unacked: number }>(
      "SELECT unacked FROM ledger_readers WHERE reader = ?", reader,
    ).toArray()[0] ?? { unacked: 0 };
  }

  /**
   * Refuse one more message to `recipient` past its mailbox's cap, or an agent's mail to the
   * owner past its `share` of the owner's mailbox (see {@link LedgerLimits}). Bytes are the
   * tier's storage's to bound: a body past `PLACE_BYTES` is in R2.
   */
  /** The readers holding the most unread mail, at most three, as the owner knows them. */
  #holders(): string {
    return this.#sql.exec<{ reader: string; unacked: number; name: string | null }>(
      "SELECT r.reader, r.unacked, coalesce(o.display_name, n.agent) AS name FROM " +
        "ledger_readers r LEFT JOIN outside_agents o ON o.agent_id = r.reader LEFT JOIN " +
        "native_agents n ON n.agent_id = r.reader WHERE r.unacked > 0 " +
        "ORDER BY r.unacked DESC, r.reader LIMIT 3").toArray()
      .map((row) => `${row.reader === OWNER ? "you" : row.name ?? row.reader} (${row.unacked})`)
      .join(", ");
  }

  /**
   * Room for one more message, never by a delete: too much unread mail owner-wide is
   * `ledger_full`, and the hot ceiling, with acked mail the `archive` lane has to move,
   * `storage_busy` until a move lands (the unread ceiling keeps a segment's records acked).
   */
  #roomForOne(): void {
    const usage = this.#usage();
    const limits = this.#limits;
    if (usage.unacked + 1 > limits.maxUnacked) {
      const most = limits.maxUnacked;
      throw new Refused(`${LEDGER_FULL}: too much mail is unread (${most} message${
        most === 1 ? " waits" : "s wait"} for their readers; the most for ${this.#holders()}); ` +
                        "reading and acking mail makes room");
    }
    if (usage.messages + 1 > limits.maxMessages) {
      throw new Refused(`${STORAGE_BUSY}: your storage is catching up, moving older mail ` +
                        `out; try again in ${STORAGE_BUSY_RETRY_S} seconds. ` +
                        JSON.stringify({ retry_after_s: STORAGE_BUSY_RETRY_S }));
    }
  }

  /**
   * The bytes of the owner's mail, as the one number counts them (D2): each hot message's
   * lines past its body, its body inline or placed (once a content), and every archive
   * segment's content. A read.
   */
  storedBytes(): number {
    return this.#sql.exec<{ bytes: number }>(
      "SELECT coalesce((SELECT SUM(body_bytes) FROM message_bodies WHERE content_id IS NULL), " +
        "0) + coalesce((SELECT SUM(meta_bytes) FROM message_bodies), 0) + " +
        "coalesce((SELECT SUM(size) FROM file_contents WHERE content_id IN (SELECT " +
        "content_id FROM message_bodies WHERE content_id IS NOT NULL UNION SELECT content_id " +
        "FROM archive_segments)), 0) AS bytes").one().bytes;
  }

  // -- MAILBOX_RECEIPT ------------------------------------------------------

  /**
   * Messages to this reader past its cursor; a reread returns the same message_ids.
   *
   * `maxBytes` bounds the page: whole messages (each measured as returned, plus
   * `padBytes` for the caller's own wrapping) while they fit, and at least one; a body is
   * never cut. Past its first message a page holds at most `READ_PAGE_BYTES` of bodies. A
   * body
   * placed in R2 (ADR-035) is the door's to read first (`placedNext`), handed in `placed`
   * by content id; the page ends before a placed body it was not handed, even its first
   * (an empty page, `more`, which the door reads again). Only a returned
   * message is marked read and receipted, so a held-back one stays unread until a read
   * returns it.
   */
  read(origin: Actor, limit: unknown, maxBytes: unknown = null, padBytes: unknown = 0,
       now: number = clock(), placed: ReadonlyMap<string, string> = new Map()): MailPage {
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
      const stored = new Map(sql.exec<{ message_id: string; body: string;
                                        content_id: string | null }>(
        "SELECT message_id, body, content_id FROM message_bodies WHERE message_id IN " +
          "(SELECT value FROM json_each(?))", ids,
      ).toArray().map((b) => [b.message_id, b]));
      // A reply's own receipt already holds its time to reply; show it, never recompute.
      const replied = new Map(sql.exec<{ ref: string; detail_json: string }>(
        "SELECT ref, detail_json FROM ledger_receipts WHERE kind = 'reply' AND ref IN " +
          "(SELECT value FROM json_each(?))", ids,
      ).toArray().map((r) => [r.ref, JSON.parse(r.detail_json).reply_s as number]));
      const messages: Mail[] = [];
      let used = 0;
      let bodyBytes = 0;
      let cut = false;
      for (const row of rows) {
        const kept = stored.get(row.message_id);
        const text = kept === undefined ? undefined
          : kept.content_id === null ? kept.body : placed.get(kept.content_id);
        bodyBytes += row.body_bytes;
        // A placed body the door did not read first ends the page there: it reads again.
        if ((messages.length && bodyBytes > READ_PAGE_BYTES)
            || (text === undefined && kept !== undefined)) {
          cut = true;
          break;
        }
        const [body, integrity]: [string | null, Integrity] = text === undefined
          ? [null, "missing"]
          : digest(text) !== row.body_sha256 ? [null, "tampered"] : [text, "ok"];
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
      return { reader, cursor, messages, more: cut || messages.length < rows.length };
    });
  }

  /**
   * The placed bodies (ADR-035) the next read of `limit` would return, by content id, in
   * order, while their bytes fit a page: a read, for the door to fetch them from R2 before
   * its read's unit (`Bodies.locate` in the same turn).
   */
  placedNext(origin: Actor, limit: unknown): string[] {
    const maxRead = this.#limits.maxRead;
    if (!count(limit, 1, maxRead)) {
      throw new Invalid(`limit is 1 to ${maxRead}`);
    }
    const reader = this.#reader(origin);
    const rows = this.#sql.exec<{ body_bytes: number; content_id: string | null }>(
      "SELECT b.body_bytes, b.content_id FROM ledger_recipients r JOIN message_bodies b " +
        "ON b.message_id = r.message_id WHERE r.agent_id = ? AND r.seq > ? " +
        "ORDER BY r.seq LIMIT ?", reader, this.#cursor(reader), limit,
    ).toArray();
    const wanted: string[] = [];
    let bytes = 0;
    for (const row of rows) {
      bytes += row.body_bytes;
      if (bytes > READ_PAGE_BYTES && (wanted.length > 0 || bytes > row.body_bytes)) {
        break;
      }
      if (row.content_id !== null) {
        wanted.push(row.content_id);
      }
    }
    return wanted;
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

  ack(origin: Actor, through: unknown, now: number = clock()):
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
   * A client's one enabled agent in a command center whose wake kind its client subscribes
   * (MCP Events), or none.
   */
  subscriber(origin: Origin, commandCenter: string): Agent | undefined {
    const agent = this.#sql.exec<Agent>(
      "SELECT * FROM outside_agents WHERE command_center_id = ? AND outside_client = ? " +
        "AND outside_family = ? AND disabled_reason IS NULL", commandCenter,
      origin.client, origin.family,
    ).toArray()[0];
    return agent !== undefined && kindFor(agent.delivery_method)?.push?.callback === "subscribe"
      ? agent : undefined;
  }

  /**
   * Store one doorbell subscription (slice W): from an agent's own client (`origin`, MCP
   * Events, after the binder verified its callback), for its one enabled agent in the
   * command center it names, whose wake kind its client subscribes; or from the owner
   * (`origin` null), for the agent and the kind it pasted a token for, which must be the
   * agent's own kind and one whose record has the owner hand it over. Keyed by the id the
   * binder derived (never the caller's): a repeat refreshes it (the blob replaced, the
   * replaced one signing beside it for the record's rotation window, its lifetime
   * re-granted, its last answer forgotten); another caller's id is refused. At most
   * `SUBSCRIPTIONS_PER_AGENT` an agent (`subscription_limit`), its lapsed ones let go first.
   * The agent's id and the expiry granted.
   */
  subscribe(origin: Origin | null, request: Subscription, now: number = clock()):
      { agent_id: string; expires_at: number | null } {
    if (typeof request.subscription_id !== "string"
        || !SUBSCRIPTION_ID.test(request.subscription_id)) {
      throw new Invalid("invalid subscription id");
    }
    const expires = request.expires_at;
    if (expires !== null && (typeof expires !== "number" || !Number.isFinite(expires)
                             || expires <= now)) {
      throw new Invalid("expires_at is a time after now, or null");
    }
    const follow = follows({ board: request.board, rooms: request.rooms });
    if (typeof request.blob !== "string" || !request.blob || typeof request.kid !== "string"
        || !count(request.version, 1, Number.MAX_SAFE_INTEGER)) {
      throw new Invalid("invalid sealed subscription");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const agent = origin === null
        ? (typeof request.agent_id === "string" ? this.#agent(request.agent_id) : undefined)
        : this.subscriber(origin, request.command_center_id ?? "");
      const kind = agent === undefined ? null : kindFor(agent.delivery_method);
      const push = kind?.push ?? null;
      const mine = origin === null ? push?.callback === "owner" && request.kind === kind!.id
        : push?.callback === "subscribe";
      if (agent === undefined || agent.disabled_reason !== null || push === null || !mine) {
        throw new Refused(origin === null ? "not an agent whose doorbell the owner sets"
          : "not this caller's subscribing agent");
      }
      const principal = origin === null ? OWNER : origin.client;
      const held = sql.exec<CallbackRow>(
        "SELECT * FROM wake_callbacks WHERE subscription_id = ?", request.subscription_id,
      ).toArray()[0];
      if (held !== undefined && (held.principal !== principal || held.agent_id !== agent.agent_id)) {
        throw new Refused("not this caller's subscription");
      }
      // The agent's lapsed subscriptions are let go, so none holds a place it no longer uses.
      sql.exec("DELETE FROM wake_callbacks WHERE agent_id = ? AND expires_at IS NOT NULL " +
                 "AND expires_at <= ?", agent.agent_id, now);
      // The schema's trigger holds the same bound; this answers it as a refusal.
      if (held === undefined && sql.exec<{ n: number }>(
        "SELECT COUNT(*) AS n FROM wake_callbacks INDEXED BY wake_callbacks_by_agent " +
          "WHERE agent_id = ?", agent.agent_id).one().n >= SUBSCRIPTIONS_PER_AGENT) {
        throw new Refused(`subscription_limit: ${SUBSCRIPTIONS_PER_AGENT} doorbell ` +
                          "subscriptions an agent; end one first");
      }
      const grace = push.events?.rotation_grace_s ?? 0;
      const previous = held === undefined || grace === 0 ? [null, null, null]
        : [held.blob, held.version, now + grace];
      sql.exec(
        "INSERT INTO wake_callbacks (subscription_id, agent_id, principal, kind, board, rooms, " +
          "blob, kid, version, previous_blob, previous_version, previous_until, expires_at, " +
          "created_at, refreshed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
          "ON CONFLICT (subscription_id) DO UPDATE SET board = excluded.board, " +
          "rooms = excluded.rooms, blob = excluded.blob, kid = excluded.kid, " +
          "version = excluded.version, previous_blob = excluded.previous_blob, " +
          "previous_version = excluded.previous_version, " +
          "previous_until = excluded.previous_until, expires_at = excluded.expires_at, " +
          "refreshed_at = excluded.refreshed_at, status = NULL",
        request.subscription_id, agent.agent_id, principal, kind!.id, follow.board ? 1 : 0,
        JSON.stringify(follow.rooms), request.blob, request.kid, request.version,
        ...previous, expires, now, now,
      );
      return { agent_id: agent.agent_id, expires_at: expires };
    });
  }

  /**
   * The caller that holds a subscription ends it (MCP Events `events/unsubscribe`, keyed by
   * the id the binder derived from its name, arguments and URL). Repeating it is harmless.
   */
  unsubscribe(origin: Origin | null, subscriptionId: unknown): boolean {
    if (typeof subscriptionId !== "string" || !SUBSCRIPTION_ID.test(subscriptionId)) {
      throw new Invalid("invalid subscription id");
    }
    return this.#storage.transactionSync(() => this.#sql.exec(
      "DELETE FROM wake_callbacks WHERE subscription_id = ? AND principal = ? " +
        "RETURNING subscription_id", subscriptionId, origin === null ? OWNER : origin.client,
    ).toArray().length > 0);
  }

  /**
   * The agent's subscriptions the doorbell may send to now: its current kind's, unexpired,
   * while the agent is enabled. A read, so the doorbell rechecks them right before it sends.
   */
  callbacks(agentId: string, now: number): LiveCallback[] {
    return this.#sql.exec<CallbackRow>(
      "SELECT c.*, a.delivery_method FROM wake_callbacks c INDEXED BY wake_callbacks_by_agent " +
        "JOIN outside_agents a ON a.agent_id = c.agent_id WHERE c.agent_id = ? " +
        "AND a.disabled_reason IS NULL AND (c.expires_at IS NULL OR c.expires_at > ?) " +
        "ORDER BY c.subscription_id", agentId, now,
    ).toArray().filter((row) => kindFor(row.delivery_method)?.id === row.kind).map((row) => ({
      subscription_id: row.subscription_id, agent_id: row.agent_id, principal: row.principal,
      kind: row.kind, board: row.board === 1, rooms: JSON.parse(row.rooms) as string[],
      blob: row.blob, kid: row.kid, version: row.version,
      previous: row.previous_blob !== null && row.previous_until !== null
          && row.previous_until > now
        ? { blob: row.previous_blob, version: row.previous_version! } : null,
      rung: row.rung, status: row.status,
    }));
  }

  /** These subscriptions are sent `wake` (`<wake_id>.<fold>`): marked before the byte. */
  rung(subscriptionIds: readonly string[], wake: string): void {
    for (const id of subscriptionIds) {
      this.#sql.exec("UPDATE wake_callbacks SET rung = ? WHERE subscription_id = ?", wake, id);
    }
  }

  /**
   * A subscription's answer to `wake`, recorded as its own: a 429 leaves it owed the wake
   * again (its mark cleared), anything else settles it for that wake.
   */
  answered(subscriptionId: string, wake: string, status: number): void {
    this.#sql.exec(
      "UPDATE wake_callbacks SET status = ?, rung = CASE WHEN ? = 429 AND rung = ? THEN NULL " +
        "ELSE rung END WHERE subscription_id = ?", status, status, wake, subscriptionId,
    );
  }

  /** A subscription's blob resealed under the gateway's active kid (`ringDoorbell`). */
  resealed(subscriptionId: string, blob: string, kid: string, version: number): void {
    this.#sql.exec("UPDATE wake_callbacks SET blob = ?, kid = ?, version = ? " +
                     "WHERE subscription_id = ? AND version < ?",
                   blob, kid, version, subscriptionId, version);
  }

  /**
   * The agents a board or room write rings: each enabled ringing agent with a live
   * subscription that follows `collection` (`board`, or the room named), with its client
   * and command center, for the object to hold to its grant.
   */
  followers(collection: string, board: boolean, now: number):
      { agent_id: string; client: string; command_center_id: string }[] {
    return this.#sql.exec<{ agent_id: string; client: string; command_center_id: string }>(
      "SELECT DISTINCT a.agent_id, a.outside_client AS client, a.command_center_id " +
        "FROM wake_callbacks c JOIN outside_agents a ON a.agent_id = c.agent_id " +
        "WHERE a.disabled_reason IS NULL AND (c.expires_at IS NULL OR c.expires_at > ?) AND " +
        (board ? "c.board = 1" : "EXISTS (SELECT 1 FROM json_each(c.rooms) WHERE value = ?)") +
        " ORDER BY a.agent_id", ...(board ? [now] : [now, collection]),
    ).toArray().filter((agent) => rings(this.#agent(agent.agent_id)!));
  }

  /**
   * A board or room row changed (`after` the clock's cursor before it): each agent named
   * (the object's `followers`, held to their grants) has it in its open wake, opened if
   * none is.
   */
  changed(change: Exclude<Change, "mail">, after: number, agentIds: readonly string[],
          now: number = clock()): void {
    this.#storage.transactionSync(() => this.#open(agentIds, CHANGE_BIT[change], after, now));
  }

  /**
   * Fold `bit` and `after` into each agent's open wake, or open one that waits out the
   * agent's batch window (W3), so a burst rings once.
   */
  #open(agentIds: readonly string[], bit: number, after: number, now: number): void {
    const sql = this.#sql;
    for (const agentId of agentIds) {
      if (sql.exec(
        "UPDATE ledger_wakes SET changes = changes | ?, after = MIN(after, ?), " +
          `fold = fold + 1 WHERE agent_id = ? AND ${OPEN} RETURNING wake_id`, bit, after, agentId,
      ).toArray().length) {
        continue;
      }
      sql.exec(
        "INSERT INTO ledger_wakes (wake_id, agent_id, state, due, changes, after, created_at) " +
          "SELECT ?, agent_id, 'pending', ? + batch_window_s, ?, ?, ? FROM outside_agents " +
          "WHERE agent_id = ?",
        "wake_" + token(16), now, bit, after, now, agentId,
      );
    }
  }

  /**
   * A changed or revoked grant (P0-3's M3): every agent of that outside client has its
   * open wakes suppressed and its client's own subscriptions erased, so it subscribes again
   * under the new grant. The owner's pasted tokens stay: the client did not hand them over,
   * and each send still rechecks the client's grant. Its ids; an index range.
   */
  suppressClient(client: string, now: number): string[] {
    return this.#storage.transactionSync(() => {
      const agents = this.#sql.exec<Agent>(
        "SELECT * FROM outside_agents WHERE outside_client = ?", client,
      ).toArray();
      for (const agent of agents) {
        this.#suppress(agent.agent_id, "grant", now);
        this.#erase(agent.agent_id, false);
      }
      return agents.map((agent) => agent.agent_id);
    });
  }

  // -- the schedule: wakes (S3b, W3) --------------------------------------------

  #suppress(agentId: string, reason: string, now: number): void {
    this.#sql.exec(
      "UPDATE ledger_wakes SET state = 'suppressed', receipt_json = ?, settled_at = ? " +
        `WHERE agent_id = ? AND ${OPEN}`, canonical({ reason }), now, agentId,
    );
    this.#prune(now);
  }

  /**
   * Erase the agent's subscriptions in the caller's transaction: every one, or (`owners`
   * false) its client's own, leaving the owner's pasted tokens.
   */
  #erase(agentId: string, owners: boolean): void {
    this.#sql.exec("DELETE FROM wake_callbacks WHERE agent_id = ?" +
                     (owners ? "" : " AND principal != 'owner'"), agentId);
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
   * Open wakes that are due, oldest first: what the `wakes` lane's claim pass takes.
   * The object is its owner, so there is no cross-owner fairness pass.
   */
  dueOpen(now: number, limit: number): Wake[] {
    return this.#sql.exec<Wake>(
      "SELECT wake_id, agent_id, state, due, claim_id FROM ledger_wakes " +
        `WHERE ${OPEN} AND due <= ? ORDER BY due LIMIT ?`, now, limit,
    ).toArray();
  }

  /** Claims whose lease has lapsed, oldest first: what the lane's recover pass takes. */
  lapsedClaims(now: number, limit: number): Wake[] {
    return this.#sql.exec<Wake>(
      "SELECT wake_id, agent_id, state, due, claim_id FROM ledger_wakes " +
        "WHERE state = 'sending' AND due <= ? ORDER BY due LIMIT ?", now, limit,
    ).toArray();
  }

  /**
   * Before any byte: the claimed agent's IDs, mailbox cursor and changes.
   *
   * A wake whose agent is gone, disabled or no longer rings, or whose client's grant no
   * longer stands, is suppressed instead. A wake past the owner's hourly cap for its agent
   * waits until the cap allows it (GCRA with no burst: one ring each `3600 / cap` seconds,
   * so never more than `cap` in any hour), its queued mail held at `held_cap` (W3); with no
   * cap, none counts.
   * The claim is unmarked: the in-process sender calls `mark()` before its first byte.
   * Nothing is claimed while the owner is not admitted (`require_work`, read in the claim's
   * own unit): the row waits, `held`, for the owner's readmission.
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
      if (!this.#admitted()) {
        return { outcome: "held" };
      }
      const agent = this.#agent(wake.agent_id);
      const live = agent !== undefined && rings(agent);
      if (!live || !this.#grants.live(agent.outside_client)) {
        this.#suppress(wake.agent_id, live ? "grant" : "agent", now);
        return { outcome: "suppressed", agent_id: wake.agent_id };
      }
      const cap = agent.max_wakes_per_hour;
      if (cap !== null) {
        const tat = agent.wake_tat ?? now;
        if (tat > now) {
          sql.exec("UPDATE ledger_wakes SET due = MAX(due, ?) WHERE wake_id = ?", tat, wakeId);
          sql.exec("UPDATE ledger_recipients SET state = 'held_cap', changed_at = ? " +
                     "WHERE agent_id = ? AND state = 'queued'", now, agent.agent_id);
          return { outcome: "capped", due: tat };
        }
        sql.exec("UPDATE outside_agents SET wake_tat = ? WHERE agent_id = ?", now + HOUR_S / cap,
                 agent.agent_id);
      }
      const claimId = "claim_" + token(16);
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
          changes: CHANGES.filter((change) => (wake.changes & CHANGE_BIT[change]) !== 0),
          after: wake.after, at: wake.created_at, fold: wake.fold,
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

  /** A claim abandoned past its lease: unknown if a byte may have left, else due again. */
  recover(wakeId: string, now: number): string | null {
    const wake = this.#sql.exec<StoredWake>(
      "SELECT * FROM ledger_wakes WHERE wake_id = ? AND state = 'sending' AND due <= ?",
      wakeId, now,
    ).toArray()[0];
    if (wake === undefined || wake.claim_id === null) {
      return null;
    }
    const state = wake.marked ? "unknown" : "pending";
    this.settle(wake.wake_id, wake.claim_id, { state, reason: "recovered" }, now);
    return state;
  }

  /**
   * A claimed wake's outcome and its receipt: its messages `woken` once accepted. A
   * rate-limited wake waits exactly its `Retry-After` (`retryAfter`).
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
      let state: string = answer.state;
      const kind = WAKE_RECEIPTS[state];
      let detail: Record<string, unknown> = { ...answer, through: wake.through_seq };
      delete detail["state"];
      let due = now;
      if (state === "rate_limited") {
        due = now + retryAfter(answer.state === "rate_limited" ? answer.retry_after : null,
                               wake.attempts);
        detail = { ...detail, retry_after: due - now };
      }
      // A wake opened for the agent meanwhile: fold into that one, which then waits out
      // any Retry-After.
      if ((state === "pending" || state === "rate_limited") && sql.exec(
        "UPDATE ledger_wakes SET due = MAX(due, ?), state = CASE WHEN ? = 'rate_limited' " +
          "THEN 'rate_limited' ELSE state END, changes = changes | ?, after = MIN(after, ?), " +
          "fold = fold + 1, created_at = MIN(created_at, ?) " +
          `WHERE agent_id = ? AND ${OPEN} RETURNING wake_id`,
        due, state, wake.changes, wake.after, wake.created_at, wake.agent_id,
      ).toArray().length) {
        state = "suppressed";
        detail = { ...detail, folded: true };
      }
      const settled = SETTLED.includes(state);
      // Its mail is woken once any subscription took the wake, a rate-limited one's too.
      const woke = state === "sent" || (answer.state === "rate_limited" && answer.accepted === true);
      sql.exec(
        "UPDATE ledger_wakes SET state = ?, due = ?, receipt_json = ?, settled_at = ? " +
          "WHERE wake_id = ?", state, due, canonical(detail), settled ? now : null, wakeId,
      );
      if (woke) {
        sql.exec(
          "UPDATE ledger_recipients SET state = 'woken', changed_at = ? WHERE agent_id = ? " +
            "AND seq <= ? AND state IN ('queued', 'held_cap')",
          now, wake.agent_id, wake.through_seq ?? 0,
        );
      }
      if (kind !== undefined) {
        this.#receipt(wake.agent_id, kind, wakeId, now, detail);
      }
      this.#prune(now);
      return true;
    });
  }

  /**
   * When the `wakes` lane next has work, never before `now`; null for none.
   *
   * A claim's lease always counts: its recovery needs no sender. Open rows count only
   * when `ringing` (the owner is admitted and the object has a doorbell); otherwise they
   * stay pending and give the lane no job, so no alarm fires for work it cannot do. Work
   * already due arms at `now`, which every pass shrinks.
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
