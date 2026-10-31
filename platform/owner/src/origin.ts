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
 * `originRoot`). Its spans record nothing but one thing, the origin, and its metrics
 * capability keeps pi's and pi-ai's metrics (`OriginMetrics`). It is ADR-032 §4's kept difference: the turn every spend cap keys on
 * rides pi's telemetry propagation, against pi-telemetry's "a span is diagnostic data,
 * not business state", because pi has no durable causal context of its own (pi audit
 * F4). So the span names it reads are held to pi's exported `DURABLE_TELEMETRY_SCHEMA`,
 * and no method of it ever throws: on pi 1.4 a throwing adapter can reject a durable
 * commit. The origin: an input admitted with a link to a context that has one (a tool or
 * background work submitting to a subagent's conversation) takes that origin. Every other
 * input is a new origin, a new turn: one a door submits, one the thread host admits,
 * and any input submitted under a context with no origin (`BACKGROUND_CONTEXT`, or a
 * context code made instead of the one pi passed it). Every other span takes its parent's,
 * but a compaction's. So every model call one input caused, through any subagent or tool,
 * is charged to that input's turn while it is open, as the Python's `_borrowed` charged a
 * child to its parent's budget, and refused `turn_ended` after; a steer's submission is
 * an origin of its own (pi marks it before it links it to the run), while the run's calls
 * that read it stay the run's.
 *
 * Every compaction is its own origin, by its task id named by its thread
 * (`task:<thread>:<id>`), whatever carrier pi created it with: a background one outlives
 * the run it was made in, and phase spans carry no background flag, so blocking ones are
 * their own turns too. Task ids are per Session, and every thread is a Session of its
 * own, so two threads' compactions never share a turn. A turn lives as long as the pi
 * records that own it (`thread-host.ts`, migration 10): its open input submissions, or its
 * compaction task. A task takes whatever other carrier it was created with; one with none
 * (created under a context with no origin) has no origin, and its calls are refused
 * (`no_run`). A task created with a forged `trace` carrier takes the forged origin. No
 * owner source creates a task (the create-task guard), so pi's own are all there are.
 *
 * Neither a fresh input nor a forged carrier is in reach of anything but our own code:
 * only trusted platform code runs inside the owner object. Package executable code is
 * refused (A5 refuses `pi.extensions`); hooks run remotely, in the hooks Worker, which has
 * no submit or task path (H0, H1); and the model's tools have no submit or task path of
 * their own beyond the contexts pi passes them. A future in-object package runtime would
 * need an owner-edge check here.
 */
import { randomUUID } from "node:crypto";

import type { DURABLE_TELEMETRY_SCHEMA } from "@earendil-works/pi-durable";
import type {
  HistogramOptions, InstrumentOptions, SpanAttributes, SpanLink, SpanOptions, SpanStatus,
  TelemetryCarrier, TelemetryContext, TelemetryCounter, TelemetryHistogram, TelemetryMetrics,
  TelemetryPropagation, TelemetrySpan, TelemetryUpDownCounter,
} from "@earendil-works/pi-telemetry";

/** A thread's id, as its row holds it. */
const THREAD = /^thread_[0-9a-f]{32}$/;
/**
 * What an origin may be, in the carrier's one key, `ta.origin`: an input's, or a
 * compaction's, named by its thread.
 */
const ORIGIN = /^(?:input:[0-9A-Za-z-]{1,64}|task:thread_[0-9a-f]{32}:[0-9]{1,16})$/;

type DurableSpans = typeof DURABLE_TELEMETRY_SCHEMA["spans"];
/**
 * pi's span names this adapter reads, held to pi-durable's exported vocabulary
 * (`DURABLE_TELEMETRY_SCHEMA`) by their type, and with the phase's `task.id` and
 * `task.name` by the origin test at run time, so a pi that renames one fails the build
 * rather than every call's turn.
 */
export const ADMISSION = "pi.durable.submit" satisfies keyof DurableSpans;
export const PHASE = "pi.durable.phase" satisfies keyof DurableSpans;
/** pi's compaction task's registered name (`CompactionTask`, held by the origin test). */
export const COMPACTION = "pi.compaction";

/** The fixed code of a model call no origin can be found for. */
export const NO_RUN = "no_run";

/** The series the metrics keep at most; past it a new one is counted as dropped. */
export const METRIC_SERIES_MAX = 1_024;

/** One series of a metric pi recorded: its name, kind and attributes, and its measurements. */
export interface MetricSeries {
  readonly name: string;
  readonly kind: "counter" | "upDownCounter" | "histogram";
  readonly unit: string | null;
  readonly attributes: Readonly<Record<string, string | number | boolean>>;
  readonly count: number;
  readonly sum: number;
  readonly min: number;
  readonly max: number;
}

/** What the metrics hold: each series, and how many measurements were dropped. */
export interface MetricsSnapshot {
  readonly series: readonly MetricSeries[];
  readonly dropped: number;
}

type Mutable<T> = { -readonly [K in keyof T]: T[K] };

/**
 * The adapter's metrics capability (pi-telemetry's `TelemetryMetrics`), so the metrics pi
 * and pi-ai record are kept rather than dropped (pi audit F4): pi-ai's `pi.ai.tokens`,
 * `pi.ai.cost`, `pi.ai.request.duration` and `pi.ai.first_chunk.duration` of every model
 * request, pi-durable's `pi.durable.phase.duration`, `pi.durable.tasks.active` and
 * `pi.durable.reports`. Each is aggregated in the object's memory by its name, kind and
 * attributes (their count, sum, minimum and maximum), at most `METRIC_SERIES_MAX` series,
 * and read by the owner's usage view (`OwnerDO.usageMetrics`), where G5 reads them. It is
 * diagnostic data, lost with the instance, and never business state: no cap reads it.
 *
 * Recording never throws, as the whole adapter never does: on pi 1.4 a throwing telemetry
 * adapter can reject a durable commit (fixed on pi's main), so a measurement that cannot
 * be kept is dropped.
 */
export class OriginMetrics implements TelemetryMetrics {
  readonly #series = new Map<string, Mutable<MetricSeries>>();
  #dropped = 0;

  counter(name: string, options?: InstrumentOptions): TelemetryCounter {
    return { add: (value, attributes) =>
      this.#record("counter", name, options, value, attributes) };
  }

  upDownCounter(name: string, options?: InstrumentOptions): TelemetryUpDownCounter {
    return { add: (value, attributes) =>
      this.#record("upDownCounter", name, options, value, attributes) };
  }

  histogram(name: string, options?: HistogramOptions): TelemetryHistogram {
    return { record: (value, attributes) =>
      this.#record("histogram", name, options, value, attributes) };
  }

  /** Every series, and the measurements dropped. */
  snapshot(): MetricsSnapshot {
    return { series: [...this.#series.values()].map((series) => ({
      ...series, attributes: { ...series.attributes } })), dropped: this.#dropped };
  }

  #record(kind: MetricSeries["kind"], name: unknown, options: InstrumentOptions | undefined,
          value: unknown, attributes: SpanAttributes | undefined): void {
    try {
      if (typeof name !== "string" || typeof value !== "number" || !Number.isFinite(value)) {
        this.#dropped += 1;
        return;
      }
      const kept: Record<string, string | number | boolean> = {};
      for (const [key, entry] of Object.entries(attributes ?? {}).sort(([a], [b]) =>
        (a < b ? -1 : a > b ? 1 : 0))) {
        if (typeof entry === "string" || typeof entry === "number" || typeof entry === "boolean") {
          kept[key] = entry;
        }
      }
      const key = JSON.stringify([kind, name, kept]);
      let series = this.#series.get(key);
      if (series === undefined) {
        if (this.#series.size >= METRIC_SERIES_MAX) {
          this.#dropped += 1;
          return;
        }
        const unit = options?.unit;
        series = { name, kind, unit: typeof unit === "string" ? unit : null, attributes: kept,
                   count: 0, sum: 0, min: value, max: value };
        this.#series.set(key, series);
      }
      series.count += 1;
      series.sum += value;
      series.min = Math.min(series.min, value);
      series.max = Math.max(series.max, value);
    } catch {
      this.#dropped += 1;
    }
  }
}

/** A context's origin, resolved once, at first need. */
type Resolve = () => string | undefined;

/**
 * The adapter's capabilities, shared by its root, every span and every extracted parent:
 * the thread whose Session it is, its metrics, and its propagation.
 */
interface Adapter {
  readonly thread: string;
  readonly metrics: OriginMetrics;
  readonly propagation: TelemetryPropagation;
}

/** A telemetry span that carries an origin and nothing else, in one thread's Session. */
class OriginSpan implements TelemetrySpan {
  readonly #adapter: Adapter;
  readonly #resolve: Resolve;

  constructor(adapter: Adapter, resolve: Resolve) {
    this.#adapter = adapter;
    this.#resolve = resolve;
  }

  get metrics(): TelemetryMetrics {
    return this.#adapter.metrics;
  }

  get propagation(): TelemetryPropagation {
    return this.#adapter.propagation;
  }

  protected get adapter(): Adapter {
    return this.#adapter;
  }

  /** This context's origin, or undefined for the root and a context that has none. */
  origin(): string | undefined {
    return this.#resolve();
  }

  /** Whether this context's origin is one it joined by a link: an admission's, only. */
  joined(): boolean {
    return false;
  }

  /**
   * The callback, run synchronously once in a span of its own; what it returns or throws
   * is the answer's (a throw as a rejection), and nothing of the adapter's own ever is.
   */
  startSpan<T>(options: SpanOptions, callback: (span: TelemetrySpan) => T | Promise<T>):
      Promise<T> {
    let span: OriginSpan;
    try {
      span = this.child(options);
    } catch {
      span = new OriginSpan(this.#adapter, () => undefined);
    }
    try {
      return Promise.resolve(callback(span));
    } catch (error) {
      return Promise.reject(error);
    }
  }

  /** A span started here: of this context's origin, but a compaction's, its own. */
  protected child(options: SpanOptions): OriginSpan {
    const own = compactionOrigin(this.#adapter.thread, options);
    return new OriginSpan(this.#adapter, own === undefined ? () => this.origin() : () => own);
  }

  addLink(_link: SpanLink): void {}
  addEvent(_name: string, _attributes?: SpanAttributes): void {}
  setAttributes(_attributes: SpanAttributes): void {}
  setStatus(_status: SpanStatus): void {}
}

/**
 * A compaction's own origin, named by its task in its thread, for a span of its phase:
 * every compaction is a turn of its own, whatever carrier pi created it with (a background
 * one outlives the run it was made in), owned by its task (`thread-host.ts`); undefined
 * for any other span.
 */
export function compactionOrigin(thread: string, options: SpanOptions): string | undefined {
  const attributes = options.attributes ?? {};
  const task = attributes["task.id"];
  return options.name === PHASE && attributes["task.name"] === COMPACTION
      && (typeof task === "number" || typeof task === "string")
    ? `task:${thread}:${task}` : undefined;
}

/** The carrier key an input that joined another's turn is marked by (`thread-host.ts`). */
export const JOINED = "ta.joined";

/** Whether `context` is an admission that joined its origin by a link; never throws. */
function joinedOf(context: unknown): boolean {
  try {
    return context instanceof OriginSpan && context.joined();
  } catch {
    return false;
  }
}

/** `context`'s origin when it is a span of an origin adapter; never throws. */
function originOf(context: unknown): string | undefined {
  try {
    return context instanceof OriginSpan ? context.origin() : undefined;
  } catch {
    return undefined;
  }
}

/**
 * A span started at the root, where a causal chain may begin: an input's admission takes
 * its first linked origin, else a new one; pi's compaction takes its task id, named by its
 * thread; anything else has none.
 */
class RootSpan extends OriginSpan {
  #origin: string | undefined;
  #linked: string | undefined;
  #resolved = false;
  readonly #fresh: () => string | undefined;

  constructor(adapter: Adapter, options: SpanOptions) {
    super(adapter, () => this.#settle());
    const compaction = compactionOrigin(adapter.thread, options);
    this.#own = compaction;
    this.#fresh = options.name === ADMISSION ? () => `input:${randomUUID()}`
      : () => compaction;
  }

  readonly #own: string | undefined;

  #settle(): string | undefined {
    if (!this.#resolved) {
      this.#resolved = true;
      this.#origin = this.#own ?? this.#linked ?? this.#fresh();
    }
    return this.#origin;
  }

  override joined(): boolean {
    this.#settle();
    return this.#own === undefined && this.#linked !== undefined;
  }

  override addLink(link: SpanLink): void {
    try {
      if (!this.#resolved && this.#linked === undefined) {
        this.#linked = originOf(link.context);
      }
    } catch {
      // A link that cannot be read links nothing.
    }
  }
}

/** A thread's Session's root: no origin of its own, and every span it starts may begin a chain. */
class Root extends OriginSpan {
  constructor(adapter: Adapter) {
    super(adapter, () => undefined);
  }

  protected override child(options: SpanOptions): OriginSpan {
    return new RootSpan(this.adapter, options);
  }
}

/** The carrier's origin, when it holds one in its grammar; never throws. */
function carried(carrier: unknown): string | undefined {
  try {
    const origin = (carrier as TelemetryCarrier | undefined)?.["ta.origin"];
    return typeof origin === "string" && ORIGIN.test(origin) ? origin : undefined;
  } catch {
    return undefined;
  }
}

/** `thread`'s adapter: its metrics, and a propagation whose extracted parents carry them. */
function adapter(thread: string, metrics: OriginMetrics): Adapter {
  const made: Adapter = {
    thread,
    metrics,
    propagation: Object.freeze({
      inject: (context: TelemetryContext): TelemetryCarrier | undefined => {
        const origin = originOf(context);
        if (origin === undefined) {
          return undefined;
        }
        // An input that joined another's turn (a steer) says so: it may keep that turn's
        // counters, never make them again once the turn has ended.
        return joinedOf(context) ? { "ta.origin": origin, "ta.joined": "1" }
          : { "ta.origin": origin };
      },
      extract: (carrier: TelemetryCarrier | undefined): TelemetryContext => {
        const origin = carried(carrier);
        return origin !== undefined ? new OriginSpan(made, () => origin) : new Root(made);
      },
    }),
  };
  return Object.freeze(made);
}

/**
 * A thread's Session's telemetry, pi's `HarnessOptions.telemetry`: a root, which holds
 * nothing but the thread a compaction's origin is named by, with `metrics` as its metrics
 * capability. Throws for a name not a thread's (before pi has it).
 */
export function originRoot(thread: string, metrics: OriginMetrics = new OriginMetrics()):
    TelemetrySpan {
  if (!THREAD.test(thread)) {
    throw new Error("an origin root is a thread's");
  }
  return new Root(adapter(thread, metrics));
}

/**
 * The turn of a model request, from the telemetry context pi gives it
 * (`options.telemetry`); throws `no_run` for a context with no origin.
 */
export function turnOf(telemetry: unknown): string {
  const origin = originOf(telemetry);
  if (origin === undefined) {
    throw new Error(NO_RUN);
  }
  return origin;
}

/** A context of origin `origin` in `thread`'s Session, as a stored carrier extracts to (tests). */
export function originContext(origin: string,
                              thread = "thread_00000000000000000000000000000000"):
    TelemetrySpan {
  const context = adapter(thread, new OriginMetrics()).propagation
    .extract({ "ta.origin": origin });
  if (context instanceof Root || !(context instanceof OriginSpan)) {
    throw new Error("not an origin");
  }
  return context;
}
