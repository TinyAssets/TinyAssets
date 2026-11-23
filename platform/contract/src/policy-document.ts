/**
 * The owner's policy document: its tables, its defaults, its digest, the one validator
 * every write and read of it holds it to (`validate`), and how an agent's proposal changes it
 * (`edited`). The contract's, so the owner's object
 * (`owner/src/policy.ts`, which stores, receipts and applies it) and the cutover's importer
 * (`platform/import/policy.ts`, which maps every old owner's document to these rules and
 * holds what it writes to them) hold a document to the same rules.
 *
 * The document is its `egress` table and the optional tables below, each left out until the
 * owner sets it. The `actions` table holds the owner's action rules (d3 §N): each names an act
 * (`acts.json`), asks first or never lets it happen, and may name the tools that do it. A rule
 * holds where its act is seen (`acts.ts`), so one nothing could see is refused here. The old
 * rule tables and the spend caps are gone: nothing read them (d3 §N6).
 */
import { actNamed, TOOL_MATCHER } from "./acts.ts";
import { canonical } from "./canonical.ts";
import { keyed, object, whole } from "./checks.ts";
import { sha256Hex } from "./digest.ts";
import { actDoors, toolNames } from "./doors.ts";
import { type FileDefaults, KEEP_CEILING } from "./file-defaults.ts";
import { CHUNK_BYTES, MAX_INTENT_CHUNKS } from "./limits.ts";
import { Refusal } from "./refusal.ts";
import { AGENT_ID, codePoints, SOURCE_ID } from "./text.ts";

/**
 * The refusal of an IP-literal egress destination. Workers `fetch` shows no resolved
 * address, so the gateway fetches strictly publicly, E2B filters the sandbox's CIDRs (its
 * private addresses are unreachable, `sandbox-probe.md`), and a destination is a DNS name,
 * matched by SNI.
 */
export const EGRESS_IP_LITERAL = "egress_ip_literal: an egress destination is a DNS name, " +
  "never an IP literal";
/** A destination's DNS label: underscores allowed, hyphens interior. */
const LABEL = /^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/;
/** A host the URL parser turned into an IPv4 address. */
const IPV4 = /^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$/;

/**
 * One egress destination of the owner's policy (`policy.ts` validates each by this): a
 * hostname in its normal form, lowercase ASCII DNS labels with no trailing dot, and a port.
 * An IP literal, in any spelling the URL parser reads as an address, is refused
 * (`EGRESS_IP_LITERAL`); a host no URL can carry (a name ending in a number, a malformed
 * `xn--` label) is invalid.
 */
export function checkDestination(entry: unknown): void {
  if (!keyed(entry, ["host", "port"])) {
    throw new Refusal("invalid", "invalid egress destination");
  }
  const { host, port } = entry;
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
 * How large policy state may grow. The document's room holds hundreds of rules (a rule is
 * under 200 bytes); one outside client holds at most a quarter of the pending proposals; a
 * receipt bucket's newest rows are about a megabyte. A pending proposal waits until the owner
 * applies it or the room needs its place: it has no life of its own (Fact 15).
 */
interface PolicyLimits {
  readonly maxDocument: number;
  readonly maxChange: number;
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
  pendingProposals: 200,
  pendingPerClient: 50,
  appliedProposals: 200,
  receiptsPerBucket: 5_000,
  receiptsListed: 50,
});

/**
 * One rule of the `actions` table: its act (`acts.json`), whether the act waits for the
 * owner's yes (`ask`) or never happens (`never`), the one agent it holds for, by its id
 * (`AGENT_ID`: that agent's runs alone; every agent's and outside client's when absent), and
 * the tools that do it, which the CLIs' hooks see by name (`mcp:<server>:<tool>`, each part a
 * name or `*` patterns). An act with no rule is allowed: the table is empty until the owner
 * writes one (Fact 15).
 */
export interface ActionRule {
  readonly act: string;
  readonly do: "ask" | "never";
  readonly agent?: string;
  readonly tools?: readonly string[];
}

/**
 * Whether a rule on the act `name` (`acts.json`) holds somewhere though it names no tools:
 * a door does the act, or a CLI tool kind is it.
 */
