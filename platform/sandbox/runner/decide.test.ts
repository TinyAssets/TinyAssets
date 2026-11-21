/**
 * The hook helper (R5 P-hooks): a CLI's pre-tool hook's call asked of the run's decide door,
 * answered in the one shape every CLI here reads (exit 0 and `{}` lets it be, exit 2 and the
 * reason on stderr refuses it), and every failure of its own a refusal; and `check`, the
 * tools the hook matches.
 */
import { createServer, type Server } from "node:http";
import { Readable } from "node:stream";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { DECIDE_ENV, decideCommand, matcherOf } from "./decide";

/** The door's answer, by the body asked, and the bodies asked. */
let answer: (body: Record<string, unknown>) => { status: number; value: unknown };
const asked: unknown[] = [];
let server: Server;
let url = "";

beforeAll(async () => {
  server = createServer((request, response) => {
    let raw = "";
    request.on("data", (chunk: Buffer) => { raw += chunk.toString("utf8"); });
    request.on("end", () => {
      const body = JSON.parse(raw) as Record<string, unknown>;
      asked.push({ path: request.url, body });
      const { status, value } = answer(body);
      response.writeHead(status, { "content-type": "application/json" });
      response.end(JSON.stringify(value));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}/r/x/decide`;
});

afterAll(() => {
  server.close();
});

/** The helper run on `input` with `args`, its exit code and what it wrote. */
async function helper(input: string, args: string[] = [], env: Record<string, string> = {
  [DECIDE_ENV]: url }) {
  let out = "";
  let err = "";
  const code = await decideCommand(args, env, Readable.from([input]),
                                   { write: (text: string) => { out += text; } },
                                   { write: (text: string) => { err += text; } });
  return { code, out, err };
}

const CALL = JSON.stringify({ session_id: "s", hook_event_name: "PreToolUse",
                              tool_name: "Bash", tool_input: { command: "ls" } });

describe("the hook helper", () => {
  it("asks the door of the CLI's call, and lets it be as every CLI reads it", async () => {
    answer = () => ({ status: 200, value: { allow: true } });
    asked.length = 0;
    expect(await helper(CALL)).toEqual({ code: 0, out: "{}\n", err: "" });
    expect(asked).toEqual([{ path: "/r/x/decide",
                             body: { tool_name: "Bash", tool_input: { command: "ls" } } }]);
  });

  it("refuses with the door's own reason, which the CLI hands the model", async () => {
    answer = () => ({ status: 403, value: { error: { code: "held_for_owner",
                                                      message: "held for your owner: card c" } } });
    expect(await helper(CALL)).toEqual({ code: 2, out: "", err: "held for your owner: card c\n" });
  });

  it.each([
    ["no door in its environment", CALL, {}],
    ["a call it cannot read", "not json", undefined],
    ["the door unreachable", CALL, { [DECIDE_ENV]: "http://127.0.0.1:1/r/x/decide" }],
  ])("refuses, never lets a call through, with %s", async (_what, input, env) => {
    answer = () => ({ status: 200, value: { allow: true } });
    const { code, out, err } = await helper(input, [], env);
    expect([code, out]).toEqual([2, ""]);
    expect(err).toMatch(/^the owner's rules could not be checked: /);
  });

  it.each([
    ["an answer that is no answer", { status: 200, value: { tools: [] } }],
    ["a failure with no message", { status: 500, value: "boom" }],
  ])("refuses %s", async (_what, given) => {
    answer = () => given;
    expect((await helper(CALL)).code).toBe(2);
  });

  it("checks: the tools the hook matches, or a refusal", async () => {
    answer = (body) => ({ status: 200, value: body["check"] === true ? { tools: ["Bash"] } : {} });
    expect(await helper("", ["check"])).toEqual({ code: 0, out: `{"tools":["Bash"]}\n`, err: "" });
    answer = () => ({ status: 403, value: { error: { message: "suspended" } } });
    expect(await helper("", ["check"])).toEqual({ code: 2, out: "", err: "suspended\n" });
  });

  it("matches exactly the tools it is given", () => {
    const matcher = new RegExp(matcherOf(["Bash", "mcp__gmail__send_.*"]));
    expect(["Bash", "mcp__gmail__send_mail"].every((tool) => matcher.test(tool))).toBe(true);
    expect(["Bashful", "xBash", "mcp__gmail__read"].some((tool) => matcher.test(tool)))
      .toBe(false);
  });
});
