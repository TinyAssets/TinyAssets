/**
 * The platform's deploy kit (tasks 5, "0c (live, slice L)"; tasks 2, "DNS, routes and
 * deploy"): the six platform Workers deployed to the production Cloudflare account, the
 * doors' routes handed over at the cutover, the handles canary after each change (AGENTS.md
 * Fact 11), and H2's probes (`h2-probes.ts`).
 *
 * It runs from the manual workflow `.github/workflows/platform-deploy.yml`, from `main`, one
 * mode a run (`MODES`, each a row of steps):
 *
 *   node --experimental-strip-types platform/deploy/deploy.ts --mode <mode> [--public | --yes]
 *        [--rotate <secrets>] [--migrate <Workers>]
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-new <file>
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-add <file>
 *
 * - `plan`: `dark`'s checks and plan, never a change.
 * - `dark`: storage, secrets and every Worker with no public route, the control record when
 *   absent, the WorkOS redirect URI and E2B's lifecycle webhook (disabled), then the handles
 *   canary.
 * - `probe`: after `dark`, the sign-in's two paths alone handed to the doors (`PROBE_ROUTES`),
 *   nothing deployed and no gate changed, then the canary and the one sign-in link to open,
 *   with its pass and fail signs (`PROBE_SIGNS`).
 * - `routes`: `dark`, then the doors' routes handed over one at a time, the egress custom
 *   domain, and the webhook enabled.
 * - `h2`: H2's probes alone, once a probe owner exists.
 * - `pair`: the pinned template with the runner this checkout ships, checked together on
 *   throwaway sandboxes unless E2B already holds their pair's tag (`build.py --check-pair`).
 *
 * `--public` is the workflow's `plan` job's: the Cloudflare token and the repository's
 * variables alone, every Cloudflare read and the discovery check, the checks that need a
 * production secret left to the apply job and said so in the plan. `--yes` is the `apply`
 * job's, in the main-only `production` environment, with that environment's secrets, never a
 * host's (Direction 8). With neither, a mode checks and plans with the secrets it is given,
 * and changes nothing. The Cloudflare account is the zone's (`Cloudflare.zone`).
 *
 * Every lookup is checked: a failed listing or read stops the kit loudly, never reads as
 * "absent". So:
 * - a namespace, bucket or queue is created only when its listing answered and lacks it;
 * - a secret is set only on a Worker whose secret listing answered and lacks it, or that the
 *   API says does not exist yet; the kit never makes a secret's value (`keyring-new` makes a
 *   first keyring, `scripts/platform_setup.py` stores one). A held one is never replaced
 *   unless `--rotate` names it, a keyring only by adding a key; Cloudflare never shows a held
 *   secret, so the kit cannot check that a rotated keyring keeps the deployed key;
 * - the control record is written only when the API answered that it is absent;
 * - the WorkOS redirect URI is registered only when WorkOS's listing lacks it (GET, then
 *   POST; never a delete), after the other live checks and before any Cloudflare change, and
 *   only then is the early sign-in check run, which needs it listed;
 * - E2B's lifecycle webhook is listed and checked before any change, registered disabled only
 *   when E2B's listing lacks it, its secret changed only with the doors' copy, and enabled
 *   only by `routes` after the handover; the run that registers it sets the doors' copy too;
 * - nothing is deployed unless every migration lock (`owner/migrations.json`, and the sandbox
 *   Worker's `sandbox/migrations/<class>.json`) marks the first deploy and pins every
 *   migration the code ships, in order (`lockRefusal`);
 * - a Worker's first deploy is `wrangler deploy`; later ones upload a version, then deploy it,
 *   which never touches a route; `--migrate` names a Worker with a pending Durable Object
 *   migration to deploy whole;
 * - a route handover moves a pattern another Worker holds in place (`PUT`), adds a missing
 *   one (`POST`), and only then deletes another Worker's narrower pattern, so no path is ever
 *   served by nothing (`handover`).
 *
 * Before anything is changed it checks, live: the owner issuer's discovery names
 * `OWNER_ISSUER` exactly and an https JWKS (an absent field is unknown, and unknown stops
 * the kit); WorkOS's authorize, sent the URL the doors build with a throwaway challenge,
 * redirects to AuthKit's sign-in (`authorizeCheck`); the sandbox's `E2B_TEMPLATE` has a ready
 * build, and E2B holds the tag of the pair this deploy makes (`pairTag`), which `pair` makes.
 * The access token's exact `iss` and `auth_time` only a real sign-in shows: `probe` settles
 * them.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { WRANGLER } from "../ci/workers.ts";
// The envelope and the E2B client import nothing, so Node's type stripping runs them here by
// their own names.
import { KEY_BYTES, parseRing, type Ring, ringText, ringWith } from "../contract/src/envelope.ts";
import { E2b } from "../sandbox/src/e2b.ts";
import {
  cfApi, Cloudflare, describeStep, handover, narrower, type Route, type RouteStep,
} from "./cloudflare.ts";
import {
  AUTHORIZE, BUCKETS, CALLBACK, CONFIGS, CONTROL_KEY, DOORS, E2B_HOOK_URL, EGRESS_DOMAIN,
  FIRST_PARTY, KEYRINGS, NAMES, NAMESPACES, OPTIONAL, PROBE_ROUTES, QUEUES, ROUTES,
  SECRET_INPUTS, SECRETS, SHARED, WORKERS, ZONE,
} from "./facts.ts";

const PLATFORM = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = resolve(PLATFORM, "..");
const require = createRequire(join(PLATFORM, "package.json"));

type Json = Record<string, unknown>;
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

/** What the plan job holds: the Cloudflare token (not a production secret) and the vars. */
export const PUBLIC_INPUTS: readonly string[] = Object.freeze([
  "CLOUDFLARE_API_TOKEN", "WORKOS_CLIENT_ID", "OWNER_ISSUER"]);
const WORKOS_API = "https://api.workos.com";
/**
 * E2B's sandbox lifecycle webhook, the doors' one E2B hook (`doors/src/e2b-hooks.ts`), its
 * API read in e2b-dev/infra's `spec/openapi.yml` (2026-11-04).
 */
export const E2B_HOOK = Object.freeze({
  name: "tinyassets-lifecycle", url: E2B_HOOK_URL,
  events: Object.freeze(["sandbox.lifecycle.paused", "sandbox.lifecycle.killed"]),
});
/** The control record a first deploy writes when there is none: every outside door closed. */
export const DARK_CONTROL = Object.freeze({
  outside: "off", ordinary: "canary", canary: [], inbound: false,
  tiers: { free: { runs: 5, storage_bytes: 25_000_000_000 },
           paid: { runs: 10, storage_bytes: 250_000_000_000 } },
});
/** What the plan job says of a check that needs a production secret. */
const LATER = "checked by the apply job before any change";

// --- The run ----------------------------------------------------------------------------

/** The outside world the kit acts on: replaced whole by the tests. */
export interface Io {
  readonly fetch: Fetch;
  readonly spawn: typeof spawnSync;
  readonly exec: typeof execFileSync;
  readonly log: (line: string) => void;
}

export const IO: Io = Object.freeze({
  fetch: (url: string, init?: RequestInit) => fetch(url, init),
  spawn: ((...args: Parameters<typeof spawnSync>) => spawnSync(...args)) as typeof spawnSync,
  exec: ((...args: Parameters<typeof execFileSync>) => execFileSync(...args)) as
    typeof execFileSync,
  log: (line: string) => console.log(line),
});

export type Mode = "plan" | "dark" | "probe" | "routes" | "h2" | "pair";

