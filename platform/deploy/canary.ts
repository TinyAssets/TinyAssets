/**
 * The platform's canary (d3 §J2, §A7): the platform checked from outside, through its public
 * routes, as a user's client reaches it. It runs on GitHub's runners
 * (`.github/workflows/platform-canary.yml`), started every ten minutes by the doors' clock
 * (`doors/src/canary-clock.ts`), and after each deploy by the kit (`deploy.ts`). A check run
 * inside Cloudflare could not prove the routes: a Worker's fetch of its own zone's routes never
 * reaches them.
 *
 *   node --experimental-strip-types platform/deploy/canary.ts [--side doors|old] [--unkeyed]
 *        [--clock] [--full] [--result <file>]
 *
 * It first reads which side serves: the doors, when the resource document on their host (the
 * identity's) names their own issuer, their origin; else the old stack, when the one on the
 * zone, where it serves until the cutover, names its authorization server
 * (`scripts/canary_handles.json`). The two are one document while the host is the zone.
 * - **The old stack** (before `routes`, and after a `handback`): the platform is dark. It
 *   checks that the old stack still answers the zone's `/mcp` (its anonymous `401` carries a
 *   body, the doors' none, so a `routes` stopped partway is seen) and that E2B holds the
 *   pinned template and the doors' lifecycle hook, disabled. The doors' own checks wait for `routes`, each
 *   named, never passed.
 * - **The doors:** the anonymous challenge and the resource document; the app page (`/app`,
 *   `/app?upgrade=1`) with its CSP; the first-party client document; the egress host's
 *   refusal; the sign-in's start (the doors' `/authorize` to WorkOS, WorkOS to AuthKit's
 *   page, the issuer's discovery and JWKS); E2B's template and the hook, enabled at the
 *   doors' hook; the off-account copy, a night of which must have passed inside two nights
 *   once it is configured (`offsite.ts` `watch`: an erased owner's hide there is owed within
 *   a week; it says so while unconfigured). Then, with the canary owner's API key (`TA_PROBE_KEY`): the
 *   handle set (one read-only `GET /api/tools/<name>` a name: each doors tool `405`, each
 *   old-only one and an unknown name `404`); a file written, read and removed in the owner's
 *   workspace; and the full run.
 * - **The full run:** one short turn of the canary owner's main agent (`converse`), on the
 *   model source the founder connected to that owner (a free tier's), which boots a sandbox,
 *   runs the engine and calls the model through the egress host. It runs once a day: its
 *   record (`FULL_RUN`, in the owner's workspace) says when it last ran and how it ended, and
 *   every run in between carries that ending, so a failed day stays red until a full run
 *   passes. `--full` runs it now.
 *
 * `--side` holds the side asked for (exit 8 if the other serves); `--unkeyed` (the `routes`
 * run's own, since no canary owner can exist before the doors serve) leaves the keyed checks
 * waiting; `--clock` (the workflow's own schedule, the watchdog) checks that the clock
 * dispatched a run in the last 30 minutes. Every check runs and prints one line; the exit is
 * the first failure's code, else 0. A run with a check waiting exits 0 and says what waits:
 * a waiting check is not a pass. `--result` writes the outcomes for the alarm (`alarm.ts`).
 * No credential is ever printed.
 *
 * Exit codes: 0 every check passed, or the platform dark; 2 unreachable; 4 handle drift; 5 the
 * sign-in's start; 6 an authentication boundary or a gate refused; 7 the keyed checks
 * unchecked (no `TA_PROBE_KEY`); 8 the side asked for is not serving; 9 neither side's
 * resource document; 10 a split, the doors and the old stack each serving a part; 11 E2B; 12
 * the owner's files; 13 the full run; 14 the clock stopped; 15 the off-account copy; 1 the
 * canary itself stopped. GitHub is read with the job's own token (`actions.ts` `github`).
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { REPO } from "../ci/paths.ts";
import { DISPATCH } from "../contract/src/canary.ts";
import { EGRESS_HOST } from "../contract/src/egress.ts";
import { E2bAdmin } from "../sandbox/e2b/admin.ts";
import { pinned } from "../sandbox/template/pair.ts";
import { github, type Reach, TOKEN } from "./actions.ts";
import { authorizeCheck, firstPartyLink, heldHook, upstreamFrom } from "./deploy.ts";
import identity from "../contract/identity.json" with { type: "json" };
import {
  APP_RESOURCE, MCP_RESOURCE, ORIGIN, UPSTREAM_CALLBACK, WEB_CLIENT, ZONE,
} from "../contract/src/identity.ts";
import { AUTHORIZE, E2B_HOOK_URL } from "./facts.ts";
import { watch } from "./offsite.ts";

type Json = Record<string, unknown>;

/** The canary's world: replaced whole by its tests. */
export interface World extends Reach {
  readonly now: () => Date;
}

