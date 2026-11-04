/**
 * The MCP door (research §4): the MCP SDK v2's stateless `createMcpHandler`
 * (`@modelcontextprotocol/server` 2.3.1, pinned exactly), one factory for both
 * revisions. 2026-07-28 is served on its own per-request envelope; 2025-11-25, which
 * clients on MCP SDK 1.x still speak, by the stateless legacy fallback
 * (`legacy: 'stateless'`), a fresh instance from the same factory per request, so the
 * two revisions can never serve different tools. GET and DELETE from an older client
 * get `405`. No session is kept anywhere.
 *
 * Every tool is a row of `tools.ts` calling its owner door with the binder's binding, and
 * a request is served only the tools of its caller's profile, which the object answers
 * from the caller's grant (`toolProfile`) before any tool is listed; a caller the object
 * refuses (no grant, a revoked one, an ended run) is answered that refusal, and no tool.
 * An owner refusal or invalid request is the tool's error result, with its reason;
 * anything else is a bug, logged in full and failing the call as `internal error`. The `Origin` and token checks run before this
 * (`index.ts`, `oauth.ts`); the handler is deliberately validation-free.
 */
import {
  type CallToolResult, createMcpHandler, fromJsonSchema, McpServer,
} from "@modelcontextprotocol/server";

import type { Bound } from "./binder";
import type { DoorsEnv } from "./config";
import { serveEvents } from "./events";
import { callDoor, type ProfileName, refusal, type Tool, toolsNamed } from "./tools";

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

/**
 * What a run's engine is told when it connects (the MCP `instructions` every engine reads
 * from its servers, so one text reaches every engine through the one door): where its
 * working files go. Clones, builds and caches go to its scratch area (`$TA_SCRATCH`, on the
 * sandbox's own disk, never synced and never counted in the owner's storage); what the user
 * keeps goes to `/workspace`, or leaves by `git push`.
 */
export const RUN_INSTRUCTIONS = "Working files: clone repositories, install dependencies and " +
  "build in your scratch area ($TA_SCRATCH, on this sandbox's own disk). Scratch is kept " +
  "between runs as a cache, is never synced and never counts toward the user's storage; it " +
  "may be cleared when the disk runs low. Clone a repository there once and refresh it with " +
  "`git fetch`. Work the user keeps goes in /workspace (it syncs and counts toward their " +
  "storage) or leaves by `git push`; a patch of a cloned repository goes in /workspace.";

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
  const instructions = got.profile === "run" ? RUN_INSTRUCTIONS : undefined;
  return createMcpHandler(() => server(bound, got.tools, env, instructions), { legacy: "stateless",
                                                      responseMode: "auto" })
    .fetch(request);
}
