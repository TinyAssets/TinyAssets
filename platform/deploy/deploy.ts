/**
 * The dark deploy (tasks 5, "0c (live, slice L)"; tasks 2, "DNS, routes and deploy"): one
 * reviewed step that deploys the six platform Workers to the production Cloudflare account
 * with no public route, runs the handles canary (AGENTS.md Fact 11) and, once a probe owner
 * exists, H2's probes (`h2-probes.ts`). Built in slice L, never run by it.
 *
 * It runs from the manual workflow `.github/workflows/platform-deploy.yml`, from `main`: a
 * `plan` job runs `--public` with the Cloudflare token alone and prints the plan to the job
 * summary; the `apply` job then runs `--yes` in the main-only `production` environment,
 * with that environment's secrets, never a host's (Direction 8). The Cloudflare account is
 * the zone's (`zoneOf`).
 *
 *   node --experimental-strip-types platform/deploy/deploy.ts               # the plan
 *   node --experimental-strip-types platform/deploy/deploy.ts --public      # the plan job's
 *   node --experimental-strip-types platform/deploy/deploy.ts --yes         # the dark deploy
 *   node --experimental-strip-types platform/deploy/deploy.ts --yes --probe   # the sign-in probe
 *   node --experimental-strip-types platform/deploy/deploy.ts --yes --routes  # + the routes
 *   node --experimental-strip-types platform/deploy/deploy.ts --h2          # H2's probes only
 *   node --experimental-strip-types platform/deploy/deploy.ts --pair        # the pair check
 *   node --experimental-strip-types platform/deploy/deploy.ts --yes --migrate owner
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-new <file>
 *   node --experimental-strip-types platform/deploy/deploy.ts keyring-add <file>
 *   (for each keyring: `GATEWAY_KEYRING`, the gateway's, and `FILES_KEYRING`, the files
 *   Worker's data keys' ring; each its own secret)
 *
 * A Worker's first deploy is `wrangler deploy`, with no route and no workers.dev; later
 * ones upload a version and then deploy it (Cloudflare's staged path), so a deploy never
 * touches a route. A Worker with a pending Durable Object migration is deployed whole only
 * when `--migrate` names it.
 *
 * `--routes` hands the doors' routes over after the doors are deployed, one route at a time
 * through Cloudflare's routes API (`handover`), so no path is ever served by nothing: a
 * pattern another Worker holds is moved to the doors in place (`PUT`, one atomic change), a
 * new one is added (`POST`), and only then is another Worker's narrower pattern, which
 * Cloudflare would prefer to the doors' (the most specific pattern wins, its routes page),
 * deleted, so its paths pass from that Worker straight to the doors. Then `wrangler
 * triggers deploy` adds the egress custom domain.
 *
 * `--probe` (`--yes --probe`, between `dark` and `--routes`) hands the doors only the
 * sign-in's two paths, `PROBE_ROUTES`, which no Worker serves before it, by the same
 * handover; it deploys nothing and changes no gate. It runs only after a dark run: the
 * doors deployed, the control record admitting nobody, and WorkOS listing `CALLBACK`. It
 * then prints the one first-party sign-in link, whose end settles everything sign-in
 * needs at the cutover in the doors' own code: the doors' client and secret at the
 * issuer, the callback, and the ID token's checks, `auth_time` among them (`PROBE_SIGNS`).
 * `--routes` later finds both paths already the doors' and hands over the rest.
 *
 * Every lookup is checked: a failed listing or read stops the kit loudly, never reads as
 * "absent". So:
 * - a namespace, bucket or queue is created only when its listing answered and lacks it;
 * - a secret is set only on a Worker whose secret listing answered and lacks it, or that
 *   the API says does not exist yet; the kit never makes a secret's value. The keyrings
 *   and `ROUTING_MAC_KEY` come from the secret store like the rest (`keyring-new` makes the
 *   first keyring; scripts/platform_setup.py stores one); a held one is never replaced, unless
 *   `--rotate` names it, and a keyring is rotated only by adding a key (the new keyring
 *   must hold two or more keys, the active one among them). Cloudflare never shows a held
 *   secret, so the kit cannot check that a rotated keyring keeps the deployed key;
 * - the control record is written only when the API answered that it is absent;
 * - the WorkOS redirect URI is registered only when WorkOS's listing lacks it (GET, then
 *   POST; never a delete);
 * - E2B's lifecycle webhook is listed and checked before any change, registered, disabled,
 *   only when E2B's listing lacks it, its secret changed only with the doors' copy, and
 *   enabled only by `--routes` after the handover; the run that registers it sets the
 *   doors' copy too (`hookPlan`, `secretPlan`, `lifecycleHook`);
 * - the owner Worker is deployed only when the migration lock (`owner/migrations.json`)
 *   marks the first deploy and pins every migration the code ships, in order
 *   (`lockRefusal`): pin with `PIN_MIGRATIONS=<commit>` (`owner/guards/migrations.test.ts`)
 *   and commit the lock with the code first, so a deployed migration is never edited.
 *
 * Before anything is changed it checks, live: the owner issuer's discovery names
 * `OWNER_ISSUER` exactly, takes `client_secret_post` at its token endpoint and names https
 * endpoints; an absent field is unknown, and unknown stops the kit. Whether the issuer's
 * ID token carries `auth_time` no discovery shows: the sign-in probe settles it; the sandbox's
 * `E2B_TEMPLATE` exists at E2B with a ready build; and the pair it deploys, that template
 * and the runner the sandbox Worker ships (its SHA-256, built as the Worker's build makes
 * it), passed its standing checks in this E2B team: E2B holds the pair's tag
 * (`pairTag`), which the workflow's `pair` job makes before the apply job starts (`--pair`:
 * `sandbox/template/build.py --check-pair`, a few throwaway sandboxes, the founder's
 * standing approval of 2026-11-04, given only `PATH`, `HOME` and `E2B_API_KEY`); without it
 * the kit stops. `--public` makes every Cloudflare read and the discovery
 * check, and leaves to the apply job the checks that need the production secrets (E2B,
 * WorkOS, each secret's value), saying so in its plan.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PLATFORM = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = resolve(PLATFORM, "..");
const require = createRequire(join(PLATFORM, "package.json"));

type Json = Record<string, unknown>;
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

/** The Workers, bound-first: each one's bindings are deployed before it. */
export const WORKERS: readonly string[] = Object.freeze([
  "files", "gateway", "hooks", "sandbox", "owner", "doors"]);
