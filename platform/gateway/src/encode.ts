/**
 * pi-ai's `AssistantMessageEvent`s, encoded as pi-messages wire events: the exact
 * inverse of `createEventConverter` in pi-ai's `dist/api/pi-messages.js` (1.4.0, lines
 * 83-175), which folds the wire events back into an assistant message on the owner
 * object's side. Protocol code, not provider code: it never looks at a provider.
 *
 * The converter rebuilds each block from its events, so each wire event carries what
 * the converter reads and nothing else. A block's `*_end` carries its final content and
 * its signature (`textSignature`, or `thinkingSignature` and `redacted`), which the
 * adapter has set on the block by then; a tool call's `start` carries the id and name the
 * converter cannot otherwise know, and its `end` the whole call. `done` carries the stop
 * reason, pi-ai's `Usage` (tokens and catalog cost), the response id and the provider's
 * thinking level.
 *
 * An `error` event is not encoded here: the gateway sends its own fixed code, never the
 * adapter's message (`messages.ts`).
 */
import type { AssistantMessage, AssistantMessageEvent, PiMessagesEvent } from "@earendil-works/pi-ai";

import { Failure } from "./failure";

type Block = AssistantMessage["content"][number];

function block(partial: AssistantMessage, index: number): Block {
  const found = partial.content[index];
  if (found === undefined) {
    throw new Failure("upstream_error");
  }
  return found;
}

/** The wire `done` or `error` fields every terminal event shares. */
function terminal(message: AssistantMessage): Pick<
    Extract<PiMessagesEvent, { type: "done" }>, "usage" | "responseId" | "providerThinkingLevel"> {
  return {
    usage: message.usage,
    ...(message.responseId === undefined ? {} : { responseId: message.responseId }),
    ...(message.providerThinkingLevel === undefined
      ? {} : { providerThinkingLevel: message.providerThinkingLevel }),
  };
}

/** The wire event for one non-error event. */
export function encode(event: Exclude<AssistantMessageEvent, { type: "error" }>): PiMessagesEvent {
  switch (event.type) {
    case "start":
      return { type: "start" };
    case "text_start":
    case "thinking_start":
      return { type: event.type, contentIndex: event.contentIndex };
    case "text_delta":
    case "thinking_delta":
    case "toolcall_delta":
      return { type: event.type, contentIndex: event.contentIndex, delta: event.delta };
    case "text_end": {
      const text = block(event.partial, event.contentIndex);
      const signature = text.type === "text" ? text.textSignature : undefined;
      return { type: "text_end", contentIndex: event.contentIndex, content: event.content,
               ...(signature === undefined ? {} : { contentSignature: signature }) };
    }
    case "thinking_end": {
      const thinking = block(event.partial, event.contentIndex);
      const signature = thinking.type === "thinking" ? thinking.thinkingSignature : undefined;
      const redacted = thinking.type === "thinking" ? thinking.redacted : undefined;
      return { type: "thinking_end", contentIndex: event.contentIndex, content: event.content,
               ...(signature === undefined ? {} : { contentSignature: signature }),
               ...(redacted === undefined ? {} : { redacted }) };
    }
    case "toolcall_start": {
      const call = block(event.partial, event.contentIndex);
      if (call.type !== "toolCall") {
        throw new Failure("upstream_error");
      }
      return { type: "toolcall_start", contentIndex: event.contentIndex, id: call.id,
               toolName: call.name };
    }
    case "toolcall_end":
      return { type: "toolcall_end", contentIndex: event.contentIndex, toolCall: event.toolCall };
    case "done":
      if (event.reason === "deferred") {
        // pi-messages has no deferred handle, and the gateway never asks for one.
        throw new Failure("unsupported_deferred");
      }
      return { type: "done", reason: event.reason, ...terminal(event.message) };
  }
}

/** The wire `error` event: pi-ai's usage and ids, with the gateway's fixed code as text. */
export function encodeError(message: AssistantMessage, reason: "aborted" | "error",
                            code: string): PiMessagesEvent {
  return { type: "error", reason, errorMessage: code, ...terminal(message) };
}

/** One server-sent event frame. JSON never holds a raw newline, so one `data:` line. */
export function frame(event: PiMessagesEvent): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}
