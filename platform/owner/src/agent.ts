/**
 * The owner's agent: pi-durable inside the owner's object, one pi Session per agent thread
 * (ADR-032 §4, amended 2026-10-30; tasks 5, sub-slices A0, A2 and S3).
 *
 * Each thread is one pi Session over its own store (`thread-store.ts`, by RPC through
 * `remote-storage.ts`), hosted by the object's thread host (`thread-host.ts`), a Lifecycle
 * capability the object installs on its own `new Lifecycle(this)` beside the schedule,
 * with none of Lifecycle's runtime handlers: the object forwards `alarm` alone. Only pi's
 * bytes live in the store; the harness, every hook, the environment, the model provider and
 * every authority check run here, in the owner's object, over the owner's rows. A Session
 * opens on demand (a door's call, or the thread's own wake job), each time through
 * `#open`, which is safe to run again after a storage error failed the last one.
 *
 * What each Session is opened with, every time it opens:
 * - one registry for the whole object, shared by every Session (pi keeps a Harness
 *   subscribed to its registry until it closes), installed once, holding first
 *   `ta.authority`, the early authority gate (`authority.ts`), whose `beforeTool` runs
 *   before every other extension's, so its block wins; then pi's `CodingTools` with `read`
 *   wrapped `replay: "safe"` (only pure reads are marked safe; `write`, `edit` and `bash`
 *   stay `unsafe`, so a call an eviction cut off comes back `interrupted` and is never run
 *   twice), then `ta` (`ta.ts`, A3), the model's way to every other ability (replay-safe
 *   by its own rule: only its reads and a delegation run twice), then `ta.skills`, the
 *   skills section built from the owner's files (`resources.ts`, A5: rebuilt from the
 *   files' change feed, never per request), then the
 *   object's packages, whose tools the model is never offered: `ta` reaches those
 *   registered callable by tools. The `ta.` names are the object's: a package named
 *   so is refused, as one pi refuses to install is; each is skipped and kept as an owner
 *   notice, once while it is kept: installing never throws for owner content, since a
 *   failed install would fail every thread's open;
 * - the models, read at each use, bound to the Session: the `gateway` provider
 *   (`gateway-provider.ts`, A1), whose models are the owner's connected ones and their
 *   virtual `auto`, the default a thread starts on, and whose authority check reads this
 *   Session's own conversations (`modelAuthority`); with nothing connected the catalog is
 *   empty, so a turn settles `unanswered: no_model`, loudly;
 * - the identity its requests carry: TinyAssets' attribution, not pi's (pi-durable's
 *   default names pi);
 * - an environment per call, replays included: `OwnerFilesEnv` over the owner's files,
 *   whose writer in the file history is the conversation's stamp (`client:<id>` for an
 *   outside client's conversation, else `agent`), and which enforces the conversation's
 *   authority on every operation, as `ta.authority`'s document holds it;
 * - the telemetry adapter's metrics (`origin.ts`, `OriginMetrics`), pi's and pi-ai's,
 *   kept for the object's instance, every thread's Session's alike, and read by the
 *   owner's usage view;
 * - settings read at each use: progress committed at most once a second (the gateway
 *   streams live tokens from memory; a commit is a store RPC), a failed run's queued inputs
 *   withdrawn, so each settles `unanswered: run_failed` rather than waiting on an unrelated
 *   next input, and context and document retention short enough for many Sessions in one
 *   isolate (F11); over those, the part of owner policy that is pi's
 *   (`AgentParts.policy`: the model retry count) and the model source's compaction
 *   thresholds (`AgentSource.compaction`: the gateway provider's, each model's own),
 *   neither of which moves the fixed ones. No
 *   source selects extensions, in settings or a conversation's agent (a guard holds it),
 *   and a client's conversation whose selection drops the gate makes no model call
 *   (`modelAuthority`);
 * - `onReport`: each report becomes an owner notice (`notices.ts`);
 * - `conversationCreated`, in every creating commit, the one place the model's tools are
 *   fixed and the one way an authority is inherited: every conversation, the root, a fork
 *   and a subagent's included, offers the model exactly read, write, edit, bash and `ta`
 *   (pi's `modelTools`, written only here, a guard holds it; nothing the model calls
 *   returns a `control` that would add one); a conversation a task creates (a subagent's)
 *   takes its owner conversation's authority, over any copy a fork brought, which must
 *   agree; a fork holds its parent's (`fork: "current"`). Each conversation's provider
 *   session is recorded in `ta.session`, so a model call finds its conversation;
 * - the thread's telemetry root (`origin.ts`), which carries each causal chain's origin,
 *   the submitted input that started it, along pi's own propagation: a model call's turn.
 *
 * A thread is its Session's root conversation. Its row is recorded first, naming its store
 * (`threads.ts`), in a unit that re-checks the stamp; the root is made at the Session's
 * first open, configured from its agent's data files (`AgentConfig`: the instructions an
 * agent's `AGENTS.md` holds, Direction 3) as pi's own conversation agent, with the thread's
 * stamp written into its `ta.authority` document, both in the root's creating commit
 * (`root`'s `init`); never its extensions or its model's tools. `#open` makes a thread's
 * root, and `delegate.ts`'s `createChild` a subagent (`#delegate`, `ta`'s `agent:delegate`,
 * A5): the object's two conversation makers (a guard holds them).
 *
 * A subagent (`#delegate`) is a conversation a `ta` call owns, in its thread's own Session:
 * made in the call's commit with `ownership: {kind: "task"}`, found again by a rerun (`ta`
 * is replay-safe for it), configured as the named agent (its `AGENTS.md`) or as the caller,
 * holding the caller's authority and the five model tools (`#inherit`), within the thread's
 * cap of live conversations (`THREAD_CONVERSATIONS`). Its one input, `subagent:<task>`, is
 * submitted under the call's own context, so it joins the call's turn and every call of
 * the child is charged there, under its caps (`origin.ts`); the host admits it as a door's
 * input (`ThreadHost.linked`: the thread's open inputs, the admission) and refuses it before
 * pi does when that turn has no row (`turn_ended`). The call waits for the answer; aborting
 * the call aborts the child, and the parent is idle only once the child is (pi's ownership);
 * a revoke, a policy write and a suspension end it with its thread's work.
 *
 * Ending work (agent research §4.2), each in the door of the change, after its own unit
 * committed: a grant change or revoke ends its client's work (`endClient`, the client's
 * threads, which the unit marked dropping), a policy write every thread's with work running
 * (`endAll`, from the threads' live rows: active Sessions only). Each ends the pending waits
 * it covers (`wait`), closes the live views of the conversations it covers (`live-tee.ts`),
 * and aborts each of those Sessions' conversations with live work: pi withdraws its queued
 * inputs, marks and signals its live tasks (a model request in flight sees its abort
 * signal, so the gateway call ends and settles through `usage.ts`), and settles their
 * inputs `unanswered: aborted`. A grant change's ending takes background work too, sleepers
 * included; a policy write's takes background work that is running, but never a sleeping
 * task or a scheduled launch's (`scheduled.ts`), whose delivery re-checks the policy as it
 * then stands. A dropped client's thread then goes the one drop path
 * (`drop`): its Session closed, a run's receipt kept, its store wiped, its row forgotten.
 * The door waits for that at most `END_WAIT_MS`; pi finishes the abort, and the `threads`
 * lane the drop, on their own past then. The abort is the prompt stop, not the
 * enforcement: an abort missed (work admitted after the end's read, or an eviction between
 * the commit and the abort) still finds every tool operation and every model call refused.
 *
 * A scheduled launch (`schedule`, `scheduled.ts`) is a `ta.scheduled` pi task in its run
 * thread's root, installed here with the object's own extensions; its delivery runs here
 * (`#deliver`), its status and cancel read its task record (`scheduledStatus`,
 * `cancelScheduled`).
 *
 * Dropping a thread (`drop`, `threads.ts`), for every reason, is one path: idle retention,
 * a run's retention, a grant change or revoke, and the owner's erasure (`erase.ts`) each
 * mark the row `dropping` in a unit of their own, then hand it here. A dropping thread is
 * never opened: its work is ended in the Session as it is, if open, and a run's receipt is
 * read from its store directly once the Session closed.
 */