/** Which side serves `/mcp`. */
export type Side = "doors" | "old";

export interface Options {
  readonly side: Side | null;
  readonly unkeyed: boolean;
  readonly clock: boolean;
  readonly full: boolean;
}

/** One check's ending: its line, and its exit code when it failed. */
export interface Outcome {
  readonly check: string;
  readonly state: "passed" | "failed" | "waiting";
  readonly line: string;
  readonly code: number;
  /**
   * A failure that is enough alone for the alarm: it already spans 30 minutes (the clock) or
   * two nights (the off-account copy).
   */
  readonly alone: boolean;
}

export interface Result {
  readonly side: Side | null;
  readonly outcomes: readonly Outcome[];
}

/** A check's failure, with its exit code, and whether it is enough alone for the alarm. */
export class Failure extends Error {
  readonly code: number;
  readonly alone: boolean;
  constructor(code: number, message: string, alone = false) {
    super(message);
    this.code = code;
    this.alone = alone;
  }
}

/**
 * The canary owner's API key, and what the checks read beside it: the job's own token reads
 * the clock's runs and the off-account copy's.
 */
export const INPUTS = Object.freeze({
  key: "TA_PROBE_KEY", e2b: "E2B_API_KEY", issuer: "OWNER_ISSUER", client: "WORKOS_CLIENT_ID",
  github: TOKEN });

/** The resource document `/mcp` names (RFC 9728's path form), on the doors' host. */
const RESOURCE_DOCUMENT = `${ORIGIN}/.well-known/oauth-protected-resource${identity.mcp}`;
/** The old stack's MCP door and its resource document: on the zone, until the cutover. */
const OLD_MCP = `https://${ZONE}${identity.mcp}`;
const OLD_DOCUMENT = `https://${ZONE}/.well-known/oauth-protected-resource${identity.mcp}`;
/** A name no side serves: the `404` a profile answers for a tool outside it. */
const UNKNOWN_TOOL = "no_such_tool";
/** Each request's limit: GitHub's runners reach some answers slowly. */
const REQUEST_MS = 30_000;
/** The beat the owner-files check writes, reads and removes. */
const BEAT = "/workspace/canary-beat.txt";
/** The full run's record: when it last ran, and how it ended. */
export const FULL_RUN = "/workspace/canary-full-run.json";
/** How often the full run runs. */
const FULL_RUN_EVERY_MS = 24 * 3600_000;
/** The full run's turn, and how many times its run is waited on (`converse`'s own wait). */
const FULL_RUN_MESSAGE = "This is the TinyAssets canary. Answer with the one word: ok";
const FULL_RUN_WAITS = 10;
const WAIT_MS = 60_000;
/** How long the clock may go without dispatching a run: three of its ticks. */
export const CLOCK_MS = 30 * 60_000;

const INITIALIZE = JSON.stringify({
  jsonrpc: "2.0", id: 1, method: "initialize",
  params: { protocolVersion: "2025-06-18", capabilities: {},
            clientInfo: { name: "tinyassets-canary", version: "1" } } });

/**
 * Each side's handle set (`scripts/canary_handles.json`), and the old stack's authorization
 * server: the doors' is their own origin (`doors/src/config.ts` `ISSUER`).
 */
