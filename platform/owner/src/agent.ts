/**
 * The owner's agent: pi-durable inside the owner's object, on PiHarness (ADR-032 §4;
 * tasks 5, sub-slice A0).
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
 *   closes, and an evicted object never closes it), holding pi's `CodingTools` with
 *   `read` wrapped `replay: "safe"` (only pure reads are marked safe; `write`, `edit` and
 *   `bash` stay `unsafe`, so a call an eviction cut off comes back `interrupted` and is
 *   never run twice), then the object's further extensions. One that pi refuses to
 *   install is skipped and kept as an owner notice, once while it is kept, though pi
 *   opens again at every wake: the factory never throws for owner
 *   content, since a failed open fails the Lifecycle's startup and the object's lanes
 *   with it;
 * - the models, read at each use: the `gateway` provider (`gateway-provider.ts`, A1),
 *   whose models are the owner's connected ones and their virtual `auto`, the default a
 *   conversation starts on; with nothing connected the catalog is empty, so a turn
 *   settles `unanswered: no_model`, loudly;
 * - the identity its requests carry: TinyAssets' attribution, not pi's (pi-durable's
 *   default names pi);
 * - an environment per call: `OwnerFilesEnv` over the owner's files, whose writer in the
 *   file history is the conversation's stamp (`client:<id>` for an outside client's
 *   conversation, else `agent`), read from the `ta.authority` document;
 * - settings read at each use: progress committed at most once a second (the gateway
 *   streams live tokens from memory; a commit is a storage write), and a failed run's
 *   queued inputs withdrawn, so each settles `unanswered: run_failed` rather than waiting
 *   on an unrelated next input; over those, the part of owner policy that is pi's
 *   (`AgentParts.policy`: the model retry count), which never moves the fixed ones;
 * - `onReport`: each report becomes an owner notice (`notices.ts`);
 * - `conversationCreated`: a conversation a task creates (a subagent's) takes its owner
 *   conversation's stamp in the creating commit, and a fork copies its parent's
 *   (`fork: "current"`), so no conversation an outside client's work makes sheds it.
 */
import type { Context } from "@earendil-works/chord/context";
import type { ClientIdentity, Models, Provider } from "@earendil-works/pi-ai";
import { createModels } from "@earendil-works/pi-ai/models";
import {
  type ConversationRecord, createRegistry, defineDoc, defineExtension, type EnvTarget,
  type Extension, Harness, type HarnessSettings, type ReportInfo, type Tx, wrapTool,
} from "@earendil-works/pi-durable";
import type { ExecutionEnv } from "@earendil-works/pi-durable/env";
import { CodingTools } from "@earendil-works/pi-durable/tools";
import { PiHarness } from "agents/harness/pi";

import type { Files } from "./files";
import { OwnerFilesEnv } from "./files-env";
import type { Write } from "./gate";
import type { LiveTee } from "./live-tee";
import type { Notices } from "./notices";
import { WORKSPACE } from "./paths";

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

/** The outside authority a conversation runs under: the stamp of the client it is for. */
export type Stamped = {
  client: string;
  generation: number;
  authenticated_at: number | null;
};

/** The `ta.authority` document's content: a conversation's stamp, or none (the owner's). */
export type Authority = { stamp: Stamped | null };

/** The `ta.authority` document: per conversation, the newest only, copied into a fork. */
export function authorityDoc() {
  return defineDoc<Authority>({
    kind: "ta.authority",
    version: 1,
    scope: "conversation",
    history: "latest",
    fork: "current",
    initial: () => ({ stamp: null }),
    checkpointWhen: () => true,
  });
}

/** Who a conversation's file changes are by, in the history. */
export function writerOf(authority: Readonly<Authority> | undefined): string {
  const client = authority?.stamp?.client;
  return client === undefined ? "agent" : `client:${client}`;
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
}

/** Besides the object's parts: the model catalog, and the extensions after the object's. */
export interface AgentSource {
  readonly models: () => Models;
  readonly extensions: readonly Extension[];
}

export class OwnerAgent {
  /** The Lifecycle capability the object installs. */
  readonly harness: PiHarness;
  /** The `ta.authority` document, as this object's pi reads and writes it. */
  readonly authority = authorityDoc();
  readonly #parts: AgentParts;
  readonly #source: AgentSource;
  readonly #registry = createRegistry();

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
          onReport: (error, info) => this.#report(info.type, detailOf(error, info)),
        }, context);
      },
      defaults: { model: AUTO },
    });
  }

  /**
   * The object's extensions on its registry: pi's coding tools, `read` marked replay-safe,
   * then the source's, each one pi refuses skipped and kept as a notice.
   */
  async #install(): Promise<void> {
    const read = CodingTools.tools?.find((tool) => tool.name === "read");
    if (read === undefined) {
      throw new Error("pi's coding tools have no read tool");
    }
    this.#registry.install(CodingTools);
    this.#registry.install(defineExtension({
      name: "ta.replay",
      wraps: [wrapTool(read, (tool) => ({ ...tool, replay: "safe" }))],
    }));
    for (const extension of this.#source.extensions) {
      try {
        this.#registry.install(extension);
      } catch (error) {
        const why = error instanceof Error ? error.message : String(error);
        await this.#parts.write(() => this.#parts.notices.once(
          "extension_skipped", `${String(extension.name)}: ${why}`, Date.now() / 1000));
      }
    }
  }

  /** One environment per call, over the owner's files, as the conversation's writer. */
  async #env({ conversationId, cwd, read }: EnvTarget, context: Context): Promise<ExecutionEnv> {
    const authority = await read.snapshot(this.authority, conversationId, context);
    await this.#parts.files.namespace();  // made through the gate, so the env reads it
    return new OwnerFilesEnv({ files: this.#parts.files, writer: writerOf(authority),
                               cwd: cwd ?? WORKSPACE, rearm: this.#parts.rearm });
  }

  /** A task's new conversation takes its owner conversation's stamp. */
  async #inherit(tx: Tx, record: ConversationRecord): Promise<void> {
    if (record.owner === undefined || record.parent !== undefined) {
      return;  // a top-level one is stamped where it is made; a fork has its copy
    }
    const from = await tx.doc(this.authority, record.owner.conversationId);
    if (from.stamp !== null) {
      (await tx.doc(this.authority, record.id)).stamp = { ...from.stamp };
    }
  }

  /** Keep a notice, in a unit of its own; pi drops a failed report, so it is logged. */
  #report(type: string, detail: string): void {
    this.#parts.write(() => this.#parts.notices.add(type, detail, Date.now() / 1000))
      .catch(() => console.error("an agent notice could not be kept"));
  }
}

/**
 * The production agent: pi on the object's `gateway` provider, the one model source, with
 * no extensions beyond the object's own.
 */
export function gatewayAgent(parts: AgentParts): OwnerAgent {
  const models = createModels();
  models.setProvider(parts.provider);
  return new OwnerAgent(parts, { models: () => models, extensions: [] });
}
