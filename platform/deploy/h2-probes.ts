/**
 * H2's live probes, narrowed to code mode (tasks 5, "H2 live"; slice L builds them, the
 * founder runs them after the dark deploy, once the doors are routed and a probe owner
 * exists): what workerd cannot show about a `code` script in production. Each probe is a
 * measurement printed as a row, never a pass a mock could fake; a probe whose answer is a
 * transport failure stops the run loudly.
 *
 * Inputs (environment): TA_DOORS_URL (default https://tinyassets.io); TA_PROBE_KEY, an API
 * key of probe owner A whose grant holds read_graph (code mode is on by default);
 * TA_PROBE_KEY_B, owner B's; and, for the revoke probe, TA_PROBE_APP_TOKEN (owner A's
 * first-party token) and TA_PROBE_KEY_ID (the id of TA_PROBE_KEY, which that probe
 * revokes, so it runs last).
 *
 * 1. A script past its interrupt budget, against the codemode Worker's `cpu_ms`: when and how
 *    it is stopped.
 * 2. The blocking isolation: 10 s of A's compute, and B's one call meanwhile.
 * 3. Whether `exchange` calls count as subrequests: 1,100 tool calls in one script.
 * 4. pi-codemode's own deadline while a script calls tools back to back: when it stops.
 * 5. While A's script loops tool calls: an owner app read's latency, and a revoke's reach
 *    (the script's next call refused); the alarm's reach is read from the owner Worker's
 *    logs for that minute.
 */
import { fileURLToPath } from "node:url";

import { ORIGIN } from "./facts.ts";

/** The probe owners' keys the probes need, by the names the workflow's apply job sets. */
export const INPUTS = Object.freeze({ a: "TA_PROBE_KEY", b: "TA_PROBE_KEY_B",
                                      token: "TA_PROBE_APP_TOKEN", keyId: "TA_PROBE_KEY_ID" });
const BASE = (process.env["TA_DOORS_URL"] ?? ORIGIN).replace(/\/$/, "");

function need(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set (h2-probes.ts names what each probe needs)`);
  }
  return value;
}

interface Answer {
  readonly status: number;
  readonly ms: number;
  readonly body: unknown;
}

async function code(key: string, script: string): Promise<Answer> {
  const started = Date.now();
  const response = await fetch(`${BASE}/api/tools/code`, {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ script }) });
  const text = await response.text();
  let body: unknown = text;
  try {
    body = JSON.parse(text);
  } catch {
    // a non-JSON answer is reported as its text
  }
  return { status: response.status, ms: Date.now() - started, body };
}

function row(probe: string, answer: Answer): void {
  const detail = JSON.stringify(answer.body).slice(0, 160);
  console.log(`| ${probe} | ${answer.status} | ${answer.ms} ms | ${detail} |`);
}

async function main(): Promise<void> {
  const a = need(INPUTS.a);
  const b = need(INPUTS.b);
  console.log("| probe | status | time | answer |\n|---|---|---|---|");
  row("1 interrupt budget vs cpu_ms", await code(a, "let i = 0; for (;;) { i++; }"));
  const busy = code(a, "const t = Date.now(); while (Date.now() - t < 10000) {} return 1;");
  await new Promise((done) => setTimeout(done, 500));
  row("2 another owner's call during 10 s of compute", await code(b, "return 1;"));
  row("2 the 10 s script", await busy);
  row("3 1,100 tool calls in one script", await code(a,
    "let n = 0; for (let i = 0; i < 1100; i++) { await tools.agents_list({}); n++; } return n;"));
  row("4 tool calls back to back past the deadline", await code(a,
    "const t = Date.now(); let n = 0; while (Date.now() - t < 600000) " +
    "{ await tools.agents_list({}); n++; } return { n, ms: Date.now() - t };"));
  const token = process.env[INPUTS.token];
  const keyId = process.env[INPUTS.keyId];
  if (!token || !keyId) {
    console.log(`| 5 revoke and app read during a script | skipped | | ${INPUTS.token} and ` +
                `${INPUTS.keyId} unset |`);
    return;
  }
  const looping = code(a, "const t = Date.now(); let n = 0; const errors = []; " +
    "while (Date.now() - t < 60000) { try { await tools.agents_list({}); n++; } " +
    "catch (e) { errors.push([Date.now() - t, String(e)]); break; } } return { n, errors };");
  await new Promise((done) => setTimeout(done, 3000));
  const app = async (path: string, body: unknown): Promise<Answer> => {
    const started = Date.now();
    const response = await fetch(`${BASE}/app/api/${path}`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, Origin: BASE,
                                 "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    return { status: response.status, ms: Date.now() - started, body: await response.json() };
  };
  row("5 owner app read during the script", await app("keys/list", {}));
  const revokedAt = Date.now();
  row("5 the key's revoke during the script", await app("keys/revoke", { key_id: keyId }));
  const ended = await looping;
  row(`5 the script after the revoke (revoked ${revokedAt} ms epoch)`, ended);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error: Error) => {
    console.error(`H2 stopped: ${error.message}`);
    process.exit(1);
  });
}
