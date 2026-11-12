/**
 * The owner's policy document (N10): its tables, its defaults, its digest and the one
 * validator every write and read of it holds it to (`validate`). The contract's, so the owner's
 * object (`owner/src/policy.ts`, which stores, receipts and applies it) and the cutover's
 * importer (`platform/import/census.ts`, which runs every old owner's document through it
 * before the window, so a document it refuses is archived and never stops an import) hold a
 * document to the same rules. Ported from the Python's `_validate`; the declared differences
 * are `owner/src/policy.ts`'s.
 */
import { sha256Hex } from "./digest";
import { toolNames } from "./doors";
import { FILE_DEFAULTS, KEEP_CEILING } from "./file-defaults";
import { CHUNK_BYTES, MAX_INTENT_CHUNKS } from "./limits";
import { Refusal } from "./refusal";
import { codePoints, SOURCE_ID } from "./text";

/**
 * The refusal of an IP-literal egress destination. Workers `fetch` shows no resolved
 * address, so the gateway fetches strictly publicly, E2B filters the sandbox's CIDRs (8b's
 * live probe confirms private addresses are unreachable), and a destination is a DNS name,
 * matched by SNI.
 */
export const EGRESS_IP_LITERAL = "egress_ip_literal: an egress destination is a DNS name, " +
  "never an IP literal";
/** `normalize_destination`'s DNS label: underscores allowed, hyphens interior. */
const LABEL = /^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/;
/** A host the URL parser turned into an IPv4 address. */
const IPV4 = /^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$/;

/**
 * One egress destination of the owner's policy (`policy.ts` validates each by this):
 * `normalize_destination(host, port) == entry`, for a hostname: lowercase ASCII DNS labels
 * with no trailing dot, as the Python's IDNA fast path leaves them, and a port. An IP
 * literal, in any spelling the URL parser reads as an address, is refused
 * (`EGRESS_IP_LITERAL`); a host no URL can carry (a name ending in a number, a malformed
 * `xn--` label) is invalid.
 */