/** One run, as `parse` reads it: its mode, how far it reaches, and its inputs. */
export interface Run {
  readonly mode: Mode;
  /** `--public`: the plan job's, which holds no production secret. */
  readonly reach: "public" | "secrets";
  /** `--yes`: the apply job's, which changes what its plan says. */
  readonly apply: boolean;
  readonly rotate: ReadonlySet<string>;
  readonly migrate: ReadonlySet<string>;
  /** The environment as the run began: its inputs, and what the tools it runs are given. */
  readonly env: Readonly<Record<string, string | undefined>>;
  readonly io: Io;
}

/** What a run's steps have learned and made so far, in order. */
export interface Work {
  readonly plan: string[];
  cloudflare?: Cloudflare;
  zone?: string;
  account?: string;
  wrangler?: Wrangler;
  e2b?: E2b;
  upstream?: UpstreamValues;
  /** E2B's listing of the lifecycle hook: null when it holds none. */
  hook?: Json | null;
  redirect?: "present" | "missing" | "registered";
  routes?: RouteStep[];
  ids?: Record<string, string>;
  /** The namespaces a plan would make: not there yet. */
  unmade?: Set<string>;
  out?: string;
  paths?: Record<string, string>;
  held?: Record<string, string[] | null>;
  setting?: { worker: string; name: string }[];
  /** The run ends here: a plan says what it would change, and stops. */
  done?: boolean;
}

/** One step of a mode: what it reads from the run and the work so far, and adds to it. */
export interface Step {
  readonly name: string;
  readonly run: (run: Run, work: Work) => Promise<void> | void;
}

function step(name: string, run: Step["run"]): Step {
  return Object.freeze({ name, run });
}

function input(run: Run, name: string): string | undefined {
  const value = run.env[name];
  return value === undefined || value === "" ? undefined : value;
}

/** What an earlier step of the row made: a step run out of order fails loudly. */
function made<T>(value: T | undefined, what: string): T {
  if (value === undefined) {
    throw new Error(`the kit's row is out of order: ${what} is not made yet`);
  }
  return value;
}

/** The sandbox's pinned E2B template, `name:tag` (its config's `E2B_TEMPLATE`). */
function template(): string {
  return String((CONFIGS.get("sandbox")!["vars"] as Json)["E2B_TEMPLATE"]);
}

/** The plan, printed and written to the job summary. */
export function report(plan: readonly string[], applied: boolean, run: Run): void {
  const summary = `${applied ? "Changed" : "Would change"}:\n${plan.map((line) => `- ${line}`)
    .join("\n")}`;
  run.io.log(summary);
  const file = run.env["GITHUB_STEP_SUMMARY"];
  if (file !== undefined) {
    writeFileSync(file, `${summary}\n`, { flag: "a" });
  }
}

/**
 * Masks `value` in a GitHub Actions job's log (`::add-mask::`), as the probe workflows mask
 * the account id: not a credential, but once a secret, and a wrangler error the kit quotes
 * names `/accounts/<id>/...` in a public log. Outside Actions it prints nothing.
 */
export function maskInLog(value: string, env: Readonly<Record<string, string | undefined>>,
                          log: (line: string) => void): void {
  if (env["GITHUB_ACTIONS"] !== undefined) {
    log(`::add-mask::${value}`);
  }
}

// --- The migration locks ----------------------------------------------------------------

/** The Workers whose Durable Objects keep tables, each with its migrations guard. */
export const MIGRATED = ["owner", "sandbox"] as const;

/**
 * Why nothing may deploy under `lock` (the migration lock at `path`, from `platform/`) with
 * the code's migrations' `digests`, or null: the first deploy is marked, and the lock pins
 * exactly the code's migrations, element for element (tasks "Append-only migrations from
 * the first deploy").
 */
export function lockRefusal(lock: unknown, digests: readonly string[],
                            path: string): string | null {
  const pin = "pin them with PIN_MIGRATIONS=<commit> npx vitest run --project guards " +
    `guards/migrations.test.ts in platform/${path.split("/")[0]}, and commit the lock with ` +
    "the code";
  const held = lock as { first_deploy?: unknown; deployed?: unknown } | null;
  if (typeof held !== "object" || held === null || !Array.isArray(held.deployed)
      || !("first_deploy" in held)) {
    return `${path} is out of shape`;
  }
  if (held.first_deploy === null) {
    return `the migration lock ${path} marks no first deploy: ${pin}`;
  }
  if (held.deployed.length !== digests.length
      || held.deployed.some((digest, index) => digest !== digests[index])) {
    return `the migration lock ${path} pins ${held.deployed.length} migrations and the code ` +
      `ships ${digests.length}, or one differs: ${pin}`;
  }
  return null;
}

/** A run of `worker`'s migrations guard, its digests written to `out`: its exit and output. */
export type GuardRun = (worker: string, out: string) => { status: number | null; stdout: string };

/** The real run: `worker`'s guards project, as it evaluates its schema. */
export function guardRun(run: Run): GuardRun {
  return (worker, out) => {
    const vitest = join(dirname(require.resolve("vitest/package.json")), "vitest.mjs");
    return run.io.spawn(process.execPath, [vitest, "run", "--project", "guards",
                                           "guards/migrations.test.ts"], {
      cwd: join(PLATFORM, worker), encoding: "utf8",
      env: { ...run.env, MIGRATION_DIGESTS: out, PIN_MIGRATIONS: "" },
    });
  };
}

/**
 * The code's migrations' digests, by lock path, as `worker`'s guards project evaluates its
 * schema (`run`).
 */
