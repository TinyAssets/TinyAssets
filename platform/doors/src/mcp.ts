/**
 * The MCP door (research §4): the MCP SDK v2's stateless `createMcpHandler`
 * (`@modelcontextprotocol/server` 2.3.1, pinned exactly), one factory for both
 * revisions. 2026-07-28 is served on its own per-request envelope; 2025-11-25, which
 * clients on MCP SDK 1.x still speak, by the stateless legacy fallback
 * (`legacy: 'stateless'`), a fresh instance from the same factory per request, so the
 * two revisions can never serve different tools. GET and DELETE from an older client
 * get `405`. No session is kept anywhere.
 *
 * Each profile is told its own `instructions`: a run's engine where its working files go
 * (`RUN_INSTRUCTIONS`), an outside client's agent the shared brain's rules
 * (`CLIENT_INSTRUCTIONS`).
 *
 * Every tool is a row of `tools.ts` calling its owner door with the binder's binding, and
 * a request is served only the tools of its caller's profile, which the object answers
 * from the caller's grant (`toolProfile`) before any tool is listed; a caller the object
 * refuses (no grant, a revoked one, an ended run) is answered that refusal, and no tool.
 * An owner refusal or invalid request is the tool's error result, with its reason;
 * anything else is a bug, logged in full and failing the call as `internal error`. The `Origin` and token checks run before this
 * (`index.ts`, `oauth.ts`); the handler is deliberately validation-free.
 *
 * **Each result is sent once, as text** (memory_research.md §3): one `text` block holding
 * the result's JSON, and no `structuredContent`. No tool declares an `outputSchema`, so the
 * spec asks for no structured copy (its SHOULD is text beside structured content, for older
 * clients), and both ChatGPT and Claude read text; a second copy doubled what the isolate
 * held and what ChatGPT's model read. The owner pages every read to `RESULT_CHARS`
 * (`owner/src/pages.ts`); a result past it here is a bug, failed loudly, never cut.
 */
import {
  type CallToolResult, createMcpHandler, fromJsonSchema, McpServer,
} from "@modelcontextprotocol/server";

import { resultText } from "./answers";
import type { Bound } from "./binder";
import type { DoorsEnv } from "./config";
import { serveEvents } from "./events";
import { CLIENT_INSTRUCTIONS, RUN_INSTRUCTIONS } from "./instructions";
import { callDoor, type ProfileName, refusal, type Tool, toolsNamed } from "./tools";

export { CLIENT_INSTRUCTIONS, RUN_INSTRUCTIONS };

export const SERVER_INFO = Object.freeze({ name: "tinyassets", version: "1.0.0" });

async function called(bound: Bound, tool: Tool, args: unknown): Promise<CallToolResult> {
  let result: unknown;
  try {
    result = await callDoor(bound.stub, bound.binding, tool, args);
  } catch (error) {
    const refused = refusal(error);
    if (refused === null) {
      // A bug, never a refusal: reported here in full, and to the client without its
      // detail (the SDK answers every thrown error as the tool's error result).
      console.error(`the ${tool.name} tool failed`, error);
      throw new Error("internal error");
    }
    return { isError: true, content: [{ type: "text", text: refused.message }] };
  }
  return { content: [{ type: "text", text: resultText(tool.name, result) }] };
}

/**
 * The server one request is served by: the tools of its caller's profile, bound to it, and
 * MCP Events (`events.ts`, slice W2), whose callbacks reach the gateway through `env`.
 */
export function server(bound: Bound, tools: readonly Tool[], env: DoorsEnv,
                       instructions?: string): McpServer {
  const mcp = new McpServer({ ...SERVER_INFO }, {
    capabilities: { tools: {} }, ...(instructions === undefined ? {} : { instructions }) });
  for (const tool of tools) {
    mcp.registerTool(tool.name, {
      title: tool.title,
      description: tool.description,
      inputSchema: fromJsonSchema<Record<string, unknown>>(tool.input),
      annotations: {
        title: tool.title,
        readOnlyHint: tool.readOnlyHint,
        destructiveHint: tool.destructiveHint,
        idempotentHint: tool.idempotentHint,
        openWorldHint: tool.openWorldHint,
      },
    }, (args: Record<string, unknown>) => called(bound, tool, args));
  }
  serveEvents(mcp, bound, env);
  return mcp;
}

/**
 * The tools the object serves this caller (`toolProfile`, from its grant or its run), or
 * the object's refusal as the answer, with its reason.
 */
export async function profiled(bound: Bound): Promise<readonly Tool[] | Response> {
  const got = await profileOf(bound);
  return got instanceof Response ? got : got.tools;
}

async function profileOf(bound: Bound):
    Promise<{ profile: ProfileName; tools: readonly Tool[] } | Response> {
  try {
    const { profile, tools } = await bound.stub.toolProfile(bound.binding);
    return { profile, tools: toolsNamed(tools) };
  } catch (error) {
    const refused = refusal(error);
    if (refused === null) {
      throw error;
    }
    return Response.json({ error: refused.message },
                         { status: refused.status, headers: { "Cache-Control": "no-store" } });
  }
}

export async function serveMcp(request: Request, bound: Bound, env: DoorsEnv):
    Promise<Response> {
  const got = await profileOf(bound);
  if (got instanceof Response) {
    return got;
  }
  const instructions = got.profile === "run" ? RUN_INSTRUCTIONS : CLIENT_INSTRUCTIONS;
  return createMcpHandler(() => server(bound, got.tools, env, instructions), {
    legacy: "stateless", responseMode: "auto", maxRequestBodySize: MCP_BODY_MAX,
  }).fetch(request);
}

/**
 * A request's body on the MCP door, a technical ceiling: the MCP SDK's own default
 * (`maxRequestBodySize`, 4 MiB), kept because the SDK materializes a JSON-RPC message whole,
 * so its ceiling is what the doors' isolate holds a few at a time; bodies up to
 * `BODY_BYTES_MAX` (8 MiB) go by `/api` (memshape_research.md §4).
 */
export const MCP_BODY_MAX = 4 * 1024 * 1024;
