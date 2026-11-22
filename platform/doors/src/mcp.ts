/**
 * The MCP door (research §4): the MCP SDK v2's stateless `createMcpHandler`
 * (`@modelcontextprotocol/server` 2.4.0, pinned exactly), one factory for both
 * revisions. 2026-07-28 is served on its own per-request envelope; 2025-11-25, which
 * clients on MCP SDK 1.x still speak, by the stateless legacy fallback
 * (`legacy: 'stateless'`), a fresh instance from the same factory per request, so the
 * two revisions can never serve different tools. GET and DELETE from an older client
 * get `405`. No session is kept anywhere.
 *
 * Each profile is told its own `instructions` (`INSTRUCTIONS`): a run's engine where its
 * working files go, an outside client's agent the shared brain's rules. Beside the tools, the
 * caller's agent definition is served as resources, the `agent` prompt and the Skills
 * extension (`definition.ts`).
 *
 * Every tool is a row of the owner's door table calling its door with the binder's binding
 * (`tools.ts`), and a request is served only the tools of its caller's profile, which the
 * object answers from the caller's grant (`toolProfile`) before any tool is listed; a
 * caller the object refuses (no grant, a revoked one, an ended run) is answered that
 * refusal, and no tool. An owner refusal or invalid request is the tool's error result,
 * with its reason and whether to call again, and when (`refused.ts` `asTool`); anything
 * else is a bug, logged in full and failing the call as
 * `internal error`. The `Origin` and credential checks (an owner's key or an OAuth token) run
 * before this (`doors.ts`); the handler is deliberately validation-free.
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
import { MCP_BODY_MAX } from "../../contract/src/limits";

import type { DoorsEnv } from "./env";
import { serveEvents } from "./events";
import { serveDefinition } from "./definition";
import { INSTRUCTIONS } from "./instructions";
import { answered, asJson, asTool } from "./refused";
import { type ProfileName, type Tool, toolsNamed } from "../../contract/src/doors";
import { callDoor } from "./tools";

const SERVER_INFO = Object.freeze({ name: "tinyassets", version: "1.0.0" });

/**
 * How long a 2025-11-25 caller is taken to wait for one tool call, in ms, since its leaving
 * cannot reach a stateless server: that revision's cancel is a `notifications/cancelled` sent
 * in a request of its own, which no instance here can match to the call (no session, and
 * request ids repeat across a grant's clients), and its client's own timeout sends only that,
 * keeping the call's stream open (MCP SDK 2.4.0's client, probed 2026-11-22). 10 minutes, the
 * longest a client that may speak it is known to wait (Gemini CLI 0.66.0's
 * `MCP_DEFAULT_TIMEOUT_MSEC`; the SDK's and pi's 60 s, the connectors' about 5 minutes as
 * reported), so no such caller is cut while it waits. A 2026-07-28 caller's leaving is its
 * request's stream closing, which that revision makes its cancel ("Cancellation") and which
 * reaches the call (`mcpReq.signal`), so its wait is its own.
 */
export const LEGACY_WAIT_MS = 600_000;

/**
 * The caller's leaving, for one tool call (`callDoor`'s `left`): its request ending (the
 * client's disconnect, or a 2026-07-28 client's cancel), and, for a 2025-11-25 request (the
 * legacy fallback's, which has no per-request envelope), `LEGACY_WAIT_MS` past its start.
 */
function leaving(request: { readonly signal: AbortSignal; readonly envelope?: unknown }):
    AbortSignal {
  if (request.envelope !== undefined) {
    return request.signal;
  }
  const waited = new AbortController();
  setTimeout(() => waited.abort(), LEGACY_WAIT_MS);
  return AbortSignal.any([request.signal, waited.signal]);
}

async function called(bound: Bound, tool: Tool, args: unknown, left: AbortSignal):
    Promise<CallToolResult> {
  let result: unknown;
  try {
    const refused = await answered<CallToolResult | null>(asTool, async () => {
      result = await callDoor(bound.stub, bound.binding, tool.door, args, left);
      return null;
    });
    if (refused !== null) {
      return refused;
    }
  } catch (error) {
    // A bug, never a refusal: reported here in full, and to the client without its detail
    // (the SDK answers every thrown error as the tool's error result).
    console.error(`the ${tool.name} tool failed`, error);
    throw new Error("internal error");
  }
  // Past the catch: a result past its bound is a bug `resultText` reports, once.
  return { content: [{ type: "text", text: resultText(tool.name, result) }] };
}

/**
 * The server one request is served by: the tools of its caller's profile, bound to it, and
 * MCP Events (`events.ts`), whose callbacks reach the gateway through `env`.
 */
function server(bound: Bound, tools: readonly Tool[], env: DoorsEnv,
                instructions: string): McpServer {
  const mcp = new McpServer({ ...SERVER_INFO }, { capabilities: { tools: {} }, instructions });
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
    }, (args: Record<string, unknown>, ctx) => called(bound, tool, args, leaving(ctx.mcpReq)));
  }
  serveEvents(mcp, bound, env);
  serveDefinition(mcp, bound);
  return mcp;
}

/**
 * The profile and tools the object serves this caller (`toolProfile`: its grant or its run),
 * and the tools its profile holds that the owner's policy withholds (`code` while code mode
 * is off): never listed here.
 */
export async function profileOf(bound: Bound):
    Promise<{ profile: ProfileName; tools: readonly Tool[]; withheld: readonly Tool[] }> {
  const { profile, tools, withheld } = await bound.stub.toolProfile(bound.binding);
  return { profile, tools: toolsNamed(tools), withheld: toolsNamed(withheld) };
}

/** An MCP request, or the object's refusal of the caller as JSON, with its reason. */
export function serveMcp(request: Request, bound: Bound, env: DoorsEnv): Promise<Response> {
  return answered(asJson, async () => {
    const got = await profileOf(bound);
    return createMcpHandler(() => server(bound, got.tools, env, INSTRUCTIONS[got.profile]), {
      legacy: "stateless", responseMode: "auto", maxRequestBodySize: MCP_BODY_MAX,
    }).fetch(request);
  });
}
