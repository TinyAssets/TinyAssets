/**
 * The `/api` door (research §5): an API key's call of one external tool,
 * `POST /api/tools/<name>` with the tool's arguments as one JSON object, answered with
 * the door's result. The same table as `/mcp` (`tools.ts`), so the two can never serve
 * different operations; the object checks every field.
 *
 * A key that does not verify is `401` and no object is reached; then the kill switch,
 * fail-closed. The request's `Cookie` header is gone before this sees it (`index.ts`).
 */
import { bearer } from "./app";
import { verifyKey } from "./api-keys";
import type { DoorsEnv } from "./config";
import { outsideGate } from "./control";
import { mediaType } from "./session";
import { callDoor, refusal, toolNamed } from "./tools";

const TOOL_PATH = /^\/api\/tools\/([a-z_]{1,64})$/;

function failure(status: number, message: string, headers: HeadersInit = {}): Response {
  return Response.json({ error: message }, { status, headers });
}

export async function serveApi(request: Request, env: DoorsEnv): Promise<Response> {
  const token = bearer(request);
  const bound = token === null ? null : await verifyKey(env, token);
  if (bound === null) {
    return failure(401, "a valid API key is required",
                   { "WWW-Authenticate": "Bearer realm=\"tinyassets-api\"" });
  }
  const gated = await outsideGate(env, bound.binding.owner);
  if (gated !== null) {
    return gated;
  }
  const match = TOOL_PATH.exec(new URL(request.url).pathname);
  const tool = match === null ? undefined : toolNamed(match[1]!);
  if (tool === undefined) {
    return failure(404, "no such tool");
  }
  if (request.method !== "POST") {
    return failure(405, "method not allowed");
  }
  if (mediaType(request) !== "application/json") {
    return failure(415, "the API takes application/json");
  }
  let args: unknown;
  try {
    args = await request.json();
  } catch {
    return failure(400, "the request is one JSON object");
  }
  if (typeof args !== "object" || args === null || Array.isArray(args)) {
    return failure(400, "the request is one JSON object");
  }
  try {
    return Response.json(await callDoor(bound.stub, bound.binding, tool, args));
  } catch (error) {
    const refused = refusal(error);
    if (refused === null) {
      throw error;
    }
    return failure(refused.status, refused.message);
  }
}
