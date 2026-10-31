/**
 * Owner policy (N10), in the owner's own object (ADR-032): one document of action rules,
 * egress and spend, its generation, the owner's receipts, and the agents' held proposals.
 *
 * Ported from `PolicyStore` in `tinyassets/broker/policy.py` (origin/main 1fd430857):
 * `read`, `write` (with a held proposal's one apply), `receipts`, `receipt_proposal`,
 * `proposal`, `receipt_refusal` and `_validate`, whose rules it keeps. Only the owner,
 * in its own protected session (`sessions.ts`), writes policy; the object checks that
 * session itself before any write here runs.
 *
 * - The document is `{entries, egress, spend}`, validated whole on every write and every
 *   read; its digest is `sha256:` over its canonical JSON and is rechecked on read, so a
 *   row changed around this module fails loudly. Every owner policy change, a per-agent
 *   wake-limit change included (`changed`), bumps the generation, which a write must
 *   name (`expected_generation`), so a change made against a stale view is refused.
 * - A fresh object starts from the default document at generation 1, put there by its
 *   migration: the Python's bootstrap, legacy upgrade and per-owner failure isolation
 *   have no counterpart, since the object is the account.
 * - An agent's proposal is receipted, and with a card id held for the owner, who
 *   applies it at most once through a write that names it. A held card never changes;
 *   pending ones are never evicted, and a full pending set refuses more.
 * - A refused owner-session call leaves a `refusal` receipt with the digest of what was
 *   asked, never the session proof, written after the refused change rolled back.
 * - The document's optional `files` table sets the owner's file quotas and history
 *   retention (`FILE_DEFAULTS`, plan §6); what it leaves out keeps the defaults. It is
 *   ours, with no Python counterpart, so the default document leaves it out and keeps
 *   its digest.
 * - The optional `models` table is the owner's model budget (`MODEL_DEFAULTS`, gateway
 *   research §4): per-turn requests, tokens and cost, owner-wide and per-connection
 *   daily and monthly cost ceilings, and pi's retry count. `usage.ts` holds every model
 *   call to it, in the unit that reserves the call, and `agentPolicy` hands pi the part
 *   that is pi's. The Python's per-turn limits were code constants; here they are policy.
 * - The optional `runs` table is the owner's run policy (`RUN_DEFAULTS`, tasks 5 S5):
 *   how long a settled run's store is kept (`keep_runs`), and its receipt
 *   (`keep_run_receipts`), which the `threads` lane reads (`threads.ts`).
 *
 * The Python closed the owner's public egress tunnels before a write committed and
 * cancelled its live streams after. Here each model call carries the policy generation
 * its reservation read, in its envelope (`gateway-provider.ts`), and a call reserved
 * under an older one is never sent; owed by the agent (5), a policy write ends the
 * owner's in-flight turns and streams.
 *
 * Declared differences from the Python:
 * - An egress destination whose host the WHATWG URL parser reads as an IP address
 *   (`8.8.8.8`, `0x7f000001`, `0x7f.1`, `[::1]`) is refused until the gateway's address
 *   check lands (tasks 4); the Python accepted a public one. A host no URL can carry
 *   (a name ending in a number, a malformed `xn--` label) is invalid. Every other DNS
 *   name the Python accepted is accepted here, and what a name resolves to is the
 *   gateway's to check at each call.
 * - The document is bounded (`maxDocument`), and receipts are kept per bucket
 *   (`receiptsPerBucket`): owner changes, agent proposals and refusals each apart, so
 *   neither an agent nor a refused stranger can push the owner's changes out. The
 *   Python kept every receipt and listed two buckets.
 * - A held proposal applies only as a `policy_proposal` card, which the Python's apply
 *   path checked and its write did not.
 * - One outside client holds at most `pendingPerClient` of the pending proposals, so it
 *   cannot fill the set the owner's agents propose into; the Python had one set.
 * - An entry's values are flat (strings, numbers, booleans, null), as every value the
 *   Python wrote is; it took any JSON there.
 * - The digest is this object's own (numbers as JavaScript prints them), so the cutover
 *   recomputes it for each imported document.
 * - The decision itself (`decide`, spend reservations, once-approvals, standing grants,
 *   declared operation kinds) is the binding gate's at OPEN and comes with the gateway
 *   (tasks 3.7): its request digest and connection facts are the gateway's.
 *
 * Every change runs in one `transactionSync`, so a refusal leaves nothing behind.
 */
import { createHash } from "node:crypto";

import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import { CHUNK_BYTES, KEEP_CEILING, MAX_INTENT_CHUNKS } from "./files";
import { codePoints, SOURCE_ID } from "./text";
import type { RunPolicy } from "./threads";