interface SideRecords {
  readonly old: { readonly authorization_server: string; readonly handles: readonly string[] };
  readonly new: { readonly handles: readonly string[] };
}
function records(): SideRecords {
  return JSON.parse(readFileSync(join(REPO, "scripts", "canary_handles.json"), "utf8"));
}

/** One request, bounded: unreachable is exit 2, never an answer. */
async function send(world: World, url: string, init: RequestInit = {}): Promise<Response> {
  try {
    return await world.fetch(url, { redirect: "manual", ...init,
                                    signal: AbortSignal.timeout(REQUEST_MS) });
  } catch (error) {
    throw new Failure(2, `unreachable ${init.method ?? "GET"} ${url}: ${(error as Error).message}`);
  }
}

/** The world's reach, each request bounded by `send`. */
function bounded(world: World): Reach {
  return { fetch: (url, init) => send(world, url, init), env: world.env };
}

/** `act`, any error of it that is not a check's own failure made this check's `code`. */
async function failingAs<T>(code: number, act: () => Promise<T>): Promise<T> {
  try {
    return await act();
  } catch (error) {
    throw error instanceof Failure ? error : new Failure(code, (error as Error).message);
  }
}

async function json(response: Response, what: string, code: number): Promise<Json> {
  try {
    const body = await response.json() as unknown;
    if (typeof body === "object" && body !== null && !Array.isArray(body)) {
      return body as Json;
    }
  } catch {
    // Not JSON: named below.
  }
  throw new Failure(code, `${what} answered no JSON object (HTTP ${response.status})`);
}

/** The authorization servers the resource document `document` names, as JSON. */
async function serversAt(world: World, document: string): Promise<string> {
  const answer = await send(world, document);
  if (answer.status !== 200) {
    throw new Failure(9, `${document} answered ${answer.status}`);
  }
  return JSON.stringify((await json(answer, document, 9))["authorization_servers"]);
}

/**
 * The side that serves: the doors, when their host's resource document names their issuer,
 * their origin; else the old stack, when the zone's names its authorization server.
 */
async function sideOf(world: World): Promise<Side> {
  let doors: string | null;
  try {
    doors = await serversAt(world, RESOURCE_DOCUMENT);
  } catch (error) {
    // A host under the zone with no route has no DNS record, so it answers nothing: the doors
    // serve nowhere, and the zone's document says the rest. On the zone this read and the
    // next are one.
    if (!(error instanceof Failure && error.code === 2)) {
      throw error;
    }
    doors = null;
  }
  if (doors === JSON.stringify([ORIGIN])) {
    return "doors";
  }
  const servers = await serversAt(world, OLD_DOCUMENT);
  if (servers === JSON.stringify([records().old.authorization_server])) {
    return "old";
  }
  throw new Failure(9, `the resource document names ${servers}, neither side's authorization ` +
                       "server");
}

/** An anonymous `initialize` of the MCP door `url`. */
function anonymous(world: World, url: string): Promise<Response> {
  return send(world, url, { method: "POST", body: INITIALIZE, headers: {
    "Content-Type": "application/json", Accept: "application/json, text/event-stream" } });
}

/** The old stack's `401` carries its body; the doors' none. */
async function oldAnswers(world: World): Promise<string> {
  const answer = await anonymous(world, OLD_MCP);
  const body = await answer.text();
  if (answer.status !== 401 || body.trim() === "") {
    throw new Failure(10, `split: the resource document is the old stack's, but /mcp answered ${
      answer.status}${body.trim() === "" ? " with no body, as the doors do" : ""}. A routes or ` +
      "handback run stopped partway: run it again to the end");
  }
  return "the old stack answers /mcp; the resource document is its own";
}

