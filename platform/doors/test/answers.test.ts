/**
 * The doors' answers (`answers.ts`): a tool's result is its JSON once, and a result past one
 * answer is a bug failed loudly, never cut. A write past one answer is answered whole, its
 * row without its body (review H3).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RESULT_CHARS } from "../../contract/src/encoded";
import { resultText } from "../src/answers";
import {
  answerOf, connect, legacyTool, OPEN, registerClient, setControl, sqlIn,
} from "./support";

beforeEach(async () => {
  await setControl(OPEN);
});

describe("a tool's answer", () => {
  it("is its result's JSON once, and past one answer a loud bug, never cut", () => {
    const result = { text: "\"".repeat(1000) };
    expect(resultText("t", result)).toBe(JSON.stringify(result));
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      expect(() => resultText("big", { text: String.fromCharCode(1).repeat(RESULT_CHARS / 6) }))
        .toThrow("internal error");
      expect(logged.mock.calls[0]![0]).toMatch(/^the big tool answered \d+ bytes of JSON/);
    } finally {
      logged.mockRestore();
    }
  });

  it("answers a collection_write longer than one answer with its row, not its body (H3)",
     async () => {
    const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
    const tokens = await connect(await registerClient(), { owner: who });
    const body = "x".repeat(60_000);
    const result = await legacyTool(tokens.access_token, "collection_write", {
      collection: "board", row_id: "big", body, expected_etag: "", private: false });
    expect(result.isError).toBeUndefined();
    const answer = answerOf(result) as { row: Record<string, unknown> };
    expect(answer.row).toMatchObject({ row_id: "big", revision: 1, body_bytes: 60_000 });
    expect(answer.row).not.toHaveProperty("body");
    expect(await sqlIn(who, "SELECT row_id, body_bytes FROM collection_rows WHERE " +
                            "row_id = 'big'")).toEqual([{ row_id: "big", body_bytes: 60_000 }]);
  });
});