export const BEHAVIOURS: readonly string[] =
  Object.freeze(["do", "do_if_preapproved", "ask_first", "hand_off"]);
export const DEFAULT_RULES: Readonly<Record<string, string>> = Object.freeze({
  "workspace.files": "do", "workspace.shell": "do", "workspace.workflows": "do",
  "shell.egress": "do", "app.read": "do", "app.write": "do",
  "people.message": "ask_first", "commons.publish": "ask_first", "share": "ask_first",
  "browser.action": "ask_first", "spend": "ask_first", "money.move": "hand_off",
  "security.change": "hand_off", "access.grant": "hand_off",
});
export const OPERATION_KINDS: Readonly<Record<string, string>> = Object.freeze({
  read: "app.read", write: "app.write", delete: "app.write", message: "people.message",
  payment: "money.move", security: "security.change", access: "access.grant",
});
const TABLES: readonly string[] =
  Object.freeze(["rules", "approval_grants", "operation_kinds", "review_on", "review_off"]);
const EGRESS_MODES: readonly string[] = Object.freeze(["open", "allowlist", "ask"]);
const CAPS: readonly string[] = Object.freeze(["daily", "monthly", "transaction", "ask_above"]);
/** `normalize_destination`'s DNS label: underscores allowed, hyphens interior. */
const LABEL = /^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/;
/** A host the URL parser turned into an IPv4 address. */
const IPV4 = /^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$/;

/** The default document as stored, and its digest (a test recomputes both). */
export const DEFAULT_POLICY_JSON =
  '{"egress":{"destinations":[],"mode":"open"},"entries":[],"spend":{"ask_above":null,' +
  '"daily":null,"monthly":null,"source_ceilings":{},"transaction":null}}';
export const DEFAULT_POLICY_DIGEST =
  "sha256:b8a7d8f3763ebeadaace95e5a634ffa3954f88b9a75061b061e21e65222d151c";

/**
 * How large policy state may grow. The document's 64 KiB is room for hundreds of rules
 * (a rule is under 200 bytes); a change is the Python's 16,000 characters. Proposals
 * keep the Python's 30 days and 200 pending, and the 200 most recently applied; one
 * outside client holds at most a quarter of the pending ones. Each
 * receipt bucket keeps its newest 5,000 rows (about a megabyte) and lists its last 50.
 */
export interface PolicyLimits {
  readonly maxDocument: number;
  readonly maxChange: number;
  readonly proposalTtlS: number;
  readonly pendingProposals: number;
  /** Pending proposals one outside client may hold. */
  readonly pendingPerClient: number;
  readonly appliedProposals: number;
  readonly receiptsPerBucket: number;
  readonly receiptsListed: number;
}

export const POLICY_LIMITS: PolicyLimits = Object.freeze({
  maxDocument: 65_536,
  maxChange: 16_000,
  proposalTtlS: 30 * 86_400,
  pendingProposals: 200,
  pendingPerClient: 50,
  appliedProposals: 200,
  receiptsPerBucket: 5_000,
  receiptsListed: 50,
});

/** A rule, review setting, grant or declared operation: its values are flat. */
export interface PolicyEntry {
  readonly scope: string;
  readonly table: string;
  readonly values: { readonly [key: string]: string | number | boolean | null };
}

/**
 * Owner files policy (plan §6): quotas and history retention. Each is an owner-editable
 * default: the document's optional `files` table sets any of them, and the rest keep
 * these values. Quotas are refused loudly at the bound and never make room by dropping
 * history. `max_bytes` counts unique chunk bytes, live and in history; `max_inline_bytes`
 * the small files kept in the object itself; `max_manifest_bytes` everything the manifest
 * takes in the object as SQLite stores it (`manifest.ts`): every entry, history row,
 * content, chunk list, chunk and temporary directory, each path and inline byte in them.
 * Its high bound, at the worst page fill SQLite leaves (`WORST_FILL`), holds the object's
 * worst case under half its 10 GB (the owner-bound guard). Every other bound the manifest
 * holds can bind inside it: a small file at one version takes about 1,070 bytes of it and
 * each further version about 850, so `max_entries` is at most what fits at one version,
 * `max_history` what fits as versions, and `max_inline_bytes` leaves a quarter of it to
 * the rows. Retention keeps each live path's last `keep_versions`, and its versions younger
 * than `retention_days` up to `keep_recent` in all; a deleted path keeps up to
 * `keep_recent` for `retention_days`. A version past those goes in the change that pushed
 * it out.
 *
 * Each outside client holds at most `client_share` percent of every quota (and of the
 * open uploads and the chunks they hold), and all of them together at most what
 * `owner_reserve` percent leaves, so no client fills the owner's files and the owner
 * always keeps room of its own, as the wait and thread caps keep the owner's. A client is
 * charged its own rows (the entries it last wrote, its history rows, its temporary
 * directories) and once for each content and chunk they or its open uploads reference,
 * whoever else references them too (`manifest.ts`).
 *
 * `max_history` is never below `max_entries × max(keep_versions, keep_recent)`, the most
 * rows the live paths can hold at once, so no churn of live files fills it. Deleted
 * paths' recent history, which retention has already let go, makes room: a change that
 * would pass `max_history` or `max_manifest_bytes`, or a client's share of either, first
 * prunes their earliest-due rows (files.ts `#fit`). The
 * owner may purge any path's history (never refused) at any time. A file is at most
 * `MAX_INTENT_CHUNKS` chunks (40 GiB), the most one upload intent holds. The sync agent's
 * exclude list is not here: it is the owner's file `/workspace/.ta/sync-ignore`, whose
 * defaults come with the sync agent (tasks 6).
 */
