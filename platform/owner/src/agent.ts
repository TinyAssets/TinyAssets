/**
 * The owner's agent: pi-durable inside the owner's object, on PiHarness (ADR-032 §4;
 * tasks 5, sub-slices A0 and A2).
 *
 * PiHarness (`agents/harness/pi`, agents 0.31.0) is a Lifecycle capability the object
 * installs on its own `new Lifecycle(this)` beside the schedule, with none of Lifecycle's
 * runtime handlers: the object forwards `alarm` alone. pi opens in Lifecycle's startup,
 * which the first alarm or the first PiHarness call runs; every PiHarness method awaits
 * that startup itself, so no door starts the Lifecycle first. pi's tables sit in the
 * owner's one SQLite database under its own prefix, with their own forward-only schema
 * version beside ours (the pi pin guard holds it at 2). pi's commits run through chord's
 * per-storage queue, which every owner write shares through the object's write gate
 * (`gate.ts`), so no owner write lands inside a pi transaction.
 *
 * What pi is opened with, every time it opens (the factory runs again after a storage
 * error, so it is safe to run twice):
 * - one registry per object (pi keeps a Harness subscribed to its registry until it
 *   closes, and an evicted object never closes it), holding first `ta.authority`, the
 *   early authority gate (`authority.ts`), whose `beforeTool` runs before every other
 *   extension's, so its block wins; then pi's `CodingTools` with `read` wrapped
 *   `replay: "safe"` (only pure reads are marked safe; `write`, `edit` and `bash` stay
 *   `unsafe`, so a call an eviction cut off comes back `interrupted` and is never run
 *   twice), then the object's packages. The `ta.` names are the object's: a package named
 *   so is refused, as one pi refuses to install is; each is skipped and kept as an owner
 *   notice, once while it is kept, though pi opens again at every wake: the factory never
 *   throws for owner content, since a failed open fails the Lifecycle's startup and the
 *   object's lanes with it;
 * - the models, read at each use: the `gateway` provider (`gateway-provider.ts`, A1),
 *   whose models are the owner's connected ones and their virtual `auto`, the default a
 *   conversation starts on; with nothing connected the catalog is empty, so a turn
 *   settles `unanswered: no_model`, loudly;
 * - the identity its requests carry: TinyAssets' attribution, not pi's (pi-durable's
 *   default names pi);
 * - an environment per call, replays included: `OwnerFilesEnv` over the owner's files,
 *   whose writer in the file history is the conversation's stamp (`client:<id>` for an
 *   outside client's conversation, else `agent`), and which enforces the conversation's
 *   authority on every operation, as `ta.authority`'s document holds it;
 * - settings read at each use: progress committed at most once a second (the gateway
 *   streams live tokens from memory; a commit is a storage write), and a failed run's
 *   queued inputs withdrawn, so each settles `unanswered: run_failed` rather than waiting
 *   on an unrelated next input; over those, the part of owner policy that is pi's
 *   (`AgentParts.policy`: the model retry count), which never moves the fixed ones. No
 *   source selects extensions, in settings or a conversation's agent (a guard holds it),
 *   and a client's conversation whose selection drops the gate makes no model call
 *   (`modelAuthority`);
 * - `onReport`: each report becomes an owner notice (`notices.ts`);
 * - `conversationCreated`, the one way an authority is inherited, in every creating
 *   commit: the root conversation is the owner's; a conversation a task creates (a
 *   subagent's) takes its owner conversation's authority, over any copy a fork brought,
 *   which must agree; a fork holds its parent's (`fork: "current"`). Each conversation's
 *   provider session is recorded in `ta.session`, so a model call finds its conversation;
 * - the telemetry adapter (`origin.ts`), which carries each causal chain's origin, the
 *   submitted input that started it, along pi's own propagation: a model call's turn.
 *
 * A thread (`thread`) is a top-level conversation the object makes for an agent: pi
 * writes its authority in the creating commit, then the object records it (`threads.ts`).
 * It is the one place the object makes a conversation (a guard holds it).
 *
 * Ending work (agent research §4.2), each in the door of the change, after its own unit
 * committed: a grant set or revoke ends its client's work (`endClient`), a policy write
 * every conversation's (`endAll`). Each ends the pending waits it covers (`wait`), closes
 * the live views of the conversations it covers (`live-tee.ts`), and aborts each
 * conversation with live work, background work included: pi withdraws its queued inputs,
 * marks and signals its live tasks (a model request in flight sees its abort signal, so
 * the gateway call ends and settles through `usage.ts`), and settles their inputs
 * `unanswered: aborted`. The door waits for that at most `END_WAIT_MS`; pi finishes it on
 * its own past then. The abort is the prompt stop, not the enforcement: an abort missed
 * (work admitted after the end's read, or an eviction between the commit and the abort)
 * still finds every tool operation and every model call refused.
 */