import type { JsonValue } from "@earendil-works/chord";
import { BACKGROUND_CONTEXT, type Context } from "@earendil-works/chord/context";
import type { ClientIdentity, Models, Provider } from "@earendil-works/pi-ai";
import { createModels } from "@earendil-works/pi-ai/models";
import {
  AgentDoc, type AgentState, type CompactionSettings, type ConversationId,
  type ConversationRecord, createRegistry, defineDoc, defineDocFamily,
  defineExtension, type EnvTarget, type Extension, Harness, type HarnessSettings, hook,
  ProviderDoc, type ReportInfo, ROOT_CONVERSATION_ID,
  type TaskRecord, ToolTask, type Tx, type UsageState, type Storage, type Wake, wrapTool,
} from "@earendil-works/pi-durable";
import type { ExecutionEnv } from "@earendil-works/pi-durable/env";
import type { ToolExecutionApi } from "@earendil-works/pi-durable";
import { CodingTools } from "@earendil-works/pi-durable/tools";

import {
  agentId, type Authority, AUTHORITY_RULE, authorityOf, type AuthorityState, MODEL_TOOLS,
  type Need, NO_CONVERSATION, offersFive, refusalCode, type Stamped, stamped, stampOf, TA_OP,
  TOOL_SURFACE,
} from "./authority";
import { createChild, delegationsDoc, subagentRequest } from "./delegate";
import { Invalid, Refused } from "./errors";
import type { Files } from "./files";
import { type Access, OwnerFilesEnv } from "./files-env";
import type { Write } from "./gate";
import type { GatewayProvider, SessionAuthority } from "./gateway-provider";
import type { LiveTee } from "./live-tee";
import type { Notices } from "./notices";
import { type MetricsSnapshot, OriginMetrics, originRoot, threadOf, turnOf } from "./origin";
import { AGENTS_DIR, AGENTS_OWNER_ONLY, within, WORKSPACE } from "./paths";
import { RemoteStorage } from "./remote-storage";
import { type PackageSelection, Resources } from "./resources";
import { inTime } from "./schedule";
import {
  createScheduled, type Delivered, requestOf, SCHEDULED, type ScheduledInput,
  type ScheduledTask, scheduledTask, standing, type Standing, WORK_ENTRY,
} from "./scheduled";
import { type Delegation, PLATFORM_NAMES, type Platform, reachable, taTool } from "./ta";
import {
  HOST_TIMING, type HostParts, type HostTiming, type Opened, type OperationResult,
  type Reported, RUN_SETTLED, SESSIONS_FULL, type SubmitOptions, THREAD_DROPPED,
  THREAD_INPUTS_FULL, ThreadHost, USAGE_UNKEPT,
} from "./thread-host";
import { type Answer, assistantText, type StoreRpc } from "./thread-store";
import {
  addedUsage, type Settled, type Thread, type ThreadKind, type Threads,
} from "./threads";

const BG = BACKGROUND_CONTEXT;
const ROOT = ROOT_CONVERSATION_ID;

/** The model a new thread starts on: the gateway provider's virtual `auto` (A1). */
export const AUTO = Object.freeze({ provider: "gateway", id: "auto" });

/** How the agent's model requests name the app to providers: TinyAssets, not pi. */
export const IDENTITY: ClientIdentity = Object.freeze({
  attribution: Object.freeze({ name: "TinyAssets", url: "https://tinyassets.io" }),
});

/**
 * How long an idle conversation keeps its last context read in memory (F11). pi's default
 * is ten minutes, for one Session per process; the object holds up to `OPEN_MAX` Sessions
 * in an isolate it shares with other owners' objects. One minute: live probe P4
 * (`store-probe.md`) held 128 Sessions at this setting with no memory pressure.
 */
export const CONTEXT_RETENTION_MS = 60_000;
/** How long a loaded document no watch observes stays in memory (F11); as P4 held it. */
export const DOCUMENT_RETENTION_MS = 60_000;

/** pi's run settings; a function in the options, so it is read at each use. */
export const AGENT_SETTINGS: HarnessSettings = Object.freeze({
  progress: Object.freeze({ partialIntervalMs: 1000, outputIntervalMs: 1000 }),
  afterFailedRun: "withdraw",
  restartLimit: 5,
  contextRetentionMs: CONTEXT_RETENTION_MS,
  documentRetentionMs: DOCUMENT_RETENTION_MS,
});

/** The longest a door waits for the work a change ends; pi finishes the abort past it. */
export const END_WAIT_MS = 5_000;
/** Waits the object keeps at once (A4's bounded waits), past which one is refused. */
export const WAITS_KEPT = 256;
/** Waits one outside client may keep at once. */
export const WAITS_PER_CLIENT = 16;
/** Waits kept for the owner alone: the clients together keep at most the rest. */
export const WAITS_OWNER = 32;
/** A wait a grant or policy change ended. */
export const WAIT_ENDED = "the wait was ended by a grant or policy change";
/** The notice of a change the owner's agent makes to its agent definitions. */
export const AGENT_DEFINITION = "agent_definition";
/** A wait refused because the object keeps `WAITS_KEPT` already. */
export const WAITS_FULL = "too many pending waits";
/** The extension names that are the object's own: no package may take one. */
export const RESERVED_PREFIX = "ta.";
/** The early authority gate's extension name. */
export const GATE = "ta.authority";
/** The extension of the `ta` tool (`ta.ts`). */
export const TA_EXTENSION = "ta.tool";
/** The extension of the scheduled launches' task (`scheduled.ts`). */
export const SCHEDULED_EXTENSION = "ta.scheduled";
/** A scheduled launch whose thread is no longer the one it was made in, as it delivers. */
export const THREAD_AUTHORITY = "thread_authority";
/** A thread with no row is never opened: it was dropped, or never made. */
export const NO_THREAD = "no such thread";
/** The `usage_unkept` notice's detail when a settling run's usage cannot be read. */
const USAGE_UNREAD_DETAIL = "a run's model usage could not be read as it settled; the " +
  "owner's total counts it as its Session last closed";
/** The rule a model call of a dropping thread is refused under: its turns end with its row. */
export const THREAD_DROPPING = "thread_dropping";
/** How long a cut-off Session's usage is waited for, before its row is forgotten. */
const CUT_OFF_USAGE_MS = 1_000;
/** How many of a run's first root submissions its receipt's read looks through. */
const RECEIPT_SCAN = 8;

/**
 * How a run's input settled, as its Session's own storage holds it: its root's first input,
 * and its answer's text once done; null while it has none, or it has not settled.
 */
export async function settledIn(storage: Storage): Promise<Settled | null> {
  const scheduled = (await storage.scanTasks({ conversationId: ROOT, kind: SCHEDULED },
                                             RECEIPT_SCAN, undefined, BG)).items[0];
  if (scheduled !== undefined) {
    return scheduledSettled(storage, scheduled);
  }
  const page = await storage.scanSubmissions({ conversationId: ROOT, order: "ascending" },
                                             RECEIPT_SCAN, undefined, BG);
  const input = page.items.find((record) => record.type === "input");
  if (input === undefined) {
    return null;
  }
  const request = input.requestId ?? null;
  if (input.status === "done") {
    const answer = await storage.entry(input.answer, BG);
    return { request, status: "done", reason: null, text: assistantText(answer?.entry) };
  }
  if (input.status === "unanswered") {
    return { request, status: "unanswered", reason: input.reason, text: "" };
  }
  return null;
}

/**
 * How a scheduled launch's run settled, by its task: under its work id, the reason it ended
 * (refused, cancelled), or, once it completed, how its last delivery was answered; null
 * while the task, or that delivery, is live.
 */
async function scheduledSettled(storage: Storage,
                                record: TaskRecord<JsonValue, JsonValue, JsonValue>):
    Promise<Settled | null> {
  const workId = (record.input as unknown as ScheduledInput).work_id;
  const stands = standing(record);
  if (stands.ended === null) {
    return null;
  }
  if (stands.ended.reason !== null) {
    return { request: workId, status: "unanswered", reason: stands.ended.reason, text: "" };
  }
  const last = await storage.submissionByRequest(ROOT, requestOf(workId, stands.delivered), BG);
  if (last?.type !== "input" || (last.status !== "done" && last.status !== "unanswered")) {
    return null;
  }
  if (last.status === "unanswered") {
    return { request: workId, status: "unanswered", reason: last.reason, text: "" };
  }
  const answer = await storage.entry(last.answer, BG);
  return { request: workId, status: "done", reason: null, text: assistantText(answer?.entry) };
}

