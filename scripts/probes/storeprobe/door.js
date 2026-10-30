/**
 * ta-storeprobe-door: the one public URL of the store probe
 * (openspec/changes/clean-sheet-platform/store-probe.md).
 *
 * Throwaway. `.github/workflows/store-probe.yml` deploys it to workers.dev and deletes it.
 * Every route needs the per-run `x-probe-key`; every route but /health needs `x-probe-run`.
 *
 *   GET  /health
 *   POST /call/<copy>/<object>/<method>   body: the case's parameters (JSON object)
 *
 * `<copy>` is `main`, `low` or `raised`, the owner-like script whose Owner class the call
 * reaches by name; `<method>` is one of the Owner's case methods. The door adds the run
 * id, the object's name and the colo this request entered at (`request.cf.colo`).
 */
const COPIES = Object.freeze({ main: "OWNER", low: "OWNER_LOW", raised: "OWNER_RAISED" });
const METHODS = Object.freeze(["read", "p3", "p1Control", "p1Burst", "p4Seed", "arm"]);
const NAME = /^[\w.-]{1,64}$/;

function json(body, status = 200) {
  return Response.json(body, { status });
}

export default {
  async fetch(request, env) {
    if (!env.RUN_KEY) {
      return json({ error: "RUN_KEY is not set" }, 500);
    }
    if (request.headers.get("x-probe-key") !== env.RUN_KEY) {
      return json({ error: "forbidden" }, 403);
    }
    const url = new URL(request.url);
    const [, verb, copy, object, method] = url.pathname.split("/");
    if (verb === "health") {
      return json({ ok: true, copies: Object.keys(COPIES).filter((c) => env[COPIES[c]]) });
    }
    const run = request.headers.get("x-probe-run") ?? "";
    if (!NAME.test(run)) {
      return json({ error: "x-probe-run is missing or malformed" }, 400);
    }
    const binding = env[COPIES[copy]];
    if (verb !== "call" || request.method !== "POST" || !binding || !NAME.test(object ?? "")
        || !METHODS.includes(method)) {
      return json({ error: "not found" }, 404);
    }
    const body = await request.json().catch(() => null);
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return json({ error: "the body must be a JSON object" }, 400);
    }
    const doorColo = request.cf?.colo ?? null;
    try {
      const result = await binding.getByName(object)[method]({ ...body, run, object, doorColo });
      return json({ result, doorColo });
    } catch (error) {
      const text = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      return json({ error: text.slice(0, 500), doorColo }, 502);
    }
  },
};
