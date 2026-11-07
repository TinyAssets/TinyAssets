/**
 * The `/api` door (research §5): an API key's call of one external tool,
 * `POST /api/tools/<name>` with the tool's arguments as one JSON object, answered with
 * the door's result. The same table as `/mcp` (`tools.ts`), held to the same profile
 * (`profiled`: the key's grant names it), so the two can never serve different operations;
 * the object checks every field. A tool outside the key's profile is `404`, as one that
 * does not exist.
 *
 * A key that does not verify is `401` and no object is reached; then the kill switch,
 * fail-closed. The request's `Cookie` header is gone before this sees it (`index.ts`).
 */
import { answerJson } from "./answers";
import { bearer } from "./app";
import { verifyKey } from "./api-keys";
import { refusalOf } from "../../contract/src/refusal";

import { jsonObject } from "./body";
import { API_BODY_MAX, type DoorsEnv } from "./config";
import { outsideRead } from "./control";
import { withTiers } from "./binder";
import { mediaType } from "./session";
import { profiled } from "./mcp";
import { refusalResponse } from "./refused";
import { callDoor } from "./tools";

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
  const gated = await outsideRead(env, bound.binding.owner);
  if (gated instanceof Response) {
    return gated;
  }
  const held = withTiers(bound, gated);
  const tools = await profiled(held);
  if (tools instanceof Response) {
    return tools;
  }
  const match = TOOL_PATH.exec(new URL(request.url).pathname);
  const tool = match === null ? undefined : tools.find((row) => row.name === match[1]);
  if (tool === undefined) {
    return failure(404, "no such tool");
  }
  if (request.method !== "POST") {
    return failure(405, "method not allowed");
  }
  if (mediaType(request) !== "application/json") {
    return failure(415, "the API takes application/json");
  }
  const args = await jsonObject(request, API_BODY_MAX);
  if (args instanceof Response) {
    return args;
  }
  try {
    return answerJson(tool.name, await callDoor(held.stub, held.binding, tool, args));
  } catch (error) {
    const refused = refusalOf(error);
    if (refused === null) {
      throw error;
    }
    return refusalResponse(refused);
  }
}
