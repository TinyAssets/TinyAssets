/**
 * A run's events (ADR-034 decision 10; `engines-replan.md` §2.2): the engine's own JSON
 * stream, read by its source record's matchers (`engine.events`, slice E), as data.
 *
 * The in-VM runner (slice S) posts each event it reads as `{seq, at, event}`, `event` the
 * CLI's raw JSON. The VM is untrusted, so the object classifies each event itself, by the
 * run's record, and frames it for the owner's app sockets as `{v, run, seq, at, kind,
 * event}`. `kind` is the first of the record's matchers the event meets, in this order:
 * `error`, `result` (its `final_text`), `init` (its `session_id`), `tool_call`, `usage`;
 * else `event`. The record's matchers name no text deltas or tool results (slice E's
 * shape), so those arrive as `event` until a record matches them.
 *
 * What an event changes durably is read here too, once, by the same matchers: the
 * conversation's session id (`init`), the run's result text (`final_text`, the last match,
 * or every match joined where the matcher says `accumulate`) and its first error.
 */
import type { EngineEvents, EventMatcher } from "../../doors/src/sources";

import { Refused } from "./errors";

export const FRAME_VERSION = 1;

/**
 * The runner's events post, bounded so the relay's memory guard counts every post the
 * object may hold at once (slice R's review M-4 and round 2; `memory-bound.test.ts`): each
 * run's runner posts on its own, one post at a time, so as many posts as the tier's top
 * runs may be in flight. Each post's body at most `EVENTS_BODY_MAX` bytes, at most
 * `EVENT_FRAMES_MAX` frames, each frame's JSON at most `FRAME_UNITS_MAX` UTF-16 code units.
 * Technical ceilings of the isolate's memory; a CLI event longer than a frame may be is the
 * runner's to cut, marked, before it posts it (slice S), the engine's own transcript keeping
 * the whole, and a busy CLI's events go in more posts, not bigger ones.
 */
export const EVENTS_BODY_MAX = 65_536;
export const EVENT_FRAMES_MAX = 64;
export const FRAME_UNITS_MAX = 32_768;

/**
 * The runners' events posts the object holds at once, whatever the tier (review round 3):
 * one more is answered `events_busy` (503, `Retry-After`) and its runner posts it again. A
 * technical ceiling of the isolate's memory, sized so the memory guard fits its budget; no
 * run is refused, a post only waits.
 */
export const EVENTS_IN_FLIGHT = 8;
export const EVENTS_BUSY = "events_busy: the object holds as many runner posts as it may; " +
  "post again shortly";
/**
 * Another batch of the run committed between an events post's two units, so the reply its
 * first unit placed in R2 is not the one its settle would post: refused, written nothing,
 * and posted again (503, as `EVENTS_BUSY`), so no reply is lost.
 */
export const EVENTS_RACED = "events_raced: another batch of this run landed first; post " +
  "again shortly";

/** The count of events posts the object holds, refusing one past `EVENTS_IN_FLIGHT`. */
export class PostGate {
  #held = 0;

  get held(): number {
    return this.#held;
  }

  /** Hold one more post, or refuse it `EVENTS_BUSY`. */
  enter(): void {
    if (this.#held >= EVENTS_IN_FLIGHT) {
      throw new Refused(EVENTS_BUSY);
    }
    this.#held += 1;
  }

  leave(): void {
    this.#held -= 1;
  }
}

/** Whether an events post is within its frame bounds (the doors' and the object's check). */
export function eventsBounded(body: unknown): boolean {
  const frames = typeof body === "object" && body !== null
    ? (body as { frames?: unknown }).frames : undefined;
  return Array.isArray(frames) && frames.length <= EVENT_FRAMES_MAX
    && frames.every((frame) => (JSON.stringify(frame) ?? "").length <= FRAME_UNITS_MAX);
}
export const FRAME_KINDS = Object.freeze(["error", "result", "init", "tool_call", "usage",
                                          "event"] as const);
export type FrameKind = (typeof FRAME_KINDS)[number];

/** A frame as the object relays it. */
export interface Frame {
  readonly v: typeof FRAME_VERSION;
  readonly run: string;
  readonly seq: number;
  readonly at: number;
  readonly kind: FrameKind;
  readonly event: unknown;
}

/** The value at a dotted path, or undefined; the empty path is the event itself. */
export function valueAt(event: unknown, path: string): unknown {
  if (path === "") {
    return event;
  }
  let at: unknown = event;
  for (const part of path.split(".")) {
    if (typeof at !== "object" || at === null || Array.isArray(at)
        || !Object.hasOwn(at, part)) {
      return undefined;
    }
    at = (at as Record<string, unknown>)[part];
  }
  return at;
}

/** Whether `event` has each of the matcher's fields at its value. */
export function meets(event: unknown, matcher: EventMatcher): boolean {
  return Object.entries(matcher.match).every(([path, value]) => valueAt(event, path) === value);
}

/** The first of `matchers` the event meets, or undefined. */
function first(matchers: readonly EventMatcher[], event: unknown): EventMatcher | undefined {
  return matchers.find((matcher) => meets(event, matcher));
}

/**
 * A value as text: a string as it is; a list of blocks, their `text` fields joined; anything
 * else as its JSON.
 */
export function textOf(value: unknown): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value) && value.length > 0 && value.every((block) =>
    typeof block === "object" && block !== null && typeof block.text === "string")) {
    return value.map((block: { text: string }) => block.text).join("");
  }
  return JSON.stringify(value);
}

/** What one event is, by its record's matchers. */
export interface Read {
  readonly kind: FrameKind;
  /** The session id it names (`init`), if any. */
  readonly session_id: string | null;
  /** The result text it carries, and whether the matcher accumulates it. */
  readonly text: { readonly value: string; readonly accumulate: boolean } | null;
  /** The error it reports. */
  readonly error: string | null;
}

/** `event`, read by `events`. */
export function readEvent(events: EngineEvents, event: unknown): Read {
  const error = first(events.error, event);
  const result = first(events.final_text, event);
  const init = first(events.session_id, event);
  const kind: FrameKind = error !== undefined ? "error" : result !== undefined ? "result"
    : init !== undefined ? "init" : first(events.tool_call, event) !== undefined ? "tool_call"
      : first(events.usage, event) !== undefined ? "usage" : "event";
  const session = init === undefined ? undefined : valueAt(event, init.path);
  const text = result === undefined ? null : textOf(valueAt(event, result.path));
  return {
    kind,
    session_id: typeof session === "string" && session.length > 0 && session.length <= 256
      ? session : null,
    text: text === null ? null : { value: text, accumulate: result?.accumulate === true },
    error: error === undefined ? null
      : textOf(valueAt(event, error.path)) ?? "the engine reported an error",
  };
}