async function challenge(world: World): Promise<string> {
  const answer = await anonymous(world, MCP_RESOURCE);
  const header = answer.headers.get("WWW-Authenticate") ?? "";
  if (answer.status !== 401 || !header.startsWith("Bearer ")
      || !header.includes(`resource_metadata="${RESOURCE_DOCUMENT}"`)
      || (await answer.text()).trim() !== "") {
    throw new Failure(10, `/mcp did not answer as the doors: HTTP ${answer.status}; a routes run ` +
                          "stopped partway is run again to the end");
  }
  const document = await json(await send(world, RESOURCE_DOCUMENT), RESOURCE_DOCUMENT, 6);
  if (document["resource"] !== MCP_RESOURCE) {
    throw new Failure(6, `the resource document's resource is not ${MCP_RESOURCE}`);
  }
  return "an anonymous initialize is challenged with the doors' resource document";
}

async function app(world: World): Promise<string> {
  for (const url of [APP_RESOURCE, `${APP_RESOURCE}?upgrade=1`]) {
    const answer = await send(world, url);
    if (answer.status !== 200 || !/^text\/html/.test(answer.headers.get("Content-Type") ?? "")
        || answer.headers.get("Content-Security-Policy") === null) {
      throw new Failure(2, `${url} answered ${answer.status}, not the app page with its CSP`);
    }
  }
  return "the app page is served, with its CSP, with and without a query";
}

async function clientDocument(world: World): Promise<string> {
  const answer = await send(world, WEB_CLIENT);
  if (answer.status !== 200
      || (await json(answer, WEB_CLIENT, 2))["client_id"] !== WEB_CLIENT) {
    throw new Failure(2, `${WEB_CLIENT} is not the first-party client's document`);
  }
  return "the first-party client's document is served";
}

async function egressHost(world: World): Promise<string> {
  const url = `https://${EGRESS_HOST}/`;
  const answer = await send(world, url);
  const error = answer.status === 404 ? (await json(answer, url, 2))["error"] : null;
  if (typeof (error as Json | null)?.["code"] !== "string") {
    throw new Failure(2, `${url} answered ${answer.status}, not the egress door's refusal`);
  }
  return `${EGRESS_HOST} answers with the egress door's refusal`;
}

async function signIn(world: World): Promise<string> {
  const fetcher = bounded(world).fetch;
  const upstream = await failingAs(5, () =>
    upstreamFrom(world.env[INPUTS.issuer], world.env[INPUTS.client], fetcher));
  const { record } = upstream;
  const keys = await send(world, record.jwks_uri);
  if (keys.status !== 200 || !Array.isArray((await json(keys, record.jwks_uri, 5))["keys"])) {
    throw new Failure(5, `the issuer's JWKS ${record.jwks_uri} answered ${keys.status}, no keys`);
  }
  const answer = await send(world, firstPartyLink());
  const to = answer.headers.get("Location");
  const sent = to === null ? null : new URL(to, AUTHORIZE);
  if (answer.status !== 302 || sent === null
      || `${sent.origin}${sent.pathname}` !== record.authorization_endpoint
      || sent.searchParams.get("redirect_uri") !== UPSTREAM_CALLBACK) {
    throw new Failure(5, `${AUTHORIZE} answered ${answer.status}, not a redirect to WorkOS's ` +
                         "authorize with the doors' callback");
  }
  return `the doors' /authorize sends the browser to WorkOS; ${
    await failingAs(5, () => authorizeCheck(upstream, false, fetcher))}`;
}

/**
 * E2B holds the pinned template, and the doors' lifecycle hook, enabled only once routed, and
 * then sent to the doors' hook (`heldHook` finds it by its name).
 */
function sandbox(world: World, side: Side): Promise<string> {
  return failingAs(11, async () => {
    const e2b = new E2bAdmin(world.env[INPUTS.e2b] ?? "", bounded(world).fetch);
    const template = pinned();
    if (!(await e2b.ready(template))) {
      throw new Failure(11, `E2B holds no ready build of the pinned template ${template}`);
    }
    const hook = await heldHook(e2b);
    const enabled = side === "doors";
    if (hook === null || (hook["enabled"] === true) !== enabled
        || (enabled && hook["url"] !== E2B_HOOK_URL)) {
      throw new Failure(11, `E2B holds no ${enabled ? "enabled" : "disabled"} lifecycle hook ` +
                            `for ${E2B_HOOK_URL}`);
    }
    return `E2B holds the pinned template ${template}, and the lifecycle hook, ${
      enabled ? "enabled" : "disabled"}`;
  });
}