export function seenUnnamed(name: string): boolean {
  return actDoors(name).length > 0 || (actNamed(name)?.hook.length ?? 0) > 0;
}

/**
 * The `actions` table: rules of an act the file holds, `ask` or `never`, an agent's id when
 * one is named, and tools, when named, distinct matchers, at least one. A rule on an act no
 * door does and no hook kind is, naming no tools, is refused: nothing could see it, so nothing
 * could decide it.
 */
function checkActions(actions: unknown): void {
  if (!Array.isArray(actions)) {
    throw new Refusal("invalid", "invalid actions policy");
  }
  for (const rule of actions) {
    const tools = object(rule) ? rule["tools"] : undefined;
    const agent = object(rule) ? rule["agent"] : undefined;
    if (!keyed(rule, ["act", "do", ...(agent === undefined ? [] : ["agent"]),
                      ...(tools === undefined ? [] : ["tools"])])
        || (rule["do"] !== "ask" && rule["do"] !== "never")
        || (agent !== undefined && (typeof agent !== "string" || !AGENT_ID.test(agent)))
        || (tools !== undefined && (!Array.isArray(tools) || tools.length === 0
            || new Set(tools).size !== tools.length
            || !tools.every((tool) => typeof tool === "string" && TOOL_MATCHER.test(tool))))) {
      throw new Refusal("invalid",
                        "invalid action rule: an act, ask or never, any agent and any tools");
    }
    if (actNamed(rule["act"]) === undefined) {
      throw new Refusal("invalid", "invalid action rule: no such act");
    }
    if (tools === undefined && !seenUnnamed(rule["act"] as string)) {
      throw new Refusal("invalid", "nothing can decide this action rule: no door or tool " +
                        "kind does its act, so name the tools that do it");
    }
  }
}

/**
 * Owner files policy: the owner's own bounds on its files, and history retention.
 * The document's optional `files` table sets any of them; what it leaves out keeps
 * `FILE_DEFAULTS`, every one off (Fact 15). A bound is refused loudly and
 * never makes room by dropping anything. `max_bytes` counts every byte of user content the
 * owner stores, wherever it lives (the one storage number, founder 2026-11-03): unique chunk
 * bytes, live and in history, the bodies placed in R2 (ADR-035), the small files kept inline,
 * and the collection rows', their history's and mail's bodies kept inline in the object;
 * never the platform's own bookkeeping (indexes, receipts, counters). It is the tier's storage
 * (the control record's, `admission.ts`), never a number of ours, which the owner may set
 * lower and no higher (`fileLimits`). A file is at most `max_file_bytes`,
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
 * together at most what `owner_reserve` percent leaves: set, no client fills the owner's
 * files and the owner keeps room of its own. Off (100 and 0), a client is held to the
 * owner's whole alone. A client is charged its own rows (the entries
 * it last wrote and its history rows) and once for each content and
 * chunk they or its open uploads reference, whoever else references them too
 * (`manifest.ts`). The index's ceiling itself is no setting: it is the object's room
 * (`owner/src/manifest.ts` `INDEX_BYTES`).
 *
 * The sync agent's exclude list is not here: it is the owner's file
 * `/workspace/.ta/sync-ignore`, whose defaults come with the sync agent.
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
 * Owner code mode policy: whether code mode scripts may run, their VMs in the codemode
 * Worker. Off until the owner turns it on, until code mode's isolation probe passes (H2,
 * `tasks.md` §5: whether one owner's looping script starves other owners' objects, as it
 * did in workerd; founder call 10, `untangle-2026-11/codemode-off.md`). Turning it on for
 * every owner is this value and the refusal's reason below, changed together, only on H2's
 * pass.
 */
export const CODEMODE_DEFAULTS = Object.freeze({ on: false });

/** The refusal of a `code` call while the owner's policy has code mode off, saying why. */
export const CODE_MODE_OFF = "code_mode_off: code mode is off for this owner. It is off " +
  "by default until its isolation probe (H2) passes; the owner turns it on in the owner " +
  "app (Connected apps, Code mode)";

/** The document's `codemode` table, as written. */
export interface CodemodePolicy {
  readonly on?: boolean;
}

