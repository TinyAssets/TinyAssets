/**
 * The owner's policy document (N10): its tables, its defaults, its digest and the one
 * validator every write and read of it holds it to (`validate`). The contract's, so the owner's
 * object (`owner/src/policy.ts`, which stores, receipts and applies it) and the cutover's
 * importer (`platform/import/census.ts`, which runs every old owner's document through it
 * before the window, so a document it refuses is archived and never stops an import) hold a
 * document to the same rules. Ported from the Python's `_validate`; the declared differences
 * are `owner/src/policy.ts`'s.
 *
 * The document is its `egress` table and the optional tables below, each left out until the
 * owner sets it. The `actions` table holds the owner's action rules (d3 §N): each names an act
 * (`acts.json`), asks first or never lets it happen, and may name the tools that do it. A rule
 * holds where its act is seen (`acts.ts`), so one nothing could see is refused here. The old
 * rule tables and the spend caps are gone: nothing read them (d3 §N6).
 */
import { actNamed } from "./acts";
import { sha256Hex } from "./digest";
import { actDoors, toolNames } from "./doors";
import { type FileDefaults, KEEP_CEILING } from "./file-defaults";
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

/** The egress table's modes: every name out, or the owner's destinations alone. */
export const EGRESS_MODES = Object.freeze(["open", "allowlist"] as const);
export type EgressMode = (typeof EGRESS_MODES)[number];

/** The default document as stored, and its digest (a test recomputes both). */
export const DEFAULT_POLICY_JSON = '{"egress":{"destinations":[],"mode":"open"}}';
export const DEFAULT_POLICY_DIGEST =
  "sha256:f90862c9625ff981a8a7a0a5604579eadad9e1787c3ac869045362377960c090";

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

/**
 * One rule of the `actions` table: its act (`acts.json`), whether the act waits for the
 * owner's yes (`ask`) or never happens (`never`), and the tools that do it, which the CLIs'
 * hooks see by name (`mcp:<server>:<tool>`, each part a name or `*` patterns). An act with no
 * rule is allowed: the table is empty until the owner writes one (Fact 15).
 */
export interface ActionRule {
  readonly act: string;
  readonly do: "ask" | "never";
  readonly tools?: readonly string[];
}

/** A tool a rule names: an MCP server's and its tool's names, `*` matching any run of them. */
const TOOL_MATCHER = /^mcp:[A-Za-z0-9_*-]{1,64}:[A-Za-z0-9_.*-]{1,128}$/;

/**
 * The `actions` table: rules of an act the file holds, `ask` or `never`, and tools, when
 * named, distinct matchers, at least one. A rule on an act no door does and no hook kind is,
 * naming no tools, is refused: nothing could see it, so nothing could decide it.
 */
function checkActions(actions: unknown): void {
  if (!Array.isArray(actions)) {
    throw new Refusal("invalid", "invalid actions policy");
  }
  for (const rule of actions) {
    const tools = plain(rule) ? rule["tools"] : undefined;
    if (!plain(rule) || !keys(rule, ["act", "do", ...(tools === undefined ? [] : ["tools"])])
        || (rule["do"] !== "ask" && rule["do"] !== "never")
        || (tools !== undefined && (!Array.isArray(tools) || tools.length === 0
            || new Set(tools).size !== tools.length
            || !tools.every((tool) => typeof tool === "string" && TOOL_MATCHER.test(tool))))) {
      throw new Refusal("invalid", "invalid action rule: an act, ask or never, and any tools");
    }
    const act = actNamed(rule["act"]);
    if (act === undefined) {
      throw new Refusal("invalid", "invalid action rule: no such act");
    }
    if (actDoors(rule["act"] as string).length === 0 && act.hook.length === 0
        && tools === undefined) {
      throw new Refusal("invalid", "nothing can decide this action rule: no door or tool " +
                        "kind does its act, so name the tools that do it");
    }
  }
}

