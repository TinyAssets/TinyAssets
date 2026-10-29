/**
 * The telemetry adapter that carries a model call's turn (`origin.ts`): the causal chain's
 * origin, as pi-durable 1.4.0 starts and propagates spans (an input's admission from the
 * harness's root, linked to its caller; a task's phase from its stored carrier, or from
 * the root when it has none).
 */
import {
  NOOP_TELEMETRY_CONTEXT, type TelemetryContext, type TelemetrySpan,
} from "@earendil-works/pi-telemetry";
import { describe, expect, it } from "vitest";

import { NO_RUN, originContext, originRoot, turnOf } from "../src/origin";

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
