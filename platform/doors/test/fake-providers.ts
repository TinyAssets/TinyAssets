/**
 * The model providers a connect reaches, for the doors' tests: every global `fetch` the
 * gateway Worker makes is routed here (vitest.config.ts), and the tests reach it as
 * `PROVIDERS`. Each request is logged, read back from `https://status.test/log`, so a
 * test sees what the gateway sent where.
 *
 * - Groq's model list (`api.groq.com/openai/v1/models`): `200` for a bearer key starting
 *   `gsk-good`, else `401` with a body that quotes the key.
 * - Google's (`generativelanguage.googleapis.com/v1beta/models`): `200` for an
 *   `x-goog-api-key` starting `AIza-good`.
 * - OpenRouter's key exchange (`openrouter.ai/api/v1/auth/keys`): JSON in; the code
 *   `or-good` buys `sk-or-bought`, `or-badkey` a key with a space; any other is `403`. Its model list: `200` for a bearer
 *   key starting `sk-or-`.
 * - A test OAuth server (`auth.test/token`): the code `good-code` gets `at-good`, any
 *   other `invalid_grant`. OpenAI's model list (`api.openai.com/v1/models`): `200` for
 *   the bearer `at-good`.
 *
 * It is test code: its module state (the log) is the fake's own.
 */
const log: unknown[] = [];

function bearer(request: Request): string {
  return (request.headers.get("authorization") ?? "").replace(/^Bearer /, "");
}

function listed(ok: boolean, credential: string): Response {
  return ok ? Response.json({ data: [] })
    : Response.json({ error: `invalid key ${credential}` }, { status: 401 });
}

async function token(request: Request): Promise<Response> {
  const form = new URLSearchParams(await request.text());
  if (form.get("grant_type") !== "authorization_code" || form.get("code") !== "good-code"
      || form.get("code_verifier") === null || form.get("client_id") !== "tinyassets-test") {
    return Response.json({ error: "invalid_grant",
                           error_description: `code ${form.get("code")} is not valid` },
                         { status: 400 });
  }
  return Response.json({ access_token: "at-good", token_type: "Bearer", expires_in: 3600,
                         refresh_token: "rt-good", scope: "models.read" });
}

async function keyExchange(request: Request): Promise<Response> {
  const body = JSON.parse(await request.text()) as Record<string, string>;
  if (body["code"] === "or-badkey") {
    return Response.json({ key: "sk-or-has a space" });
  }
  if (body["code"] !== "or-good" || body["code_challenge_method"] !== "S256"
      || typeof body["code_verifier"] !== "string") {
    return Response.json({ error: { message: `code ${body["code"]} is not valid` } },
                         { status: 403 });
  }
  return Response.json({ key: "sk-or-bought", user_id: "u1" });
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.hostname === "status.test") {
      if (request.method === "DELETE") {
        log.length = 0;
        return new Response(null, { status: 204 });
      }
      return Response.json(log);
    }
    const body = request.method === "GET" ? "" : await request.clone().text();
    log.push({ method: request.method, url: request.url,
               headers: Object.fromEntries(request.headers), body });
    const where = `${url.hostname}${url.pathname}`;
    switch (where) {
      case "api.groq.com/openai/v1/models":
        return listed(bearer(request).startsWith("gsk-good"), bearer(request));
      case "generativelanguage.googleapis.com/v1beta/models": {
        const key = request.headers.get("x-goog-api-key") ?? "";
        return listed(key.startsWith("AIza-good"), key);
      }
      case "openrouter.ai/api/v1/auth/keys":
        return keyExchange(request);
      case "openrouter.ai/api/v1/models/user":
        return listed(bearer(request).startsWith("sk-or-"), bearer(request));
      case "auth.test/token":
        return token(request);
      case "api.openai.com/v1/models":
        return listed(bearer(request) === "at-good", bearer(request));
      default:
        return new Response(`no fake for ${where}`, { status: 599 });
    }
  },
};
