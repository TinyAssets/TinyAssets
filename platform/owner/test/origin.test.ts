/**
 * The telemetry adapter that carries a model call's turn (`origin.ts`): the causal chain's
 * origin, as pi-durable 1.4.0 starts and propagates spans (an input's admission from the
 * harness's root, linked to its caller; a task's phase from its stored carrier, or from
 * the root when it has none).
 */
import { AI_TELEMETRY_SCHEMA } from "@earendil-works/pi-ai";
import { CompactionTask, DURABLE_TELEMETRY_SCHEMA } from "@earendil-works/pi-durable";
import {
  createTypedMetrics, NOOP_TELEMETRY_CONTEXT, type TelemetryContext, type TelemetryMetrics,
  type TelemetrySpan,
} from "@earendil-works/pi-telemetry";
import { describe, expect, it } from "vitest";

import {
  ADMISSION, COMPACTION, METRIC_SERIES_MAX, NO_RUN, OriginMetrics, originContext, originRoot,
  PHASE, turnOf,
} from "../src/origin";

const ROOT = originRoot();

/** The span `parent` starts with `name` and `attributes`, handed to `act`. */
function within<T>(parent: TelemetryContext, name: string,
                   attributes: Record<string, string | number>,
                   act: (span: TelemetrySpan) => T): Promise<T> {
  return parent.startSpan({ name, attributes }, (span) => act(span as TelemetrySpan));
}

const admit = <T>(act: (span: TelemetrySpan) => T) =>
  within(ROOT, "pi.durable.submit", { "submission.type": "input" }, act);

describe("a call's origin", () => {
  it("is none for the root, a foreign context, or none at all: the call is refused", () => {
    for (const context of [undefined, {}, ROOT, NOOP_TELEMETRY_CONTEXT]) {
      expect(() => turnOf(context)).toThrow(NO_RUN);
    }
    expect(ROOT.propagation!.inject(ROOT)).toBeUndefined();
  });

  it("is a new input's for an admission no origin is linked to, kept through its carrier",
     async () => {
    const carried = await admit((span) => {
      span.addLink({ context: NOOP_TELEMETRY_CONTEXT });
      span.addLink({ context: ROOT });
      const origin = turnOf(span);
      expect(origin).toMatch(/^input:[0-9a-f-]{36}$/);
      expect(turnOf(span)).toBe(origin);
      return { origin, carrier: span.propagation!.inject(span) };
    });
    const phase = ROOT.propagation!.extract(carried.carrier);
    expect(turnOf(phase)).toBe(carried.origin);
    // A span started in it, as a request's, is of the same origin.
    expect(await within(phase, "pi.ai.request", {}, (span) => turnOf(span)))
      .toBe(carried.origin);
    // Two admissions are two origins.
    expect(await admit((span) => turnOf(span))).not.toBe(carried.origin);
  });

  it("is the caller's for an admission linked to it, the first such link fixing it",
     async () => {
    const caller = originContext("input:caller-1");
    const origin = await admit((span) => {
      span.addLink({ context: ROOT });
      span.addLink({ context: caller });
      span.addLink({ context: originContext("input:another") });
      return turnOf(span);
    });
    expect(origin).toBe("input:caller-1");
    // A link after the origin was read changes nothing.
    const [first, second] = await admit((span) => {
      const read = turnOf(span);
      span.addLink({ context: caller });
      return [read, turnOf(span)];
    });
    expect(second).toBe(first);
    expect(first).not.toBe("input:caller-1");
  });

  it("is a compaction's own, by its task id, when pi gives it no carrier, and none for any " +
     "other task", async () => {
    expect(await within(ROOT, "pi.durable.phase", { "task.id": 41, "task.name": "pi.compaction" },
                        (span) => turnOf(span))).toBe("task:41");
    await within(ROOT, "pi.durable.phase", { "task.id": 42, "task.name": "pkg.work" },
                 (span) => expect(() => turnOf(span)).toThrow(NO_RUN));
    await within(ROOT, "pi.durable.phase", { "task.name": "pi.compaction" },
                 (span) => expect(() => turnOf(span)).toThrow(NO_RUN));
    await within(ROOT, "other", {}, (span) => expect(() => turnOf(span)).toThrow(NO_RUN));
  });

  it("is carried only in its own key, in its own grammar", () => {
    const extract = ROOT.propagation!.extract;
    expect(turnOf(extract({ "ta.origin": "task:7" }))).toBe("task:7");
    for (const carrier of [undefined, {}, { "ta.origin": "run:7" }, { "ta.origin": "input:" },
                           { origin: "input:x" }, { "ta.origin": `input:${"a".repeat(65)}` }]) {
      expect(() => turnOf(extract(carrier))).toThrow(NO_RUN);
    }
    expect(ROOT.propagation!.inject(originContext("input:x"))).toEqual({ "ta.origin": "input:x" });
    expect(() => originContext("bad")).toThrow();
  });
});

