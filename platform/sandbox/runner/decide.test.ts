/**
 * The hook helper (R5 P-hooks): a CLI's pre-tool hook's call asked of the decide door that
 * the hook's own command line names, by the run's credential from its environment, answered
 * in the one shape every CLI here reads (exit 0 and `{}` lets it be, exit 2 and the reason on
 * stderr refuses it), and every failure of its own a refusal; and `check`, the tools the hook
 * matches. Nothing the agent could set in the environment names its door or lets a call be.
 */
import { createServer, type Server } from "node:http";
import { Readable } from "node:stream";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { DECIDE_HEADER } from "../../contract/src/egress";
import { DECIDE_ENV, decideCommand, matcherOf } from "./decide";

const RUN = `run_${"1".repeat(32)}`;
const CREDENTIAL = `${RUN}:${"2".repeat(64)}`;
/** The door's answer, by the body asked, and what was asked of it. */
let answer: (body: Record<string, unknown>) => { status: number; value: unknown };
const asked: unknown[] = [];
let server: Server;
let door = "";

beforeAll(async () => {
  server = createServer((request, response) => {
    let raw = "";
    request.on("data", (chunk: Buffer) => { raw += chunk.toString("utf8"); });
    request.on("end", () => {
      const body = JSON.parse(raw) as Record<string, unknown>;
      asked.push({ path: request.url, credential: request.headers[DECIDE_HEADER], body });
      const { status, value } = answer(body);
      response.writeHead(status, { "content-type": "application/json" });
      response.end(JSON.stringify(value));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  door = `http://127.0.0.1:${(server.address() as { port: number }).port}/decide`;
});

afterAll(() => {
  server.close();
});

/** The helper run on `input` with `args` (the door first), its exit code and what it wrote. */
async function helper(input: string, args?: string[],
                      env: Record<string, string> = { [DECIDE_ENV]: CREDENTIAL }) {
  let out = "";
  let err = "";
  const code = await decideCommand(args ?? [door], env, Readable.from([input]),
                                   { write: (text: string) => { out += text; } },
                                   { write: (text: string) => { err += text; } });
  return { code, out, err };
}

const CALL = JSON.stringify({ session_id: "s", hook_event_name: "PreToolUse",
                              tool_name: "Bash", tool_input: { command: "ls" } });

describe("the hook helper", () => {
  it("asks the door its command line names, by the run's credential, and lets be as every " +
     "CLI reads it", async () => {
    answer = () => ({ status: 200, value: { allow: true } });
    asked.length = 0;
    expect(await helper(CALL)).toEqual({ code: 0, out: "{}\n", err: "" });
    expect(asked).toEqual([{ path: "/decide", credential: CREDENTIAL,
                             body: { tool_name: "Bash", tool_input: { command: "ls" } } }]);
  });

  it("refuses with the door's own reason, which the CLI hands the model", async () => {
    answer = () => ({ status: 403, value: { error: { code: "held_for_owner",
                                                      message: "held for your owner: card c" } } });
    expect(await helper(CALL)).toEqual({ code: 2, out: "", err: "held for your owner: card c\n" });
  });

  it("refuses the review's spoof: a credential that is an answer, not a run's", async () => {
    // The reviewer's reproduction: the variable the agent can set made a data: URL that
    // answers allow. It is the run's credential now, never an address, so it names no run.
    answer = () => ({ status: 200, value: { allow: true } });
    asked.length = 0;
    const spoof = await helper(CALL, [door],
                               { [DECIDE_ENV]: 'data:application/json,{"allow":true}' });
    expect([spoof.code, spoof.out]).toEqual([2, ""]);
    expect(spoof.err).toMatch(/no decide credential/);
    expect(asked).toEqual([]);
  });

  it.each([
    ["a door that is an answer", ['data:application/json,{"allow":true}']],
    ["a door on another path", ["http://127.0.0.1:1/r/x/decide"]],
    ["a door with a query", ["http://127.0.0.1:1/decide?allow=1"]],
    ["no door", [""]],
    ["a mode it does not take", ["http://127.0.0.1:1/decide", "allow"]],
  ])("refuses a command line with %s", async (_what, args) => {
    answer = () => ({ status: 200, value: { allow: true } });
    const { code, out, err } = await helper(CALL, args);
    expect([code, out]).toEqual([2, ""]);
    expect(err).toMatch(/names no decide door/);
  });

  it.each([
    ["no credential", CALL, undefined, {}],
    ["a call it cannot read", "not json", undefined, undefined],
    ["the door unreachable", CALL, ["http://127.0.0.1:1/decide"], undefined],
  ])("refuses, never lets a call through, with %s", async (_what, input, args, env) => {
    answer = () => ({ status: 200, value: { allow: true } });
    const { code, out, err } = await helper(input, args, env);
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

  it("checks: the tools the hook matches and their generation, or a refusal", async () => {
    answer = (body) => ({ status: 200, value: body["check"] === true
      ? { tools: ["Bash"], generation: 3 } : {} });
    expect(await helper("", [door, "check"]))
      .toEqual({ code: 0, out: `{"tools":["Bash"],"generation":3}\n`, err: "" });
    answer = () => ({ status: 403, value: { error: { message: "suspended" } } });
    expect(await helper("", [door, "check"])).toEqual({ code: 2, out: "", err: "suspended\n" });
  });

  it("matches exactly the tools it is given", () => {
    const matcher = new RegExp(matcherOf(["Bash", "mcp__gmail__send_.*"]));
    expect(["Bash", "mcp__gmail__send_mail"].every((tool) => matcher.test(tool))).toBe(true);
    expect(["Bashful", "xBash", "mcp__gmail__read"].some((tool) => matcher.test(tool)))
      .toBe(false);
  });
});
