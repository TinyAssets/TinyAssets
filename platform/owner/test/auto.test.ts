/**
 * `auto`, the virtual model (`auto.ts`), and the live tee's bounds (`live-tee.ts`): the
 * ranking data file, stickiness and fallback read from the request's own transcript.
 */
import type { AssistantMessage, Message } from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";

import {
  blockingThreshold, type Candidate, CONTEXT_OVERFLOW, compactionFor, physicalId, rankOf, ranked,
  route,
} from "../src/auto";
import type { ModelInfo, Usable } from "../src/connections";
import { LiveTee, type LiveEvent } from "../src/live-tee";
import ranking from "../src/model-ranking.json" with { type: "json" };

function model(id: string, output = 1): ModelInfo {
  return { id, name: id, reasoning: true, input: ["text"], contextWindow: 100_000,
           maxTokens: 8_000, cost: { input: 1, output, cacheRead: 0, cacheWrite: 0 } };
}

function usable(connection: string, provider: string, created: number,
                ...models: ModelInfo[]): Usable {
  return { connection, source: provider, provider, display_name: connection, created_at: created,
           models };
}

const user = (text: string): Message => ({ role: "user", content: text, timestamp: 1 });

function answer(candidate: Candidate, extra: Partial<AssistantMessage> = {}): Message {
  return { role: "assistant", content: [{ type: "text", text: "ok" }], api: "pi-messages",
           provider: "gateway", model: physicalId(candidate), stopReason: "stop",
           usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
                    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
           timestamp: 2, ...extra };
}

const toolResult: Message = { role: "toolResult", toolCallId: "t1", toolName: "read",
                              content: [{ type: "text", text: "x" }], isError: false,
                              timestamp: 3 };

const CONNECTED = [
  usable("c-a", "anthropic", 1, model("claude-sonnet-4-5"), model("claude-fable-5"),
         model("claude-fable-5-1")),
  usable("c-o", "openrouter", 2, model("anthropic/claude-opus-5"), model("vendor/unranked-x", 9),
         model("vendor/unranked-y", 20)),
  usable("c-g", "google", 3, model("gemini-4-argon")),
];

describe("the ranking", () => {
  it("is a data file of provider and model patterns", () => {
    expect(ranking.version).toBe(1);
    expect(ranking.ranks.length).toBeGreaterThan(5);
    expect(ranking.ranks.every((entry) => typeof entry.provider === "string"
                               && typeof entry.model === "string")).toBe(true);
  });

  it("orders the earliest entry first, newer ids first in an entry, then unmatched by " +
     "output price", () => {
    expect(ranked(CONNECTED).map(physicalId)).toEqual([
      "c-a/claude-fable-5-1", "c-a/claude-fable-5", "c-g/gemini-4-argon",
      "c-o/anthropic/claude-opus-5", "c-a/claude-sonnet-4-5", "c-o/vendor/unranked-y",
      "c-o/vendor/unranked-x"]);
  });

  it("matches a provider exactly or any, and a glob against the id or its last part", () => {
    const entries = [{ provider: "anthropic", model: "claude-*-5" }, { provider: "*", model: "x" }];
    expect(rankOf({ provider: "anthropic", model: model("claude-opus-5") }, entries)).toBe(0);
    expect(rankOf({ provider: "openrouter", model: model("anthropic/claude-opus-5") }, entries))
      .toBe(2);
    expect(rankOf({ provider: "openrouter", model: model("vendor/x") }, entries)).toBe(1);
    expect(rankOf({ provider: "openrouter", model: model("xx") }, entries)).toBe(2);
    expect(rankOf({ provider: "anthropic", model: model("claude.opus.5") },
                  [{ provider: "anthropic", model: "claude.opus.5" }])).toBe(0);
    expect(rankOf({ provider: "anthropic", model: model("claudeXopusX5") },
                  [{ provider: "anthropic", model: "claude.opus.5" }])).toBe(1);
  });

  it("breaks a tie by the older connection", () => {
    const twice = [usable("c-new", "anthropic", 9, model("claude-fable-5")),
                   usable("c-old", "anthropic", 1, model("claude-fable-5"))];
    expect(ranked(twice).map(physicalId)).toEqual(["c-old/claude-fable-5", "c-new/claude-fable-5"]);
  });
});