export function checkDestination(entry: unknown): void {
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)
      || Object.keys(entry).sort().join() !== "host,port") {
    throw new Refusal("invalid", "invalid egress destination");
  }
  const { host, port } = entry as Record<string, unknown>;
  if (typeof host !== "string" || typeof port !== "number" || !Number.isSafeInteger(port)
      || port < 1 || port > 65_535) {
    throw new Refusal("invalid", "invalid egress destination");
  }
  if (host.includes(":") || host.startsWith("[")) {
    throw new Refusal("egress_ip_literal", EGRESS_IP_LITERAL);
  }
  if (!host || host.length > 253 || !host.split(".").every((label) => LABEL.test(label))) {
    throw new Refusal("invalid", "invalid egress destination");
  }
  // The host as a URL reads it: any spelling of an IPv4 address (decimal, hex, octal,
  // short forms) comes back dotted, and a name ending in a number is no host at all.
  let parsed: string;
  try {
    parsed = new URL(`https://${host}/`).hostname;
  } catch {
    throw new Refusal("invalid", "invalid egress destination");
  }
  if (IPV4.test(parsed)) {
    throw new Refusal("egress_ip_literal", EGRESS_IP_LITERAL);
  }
}

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
interface PolicyLimits {
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
  proposalTtlS: 90 * 86_400,
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
 * history. `max_bytes` counts every byte of user content the owner stores, wherever it
 * lives (the one storage number, founder 2026-11-03): unique chunk bytes, live and in
 * history, the bodies placed in R2 (ADR-035), the small files kept inline, and the
 * collection rows', their history's and mail's bodies kept inline in the object; never the
 * platform's own bookkeeping (indexes, receipts, counters). It is the tier's storage (free 25 GB, paid 250 GB, the control
 * record's, `tier.ts`), never a number of ours, which the owner may set lower and no
 * higher (`fileLimits`); `max_inline_bytes`
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
 * always keeps room of its own. A client is
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
 * defaults come with the sync agent (tasks 6). The defaults are the contract's
 * `FILE_DEFAULTS`, since the cutover's importer holds each owner to them too.
 */
export type FileSettings = { readonly [K in keyof typeof FILE_DEFAULTS | "max_bytes"]: number };
/** The range each setting may take. `max_bytes` past the tier's storage holds at the tier's. */
export const FILE_SETTINGS: { readonly [K in keyof FileSettings]: readonly [number, number] } =
  Object.freeze({
    max_bytes: Object.freeze([0, 1024 ** 5] as const),
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

/**
 * Owner code mode policy (agent research §6.4): whether code mode scripts may run, their
 * VMs in the codemode Worker. On until the owner turns it off: code mode adds no authority
 * (each call is the tool's own door, admitted again), so the owner's own setting is the
 * one that limits it.
 */
export const CODEMODE_DEFAULTS = Object.freeze({ on: true });

/** The document's `codemode` table, as written. */
export interface CodemodePolicy {
  readonly on?: boolean;
}

/** The `codemode` table: `on` alone, a boolean. */
function validCodemode(codemode: unknown): boolean {
  return plain(codemode) && Object.entries(codemode).every(([key, value]) =>
    key === "on" && typeof value === "boolean");
}

/** The document's `tools` table, as written: the owner's `custom` profile. */
export interface ToolPolicy {
  readonly custom?: readonly string[];
}

/** The `tools` table: `custom` alone, distinct names of the table's tools. */
function validTools(tools: unknown): boolean {
  const names = toolNames();
  return plain(tools) && Object.entries(tools).every(([key, value]) =>
    key === "custom" && Array.isArray(value) && new Set(value).size === value.length
    && value.every((name) => typeof name === "string" && names.includes(name)));
}

/**
 * Owner model policy (gateway research §4): what one sandbox run's model calls may spend
 * (held at the egress door, `egress.ts`; the `turn_` names stay until slice R names them
 * for runs), and what the owner's sources may spend in a UTC day and month.
 * Money is in micro-dollars (µ$), as pi-ai's catalog prices are dollars per million
 * tokens: a token at a price of `p` costs `p` µ$. The Python's per-turn limits
 * (`TurnRequestBudget`: `max_tokens=1_000_000`, `max_cost_microunits=25_000_000`) are the
 * defaults' model, raised for tokens since a run is a whole agent loop, each request
 * re-sending its context. A request cap bounds a runaway loop. The daily and monthly
 * ceilings default to none, as the Python's do (`PolicyStore`'s default document:
 * `"daily": None, "monthly": None`); the owner sets them.
 */
export const MODEL_DEFAULTS = Object.freeze({
  turn_requests: null as number | null,
  turn_tokens: null as number | null,
  turn_cost: null as number | null,
  daily_cost: null as number | null,
  monthly_cost: null as number | null,
});
/** The range each model setting may take, when the owner sets it (null: none, the default). */
export const MODEL_SETTINGS = Object.freeze({
  turn_requests: Object.freeze([1, 1_000_000] as const),
  turn_tokens: Object.freeze([1, 1_000_000_000] as const),
  turn_cost: Object.freeze([0, 1_000_000_000_000] as const),
  daily_cost: Object.freeze([0, 1_000_000_000_000] as const),
  monthly_cost: Object.freeze([0, 1_000_000_000_000] as const),
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
  readonly turn_requests?: number | null;
  readonly turn_tokens?: number | null;
  readonly turn_cost?: number | null;
  readonly daily_cost?: number | null;
  readonly monthly_cost?: number | null;
  readonly sources?: { readonly [source: string]: SourceCeilings };
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
    return value === null || bounded(value, range);
  });
}

/** The document as `validate` leaves it. */
export interface PolicyDocument {
  readonly codemode?: CodemodePolicy;
  readonly files?: Partial<FileSettings>;
  readonly models?: ModelPolicy;
  readonly tools?: ToolPolicy;
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

/** A JSON object: not an array, not null, not a class instance. */
export function plain(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

export function keys(value: Record<string, unknown>, expected: readonly string[]):
    boolean {
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
  return "sha256:" + sha256Hex(canonical(value));
}

/** A non-negative whole number the store can hold exactly: the Python's `type(v) is int`. */
export function whole(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

/** `_validate`: the whole document, or a refusal naming its first fault. */
export function validate(document: unknown): PolicyDocument {
  if (!plain(document) || !keys(document, ["entries", "spend", "egress",
    ...(["codemode", "files", "models", "tools"] as const).filter((table) =>
      Object.hasOwn(document, table))])) {
    throw new Refusal("invalid", "invalid policy document");
  }
  if (document["models"] !== undefined && !validModels(document["models"])) {
    throw new Refusal("invalid", "invalid models policy");
  }
  if (document["codemode"] !== undefined && !validCodemode(document["codemode"])) {
    throw new Refusal("invalid", "invalid codemode policy: on, a boolean");
  }
  if (document["tools"] !== undefined && !validTools(document["tools"])) {
    throw new Refusal("invalid",
                      "invalid tools policy: custom, distinct names of the MCP door's tools");
  }
  const files = document["files"];
  if (files !== undefined && (!plain(files) || Object.entries(files).some(([key, value]) =>
    !Object.hasOwn(FILE_SETTINGS, key) || !whole(value)
    || value < FILE_SETTINGS[key as keyof FileSettings][0]
    || value > FILE_SETTINGS[key as keyof FileSettings][1]))) {
    throw new Refusal("invalid", "invalid files policy");
  }
  const settings = { ...FILE_DEFAULTS, ...(files as Partial<FileSettings> | undefined) };
  if (settings.max_entries * Math.max(settings.keep_versions, settings.keep_recent)
      > settings.max_history) {
    throw new Refusal("invalid",
                      "files policy: max_history holds max_entries times the larger of " +
                      "keep_versions and keep_recent");
  }
  const egress = document["egress"];
  if (!plain(egress) || !keys(egress, ["mode", "destinations"])
      || !EGRESS_MODES.includes(egress["mode"] as string)
      || !Array.isArray(egress["destinations"])) {
    throw new Refusal("invalid", "invalid egress policy");
  }
  for (const entry of egress["destinations"]) {
    checkDestination(entry);
  }
  const entries = document["entries"];
  if (!Array.isArray(entries)) {
    throw new Refusal("invalid", "invalid policy entries");
  }
  for (const entry of entries) {
    if (!plain(entry) || !keys(entry, ["scope", "table", "values"])
        || entry["scope"] !== "account" || !TABLES.includes(entry["table"] as string)
        || !plain(entry["values"]) || Object.values(entry["values"]).some((value) =>
          value !== null && !["string", "number", "boolean"].includes(typeof value))) {
      throw new Refusal("invalid", "invalid scoped policy entry");
    }
    const values = entry["values"];
    const action = values["action_class"];
    if (entry["table"] === "rules" && (!BEHAVIOURS.includes(values["behaviour"] as string)
        || typeof action !== "string" || !Object.hasOwn(DEFAULT_RULES, action))) {
      throw new Refusal("invalid", "invalid approval rule");
    }
    const kind = values["kind"];
    if (entry["table"] === "operation_kinds"
        && (typeof kind !== "string" || !Object.hasOwn(OPERATION_KINDS, kind))) {
      throw new Refusal("invalid", "invalid declared operation");
    }
  }
  const spend = document["spend"];
  if (!plain(spend) || !keys(spend, [...CAPS, "source_ceilings"])) {
    throw new Refusal("invalid", "invalid spend policy");
  }
  if (CAPS.some((cap) => spend[cap] !== null && !whole(spend[cap]))) {
    throw new Refusal("invalid", "invalid spend cap");
  }
  const ceilings = spend["source_ceilings"];
  if (!plain(ceilings) || Object.values(ceilings).some((value) => !whole(value))) {
    throw new Refusal("invalid", "invalid source ceiling");
  }
  if (codePoints(canonical(document)) > POLICY_LIMITS.maxDocument) {
    throw new Refusal("refused", "the policy document is too large");
  }
  return document as unknown as PolicyDocument;
}