export const FILE_DEFAULTS = Object.freeze({
  max_bytes: 1024 ** 3,
  max_inline_bytes: 256 * 1024 ** 2,
  max_manifest_bytes: 512 * 1024 ** 2,
  max_entries: 30_000,
  max_file_bytes: 5 * 1024 ** 3,
  max_history: 600_000,
  retention_days: 30,
  keep_versions: 10,
  keep_recent: 20,
  client_share: 25,
  owner_reserve: 25,
});
export type FileSettings = { readonly [K in keyof typeof FILE_DEFAULTS]: number };
/** The range each setting may take. */
export const FILE_SETTINGS: { readonly [K in keyof FileSettings]: readonly [number, number] } =
  Object.freeze({
    max_bytes: Object.freeze([0, 1024 ** 4] as const),
    max_inline_bytes: Object.freeze([0, 384 * 1024 ** 2] as const),
    max_manifest_bytes: Object.freeze([0, 512 * 1024 ** 2] as const),
    max_entries: Object.freeze([0, 500_000] as const),
    max_file_bytes: Object.freeze([0, MAX_INTENT_CHUNKS * CHUNK_BYTES] as const),
    max_history: Object.freeze([0, 600_000] as const),
    retention_days: Object.freeze([0, 3650] as const),
    keep_versions: Object.freeze([1, KEEP_CEILING] as const),
    keep_recent: Object.freeze([1, KEEP_CEILING] as const),
    client_share: Object.freeze([0, 100] as const),
    owner_reserve: Object.freeze([0, 100] as const),
  });

/** The files settings a document leaves in force: its own, and the defaults for the rest. */
export function fileSettings(document: PolicyDocument): FileSettings {
  return { ...FILE_DEFAULTS, ...document.files };
}

/**
 * Owner run policy (tasks 5, S5): how long a settled run's store is kept (`keep_runs`,
 * whole days, at most 30), and how long its receipt is kept from when it settled
 * (`keep_run_receipts`, whole days, at least `keep_runs` and at most 365); and, as the
 * files quotas share theirs, the percent of the runs' live stores (`runs_kept`) and of
 * their receipts one outside client may hold (`client_share`), and the percent every
 * client together leaves the owner (`owner_reserve`), so the owner can always launch; and
 * how many runs may be live at once, not yet settled (`runs_live`, shared the same way),
 * since only a live run holds work. The document's optional `runs` table sets any of them;
 * the rest keep these values. A run's
 * receipt is how its status is answered once it settled (`threads.ts`); receipts past a
 * share let their oldest go, and never refuse a launch.
 */
export const RUN_DEFAULTS = Object.freeze({
  keep_runs: 7,
  keep_run_receipts: 90,
  client_share: 25,
  owner_reserve: 25,
  runs_live: 2_000,
});
export type RunSettings = { readonly [K in keyof typeof RUN_DEFAULTS]: number };
/** The range each run setting may take. */
export const RUN_SETTINGS: { readonly [K in keyof RunSettings]: readonly [number, number] } =
  Object.freeze({
    keep_runs: Object.freeze([1, 30] as const),
    keep_run_receipts: Object.freeze([1, 365] as const),
    client_share: Object.freeze([0, 100] as const),
    owner_reserve: Object.freeze([0, 100] as const),
    runs_live: Object.freeze([1, 10_000] as const),
  });
const DAY_S = 86_400;

/** The run settings a document leaves in force: its own, and the defaults for the rest. */
export function runSettings(document: PolicyDocument): RunSettings {
  return { ...RUN_DEFAULTS, ...document.runs };
}