export function codeDigests(worker: string, run: GuardRun): Record<string, string[]> {
  const dir = mkdtempSync(join(tmpdir(), "ta-migrations-"));
  const out = join(dir, "digests.json");
  try {
    const result = run(worker, out);
    if (result.status !== 0) {
      throw new Error(`${worker}'s migration lock guard failed (exit ${result.status}): ` +
                      `${result.stdout.slice(-2000)}`);
    }
    return locksOf(worker, JSON.parse(readFileSync(out, "utf8")));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/**
 * `worker`'s locks as its guard reported them (`codeDigests`): a Worker in `MIGRATED` whose
 * guard reports none is refused, so the kit's check never passes with nothing checked.
 */
function locksOf(worker: string, reported: unknown): Record<string, string[]> {
  if (typeof reported !== "object" || reported === null || Array.isArray(reported)
      || Object.keys(reported).length === 0) {
    throw new Error(`${worker}'s migrations guard reported no migration lock: each Worker in ` +
                    "MIGRATED holds its objects to at least one");
  }
  return reported as Record<string, string[]>;
}

// --- WorkOS and the owner issuer --------------------------------------------------------

/** The doors' upstream: the issuer, and its `UPSTREAM` record. */
export interface UpstreamValues {
  readonly issuer: string;
  readonly record: Json;
}

/**
 * The doors' `UPSTREAM` record, which the doors' own parser accepts: User Management's
 * sign-in for the environment's client (ADR-032), its authorize and authenticate endpoints
 * fixed WorkOS URLs, and the JWKS the owner issuer's discovery names, that discovery held
 * to `issuer` exactly (an absent or non-https `jwks_uri` is unknown, and unknown stops the
 * kit). The access token's expected `iss` is derived here alone (`token_issuer`); a wrong
 * one is the sign-in probe's 502, which names the issuer the token carries.
 */
export async function upstreamFrom(issuer: string | undefined, clientId: string | undefined,
                                   fetcher: Fetch): Promise<UpstreamValues> {
  if (typeof issuer !== "string" || !/^https:\/\/\S+$/.test(issuer)) {
    throw new Error("OWNER_ISSUER is the owner issuer's https URL");
  }
  if (typeof clientId !== "string" || clientId === "") {
    throw new Error("WORKOS_CLIENT_ID is not set");
  }
  if (!/^[A-Za-z0-9_-]{1,256}$/.test(clientId)) {
    throw new Error("WORKOS_CLIENT_ID is a WorkOS client id (letters, digits, _ and -)");
  }
  const url = `${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`;
  const response = await fetcher(url, { headers: { Accept: "application/json" } });
  if (response.status !== 200) {
    throw new Error(`${url} answered ${response.status}`);
  }
  const discovery = await response.json() as Json;
  if (discovery["issuer"] !== issuer) {
    // Any other spelling refuses every sign-in (tasks 2): stop before deploying it.
    throw new Error(`the issuer's discovery names ${JSON.stringify(discovery["issuer"])}, ` +
                    `not OWNER_ISSUER ${JSON.stringify(issuer)}`);
  }
  const jwks = discovery["jwks_uri"];
  if (typeof jwks !== "string" || !/^https:\/\/\S+$/.test(jwks)) {
    throw new Error("the issuer's discovery names no https jwks_uri: unknown");
  }
  // The doors' parser (`upstreamConfig`) accepts this record: the kit's tests hold it so.
  const record = { client_id: clientId,
                   authorization_endpoint: `${WORKOS_API}/user_management/authorize`,
                   token_endpoint: `${WORKOS_API}/user_management/authenticate`,
                   jwks_uri: jwks,
                   // WorkOS's docs and SDK fixtures (research, 2026-11-06); the probe proves it.
                   token_issuer: `${WORKOS_API}/user_management/${clientId}`,
                   user_delete: `${WORKOS_API}/user_management/users/{sub}`,
                   authorize_params: { provider: "authkit" } };
  return { issuer, record };
}

/**
 * The authorize URL the doors build from `upstream` (`doors/src/oidc.ts` `authorizeUrl`),
 * for `state` and `challenge` and `CALLBACK`.
 */
export function authorizeLink(upstream: UpstreamValues, state: string, challenge: string):
    string {
  const record = upstream.record as { authorization_endpoint: string; client_id: string;
                                      authorize_params?: Record<string, string> };
  const url = new URL(record.authorization_endpoint);
  for (const [name, value] of Object.entries({
    ...record.authorize_params, response_type: "code", client_id: record.client_id,
    redirect_uri: CALLBACK, state, code_challenge: challenge, code_challenge_method: "S256",
    max_age: "0" })) {
    url.searchParams.set(name, value);
  }
  return url.href;
}

/** Where WorkOS sends a browser whose `redirect_uri` the client does not list. */
export const REDIRECT_UNLISTED = "https://error.workos.com/redirect-uri-invalid";

/**
 * The early sign-in check, before any person signs in: a `GET` of the WorkOS authorize URL
 * the doors build (`authorizeLink`, User Management's; never the doors' own `/authorize`)
 * with a throwaway state and S256 challenge (its verifier dropped, so no code it could end
 * in is redeemable), which must answer a 302 to the owner issuer's host, AuthKit's sign-in
 * page. A refused client or redirect (`error.workos.com/sso/client-id-invalid`,
 * `…/redirect-uri-invalid`, an `error=` answer) stops the kit, naming where it was sent.
 *
 * `unlisted`: `CALLBACK` is not known to be on the client's list yet (the plan job cannot
 * list it, and a run without `--yes` leaves it missing), so a `redirect-uri-invalid` answer
 * is reported as `dark`'s to register, not a stop; every other refusal still stops.
 */
export async function authorizeCheck(upstream: UpstreamValues, unlisted: boolean,
                                     fetcher: Fetch): Promise<string> {
  const { state, challenge } = throwaway();
  const link = authorizeLink(upstream, state, challenge);
  const answer = await fetcher(link, { redirect: "manual" });
  const location = answer.headers.get("Location");
  if (answer.status !== 302 || location === null) {
    throw new Error(`WorkOS's authorize answered ${answer.status}, not a redirect to the ` +
                    "sign-in page");
  }
  let to: URL;
  try {
    to = new URL(location, link);
  } catch {
    throw new Error("WorkOS's authorize redirected to no URL");
  }
  const host = new URL(upstream.issuer).host;
  if (unlisted && `${to.origin}${to.pathname}` === REDIRECT_UNLISTED) {
    return `WorkOS's authorize does not take ${CALLBACK} yet: dark registers it, then ` +
           "checks the client and it again";
  }
  if (to.protocol !== "https:" || to.host !== host || to.searchParams.has("error")
      || /error/i.test(to.pathname)) {
    throw new Error(`WorkOS's authorize refused the doors' client or ${CALLBACK}: it sent ` +
                    `the browser to ${to.origin}${to.pathname}, not ${host}'s sign-in`);
  }
  return `WorkOS's authorize takes the doors' client and ${CALLBACK}: it sent the browser ` +
         `to ${host}'s sign-in`;
}

/** The WorkOS redirect URI `CALLBACK`, registered when missing: GET, then POST; never a delete. */
export async function registerRedirect(key: string, apply: boolean, fetcher: Fetch):
    Promise<"present" | "missing" | "registered"> {
  const headers = { Authorization: `Bearer ${key}`, "Content-Type": "application/json" };
  const listed = await fetcher(`${WORKOS_API}/user_management/redirect_uris`, { headers });
  if (listed.status !== 200) {
    throw new Error(`WorkOS listed its redirect URIs with ${listed.status}`);
  }
  const { data } = await listed.json() as { data?: unknown };
  if (!Array.isArray(data)) {
    throw new Error("WorkOS's redirect URI listing holds no list");
  }
  if (data.some((row) => (row as Json | null)?.["uri"] === CALLBACK)) {
    return "present";
  }
  if (!apply) {
    return "missing";
  }
  const made = await fetcher(`${WORKOS_API}/user_management/redirect_uris`, {
    method: "POST", headers, body: JSON.stringify({ uri: CALLBACK }) });
  if (made.status !== 200 && made.status !== 201) {
    throw new Error(`WorkOS refused the redirect URI with ${made.status}`);
  }
  return "registered";
}

// --- E2B: the pair and the lifecycle webhook --------------------------------------------

/**
 * The runner the sandbox Worker ships, built as its build makes it (`build.command`,
 * `sandbox/template/bundle.mjs`, the same bytes every build): its SHA-256.
 */
export function shippedRunner(exec: Io["exec"] = IO.exec): string {
  const out = mkdtempSync(join(tmpdir(), "ta-runner-"));
  try {
    exec(process.execPath, [join(PLATFORM, "sandbox", "template", "bundle.mjs"), out],
         { stdio: "ignore" });
    return createHash("sha256").update(readFileSync(join(out, "runner.mjs"))).digest("hex");
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/**
 * The E2B tag a (template, runner) pair is marked with once it passed its standing checks
 * (`build.py`'s `pair_tag`): the template's `name:tag`, its tag followed by the runner's.
 */
export function pairTag(template: string, runner: string): string {
  if (!/^[0-9a-f]{64}$/.test(runner)) {
    throw new Error("a runner is named by its SHA-256");
  }
  return `${template}-r${runner.slice(0, 16)}`;
}

/**
 * Whether this run sets E2B's copy of the webhook secret: only when it sets the doors' copy
 * (missing on the doors, named in `--rotate`, or the hook made this run), so the two never
 * drift apart.
 */
export function hookSetsSecret(setting: readonly { worker: string; name: string }[]): boolean {
  return setting.some((each) => each.worker === "doors" && each.name === "E2B_WEBHOOK_SECRET");
}

/**
 * E2B's lifecycle webhook as its listing holds it, matched by its URL, or null when it holds
 * none. A failed listing, or two hooks for the URL, throws.
 */
export async function heldHook(e2b: E2b): Promise<Json | null> {
  const hooks = await e2b.webhooks();
  const ours = hooks.filter((hook) => (hook as Json | null)?.["url"] === E2B_HOOK.url) as Json[];
  if (ours.length > 1) {
    throw new Error(`E2B holds ${ours.length} webhooks for ${E2B_HOOK.url}: keep one`);
  }
  return ours[0] ?? null;
}

/**
 * E2B's lifecycle webhook (`heldHook`). Missing, it is registered with `secret`, disabled
 * unless `enable`; the run that does so sets the doors' copy of the secret too
 * (`secretPlan`). Held, it is changed only where the run asks: its signature secret only
 * with `setSecret`, when this run also sets the doors' E2B_WEBHOOK_SECRET (E2B never shows a
 * hook's secret, so the two change together or not at all); its events when they differ;
 * and it is enabled with `enable` (mode `routes`). It is never disabled or deleted. Without
 * `apply` it only lists and checks. A failed listing or write throws.
 */
export async function lifecycleHook(e2b: E2b, secret: string, apply: boolean, enable: boolean,
                                    setSecret: boolean): Promise<string> {
  const hook = await heldHook(e2b);
  if (hook === null) {
    const state = enable ? "enabled" : "disabled";
    if (!apply) {
      return `missing: registered, ${state}`;
    }
    await e2b.addWebhook({ name: E2B_HOOK.name, url: E2B_HOOK.url, events: [...E2B_HOOK.events],
                           enabled: enable, signatureSecret: secret });
    return `registered, ${state}`;
  }
  const events = Array.isArray(hook["events"]) ? [...hook["events"] as string[]].sort() : [];
  const change: Json = {
    ...(setSecret ? { signatureSecret: secret } : {}),
    ...(JSON.stringify(events) !== JSON.stringify([...E2B_HOOK.events].sort())
      ? { events: [...E2B_HOOK.events] } : {}),
    ...(enable && hook["enabled"] !== true ? { enabled: true } : {}),
  };
  const state = hook["enabled"] === true || enable ? "enabled" : "disabled";
  const what = Object.keys(change).length === 0 ? "unchanged"
    : `${Object.keys(change).map((field) => field === "signatureSecret" ? "its secret"
      : field === "events" ? "its events" : "enabled").join(", ")} set`;
  if (!apply || Object.keys(change).length === 0) {
    return `present, ${what}, ${state}`;
  }
  await e2b.changeWebhook(String(hook["id"]), change);
  return `present, ${what}, ${state}`;
}

// --- Secrets ----------------------------------------------------------------------------

/**
 * A keyring as the gateway and the files Worker read it (`contract/src/envelope.ts`
 * `parseRing`), the secret it is held as named in its refusal: a ring either Worker would
 * refuse stops the deploy before any change.
 */
export function keyring(text: string | undefined, name: string): Ring {
  const ring = parseRing(text);
  if (ring === null) {
    throw new Error(`${name} is {active, keys} as JSON, each key 32 bytes in base64, its ` +
                    "active key among its keys");
  }
  return ring;
}

/**
 * What to set: each Worker's missing secrets, and those `rotate` names (the shared routing
 * key on both or neither; a keyring only as an addition). `held` is each Worker's listing,
 * null for a Worker the API says does not exist. A webhook made this run (`hookCreated`)
 * takes production's secret, so the doors take it too.
 */
export function secretPlan(held: Readonly<Record<string, readonly string[] | null>>,
                           rotate: ReadonlySet<string>,
                           values: ((name: string) => string | undefined) | null,
                           hookCreated = false):
    { worker: string; name: string }[] {
  const has = (worker: string, name: string) => (held[worker] ?? []).includes(name);
  for (const name of SHARED) {
    const holders = Object.keys(SECRETS).filter((worker) => SECRETS[worker]!.includes(name));
    if (new Set(holders.map((worker) => has(worker, name))).size > 1 && !rotate.has(name)) {
      throw new Error(`only one of ${holders.map((worker) => `the ${worker}`).join(" and ")} ` +
                      `holds ${name}: pass --rotate ${name} to set both again`);
    }
  }
  // `values` null: the plan job, which holds no secret; the apply job checks the ring.
  for (const name of KEYRINGS) {
    if (rotate.has(name) && values !== null) {
      const ring = keyring(values(name), name);
      if (ring.keys.size < 2) {
        throw new Error(`a keyring is rotated by adding a key: the new ${name} holds ` +
                        "the old keys and a new active one (keyring-add)");
      }
    }
  }
  const plan: { worker: string; name: string }[] = [];
  for (const [worker, names] of Object.entries(SECRETS)) {
    for (const name of names) {
      const withHook = hookCreated && worker === "doors" && name === "E2B_WEBHOOK_SECRET";
      if (!has(worker, name) || rotate.has(name) || withHook) {
        plan.push({ worker, name });
      }
    }
  }
  return plan;
}

// --- Wrangler ---------------------------------------------------------------------------

/** One wrangler command, its stdin given: its stdout, or a throw. */
export type Wrangler = (args: string[], stdin?: string) => string;

/** Wrangler in the platform's folder, for the zone's `account`, with the run's environment. */
export function wranglerFor(run: Run, account: string): Wrangler {
  return (args, stdin) => {
    const result = run.io.spawn(process.execPath, [WRANGLER, ...args], {
      cwd: PLATFORM, input: stdin, encoding: "utf8",
      env: { ...run.env, CLOUDFLARE_ACCOUNT_ID: account, WRANGLER_SEND_METRICS: "false" },
    });
    if (result.status !== 0) {
      // Its stderr alone: wrangler never echoes a secret's value.
      throw new Error(`wrangler ${args.slice(0, 3).join(" ")} failed (exit ${result.status}): ` +
                      `${String(result.stderr).trim()}`);
    }
    return String(result.stdout);
  };
}

/** Whether a wrangler listing names `name` as a whole word (never a longer name holding it). */
export function listed(output: string, name: string): boolean {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(^|[\\s│|:])${escaped}($|[\\s│|])`, "m").test(output);
}

/** The version id `wrangler versions upload` prints, or a throw. */
export function versionOf(output: string): string {
  const id = /Worker Version ID:\s*([0-9a-f-]{36})/.exec(output)?.[1];
  if (id === undefined) {
    throw new Error("wrangler versions upload printed no Worker Version ID");
  }
  return id;
}

/**
 * The deployed config of `worker`: its checked-in config with the namespace ids it names
 * filled, the doors' upstream values set and, with `routes`, the doors' routes; nothing
 * else changes, so what the guards hold is what deploys. It is written outside the Worker's
 * folder, so its paths are made absolute: `main` and the doors' app files'
 * `assets.directory`, which wrangler reads relative to the config file, and the folder the
 * sandbox's custom build runs in (`build.cwd`), which is otherwise wrangler's own working
 * directory.
 */
export function deployedConfig(worker: string, ids: Readonly<Record<string, string>>,
                               upstream: UpstreamValues, routes: boolean): Json {
  const config = structuredClone(CONFIGS.get(worker)) as Json;
  config["main"] = join(PLATFORM, worker, String(config["main"]));
  const assets = config["assets"] as Json | undefined;
  if (assets !== undefined) {
    assets["directory"] = join(PLATFORM, worker, String(assets["directory"]));
  }
  const build = config["build"] as Json | undefined;
  if (build !== undefined) {
    build["cwd"] = join(PLATFORM, worker, String(build["cwd"] ?? "."));
  }
  delete config["$schema"];
  for (const kv of (config["kv_namespaces"] ?? []) as { binding: string; id: string }[]) {
    const id = ids[kv.binding];
    if (id === undefined) {
      throw new Error(`${worker}: no namespace for ${kv.binding}`);
    }
    kv.id = id;
  }
  if (worker === "doors") {
    config["vars"] = { ...(config["vars"] as Json), OWNER_ISSUER: upstream.issuer,
                       UPSTREAM: JSON.stringify(upstream.record) };
    if (routes) {
      config["routes"] = [...ROUTES.map((pattern) => ({ pattern, zone_name: ZONE })),
                          { pattern: EGRESS_DOMAIN, custom_domain: true }];
    }
  }
  return config;
}

// --- The probe --------------------------------------------------------------------------

/**
 * The probe's handover (mode `probe`): `PROBE_ROUTES` alone, each added for the doors, or
 * left when the doors hold it already. A path another Worker (or no Worker) serves, by the
 * same, a narrower or a wider pattern, stops it: the probe takes only paths nothing serves.
 */
export function probeSteps(routes: readonly Route[]): RouteStep[] {
  const served = routes.filter((route) => route.script !== DOORS && PROBE_ROUTES.some(
    (pattern) => route.pattern === pattern || narrower(route.pattern, pattern)
      || narrower(pattern, route.pattern)));
  if (served.length > 0) {
    throw new Error(`the probe takes only paths nothing serves, and ${served.map((route) =>
      `${route.pattern} is ${route.script ?? "no Worker"}'s`).join(", ")}: nothing is changed`);
  }
  return handover(routes, DOORS, PROBE_ROUTES);
}

/**
 * Whether a control record, as KV holds it, admits nobody: no owner's sign-in and no outside
 * client (`doors/src/control.ts`), as the dark record does. Missing or any other shape is
 * not.
 */
export function admitsNobody(record: unknown): boolean {
  const held = record as { ordinary?: unknown; canary?: unknown } | null | undefined;
  return typeof held === "object" && held !== null
    && (held.ordinary === "none"
        || (held.ordinary === "canary" && Array.isArray(held.canary) && held.canary.length === 0));
}

/**
 * The one first-party sign-in link the probe prints: the doors' own app's authorization
 * (`doors/app/api.js`), with a fresh S256 challenge whose verifier is dropped, so no code it
 * could end in is ever redeemable.
 */
export function probeLink(): string {
  const { state, challenge } = throwaway();
  const query = new URLSearchParams({
    response_type: "code", client_id: FIRST_PARTY.client, redirect_uri: FIRST_PARTY.redirect,
    state, code_challenge: challenge, code_challenge_method: "S256",
    resource: FIRST_PARTY.resource });
  return `${AUTHORIZE}?${query}`;
}

/**
 * A fresh state and S256 challenge, the challenge's verifier dropped here, so no code a flow
 * started with them could end in is ever redeemable (the probe's link, `authorizeCheck`).
 */
export function throwaway(): { state: string; challenge: string } {
  return { state: randomBytes(16).toString("base64url"),
           challenge: createHash("sha256").update(randomBytes(32).toString("base64url"))
             .digest("base64url") };
}

/** What the probe's sign-in shows, read from where the browser ends. */
export const PROBE_SIGNS = [
  `Pass: the browser's address begins ${FIRST_PARTY.redirect}?error=temporarily_unavailable ` +
  "(more follows it; the page itself is the old app's and reads as a sign-in error, " +
  "which is expected): the closed gate refused the sign-in after every " +
  "check passed, and no owner was made. The doors' client and API key were taken at User " +
  "Management's authenticate, the callback was its registered redirect, and the access " +
  "token's signature, iss, client_id, sub (the answer's user.id) and auth_time held.",
  `Fail, at ${AUTHORIZE}: a 400 page, so the doors could not read their own ` +
  "app's client document.",
  "Fail, at error.workos.com or another error page before or after the sign-in: WorkOS " +
  "refused the doors' client or the callback.",
  `Fail, at ${CALLBACK}: a 502 page naming the check that failed (the token endpoint's ` +
  "status and error code, auth_time, client_id...), the same line in the doors' log. On " +
  "issuer, it names the iss the token carries: that value becomes upstreamFrom's " +
  "token_issuer.",
  `Fail, at ${FIRST_PARTY.redirect}?error=access_denied ("sign in again"): auth_time is more ` +
  "than 600 s old or ahead of the clock, so max_age=0 did not force a fresh sign-in.",
].join("\n");

// --- The steps --------------------------------------------------------------------------

/** The run's production secrets reached: its E2B client and keys. */
function secretsReached(run: Run): boolean {
  return run.reach === "secrets";
}

/** Every input the mode reads is set: the vars, and on a secret run the production secrets. */
const inputs = step("inputs", (run) => {
  for (const name of [...PUBLIC_INPUTS, ...(secretsReached(run) ? SECRET_INPUTS : [])]) {
    if (input(run, name) === undefined) {
      throw new Error(`${name} is not set (${SECRET_INPUTS.includes(name)
        ? "the production environment's secrets" : "the workflow's inputs"})`);
    }
  }
});

/** Each keyring is one the gateway and the files Worker would take. */
const keyrings = step("keyrings", (run) => {
  if (secretsReached(run)) {
    for (const name of KEYRINGS) {
      keyring(input(run, name), name);
    }
  }
});

/** No Worker deploys a migration its lock does not pin, checked before anything is changed. */
const locks = step("locks", (run, work) => {
  for (const worker of MIGRATED) {
    for (const [path, digests] of Object.entries(codeDigests(worker, guardRun(run)))) {
      const refused = lockRefusal(JSON.parse(readFileSync(join(PLATFORM, path), "utf8")),
                                  digests, path);
      if (refused !== null) {
        throw new Error(refused);
      }
    }
  }
  work.plan.push(`the migration locks pin every migration the ${MIGRATED.join(" and ")} ` +
                 "Workers ship");
});

/** The zone and its account, which Cloudflare's client and wrangler are given. */
const zone = step("zone", async (run, work) => {
  const cloudflare = new Cloudflare(cfApi(input(run, "CLOUDFLARE_API_TOKEN")!, run.io.fetch));
  const { id, account } = await cloudflare.zone(ZONE);
  maskInLog(account, run.env, run.io.log);
  Object.assign(work, { cloudflare, zone: id, account, wrangler: wranglerFor(run, account) });
});

/** The doors' upstream, from the owner issuer's live discovery. */
const upstream = step("upstream", async (run, work) => {
  work.upstream = await upstreamFrom(input(run, "OWNER_ISSUER"), input(run, "WORKOS_CLIENT_ID"),
                                     run.io.fetch);
  work.plan.push(`set the doors' OWNER_ISSUER to ${work.upstream.issuer}, held to its discovery`);
});

/** E2B has a ready build of the pinned template: the first of the run's calls to E2B. */
const templateBuilt = step("template", async (run, work) => {
  const pinned = template();
  if (secretsReached(run)) {
    work.e2b = new E2b(input(run, "E2B_API_KEY")!, run.io.fetch);
  }
  if (work.e2b === undefined) {
    work.plan.push(`E2B's ready build of ${pinned}: ${LATER}`);
  } else if (!await work.e2b.ready(pinned)) {
    throw new Error(`E2B has no ready build of ${pinned}: build it (sandbox/template/build.py)`);
  }
});

/** The pair this deploy makes, the template and the runner the sandbox Worker ships, passed. */
const pairPassed = step("pair", async (run, work) => {
  const pair = pairTag(template(), shippedRunner(run.io.exec));
  if (work.e2b === undefined) {
    work.plan.push(`the pair ${pair} passed its standing checks: ${LATER}`);
  } else if (await work.e2b.ready(pair)) {
    work.plan.push(`the pair ${pair} passed its standing checks in this E2B team`);
  } else if (!run.apply) {
    work.plan.push(`the pair ${pair} has not passed in this E2B team: the pair job runs its ` +
                   "check (--mode pair) before the apply job");
  } else {
    throw new Error(`the pair ${pair} has not passed its standing checks in this E2B team: ` +
                    "run the pair job (--mode pair) first; nothing is deployed");
  }
});

/** E2B's lifecycle webhook, listed and checked before anything is changed. */
const hookListed = step("hook listed", async (_run, work) => {
  if (work.e2b !== undefined) {
    work.hook = await heldHook(work.e2b);
  }
});

/**
 * The WorkOS redirect URI, listed, and registered when missing (additive, never a delete)
 * on an applied run, before any Cloudflare change: the early sign-in check needs it listed.
 */
const redirect = step("redirect", async (run, work) => {
  if (secretsReached(run)) {
    work.redirect = await registerRedirect(input(run, "WORKOS_API_KEY")!, run.apply,
                                           run.io.fetch);
  }
  work.plan.push(`WorkOS redirect URI ${CALLBACK}: ${work.redirect ?? LATER}`);
});

/**
 * The early sign-in check: a callback the run could not list, or found missing, is one a
 * redirect-uri-invalid answer leaves to `dark` to register.
 */
const authorize = step("authorize", async (run, work) => {
  work.plan.push(await authorizeCheck(made(work.upstream, "the upstream"),
                                      work.redirect === undefined || work.redirect === "missing",
                                      run.io.fetch));
});

/** The doors' routes' handover, from the zone's routes as they stand. */
const routePlan = step("route plan", async (_run, work) => {
  const routes = await made(work.cloudflare, "Cloudflare").routes(made(work.zone, "the zone"));
  work.routes = handover(routes, DOORS, ROUTES);
});

/** Each KV namespace by its title, made when the account's listing lacks it. */
const namespaces = step("namespaces", async (run, work) => {
  const cloudflare = made(work.cloudflare, "Cloudflare");
  const account = made(work.account, "the account");
  const titles = await cloudflare.namespaces(account);
  work.ids = {};
  work.unmade = new Set();
  for (const [binding, title] of Object.entries(NAMESPACES)) {
    const id = titles.get(title);
    if (id !== undefined) {
      work.ids[binding] = id;
      continue;
    }
    work.plan.push(`create the KV namespace ${title} (${binding})`);
    if (run.apply) {
      made(work.wrangler, "wrangler")(["kv", "namespace", "create", title]);
      const made_ = (await cloudflare.namespaces(account)).get(title);
      if (made_ === undefined) {
        throw new Error(`the KV namespace ${title} was not made`);
      }
      work.ids[binding] = made_;
    } else {
      // A planned config names the namespace it would be made as.
      work.ids[binding] = `planned-${title}`;
      work.unmade.add(binding);
    }
  }
});

/** Storage wrangler lists and makes: its listing command, its noun and its make command. */
function storage(name: string, list: string[], noun: string, create: (made: string) => string[],
                 names: readonly string[]): Step {
  return step(name, (run, work) => {
    const wrangler = made(work.wrangler, "wrangler");
    for (const each of names) {
      // `wrangler` throws on a failed listing: a failure is never "absent".
      if (listed(wrangler(list), each)) {
        continue;
      }
      work.plan.push(`create the ${noun} ${each}`);
      if (run.apply) {
        wrangler(create(each));
      }
    }
  });
}

/** The R2 buckets the configs bind. */
const buckets = storage("buckets", ["r2", "bucket", "list"], "R2 bucket",
                        (bucket) => ["r2", "bucket", "create", bucket], BUCKETS);
/** The queues the configs send to. */
const queues = storage("queues", ["queues", "list"], "queue",
                       (queue) => ["queues", "create", queue], QUEUES);

/** Reached only when every listing answered: a token without the scope stopped above. */
const listings = step("listings", (_run, work) => {
  work.plan.push("the Cloudflare token listed the account's KV namespaces, R2 buckets and queues");
});

/** Each Worker's config as it deploys, written outside the checkout. */
const staged = step("staged", (_run, work) => {
  work.out = mkdtempSync(join(tmpdir(), "tinyassets-deploy-"));
  work.paths = {};
  for (const worker of WORKERS) {
    mkdirSync(join(work.out, worker));
    work.paths[worker] = join(work.out, worker, "wrangler.json");
    writeFileSync(work.paths[worker]!, JSON.stringify(
      deployedConfig(worker, made(work.ids, "the namespaces"), made(work.upstream, "the upstream"),
                     false), null, 2));
  }
});

/** Each Worker's missing secrets (and the rotated), first: `secrets.required` refuses a deploy. */
const secrets = step("secrets", async (run, work) => {
  const cloudflare = made(work.cloudflare, "Cloudflare");
  work.held = {};
  for (const worker of Object.keys(SECRETS)) {
    work.held[worker] = await cloudflare.secrets(made(work.account, "the account"),
                                                 NAMES[worker]!);
  }
  work.setting = secretPlan(work.held, run.rotate,
                            secretsReached(run) ? (name) => input(run, name) : null,
                            work.hook === null);
  for (const { worker, name } of work.setting) {
    const value = input(run, name);
    if (value === undefined && secretsReached(run)) {
      if (OPTIONAL.includes(name)) {
        continue;
      }
      throw new Error(`${name} has no value for ${NAMES[worker]}`);
    }
    work.plan.push(`set the secret ${name} on ${NAMES[worker]}${secretsReached(run) ? ""
      : ` (its value, or its absence if optional: ${LATER})`}`);
    if (run.apply) {
      made(work.wrangler, "wrangler")(["secret", "put", name, "--config",
                                       made(work.paths, "the configs")[worker]!], value);
    }
  }
});

/**
 * Each Worker deployed, bound first, touching no route. A first deploy (the API says the
 * Worker does not exist) is `wrangler deploy`; later ones are Cloudflare's staged path,
 * `versions upload` then `versions deploy` at 100%, which leaves the Worker's routes as they
 * are. Wrangler 4.154 refuses a versions upload with a pending Durable Object migration:
 * `--migrate <worker>` deploys it whole.
 */
const workers = step("workers", async (run, work) => {
  const wrangler = made(work.wrangler, "wrangler");
  const held = made(work.held, "the secret listings");
  for (const worker of WORKERS) {
    // Whether it exists: its secret listing's answer (null when the API says it does not).
    const exists = (worker in held ? held[worker]
      : await made(work.cloudflare, "Cloudflare").secrets(made(work.account, "the account"),
                                                          NAMES[worker]!)) !== null;
    const whole = !exists || run.migrate.has(worker);
    work.plan.push(`${whole ? "deploy" : "upload and deploy a version of"} ${NAMES[worker]}, ` +
                   "touching no route");
    if (run.apply) {
      const config = made(work.paths, "the configs")[worker]!;
      if (whole) {
        wrangler(["deploy", "--config", config]);
      } else {
        const id = versionOf(wrangler(["versions", "upload", "--config", config]));
        wrangler(["versions", "deploy", `${id}@100`, "--yes", "--config", config]);
      }
    }
  }
});

/** The control record, written closed only when the API answered there is none. */
const control = step("control", async (run, work) => {
  const ids = made(work.ids, "the namespaces");
  const account = made(work.account, "the account");
  const cloudflare = made(work.cloudflare, "Cloudflare");
  if (made(work.unmade, "the namespaces").has("CONTROL")
      || await cloudflare.value(account, ids["CONTROL"]!, CONTROL_KEY) === undefined) {
    work.plan.push("write the control record closed (outside off, canary none, inbound off, " +
                   "the founder's tier numbers): the API answered there is none");
    if (run.apply) {
      const status = await cloudflare.put(account, ids["CONTROL"]!, CONTROL_KEY, DARK_CONTROL);
      if (status !== 200) {
        throw new Error(`writing the control record failed (${status})`);
      }
    }
  }
});

/** The plan job's word on E2B's lifecycle webhook, which it cannot reach. */
const hookLater = step("hook later", (run, work) => {
  if (!secretsReached(run)) {
    work.plan.push(`E2B lifecycle webhook ${E2B_HOOK.url}: ${LATER}`);
  }
});

/**
 * E2B's lifecycle webhook, registered or changed once the doors hold E2B_WEBHOOK_SECRET:
 * disabled in `dark`, enabled only after the routes, since an enabled hook sends the whole
 * team's events to whatever serves its path.
 */
function hook(enable: boolean, where: string): Step {
  return step(enable ? "hook enabled" : "hook", async (run, work) => {
    if (work.e2b !== undefined) {
      const result = await lifecycleHook(work.e2b, input(run, "E2B_WEBHOOK_SECRET")!, run.apply,
                                         enable, hookSetsSecret(made(work.setting, "the secrets")));
      work.plan.push(`E2B lifecycle webhook ${E2B_HOOK.url}${where}: ${result}`);
    }
  });
}

/** The doors' routes handed over one at a time, no path ever served by nothing. */
const handOver = step("hand over", async (run, work) => {
  const steps = made(work.routes, "the route plan");
  work.plan.push(...steps.map((each) => describeStep(each, DOORS)));
  if (run.apply) {
    await made(work.cloudflare, "Cloudflare").handOver(made(work.zone, "the zone"), DOORS, steps);
  }
});

/** The sandbox's egress host, the doors' custom domain (`wrangler triggers deploy`). */
const customDomain = step("custom domain", (run, work) => {
  work.plan.push(`add the custom domain ${EGRESS_DOMAIN} for ${DOORS} (wrangler triggers deploy)`);
  if (run.apply) {
    const routed = join(made(work.out, "the configs"), "doors", "routed.json");
    writeFileSync(routed, JSON.stringify(deployedConfig("doors", made(work.ids, "the namespaces"),
                                                        made(work.upstream, "the upstream"),
                                                        true), null, 2));
    made(work.wrangler, "wrangler")(["triggers", "deploy", "--config", routed]);
  }
});

/** The plan reported; a run that changes nothing stops here, saying so. */
function reported(unchanged: string): Step {
  return step("report", (run, work) => {
    report(work.plan, run.apply, run);
    if (!run.apply) {
      run.io.log(unchanged);
      work.done = true;
    }
  });
}

/** Fact 11: after a deploy or a route change, the handles canary (it reads its own token). */
const canary = step("canary", (run) => {
  const ran = run.io.spawn("python3", [join(REPO, "scripts", "mcp_public_canary.py"),
                                       "--assert-handles"], { stdio: "inherit", cwd: REPO });
  if (ran.status !== 0) {
    throw new Error(`the handles canary failed (exit ${ran.status})`);
  }
});

const h2Next = step("h2 next", (run) => {
  run.io.log("H2's probes wait for a routed door and a probe owner's key: run with --mode h2 " +
             "(h2-probes.ts names what it needs).");
});

/** The probe's own inputs: the Cloudflare token, and on a secret run WorkOS's key. */
const probeInputs = step("probe inputs", (run) => {
  for (const name of ["CLOUDFLARE_API_TOKEN", ...(secretsReached(run) ? ["WORKOS_API_KEY"] : [])]) {
    if (input(run, name) === undefined) {
      throw new Error(`${name} is not set`);
    }
  }
});

/** The doors are deployed: a dark run came first. */
const doorsDeployed = step("doors deployed", async (_run, work) => {
  if (await made(work.cloudflare, "Cloudflare").secrets(made(work.account, "the account"), DOORS)
      === null) {
    throw new Error(`${DOORS} is not deployed: the probe runs after a dark run`);
  }
  work.plan.push(`${DOORS} is deployed`);
});

/** The control record admits nobody, as a dark run writes it. */
const gatesClosed = step("gates closed", async (_run, work) => {
  const cloudflare = made(work.cloudflare, "Cloudflare");
  const account = made(work.account, "the account");
  const namespace = (await cloudflare.namespaces(account)).get(NAMESPACES["CONTROL"]!);
  if (namespace === undefined
      || !admitsNobody(await cloudflare.value(account, namespace, CONTROL_KEY))) {
    throw new Error("the control record is missing or admits someone: the probe runs only " +
                    "with every gate closed, as a dark run writes it");
  }
  work.plan.push("the control record admits no owner and no outside client; no gate is changed");
});

/** WorkOS lists the callback, as a dark run registers it. */
const callbackListed = step("callback listed", async (run, work) => {
  if (!secretsReached(run)) {
    work.plan.push(`WorkOS lists ${CALLBACK}, as a dark run registers it: checked by the ` +
                   "apply job before any change");
  } else if (await registerRedirect(input(run, "WORKOS_API_KEY")!, false, run.io.fetch)
             !== "present") {
    throw new Error(`WorkOS lists no ${CALLBACK}: run dark first, which registers it`);
  } else {
    work.plan.push(`WorkOS lists ${CALLBACK}, as a dark run registers it`);
  }
});

/** The probe's handover, from the zone's routes as they stand. */
const probePlan = step("probe plan", async (_run, work) => {
  work.routes = probeSteps(await made(work.cloudflare, "Cloudflare")
    .routes(made(work.zone, "the zone")));
});

/**
 * The early sign-in check, before a route moves, on the run that moves them: WorkOS takes the
 * client and CALLBACK, which it lists, so a redirect-uri-invalid answer stops the probe.
 */
const probeAuthorize = step("probe authorize", async (run, work) => {
  if (run.apply) {
    work.plan.push(await authorizeCheck(await upstreamFrom(input(run, "OWNER_ISSUER"),
                                                           input(run, "WORKOS_CLIENT_ID"),
                                                           run.io.fetch), false, run.io.fetch));
  }
});

/** The sign-in's two paths handed to the doors, or found theirs already. */
const probeHandOver = step("probe hand over", async (run, work) => {
  const steps = made(work.routes, "the probe's plan");
  work.plan.push(...(steps.length === 0 ? [`${DOORS} holds ${PROBE_ROUTES.join(" and ")} already`]
    : steps.map((each) => describeStep(each, DOORS))));
  if (run.apply) {
    await made(work.cloudflare, "Cloudflare").handOver(made(work.zone, "the zone"), DOORS, steps);
  }
});

const signInLink = step("sign-in link", (run) => {
  run.io.log(`\nSign in once, in a browser, at:\n${probeLink()}\n\n${PROBE_SIGNS}`);
});

/** H2's probes alone: they change nothing, and need a routed door and a probe owner. */
const h2 = step("h2", (run) => {
  if (!secretsReached(run)) {
    report(["run H2's probes against the routed doors; nothing is changed"], false, run);
    return;
  }
  run.io.exec(process.execPath, ["--experimental-strip-types",
                                 join(PLATFORM, "deploy", "h2-probes.ts")],
              { stdio: "inherit", env: { ...run.env } });
});

/**
 * The pair check (the workflow's `pair` job, before the apply job): the pinned template and
 * the runner this checkout ships, unless E2B already holds their pair's tag; `build.py
 * --check-pair` runs the standing checks and tags the pair, given only what it needs (never
 * the deploy's other secrets). A failure, or no tag after it, throws.
 */
const pair = step("pair check", async (run) => {
  const key = input(run, "E2B_API_KEY");
  if (key === undefined) {
    throw new Error("E2B_API_KEY is not set (the production environment's secrets)");
  }
  const e2b = new E2b(key, run.io.fetch);
  const tag = pairTag(template(), shippedRunner(run.io.exec));
  if (await e2b.ready(tag)) {
    run.io.log(`the pair ${tag} passed its standing checks in this E2B team`);
    return;
  }
  const checked = run.io.spawn("python3", [join(PLATFORM, "sandbox", "template", "build.py"),
                                           "--check-pair"],
                               { stdio: "inherit", env: { PATH: run.env["PATH"] ?? "",
                                                          HOME: run.env["HOME"] ?? "",
                                                          E2B_API_KEY: key } });
  if (checked.status !== 0 || !await e2b.ready(tag)) {
    throw new Error(`the pair ${tag} failed its standing checks: nothing is deployed`);
  }
  run.io.log(`the pair ${tag} passed its standing checks, run now`);
});

// --- The modes --------------------------------------------------------------------------

/** One mode: its steps, in order, and what it takes. */
export interface Row {
  readonly steps: readonly Step[];
  /** A row that plans changes holds its run to them: `--public` and `--yes` apart. */
  readonly plans: boolean;
  /** Whether `--yes` may apply what it plans. */
  readonly applies: boolean;
  /** Whether it takes `--rotate` and `--migrate`. */
  readonly options: boolean;
}

/** The checks every deploy makes before any change, in this order. */
const CHECKS: readonly Step[] = [inputs, keyrings, locks, zone, upstream, templateBuilt,
                                 pairPassed, hookListed, redirect, authorize];
/** Storage, then the Workers, then the control record. */
const STORAGE: readonly Step[] = [namespaces, buckets, queues, listings];
const DEPLOY: readonly Step[] = [staged, secrets, workers, control, hookLater];
const DARK: readonly Step[] = [...CHECKS, ...STORAGE, ...DEPLOY, hook(false, ""),
                               reported("\nNothing was changed."), canary, h2Next];

export const MODES: Readonly<Record<Mode, Row>> = Object.freeze({
  plan: { steps: DARK, plans: true, applies: false, options: true },
  dark: { steps: DARK, plans: true, applies: true, options: true },
  probe: {
    steps: [probeInputs, zone, doorsDeployed, gatesClosed, callbackListed, probePlan,
            probeAuthorize, probeHandOver,
            reported("\nNothing was changed. The apply job prints the sign-in link."), canary,
            signInLink],
    plans: true, applies: true, options: false,
  },
  routes: {
    steps: [...CHECKS, routePlan, ...STORAGE, ...DEPLOY, handOver, customDomain,
            hook(true, ", after the routes"), reported("\nNothing was changed."), canary,
            h2Next],
    plans: true, applies: true, options: true,
  },
  h2: { steps: [h2], plans: false, applies: true, options: true },
  pair: { steps: [pair], plans: false, applies: true, options: true },
});

// --- The keyring tools ------------------------------------------------------------------

/** `keyring-new <file>`: a first keyring, written for the founder to store; never printed. */
export function keyringNew(file: string, log: Io["log"] = IO.log): void {
  writeFileSync(file, ringText({ active: "k1", keys: new Map([["k1", randomBytes(KEY_BYTES)]]) }),
                { mode: 0o600, flag: "wx" });
  log(`wrote a new keyring to ${file}; store it as ${KEYRINGS.join(" or ")}, one ring each`);
}

/**
 * `keyring-add <file>`: the keyring in `file` with one more key under the kid after its
 * highest (never a retired kid's name), made active; the old kept.
 */
export function keyringAdd(file: string, log: Io["log"] = IO.log): void {
  const ring = ringWith(keyring(readFileSync(file, "utf8"), "the keyring"),
                        randomBytes(KEY_BYTES));
  writeFileSync(file, ringText(ring), { mode: 0o600 });
  log(`added ${ring.active} to ${file}, active; store it as the keyring it was, then run with ` +
      "--rotate naming that keyring");
}

const TOOLS: Readonly<Record<string, (file: string, log: Io["log"]) => void>> = Object.freeze({
  "keyring-new": keyringNew, "keyring-add": keyringAdd });

// --- The command line -------------------------------------------------------------------

function option(argv: readonly string[], name: string): ReadonlySet<string> {
  const at = argv.indexOf(name);
  return new Set((at < 0 ? "" : argv[at + 1] ?? "").split(",").filter(Boolean));
}

/**
 * The run `argv` asks for, over `env`: its mode and options, held to what the mode's row
 * takes. Every refusal is before any step.
 */
export function parse(argv: readonly string[], env: Readonly<Record<string, string | undefined>>,
                      io: Io = IO): Run {
  const at = argv.indexOf("--mode");
  const mode = at < 0 ? undefined : argv[at + 1];
  if (mode === undefined || !Object.hasOwn(MODES, mode)) {
    throw new Error(`--mode is one of ${Object.keys(MODES).join(", ")}`);
  }
  if (argv.indexOf("--mode", at + 1) >= 0) {
    throw new Error("--mode is given once: a run is one mode");
  }
  const row = MODES[mode as Mode];
  const run: Run = Object.freeze({
    mode: mode as Mode, reach: argv.includes("--public") ? "public" : "secrets",
    apply: argv.includes("--yes"), rotate: option(argv, "--rotate"),
    migrate: option(argv, "--migrate"), env, io });
  if (row.plans) {
    if (run.apply && run.reach === "public") {
      throw new Error("--public is the plan job's: it never changes anything");
    }
    if (run.apply && !row.applies) {
      throw new Error(`mode ${mode} changes nothing: it takes no --yes`);
    }
    if (run.migrate.has("doors")) {
      throw new Error("the doors hold no Durable Object migration: --migrate never names them");
    }
    if (!row.options && (run.rotate.size > 0 || run.migrate.size > 0)) {
      throw new Error(`mode ${mode} deploys nothing: it takes no --rotate or --migrate`);
    }
  }
  return run;
}

/** Runs the row's steps in order, then removes what the run wrote outside the checkout. */
export async function perform(run: Run): Promise<Work> {
  const work: Work = { plan: [] };
  try {
    for (const each of MODES[run.mode].steps) {
      if (work.done === true) {
        break;
      }
      await each.run(run, work);
    }
  } finally {
    if (work.out !== undefined) {
      rmSync(work.out, { recursive: true, force: true });
    }
  }
  return work;
}

async function main(argv: readonly string[]): Promise<void> {
  const tool = TOOLS[argv[0] ?? ""];
  if (tool !== undefined) {
    if (argv[1] === undefined) {
      throw new Error(`${argv[0]} <file>`);
    }
    tool(argv[1], IO.log);
    return;
  }
  await perform(parse(argv, { ...process.env }));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error: Error) => {
    console.error(`deploy stopped: ${error.message}`);
    process.exit(1);
  });
}