/** The KV namespaces, by binding, and their titles in the account. */
export const NAMESPACES: Readonly<Record<string, string>> = Object.freeze({
  OAUTH_KV: "tinyassets-oauth", DIRECTORY: "tinyassets-directory", CONTROL: "tinyassets-control",
});
export const BUCKET = "tinyassets-files";
/** Each Worker's secrets. */
export const SECRETS: Readonly<Record<string, readonly string[]>> = Object.freeze({
  doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET",
          "INBOUND_SLACK_SIGNING_SECRET"],
  owner: ["ROUTING_MAC_KEY"],
  gateway: ["GATEWAY_KEYRING"],
  sandbox: ["E2B_API_KEY"],
  files: ["FILES_KEYRING"],
});
/** The keyrings: each rotated only by adding a key. */
export const KEYRINGS: readonly string[] = Object.freeze(["GATEWAY_KEYRING", "FILES_KEYRING"]);
/** The one secret two Workers hold: set on both or neither. */
const SHARED = "ROUTING_MAC_KEY";
/** Set when given; without it that door refuses loudly. */
const OPTIONAL: readonly string[] = Object.freeze(["INBOUND_SLACK_SIGNING_SECRET"]);
/** The doors' public routes (`--routes`, the cutover's), on the tinyassets.io zone. */
export const ROUTES: readonly string[] = Object.freeze([
  "tinyassets.io/mcp*", "tinyassets.io/api/*", "tinyassets.io/hooks/inbound/*",
  "tinyassets.io/hooks/e2b/*",
  "tinyassets.io/app/api/*", "tinyassets.io/authorize*", "tinyassets.io/oauth/*",
  "tinyassets.io/.well-known/oauth-*",
]);
export const ZONE = "tinyassets.io";
/** The sandbox's egress host, a custom domain of the doors Worker (`--routes`). */
export const EGRESS_DOMAIN = "egress.tinyassets.io";
export const CALLBACK = "https://tinyassets.io/oauth/upstream/callback";
/**
 * The sign-in probe's routes (`--probe`): the sign-in's own two paths, which no Worker
 * serves before the cutover, so handing them to the doors early serves no user.
 */
export const PROBE_ROUTES: readonly string[] = Object.freeze([
  "tinyassets.io/authorize*", "tinyassets.io/oauth/*"]);
/** The doors' own app's sign-in (`doors/src/config.ts`): its client, redirect and resource. */
export const FIRST_PARTY = Object.freeze({
  client: `https://${ZONE}/oauth/clients/web.json`, redirect: `https://${ZONE}/app/signed-in`,
  resource: `https://${ZONE}/app`,
});
/** The doors Worker, which the routes are handed to. */
export const DOORS = "tinyassets-doors";
/** What the plan job holds: the Cloudflare token (not a production secret) and the vars. */
export const PUBLIC_INPUTS: readonly string[] = Object.freeze([
  "CLOUDFLARE_API_TOKEN", "WORKOS_CLIENT_ID", "OWNER_ISSUER"]);
/** What only the apply job holds: the production environment's secrets. */
export const SECRET_INPUTS: readonly string[] = Object.freeze([
  "WORKOS_API_KEY", "E2B_API_KEY", "ROUTING_MAC_KEY", "GATEWAY_KEYRING", "FILES_KEYRING",
  "E2B_WEBHOOK_SECRET"]);
const WORKOS_API = "https://api.workos.com";
const CF_API = "https://api.cloudflare.com/client/v4";
/** E2B's platform API (`sandbox/src/e2b.ts`'s), its OpenAPI spec read 2026-11-03. */
const E2B_API = "https://api.e2b.app";
/**
 * E2B's sandbox lifecycle webhook, the doors' one E2B hook (`doors/src/e2b-hooks.ts`), its
 * API read in e2b-dev/infra's `spec/openapi.yml` (2026-11-04): `GET` and `POST
 * /events/webhooks`, `PATCH /events/webhooks/{id}`.
 */
export const E2B_HOOK = Object.freeze({
  name: "tinyassets-lifecycle", url: "https://tinyassets.io/hooks/e2b/lifecycle",
  events: Object.freeze(["sandbox.lifecycle.paused", "sandbox.lifecycle.killed"]),
});
/** The control record's key in the `CONTROL` namespace (`doors/src/directory.ts`). */
const CONTROL_KEY = "c:control";
/** The control record a first deploy writes when there is none: every outside door closed. */
export const DARK_CONTROL = Object.freeze({
  outside: "off", ordinary: "canary", canary: [], inbound: false,
  tiers: { free: { runs: 5, storage_bytes: 25_000_000_000 },
           paid: { runs: 10, storage_bytes: 250_000_000_000 } },
});

function flag(name: string): boolean {
  return process.argv.includes(name);
}

function option(name: string): string | undefined {
  const at = process.argv.indexOf(name);
  return at < 0 ? undefined : process.argv[at + 1];
}

function input(name: string): string | undefined {
  const value = process.env[name];
  return value === undefined || value === "" ? undefined : value;
}

/** A Worker's checked-in config, its comments stripped. */
export function configOf(worker: string): Json {
  const ts = require("typescript") as typeof import("typescript");
  const path = join(PLATFORM, worker, "wrangler.jsonc");
  const parsed = ts.parseConfigFileTextToJson(path, readFileSync(path, "utf8"));
  if (parsed.error) {
    throw new Error(`${path} does not parse`);
  }
  return parsed.config as Json;
}

function wrangler(args: string[], stdin?: string): string {
  const bin = join(dirname(require.resolve("wrangler/package.json")), "bin", "wrangler.js");
  const result = spawnSync(process.execPath, [bin, ...args], {
    cwd: PLATFORM, input: stdin, encoding: "utf8",
    env: { ...process.env, WRANGLER_SEND_METRICS: "false" },
  });
  if (result.status !== 0) {
    // Its stderr alone: wrangler never echoes a secret's value.
    throw new Error(`wrangler ${args.slice(0, 3).join(" ")} failed (exit ${result.status}): ` +
                    `${result.stderr.trim()}`);
  }
  return result.stdout;
}

/**
 * Why the owner Worker may not deploy under `lock` (the migration lock as
 * `owner/migrations.json` holds it) with the code's migrations' `digests`, or null: the
 * first deploy is marked, and the lock pins exactly the code's migrations, element for
 * element (tasks "Append-only migrations from the first deploy").
 */
export function lockRefusal(lock: unknown, digests: readonly string[]): string | null {
  const pin = "pin them with PIN_MIGRATIONS=<commit> npx vitest run --project guards " +
    "guards/migrations.test.ts in platform/owner, and commit the lock with the code";
  const held = lock as { first_deploy?: unknown; deployed?: unknown } | null;
  if (typeof held !== "object" || held === null || !Array.isArray(held.deployed)
      || !("first_deploy" in held)) {
    return "owner/migrations.json is out of shape";
  }
  if (held.first_deploy === null) {
    return `the migration lock marks no first deploy: ${pin}`;
  }
  if (held.deployed.length !== digests.length
      || held.deployed.some((digest, index) => digest !== digests[index])) {
    return `the migration lock pins ${held.deployed.length} migrations and the code ships ` +
      `${digests.length}, or one differs: ${pin}`;
  }
  return null;
}