/** The run policy a document leaves in force, its times in seconds, as the threads read it. */
export function runPolicy(document: PolicyDocument): RunPolicy {
  const settings = runSettings(document);
  return { keepRunsS: settings.keep_runs * DAY_S,
           keepReceiptsS: settings.keep_run_receipts * DAY_S,
           clientShare: settings.client_share, ownerReserve: settings.owner_reserve,
           runsLive: settings.runs_live };
}

/**
 * Owner model policy (gateway research §4): what one turn may spend, and what the owner's
 * sources may spend in a UTC day and month. A turn is the submitted input at the origin of
 * a call's causal chain, every subagent's, compaction's and background work's call it
 * caused included (`origin.ts`).
 * Money is in micro-dollars (µ$), as pi-ai's catalog prices are dollars per million
 * tokens: a token at a price of `p` costs `p` µ$. The Python's per-turn limits
 * (`TurnRequestBudget`: `max_tokens=1_000_000`, `max_cost_microunits=25_000_000`) are the
 * defaults' model, raised for tokens since a turn here is a whole agent loop, each request
 * re-sending its context. A request cap bounds a runaway loop. The daily and monthly
 * ceilings default to none, as the Python's do (`PolicyStore`'s default document:
 * `"daily": None, "monthly": None`); the owner sets them.
 */
export const MODEL_DEFAULTS = Object.freeze({
  turn_requests: 100,
  turn_tokens: 10_000_000,
  turn_cost: 25_000_000,
  daily_cost: null as number | null,
  monthly_cost: null as number | null,
});
/** The range each model setting may take. */
export const MODEL_SETTINGS = Object.freeze({
  turn_requests: Object.freeze([1, 10_000] as const),
  turn_tokens: Object.freeze([1, 1_000_000_000] as const),
  turn_cost: Object.freeze([0, 1_000_000_000_000] as const),
  daily_cost: Object.freeze([0, 1_000_000_000_000] as const),
  monthly_cost: Object.freeze([0, 1_000_000_000_000] as const),
  retries: Object.freeze([0, 10] as const),
});
/** The sources one `models.sources` table may name, each by its id (`SOURCE_ID`). */
const MODEL_SOURCES_MAX = 64;

/** A source's own ceilings. */
export interface SourceCeilings {
  readonly daily_cost?: number | null;
  readonly monthly_cost?: number | null;
}

/** The document's `models` table, as written. */
export interface ModelPolicy {
  readonly turn_requests?: number;
  readonly turn_tokens?: number;
  readonly turn_cost?: number;
  readonly daily_cost?: number | null;
  readonly monthly_cost?: number | null;
  readonly sources?: { readonly [source: string]: SourceCeilings };
  readonly retries?: number;
}

/** The model settings a document leaves in force. */
export interface ModelSettings {
  readonly turn_requests: number;
  readonly turn_tokens: number;
  readonly turn_cost: number;
  readonly daily_cost: number | null;
  readonly monthly_cost: number | null;
  readonly sources: { readonly [source: string]: SourceCeilings };
}

/** The model settings a document leaves in force: its own, and the defaults for the rest. */
export function modelSettings(document: PolicyDocument): ModelSettings {
  const { retries: _retries, sources = {}, ...caps } = document.models ?? {};
  return { ...MODEL_DEFAULTS, ...caps, sources };
}

/**
 * The part of owner policy that is pi's own settings (tasks 5: owner policy feeds pi's
 * settings), spread over the object's fixed ones: today the model retry count.
 */
export function agentPolicy(document: PolicyDocument): { readonly retry?: { readonly maxRetries: number } } {
  const retries = document.models?.retries;
  return retries === undefined ? {} : { retry: { maxRetries: retries } };
}

function bounded(value: unknown, [low, high]: readonly [number, number]): boolean {
  return whole(value) && value >= low && value <= high;
}

/** The `models` table: known keys only, each in its range; null only for a ceiling. */
function validModels(models: unknown): boolean {
  if (!plain(models)) {
    return false;
  }
  return Object.entries(models).every(([key, value]) => {
    if (key === "sources") {
      return plain(value) && Object.keys(value).length <= MODEL_SOURCES_MAX
        && Object.entries(value).every(([id, ceilings]) => SOURCE_ID.test(id)
          && plain(ceilings) && Object.entries(ceilings).every(([name, cap]) =>
            (name === "daily_cost" || name === "monthly_cost")
            && (cap === null || bounded(cap, MODEL_SETTINGS[name]))));
    }
    if (!Object.hasOwn(MODEL_SETTINGS, key)) {
      return false;
    }
    const range = MODEL_SETTINGS[key as keyof typeof MODEL_SETTINGS];
    return ((key === "daily_cost" || key === "monthly_cost") && value === null)
      || bounded(value, range);
  });
}