import { BACKGROUND_CONTEXT, type Context } from "@earendil-works/chord/context";
import type { ClientIdentity, Models, Provider } from "@earendil-works/pi-ai";
import { createModels } from "@earendil-works/pi-ai/models";
import {
  AgentDoc, type AgentState, type ConversationId, type ConversationRecord, createRegistry,
  defineDoc, defineDocFamily,
  defineExtension, type EnvTarget, type Extension, Harness, type HarnessSettings, hook,
  ProviderDoc, type ReportInfo, ROOT_CONVERSATION_ID, ToolTask, type Tx, wrapTool,
} from "@earendil-works/pi-durable";
import type { ExecutionEnv } from "@earendil-works/pi-durable/env";
import { CodingTools } from "@earendil-works/pi-durable/tools";
import { type PiOperationResult, PiHarness } from "agents/harness/pi";

import {
  agentId, type Authority, AUTHORITY_RULE, authorityOf, type AuthorityState, type Need,
  NO_CONVERSATION, type Stamped, stamped, stampOf,
} from "./authority";
import { Invalid, Refused } from "./errors";
import type { Files } from "./files";
import { type Access, OwnerFilesEnv } from "./files-env";
import type { Write } from "./gate";
import type { LiveTee } from "./live-tee";
import type { Notices } from "./notices";
import { originRoot } from "./origin";
import { WORKSPACE } from "./paths";
import { inTime } from "./schedule";
import type { Thread, Threads } from "./threads";

const BG = BACKGROUND_CONTEXT;

/** The model a new conversation starts on: the gateway provider's virtual `auto` (A1). */
export const AUTO = Object.freeze({ provider: "gateway", id: "auto" });

/** How the agent's model requests name the app to providers: TinyAssets, not pi. */
export const IDENTITY: ClientIdentity = Object.freeze({
  attribution: Object.freeze({ name: "TinyAssets", url: "https://tinyassets.io" }),
});

/** pi's run settings; a function in the options, so it is read at each use. */
export const AGENT_SETTINGS: HarnessSettings = Object.freeze({
  progress: Object.freeze({ partialIntervalMs: 1000, outputIntervalMs: 1000 }),
  afterFailedRun: "withdraw",
  restartLimit: 5,
});

/** The longest a door waits for the work a change ends; pi finishes the abort past it. */
export const END_WAIT_MS = 5_000;
/** The longest a status read waits on an operation it found settled or unknown. */
export const STATUS_WAIT_MS = 1_000;
/** Waits the object keeps at once (A4's bounded waits), past which one is refused. */
export const WAITS_KEPT = 256;
/** A wait a grant or policy change ended. */
export const WAIT_ENDED = "the wait was ended by a grant or policy change";
/** The extension names that are the object's own: no package may take one. */
export const RESERVED_PREFIX = "ta.";
/** The early authority gate's extension name. */
export const GATE = "ta.authority";

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

/** Who a conversation's file changes are by, in the history. */
export function writerOf(authority: Readonly<AuthorityState> | undefined): string {
  const client = stampOf(authority)?.client;
  return client === undefined ? "agent" : `client:${client}`;
}

/** An operation's status, from pi's submission records: pending, or how it settled. */
export interface AgentStatus {
  readonly operation_id: string;
  readonly session: string;
  readonly status: "queued" | "running" | "parked" | "done" | "unanswered";
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
  readonly provider: Provider;
  /** The live tee the provider publishes every model event to, for the owner's views. */
  readonly tee: LiveTee;
  /** The authority checks, over the object's grants. */
  readonly authority: Authority;
  /** The object's threads. */
  readonly threads: Threads;
}

/** Besides the object's parts: the model catalog, and the packages after the object's own. */
export interface AgentSource {
  readonly models: () => Models;
  readonly packages: readonly Extension[];
}

type Pi = Awaited<ReturnType<PiHarness["pi"]>>;

