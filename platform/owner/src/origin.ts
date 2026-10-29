/**
 * A model call's turn: the submitted input at the origin of its causal chain, carried by
 * pi's own telemetry propagation (ADR-032 §4; tasks 5 A1).
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
 * This module is the harness's telemetry adapter, and records nothing but that one
 * thing, the origin: an input admitted with a link to a context that has one (a tool or
 * background work submitting to a subagent's conversation) takes that origin; any other
 * input (a door's, or the owner's own) is a new origin. Every other span takes its
 * parent's. So every model call one input caused, through any subagent, compaction or
 * background work, even after the run that started it ended, is charged to that input's
 * turn, as the Python's `_borrowed` charged a child to its parent's budget; a steer joins
 * the run it steers; and only a new input opens a new turn.
 *
 * The one task pi itself starts with no carrier is a compaction asked for outside a run
 * (a door's write submission starts no input span): it is its own origin, by its task id.
 * Any other context without an origin (a package's commit made outside the context it was
 * given) has none, and its calls are refused (`no_run`), never given a fresh turn.
 */
import { randomUUID } from "node:crypto";

import type {
  SpanAttributes, SpanLink, SpanOptions, SpanStatus, TelemetryCarrier, TelemetryContext,
  TelemetryPropagation, TelemetrySpan,
} from "@earendil-works/pi-telemetry";

/** What an origin may be, in the carrier's one key, `ta.origin`. */
const ORIGIN = /^(?:input|task):[0-9A-Za-z-]{1,64}$/;
/** pi's span names and attributes this adapter reads (pi-durable 1.4.0). */
const ADMISSION = "pi.durable.submit";
const PHASE = "pi.durable.phase";
const COMPACTION = "pi.compaction";

/** The fixed code of a model call no origin can be found for. */
export const NO_RUN = "no_run";

/** A context's origin, resolved once, at first need. */
type Resolve = () => string | undefined;

/** A telemetry span that carries an origin and nothing else. */
class OriginSpan implements TelemetrySpan {
  readonly propagation: TelemetryPropagation = PROPAGATION;
  readonly #resolve: Resolve;

  constructor(resolve: Resolve) {
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
    return new OriginSpan(() => this.origin());
  }

  addLink(_link: SpanLink): void {}
  addEvent(_name: string, _attributes?: SpanAttributes): void {}
  setAttributes(_attributes: SpanAttributes): void {}
  setStatus(_status: SpanStatus): void {}
}

/**
 * A span started at the root, where a causal chain may begin: an input's admission takes
 * its first linked origin, else a new one; pi's compaction with no carrier takes its task
 * id; anything else has none.
 */
class RootSpan extends OriginSpan {
  #origin: string | undefined;
  #linked: string | undefined;
  #resolved = false;
  readonly #fresh: () => string | undefined;

  constructor(options: SpanOptions) {
    super(() => this.#settle());
    const attributes = options.attributes ?? {};
    const task = attributes["task.id"];
    this.#fresh = options.name === ADMISSION ? () => `input:${randomUUID()}`
      : options.name === PHASE && attributes["task.name"] === COMPACTION
          && (typeof task === "number" || typeof task === "string")
        ? () => `task:${task}`
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
    if (!this.#resolved && this.#linked === undefined && link.context instanceof OriginSpan) {
      this.#linked = link.context.origin();
    }
  }
}

/** The harness's root: no origin of its own, and every span it starts may begin a chain. */
class Root extends OriginSpan {
  constructor() {
    super(() => undefined);
  }

  protected override child(options: SpanOptions): OriginSpan {
    return new RootSpan(options);
  }
}

const PROPAGATION: TelemetryPropagation = Object.freeze({
  inject: (context: TelemetryContext): TelemetryCarrier | undefined => {
    const origin = context instanceof OriginSpan ? context.origin() : undefined;
    return origin === undefined ? undefined : { "ta.origin": origin };
  },
  extract: (carrier: TelemetryCarrier | undefined): TelemetryContext => {
    const origin = carrier?.["ta.origin"];
    return origin !== undefined && ORIGIN.test(origin) ? new OriginSpan(() => origin)
      : new Root();
  },
});

/** A harness's telemetry, pi's `HarnessOptions.telemetry`: a root, which holds nothing. */
export function originRoot(): TelemetrySpan {
  return new Root();
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

/** A context of origin `origin`, as a carrier pi stored extracts to (for tests). */
export function originContext(origin: string): TelemetrySpan {
  const context = PROPAGATION.extract({ "ta.origin": origin });
  if (context instanceof Root || !(context instanceof OriginSpan)) {
    throw new Error("not an origin");
  }
  return context;
}