/** The code's migrations' digests, as the owner's guards project evaluates its schema. */
export function codeDigests(): string[] {
  const dir = mkdtempSync(join(tmpdir(), "ta-migrations-"));
  const out = join(dir, "digests.json");
  try {
    const vitest = join(dirname(require.resolve("vitest/package.json")), "vitest.mjs");
    const result = spawnSync(process.execPath, [vitest, "run", "--project", "guards",
                                                "guards/migrations.test.ts"], {
      cwd: join(PLATFORM, "owner"), encoding: "utf8",
      env: { ...process.env, MIGRATION_DIGESTS: out, PIN_MIGRATIONS: "" },
    });
    if (result.status !== 0) {
      throw new Error(`the migration lock's guard failed (exit ${result.status}): ` +
                      `${result.stdout.slice(-2000)}`);
    }
    return JSON.parse(readFileSync(out, "utf8")) as string[];
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Cloudflare's API answer: its status and its parsed body, or a throw on no answer. */
export interface CfAnswer {
  readonly status: number;
  readonly body: { success?: boolean; result?: unknown; errors?: { code?: number }[] };
}

export type CfApi = (path: string, init?: RequestInit) => Promise<CfAnswer>;

/** The Cloudflare API with the account's token. */
export function cfApi(token: string, fetcher: Fetch = fetch): CfApi {
  return async (path, init = {}) => {
    const response = await fetcher(`${CF_API}${path}`, {
      ...init, headers: { Authorization: `Bearer ${token}`, ...(init.headers ?? {}) } });
    const text = await response.text();
    let body: CfAnswer["body"];
    try {
      body = JSON.parse(text) as CfAnswer["body"];
    } catch {
      body = {};
    }
    return { status: response.status, body };
  };
}

function codes(answer: CfAnswer): number[] {
  return (answer.body.errors ?? []).map((error) => error.code ?? 0);
}

/**
 * A Worker's secrets' names, or null when the API answers that the Worker does not exist
 * (404 with Cloudflare's 10007, "workers.api.error.script_not_found"); any other failure
 * throws: a failed lookup is never "no secrets".
 */
export async function secretsOf(api: CfApi, account: string, script: string):
    Promise<string[] | null> {
  const answer = await api(`/accounts/${account}/workers/scripts/${script}/secrets`);
  if (answer.status === 404 && codes(answer).includes(10007)) {
    return null;
  }
  if (answer.status !== 200 || answer.body.success !== true || !Array.isArray(answer.body.result)) {
    throw new Error(`the secret listing of ${script} failed (${answer.status}, ${
      JSON.stringify(codes(answer))})`);
  }
  return (answer.body.result as { name?: unknown }[]).map((secret) => String(secret.name));
}

/**
 * A KV key's value, as JSON (`{}` when it is not JSON), or undefined only when the API
 * answers that it is absent (404 with Cloudflare's 10009, "key not found"); any other
 * answer throws.
 */
export async function kvValue(api: CfApi, account: string, namespace: string, key: string):
    Promise<unknown> {
  const answer = await api(`/accounts/${account}/storage/kv/namespaces/${namespace}/values/${
    encodeURIComponent(key)}`);
  if (answer.status === 200) {
    return answer.body;
  }
  if (answer.status === 404 && codes(answer).includes(10009)) {
    return undefined;
  }
  throw new Error(`reading ${key} failed (${answer.status}, ${JSON.stringify(codes(answer))})`);
}

/** The account's KV namespaces by title, every page read; a failure throws. */
export async function namespacesOf(api: CfApi, account: string): Promise<Map<string, string>> {
  const found = new Map<string, string>();
  for (let page = 1; ; page += 1) {
    const answer = await api(`/accounts/${account}/storage/kv/namespaces?per_page=100&page=${page}`);
    if (answer.status !== 200 || answer.body.success !== true
        || !Array.isArray(answer.body.result)) {
      throw new Error(`listing the KV namespaces failed (${answer.status})`);
    }
    const rows = answer.body.result as { id: string; title: string }[];
    for (const row of rows) {
      found.set(row.title, row.id);
    }
    if (rows.length < 100) {
      return found;
    }
  }
}

/** A Worker route on the zone, as Cloudflare's routes API lists it. */
export interface Route {
  readonly id: string;
  readonly pattern: string;
  readonly script?: string;
}

/** One change of the handover, in order. */
export type RouteStep =
  | { readonly method: "PUT"; readonly id: string; readonly pattern: string;
      readonly from: string }
  | { readonly method: "POST"; readonly pattern: string }
  | { readonly method: "DELETE"; readonly id: string; readonly pattern: string;
      readonly from: string };

function base(pattern: string): string {
  return pattern.endsWith("*") ? pattern.slice(0, -1) : pattern;
}

/**
 * Whether `narrow` is a more specific pattern than `wide`, matching a part of its URLs:
 * Cloudflare sends those URLs to `narrow` (the most specific pattern wins).
 */
export function narrower(narrow: string, wide: string): boolean {
  return narrow !== wide && wide.endsWith("*") && narrow.startsWith(base(wide));
}

/**
 * The zone, by its name: its id, and the id of the account that holds it. That account is
 * the one the doors' routes need their Workers in, so the kit reads it here and holds no
 * account id of its own. A failed read, or any answer but one zone with its account, throws.
 */
export async function zoneOf(api: CfApi): Promise<{ id: string; account: string }> {
  const zones = await api(`/zones?name=${ZONE}`);
  const rows = Array.isArray(zones.body.result)
    ? zones.body.result as { id?: unknown; account?: { id?: unknown } }[] : [];
  const id = rows[0]?.id;
  const account = rows[0]?.account?.id;
  if (zones.status !== 200 || zones.body.success !== true || rows.length !== 1
      || typeof id !== "string" || typeof account !== "string"
      || !/^[0-9a-f]{32}$/.test(account)) {
    throw new Error(`the zone ${ZONE} could not be read (${zones.status}, ${rows.length} found)`);
  }
  return { id, account };
}

/**
 * Masks `value` in a GitHub Actions job's log (`::add-mask::`), as the probe workflows mask
 * the account id: not a credential, but once a secret, and a wrangler error the kit quotes
 * names `/accounts/<id>/...` in a public log. Outside Actions it prints nothing.
 */
export function maskInLog(value: string, env: NodeJS.ProcessEnv = process.env,
                          log: (line: string) => void = console.log): void {
  if (env["GITHUB_ACTIONS"] !== undefined) {
    log(`::add-mask::${value}`);
  }
}

/** The zone's Worker routes; a failed read throws. */
export async function routesOf(api: CfApi, zone: string): Promise<Route[]> {
  const routes = await api(`/zones/${zone}/workers/routes`);
  if (routes.status !== 200 || routes.body.success !== true
      || !Array.isArray(routes.body.result)) {
    throw new Error(`the zone's routes could not be read (${routes.status})`);
  }
  return routes.body.result as Route[];
}

/**
 * The handover of `patterns` to `script`, in order: each pattern another route holds moved
 * in place (`PUT`), each missing one added (`POST`), and after each, every other route's
 * narrower pattern deleted, so its paths fall to `script` with no gap. A wider pattern of
 * another Worker is left: `script`'s narrower one already takes its own paths from it.
 */
export function handover(routes: readonly Route[], script: string,
                         patterns: readonly string[]): RouteStep[] {
  const steps: RouteStep[] = [];
  const deleted = new Set<string>();
  for (const pattern of patterns) {
    const held = routes.find((route) => route.pattern === pattern);
    if (held === undefined) {
      steps.push({ method: "POST", pattern });
    } else if (held.script !== script) {
      steps.push({ method: "PUT", id: held.id, pattern, from: held.script ?? "no Worker" });
    }
    for (const route of routes) {
      if (route.script !== script && !patterns.includes(route.pattern) && !deleted.has(route.id)
          && narrower(route.pattern, pattern)) {
        deleted.add(route.id);
        steps.push({ method: "DELETE", id: route.id, pattern: route.pattern,
                     from: route.script ?? "no Worker" });
      }
    }
  }
  return steps;
}

/** A step, as the plan says it. */
export function describeStep(step: RouteStep, script: string): string {
  return step.method === "PUT" ? `move the route ${step.pattern} from ${step.from} to ${script}`
    : step.method === "POST" ? `add the route ${step.pattern} for ${script}`
    : `delete ${step.from}'s narrower route ${step.pattern}, its paths now ${script}'s`;
}

/** Runs the handover's steps in order, each answer checked; the first failure stops it. */
export async function runHandover(api: CfApi, zone: string, script: string,
                                  steps: readonly RouteStep[]): Promise<void> {
  for (const step of steps) {
    const path = `/zones/${zone}/workers/routes${step.method === "POST" ? "" : `/${step.id}`}`;
    const answer = await api(path, step.method === "DELETE" ? { method: "DELETE" } : {
      method: step.method, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pattern: step.pattern, script }) });
    if (answer.status !== 200 || answer.body.success !== true) {
      throw new Error(`${describeStep(step, script)} failed (${answer.status}, ${
        JSON.stringify(codes(answer))}); the steps before it are done, the rest are not`);
    }
  }
}

