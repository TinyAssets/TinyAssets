/**
 * The owner app's sockets in a test (A6, `sockets.ts`): the binder's upgrade as the doors
 * build it, and a client that reads frames as an app does, applying each commit's exact
 * Chord ops to its view, and the live tee's appends to a display copy reset at each commit.
 */
import { apply } from "@earendil-works/chord/delta";
import type { ConversationView } from "@earendil-works/pi-durable";

import type { Binding } from "../src/owner-do";
import {
  BINDING_HEADER, SOCKET_URL, THREAD_HEADER, TICKET_HEADER,
} from "../src/socket-shape";

/** The runtime's reserved methods on the object: none is a door. */
export const RESERVED_METHODS: readonly string[] = Object.freeze([
  "alarm", "fetch", "webSocketClose", "webSocketError", "webSocketMessage"]);

/** The binder's upgrade, as `doors/src/binder.ts`'s `socketRequest` builds it. */
export function upgrade(binding: Binding, thread: string, secret: string,
                        extra: Record<string, string> = {}, url: string = SOCKET_URL): Request {
  const headers = new Headers({ Upgrade: "websocket", ...extra });
  headers.set(BINDING_HEADER, JSON.stringify(binding));
  headers.set(THREAD_HEADER, thread);
  headers.set(TICKET_HEADER, secret);
  return new Request(url, { headers });
}

/** The owner's binding as the ticket door binds it: its token's grant and expiry. */
export function granted(owner: string, grant = "grant-a",
                        expiresAt = Math.floor(Date.now() / 1000) + 3600): Binding {
  return { owner, caller: owner, origin: null, grant, expires_at: expiresAt };
}

/** A ticket's secret for `thread`, minted as the ticket door mints it. */
export async function mint(stub: { socketTicket: (binding: Binding, request: {
  thread_id: string }) => Promise<{ secret: string }> }, binding: Binding, thread: string):
    Promise<string> {
  return (await stub.socketTicket(binding, { thread_id: thread })).secret;
}

/** One frame as the object sent it. */
export type Frame = { v: number; type: string; seq?: number; base?: number; value?: unknown;
                      ops?: unknown[] };

/** An app's end of one socket: its frames, its view as it applied them, and its close. */
export class AppClient {
  readonly ws: WebSocket;
  readonly frames: Frame[] = [];
  /** The view as the commits left it, and the display: the view with the live appends. */
  view: unknown = undefined;
  display: unknown = undefined;
  graph: unknown = undefined;
  closed: { code: number; reason: string } | null = null;
  #acking: boolean;

  constructor(ws: WebSocket, acking = true) {
    this.ws = ws;
    this.#acking = acking;
    ws.accept();
    ws.addEventListener("message", (event) => {
      const frame = JSON.parse(String(event.data)) as Frame;
      this.frames.push(frame);
      this.#apply(frame);
      if (this.#acking && frame.seq !== undefined) {
        this.send({ v: 1, type: "ack", seq: frame.seq });
      }
    });
    ws.addEventListener("close", (event) => {
      this.closed = { code: event.code, reason: event.reason };
    });
  }

  #apply(frame: Frame): void {
    switch (frame.type) {
      case "view":
        this.view = frame.value;
        this.display = structuredClone(this.view);
        break;
      case "ops":
        this.view = apply(this.view as never, frame.ops as never);
        this.display = structuredClone(this.view);
        break;
      case "live":
        this.display = apply(this.display as never, frame.ops as never);
        break;
      case "graph":
        this.graph = frame.value;
        break;
      case "graph_ops":
        this.graph = apply(this.graph as never, frame.ops as never);
        break;
    }
  }

  /** Start or stop acknowledging: a slow app stops, and catches up once it starts. */
  acking(on: boolean): void {
    this.#acking = on;
    if (on) {
      const last = this.frames.filter((frame) => frame.seq !== undefined).at(-1);
      if (last !== undefined) {
        this.send({ v: 1, type: "ack", seq: last.seq });
      }
    }
  }

  send(frame: unknown): void {
    this.ws.send(typeof frame === "string" ? frame : JSON.stringify(frame));
  }

  /** The view as a `ConversationView`. */
  get conversation(): ConversationView {
    return this.view as ConversationView;
  }

  /** Wait until `check` holds of this client. */
  async until(check: (client: AppClient) => boolean, what: string, tries = 800): Promise<void> {
    for (let i = 0; i < tries; i++) {
      if (check(this)) {
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    throw new Error(`timed out waiting for ${what}; frames: ${JSON.stringify(
      this.frames.map((frame) => frame.type))}, closed: ${JSON.stringify(this.closed)}`);
  }
}

/** A socket's answer: the client end, once it is a 101. */
export function opened(response: Response, acking = true): AppClient {
  if (response.status !== 101 || response.webSocket === null) {
    throw new Error(`the upgrade was answered ${response.status}`);
  }
  return new AppClient(response.webSocket, acking);
}

/** The text an answer's committed and live blocks show. */
export function shownText(value: unknown): string {
  const live = (value as { docs?: Record<string, { generation?: { message?: {
    content?: { type: string; text?: string }[] } } }> } | undefined)
    ?.docs?.["pi.live"]?.generation?.message?.content ?? [];
  return live.map((block) => (block.type === "text" ? block.text ?? "" : "")).join("");
}
