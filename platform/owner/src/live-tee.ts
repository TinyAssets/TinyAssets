/**
 * The live tee (agent research §2.4; gateway research §3.4): every event of a model call,
 * as it streams, for the owner's live views, kept in memory only and never committed.
 *
 * pi commits a streaming answer at most once a second (`agent.ts`, the progress policy),
 * since each commit is a storage write, and its own event streams are derived from those
 * commits, so they move in one-second steps. The `gateway` provider's wrapper publishes
 * each event here as it forwards it to pi, keyed by the request's `sessionId`, pi's
 * persisted per-conversation provider identity (`pi.provider`). A viewer resolves its
 * conversation's `sessionId` once, reconciles with the conversation's committed events
 * (the truth), and shows these until the next commit overtakes them.
 *
 * What is published is the event without pi-ai's running `partial` (a delta and where it
 * goes, never the whole answer again), then `end`. Subscriptions live on the object's
 * instance, never at module level, and are bounded (`TEE_LIMITS`: conversations listened
 * to, and listeners of each); a listener that throws is dropped. `close` ends one
 * conversation's subscriptions (a grant or policy change, A2), or all.
 *
 * The owner's app sockets (`sockets.ts`, A6) show the tee in the view's own vocabulary:
 * appends to `pi.live.generation.message`'s content blocks by `contentIndex`, laid over
 * the last committed view, so the app has the one reducer pi's watch needs. For that the
 * tee keeps, only for a conversation someone listens to, the text and thinking of its
 * current attempt's blocks (`blocks`), from the `start` it heard to its `done` or `error`;
 * a stream whose `start` came before its first listener is shown by its commits alone. Each
 * block's text is a growable UTF-16 buffer (`LiveText`), so an append is amortized O(1) in
 * its length and a tail is read without copying the rest. A stream keeps at most
 * `TEE_TEXT_MAX` code units and `TEE_BLOCKS_MAX` blocks, past which it is shown by its
 * commits alone, and at
 * most `TEE_LIMITS.sessions` streams are kept, one per conversation listened to: the
 * worst case is the memory guard's (`guards/memory-bound.test.ts`). pi's next minor makes
 * its own partials reach watches at once (deferred persistence, pi audit F2): this module,
 * its `publish` calls and its close paths are deleted then (tasks, the pi upgrade).
 */
import type { AssistantMessageEvent } from "@earendil-works/pi-ai";

import { Refused } from "./errors";

/** One published event: its type, and the delta and its place for the streaming kinds. */
export interface LiveEvent {
  readonly type: AssistantMessageEvent["type"] | "end";
  readonly contentIndex?: number;
  readonly delta?: string;
  readonly reason?: string;
}

export type LiveListener = (event: LiveEvent) => void;

export interface TeeLimits {
  readonly perSession: number;
  readonly sessions: number;
}

/**
 * Conversations listened to at once, and listeners of each: the owner app's sockets (at
 * most `OWNER_SOCKETS`, 16, an owner; `THREAD_SOCKETS`, 4, a thread) are its listeners.
 */
export const TEE_LIMITS: TeeLimits = Object.freeze({ perSession: 4, sessions: 16 });

/** The most text one stream's blocks keep, past which it is shown by its commits alone. */
export const TEE_TEXT_MAX = 65_536;
/** The most blocks one stream keeps, past which it is shown by its commits alone. */
export const TEE_BLOCKS_MAX = 64;

/** A block's text as the tee holds it: a growable UTF-16 buffer, doubled as it fills. */
export class LiveText {
  #units = new Uint16Array(64);
  #length = 0;

  get length(): number {
    return this.#length;
  }