/** The document as `validate` leaves it. */
export interface PolicyDocument {
  readonly files?: Partial<FileSettings>;
  readonly runs?: Partial<RunSettings>;
  readonly models?: ModelPolicy;
  readonly entries: PolicyEntry[];
  readonly egress: { readonly mode: string;
                     readonly destinations: { readonly host: string; readonly port: number }[] };
  readonly spend: {
    readonly daily: number | null;
    readonly monthly: number | null;
    readonly transaction: number | null;
    readonly ask_above: number | null;
    readonly source_ceilings: { readonly [source: string]: number };
  };
}

/** A held proposal's change: the JSON object an agent filed. */
export type Change = { readonly [key: string]: string | number | boolean | null | object };

export interface PolicyState {
  readonly generation: number;
  readonly digest: string;
  readonly document: PolicyDocument;
}

/** What `read` shows the owner: the Python's `OwnerPolicy.projection()`. */
export interface Projection {
  readonly owner: string;
  readonly generation: number;
  readonly digest: string;
  readonly policy: PolicyDocument;
}

export type ReceiptKind = "write" | "wake_limits" | "outside_grant" | "proposal" | "refusal";

export interface ReceiptFields {
  readonly session_id?: string | null;
  readonly old_digest?: string | null;
  readonly new_digest?: string | null;
  readonly request_digest?: string | null;
  readonly generation?: number;
  readonly rule?: string | null;
  readonly decision?: string | null;
  readonly outcome?: string | null;
}

export type Receipt = {
  readonly at: number;
  readonly kind: string;
  readonly old_digest: string | null;
  readonly new_digest: string | null;
  readonly request_digest: string | null;
  readonly generation: number;
  readonly rule: string | null;
  readonly decision: string | null;
  readonly outcome: string | null;
};

/** A held card the owner applies: its id and the digest of what the owner was shown. */
export interface ProposalApply {
  readonly request_id: string;
  readonly digest: string;
}

type Usage = {
  pending_proposals: number;
  applied_proposals: number;
  owner_receipts: number;
  agent_receipts: number;
  refusal_receipts: number;
};

const BUCKETS = Object.freeze(["owner", "agent", "refusal"] as const);
type Bucket = (typeof BUCKETS)[number];

function bucket(kind: ReceiptKind): Bucket {
  return kind === "proposal" ? "agent" : kind === "refusal" ? "refusal" : "owner";
}

/** A JSON object: not an array, not null, not a class instance. */
export function plain(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function keys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const own = Object.keys(value);
  return own.length === expected.length && expected.every((key) => Object.hasOwn(value, key));
}

