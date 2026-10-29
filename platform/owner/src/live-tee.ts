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
 * instance, never at module level, and are bounded; a listener that throws is dropped.
 * `close` ends one conversation's subscriptions (a grant or policy change, A2), or all.
 * The transport to the owner's app (a hibernating WebSocket) is the app slice's.
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

export const TEE_LIMITS: TeeLimits = Object.freeze({ perSession: 16, sessions: 256 });

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
    }
  }

  /** Hand `event` to the conversation's listeners, now; a throwing one is dropped. */
  publish(sessionId: string | undefined, event: AssistantMessageEvent | LiveEvent): void {
    if (sessionId === undefined) {
      return;
    }
    const set = this.#listeners.get(sessionId);
    if (set === undefined) {
      return;
    }
    const shown = "partial" in event || "message" in event || "error" in event
      ? liveEvent(event as AssistantMessageEvent) : event as LiveEvent;
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
    }
  }
}