/** Whether two stamps are the same authority, field by field. */
export function sameStamp(a: Stamped | null, b: Stamped | null): boolean {
  return a === null || b === null ? a === b
    : a.client === b.client && a.generation === b.generation
      && a.authenticated_at === b.authenticated_at && a.universe === b.universe
      && a.agent === b.agent;
}

export type { Stamped };

/**
 * What each environment access needs of the conversation's authority, each one of the
 * alternatives (`resolve`, a path's canonical form, is a read's or a write's).
 */
const ACCESS_NEEDS: Readonly<Record<Access, readonly Need[]>> = Object.freeze({
  read: Object.freeze([Object.freeze(["read_files"] as const)]),
  write: Object.freeze([Object.freeze(["write_files"] as const)]),
  exec: Object.freeze([null]),
  resolve: Object.freeze([Object.freeze(["read_files"] as const),
                          Object.freeze(["write_files"] as const)]),
});

/** The `ta.authority` document: per conversation, the newest only, copied into a fork. */
export function authorityDoc() {
  return defineDoc<AuthorityState>({
    kind: "ta.authority",
    version: 1,
    scope: "conversation",
    history: "latest",
    fork: "current",
    initial: () => ({ by: "none" }),
    checkpointWhen: () => true,
  });
}

/** `ta.session`: each conversation by its provider session id, as a model call names it. */
export function sessionDoc() {
  return defineDocFamily<{ conversation: number }, number>({
    kind: "ta.session",
    version: 1,
    scope: "session",
    family: true,
    initial: (conversation) => ({ conversation }),
    checkpointWhen: () => true,
  });
}

/** How a thread's root is configured: its agent's data files, as pi's agent takes them. */
export interface AgentConfig {
  /** The agent's instructions (`AGENTS.md`), rendered after every extension's section. */
  readonly instructions?: string;
}

/** How a thread is made: what it is for, and what its stamp needs where it acts. */
export interface ThreadOptions {
  readonly kind?: ThreadKind;
  /** What the stamp must hold where the thread acts, checked before and when it is recorded. */
  readonly need?: Need;
  /** A scheduled launch's work id, which its run's row names from the start. */
  readonly work?: string;
}

/** Who a conversation's file changes are by, in the history. */
export function writerOf(authority: Readonly<AuthorityState> | undefined): string {
  const client = stampOf(authority)?.client;
  return client === undefined ? "agent" : `client:${client}`;
}

/** An operation's status, from pi's submission record: pending, or how it settled. */
export interface AgentStatus {
  readonly operation_id: string;
  readonly status: OperationResult["status"];
  readonly reason?: string;
  readonly text?: string;
}

/** A report's notice detail: what pi says happened, and the error's message. */
function detailOf(error: unknown, info: ReportInfo): string {
  const parts: string[] = [];
  if ("reason" in info) {
    parts.push(info.reason);
  }
  if ("extension" in info && info.extension !== undefined) {
    parts.push(`extension ${info.extension}`);
  }
  if ("task" in info && info.task !== undefined) {
    parts.push(`task ${info.task.kind}`);
  }
  if ("callback" in info) {
    parts.push(`callback ${info.callback}`);
  }
  parts.push(error instanceof Error ? error.message : String(error));
  return parts.join("; ");
}

/** What the object builds its agent from. */
export interface AgentParts {
  readonly files: Files;
  readonly notices: Notices;
  /** The object's write gate. */
  readonly write: Write;
  /** The object's `Schedule.sync()`: a changed file's housekeeping is scheduled. */
  readonly rearm: () => Promise<void>;
  /** The part of owner policy that is pi's settings, read at each use. */
  readonly policy: () => HarnessSettings;
  /** The object's model provider: `gateway`, over the owner's connections. */
  readonly provider: GatewayProvider;
  /** The live tee the provider publishes every model event to, for the owner's views. */
  readonly tee: LiveTee;
  /** The authority checks, over the object's grants. */
  readonly authority: Authority;
  /** The object's threads. */
  readonly threads: Threads;
  /** What `ta`'s platform capabilities run on. */
  readonly platform: Platform;
  /** A thread's store, by the name its row holds (`PISTORE`). */
  readonly stores: (name: string) => StoreRpc;
  /** An agent's configuration, from its data files, read when its thread's root is made. */
  readonly config: (agent: string) => Promise<AgentConfig>;
  /** Keep a thread's wake in its live row (`threads.ts`), in a unit of the write gate. */
  readonly live: (thread: string, wake: Wake) => Promise<void>;
  /** Keep a thread's usage as its Session closes (`threads.ts`), in a unit of the gate. */
  readonly usage: (thread: string, usage: UsageState) => Promise<void>;
  /** Keep a thread's turn owners (`Usage.turnOwners`, `Usage.turnsReconciled`), in units. */
  readonly turns: HostParts["turns"];
  /**
   * The owner's admission's `require_work` refusal, or null when it is admitted: a read, run
   * in a scheduled delivery's unit (`admission.ts`).
   */
  readonly admission: () => string | null;
  /** The owner's package selection, as policy holds it now (`policy.ts`, `packages`). */
  readonly packages: () => readonly PackageSelection[];
  /**
   * The refusal of an input joining `turn` (`turn_ended`: it has no row, `Usage.turnLive`),
   * or null while it has one: a read, run in a linked admission's unit.
   */
  readonly joined: (turn: string) => string | null;
}

/** How a scheduled launch stands, as its store holds it: its task, and its last delivery. */
export interface ScheduledStatus {
  readonly standing: Standing;
  /** How its last delivery stands, once it made one. */
  readonly last: Answer | null;
}

/** What a Session's models are bound to: its thread, and its own authority check. */
export interface SessionBinding {
  readonly thread: string;
  readonly authority: SessionAuthority;
}

/** Besides the object's parts: the model catalog, and the packages after the object's own. */
export interface AgentSource {
  /** One Session's models, bound to it. */
  readonly models: (session: SessionBinding) => Models;
  /** pi's compaction settings for the catalog's models, read at each use; none: pi's own. */
  readonly compaction?: () => CompactionSettings;
  /**
   * Extensions installed under the package rules (`packageRefusal`): a test's. Production
   * passes none (`gatewayAgent`): no package code runs in the object, a package being data
   * (`resources.ts`), its code refused.
   */
  readonly packages: readonly Extension[];
  /**
   * Further extensions of the object's own, installed before the packages and without
   * their rules: none in production (`gatewayAgent`); a test's probes.
   */
  readonly trusted?: readonly Extension[];
  /** The thread host's timings: production's, or a test's shorter ones. */
  readonly timing?: HostTiming;
  /**
   * pi's clock (`HarnessOptions.now`), on which sleeps are stored: production's `Date.now`,
   * or a test's that moves days ahead.
   */
  readonly now?: () => number;
}

/**
 * A package as it is checked and installed: each field read once, its lists copied and its
 * tools and wraps copied whole, all frozen.
 */
export function frozenPackage(extension: Extension): Extension {
  const { name, tools, sections, hooks, wraps, tasks } = { ...extension };
  return Object.freeze(defineExtension({
    name,
    ...(tools === undefined ? {} : { tools: Object.freeze(tools.map((tool) =>
      Object.freeze({ ...tool }))) }),
    ...(sections === undefined ? {} : { sections: Object.freeze([...sections]) }),
    ...(hooks === undefined ? {} : { hooks: Object.freeze([...hooks]) }),
    ...(wraps === undefined ? {} : { wraps: Object.freeze(wraps.map((wrap) =>
      Object.freeze({ ...wrap }))) }),
    ...(tasks === undefined ? {} : { tasks: Object.freeze([...tasks]) }),
  }));
}

/**
 * Why a package may not be installed, or undefined: a `ta.` name is the object's; a tool
 * it provides or wraps may not take a model tool's name, a platform capability's or a `ta:`
 * name (it would shadow it); and each tool it provides must be callable by tools, since
 * the model is offered only the five and `ta` is the one way to the rest (one that is not
 * would sit installed and never run).
 */
export function packageRefusal(extension: Extension): string | undefined {
  if (String(extension.name).startsWith(RESERVED_PREFIX)) {
    return `the ${RESERVED_PREFIX} names are the object's own`;
  }
  const taken = (name: string) => MODEL_TOOLS.includes(name) || PLATFORM_NAMES.includes(name)
    || name.startsWith(TA_OP);
  for (const tool of extension.tools ?? []) {
    if (taken(tool.name)) {
      return `a package tool may not take the name ${tool.name}`;
    }
    if (!reachable(tool)) {
      return `the tool ${tool.name} is not callable by tools, so nothing could call it`;
    }
  }
  for (const wrap of extension.wraps ?? []) {
    if ("tool" in wrap && taken(wrap.tool)) {
      return `a package may not wrap ${wrap.tool}`;
    }
  }
  return undefined;
}