/** A night of the off-account copy passed inside two nights, or it isn't configured yet. */
async function offsiteCopy(world: World): Promise<Ending> {
  const watched = await failingAs(15, () => watch(bounded(world), world.now()));
  if (watched.state === "overdue") {
    throw new Failure(15, watched.line, true);
  }
  return watched.state === "waiting" ? { waits: watched.line } : watched.line;
}

/** The canary owner's key, or exit 7: a keyed check unchecked is not a pass. */
function keyOf(world: World): string {
  const key = world.env[INPUTS.key];
  if (key === undefined || key === "") {
    throw new Failure(7, `${INPUTS.key} (the canary owner's API key) is not set`);
  }
  return key;
}

/** One `/api` call with the canary owner's key: its status and its JSON. */
async function tool(world: World, name: string, args: Json | null):
    Promise<{ status: number; body: Json | null }> {
  const answer = await send(world, `${ORIGIN}/api/tools/${name}`, {
    method: args === null ? "GET" : "POST",
    headers: { Authorization: `Bearer ${keyOf(world)}`, Accept: "application/json",
               ...(args === null ? {} : { "Content-Type": "application/json" }) },
    ...(args === null ? {} : { body: JSON.stringify(args) }) });
  if (answer.status === 401) {
    throw new Failure(6, `the doors refused ${INPUTS.key} (HTTP 401): it is not a live API key`);
  }
  if (answer.status === 503) {
    throw new Failure(6, "the doors' gate refused the canary owner (HTTP 503): the control " +
                         "record must admit it");
  }
  const text = await answer.text();
  let body: Json | null = null;
  try {
    body = JSON.parse(text) as Json;
  } catch {
    // A GET's answer, or a refusal's text.
  }
  return { status: answer.status, body };
}

async function handles(world: World): Promise<string> {
  const sides = records();
  const served = new Set(sides.new.handles);
  const absent = new Set([...sides.old.handles.filter((name) => !served.has(name)),
                          UNKNOWN_TOOL]);
  const missing: string[] = [];
  const extra: string[] = [];
  for (const name of [...served, ...absent].sort()) {
    const { status } = await tool(world, name, null);
    if (served.has(name) && status !== 405) {
      missing.push(`${name} (HTTP ${status})`);
    } else if (absent.has(name) && status !== 404) {
      extra.push(`${name} (HTTP ${status})`);
    }
  }
  if (missing.length > 0 || extra.length > 0) {
    throw new Failure(4, `handle drift: missing=${JSON.stringify(missing)} extra=${
      JSON.stringify(extra)}`);
  }
  return `${served.size} handles served, the old-only ones and an unknown name not`;
}

/** A tool's answer, held to 200. */
async function called(world: World, name: string, args: Json, code: number): Promise<Json> {
  const { status, body } = await tool(world, name, args);
  if (status !== 200 || body === null) {
    throw new Failure(code, `${name} answered ${status}${
      typeof body?.["error"] === "string" ? `: ${body["error"]}` : ""}`);
  }
  return body;
}

/** `path` written whole: over whatever stands there (a conflict answers its etag). */
async function write(world: World, path: string, content: string, code: number):
    Promise<string> {
  let expected = "";
  for (const attempt of [1, 2]) {
    const answer = await called(world, "file_write", { path, expected_etag: expected, content },
                                code);
    const entry = (answer["entry"] ?? null) as Json | null;
    if (typeof entry?.["etag"] === "string") {
      return entry["etag"];
    }
    const current = (answer["conflict"] ?? null) as Json | null;
    if (attempt === 1 && typeof current?.["etag"] === "string") {
      expected = current["etag"];
      continue;
    }
    break;
  }
  throw new Failure(code, `file_write of ${path} answered neither an entry nor a conflict`);
}

