/**
 * The runner's frames (`run-events.ts`'s bounds, ADR-034 decision 10): each event the CLI
 * writes becomes one frame `{seq, at, event}`, `seq` from 1 and rising, and frames go to
 * the run's event door in posts within the object's bounds (`EVENTS_BODY_MAX` bytes,
 * `EVENT_FRAMES_MAX` frames, each frame's JSON at most `FRAME_UNITS_MAX` UTF-16 units). An
 * event longer than a frame may be is cut, marked, before it is posted (`ta_cut`, its
 * type and its head), the engine's own transcript keeping it whole. A line that is not
 * JSON is the CLI's own text, posted as `{type: "ta.line", text}`. A line shaped as the
 * runner's own event (`runnerShaped`) is never posted as it: it is `{type: "ta.cli_refused",
 * text}`, so every `ta.…` event the doors and the object read is the runner's.
 */
import { EVENT_FRAMES_MAX, EVENTS_BODY_MAX, FRAME_UNITS_MAX } from "../../contract/src/run-events";

/**
 * The runner's heartbeat: slice R's lease renewal cadence, a post at least every 20 s
 * (`Poster`), well inside the lease's TTL.
 */
export const HEARTBEAT_MS = 20_000;

export interface PostedFrame {
  readonly seq: number;
  readonly at: number;
  readonly event: unknown;
}

/**
 * Whether a frame fits: its JSON within `FRAME_UNITS_MAX` UTF-16 units, as the object
 * measures it, and its UTF-8 bytes within one post, beside the post's own fields.
 */
function fits(frame: PostedFrame): boolean {
  const text = JSON.stringify(frame);
  return text.length <= FRAME_UNITS_MAX
    && new TextEncoder().encode(text).length <= EVENTS_BODY_MAX - 128;
}

/** The event, cut to fit one frame and marked, when it does not. */
export function fitted(seq: number, at: number, event: unknown): PostedFrame {
  const whole = { seq, at, event };
  if (fits(whole)) {
    return whole;
  }
  const type = typeof event === "object" && event !== null && !Array.isArray(event)
    && typeof (event as { type?: unknown }).type === "string"
    ? (event as { type: string }).type.slice(0, 128) : null;
  const text = JSON.stringify(event);
  // Room for the frame's own fields and the marker, with the head's JSON escaping.
  let head = text.slice(0, FRAME_UNITS_MAX - 512);
  let frame: PostedFrame = { seq, at, event: { ta_cut: true, type, units: text.length, head } };
  while (!fits(frame)) {
    head = head.slice(0, Math.floor(head.length * 0.9));
    frame = { seq, at, event: { ta_cut: true, type, units: text.length, head } };
  }
  return frame;
}

/**
 * Whether an event is shaped as the runner's own: its `type` the runner's (`ta.` and on) or
 * its cut marker (`ta_cut`). The runner's alone: a CLI's line, which the run's agent and
 * every tool it runs may write (round 8, R8-1), never is one.
 */
export function runnerShaped(event: object): boolean {
  if (Array.isArray(event)) {
    return false;
  }
  const type = (event as Record<string, unknown>)["type"];
  return (typeof type === "string" && type.startsWith("ta.")) || Object.hasOwn(event, "ta_cut");
}

/**
 * A line of the CLI's stdout as its event: its JSON, or its text; one shaped as the runner's
 * own event refused, loudly, its text kept (`ta.cli_refused`, by its code: the app says why).
 */
export function lineEvent(line: string): unknown {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    return { type: "ta.line", text: line };  // not JSON: the CLI's own text
  }
  if (typeof parsed !== "object" || parsed === null) {
    return { type: "ta.line", text: line };
  }
  return runnerShaped(parsed) ? { type: "ta.cli_refused", text: line, code: "runner_type" }
    : parsed;
}

/** The frames waiting to be posted, in order, each numbered once. */
export class Outbox {
  #seq: number;
  readonly #queue: PostedFrame[] = [];

  constructor(lastSeq = 0) {
    this.#seq = lastSeq;
  }

  get seq(): number {
    return this.#seq;
  }

  get pending(): number {
    return this.#queue.length;
  }

  push(event: unknown, at = Date.now()): PostedFrame {
    this.#seq += 1;
    const frame = fitted(this.#seq, at, event);
    this.#queue.push(frame);
    return frame;
  }

  /**
   * The next post's frames, within the bounds, left queued until `taken` is called with
   * them (a post the object did not take is posted again, the same frames).
   */
  peek(): PostedFrame[] {
    const batch: PostedFrame[] = [];
    // `{"frames":[` and `]}` and the commas between frames, and room for `end`.
    let bytes = 64;
    for (const frame of this.#queue) {
      const size = new TextEncoder().encode(JSON.stringify(frame)).length + 1;
      if (batch.length >= EVENT_FRAMES_MAX || bytes + size > EVENTS_BODY_MAX) {
        break;
      }
      batch.push(frame);
      bytes += size;
    }
    return batch;
  }

  /** The frames a post delivered, gone from the queue. */
  taken(frames: readonly PostedFrame[]): void {
    this.#queue.splice(0, frames.length);
  }
}