  /** Append `text`, growing the buffer by doubling: amortized O(1) a code unit. */
  append(text: string): void {
    const needed = this.#length + text.length;
    if (needed > this.#units.length) {
      let size = this.#units.length;
      while (size < needed) {
        size *= 2;
      }
      const grown = new Uint16Array(size);
      grown.set(this.#units.subarray(0, this.#length));
      this.#units = grown;
    }
    for (let i = 0; i < text.length; i++) {
      this.#units[this.#length + i] = text.charCodeAt(i);
    }
    this.#length = needed;
  }

  /** The text from code unit `at` to the end. */
  from(at: number): string {
    let out = "";
    for (let i = at; i < this.#length; i += 8192) {
      out += String.fromCharCode(...this.#units.subarray(i, Math.min(i + 8192, this.#length)));
    }
    return out;
  }

  /** Whether the text begins with `prefix`. */
  startsWith(prefix: string): boolean {
    if (prefix.length > this.#length) {
      return false;
    }
    for (let i = 0; i < prefix.length; i++) {
      if (this.#units[i] !== prefix.charCodeAt(i)) {
        return false;
      }
    }
    return true;
  }
}

/** One block of a streaming answer as the tee has seen it: its kind, and its text so far. */
export interface LiveBlock {
  readonly type: "text" | "thinking";
  readonly text: LiveText;
}

/** One stream's blocks, by content index; `over` once past `TEE_TEXT_MAX`. */
interface Stream {
  readonly blocks: Map<number, LiveBlock>;
  size: number;
  over: boolean;
}

/** The event as published: no partial, no whole message. */
export function liveEvent(event: AssistantMessageEvent): LiveEvent {
  switch (event.type) {
    case "text_delta":
    case "thinking_delta":
    case "toolcall_delta":
      return { type: event.type, contentIndex: event.contentIndex, delta: event.delta };
    case "done":
    case "error":
      return { type: event.type, reason: event.reason };
    case "start":
      return { type: event.type };
    default:
      return { type: event.type, contentIndex: event.contentIndex };
  }
}

export class LiveTee {
  readonly #listeners = new Map<string, Set<LiveListener>>();
  /** The streams in flight of the conversations listened to: each current attempt's blocks. */
  readonly #streams = new Map<string, Stream>();
  readonly #limits: TeeLimits;

  constructor(limits: TeeLimits = TEE_LIMITS) {
    this.#limits = limits;
  }

  /** Listen to one conversation's live events; returns the unsubscribe. */
  subscribe(sessionId: string, listener: LiveListener): () => void {
    let set = this.#listeners.get(sessionId);
    if (set === undefined) {
      if (this.#listeners.size >= this.#limits.sessions) {
        throw new Refused("too many live views");
      }
      set = new Set();
      this.#listeners.set(sessionId, set);
    }
    if (set.size >= this.#limits.perSession) {
      throw new Refused("too many live views of this conversation");
    }
    set.add(listener);
    return () => this.#drop(sessionId, listener);
  }

  #drop(sessionId: string, listener: LiveListener): void {
    const set = this.#listeners.get(sessionId);
    set?.delete(listener);
    if (set?.size === 0) {
      this.#listeners.delete(sessionId);
      this.#streams.delete(sessionId);  // no one listens: its text goes
    }
  }

  /**
   * The blocks of `sessionId`'s current attempt as the tee has seen them, or undefined when
   * it is not streaming, no one listened from its start, or it passed `TEE_TEXT_MAX`.
   */
  blocks(sessionId: string): ReadonlyMap<number, LiveBlock> | undefined {
    const stream = this.#streams.get(sessionId);
    return stream === undefined || stream.over ? undefined : stream.blocks;
  }

  /**
   * Keep `event` in its stream's blocks, for a conversation listened to: a `start` begins
   * one, a `done` or `error` ends it.
   */
  #keep(sessionId: string, event: LiveEvent): void {
    if (event.type === "done" || event.type === "error" || event.type === "end") {
      this.#streams.delete(sessionId);
      return;
    }
    if (!this.#listeners.has(sessionId)) {
      return;
    }
    if (event.type === "start") {
      this.#streams.set(sessionId, { blocks: new Map(), size: 0, over: false });
      return;
    }
    const stream = this.#streams.get(sessionId);
    const index = event.contentIndex;
    if (stream === undefined || stream.over || index === undefined) {
      return;
    }
    const type = event.type.startsWith("text_") ? "text" as const
      : event.type.startsWith("thinking_") ? "thinking" as const : null;
    if (type === null) {
      return;
    }
    let held = stream.blocks.get(index);
    if (held !== undefined && held.type !== type) {
      stream.over = true;  // a block that changed kind: shown by its commits alone
      stream.blocks.clear();
      return;
    }
    const delta = event.delta ?? "";
    stream.size += delta.length;
    if (stream.size > TEE_TEXT_MAX) {
      stream.over = true;
      stream.blocks.clear();
      return;
    }
    if (held === undefined) {
      if (stream.blocks.size >= TEE_BLOCKS_MAX) {
        stream.over = true;
        stream.blocks.clear();
        return;
      }
      held = { type, text: new LiveText() };
      stream.blocks.set(index, held);
    }
    held.text.append(delta);
  }

  /** Hand `event` to the conversation's listeners, now; a throwing one is dropped. */
  publish(sessionId: string | undefined, event: AssistantMessageEvent | LiveEvent): void {
    if (sessionId === undefined) {
      return;
    }
    const shown = "partial" in event || "message" in event || "error" in event
      ? liveEvent(event as AssistantMessageEvent) : event as LiveEvent;
    this.#keep(sessionId, shown);
    const set = this.#listeners.get(sessionId);
    if (set === undefined) {
      return;
    }
    for (const listener of [...set]) {
      try {
        listener(shown);
      } catch {
        this.#drop(sessionId, listener);
      }
    }
  }

  /** End one conversation's live views (each hears `end`), or every one. */
  close(sessionId?: string): void {
    const ids = sessionId === undefined ? [...this.#listeners.keys()] : [sessionId];
    for (const id of ids) {
      this.publish(id, { type: "end" });
      this.#listeners.delete(id);
      this.#streams.delete(id);
    }
    if (sessionId === undefined) {
      this.#streams.clear();
    }
  }
}