async function ownerFiles(world: World): Promise<string> {
  const beat = `beat ${world.now().toISOString()}`;
  const etag = await write(world, BEAT, beat, 12);
  const read = await called(world, "file_read", { path: BEAT }, 12);
  if (read["text"] !== beat || (read["entry"] as Json | undefined)?.["etag"] !== etag) {
    throw new Failure(12, `file_read of ${BEAT} answered other than the beat just written`);
  }
  await called(world, "file_remove", { path: BEAT, expected_etag: etag, recursive: false }, 12);
  return `${BEAT} written, read back and removed`;
}

/** The full run's record: when it last ran, and how it ended. */
interface FullRun {
  readonly at: string;
  readonly passed: boolean;
  readonly line: string;
}

async function fullRun(world: World, forced: boolean): Promise<string> {
  const now = world.now();
  const { status, body } = await tool(world, "file_read", { path: FULL_RUN });
  if (status !== 200 && status !== 404) {
    throw new Failure(13, `file_read of ${FULL_RUN} answered ${status}`);
  }
  const held = status === 200 ? JSON.parse(String(body?.["text"])) as FullRun : null;
  if (held !== null && !forced && now.getTime() - Date.parse(held.at) < FULL_RUN_EVERY_MS) {
    if (!held.passed) {
      throw new Failure(13, `the full run of ${held.at} failed, and runs again a day after it ` +
                            `(or with --full): ${held.line}`);
    }
    return `the full run of ${held.at} passed: ${held.line}`;
  }
  const turn = { message: FULL_RUN_MESSAGE, client_send_id: `canary-${now.getTime()}`,
                 wait_ms: WAIT_MS };
  let run: Json = {};
  for (let wait = 0; wait < FULL_RUN_WAITS; wait += 1) {
    run = await called(world, "converse", turn, 13);
    if (!["waiting", "queued", "running"].includes(String(run["state"]))) {
      break;
    }
  }
  const passed = run["state"] === "succeeded" && typeof run["result"] === "string"
    && run["result"].trim() !== "";
  const line = passed ? `run ${String(run["run_id"])} answered on ${String(run["engine"])}`
    : `run ${String(run["run_id"])} ended ${String(run["state"])}${
      run["code"] === null || run["code"] === undefined ? "" : ` (${String(run["code"])})`}${
      typeof run["error"] === "string" ? `: ${run["error"]}` : ""}`;
  const record: FullRun = { at: now.toISOString(), passed, line };
  await called(world, "file_write", {
    path: FULL_RUN, content: JSON.stringify(record),
    expected_etag: held === null ? ""
      : String(((body?.["entry"] ?? {}) as Json)["etag"]) }, 13);
  if (!passed) {
    throw new Failure(13, `the full run failed: ${line}`);
  }
  return `the full run passed: ${line}`;
}

/** The clock dispatched a run in the last 30 minutes. */
async function clock(world: World): Promise<string> {
  const listed = await failingAs(14, () => github(bounded(world), "GET", `/actions/workflows/${
    DISPATCH.workflow}/runs?event=workflow_dispatch&branch=${DISPATCH.ref}&per_page=1`));
  const last = ((listed as Json | null)?.["workflow_runs"] as Json[] | undefined)?.[0];
  const at = typeof last?.["created_at"] === "string" ? Date.parse(last["created_at"]) : NaN;
  if (!(world.now().getTime() - at <= CLOCK_MS)) {
    throw new Failure(14, `the canary's clock stopped: no run dispatched since ${
      Number.isNaN(at) ? "ever" : new Date(at).toISOString()} (the doors' Cron Trigger and ${
      DISPATCH.token})`, true);
  }
  return `the clock dispatched a run at ${new Date(at).toISOString()}`;
}

/** How a check ends that doesn't fail: its line, or what it waits for. */
type Ending = string | { readonly waits: string };

/** One check: its name, and what it runs. */
type Check = readonly [string, () => Promise<Ending>];

async function outcome([check, act]: Check): Promise<Outcome> {
  try {
    const ended = await act();
    return typeof ended === "string"
      ? { check, state: "passed", line: ended, code: 0, alone: false }
      : waiting(check, ended.waits);
  } catch (error) {
    if (!(error instanceof Failure)) {
      throw error;
    }
    return { check, state: "failed", line: error.message, code: error.code, alone: error.alone };
  }
}