describe("pi's names the adapter reads", () => {
  it("are pi-durable's exported vocabulary: its span names, the phase's attributes and the " +
     "compaction task's name", () => {
    expect(Object.keys(DURABLE_TELEMETRY_SCHEMA.spans)).toEqual(
      expect.arrayContaining([ADMISSION, PHASE]));
    // The phase's attributes a compaction's origin is read from.
    const phase = DURABLE_TELEMETRY_SCHEMA.spans[PHASE].startAttributes;
    expect([phase["task.id"].required, phase["task.name"].required]).toEqual([true, true]);
    expect(Object.keys(phase)).toEqual(expect.arrayContaining(["task.id", "task.name"]));
    expect(COMPACTION).toBe(CompactionTask.definition.name);
    expect(COMPACTION).toBe("pi.compaction");
  });
});

/** A value every one of whose reads and calls throws, as adapter input pi never sends. */
function hostile(): never {
  const thrower = () => {
    throw new Error("hostile");
  };
  return new Proxy(thrower, {
    get: thrower, has: thrower, ownKeys: thrower, getPrototypeOf: thrower, apply: thrower,
    getOwnPropertyDescriptor: thrower,
  }) as never;
}

describe("the adapter on pi 1.4, where a throwing adapter can reject a durable commit", () => {
  it("never throws, from any span, its propagation or its metrics, whatever it is handed",
     async () => {
    const metrics = new OriginMetrics();
    const root = originRoot(metrics);
    const spans: TelemetrySpan[] = [root, originContext("input:x")];
    await root.startSpan({ name: ADMISSION }, (span) => {
      spans.push(span as TelemetrySpan);
    });
    await root.startSpan({ name: PHASE, attributes: { "task.id": 1, "task.name": COMPACTION } },
                         (span) => {
      spans.push(span as TelemetrySpan);
    });
    for (const span of spans) {
      for (const input of [hostile(), undefined, null, 7, "x", {}]) {
        expect(() => span.addLink(input as never)).not.toThrow();
        expect(() => span.addLink({ context: input } as never)).not.toThrow();
        expect(() => span.addEvent(input as never, input as never)).not.toThrow();
        expect(() => span.setAttributes(input as never)).not.toThrow();
        expect(() => span.setStatus(input as never)).not.toThrow();
        expect(() => span.propagation!.inject(input as never)).not.toThrow();
        expect(() => span.propagation!.extract(input as never)).not.toThrow();
        expect(() => turnOf(span.propagation!.extract(input as never))).toThrow(NO_RUN);
        let ran = 0;
        const started = span.startSpan(input as never, () => {
          ran += 1;
          return "ran";
        });
        expect(ran).toBe(1);
        expect(await started).toBe("ran");
        for (const make of ["counter", "upDownCounter", "histogram"] as const) {
          for (const name of [input, "pi.ai.tokens"]) {
            const instrument = span.metrics![make](name as never, input as never) as
              { add?: (...args: unknown[]) => void; record?: (...args: unknown[]) => void };
            const measure = instrument.add ?? instrument.record!;
            expect(() => measure(input, input, input)).not.toThrow();
            expect(() => measure(1, input, input)).not.toThrow();
          }
        }
      }
    }
    expect(metrics.snapshot().dropped).toBeGreaterThan(0);
  });

  it("rejects, never throws, for a callback that throws, as pi-telemetry's contract says",
     async () => {
    const failure = new Error("the callback's own");
    let started: Promise<unknown> | undefined;
    expect(() => {
      started = originRoot().startSpan({ name: ADMISSION }, () => {
        throw failure;
      });
    }).not.toThrow();
    await expect(started).rejects.toBe(failure);
  });

  it("keeps the same metrics on its root, every span and every extracted parent", async () => {
    const metrics = new OriginMetrics();
    const root = originRoot(metrics);
    expect(root.metrics).toBe(metrics);
    await root.startSpan({ name: ADMISSION }, async (span) => {
      expect(span.metrics).toBe(metrics);
      const carrier = span.propagation!.inject(span);
      expect(root.propagation!.extract(carrier).metrics).toBe(metrics);
      expect(root.propagation!.extract(undefined).metrics).toBe(metrics);
      await span.startSpan({ name: "pi.ai.request" }, (child) => {
        expect(child.metrics).toBe(metrics);
      });
    });
  });
});