/** Every conversation with live work: a live task, or an input not yet settled. */
async function liveConversations(pi: Pi): Promise<ConversationId[]> {
  const inspection = await pi.inspect(BG);
  return [...new Set([...inspection.tasks.map((task) => task.record.conversationId),
                      ...inspection.submissions.map((record) => record.conversationId)])];
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

/** An authority as plain JSON: a copy of a document's draft, which is no plain object. */
function copy(state: Readonly<AuthorityState>): AuthorityState {
  return JSON.parse(JSON.stringify(state)) as AuthorityState;
}

/** Whether two authorities are the same. */
function same(a: Readonly<AuthorityState>, b: Readonly<AuthorityState>): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export class OwnerAgent {
  /** The Lifecycle capability the object installs. */
  readonly harness: PiHarness;
  /** The `ta.authority` document, as this object's pi reads and writes it. */
  readonly authority = authorityDoc();
  /** The `ta.session` documents, as this object's pi reads and writes them. */
  readonly sessions = sessionDoc();
  readonly #parts: AgentParts;
  readonly #source: AgentSource;
  readonly #registry = createRegistry();
  /** The pending waits, by the client they are for ("" for the owner's), on the instance. */
  readonly #waits = new Map<string, Set<AbortController>>();

  constructor(parts: AgentParts, source: AgentSource) {
    this.#parts = parts;
    this.#source = source;
    this.harness = new PiHarness({
      harness: async ({ storage, context, onWake }) => {
        await this.#install();
        return Harness.open(storage, {
          models: () => this.#source.models(),
          registry: this.#registry,
          onWake,
          identity: IDENTITY,
          settings: () => ({ ...this.#parts.policy(), ...AGENT_SETTINGS }),
          env: (target, envContext) => this.#env(target, envContext),
          conversationCreated: (tx, record) => this.#inherit(tx, record),
          telemetry: originRoot(),
          onReport: (error, info) => this.#report(info.type, detailOf(error, info)),
        }, context);
      },
      defaults: { model: AUTO },
    });
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
    for (const extension of this.#source.packages) {
      try {
        if (String(extension.name).startsWith(RESERVED_PREFIX)) {
          throw new Error(`the ${RESERVED_PREFIX} names are the object's own`);
        }
        this.#registry.install(extension);
      } catch (error) {
        const why = error instanceof Error ? error.message : String(error);
        await this.#parts.write(() => this.#parts.notices.once(
          "extension_skipped", `${String(extension.name)}: ${why}`, Date.now() / 1000));
      }
    }
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
    });
  }

  /**
   * In every creating commit: the root is the owner's; a task's new conversation takes its
   * owner conversation's authority, which a fork's copy must agree with; and each
   * conversation's provider session is recorded.
   */
  async #inherit(tx: Tx, record: ConversationRecord): Promise<void> {
    const mine = await tx.doc(this.authority, record.id);
    if (record.id === ROOT_CONVERSATION_ID) {
      Object.assign(mine, { by: "owner" });
    } else if (record.owner !== undefined) {
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
   * Make a thread for `agent`: a top-level conversation on `auto`, its authority (the
   * owner's for a null stamp, else the client's stamp) written in pi's creating commit,
   * then recorded, in a unit that re-checks the stamp still stands.
   */
  async thread(agent: unknown, stamp: unknown): Promise<Thread> {
    const id = agentId(agent);
    const by = stamped(stamp);
    if (by !== null && by.agent !== id) {
      throw new Invalid("a thread's stamp names its own agent");
    }
    const { authority, threads, write } = this.#parts;
    const state = authorityOf(by);
    authority.require(state, []);
    threads.room(by);  // a refusal makes no conversation; the recording unit checks again
    const pi = await this.harness.pi();
    const conversation = await pi.createConversation({
      ownership: { kind: "ownerless" },
      agent: { model: { provider: AUTO.provider, modelId: AUTO.id } },
      init: async (tx, made) => {
        Object.assign(await tx.doc(this.authority, made), copy(state));
      },
    }, BG);
    return write(() => {
      authority.require(state, []);
      return threads.record(conversation.id, id, by, Date.now() / 1000);
    });
  }

  /**
   * The authority a model request runs under, by its provider session: a check, run in
   * the call's reservation and dispatch units, answering the refusal's code or null. A
   * request that names no conversation the object made is refused (`NO_CONVERSATION`),
   * and a client's or no one's conversation whose stored selection (pi's `AgentDoc`, read
   * only here) drops the gate is refused `AUTHORITY_RULE`, whatever its grant: with its
   * tools held by its environment, such a conversation does nothing.
   */
  async modelAuthority(sessionId: string | undefined): Promise<() => string | null> {
    if (sessionId === undefined) {
      return () => NO_CONVERSATION;
    }
    const pi = await this.harness.pi();
    const at = await pi.snapshot(this.sessions, sessionId, BG);
    if (at === undefined) {
      return () => NO_CONVERSATION;
    }
    const conversation = at.conversation as ConversationId;
    const state = await pi.snapshot(this.authority, conversation, BG);
    // The backstop: a client's or no one's conversation whose selection drops the gate
    // spends nothing; the environment already holds its tools.
    if (state?.by !== "owner" && !keepsGate((await pi.snapshot(AgentDoc, conversation, BG))
      ?.extensions)) {
      return () => AUTHORITY_RULE;
    }
    return () => this.#parts.authority.modelRule(state);
  }

  /**
   * An operation's status in `session`, from pi's receipts: its pending record first,
   * then, when it is not pending, how it settled (`not_found` for one pi never saw). One
   * admitted since the pending read is waited on only `STATUS_WAIT_MS`; any other failure
   * is the call's.
   */
  async status(operationId: string, session: string): Promise<AgentStatus> {
    const pending = async () => (await this.harness.pending({ session }))
      .find((operation) => operation.operationId === operationId);
    const open = await pending();
    if (open !== undefined) {
      return { operation_id: operationId, session, status: open.status };
    }
    const timeout = AbortSignal.timeout(STATUS_WAIT_MS);
    let settled: PiOperationResult;
    try {
      settled = await this.harness.wait(operationId, { session, signal: timeout });
    } catch (error) {
      if (!timeout.aborted) {
        throw error;
      }
      return { operation_id: operationId, session, status: (await pending())?.status ?? "running" };
    }
    return { operation_id: operationId, session, status: settled.status,
             ...(settled.reason === undefined ? {} : { reason: settled.reason }),
             ...(settled.text === undefined ? {} : { text: settled.text }) };
  }

  /**
   * Wait for an operation of `client` (null: the owner's) to settle, until `signal`; a
   * grant change of that client, or a policy write, ends the wait (`WAIT_ENDED`).
   */
  async wait(operationId: string, session: string, client: string | null,
             signal?: AbortSignal): Promise<PiOperationResult> {
    const key = client ?? "";
    const ended = this.#keepWait(key);
    try {
      return await this.harness.wait(operationId, {
        session,
        signal: signal === undefined ? ended.signal : AbortSignal.any([signal, ended.signal]),
      });
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

  /** A new pending wait of `key`'s, kept on the instance, within `WAITS_KEPT`. */
  #keepWait(key: string): AbortController {
    const kept = [...this.#waits.values()].reduce((sum, set) => sum + set.size, 0);
    if (kept >= WAITS_KEPT) {
      throw new Refused("too many pending waits");
    }
    const ended = new AbortController();
    const set = this.#waits.get(key) ?? new Set<AbortController>();
    this.#waits.set(key, set.add(ended));
    return ended;
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

  /** Close the live views of conversation `id`, by its provider session. */
  async #closeViews(pi: Pi, id: ConversationId): Promise<void> {
    const session = (await pi.snapshot(ProviderDoc, id, BG))?.sessionId;
    if (session !== undefined) {
      this.#parts.tee.close(session);
    }
  }

  /** Abort each conversation's work, background work included, waiting `END_WAIT_MS`. */
  async #abort(pi: Pi, ids: readonly ConversationId[]): Promise<void> {
    const aborting = Promise.all(ids.map(async (id) =>
      (await pi.conversation(id, BG))?.abort(BG, { background: true })));
    if (await inTime(aborting, END_WAIT_MS) === "late") {
      aborting.catch(() => console.error("ending an agent's work failed past its wait"));
    }
  }

  /**
   * A grant change ends its client's work: its pending waits, the live views of `threads`
   * (the client's threads, which the change's unit dropped) and of every conversation with
   * live work stamped for it, and that work. A client with no thread has none: every
   * conversation stamped for a client descends from a thread the object made for it.
   */
  async endClient(client: string, threads: readonly number[]): Promise<void> {
    this.#endWaits(client);
    if (threads.length === 0) {
      return;
    }
    const pi = await this.harness.pi();
    const ended: ConversationId[] = [];
    for (const id of await liveConversations(pi)) {
      if (stampOf(await pi.snapshot(this.authority, id, BG))?.client === client) {
        ended.push(id);
      }
    }
    const viewed = new Set([...ended, ...threads.map((id) => id as ConversationId)]);
    for (const id of viewed) {
      await this.#closeViews(pi, id);
    }
    await this.#abort(pi, ended);
  }

  /** A policy write ends every conversation's work: every wait, every view, all live work. */
  async endAll(): Promise<void> {
    this.#endWaits();
    this.#parts.tee.close();
    const pi = await this.harness.pi();
    await this.#abort(pi, await liveConversations(pi));
  }
}

/**
 * The production agent: pi on the object's `gateway` provider, the one model source, with
 * no packages beyond the object's own (`packages` are a test's).
 */
export function gatewayAgent(parts: AgentParts, packages: readonly Extension[] = []): OwnerAgent {
  const models = createModels();
  models.setProvider(parts.provider);
  return new OwnerAgent(parts, { models: () => models, packages });
}