const waiting = (check: string, line: string): Outcome =>
  ({ check, state: "waiting", line, code: 0, alone: false });

/** The canary, each check once, in order. */
export async function canary(world: World, options: Options): Promise<Result> {
  let side: Side;
  try {
    side = await sideOf(world);
    if (options.side !== null && side !== options.side) {
      throw new Failure(8, `asked for the ${options.side} side, but the ${side} side serves ${
        MCP_RESOURCE}`);
    }
  } catch (error) {
    if (!(error instanceof Failure)) {
      throw error;
    }
    return { side: null, outcomes: [{ check: "the side", state: "failed", line: error.message,
                                      code: error.code, alone: false }] };
  }
  const run = (checks: readonly Check[]) => checks.reduce<Promise<Outcome[]>>(
    async (done, each) => [...await done, await outcome(each)], Promise.resolve([]));
  if (side === "old") {
    return { side, outcomes: [
      ...await run([["the old stack", () => oldAnswers(world)],
                    ["E2B", () => sandbox(world, side)]]),
      waiting("the doors", "the doors' checks, the off-account copy, the keyed ones and the " +
                           "clock wait for routes: the old stack serves /mcp") ] };
  }
  const keyed: Check[] = [["the handle set", () => handles(world)],
                          ["the owner's files", () => ownerFiles(world)],
                          ["the full run", () => fullRun(world, options.full)]];
  return { side, outcomes: [
    ...await run([["the challenge", () => challenge(world)], ["the app", () => app(world)],
                  ["the client document", () => clientDocument(world)],
                  ["the egress host", () => egressHost(world)],
                  ["the sign-in", () => signIn(world)], ["E2B", () => sandbox(world, side)],
                  ["the off-account copy", () => offsiteCopy(world)]]),
    ...options.unkeyed
      ? [waiting("the keyed checks", "the handle set, the owner's files and the full run wait " +
                                     `for the canary owner's key (${INPUTS.key})`)]
      : await run(keyed),
    ...options.clock ? await run([["the clock", () => clock(world)]]) : [] ] };
}

/** The run's exit code: its first failure's, else 0. */
export function exitOf(result: Result): number {
  return result.outcomes.find((each) => each.state === "failed")?.code ?? 0;
}

/** The lines a run prints: one a check, and what waits. */
export function report(result: Result): string[] {
  return [`the platform canary: ${result.side === null ? "no side"
    : result.side === "doors" ? "the doors serve" : "dark (the old stack serves)"}`,
          ...result.outcomes.map((each) => `${each.state === "passed" ? "ok"
            : each.state === "failed" ? `FAILED (exit ${each.code})` : "waits"} ${each.check}: ${
            each.line}`)];
}

function option(argv: readonly string[], name: string): string | null {
  const at = argv.indexOf(name);
  return at < 0 ? null : argv[at + 1] ?? null;
}

async function main(argv: readonly string[]): Promise<number> {
  const side = option(argv, "--side");
  if (side !== null && side !== "doors" && side !== "old") {
    throw new Error("--side is doors or old");
  }
  // A canary that stops is a red run like any other, so the alarm still reads its result.
  const result = await canary({ fetch: (url, init) => fetch(url, init), env: process.env,
                                now: () => new Date() },
                              { side, unkeyed: argv.includes("--unkeyed"),
                                clock: argv.includes("--clock"), full: argv.includes("--full") })
    .catch((error: Error): Result => ({ side: null, outcomes: [{
      check: "the canary", state: "failed", line: `it stopped: ${error.message}`, code: 1,
      alone: false }] }));
  for (const line of report(result)) {
    console.log(line);
  }
  const file = option(argv, "--result");
  if (file !== null) {
    writeFileSync(file, JSON.stringify(result));
  }
  return exitOf(result);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => process.exit(code), (error: Error) => {
    console.error(`the platform canary stopped: ${error.message}`);
    process.exit(1);
  });
}
