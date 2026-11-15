/**
 * The `tools/list` recorder (`npm run record`, `ci/record.ts`): MCP `tools/list` byte for byte,
 * each profile's raw answer body (a custom client, a listed one, the directory's, a run's,
 * with code mode on and off), and the app's tool list, each one `TOOLS` line.
 */
import { env } from "cloudflare:test";
import { beforeEach, describe, it } from "vitest";

import { egressTokenOf, GENERATION_HEADER, SANDBOX_HEADER } from "../../../contract/src/egress";
import type { Binding } from "../../../contract/src/binding";
import type { SandboxStart } from "../../../contract/src/sandbox";
import { mcpOrigin } from "../../src/binder";
import {
  appPost, call, connect, legacyPost, OPEN, registerClient, serveFirstPartyDocument,
  setControl,
} from "../support";
import { owned } from "./support";

let restore: () => void = () => undefined;
beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

const INIT = { jsonrpc: "2.0", id: 0, method: "initialize", params: {
  protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "rec", version: "1" } } };
const LIST = { jsonrpc: "2.0", id: 7, method: "tools/list" };

async function listedRaw(token: string): Promise<string> {
  await legacyPost(token, INIT);
  await legacyPost(token, { jsonrpc: "2.0", method: "notifications/initialized" });
  const response = await legacyPost(token, LIST);
  return `${response.status} ${JSON.stringify(await response.text())}`;
}

async function runListed(start: SandboxStart): Promise<string> {
  const post = (body: unknown) => call(new Request(start.urls.mcp, {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-11-25", [SANDBOX_HEADER]: egressTokenOf(start.network!.network)!,
               [GENERATION_HEADER]: "1" } }));
  await post(INIT);
  await post({ jsonrpc: "2.0", method: "notifications/initialized" });
  const response = await post(LIST);
  return `${response.status} ${JSON.stringify(await response.text())}`;
}

describe("tools/list, byte for byte", () => {
  it("every profile", async () => {
    const { who, app, binding: own } = await owned(`user_${"ab".repeat(16)}`);
    const stub = env.OWNER.getByName(who);
    const custom = await connect(await registerClient("Custom"), { owner: who });
    const listedClient = await registerClient("Listed");
    const listed = await connect(listedClient, { owner: who });
    const origin = await mcpOrigin(listedClient.clientId, listedClient.redirectUri);
    const session: Binding = { ...own, session: app.session };
    console.log(`TOOLS\tlisted-custom\t${await listedRaw(listed.access_token)}`);
    const row = (await stub.grantList(own)).clients.find((c) => c.client === origin.client)!;
    await stub.grantSet(session, { client: origin.client, source: row.source, mode: row.mode,
                                   scopes: row.scopes as never, expected_revision: row.revision,
                                   name: "", profile: "directory" });
    console.log(`TOOLS\tcustom\t${await listedRaw(custom.access_token)}`);
    console.log(`TOOLS\tdirectory\t${await listedRaw(listed.access_token)}`);
    const apptools = await appPost("tools", app.access_token, null, {});
    console.log(`TOOLS\tapp\t${apptools.status} ${await apptools.text()}`);
    await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "t-1" });
    // The owner's fake sandbox Worker records each start, by owner (T1a's
    // `owner/test/fake-sandbox.ts`, bound to the doors' tests as `SANDBOX_LOG`).
    const [start] = (await env.SANDBOX_LOG.recorded(who)).starts as SandboxStart[];
    console.log(`TOOLS\trun\t${await runListed(start!)}`);
    // Code mode off: every custom tool but code.
    const { generation, policy } = await stub.policyRead(own);
    const { tools: _tools, ...rest } = policy as typeof policy & { tools?: unknown };
    await stub.policyWrite(session, { expected_generation: generation,
                                      policy: { ...rest, codemode: { on: false } } });
    console.log(`TOOLS\tcustom-nocode\t${await listedRaw(custom.access_token)}`);
    console.log(`TOOLS\trun-nocode\t${await runListed(start!)}`);
  });
});