/** The `codemode` table: `on` alone, a boolean. */
function validCodemode(codemode: unknown): boolean {
  return object(codemode) && Object.entries(codemode).every(([key, value]) =>
    key === "on" && typeof value === "boolean");
}

/** The document's `tools` table, as written: the owner's `custom` profile. */
export interface ToolPolicy {
  readonly custom?: readonly string[];
}

/** The `tools` table: `custom` alone, distinct names of the table's tools. */
function validTools(tools: unknown): boolean {
  const names = toolNames();
  return object(tools) && Object.entries(tools).every(([key, value]) =>
    key === "custom" && Array.isArray(value) && new Set(value).size === value.length
    && value.every((name) => typeof name === "string" && names.includes(name)));
}

/**
 * Owner model policy: what one sandbox run's model calls may spend (the `turn_` settings,
 * held at the egress door, `owner/src/egress.ts`), and what the owner's sources may spend in
 * a UTC day and month. Money is in micro-dollars (µ$), as pi-ai's catalog prices are dollars
 * per million tokens: a token at a price of `p` costs `p` µ$. Every setting is off until the
 * owner sets it (Fact 15); a request cap bounds a runaway loop, since a run is a whole agent
 * loop, each request re-sending its context.
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
  return whole(value, low, high);
}

/** The `models` table: known keys only, each in its range; null only for a ceiling. */
function validModels(models: unknown): boolean {
  if (!object(models)) {
    return false;
  }
  return Object.entries(models).every(([key, value]) => {
    if (key === "sources") {
      return object(value) && Object.keys(value).length <= MODEL_SOURCES_MAX
        && Object.entries(value).every(([id, ceilings]) => SOURCE_ID.test(id)
          && object(ceilings) && Object.entries(ceilings).every(([name, cap]) =>
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

/** The document's digest: `sha256:` over the canonical JSON. */
export function policyDigest(value: unknown): string {
  return "sha256:" + sha256Hex(canonical(value));
}

/** The whole document, or a refusal naming its first fault. */
export function validate(document: unknown): PolicyDocument {
  if (!object(document) || !keyed(document, ["egress",
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
  if (files !== undefined && (!object(files) || Object.entries(files).some(([key, value]) =>
    !Object.hasOwn(FILE_SETTINGS, key)
    || !whole(value, ...FILE_SETTINGS[key as keyof FileSettings])))) {
    throw new Refusal("invalid", "invalid files policy");
  }
  const egress = document["egress"];
  if (!keyed(egress, ["mode", "destinations"])
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

const ACTION_RULES = Object.freeze(["actions"]);
const DESTINATIONS = Object.freeze(["egress", "destinations"]);
/**
 * The lists whose entries a change adds and removes, by their places, each entry a whole rule.
 * Not the `tools` table's `custom`: left out, it is every tool, so an entry added to it or
 * taken from it means nothing of its own; the owner sets it whole.
 */
const LISTS: readonly (readonly string[])[] = Object.freeze([ACTION_RULES, DESTINATIONS]);
/**
 * Every list of the document, by its place: a `set` names none, nor anything holding one, so
 * no change replaces a list whole.
 */
const LIST_PLACES: readonly (readonly string[])[] = Object.freeze([
  ACTION_RULES, DESTINATIONS, Object.freeze(["tools", "custom"])]);

const CHANGE_REFUSED = "invalid policy change: it sets settings, nested as the policy is " +
  "(set, null for the default), and adds or removes action rules (actions) and egress " +
  "destinations (egress.destinations), naming at least one, and sets no list";

/** Whether `value` is a list or holds one: what a `set` never names. */
function holdsList(value: unknown): boolean {
  return Array.isArray(value) || (object(value) && Object.values(value).some(holdsList));
}

/**
 * Whether a `set` of `value` at `path` would replace or remove a list: `path` is a list's
 * place, or holds one and `value` is no table to merge into it. Read from the places, never
 * from the owner's document, so a refusal tells an agent nothing of what the owner set.
 */
function replacesList(path: readonly string[], value: unknown): boolean {
  return holdsList(value) || LIST_PLACES.some((place) =>
    path.every((key, at) => place[at] === key)
    && (place.length === path.length || !object(value)));
}

/**
 * `target`'s own value under `key`, and `value` put there as its own: a key a change names is
 * the change's data, never the object's prototype (`__proto__`).
 */
function own(target: unknown, key: string): unknown {
  return object(target) && Object.hasOwn(target, key) ? target[key] : undefined;
}
function put(target: Record<string, unknown>, key: string, value: unknown): void {
  Object.defineProperty(target, key, { value, enumerable: true, writable: true,
                                       configurable: true });
}

/**
 * `patch` merged into `target`, at `path` of the document (RFC 7396), naming no list; how many
 * settings it names.
 */
function merged(target: Record<string, unknown>, patch: Record<string, unknown>,
                path: readonly string[] = []): number {
  let named = 0;
  for (const [key, value] of Object.entries(patch)) {
    if (replacesList([...path, key], value)) {
      throw new Refusal("invalid", CHANGE_REFUSED);
    }
    if (object(value)) {
      const held = own(target, key);
      const into = object(held) ? held : {};
      put(target, key, into);
      named += merged(into, value, [...path, key]);
    } else {
      if (value === null) {
        delete target[key];
      } else {
        put(target, key, value);
      }
      named += 1;
    }
  }
  return named;
}

/** The entries `part` (a change's `add` or `remove`) names, for each of `LISTS`, or refused. */
function entries(part: unknown): unknown[][] {
  const lists = LISTS.map((): unknown[] => []);
  const walk = (value: unknown, path: readonly string[]): void => {
    const list = LISTS.findIndex((place) => canonical(place) === canonical(path));
    if (list >= 0 && Array.isArray(value)) {
      lists[list] = value;
    } else if (object(value)
               && LISTS.some((place) => path.every((key, at) => place[at] === key))) {
      for (const [key, inner] of Object.entries(value)) {
        walk(inner, [...path, key]);
      }
    } else {
      throw new Refusal("invalid", CHANGE_REFUSED);
    }
  };
  if (part !== undefined) {
    walk(part, []);
  }
  return lists;
}

/**
 * `document` with an agent's proposed `change` (`policy_propose`) made, validated whole. A
 * change names only what it changes, as the major agents' rule suggestions do
 * (`untangle-2026-11/policy-proposal-shape.md`): `set`, a JSON merge patch (RFC 7396) of the
 * settings, nested as the document is, null putting a setting back to its default; `add` and
 * `remove`, entries of `LISTS` at their places. The settings it sets are merged, the entries
 * it removes taken out, then those it adds put in unless there already. An agent reads no
 * policy, so a change replaces nothing it does not name, and an entry added that is there, or
 * removed that is not, changes nothing and is no fault: no answer tells an agent what the
 * owner's rules hold. Refused when it is no such change, names nothing, or leaves a document
 * `validate` refuses.
 */
export function edited(document: PolicyDocument, change: unknown): PolicyDocument {
  if (!keyed(change, [], ["set", "add", "remove"])
      || (change["set"] !== undefined && !object(change["set"]))) {
    throw new Refusal("invalid", CHANGE_REFUSED);
  }
  const next = structuredClone(document) as unknown as Record<string, unknown>;
  const set = object(change["set"]) ? merged(next, change["set"]) : 0;
  const [added, removed] = [entries(change["add"]), entries(change["remove"])];
  if (set + added.flat().length + removed.flat().length === 0) {
    throw new Refusal("invalid", CHANGE_REFUSED);
  }
  LISTS.forEach((place, list) => {
    const table = place.slice(0, -1).reduce<unknown>((at, key) => own(at, key), next);
    const key = place.at(-1)!;
    const before = own(table, key);
    const gone = new Set(removed[list]!.map((entry) => canonical(entry)));
    const after = (Array.isArray(before) ? before : [])
      .filter((entry) => !gone.has(canonical(entry)));
    const held = new Set(after.map((entry) => canonical(entry)));
    for (const entry of added[list]!) {
      if (!held.has(canonical(entry))) {
        held.add(canonical(entry));
        after.push(entry);
      }
    }
    if (object(table) && (before !== undefined || after.length > 0)) {
      put(table, key, after);
    }
  });
  return validate(next);
}