/** The doors' upstream: the issuer, and its `UPSTREAM` record. */
export interface UpstreamValues {
  readonly issuer: string;
  readonly record: Json;
}

/**
 * The deployed config of `worker`: its checked-in config with the namespace ids it names
 * filled, the doors' upstream values set and, with `routes`, the doors' routes; nothing
 * else changes, so what the guards hold is what deploys. It is written outside the Worker's
 * folder, so its paths (`main`, and the doors' app files' `assets.directory`) are made
 * absolute: wrangler reads each relative to the config file.
 */
export function deployedConfig(worker: string, ids: Readonly<Record<string, string>>,
                               upstream: UpstreamValues, routes: boolean): Json {
  const config = structuredClone(configOf(worker));
  config["main"] = join(PLATFORM, worker, String(config["main"]));
  const assets = config["assets"] as Json | undefined;
  if (assets !== undefined) {
    assets["directory"] = join(PLATFORM, worker, String(assets["directory"]));
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

/**
 * The owner issuer's discovery, held to `issuer` exactly, and the doors' `UPSTREAM` from it,
 * which the doors' own parser accepts. The token endpoint must declare
 * `client_secret_post`, the way the doors exchange a code, and every endpoint is https; an
 * absent field is unknown, and unknown stops the kit. What the ID token carries is the
 * sign-in probe's to show (`--probe`).
 */
export async function upstreamFrom(issuer: string | undefined, clientId: string | undefined,
                                   fetcher: Fetch = fetch): Promise<UpstreamValues> {
  if (typeof issuer !== "string" || !/^https:\/\/\S+$/.test(issuer)) {
    throw new Error("OWNER_ISSUER is the owner issuer's https URL");
  }
  if (typeof clientId !== "string" || clientId === "") {
    throw new Error("WORKOS_CLIENT_ID is not set");
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
  const methods = discovery["token_endpoint_auth_methods_supported"];
  if (!Array.isArray(methods)) {
    throw new Error("the issuer's discovery declares no token endpoint auth methods: unknown");
  }
  if (!methods.includes("client_secret_post")) {
    throw new Error("the issuer takes no client_secret_post at its token endpoint");
  }
  const endpoint = (name: string): string => {
    const value = discovery[name];
    if (typeof value !== "string" || !/^https:\/\/\S+$/.test(value)) {
      throw new Error(`the issuer's discovery names no https ${name}: unknown`);
    }
    return value;
  };
  // The doors' parser (`upstreamConfig`) accepts this record: the kit's guard holds it so.
  const record = { client_id: clientId, authorization_endpoint: endpoint("authorization_endpoint"),
                   token_endpoint: endpoint("token_endpoint"), jwks_uri: endpoint("jwks_uri"),
                   user_delete: `${WORKOS_API}/user_management/users/{sub}` };
  return { issuer, record };
}

/**
 * The runner the sandbox Worker ships, built as its build makes it (`build.command`,
 * `sandbox/template/bundle.mjs`, the same bytes every build): its SHA-256.
 */
export function shippedRunner(): string {
  const out = mkdtempSync(join(tmpdir(), "ta-runner-"));
  try {
    execFileSync(process.execPath, [join(PLATFORM, "sandbox", "template", "bundle.mjs"), out],
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
 * The pair check (`--pair`, the workflow's `pair` job, before the apply job): the pinned
 * template and the runner this checkout ships, unless E2B already holds their pair's tag;
 * `build.py --check-pair` runs the standing checks and tags the pair, given only what it
 * needs (never the deploy's other secrets). A failure, or no tag after it, throws.
 */
export async function pairCheck(spawn: typeof spawnSync = spawnSync, fetcher: Fetch = fetch):
    Promise<void> {
  const key = input("E2B_API_KEY");
  if (key === undefined) {
    throw new Error("E2B_API_KEY is not set (the production environment's secrets)");
  }
  const template = String((configOf("sandbox")["vars"] as Json)["E2B_TEMPLATE"]);
  const pair = pairTag(template, shippedRunner());
  if (await templateReady(pair, key, fetcher)) {
    console.log(`the pair ${pair} passed its standing checks in this E2B team`);
    return;
  }
  const checked = spawn("python3", [join(PLATFORM, "sandbox", "template", "build.py"),
                                    "--check-pair"],
                        { stdio: "inherit", env: { PATH: process.env["PATH"] ?? "",
                                                   HOME: process.env["HOME"] ?? "",
                                                   E2B_API_KEY: key } });
  if (checked.status !== 0 || !await templateReady(pair, key, fetcher)) {
    throw new Error(`the pair ${pair} failed its standing checks: nothing is deployed`);
  }
  console.log(`the pair ${pair} passed its standing checks, run now`);
}

/** Whether `name:tag` has a ready build at E2B (its OpenAPI spec's alias and tag checks). */
export async function templateReady(template: string, key: string, fetcher: Fetch = fetch):
    Promise<boolean> {
  const at = template.lastIndexOf(":");
  if (at < 1) {
    throw new Error("E2B_TEMPLATE is name:tag");
  }
  const headers = { "X-API-Key": key, Accept: "application/json" };
  const alias = await fetcher(`${E2B_API}/templates/aliases/${
    encodeURIComponent(template.slice(0, at))}`, { headers });
  if (alias.status === 404) {
    return false;
  }
  if (alias.status !== 200) {
    throw new Error(`E2B's alias lookup answered ${alias.status}`);
  }
  const { templateID } = await alias.json() as { templateID?: string };
  const tag = await fetcher(`${E2B_API}/templates/${encodeURIComponent(String(templateID))}/` +
                            `tags/exists?tag=${encodeURIComponent(template.slice(at + 1))}`,
                            { headers });
  if (tag.status !== 200) {
    throw new Error(`E2B's tag lookup answered ${tag.status}`);
  }
  return (await tag.json() as { exists?: unknown }).exists === true;
}

/** The WorkOS redirect URI, registered when missing: GET, then POST; never a delete. */
export async function registerRedirect(key: string, apply: boolean, fetcher: Fetch = fetch,
                                       uri: string = CALLBACK):
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
  if (data.some((row) => (row as Json | null)?.["uri"] === uri)) {
    return "present";
  }
  if (!apply) {
    return "missing";
  }
  const made = await fetcher(`${WORKOS_API}/user_management/redirect_uris`, {
    method: "POST", headers, body: JSON.stringify({ uri }) });
  if (made.status !== 200 && made.status !== 201) {
    throw new Error(`WorkOS refused the redirect URI with ${made.status}`);
  }
  return "registered";
}

/**
 * Where a run touches E2B's lifecycle webhook, and whether it enables it: the hook is listed
 * and checked with the live checks, before any change, in every mode that holds the E2B key
 * (`missing` is that listing's answer); `dark` then registers or changes it once the doors
 * hold their secrets, never enabling it; `routes` does so only after the last route is
 * handed to the doors, enabling it, since an enabled hook sends the whole team's events to
 * whatever serves its path. A run that `creates` the hook sets the doors' E2B_WEBHOOK_SECRET
 * too (`secretPlan`), so the pair starts equal. The public plan never reaches E2B.
 */
export function hookPlan(mode: { open: boolean; routes: boolean; missing?: boolean }):
    { check: boolean; at: "none" | "secrets" | "routes"; enable: boolean; creates: boolean } {
  if (mode.open) {
    return { check: false, at: "none", enable: false, creates: false };
  }
  const creates = mode.missing === true;
  return mode.routes ? { check: true, at: "routes", enable: true, creates }
    : { check: true, at: "secrets", enable: false, creates };
}

/**
 * Whether this run sets E2B's copy of the webhook secret: only when it sets the doors' copy
 * (missing on the doors, named in `--rotate`, or the hook made this run), so the two never
 * drift apart.
 */
export function hookSetsSecret(setting: readonly { worker: string; name: string }[]): boolean {
  return setting.some((step) => step.worker === "doors" && step.name === "E2B_WEBHOOK_SECRET");
}

/**
 * E2B's lifecycle webhook as its listing holds it, matched by its URL, or null when it holds
 * none. A failed listing, or two hooks for the URL, throws.
 */
export async function heldHook(key: string, fetcher: Fetch = fetch): Promise<Json | null> {
  const listed = await fetcher(`${E2B_API}/events/webhooks`, {
    headers: { "X-API-Key": key, "Content-Type": "application/json" } });
  if (listed.status !== 200) {
    throw new Error(`E2B listed its webhooks with ${listed.status}`);
  }
  const hooks = await listed.json() as unknown;
  if (!Array.isArray(hooks)) {
    throw new Error("E2B's webhook listing holds no list");
  }
  const ours = hooks.filter((hook) => (hook as Json | null)?.["url"] === E2B_HOOK.url) as Json[];
  if (ours.length > 1) {
    throw new Error(`E2B holds ${ours.length} webhooks for ${E2B_HOOK.url}: keep one`);
  }
  return ours[0] ?? null;
}

/**
 * E2B's lifecycle webhook (`heldHook`). Missing, it is registered with `secret`, disabled
 * unless `enable`; the run that does so sets the doors' copy of the secret too (`hookPlan`,
 * `secretPlan`). Held, it is changed only where the run asks: its signature secret only with
 * `setSecret`, when this run also sets the doors' E2B_WEBHOOK_SECRET (E2B never shows a
 * hook's secret, so the two change together or not at all); its events when they differ;
 * and it is enabled with `enable` (`--routes`). It is never disabled or deleted. Without
 * `apply` it only lists and checks. A failed listing or write throws.
 */
export async function lifecycleHook(key: string, secret: string, apply: boolean,
                                    enable: boolean, setSecret: boolean,
                                    fetcher: Fetch = fetch): Promise<string> {
  const headers = { "X-API-Key": key, "Content-Type": "application/json" };
  const hook = await heldHook(key, fetcher);
  if (hook === null) {
    const state = enable ? "enabled" : "disabled";
    if (!apply) {
      return `missing: registered, ${state}`;
    }
    const made = await fetcher(`${E2B_API}/events/webhooks`, {
      method: "POST", headers, body: JSON.stringify({
        name: E2B_HOOK.name, url: E2B_HOOK.url, events: [...E2B_HOOK.events], enabled: enable,
        signatureSecret: secret }) });
    if (made.status !== 200 && made.status !== 201) {
      throw new Error(`E2B refused the webhook with ${made.status}`);
    }
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
  const changed = await fetcher(`${E2B_API}/events/webhooks/${
    encodeURIComponent(String(hook["id"]))}`, {
    method: "PATCH", headers, body: JSON.stringify(change) });
  if (changed.status !== 200) {
    throw new Error(`E2B refused the webhook's change with ${changed.status}`);
  }
  return `present, ${what}, ${state}`;
}

/** A keyring as the gateway and the files Worker hold it: `{active, keys}`, the active key among the keys. */
export function keyring(text: string | undefined, name = "GATEWAY_KEYRING"):
    { active: string; keys: Record<string, string> } {
  let value: unknown;
  try {
    value = JSON.parse(text ?? "");
  } catch {
    throw new Error(`${name} is {active, keys} as JSON`);
  }
  const ring = value as { active?: unknown; keys?: unknown };
  if (typeof ring.active !== "string" || typeof ring.keys !== "object" || ring.keys === null
      || !Object.hasOwn(ring.keys, ring.active)) {
    throw new Error(`${name} is {active, keys}, its active key among its keys`);
  }
  return ring as { active: string; keys: Record<string, string> };
}

/**
 * What to set: each Worker's missing secrets, and those `rotate` names (the shared routing
 * key on both or neither; a keyring only as an addition). `held` is each Worker's listing,
 * null for a Worker the API says does not exist.
 */
export function secretPlan(held: Readonly<Record<string, readonly string[] | null>>,
                           rotate: ReadonlySet<string>,
                           values: ((name: string) => string | undefined) | null,
                           hookCreated = false):
    { worker: string; name: string }[] {
  const has = (worker: string, name: string) => (held[worker] ?? []).includes(name);
  if (has("doors", SHARED) !== has("owner", SHARED) && !rotate.has(SHARED)) {
    throw new Error(`only one of the doors and the owner holds ${SHARED}: pass --rotate ` +
                    `${SHARED} to set both again`);
  }
  // `values` null: the plan job, which holds no secret; the apply job checks the ring.
  for (const name of KEYRINGS) {
    if (rotate.has(name) && values !== null) {
      const ring = keyring(values(name), name);
      if (Object.keys(ring.keys).length < 2) {
        throw new Error(`a keyring is rotated by adding a key: the new ${name} holds ` +
                        "the old keys and a new active one (keyring-add)");
      }
    }
  }
  const plan: { worker: string; name: string }[] = [];
  for (const [worker, names] of Object.entries(SECRETS)) {
    for (const name of names) {
      // A webhook made this run takes production's secret: the doors take it too.
      const withHook = hookCreated && worker === "doors" && name === "E2B_WEBHOOK_SECRET";
      if (!has(worker, name) || rotate.has(name) || withHook) {
        plan.push({ worker, name });
      }
    }
  }
  return plan;
}

/** Whether a wrangler listing names `name` as a whole word (never a longer name holding it). */
export function listed(output: string, name: string): boolean {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(^|[\\s│|:])${escaped}($|[\\s│|])`, "m").test(output);
}

function ensure(kind: "bucket" | "queue", name: string, apply: boolean, plan: string[]): void {
  // `wrangler` throws on a failed listing: a failure is never "absent".
  const list = kind === "bucket" ? ["r2", "bucket", "list"] : ["queues", "list"];
  if (listed(wrangler(list), name)) {
    return;
  }
  plan.push(`create the ${kind === "bucket" ? "R2 bucket" : "queue"} ${name}`);
  if (apply) {
    wrangler(kind === "bucket" ? ["r2", "bucket", "create", name] : ["queues", "create", name]);
  }
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
 * Deploys one Worker. A first deploy (the API says the Worker does not exist) is
 * `wrangler deploy`, with no route; later ones are Cloudflare's staged path, `wrangler
 * versions upload` then `versions deploy` at 100%, which leaves the Worker's routes as they
 * are. Wrangler 4.154 refuses a versions upload with a pending Durable Object migration;
 * `--migrate <worker>` names the Workers to deploy whole for that (never the doors, which
 * hold no migration and do hold the routes). The config deployed names no route; the
 * routes are the handover's.
 */
function deployWorker(worker: string, config: string, exists: boolean,
                      migrate: ReadonlySet<string>): void {
  if (!exists || migrate.has(worker)) {
    wrangler(["deploy", "--config", config]);
  } else {
    const id = versionOf(wrangler(["versions", "upload", "--config", config]));
    wrangler(["versions", "deploy", `${id}@100`, "--yes", "--config", config]);
  }
}

/** `keyring-new <file>`: a first keyring, written for the founder to store; never printed. */
function keyringNew(file: string): void {
  writeFileSync(file, JSON.stringify({ active: "k1", keys: {
    k1: randomBytes(32).toString("base64") } }), { mode: 0o600, flag: "wx" });
  console.log(`wrote a new keyring to ${file}; store it as ${KEYRINGS.join(" or ")}, ` +
              "one ring each");
}

/** `keyring-add <file>`: the keyring in `file` with one more key, made active; the old kept. */
function keyringAdd(file: string): void {
  const ring = keyring(readFileSync(file, "utf8"));
  const next = `k${Object.keys(ring.keys).length + 1}`;
  writeFileSync(file, JSON.stringify({ active: next, keys: {
    ...ring.keys, [next]: randomBytes(32).toString("base64") } }), { mode: 0o600 });
  console.log(`added ${next} to ${file}, active; store it as the keyring it was, then run ` +
              "with --rotate naming that keyring");
}

/**
 * The probe's handover (`--probe`): `PROBE_ROUTES` alone, each added for the doors, or left
 * when the doors hold it already. A path another Worker (or no Worker) serves, by the same,
 * a narrower or a wider pattern, stops it: the probe takes only paths nothing serves.
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
  const challenge = createHash("sha256").update(randomBytes(32).toString("base64url"))
    .digest("base64url");
  const query = new URLSearchParams({
    response_type: "code", client_id: FIRST_PARTY.client, redirect_uri: FIRST_PARTY.redirect,
    state: randomBytes(16).toString("base64url"), code_challenge: challenge,
    code_challenge_method: "S256", resource: FIRST_PARTY.resource });
  return `https://${ZONE}/authorize?${query}`;
}

/** What the probe's sign-in shows, read from where the browser ends. */
export const PROBE_SIGNS = [
  `Pass: the browser's address begins ${FIRST_PARTY.redirect}?error=temporarily_unavailable ` +
  "(more follows it; the page itself is the old app's and reads as a sign-in error, " +
  "which is expected): the closed gate refused the sign-in after every " +
  "check passed, and no owner was made. The doors' client and API key were taken at the " +
  "issuer's token endpoint, the callback was its registered redirect, and the ID token's " +
  "signature, iss, aud, nonce and auth_time held.",
  `Fail, at https://${ZONE}/authorize: a 400 page, so the doors could not read their own ` +
  "app's client document.",
  "Fail, at the issuer: an error page before or after the sign-in, so it refused the doors' " +
  "client or the callback.",
  `Fail, at ${CALLBACK}: a 502 page naming the check that failed (the token endpoint's ` +
  "status and error code, auth_time, iss, the nonce...), the same line in the doors' log.",
  `Fail, at ${FIRST_PARTY.redirect}?error=access_denied ("sign in again"): auth_time is more ` +
  "than 600 s old or ahead of the clock.",
].join("\n");

/**
 * Fact 11: after a deploy or a route change, the handles canary (it reads
 * TINYASSETS_WIKI_CANARY_TOKEN).
 */
function handlesCanary(): void {
  const canary = spawnSync("python3", [join(REPO, "scripts", "mcp_public_canary.py"),
                                       "--assert-handles"], { stdio: "inherit", cwd: REPO });
  if (canary.status !== 0) {
    throw new Error(`the handles canary failed (exit ${canary.status})`);
  }
}

/**
 * The sign-in probe (`--probe`): after a dark run (the doors deployed, the control record
 * admitting nobody, WorkOS listing `CALLBACK`; each a live read, so the kit keeps no state of
 * its own), the sign-in's two paths handed to the doors (`probeSteps`); nothing deployed, no
 * gate changed. Then the handles canary, and the link to sign in by with its signs. Without
 * `apply` it checks and plans only; the plan job (`open`) leaves the WorkOS listing, which
 * needs the production key, to the apply job.
 */
async function probe(apply: boolean, open: boolean): Promise<void> {
  for (const name of ["CLOUDFLARE_API_TOKEN", ...(open ? [] : ["WORKOS_API_KEY"])]) {
    if (input(name) === undefined) {
      throw new Error(`${name} is not set`);
    }
  }
  const api = cfApi(input("CLOUDFLARE_API_TOKEN")!);
  const { id: zone, account } = await zoneOf(api);
  maskInLog(account);
  const plan: string[] = [];
  if (await secretsOf(api, account, DOORS) === null) {
    throw new Error(`${DOORS} is not deployed: the probe runs after a dark run`);
  }
  plan.push(`${DOORS} is deployed`);
  const control = (await namespacesOf(api, account)).get(NAMESPACES["CONTROL"]!);
  if (control === undefined
      || !admitsNobody(await kvValue(api, account, control, CONTROL_KEY))) {
    throw new Error("the control record is missing or admits someone: the probe runs only " +
                    "with every gate closed, as a dark run writes it");
  }
  plan.push("the control record admits no owner and no outside client; no gate is changed");
  if (open) {
    plan.push(`WorkOS lists ${CALLBACK}, as a dark run registers it: checked by the apply ` +
              "job before any change");
  } else if (await registerRedirect(input("WORKOS_API_KEY")!, false) !== "present") {
    throw new Error(`WorkOS lists no ${CALLBACK}: run dark first, which registers it`);
  } else {
    plan.push(`WorkOS lists ${CALLBACK}, as a dark run registers it`);
  }
  const steps = probeSteps(await routesOf(api, zone));
  plan.push(...(steps.length === 0 ? [`${DOORS} holds ${PROBE_ROUTES.join(" and ")} already`]
    : steps.map((step) => describeStep(step, DOORS))));
  if (apply) {
    await runHandover(api, zone, DOORS, steps);
  }
  report(plan, apply);
  if (!apply) {
    console.log("\nNothing was changed. The apply job prints the sign-in link.");
    return;
  }
  handlesCanary();
  console.log(`\nSign in once, in a browser, at:\n${probeLink()}\n\n${PROBE_SIGNS}`);
}

async function main(): Promise<void> {
  if (process.argv[2] === "keyring-new" || process.argv[2] === "keyring-add") {
    const file = process.argv[3];
    if (file === undefined) {
      throw new Error(`${process.argv[2]} <file>`);
    }
    (process.argv[2] === "keyring-new" ? keyringNew : keyringAdd)(file);
    return;
  }
  if (flag("--pair")) {
    await pairCheck();
    return;
  }
  if (flag("--h2")) {
    // H2's probes alone: they change nothing, and need a routed door and a probe owner.
    if (flag("--public")) {
      report(["run H2's probes against the routed doors; nothing is changed"], false);
      return;
    }
    execFileSync(process.execPath, ["--experimental-strip-types",
                                    join(PLATFORM, "deploy", "h2-probes.ts")],
                 { stdio: "inherit", env: process.env });
    return;
  }
  const apply = flag("--yes");
  const open = flag("--public");
  if (apply && open) {
    throw new Error("--public is the plan job's: it never changes anything");
  }
  const routes = flag("--routes");
  const rotate = new Set((option("--rotate") ?? "").split(",").filter(Boolean));
  const migrate = new Set((option("--migrate") ?? "").split(",").filter(Boolean));
  if (migrate.has("doors")) {
    throw new Error("the doors hold no Durable Object migration: --migrate never names them");
  }
  if (flag("--probe")) {
    if (routes || rotate.size > 0 || migrate.size > 0) {
      throw new Error("--probe deploys nothing: it takes no --routes, --rotate or --migrate");
    }
    await probe(apply, open);
    return;
  }
  const plan: string[] = [];
  for (const worker of WORKERS) {
    if (!existsSync(join(PLATFORM, worker, "wrangler.jsonc"))) {
      throw new Error(`platform/${worker} is missing: its slice has not landed`);
    }
  }
  for (const name of [...PUBLIC_INPUTS, ...(open ? [] : SECRET_INPUTS)]) {
    if (input(name) === undefined) {
      throw new Error(`${name} is not set (${SECRET_INPUTS.includes(name)
        ? "the production environment's secrets" : "the workflow's inputs"})`);
    }
  }
  const later = "checked by the apply job before any change";
  if (!open) {
    for (const name of KEYRINGS) {
      keyring(input(name), name);
    }
  }
  // The migration lock, before anything is changed: the owner Worker never deploys a
  // migration the lock does not pin.
  const refused = lockRefusal(JSON.parse(readFileSync(join(PLATFORM, "owner",
                                                           "migrations.json"), "utf8")),
                              codeDigests());
  if (refused !== null) {
    throw new Error(refused);
  }
  plan.push("the migration lock pins every migration the owner Worker ships");
  const api = cfApi(input("CLOUDFLARE_API_TOKEN")!);
  // The account is the zone's (`zoneOf`), and wrangler is handed that one.
  const { id: zone, account } = await zoneOf(api);
  maskInLog(account);
  process.env["CLOUDFLARE_ACCOUNT_ID"] = account;
  // The live checks, before anything is changed.
  const upstream = await upstreamFrom(input("OWNER_ISSUER"), input("WORKOS_CLIENT_ID"));
  plan.push(`set the doors' OWNER_ISSUER to ${upstream.issuer}, held to its discovery`);
  const template = String((configOf("sandbox")["vars"] as Json)["E2B_TEMPLATE"]);
  if (open) {
    plan.push(`E2B's ready build of ${template}: ${later}`);
  } else if (!await templateReady(template, input("E2B_API_KEY")!)) {
    throw new Error(`E2B has no ready build of ${template}: build it (sandbox/template/build.py)`);
  }
  // The pair this deploy makes: the template, and the runner the sandbox Worker ships.
  const runner = shippedRunner();
  const pair = pairTag(template, runner);
  if (open) {
    plan.push(`the pair ${pair} passed its standing checks: ${later}`);
  } else if (await templateReady(pair, input("E2B_API_KEY")!)) {
    plan.push(`the pair ${pair} passed its standing checks in this E2B team`);
  } else if (!apply) {
    plan.push(`the pair ${pair} has not passed in this E2B team: the pair job runs its check ` +
              "(--pair) before the apply job");
  } else {
    throw new Error(`the pair ${pair} has not passed its standing checks in this E2B team: ` +
                    "run the pair job (--pair) first; nothing is deployed");
  }
  // E2B's lifecycle webhook, listed and checked now: two hooks for its URL, or a failed
  // listing, stop the kit before a route moves.
  const listed = open ? undefined : await heldHook(input("E2B_API_KEY")!);
  const hookAt = hookPlan({ open, routes, missing: listed === null });
  const hook = (change: boolean, setSecret: boolean) => lifecycleHook(
    input("E2B_API_KEY")!, input("E2B_WEBHOOK_SECRET")!, change, hookAt.enable, setSecret);
  let steps: RouteStep[] = [];
  if (routes) {
    steps = handover(await routesOf(api, zone), DOORS, ROUTES);
  }
  const titles = await namespacesOf(api, account);
  const ids: Record<string, string> = {};
  for (const [binding, title] of Object.entries(NAMESPACES)) {
    const id = titles.get(title);
    if (id !== undefined) {
      ids[binding] = id;
      continue;
    }
    plan.push(`create the KV namespace ${title} (${binding})`);
    if (apply) {
      wrangler(["kv", "namespace", "create", title]);
      const made = (await namespacesOf(api, account)).get(title);
      if (made === undefined) {
        throw new Error(`the KV namespace ${title} was not made`);
      }
      ids[binding] = made;
    } else {
      ids[binding] = `planned-${title}`;
    }
  }
  ensure("bucket", BUCKET, apply, plan);
  const queues = new Set<string>();
  for (const worker of ["sandbox", "owner"]) {
    const config = configOf(worker)["queues"] as { producers?: { queue: string }[] } | undefined;
    for (const { queue } of config?.producers ?? []) {
      queues.add(queue);
    }
  }
  for (const queue of queues) {
    ensure("queue", queue, apply, plan);
  }
  // Reached only when every listing answered: a token without the scope stops above.
  plan.push("the Cloudflare token listed the account's KV namespaces, R2 buckets and queues");
  const out = mkdtempSync(join(tmpdir(), "tinyassets-deploy-"));
  try {
    const paths: Record<string, string> = {};
    for (const worker of WORKERS) {
      mkdirSync(join(out, worker));
      paths[worker] = join(out, worker, "wrangler.json");
      writeFileSync(paths[worker]!, JSON.stringify(deployedConfig(worker, ids, upstream, false),
                                                   null, 2));
    }
    // Secrets first: `secrets.required` refuses a deploy without them.
    const held: Record<string, string[] | null> = {};
    for (const worker of Object.keys(SECRETS)) {
      held[worker] = await secretsOf(api, account, `tinyassets-${worker}`);
    }
    const setting = secretPlan(held, rotate, open ? null : input, hookAt.creates);
    const hookSecret = hookSetsSecret(setting);
    for (const { worker, name } of setting) {
      const value = input(name);
      if (value === undefined && !open) {
        if (OPTIONAL.includes(name)) {
          continue;
        }
        throw new Error(`${name} has no value for tinyassets-${worker}`);
      }
      plan.push(`set the secret ${name} on tinyassets-${worker}${open
        ? ` (its value, or its absence if optional: ${later})` : ""}`);
      if (apply) {
        wrangler(["secret", "put", name, "--config", paths[worker]!], value);
      }
    }
    for (const worker of WORKERS) {
      // Whether it exists: its secret listing's answer (null when the API says it does not).
      const exists = (worker in held ? held[worker]
        : await secretsOf(api, account, `tinyassets-${worker}`)) !== null;
      plan.push(`${exists && !migrate.has(worker) ? "upload and deploy a version of"
        : "deploy"} tinyassets-${worker}, touching no route`);
      if (apply) {
        deployWorker(worker, paths[worker]!, exists, migrate);
      }
    }
    if (ids["CONTROL"]!.startsWith("planned-")
        || await kvValue(api, account, ids["CONTROL"]!, CONTROL_KEY) === undefined) {
      plan.push("write the control record closed (outside off, canary none, inbound off, the " +
                "founder's tier numbers): the API answered there is none");
      if (apply) {
        const put = await api(`/accounts/${account}/storage/kv/namespaces/${ids["CONTROL"]}/` +
                              "values/c%3Acontrol", {
          method: "PUT", body: JSON.stringify(DARK_CONTROL),
          headers: { "Content-Type": "application/json" } });
        if (put.status !== 200) {
          throw new Error(`writing the control record failed (${put.status})`);
        }
      }
    }
    plan.push(`WorkOS redirect URI ${CALLBACK}: ${open ? later
      : await registerRedirect(input("WORKOS_API_KEY")!, apply)}`);
    // E2B's lifecycle webhook, once the doors hold E2B_WEBHOOK_SECRET (`hookPlan`).
    if (hookAt.at === "none") {
      plan.push(`E2B lifecycle webhook ${E2B_HOOK.url}: ${later}`);
    }
    if (hookAt.at === "secrets") {
      plan.push(`E2B lifecycle webhook ${E2B_HOOK.url}: ${await hook(apply, hookSecret)}`);
    }
    if (routes) {
      plan.push(...steps.map((step) => describeStep(step, DOORS)));
      plan.push(`add the custom domain ${EGRESS_DOMAIN} for ${DOORS} (wrangler triggers deploy)`);
      if (apply) {
        await runHandover(api, zone, DOORS, steps);
        const routed = join(out, "doors", "routed.json");
        writeFileSync(routed, JSON.stringify(deployedConfig("doors", ids, upstream, true),
                                             null, 2));
        wrangler(["triggers", "deploy", "--config", routed]);
      }
      if (hookAt.at === "routes") {
        plan.push(`E2B lifecycle webhook ${E2B_HOOK.url}, after the routes: ${
          await hook(apply, hookSecret)}`);
      }
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
  report(plan, apply);
  if (!apply) {
    console.log("\nNothing was changed.");
    return;
  }
  handlesCanary();
  console.log("H2's probes wait for a routed door and a probe owner's key: run with --h2 " +
              "(h2-probes.ts names what it needs).");
}

/** The plan, printed and written to the job summary. */
function report(plan: readonly string[], applied: boolean): void {
  const summary = `${applied ? "Changed" : "Would change"}:\n${plan.map((step) => `- ${step}`)
    .join("\n")}`;
  console.log(summary);
  if (process.env["GITHUB_STEP_SUMMARY"] !== undefined) {
    writeFileSync(process.env["GITHUB_STEP_SUMMARY"], `${summary}\n`, { flag: "a" });
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error: Error) => {
    console.error(`deploy stopped: ${error.message}`);
    process.exit(1);
  });
}