/**
 * Owner files policy (plan §6): the owner's own bounds on its files, and history retention.
 * The document's optional `files` table sets any of them; what it leaves out keeps
 * `FILE_DEFAULTS`, every one off but the two shares (Fact 15). A bound is refused loudly and
 * never makes room by dropping anything. `max_bytes` counts every byte of user content the
 * owner stores, wherever it lives (the one storage number, founder 2026-11-03): unique chunk
 * bytes, live and in history, the bodies placed in R2 (ADR-035), the small files kept inline,
 * and the collection rows', their history's and mail's bodies kept inline in the object;
 * never the platform's own bookkeeping (indexes, receipts, counters). It is the tier's storage
 * (free 25 GB, paid 250 GB, the control record's, `tier.ts`), never a number of ours, which
 * the owner may set lower and no higher (`fileLimits`). A file is at most `max_file_bytes`,
 * off at `MAX_INTENT_CHUNKS` chunks (40 GiB), the most one upload intent holds.
 *
 * Retention, each setting off until the owner sets it, and each binding alone as set:
 * `keep_versions` is how many of a live path's newest versions are always kept (off: its
 * current one alone), and, with `keep_recent` and `retention_days` off, the most any path
 * keeps; `retention_days` how long any other version is kept after it was
 * written (off: for ever); `keep_recent` the most a path keeps in all, those always kept
 * among them (off: no count). All off, every version is kept. A deleted path keeps up
 * to `keep_recent` versions for `retention_days`. A version past them goes in the change that
 * pushed it out, or once it is due; the owner may purge any path's history at any time. With
 * retention off, old versions take storage until the owner deletes them, and the storage-full
 * words name them (`storage-full.ts`).
 *
 * Each outside client holds at most `client_share` percent of the storage and of the file
 * index's ceiling (and of the open uploads and the chunks they hold), and all of them
 * together at most what `owner_reserve` percent leaves, so no client fills the owner's files
 * and the owner always keeps room of its own. A client is charged its own rows (the entries
 * it last wrote, its history rows, its temporary directories) and once for each content and
 * chunk they or its open uploads reference, whoever else references them too
 * (`manifest.ts`). The index's ceiling itself is no setting: it is the object's room
 * (`owner/src/files.ts` `INDEX_BYTES`).
 *
 * The sync agent's exclude list is not here: it is the owner's file
 * `/workspace/.ta/sync-ignore`, whose defaults come with the sync agent (tasks 6).
 */
export type FileSettings = { readonly [K in keyof FileDefaults | "max_bytes"]: number };
/** The range each setting may take. `max_bytes` past the tier's storage holds at the tier's. */
export const FILE_SETTINGS: { readonly [K in keyof FileSettings]: readonly [number, number] } =
  Object.freeze({
    max_bytes: Object.freeze([0, 1024 ** 5] as const),
    max_file_bytes: Object.freeze([0, MAX_INTENT_CHUNKS * CHUNK_BYTES] as const),
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
  readonly actions?: readonly ActionRule[];
  readonly codemode?: CodemodePolicy;
  readonly files?: Partial<FileSettings>;
  readonly models?: ModelPolicy;
  readonly tools?: ToolPolicy;
  readonly egress: { readonly mode: EgressMode;
                     readonly destinations: { readonly host: string; readonly port: number }[] };
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
  if (!plain(document) || !keys(document, ["egress",
    ...(["actions", "codemode", "files", "models", "tools"] as const).filter((table) =>
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
  const egress = document["egress"];
  if (!plain(egress) || !keys(egress, ["mode", "destinations"])
      || !(EGRESS_MODES as readonly unknown[]).includes(egress["mode"])
      || !Array.isArray(egress["destinations"])) {
    throw new Refusal("invalid", "invalid egress policy");
  }
  for (const entry of egress["destinations"]) {
    checkDestination(entry);
  }
  if (document["actions"] !== undefined) {
    checkActions(document["actions"]);
  }
  if (codePoints(canonical(document)) > POLICY_LIMITS.maxDocument) {
    throw new Refusal("refused", "the policy document is too large");
  }
  return document as unknown as PolicyDocument;
}