/**
 * Whether a conversation's stored extension selection (pi's `AgentDoc`) keeps the gate:
 * unset is the host's default, every installed extension, the gate first.
 */
export function keepsGate(selection: AgentState["extensions"]): boolean {
  if (selection === undefined) {
    return true;
  }
  return Array.isArray(selection) ? selection.includes(GATE)
    : !(selection.remove ?? []).includes(GATE);
}

/**
 * Whether a conversation's stored model tools (pi's `AgentDoc`) are exactly the five the
 * object fixes in every creating commit: anything else was widened past it.
 */
export function keepsFive(tools: AgentState["modelTools"]): boolean {
  return Array.isArray(tools) && tools.length === MODEL_TOOLS.length && offersFive(tools);
}

/** An authority as plain JSON: a copy of a document's draft, which is no plain object. */
function copy(state: Readonly<AuthorityState>): AuthorityState {
  return JSON.parse(JSON.stringify(state)) as AuthorityState;
}

/** Whether two authorities are the same. */
function same(a: Readonly<AuthorityState>, b: Readonly<AuthorityState>): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export class OwnerAgent {
  /** The Lifecycle capability the object installs: every thread's Session. */
  readonly host: ThreadHost;
  /** The `ta.authority` document, as every Session reads and writes it. */
  readonly authority = authorityDoc();
  /** The `ta.session` documents, as every Session reads and writes them. */
  readonly sessions = sessionDoc();
  /** pi's and pi-ai's metrics, kept by the telemetry adapter for the object's instance. */
  readonly metrics = new OriginMetrics();
  /** The `ta.delegations` document: each Session's live subagents (`delegate.ts`). */
  readonly delegations = delegationsDoc();
  /** The skills, templates and packages, from the owner's files (`resources.ts`). */
  readonly resources: Resources;
  readonly #parts: AgentParts;
  readonly #source: AgentSource;
  /** One registry for every Session (pi keeps each Harness subscribed until it closes). */
  readonly #registry = createRegistry();
  #installed: Promise<void> | undefined;
  /** The drops in flight, by thread, on the instance: one each at a time. */
  readonly #drops = new Map<string, Promise<void>>();
  /** The pending waits, by the client they are for ("" for the owner's), on the instance. */
  readonly #waits = new Map<string, Set<AbortController>>();
  /** The scheduled launches' task, delivering through this agent (`#deliver`). */
  readonly #scheduled: ScheduledTask = scheduledTask({
    deliver: (input, n, signal) => this.#deliver(input, n, signal),
    made: (input, n) => this.#made(input, n),
  });

  constructor(parts: AgentParts, source: AgentSource) {
    this.#parts = parts;
    this.#source = source;
    this.resources = new Resources({
      files: parts.files, notices: parts.notices, write: parts.write,
      selection: () => parts.packages(),
      install: (extension) => this.#registry.install(extension),
    });
    this.host = new ThreadHost({
      open: (thread, onWake) => this.#open(thread, onWake),
      exists: (thread) => parts.threads.get(thread) !== null,
      admission: () => parts.admission(),
      liveRow: (thread) => {
        const row = parts.threads.liveRow(thread);
        return row === null ? null
          : { active: row.active, wakeAt: row.wake_at === null ? null : row.wake_at * 1000 };
      },
      now: () => (source.now ?? Date.now)(),
      settled: (thread) => parts.threads.settledRun(thread),
      live: (thread, wake, from) => this.#live(thread, wake, from),
      usage: (thread, usage) => parts.usage(thread, usage),
      turns: parts.turns,
      notice: (type, detail) => this.#report(type, detail),
    }, source.timing ?? HOST_TIMING);
  }

  /**
   * Open `thread`'s Session over its store, as its row names it: the registry installed
   * (once), its models bound to it, and its root made when absent, configured from its
   * agent's data files, with its stamp written in the root's creating commit.
   */
  async #open(thread: string, onWake: (wake: Wake) => void): Promise<Opened> {
    const row = this.#parts.threads.get(thread);
    if (row === null) {
      throw new Error(NO_THREAD);
    }
    // The store opens while the registry installs: a new thread's store opens cold in about
    // 0.3 s (live probe P2), and neither waits on the other.
    const [installing, opening] = await Promise.allSettled([
      this.#ready(), RemoteStorage.open(() => this.#parts.stores(row.store))]);
    if (installing.status === "rejected") {
      if (opening.status === "fulfilled") {
        await opening.value.close(BG);
      }
      throw installing.reason;
    }
    if (opening.status === "rejected") {
      throw opening.reason;
    }
    const storage = opening.value;
    let opened: Harness | undefined;
    const models = this.#source.models({
      thread,
      // A dropping thread's turns count until its row goes, but no call of one is sent.
      authority: async (sessionId) => {
        const rule = opened === undefined ? () => NO_CONVERSATION
          : await this.modelAuthority(opened, sessionId);
        return () => (this.#parts.threads.get(thread) === null ? THREAD_DROPPING : rule());
      },
    });
    // An opened Harness closes its storage; one that failed to open may not have, so its
    // storage is closed here (twice is once), releasing the store's session.
    const pi = await Harness.open(storage, {
      models: () => models,
      registry: this.#registry,
      onWake,
      identity: IDENTITY,
      settings: () => ({ ...this.#parts.policy(), ...this.#compaction(), ...AGENT_SETTINGS }),
      env: (target, envContext) => this.#env(target, envContext),
      conversationCreated: (tx, record) => this.#inherit(tx, record),
      telemetry: originRoot(thread, this.metrics),
      onReport: (error, info) => this.#report(info.type, detailOf(error, info)),
      ...(this.#source.now === undefined ? {} : { now: this.#source.now }),
    }, BG).catch(async (error: unknown) => {
      await storage.close(BG);
      throw error;
    });
    opened = pi;
    try {
      if (await storage.conversation(ROOT, BG) === undefined) {
        const config = await this.#parts.config(row.agent_id);
        const state = copy(this.#parts.threads.authority(row));
        await pi.root(BG, {
          agent: { model: { provider: AUTO.provider, modelId: AUTO.id },
                   ...(config.instructions === undefined ? {}
                     : { instructions: config.instructions }) },
          init: async (tx, made) => {
            Object.assign(await tx.doc(this.authority, made), state);
          },
        });
      }
    } catch (error) {
      await pi.close(BG).catch(() => {});
      throw error;
    }
    return { pi, storage };
  }

  /** The registry installed, once per instance (again after an install that failed). */
  #ready(): Promise<void> {
    this.#installed ??= this.#install().catch((error: unknown) => {
      this.#installed = undefined;
      throw error;
    });
    return this.#installed;
  }

  /**
   * The owner's own input as pi's `prompt` expands it (`Resources.expand`): `/skill:name
   * args` and `/template args`, at the doors; anything else as typed.
   */
  async expand(text: string): Promise<string> {
    if (!text.startsWith("/")) {
      return text;
    }
    await this.#ready();
    return this.resources.expand(text);
  }

  /** The owner's policy was written: its package selection may have moved. */
  policyChanged(): void {
    this.resources.selectionChanged();
  }

  /** The model source's compaction settings, when it has any. */
  #compaction(): Pick<HarnessSettings, "compaction"> {
    const compaction = this.#source.compaction;
    return compaction === undefined ? {} : { compaction: compaction() };
  }

  /**
   * The object's extensions on its registry: the authority gate first, pi's coding tools,
   * `read` marked replay-safe, then the source's packages, each one named as the object's
   * or one pi refuses skipped and kept as a notice.
   */
  async #install(): Promise<void> {
    const read = CodingTools.tools?.find((tool) => tool.name === "read");
    if (read === undefined) {
      throw new Error("pi's coding tools have no read tool");
    }
    this.#registry.install(defineExtension({
      name: GATE,
      hooks: [hook(ToolTask, {
        beforeTool: async (call, api, context) => {
          const state = await api.snapshot(this.authority, api.conversationId, context);
          return this.#parts.authority.decide(state, call);
        },
      })],
    }));
    this.#registry.install(CodingTools);
    this.#registry.install(defineExtension({
      name: "ta.replay",
      wraps: [wrapTool(read, (tool) => ({ ...tool, replay: "safe" }))],
    }));
    this.#registry.install(defineExtension({
      name: TA_EXTENSION,
      tools: [taTool({
        authority: this.#parts.authority, platform: this.#parts.platform,
        write: this.#parts.write,
        state: (api, context) => api.snapshot(this.authority, api.conversationId, context),
        delegate: (state, delegation, api, context) =>
          this.#delegate(state, delegation, api, context),
      })],
    }));
    this.#registry.install(defineExtension({ name: SCHEDULED_EXTENSION,
                                             tasks: [this.#scheduled] }));
    // The skills section, built from the owner's files and installed in place here; its
    // rebuilds install it again under its name, where it is.
    await this.resources.ready();
    for (const extension of this.#source.trusted ?? []) {
      this.#registry.install(extension);
    }
    for (const extension of this.#source.packages) {
      let name = "?";
      try {
        // What is checked is what is installed: a frozen copy, so no getter or later change
        // of the package can bring in a tool the check never saw.
        const snapshot = frozenPackage(extension);
        name = String(snapshot.name);
        const refused = packageRefusal(snapshot);
        if (refused !== undefined) {
          throw new Error(refused);
        }
        this.#registry.install(snapshot);
      } catch (error) {
        const why = error instanceof Error ? error.message : String(error);
        await this.#parts.write(() => this.#parts.notices.once(
          "extension_skipped", `${name}: ${why}`, Date.now() / 1000));
      }
    }
  }

  /**
   * `ta`'s `agent:delegate`: a subagent of the calling conversation (`delegate.ts`), made or
   * found again in the call's own commit, configured as the agent named (its `AGENTS.md`) or
   * as the caller, given `task` as its one input and waited on. Under the caller's
   * authority, which must hold `run_graph` where the caller acts (`ta`'s gate and its own
   * check) and, with an agent named, where that agent acts, so a scoped client delegates
   * only to an agent it may run. The input is admitted as a door's (`ThreadHost.linked`),
   * in a unit that re-checks both and refuses it when the call's turn has no row; once
   * answered, the child's lingering work (a background compaction) is ended, so the thread's
   * live conversations stay within their cap. Its answer: the child's conversation, and its
   * text, or why it went unanswered.
   */
  async #delegate(state: Readonly<AuthorityState> | undefined, delegation: Delegation,
                  api: ToolExecutionApi, context: Context): Promise<unknown> {
    const thread = threadOf(context.telemetry);
    if (thread === undefined) {
      throw new Error("a delegation runs in a thread's Session");
    }
    const turn = turnOf(context.telemetry);
    const named = delegation.agent === undefined ? null : agentId(delegation.agent);
    const there: Readonly<AuthorityState> | undefined = named === null
      || state?.by !== "client" ? state : { by: "client", stamp: { ...state.stamp, agent: named } };
    const { authority, write } = this.#parts;
    authority.require(there, ["run_graph"]);
    const instructions = named === null ? null : (await this.#parts.config(named)).instructions;
    const child = await api.commit((tx) => createChild(tx, api.taskId, instructions,
                                                       this.delegations), context);
    await api.details({ conversation_id: Number(child) }, context);
    const handle = await api.conversation(child, context);
    if (handle === undefined) {
      throw new Error("a subagent's conversation is gone");
    }
    const requestId = subagentRequest(api.taskId);
    const submission = await this.host.linked(thread, { conversation: child, requestId },
      () => write(() => {
        authority.require(state, ["run_graph"]);
        authority.require(there, ["run_graph"]);
        const ended = this.#parts.joined(turn);
        if (ended !== null) {
          throw new Refused(ended);
        }
      }),
      () => handle.submit({ type: "input", content: delegation.task, requestId }, context));
    const settled = await submission.wait(context);
    await handle.abort(context, { background: true });
    if (settled.status === "unanswered") {
      return { conversation_id: Number(child), status: "unanswered", reason: settled.reason };
    }
    const answer = settled.type !== "input" ? undefined
      : await this.host.use(thread, ({ storage }) => storage.entry(settled.answer, context));
    return { conversation_id: Number(child), status: "done",
             text: assistantText(answer?.entry) };
  }

  /**
   * One environment per call, replays included, over the owner's files, as the
   * conversation's writer, holding every operation to the conversation's authority.
   */
  async #env({ conversationId, cwd, read }: EnvTarget, context: Context): Promise<ExecutionEnv> {
    const state = await read.snapshot(this.authority, conversationId, context);
    await this.#parts.files.namespace();  // made through the gate, so the env reads it
    return new OwnerFilesEnv({
      files: this.#parts.files, writer: writerOf(state), cwd: cwd ?? WORKSPACE,
      rearm: this.#parts.rearm,
      authorize: (access) => this.#parts.authority.requireOne(state, ACCESS_NEEDS[access]),
      guard: (path) => this.#definitions(state, path),
    });
  }

  /**
   * In a change's own unit: the owner's agent definitions change only under the owner's own
   * authority, and the owner is told each time its agent changes them, since content another
   * party wrote elsewhere can steer the agent (Direction 3 lets it write there). A refused
   * change leaves no notice.
   */
  #definitions(state: Readonly<AuthorityState> | undefined, path: string): void {
    if (!within(path, AGENTS_DIR)) {
      return;
    }
    if (state?.by !== "owner") {
      throw new Refused(AGENTS_OWNER_ONLY);
    }
    this.#parts.notices.add(AGENT_DEFINITION, `the owner's agent changes ${path}`,
                            Date.now() / 1000);
  }

  /**
   * In every creating commit: the model is offered exactly `MODEL_TOOLS`, over whatever
   * pi copied from an owner conversation or a fork's entry; a task's new conversation takes
   * its owner conversation's authority, which a fork's copy must agree with; and each
   * conversation's provider session is recorded. The root's authority is its thread's
   * stamp, which `root`'s `init` writes after this, in the same commit.
   */
  async #inherit(tx: Tx, record: ConversationRecord): Promise<void> {
    (await tx.doc(AgentDoc, record.id)).modelTools = [...MODEL_TOOLS];
    const mine = await tx.doc(this.authority, record.id);  // explicit: none, until given one
    if (record.owner !== undefined) {
      const from = await tx.doc(this.authority, record.owner.conversationId);
      if (record.parent !== undefined && !same(mine, from)) {
        throw new Error("a fork's authority disagrees with its owner conversation's");
      }
      Object.assign(mine, copy(from));  // over the initial none, or an equal copy
    }
    const { sessionId } = await tx.doc(ProviderDoc, record.id);
    await tx.doc(this.sessions, sessionId, record.id);
  }

  /** Keep a notice, in a unit of its own; pi drops a failed report, so it is logged. */
  #report(type: string, detail: string): void {
    this.#parts.write(() => this.#parts.notices.add(type, detail, Date.now() / 1000))
      .catch(() => console.error("an agent notice could not be kept"));
  }

  /**
   * Make a thread for `agent`, under `stamp` (null: the owner's): its row, naming a fresh
   * store, recorded as `kind` in a unit that re-checks the stamp still stands and holds
   * `need` where the thread acts. Its Session, store and root are made at its first open.
   */
  async thread(agent: unknown, stamp: unknown, options: ThreadOptions = {}): Promise<Thread> {
    const id = agentId(agent);
    const by = stamped(stamp);
    if (by !== null && by.agent !== id) {
      throw new Invalid("a thread's stamp names its own agent");
    }
    const { authority, threads, write } = this.#parts;
    const { kind = "run", need = [], work = null } = options;
    const state = authorityOf(by);
    authority.require(state, need);
    threads.room(by, kind, work !== null);  // a refusal makes nothing; the unit checks again
    return write(() => {
      authority.require(state, need);
      return threads.record(id, by, Date.now() / 1000, kind, work);
    });
  }

  /**
   * Make a scheduled launch in its run thread (`scheduled.ts`): its `ta.scheduled` task, in
   * the thread's root, inside the thread's wake, so its job is set before the commit. The
   * thread was recorded first, naming its work and its store.
   */
  async schedule(thread: Thread, input: ScheduledInput): Promise<void> {
    if (input.thread !== thread.thread_id || thread.request !== input.work_id) {
      throw new Error("a scheduled launch is made in the run its row names");
    }
    await this.host.admitted(thread.thread_id, async ({ pi }) => {
      const root = await pi.conversation(ROOT, BG);
      if (root === undefined) {
        throw new Error("a thread's Session has no root");
      }
      await createScheduled(root, this.#scheduled, input);
    });
  }

  /** Whether occurrence `n` of a scheduled launch was submitted: pi holds its request id. */
  async #made(input: ScheduledInput, n: number): Promise<boolean> {
    const record = await this.host.use(input.thread, ({ storage }) =>
      storage.submissionByRequest(ROOT, requestOf(input.work_id, n), BG));
    return record !== undefined;
  }

  /**
   * In a unit: how occurrence `n`'s delivery stands under the owner's rows now. Refused for
   * good when its thread is gone or dropping, is not the one it was made in under the same
   * stamp, or its stamp no longer stands or holds `run_graph`; held, to try again, while the
   * owner is not admitted (`require_work`: an erased owner's object runs nothing, so this is
   * a suspension) or the run policy as it stands has no room for it
   * (`Threads.liveRefusal`); else null.
   */
  #deliveryCheck(input: ScheduledInput): Exclude<Delivered, { kind: "delivered" }> | null {
    const { threads, authority } = this.#parts;
    const row = threads.get(input.thread);
    if (row === null) {
      return { kind: "refused", reason: NO_THREAD };  // dropping: its Session ends with it
    }
    const state = threads.authority(row);
    if (row.request !== input.work_id || !sameStamp(stampOf(state), input.stamp)) {
      return { kind: "refused", reason: THREAD_AUTHORITY };
    }
    const unadmitted = this.#parts.admission();
    if (unadmitted !== null) {
      return { kind: "held", reason: refusalCode(unadmitted) };
    }
    const refused = authority.refusal(state, ["run_graph"]);
    if (refused !== undefined) {
      return { kind: "refused", reason: refused };
    }
    const room = threads.liveRefusal(row);
    return room === null ? null : { kind: "held", reason: refusalCode(room) };
  }

  /**
   * Deliver occurrence `n` of a scheduled launch, from its task's `deliver` phase. A
   * delivery its request id finds already made (an eviction between its submit and the
   * phase's commit) is pi's: it is not checked again, and its submit is pi's duplicate.
   * Otherwise it is submitted through the host's admission, which holds it to the thread's
   * open inputs, and whose `admitted` step, right before pi commits it, stops on the
   * task's abort signal (a cancel) and re-checks, in one unit, what `#deliveryCheck`
   * reads. A refusal for good ends the task; one held (a suspension, no room in the run
   * policy or the thread) sleeps again, backing off, so the schedule survives it.
   */
  async #deliver(input: ScheduledInput, n: number, signal: AbortSignal): Promise<Delivered> {
    const request = requestOf(input.work_id, n);
    const made = await this.#made(input, n);
    let checked: Delivered | null = null;
    const admitted = made ? undefined : async () => {
      if (signal.aborted) {
        throw signal.reason ?? new Error("the delivery was cancelled");
      }
      checked = await this.#parts.write(() => this.#deliveryCheck(input));
      if (checked !== null) {
        throw new Refused(checked.reason);
      }
    };
    try {
      await this.host.submit(input.thread, input.input, {
        operationId: request, whenBusy: "followUp",
        entry: { kind: WORK_ENTRY, data: { work_id: input.work_id,
                                           client: input.stamp?.client ?? null } },
        ...(admitted === undefined ? {} : { admitted }),
      });
    } catch (error) {
      if (checked !== null) {
        return checked;
      }
      if (signal.aborted) {
        throw error;
      }
      if (error instanceof Refused
          && (error.message === THREAD_INPUTS_FULL || error.message === SESSIONS_FULL)) {
        return { kind: "held", reason: refusalCode(error.message) };
      }
      if (error instanceof Refused
          && (error.message === RUN_SETTLED || error.message === THREAD_DROPPED)) {
        return { kind: "refused", reason: refusalCode(error.message) };
      }
      if (error instanceof Refused && error.message === this.#parts.admission()) {
        return { kind: "held", reason: refusalCode(error.message) };
      }
      throw error;
    }
    return { kind: "delivered" };
  }

  /**
   * How a scheduled launch of `thread` stands, by a pure read of its store
   * (`ThreadStore.task`, then `answer` for its last delivery): no Session opened, no
   * holder claimed. Null once its row or store is gone, the store does not answer in time,
   * or it holds no such task.
   */
  async scheduledStatus(thread: Thread, workId: string): Promise<ScheduledStatus | null> {
    const row = this.#parts.threads.row(thread.thread_id);
    if (row === null) {
      return null;
    }
    const store = this.#parts.stores(row.store);
    const read = (async () => {
      const record = await store.task(SCHEDULED, workId);
      if (record === null) {
        return null;
      }
      const stands = standing(JSON.parse(record) as TaskRecord<JsonValue, JsonValue,
                                                                 JsonValue>);
      const last = stands.delivered === 0 ? null
        : await store.answer(requestOf(workId, stands.delivered));
      return { standing: stands, last };
    })().catch(() => null);
    const answered = await inTime(read, END_WAIT_MS);
    return answered === "late" ? null : answered;
  }

  /**
   * Cancel a scheduled launch of `thread`: its task aborted (`abortTask`, at once from a
   * sleep) and waited on until it ends, which says truly how many occurrences it delivered
   * (one being delivered as the cancel lands, too: its abort handler asks pi); then the
   * last of them withdrawn, or the run it joined aborted. Whether either did anything.
   */
  cancelScheduled(thread: Thread, workId: string): Promise<boolean> {
    return this.host.use(thread.thread_id, async ({ pi, storage }) => {
      const record = (await storage.scanTasks({ conversationId: ROOT, kind: SCHEDULED },
                                              RECEIPT_SCAN, undefined, BG)).items
        .find((task) => (task.input as unknown as ScheduledInput | null)?.work_id === workId);
      if (record === undefined) {
        return false;
      }
      let stands = standing(record);
      let aborted = false;
      if (stands.ended === null) {
        aborted = await pi.abortTask(record.id, BG) === "marked";
        stands = standing(await pi.waitForTask(record.id, BG));
      }
      const withdrawn = stands.delivered > 0
        && await this.host.withdraw(thread.thread_id, requestOf(workId, stands.delivered));
      return aborted || withdrawn;
    });
  }

  /** `thread`'s Session, opened if it is not. */
  pi(thread: Thread | string): Promise<Harness> {
    return this.host.pi(typeof thread === "string" ? thread : thread.thread_id);
  }

  /**
   * Submit `input` to a thread's root (`ThreadHost.submit`); a run is first told, in a unit,
   * the request id of the input it is handed, so its receipt names it however it ends.
   */
  async submit(thread: Thread, input: string, options: SubmitOptions):
      Promise<{ operationId: string; accepted: boolean }> {
    if (thread.kind === "run") {
      await this.#parts.write(() => this.#parts.threads.handed(thread.thread_id,
                                                               options.operationId));
    }
    return this.host.submit(thread.thread_id, input, options);
  }

  /**
   * How a run's input `request` stands in its store, its whole answer once done, while the
   * store is there: a pure read (`ThreadStore.answer`), never a Session's open or a holder's
   * claim; null once its row or store is gone, or the store does not answer in time.
   */
  async answer(threadId: string, request: string): Promise<Answer | null> {
    const row = this.#parts.threads.row(threadId);
    if (row === null) {
      return null;
    }
    const read = this.#parts.stores(row.store).answer(request).catch(() => null);
    const answered = await inTime(read, END_WAIT_MS);
    return answered === "late" ? null : answered;
  }

  /** Withdraw an operation of a thread, or abort the run it joined. */
  cancel(thread: Thread, operationId: string): Promise<boolean> {
    return this.host.withdraw(thread.thread_id, operationId);
  }

  /**
   * Drop a thread its row marks `dropping` (`threads.ts`), for whatever reason, the one
   * path: its Session's live views closed and its work ended, background work included,
   * then the Session closed (`ThreadHost.drop`: never opened for it), for at most
   * `END_WAIT_MS`, past which a Session that will not close is cut off (its store, once
   * wiped, refuses its late calls: it is no longer opened); its store destroyed; then, in
   * one unit, its row forgotten with every row keyed to it, its usage added into the
   * owner's total, and its wake job. A run's receipt was kept when it settled, or at its
   * mark; nothing reads its store. Each step is safe to run again, so a drop cut short is
   * finished by running it again (the `threads` lane, the erasure); one in flight for a
   * thread is joined, never run twice at once.
   */
  drop(thread: Thread): Promise<void> {
    const id = thread.thread_id;
    const running = this.#drops.get(id);
    if (running !== undefined) {
      return running;
    }
    const dropping = this.#drop(thread).finally(() => {
      this.#drops.delete(id);
    });
    this.#drops.set(id, dropping);
    return dropping;
  }

  async #drop(thread: Thread): Promise<void> {
    const { threads, write } = this.#parts;
    const id = thread.thread_id;
    if (threads.row(id)?.state !== "dropping") {
      throw new Error("only a thread marked dropping is dropped");
    }
    let ended: Harness | undefined;
    const ending = this.host.drop(id, ({ pi }) => {
      ended = pi;
      return this.#endSession(pi, true);
    });
    if (await inTime(ending, END_WAIT_MS) === "late") {
      ending.catch(() => undefined);
      console.error("a dropped thread's Session did not close in time; it is cut off");
      // Its usage as it stands, kept now: it closes, if it does, after its row is gone.
      const usage = ended?.usage(BG).catch(() => undefined);
      const read = usage === undefined ? undefined : await inTime(usage, CUT_OFF_USAGE_MS);
      if (read !== undefined && read !== "late") {
        await this.#parts.usage(id, read).catch(() => console.error(
          "a cut-off Session's usage could not be kept"));
      }
    }
    await this.#parts.stores(thread.store).destroy();
    const [rearmed] = await write((): [Promise<unknown>] | [] =>
      (threads.forget(id) ? [this.host.forgetWake(id)] : []));
    await rearmed;
  }

  /**
   * A Session's wake, kept in its live row; for a run not yet settled whose Session reports
   * its work done (nothing running, nothing asleep), its input's settling is read from the
   * Session that reported it and kept, with the time, in the unit that forgets the wake
   * (`Threads.settle`). A read that fails keeps the wake alone, loudly: the run then counts
   * as one never handed an input.
   */
  async #live(thread: string, wake: Wake, from: Reported | undefined): Promise<void> {
    const { threads, write } = this.#parts;
    if (wake.active || wake.at !== undefined || from === undefined
        || !threads.unsettledRun(thread)) {
      return this.#parts.live(thread, wake);
    }
    let settled: Settled | null;
    try {
      settled = await settledIn(from.opened.storage);
    } catch {
      console.error("a run's settling could not be read");  // no ids
      settled = null;
    }
    if (settled === null) {
      return this.#parts.live(thread, wake);
    }
    // Its usage as its Session holds it now goes into the owner's total as it settles; a
    // read that fails leaves the usage it last kept, told loudly.
    const usage = await from.opened.pi.usage(BG).catch(() => {
      console.error("a settling run's usage could not be read");  // no ids
      this.#report(USAGE_UNKEPT, USAGE_UNREAD_DETAIL);
      return undefined;
    });
    const kept = settled;
    const done = await write(() => {
      if (!from.quiet()) {
        return false;  // an input is being admitted: the run works again
      }
      threads.settle(thread, kept, Date.now() / 1000,
                     usage === undefined ? null : JSON.stringify(usage));
      return true;
    });
    if (!done) {
      return this.#parts.live(thread, wake);
    }
  }

  /**
   * The authority a model request of `pi`'s Session runs under, by its provider session: a
   * check, run in the call's reservation and dispatch units, answering the refusal's code
   * or null. A request that names no conversation of the Session is refused
   * (`NO_CONVERSATION`), and a client's or no one's conversation whose stored selection
   * (pi's `AgentDoc`, read only here) drops the gate is refused `AUTHORITY_RULE`, whatever
   * its grant: with its tools held by its environment, such a conversation does nothing.
   * Any conversation whose stored model tools are not exactly `MODEL_TOOLS` (a hook's
   * result `control` can add one) is refused `TOOL_SURFACE`, the owner's included.
   */
  async modelAuthority(pi: Harness, sessionId: string | undefined):
      Promise<() => string | null> {
    if (sessionId === undefined) {
      return () => NO_CONVERSATION;
    }
    const at = await pi.snapshot(this.sessions, sessionId, BG);
    if (at === undefined) {
      return () => NO_CONVERSATION;
    }
    const conversation = at.conversation as ConversationId;
    const state = await pi.snapshot(this.authority, conversation, BG);
    const agent = await pi.snapshot(AgentDoc, conversation, BG);
    // The backstop: a client's or no one's conversation whose selection drops the gate
    // spends nothing; the environment already holds its tools.
    if (state?.by !== "owner" && !keepsGate(agent?.extensions)) {
      return () => AUTHORITY_RULE;
    }
    // Any conversation's, the owner's too, whose stored model tools were widened past the
    // five spends nothing either (the provider also refuses a widened request itself).
    if (!keepsFive(agent?.modelTools)) {
      return () => TOOL_SURFACE;
    }
    return () => this.#parts.authority.modelRule(state);
  }

  /** An operation's status in `thread`, from pi's submission record (one read). */
  async status(thread: Thread, operationId: string): Promise<AgentStatus> {
    const result = await this.host.status(thread.thread_id, operationId);
    return { operation_id: operationId, status: result.status,
             ...(result.reason === undefined ? {} : { reason: result.reason }),
             ...(result.text === undefined ? {} : { text: result.text }) };
  }

  /**
   * Wait for an operation of `client` (null: the owner's) in `thread` to settle, until
   * `signal`; a grant change of that client, or a policy write, ends the wait
   * (`WAIT_ENDED`).
   */
  async wait(thread: Thread, operationId: string, client: string | null,
             signal?: AbortSignal): Promise<OperationResult> {
    const key = client ?? "";
    const ended = this.#keepWait(key);
    try {
      return await this.host.settled(thread.thread_id, operationId,
        signal === undefined ? ended.signal : AbortSignal.any([signal, ended.signal]));
    } catch (error) {
      if (ended.signal.aborted) {
        throw new Refused(WAIT_ENDED);
      }
      throw error;
    } finally {
      const set = this.#waits.get(key);
      set?.delete(ended);
      if (set?.size === 0) {
        this.#waits.delete(key);
      }
    }
  }

  /**
   * A new pending wait of `key`'s, kept on the instance: within `WAITS_KEPT` in all, an
   * outside client's within `WAITS_PER_CLIENT` of its own and, with every other client's,
   * outside the owner's reserve (`WAITS_OWNER`), so no client's waits crowd out another's
   * or the owner's.
   */
  #keepWait(key: string): AbortController {
    const kept = [...this.#waits.values()].reduce((sum, set) => sum + set.size, 0);
    const clients = kept - (this.#waits.get("")?.size ?? 0);
    if (kept >= WAITS_KEPT || (key !== "" && ((this.#waits.get(key)?.size ?? 0)
                                              >= WAITS_PER_CLIENT
                                              || clients >= WAITS_KEPT - WAITS_OWNER))) {
      throw new Refused(WAITS_FULL);
    }
    const ended = new AbortController();
    const set = this.#waits.get(key) ?? new Set<AbortController>();
    this.#waits.set(key, set.add(ended));
    return ended;
  }

  /** How many waits the instance keeps now. */
  waitsKept(): number {
    return [...this.#waits.values()].reduce((sum, set) => sum + set.size, 0);
  }

  /** End the pending waits of `client`, or every one (`undefined`). */
  #endWaits(client?: string): void {
    for (const [key, set] of [...this.#waits]) {
      if (client === undefined || key === client) {
        for (const ended of set) {
          ended.abort();
        }
      }
    }
  }

  /**
   * End one Session's work: the live views of its root and of every conversation with live
   * work closed (`views`), then each of those aborted, background work included (`keep`
   * none: a drop's). A policy write's ending (`keep` `sleepers`) aborts each conversation's
   * ordinary work, a run's own sleeping retry among it, and each background task that is
   * not asleep, never a scheduled launch's: a sleeping background task and a schedule meet
   * the policy as it stands when they wake (`#deliver`). A suspension's (`keep`
   * `scheduled`) aborts all of it, asleep or not, but a scheduled launch's task, whose
   * delivery re-checks the admission. The tasks it aborts are those live as it looks.
   */
  async #endSession(pi: Harness, views: boolean,
                    keep: "none" | "sleepers" | "scheduled" = "none"): Promise<void> {
    const inspection = await pi.inspect(BG);
    const live = new Set<ConversationId>([ROOT,
      ...inspection.tasks.map((task) => task.record.conversationId),
      ...inspection.submissions.map((record) => record.conversationId)]);
    if (views) {
      for (const id of live) {
        const session = (await pi.snapshot(ProviderDoc, id, BG))?.sessionId;
        if (session !== undefined) {
          this.#parts.tee.close(session);
        }
      }
    }
    if (keep === "none") {
      await Promise.all([...live].map(async (id) =>
        (await pi.conversation(id, BG))?.abort(BG, { background: true })));
      return;
    }
    // Each conversation's ordinary work, its queued inputs and runs; then each task that is
    // neither asleep nor a scheduled launch's, background ones aborted by their ids, every
    // one waited on until it is terminal, as an abort across background boundaries waits.
    const ending = inspection.tasks.filter(({ record, state }) =>
      record.kind !== SCHEDULED && (keep === "scheduled" || state.kind !== "sleeping"));
    await Promise.all([...live].map(async (id) => (await pi.conversation(id, BG))?.abort(BG)));
    await Promise.all(ending.filter(({ record }) => record.background)
      .map(({ record }) => pi.abortTask(record.id, BG)));
    await Promise.all(ending.map(({ record }) => pi.waitForTask(record.id, BG)));
  }

  /** Run `ending` for at most `END_WAIT_MS`; pi finishes what is past it on its own. */
  async #within(ending: Promise<unknown>): Promise<void> {
    if (await inTime(ending, END_WAIT_MS) === "late") {
      ending.catch(() => console.error("ending an agent's work failed past its wait"));
    }
  }

  /**
   * A grant change ends its client's work: its pending waits, then each of `threads` (the
   * client's threads, which the change's unit marked dropping) dropped: its live views and
   * work ended, its Session closed, its store wiped, its row forgotten (`drop`), sleepers
   * included, whose sleeping tasks go with their stores. The door waits for that at most
   * `END_WAIT_MS`. A thread whose work could not be ended rejects this, so the door does; a
   * store that did not answer is wiped by the `threads` lane, which finishes every drop.
   */
  async endClient(client: string, threads: readonly Thread[]): Promise<void> {
    this.#endWaits(client);
    await this.#within(Promise.all(threads.map(async (thread) => {
      try {
        await this.drop(thread);
      } catch (error) {
        if (this.host.peek(thread.thread_id) !== undefined) {
          throw error;
        }
        console.error("a dropped thread's store is wiped by the lane");  // no ids
      }
    })));
  }

  /**
   * The owner's erasure began (`erasure.ts`): every pending wait and live view ends; each
   * thread's work then ends with its drop.
   */
  erasing(): void {
    this.#endWaits();
    this.#parts.tee.close();
  }

  /**
   * A policy write ends every thread's running work: every wait, every view, and the work
   * of each thread with work running (its live row active, or an open Session working),
   * opened if need be, but no sleeping task and no scheduled launch (`#endSession`'s
   * `sleepers`): each meets the policy as it stands when it wakes.
   */
  endAll(): Promise<void> {
    const running = new Set([...this.#parts.threads.active(),
      ...this.host.openThreads().filter((thread) => this.host.peek(thread)?.pi.wake().active)]);
    return this.#endThreads([...running], "sleepers");
  }

  /**
   * A suspension ends the work of every thread with a live row, working or asleep (a run in
   * pi's retry backoff or deferred poll among them), and of each open Session working:
   * everything but the scheduled launches' tasks, whose deliveries are held while the owner
   * is suspended. The abort is the prompt stop: every model call, tool call and new input is
   * refused while the owner is not admitted.
   */
  endUnadmitted(): Promise<void> {
    const live = new Set([...this.#parts.threads.liveThreads(),
      ...this.host.openThreads().filter((thread) => this.host.peek(thread)?.pi.wake().active)]);
    return this.#endThreads([...live], "scheduled");
  }

  /**
   * A readmission wakes each thread whose wake job its admission held (`Threads.overdue`:
   * a scheduled launch's held delivery), so it delivers now, not at the hold's end. Each
   * job is set due in batches of the host's bound; the alarm then opens them as it opens
   * every wake, never more at once than the bound. The door waits at most `END_WAIT_MS`; a
   * wake missed is found by its job at `ADMISSION_HOLD_MS`.
   */
  async wakeHeld(): Promise<void> {
    // On pi's clock, which its wakes are stored on.
    const threads = this.#parts.threads.overdue((this.#source.now ?? Date.now)() / 1000);
    await this.#within((async () => {
      for (let at = 0, size = Math.max(1, this.host.openMax); at < threads.length;
           at += size) {
        await Promise.all(threads.slice(at, at + size).map((thread) =>
          this.host.wakeNow(thread)));
      }
    })());
  }

  /**
   * End `threads`' work, keeping what `keep` says: every wait and view first, then each
   * thread opened if need be: an ending's open is never refused, and aborts before the
   * Session resumes (`ThreadHost.end`), so the endings run in batches of the host's bound,
   * never more Sessions open for them at once than the bound allows. A thread whose work
   * could not be ended rejects this, so the door rejects too.
   */
  async #endThreads(threads: readonly string[], keep: "sleepers" | "scheduled"): Promise<void> {
    this.#endWaits();
    this.#parts.tee.close();
    await this.#within((async () => {
      for (let at = 0, size = Math.max(1, this.host.openMax); at < threads.length;
           at += size) {
        await Promise.all(threads.slice(at, at + size).map((thread) =>
          this.host.end(thread, ({ pi }) => this.#endSession(pi, false, keep))));
      }
    })());
  }

  /**
   * The owner's model usage as pi records it (pi audit F4): pi's own `pi.usage`, every
   * conversation's summed (`Harness.usage`: per `provider/model` and per tool, failed and
   * aborted attempts included) across every thread, and the metrics pi and pi-ai recorded
   * since this instance began. Diagnostic only: every cap reads `usage.ts`'s counters.
   *
   * A read never opens a Session: a thread's usage is its open Session's, read live, or
   * else its usage row, kept as its Session last closed (`ThreadHost`); a dropped thread's
   * is in the owner's usage total, added in its drop's last unit. A thread whose object died
   * with its Session open counts as it last closed, until it closes again.
   */
  async usage(): Promise<UsageView> {
    const sum: UsageState = { models: {}, tools: {} };
    const add = (usage: Readonly<UsageState>) => {
      for (const bucket of ["models", "tools"] as const) {
        for (const [key, counts] of Object.entries(usage[bucket] ?? {})) {
          sum[bucket][key] = addedUsage(sum[bucket][key], counts) as typeof counts;
        }
      }
    };
    const read: string[] = [];  // the threads read live
    for (const thread of this.host.openThreads()) {
      if (this.#parts.threads.settledRun(thread)) {
        continue;  // a settled run's usage is in the total, from its settling
      }
      const usage = await this.host.peek(thread)?.pi.usage(BG).catch(() => undefined);
      if (usage !== undefined) {  // one closing counts by the row its close keeps
        read.push(thread);
        add(usage);
      }
    }
    for (const kept of this.#parts.threads.keptUsage()) {
      if (!read.includes(kept.thread_id)) {
        add(JSON.parse(kept.usage) as UsageState);
      }
    }
    add(this.#parts.threads.usageTotal() as unknown as UsageState);  // dropped threads' total
    return { models: sum.models, tools: sum.tools, metrics: this.metrics.snapshot() };
  }
}