/**
 * The Python's `json.dumps(value, sort_keys=True, separators=(",", ":"),
 * ensure_ascii=False)`, for the JSON values a request carries.
 */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => (item === undefined ? "null" : canonical(item))).join(",")}]`;
  }
  if (plain(value)) {
    return `{${Object.keys(value).filter((key) => value[key] !== undefined).sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/** `_digest`: `sha256:` over the canonical JSON. */
export function policyDigest(value: unknown): string {
  return "sha256:" + createHash("sha256").update(canonical(value), "utf8").digest("hex");
}

/** A non-negative whole number the store can hold exactly: the Python's `type(v) is int`. */
function whole(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

/**
 * `normalize_destination(host, port) == entry`, for a hostname: lowercase ASCII DNS
 * labels with no trailing dot, as the Python's IDNA fast path leaves them. An IP literal
 * waits for the gateway's address check.
 */
function destination(entry: unknown): void {
  if (!plain(entry) || !keys(entry, ["host", "port"])) {
    throw new Invalid("invalid egress destination");
  }
  const { host, port } = entry;
  if (typeof host !== "string" || !whole(port) || port < 1 || port > 65_535) {
    throw new Invalid("invalid egress destination");
  }
  if (host.includes(":") || host.startsWith("[")) {
    throw new Refused("an egress IP literal waits for the gateway's address check");
  }
  if (!host || host.length > 253 || !host.split(".").every((label) => LABEL.test(label))) {
    throw new Invalid("invalid egress destination");
  }
  // The host as a URL reads it: any spelling of an IPv4 address (decimal, hex, octal,
  // short forms) comes back dotted, and a name ending in a number is no host at all.
  let parsed: string;
  try {
    parsed = new URL(`https://${host}/`).hostname;
  } catch {
    throw new Invalid("invalid egress destination");
  }
  if (IPV4.test(parsed)) {
    throw new Refused("an egress IP literal waits for the gateway's address check");
  }
}

/** `_validate`: the whole document, or a refusal naming its first fault. */
export function validate(document: unknown, limits: PolicyLimits = POLICY_LIMITS):
    PolicyDocument {
  if (!plain(document) || !keys(document, ["entries", "spend", "egress",
    ...(["files", "models", "runs"] as const).filter((table) =>
      Object.hasOwn(document, table))])) {
    throw new Invalid("invalid policy document");
  }
  if (document["models"] !== undefined && !validModels(document["models"])) {
    throw new Invalid("invalid models policy");
  }
  const files = document["files"];
  if (files !== undefined && (!plain(files) || Object.entries(files).some(([key, value]) =>
    !Object.hasOwn(FILE_SETTINGS, key) || !whole(value)
    || value < FILE_SETTINGS[key as keyof FileSettings][0]
    || value > FILE_SETTINGS[key as keyof FileSettings][1]))) {
    throw new Invalid("invalid files policy");
  }
  const settings = { ...FILE_DEFAULTS, ...(files as Partial<FileSettings> | undefined) };
  if (settings.max_entries * Math.max(settings.keep_versions, settings.keep_recent)
      > settings.max_history) {
    throw new Invalid("files policy: max_history holds max_entries times the larger of " +
                      "keep_versions and keep_recent");
  }
  const runs = document["runs"];
  if (runs !== undefined && (!plain(runs) || Object.entries(runs).some(([key, value]) =>
    !Object.hasOwn(RUN_SETTINGS, key)
    || !bounded(value, RUN_SETTINGS[key as keyof RunSettings])))) {
    throw new Invalid("invalid runs policy");
  }
  const kept = { ...RUN_DEFAULTS, ...(runs as Partial<RunSettings> | undefined) };
  if (kept.keep_run_receipts < kept.keep_runs) {
    throw new Invalid("runs policy: keep_run_receipts is at least keep_runs");
  }
  const egress = document["egress"];
  if (!plain(egress) || !keys(egress, ["mode", "destinations"])
      || !EGRESS_MODES.includes(egress["mode"] as string)
      || !Array.isArray(egress["destinations"])) {
    throw new Invalid("invalid egress policy");
  }
  for (const entry of egress["destinations"]) {
    destination(entry);
  }
  const entries = document["entries"];
  if (!Array.isArray(entries)) {
    throw new Invalid("invalid policy entries");
  }
  for (const entry of entries) {
    if (!plain(entry) || !keys(entry, ["scope", "table", "values"])
        || entry["scope"] !== "account" || !TABLES.includes(entry["table"] as string)
        || !plain(entry["values"]) || Object.values(entry["values"]).some((value) =>
          value !== null && !["string", "number", "boolean"].includes(typeof value))) {
      throw new Invalid("invalid scoped policy entry");
    }
    const values = entry["values"];
    const action = values["action_class"];
    if (entry["table"] === "rules" && (!BEHAVIOURS.includes(values["behaviour"] as string)
        || typeof action !== "string" || !Object.hasOwn(DEFAULT_RULES, action))) {
      throw new Invalid("invalid approval rule");
    }
    const kind = values["kind"];
    if (entry["table"] === "operation_kinds"
        && (typeof kind !== "string" || !Object.hasOwn(OPERATION_KINDS, kind))) {
      throw new Invalid("invalid declared operation");
    }
  }
  const spend = document["spend"];
  if (!plain(spend) || !keys(spend, [...CAPS, "source_ceilings"])) {
    throw new Invalid("invalid spend policy");
  }
  if (CAPS.some((cap) => spend[cap] !== null && !whole(spend[cap]))) {
    throw new Invalid("invalid spend cap");
  }
  const ceilings = spend["source_ceilings"];
  if (!plain(ceilings) || Object.values(ceilings).some((value) => !whole(value))) {
    throw new Invalid("invalid source ceiling");
  }
  if (codePoints(canonical(document)) > limits.maxDocument) {
    throw new Refused("the policy document is too large");
  }
  return document as unknown as PolicyDocument;
}

/** A card id: 1 to 128 characters, as the Python's. */
function cardId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && codePoints(value) <= 128;
}

/** `expected_generation`: the policy generation the owner's change was made against. */
function expected(value: unknown): number {
  if (!whole(value) || value < 1) {
    throw new Invalid("expected policy generation required");
  }
  return value;
}

export class Policy {
  readonly #storage: OwnerStorage;
  readonly #limits: PolicyLimits;

  constructor(storage: OwnerStorage, limits: PolicyLimits = POLICY_LIMITS) {
    this.#storage = storage;
    this.#limits = limits;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  #usage(): Usage {
    return this.#sql.exec<Usage>(
      "SELECT pending_proposals, applied_proposals, owner_receipts, agent_receipts, " +
        "refusal_receipts FROM policy_usage",
    ).one();
  }

  /** The policy generation as stored. */
  generation(): number {
    return this.#generation();
  }

  #generation(): number {
    return this.#sql.exec<{ generation: number }>("SELECT generation FROM owner_policy").one()
      .generation;
  }

  #stored(): { generation: number; digest: string; document: string } {
    return this.#sql.exec<{ generation: number; digest: string; document: string }>(
      "SELECT generation, digest, document FROM owner_policy",
    ).one();
  }

  /** The policy as stored, its digest and its document rechecked. */
  read(): PolicyState {
    const row = this.#stored();
    const document: unknown = JSON.parse(row.document);
    if (policyDigest(document) !== row.digest) {
      throw new Error("policy digest mismatch");
    }
    return { generation: row.generation, digest: row.digest,
             document: validate(document, this.#limits) };
  }

  projection(owner: string): Projection {
    const { generation, digest, document } = this.read();
    return { owner, generation, digest, policy: document };
  }

  /** One receipt; each bucket keeps its newest `receiptsPerBucket`. */
  receipt(kind: ReceiptKind, fields: ReceiptFields, now: number): void {
    const sql = this.#sql;
    const into = bucket(kind);
    sql.exec(
      "INSERT INTO policy_receipts (bucket, kind, at, session_id, old_digest, new_digest, " +
        "request_digest, generation, rule, decision, outcome) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      into, kind, now, fields.session_id ?? null, fields.old_digest ?? null,
      fields.new_digest ?? null, fields.request_digest ?? null,
      fields.generation ?? this.#generation(), fields.rule ?? null,
      fields.decision ?? null, fields.outcome ?? null,
    );
    const over = this.#usage()[`${into}_receipts`] - this.#limits.receiptsPerBucket;
    if (over > 0) {
      sql.exec("DELETE FROM policy_receipts WHERE seq IN (SELECT seq FROM policy_receipts " +
                 "WHERE bucket = ? ORDER BY seq LIMIT ?)", into, over);
    }
  }

  /** The last `receiptsListed` of each bucket, newest first; never a session id. */
  receipts(): Receipt[] {
    const listed = BUCKETS.flatMap((from) => this.#sql.exec<Receipt & { seq: number }>(
      "SELECT seq, at, kind, old_digest, new_digest, request_digest, generation, rule, " +
        "decision, outcome FROM policy_receipts WHERE bucket = ? ORDER BY seq DESC LIMIT ?",
      from, this.#limits.receiptsListed,
    ).toArray());
    return listed.sort((a, b) => b.seq - a.seq).map(({ seq: _seq, ...receipt }) => receipt);
  }

  /**
   * The owner's write; with `proposal`, only as that held card's one apply. The held
   * proposal must be pending, unexpired, a `policy_proposal` of exactly this document
   * and generation with the digest the owner was shown, and is marked applied in the
   * same transaction.
   */
  write(sessionId: string, expectedGeneration: unknown, document: unknown,
        proposal: unknown, now: number): PolicyState {
    validate(document, this.#limits);
    const generation = expected(expectedGeneration);
    if (proposal !== null && (!plain(proposal) || !keys(proposal, ["request_id", "digest"])
                              || !cardId(proposal["request_id"])
                              || !cardId(proposal["digest"]))) {
      throw new Invalid("invalid proposal apply");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const row = this.#stored();
      if (row.generation !== generation) {
        throw new Refused("policy generation changed");
      }
      if (proposal !== null) {
        const held = sql.exec<{ digest: string; document: string }>(
          "SELECT digest, document FROM policy_proposals WHERE request_id = ? " +
            "AND applied_at IS NULL AND at > ?",
          proposal["request_id"] as string, now - this.#limits.proposalTtlS,
        ).toArray()[0];
        const change: unknown = held === undefined ? {} : JSON.parse(held.document);
        if (held === undefined || held.digest !== proposal["digest"] || !plain(change)
            || change["type"] !== "policy_proposal"
            || canonical(change["policy"]) !== canonical(document)
            || change["expected_generation"] !== generation) {
          throw new Refused("proposal is not the pending card shown");
        }
        sql.exec("UPDATE policy_proposals SET applied_at = ? WHERE request_id = ?", now,
                 proposal["request_id"] as string);
      }
      const digest = policyDigest(document);
      sql.exec("UPDATE owner_policy SET generation = ?, digest = ?, document = ?",
               generation + 1, digest, canonical(document));
      this.receipt("write", { session_id: sessionId, old_digest: row.digest, new_digest: digest,
                              generation: generation + 1, outcome: "committed" }, now);
      return { generation: generation + 1, digest, document: document as PolicyDocument };
    });
  }

  /**
   * An owner policy change kept outside the document (an agent's wake limits, stored on
   * its registry row where the ledger reads them): it bumps the generation and is
   * receipted like a write. Run inside the caller's transaction with the change itself.
   */
  changed(sessionId: string, expectedGeneration: unknown, kind: ReceiptKind,
          request: unknown, rule: string, now: number): number {
    const generation = expected(expectedGeneration);
    return this.#storage.transactionSync(() => {
      const row = this.#stored();
      if (row.generation !== generation) {
        throw new Refused("policy generation changed");
      }
      this.#sql.exec("UPDATE owner_policy SET generation = ?", generation + 1);
      this.receipt(kind, { session_id: sessionId, old_digest: row.digest,
                           new_digest: row.digest, request_digest: policyDigest(request),
                           generation: generation + 1, rule, decision: "set",
                           outcome: "committed" }, now);
      return generation + 1;
    });
  }

  /**
   * An agent's proposal, receipted; with its card's id, held for the owner. Refiling a
   * held card with another change is refused (the same change again is a no-op);
   * expired proposals and all but the newest applied ones are pruned first.
   */
  propose(change: unknown, requestId: unknown, proposer: string, now: number):
      { request_digest: string; generation: number } {
    if (!plain(change) || codePoints(canonical(change)) > this.#limits.maxChange) {
      throw new Invalid("invalid policy proposal");
    }
    if (requestId !== null && !cardId(requestId)) {
      throw new Invalid("invalid policy proposal");
    }
    const generation = this.#generation();
    const digest = policyDigest(change);
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      if (requestId !== null) {
        sql.exec("DELETE FROM policy_proposals WHERE at < ?", now - this.#limits.proposalTtlS);
        const over = this.#usage().applied_proposals - this.#limits.appliedProposals;
        if (over > 0) {
          sql.exec("DELETE FROM policy_proposals WHERE request_id IN (SELECT request_id " +
                     "FROM policy_proposals WHERE applied_at IS NOT NULL " +
                     "ORDER BY applied_at LIMIT ?)", over);
        }
        const held = sql.exec<{ digest: string }>(
          "SELECT digest FROM policy_proposals WHERE request_id = ?", requestId,
        ).toArray()[0];
        if (held !== undefined && held.digest !== digest) {
          throw new Refused("a proposal is already held for this card");
        }
        if (held === undefined) {
          if (this.#usage().pending_proposals >= this.#limits.pendingProposals) {
            throw new Refused("too many pending policy proposals");
          }
          const share = sql.exec<{ pending: number }>(
            "SELECT pending FROM proposal_shares WHERE proposer = ?", proposer,
          ).toArray()[0]?.pending ?? 0;
          if (proposer !== "owner" && share >= this.#limits.pendingPerClient) {
            throw new Refused("this client's pending policy proposals are at their limit");
          }
          sql.exec("INSERT INTO policy_proposals (request_id, digest, document, at, proposer) " +
                     "VALUES (?, ?, ?, ?, ?)", requestId, digest, canonical(change), now,
                   proposer);
        }
      }
      this.receipt("proposal", { request_digest: digest, generation, rule: "owner_policy",
                                 decision: "ask", outcome: "requested" }, now);
      return { request_digest: digest, generation };
    });
  }

  /** The pending, unexpired proposal held under a card id, and its digest, or null. */
  held(requestId: unknown, now: number): { change: Change; digest: string } | null {
    if (!cardId(requestId)) {
      return null;
    }
    const row = this.#sql.exec<{ digest: string; document: string }>(
      "SELECT digest, document FROM policy_proposals WHERE request_id = ? " +
        "AND applied_at IS NULL AND at > ?", requestId, now - this.#limits.proposalTtlS,
    ).toArray()[0];
    return row === undefined ? null
      : { change: JSON.parse(row.document) as Change, digest: row.digest };
  }

  /**
   * `receipt_refusal`: a denied call, by the digest of what was asked, under its rule: an
   * owner-session call's, or an outside client's tool call's (`authority.ts`).
   */
  refusal(action: string, request: unknown, now: number,
          rule = "interactive_owner_session"): void {
    this.#storage.transactionSync(() => this.receipt("refusal", {
      request_digest: policyDigest({ action, request }), rule, decision: "deny",
      outcome: "refused",
    }, now));
  }
}
