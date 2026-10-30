/**
 * A model call's turn: the submitted input at the origin of its causal chain, carried by
 * pi's own telemetry propagation (ADR-032 §4; tasks 5 A1, S3).
 *
 * pi-durable propagates a telemetry context along every causal edge it records, and keeps
 * it durably in its own records (`session/transaction.js`, `harness/scheduler.js`,
 * `harness/submissions.js`, `harness/generation.js` in pi-durable 1.4.0):
 * - an input's admission span starts at the harness's root, linked to the caller's
 *   context, and its carrier is the submission's `trace`;
 * - a run's first generation takes its first input's carrier as its `trace`, and every
 *   task a commit creates takes the creating context's carrier (`inject`), unless told
 *   otherwise: the next round's generation, a tool call, a compaction, a subagent's
 *   conversation's task, and background work a tool starts (`background: true`), which
 *   pi keeps running past the run;
 * - each phase of a task runs in the context its `trace` extracts to, and a model request
 *   carries the phase's context (`options.telemetry`).
 *
 * This module is each thread's Session's telemetry adapter (one root per thread,
 * `originRoot`), and records nothing but that one thing, the origin: an input admitted
 * with a link to a context that has one (a tool or background work submitting to a
 * subagent's conversation) takes that origin. Every other input is a new origin, a new
 * turn: one a door submits, one the thread host admits, and any input submitted under a
 * context with no origin (`BACKGROUND_CONTEXT`, or a context code made instead of the one
 * pi passed it). Every other span takes its parent's. So every model call one input
 * caused, through any subagent, compaction or background work, even after the run that
 * started it ended, is charged to that input's turn, as the Python's `_borrowed` charged a
 * child to its parent's budget; a steer joins the run it steers.
 *
 * A task takes whatever carrier it was created with. One with no carrier has no origin:
 * pi's compaction is the one such task pi itself starts (one asked for outside a run; a
 * door's write submission starts no input span), and it is its own origin, by its task id
 * named by its thread (`task:<thread>:<id>`): task ids are per Session, and every thread
 * is a Session of its own, so two threads' compactions never share a turn. Any other task
 * with no carrier (created under a context with no origin) has none, and its calls are
 * refused (`no_run`). A task created with a forged `trace` carrier takes the forged origin.
 *
 * Neither a fresh input nor a forged carrier is in reach of anything but our own code:
 * only trusted platform code runs inside the owner object. Package executable code is
 * refused (A5 refuses `pi.extensions`); hooks run remotely, in the hooks Worker, which has
 * no submit or task path (H0, H1); and the model's tools have no submit or task path of
 * their own beyond the contexts pi passes them. A future in-object package runtime would
 * need an owner-edge check here.
 *
 * The adapter never throws into pi: on pi 1.4 a throwing telemetry adapter can reject a
 * durable commit (fixed on pi main), so every span method is total (a guard holds it).
 */
import { randomUUID } from "node:crypto";

import type {
  SpanAttributes, SpanLink, SpanOptions, SpanStatus, TelemetryCarrier, TelemetryContext,
  TelemetryPropagation, TelemetrySpan,
} from "@earendil-works/pi-telemetry";

/** A thread's id, as its row holds it. */
const THREAD = /^thread_[0-9a-f]{32}$/;
/**
 * What an origin may be, in the carrier's one key, `ta.origin`: an input's, or a
 * compaction's, named by its thread.
 */
const ORIGIN = /^(?:input:[0-9A-Za-z-]{1,64}|task:thread_[0-9a-f]{32}:[0-9]{1,16})$/;
/** pi's span names and attributes this adapter reads (pi-durable 1.4.0). */
const ADMISSION = "pi.durable.submit";
const PHASE = "pi.durable.phase";
const COMPACTION = "pi.compaction";

/** The fixed code of a model call no origin can be found for. */
export const NO_RUN = "no_run";

/** A context's origin, resolved once, at first need. */
type Resolve = () => string | undefined;

/** A telemetry span that carries an origin and nothing else, in one thread's Session. */
class OriginSpan implements TelemetrySpan {
  readonly propagation: TelemetryPropagation;
  /** The thread whose Session this context is of. */
  readonly thread: string;
  readonly #resolve: Resolve;