describe("a route", () => {
  const candidates = ranked(CONNECTED);
  const [first, second, third] = candidates;

  it("is the strongest for a user's message, and none with nothing connected", () => {
    expect(route(candidates, [user("hi")])).toBe(first);
    expect(route(candidates, [])).toBe(first);
    expect(route([], [user("hi")])).toBeUndefined();
  });

  it("stays on the model that answered for a continuation, while it is connected", () => {
    const messages = [user("hi"), answer(third!), toolResult];
    expect(route(candidates, messages)).toBe(third);
    expect(route(candidates.filter((c) => c !== third), messages)).toBe(first);
    // A user's new message routes afresh.
    expect(route(candidates, [...messages, answer(third!), user("next")])).toBe(first);
  });

  it("falls to the next-ranked after a failure before any output, and stays after one " +
     "with output", () => {
    const failed = (candidate: Candidate, content: AssistantMessage["content"] = []) =>
      answer(candidate, { stopReason: "error", content });
    expect(route(candidates, [user("hi"), failed(first!)])).toBe(second);
    expect(route(candidates, [user("hi"), failed(second!)])).toBe(third);
    expect(route(candidates, [user("hi"), failed(candidates.at(-1)!)])).toBe(first);
    expect(route(candidates, [user("hi"), failed(second!, [{ type: "text", text: "pa" }])]))
      .toBe(second);
    expect(route([first!], [user("hi"), failed(first!)])).toBe(first);
    // A failed model no longer connected: the strongest.
    expect(route(candidates.slice(1), [user("hi"), failed(first!)])).toBe(second);
  });

  it("is the strongest after an answer that ended without an error, with or without output",
     () => {
    // pi asks again after an aborted or a stopped answer only on a retry or a new input.
    expect(route(candidates, [user("hi"), answer(second!, { stopReason: "aborted",
                                                             content: [] })])).toBe(first);
    expect(route(candidates, [user("hi"), answer(second!)])).toBe(first);
    expect(route(candidates, [user("hi"), answer(first!, { stopReason: "length",
                                                            content: [] })])).toBe(first);
  });
});

describe("the live tee", () => {
  it("publishes deltas without the running answer, drops a throwing listener, and ends",
     () => {
    const tee = new LiveTee({ perSession: 2, sessions: 1 });
    const seen: LiveEvent[] = [];
    const off = tee.subscribe("s", (event) => seen.push(event));
    tee.subscribe("s", () => {
      throw new Error("a broken view");
    });
    expect(() => tee.subscribe("s", () => undefined)).toThrow("too many live views of this " +
                                                              "conversation");
    expect(() => tee.subscribe("t", () => undefined)).toThrow("too many live views");
    const partial = answer(ranked(CONNECTED)[0]!) as AssistantMessage;
    tee.publish("s", { type: "text_delta", contentIndex: 0, delta: "he", partial });
    tee.publish("s", { type: "text_delta", contentIndex: 0, delta: "y", partial });
    tee.publish("other", { type: "text_delta", contentIndex: 0, delta: "no", partial });
    tee.publish(undefined, { type: "text_delta", contentIndex: 0, delta: "no", partial });
    tee.publish("s", { type: "done", reason: "stop", message: partial });
    expect(seen).toEqual([{ type: "text_delta", contentIndex: 0, delta: "he" },
                          { type: "text_delta", contentIndex: 0, delta: "y" },
                          { type: "done", reason: "stop" }]);
    // The broken listener was dropped: there is room for another.
    const late: LiveEvent[] = [];
    tee.subscribe("s", (event) => late.push(event));
    tee.close("s");
    expect(late).toEqual([{ type: "end" }]);
    expect(seen.at(-1)).toEqual({ type: "end" });
    off();
    tee.subscribe("t", () => undefined);  // closed sessions free their room
  });
});

describe("each model's compaction thresholds", () => {
  it("are pi's defaults from 256K tokens, and in proportion to a smaller window", () => {
    expect(compactionFor({ contextWindow: 1_000_000 })).toEqual(
      { reserveTokens: 16_384, keepRecentTokens: 20_000, backgroundTokens: 32_768 });
    expect(compactionFor({ contextWindow: 262_144 })).toEqual(
      { reserveTokens: 16_384, keepRecentTokens: 20_000, backgroundTokens: 32_768 });
    expect(compactionFor({ contextWindow: 200_000 })).toEqual(
      { reserveTokens: 16_384, keepRecentTokens: 20_000, backgroundTokens: 25_000 });
    expect(compactionFor({ contextWindow: 32_000 })).toEqual(
      { reserveTokens: 4_000, keepRecentTokens: 8_000, backgroundTokens: 4_000 });
    expect(compactionFor({ contextWindow: 8_191 })).toEqual(
      { reserveTokens: 1_023, keepRecentTokens: 2_047, backgroundTokens: 1_023 });
    expect(blockingThreshold({ contextWindow: 64_000 })).toBe(56_000);
    expect(blockingThreshold({ contextWindow: 1_000_000 })).toBe(1_000_000 - 16_384);
  });
});

describe("a retry after an overflow", () => {
  it("stays on the model whose context was compacted, where another failure moves on", () => {
    const candidates = ranked(CONNECTED);
    const [, second] = candidates;
    const failed = (errorMessage: string) => answer(second!, { content: [], stopReason: "error",
                                                                errorMessage });
    expect(route(candidates, [user("q"), failed(CONTEXT_OVERFLOW)])).toBe(second);
    expect(route(candidates, [user("q"), failed("upstream_overloaded")])).toBe(candidates[2]);
  });
});