/** The owner's usage view: pi's `pi.usage` summed, and the adapter's metrics. */
export interface UsageView {
  readonly models: UsageState["models"];
  readonly tools: UsageState["tools"];
  readonly metrics: MetricsSnapshot;
}

/** One Session's model catalog: the object's one model source, bound to the Session. */
export function gatewayModels(parts: AgentParts): (session: SessionBinding) => Models {
  return ({ authority }) => {
    const models = createModels();
    models.setProvider(parts.provider.forSession(authority) as Provider);
    return models;
  };
}

/**
 * The production agent: pi on the object's `gateway` provider, the one model source, with
 * no extensions beyond the object's own (a package is data, `resources.ts`) and no trusted
 * extension (the ta guard holds `trusted` to `AgentSource` and `#install`).
 */
export function gatewayAgent(parts: AgentParts): OwnerAgent {
  return new OwnerAgent(parts, { ...gatewaySource(parts), packages: [] });
}

/**
 * The object's model source: its catalog (`gatewayModels`) and its models' compaction
 * thresholds, each one's own (`GatewayProvider.compaction`).
 */
export function gatewaySource(parts: AgentParts): Pick<AgentSource, "models" | "compaction"> {
  return { models: gatewayModels(parts), compaction: () => parts.provider.compaction() };
}
