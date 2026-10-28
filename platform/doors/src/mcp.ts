/**
 * The MCP door (research §4): the MCP SDK v2's stateless `createMcpHandler`
 * (`@modelcontextprotocol/server` 2.3.1, pinned exactly), one factory for both
 * revisions. 2026-07-28 is served on its own per-request envelope; 2025-11-25, which
 * clients on MCP SDK 1.x still speak, by the stateless legacy fallback
 * (`legacy: 'stateless'`), a fresh instance from the same factory per request, so the
 * two revisions can never serve different tools. GET and DELETE from an older client
 * get `405`. No session is kept anywhere.
 *
 * Every tool is a row of `tools.ts` calling its owner door with the binder's binding.
 * An owner refusal or invalid request is the tool's error result, with its reason;
 * anything else is a bug, logged in full and failing the call as `internal error`. The `Origin` and token checks run before this
 * (`index.ts`, `oauth.ts`); the handler is deliberately validation-free.
 */
import {
  type CallToolResult, createMcpHandler, fromJsonSchema, McpServer,
} from "@modelcontextprotocol/server";

import type { Bound } from "./binder";
import { callDoor, refusal, type Tool, toolTable } from "./tools";

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
  return { content: [{ type: "text", text: JSON.stringify(result) }],
           structuredContent: result as Record<string, unknown> };
}

/** The server one request is served by: every tool of the table, bound to this caller. */
export function server(bound: Bound): McpServer {
  const mcp = new McpServer({ ...SERVER_INFO }, { capabilities: { tools: {} } });
  for (const tool of toolTable()) {
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
  return mcp;
}

export function serveMcp(request: Request, bound: Bound): Promise<Response> {
  return createMcpHandler(() => server(bound), { legacy: "stateless", responseMode: "auto" })
    .fetch(request);
}