  constructor(thread: string, resolve: Resolve) {
    this.thread = thread;
    this.propagation = propagationOf(thread);
    this.#resolve = resolve;
  }

  /** This context's origin, or undefined for the root and a context that has none. */
  origin(): string | undefined {
    return this.#resolve();
  }

  startSpan<T>(options: SpanOptions, callback: (span: TelemetrySpan) => T | Promise<T>):
      Promise<T> {
    return Promise.resolve(callback(this.child(options)));
  }

  /** A span started here: of this context's origin. */
  protected child(_options: SpanOptions): OriginSpan {
    return new OriginSpan(this.thread, () => this.origin());
  }

  addLink(_link: SpanLink): void {}
  addEvent(_name: string, _attributes?: SpanAttributes): void {}
  setAttributes(_attributes: SpanAttributes): void {}
  setStatus(_status: SpanStatus): void {}
}

/**
 * A span started at the root, where a causal chain may begin: an input's admission takes
 * its first linked origin, else a new one; pi's compaction with no carrier takes its task
 * id, named by its thread; anything else has none.
 */
class RootSpan extends OriginSpan {
  #origin: string | undefined;
  #linked: string | undefined;
  #resolved = false;
  readonly #fresh: () => string | undefined;

  constructor(thread: string, options: SpanOptions) {
    super(thread, () => this.#settle());
    const attributes = options?.attributes ?? {};
    const task = attributes["task.id"];
    this.#fresh = options?.name === ADMISSION ? () => `input:${randomUUID()}`
      : options?.name === PHASE && attributes["task.name"] === COMPACTION
          && (typeof task === "number" || typeof task === "string")
        ? () => `task:${thread}:${task}`
        : () => undefined;
  }

  #settle(): string | undefined {
    if (!this.#resolved) {
      this.#resolved = true;
      this.#origin = this.#linked ?? this.#fresh();
    }
    return this.#origin;
  }

  override addLink(link: SpanLink): void {
    if (!this.#resolved && this.#linked === undefined && link?.context instanceof OriginSpan) {
      this.#linked = link.context.origin();
    }
  }
}

/** A thread's Session's root: no origin of its own, and every span it starts may begin a chain. */
class Root extends OriginSpan {
  constructor(thread: string) {
    super(thread, () => undefined);
  }

  protected override child(options: SpanOptions): OriginSpan {
    return new RootSpan(this.thread, options);
  }
}

/** One thread's propagation: a carrier with an origin extracts to it, any other to the root. */
function propagationOf(thread: string): TelemetryPropagation {
  return Object.freeze({
    inject: (context: TelemetryContext): TelemetryCarrier | undefined => {
      const origin = context instanceof OriginSpan ? context.origin() : undefined;
      return origin === undefined ? undefined : { "ta.origin": origin };
    },
    extract: (carrier: TelemetryCarrier | undefined): TelemetryContext => {
      const origin = carrier?.["ta.origin"];
      return origin !== undefined && ORIGIN.test(origin)
        ? new OriginSpan(thread, () => origin) : new Root(thread);
    },
  });
}

/**
 * A thread's Session's telemetry, pi's `HarnessOptions.telemetry`: a root, which holds
 * nothing but the thread a compaction's origin is named by.
 */
export function originRoot(thread: string): TelemetrySpan {
  if (!THREAD.test(thread)) {
    throw new Error("an origin root is a thread's");
  }
  return new Root(thread);
}

/**
 * The turn of a model request, from the telemetry context pi gives it
 * (`options.telemetry`); throws `no_run` for a context with no origin.
 */
export function turnOf(telemetry: unknown): string {
  const origin = telemetry instanceof OriginSpan ? telemetry.origin() : undefined;
  if (origin === undefined) {
    throw new Error(NO_RUN);
  }
  return origin;
}

/** A context of origin `origin` in `thread`'s Session, as a stored carrier extracts to (tests). */
export function originContext(origin: string,
                              thread = "thread_00000000000000000000000000000000"):
    TelemetrySpan {
  const context = originRoot(thread).propagation!.extract({ "ta.origin": origin });
  if (context instanceof Root || !(context instanceof OriginSpan)) {
    throw new Error("not an origin");
  }
  return context;
}