describe("the adapter's metrics", () => {
  it("aggregate pi's own instruments by name, kind and attributes", () => {
    const metrics = new OriginMetrics();
    const typed = createTypedMetrics(metrics as TelemetryMetrics,
                                     [AI_TELEMETRY_SCHEMA, DURABLE_TELEMETRY_SCHEMA]);
    const tokens = typed.counter("pi.ai.tokens");
    tokens.add(11, { provider: "gateway", model: "auto", kind: "input" });
    tokens.add(4, { provider: "gateway", model: "auto", kind: "input" });
    tokens.add(7, { provider: "gateway", model: "auto", kind: "output" });
    const duration = typed.histogram("pi.durable.phase.duration");
    duration.record(20, { task: "pi.generation", phase: "request", outcome: "done" });
    duration.record(10, { task: "pi.generation", phase: "request", outcome: "done" });
    duration.record(30, { task: "pi.generation", phase: "request", outcome: "done" });
    const active = typed.upDownCounter("pi.durable.tasks.active");
    active.add(1, { task: "pi.generation" });
    active.add(-1, { task: "pi.generation" });
    const { series, dropped } = metrics.snapshot();
    expect(dropped).toBe(0);
    expect(series).toEqual([
      { name: "pi.ai.tokens", kind: "counter", unit: "token",
        attributes: { kind: "input", model: "auto", provider: "gateway" },
        count: 2, sum: 15, min: 4, max: 11 },
      { name: "pi.ai.tokens", kind: "counter", unit: "token",
        attributes: { kind: "output", model: "auto", provider: "gateway" },
        count: 1, sum: 7, min: 7, max: 7 },
      { name: "pi.durable.phase.duration", kind: "histogram", unit: "ms",
        attributes: { outcome: "done", phase: "request", task: "pi.generation" },
        count: 3, sum: 60, min: 10, max: 30 },
      { name: "pi.durable.tasks.active", kind: "upDownCounter", unit: null,
        attributes: { task: "pi.generation" }, count: 2, sum: 0, min: -1, max: 1 },
    ]);
  });

  it("keep each kind of instrument apart, though name and attributes agree", () => {
    const metrics = new OriginMetrics();
    metrics.counter("same").add(2, { a: "x" });
    metrics.histogram("same").record(5, { a: "x" });
    expect(metrics.snapshot().series.map((series) => [series.kind, series.sum]))
      .toEqual([["counter", 2], ["histogram", 5]]);
  });

  it("keep at most their bound of series, counting what they drop", () => {
    const metrics = new OriginMetrics();
    const counter = metrics.counter("c");
    for (let i = 0; i < METRIC_SERIES_MAX + 3; i++) {
      counter.add(1, { i });
    }
    counter.add(1, { i: 0 });
    counter.add(Number.NaN, { i: 0 });
    const { series, dropped } = metrics.snapshot();
    expect(series).toHaveLength(METRIC_SERIES_MAX);
    expect(series[0]).toMatchObject({ attributes: { i: 0 }, count: 2 });
    expect(dropped).toBe(4);
  });
});
