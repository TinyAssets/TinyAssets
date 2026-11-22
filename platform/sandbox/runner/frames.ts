/**
 * The runner's frames (`run-events.ts`'s bounds, ADR-034 decision 10): each event the CLI
 * writes becomes one frame `{seq, at, event}`, `seq` from 1 and rising, and frames go to
 * the run's event door in posts within the object's bounds (`EVENTS_BODY_MAX` bytes,
 * `EVENT_FRAMES_MAX` frames, each frame's JSON at most `FRAME_UNITS_MAX` UTF-16 units). An
 * event longer than a frame may be is cut, marked, before it is posted (`ta_cut`, its
 * type and its head), the engine's own transcript keeping it whole. A line that is not
 * JSON is the CLI's own text, posted as `{type: "ta.line", text}`. A line shaped as the
 * runner's own event (`runnerShaped`) is never posted as it: it is `{type: "ta.cli_refused",
 * text}`, so every `ta.…` event the doors and the object read is the runner's. A job's posts
 * (`Poster`) are its heartbeat: once none is taken for a lease's TTL, the job ends.
 */
import { RUNNER_HEADER } from "../../contract/src/egress";
import { LEASE_TTL_S } from "../../contract/src/limits";
import { EVENT_FRAMES_MAX, EVENTS_BODY_MAX, FRAME_UNITS_MAX } from "../../contract/src/run-events";

/**
 * The runner's heartbeat: the lease's renewal cadence, a post at least every 20 s
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
 * every tool it runs may write, never is one.
 */
function runnerShaped(event: object): boolean {
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

/** How often the runner flushes what its CLI wrote: the frames' own cadence (§2.2). */
export const FLUSH_MS = 250;

/**
 * A job's posts to its event door: one in flight, held during a renewal's pause. Its posts
 * are its heartbeat, the one thing that keeps its job and its lease live at the owner's
 * object (`owner/src/runs.ts` settles a job lost, and `files.ts` lapses its lease, a lease's
 * TTL after its last post). So once none was taken for `LEASE_TTL_S`, since it was made or
 * since the last that was, none ever will be: it is refused then as the object would refuse
 * it (`lapse`), whether the object answers or cannot be reached, and its job ends. A job
 * the object no longer hears never runs on, and its sandbox is not renewed for it.
 */
export class Poster {
  holding = false;
  /** When a post was last taken; 0 before the first. */
  #last = 0;
  readonly #made = Date.now();
  #busy = false;
  #wait = FLUSH_MS;
  #refused: string | null = null;
  readonly #lapsed = Promise.withResolvers<void>();
  readonly url: string;
  readonly token: string;
  readonly outbox: Outbox;
  readonly onRefused: (why: string) => void;

  constructor(url: string, token: string, outbox: Outbox, onRefused: (why: string) => void) {
    this.url = url;
    this.token = token;
    this.outbox = outbox;
    this.onRefused = onRefused;
  }

  get refused(): string | null {
    return this.#refused;
  }

  /** Settled once its heartbeat lapsed: refused by the lease's TTL, not by the object. */
  get lapse(): Promise<void> {
    return this.#lapsed.promise;
  }

  /** Refused: told once (`onRefused`), and nothing more is posted. */
  #refuse(why: string): void {
    this.#refused = why;
    this.onRefused(why);
  }

  /** Whether it is refused: by the object, or by its lapse as of now (logged loudly). */
  #over(): boolean {
    if (this.#refused === null
        && Date.now() - Math.max(this.#last, this.#made) >= LEASE_TTL_S * 1000) {
      this.#lapsed.resolve();
      const why = `heartbeat_lapsed: no post was taken in ${LEASE_TTL_S} s, so the owner's ` +
        "object has settled the job lost and its lease has lapsed; the job ends";
      console.error(why);
      this.#refuse(why);
    }
    return this.#refused !== null;
  }

  /**
   * One post of the next frames, with `end` when they are the last ones: `taken`, `retry`
   * (the network, or `events_busy` after its `Retry-After`, a retry's wait doubling to at
   * most a heartbeat), or `refused` (the object ended the run: cancelled, revoked, settled,
   * its lease closed).
   */
  async #post(end: { exit_code: number } | null): Promise<"taken" | "retry" | "refused"> {
    const frames = this.outbox.peek();
    const last = end !== null && frames.length === this.outbox.pending;
    let response: Response;
    try {
      response = await fetch(this.url, {
        method: "POST",
        headers: { "content-type": "application/json", [RUNNER_HEADER]: this.token },
        body: JSON.stringify({ frames, ...(last ? { end } : {}) }) });
    } catch {
      await new Promise((resolve) => setTimeout(resolve, this.#wait));
      return "retry";
    }
    const text = await response.text();
    if (response.status === 200) {
      this.outbox.taken(frames);
      this.#last = Date.now();
      return "taken";
    }
    if (response.status === 503 || response.status === 429) {
      const after = Number(response.headers.get("retry-after") ?? "0") * 1000;
      await new Promise((resolve) => setTimeout(resolve, Math.min(HEARTBEAT_MS,
        Number.isFinite(after) && after > 0 ? after : this.#wait)));
      return "retry";
    }
    if (response.status === 401 || response.status === 403 || response.status === 404) {
      this.#refuse(text);
      return "refused";
    }
    await new Promise((resolve) => setTimeout(resolve, this.#wait));
    return "retry";
  }

  async #once(end: { exit_code: number } | null): Promise<"taken" | "retry" | "refused"> {
    this.#busy = true;
    try {
      const answer = await this.#post(end);
      this.#wait = answer === "retry" ? Math.min(this.#wait * 2, HEARTBEAT_MS) : FLUSH_MS;
      return answer;
    } finally {
      this.#busy = false;
    }
  }

  /** Post whatever waits, and a heartbeat when nothing went for `HEARTBEAT_MS`. */
  async tick(): Promise<void> {
    if (this.#over() || this.holding || this.#busy) {
      return;
    }
    if (this.outbox.pending > 0 || Date.now() - this.#last >= HEARTBEAT_MS) {
      await this.#once(null);
    }
  }

  /** The post in flight finished, and every frame that waits posted (or refused or lapsed). */
  async drain(): Promise<void> {
    while (this.#busy && !this.#over()) {
      await new Promise((resolve) => setTimeout(resolve, FLUSH_MS / 5));
    }
    while (this.outbox.pending > 0 && !this.#over()) {
      await this.#once(null);
    }
  }

  /** Every frame, then the end with the last of them, until taken, refused or lapsed. */
  async finish(exitCode: number): Promise<void> {
    for (;;) {
      if (this.#over()) {
        return;
      }
      if (this.holding || this.#busy) {
        await new Promise((resolve) => setTimeout(resolve, FLUSH_MS));
        continue;
      }
      const last = this.outbox.peek().length === this.outbox.pending;
      if (await this.#once({ exit_code: exitCode }) === "taken" && last) {
        return;
      }
    }
  }
}
