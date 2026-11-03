/**
 * The dark deploy (tasks 5, "0c (live, slice L)"; tasks 2, "DNS, routes and deploy"): one
 * reviewed step that deploys the six platform Workers to the production Cloudflare account
 * with no public route, then runs the handles canary (AGENTS.md Fact 11) and, once a probe
 * owner exists, H2's probes (`h2-probes.ts`). Built in slice L, never run by it: the
 * founder runs it (`checklist.md`).
 *
 *   node --experimental-strip-types platform/deploy/deploy.ts                 # the plan only
 *   node --experimental-strip-types platform/deploy/deploy.ts --yes           # dark deploy
 *   node --experimental-strip-types platform/deploy/deploy.ts --yes --routes  # + the routes
 *   node --experimental-strip-types platform/deploy/deploy.ts --yes --h2      # + H2's probes
 *
 * Without `--yes` it only reads (the account's lists, the issuer's discovery, WorkOS's
 * redirect URIs) and prints every change it would make. Each step is idempotent: a
 * namespace, bucket or queue is created only when missing, a secret set only when its
 * Worker lacks it (`--rotate NAME,...` sets one again), the control record written only
 * when there is none, the WorkOS redirect URI registered only when missing (GET, then POST;
 * never a delete). The checked-in configs stay undeployable (their guards hold them): this
 * writes the deployed configs into a temporary directory, filling only the namespace ids,
 * the doors' upstream values and, with `--routes`, the doors' routes.
 *
 * Inputs, from the environment or the production host's `/etc/tinyassets/env` (`KEY=value`
 * lines; no value is ever printed): CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID
 * (wrangler's), WORKOS_API_KEY and WORKOS_CLIENT_ID, OWNER_ISSUER (AuthKit's issuer, held
 * to its live discovery document exactly, or nothing deploys), E2B_API_KEY, and optionally
 * INBOUND_SLACK_SIGNING_SECRET. ROUTING_MAC_KEY (the doors' and the owner's one key) and
 * GATEWAY_KEYRING are made here on the first deploy, 32 random bytes each.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
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
  doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "INBOUND_SLACK_SIGNING_SECRET"],
  owner: ["ROUTING_MAC_KEY"],
  gateway: ["GATEWAY_KEYRING"],
  sandbox: ["E2B_API_KEY"],
});
/** The secrets made here, once; and the one both the doors and the owner hold. */
export const MADE: readonly string[] = Object.freeze(["ROUTING_MAC_KEY", "GATEWAY_KEYRING"]);
const SHARED = "ROUTING_MAC_KEY";
/** Set when given; without it that door refuses loudly. */
const OPTIONAL: readonly string[] = Object.freeze(["INBOUND_SLACK_SIGNING_SECRET"]);
/** The doors' public routes (`--routes`, the cutover's), on the tinyassets.io zone. */
export const ROUTES: readonly string[] = Object.freeze([
  "tinyassets.io/mcp*", "tinyassets.io/api/*", "tinyassets.io/hooks/inbound/*",
  "tinyassets.io/app/api/*", "tinyassets.io/authorize*", "tinyassets.io/oauth/*",
  "tinyassets.io/.well-known/oauth-*",
]);
/** The sandbox's egress host, a custom domain of the doors Worker (`--routes`). */
export const EGRESS_DOMAIN = "egress.tinyassets.io";
export const CALLBACK = "https://tinyassets.io/oauth/upstream/callback";
const WORKOS_API = "https://api.workos.com";
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

/** The production host's env file's values, without printing any. */
export function hostEnv(path = "/etc/tinyassets/env"): Record<string, string> {
  if (!existsSync(path)) {
    return {};
  }
  const values: Record<string, string> = {};
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const match = /^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$/.exec(line);
    if (match) {
      values[match[1]!] = match[2]!.replace(/^(['"])(.*)\1$/, "$2");
    }
  }
  return values;
}

let host: Record<string, string> | undefined;
function input(name: string): string | undefined {
  host ??= hostEnv();
  return process.env[name] ?? host[name];
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
    env: { ...process.env, WRANGLER_SEND_METRICS: "false",
           CLOUDFLARE_API_TOKEN: input("CLOUDFLARE_API_TOKEN"),
           CLOUDFLARE_ACCOUNT_ID: input("CLOUDFLARE_ACCOUNT_ID") },
  });
  if (result.status !== 0) {
    // Its stderr alone: wrangler never echoes a secret's value.
    throw new Error(`wrangler ${args.slice(0, 3).join(" ")} failed: ${result.stderr.trim()}`);
  }
  return result.stdout;
}

/** The doors' upstream: the issuer, and its `UPSTREAM` record. */
export interface UpstreamValues {
  readonly issuer: string;
  readonly record: Json;
}

/**
 * The deployed config of `worker`: its checked-in config with the namespace ids it names
 * filled, the doors' upstream values set and, with `routes`, the doors' routes; nothing
 * else changes, so what the guards hold is what deploys.
 */
export function deployedConfig(worker: string, ids: Readonly<Record<string, string>>,
                               upstream: UpstreamValues, routes: boolean): Json {
  const config = structuredClone(configOf(worker));
  config["main"] = join(PLATFORM, worker, String(config["main"]));
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
      config["routes"] = [...ROUTES.map((pattern) => ({ pattern, zone_name: "tinyassets.io" })),
                          { pattern: EGRESS_DOMAIN, custom_domain: true }];
    }
  }
  return config;
}

/** The owner issuer's discovery, held to `issuer` exactly, and the doors' `UPSTREAM` from it. */
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
  const claims = discovery["claims_supported"];
  if (Array.isArray(claims) && !claims.includes("auth_time")) {
    throw new Error("the issuer lists no auth_time claim: every sign-in would be refused");
  }
  const methods = discovery["token_endpoint_auth_methods_supported"];
  if (Array.isArray(methods) && !methods.includes("client_secret_post")) {
    throw new Error("the issuer takes no client_secret_post at its token endpoint");
  }
  return {
    issuer,
    record: { client_id: clientId, authorization_endpoint: discovery["authorization_endpoint"],
              token_endpoint: discovery["token_endpoint"], jwks_uri: discovery["jwks_uri"],
              user_delete: `${WORKOS_API}/user_management/users/{sub}` },
  };
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
  if (Array.isArray(data) && data.some((row) => (row as Json | null)?.["uri"] === uri)) {
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
 * What to set: each Worker's missing secrets (and those `rotate` names), the shared routing
 * key on both or neither. `held` is what each Worker holds now.
 */
export function secretPlan(held: Readonly<Record<string, readonly string[]>>,
                           rotate: ReadonlySet<string>): { worker: string; name: string }[] {
  const doors = held["doors"]?.includes(SHARED) ?? false;
  const owner = held["owner"]?.includes(SHARED) ?? false;
  if (doors !== owner && !rotate.has(SHARED)) {
    throw new Error(`only one of the doors and the owner holds ${SHARED}: pass --rotate ` +
                    `${SHARED} to set both again`);
  }
  const plan: { worker: string; name: string }[] = [];
  for (const [worker, names] of Object.entries(SECRETS)) {
    for (const name of names) {
      if (!(held[worker] ?? []).includes(name) || rotate.has(name)) {
        plan.push({ worker, name });
      }
    }
  }
  return plan;
}

function madeValue(name: string): string {
  return name === "GATEWAY_KEYRING"
    ? JSON.stringify({ active: "k1", keys: { k1: randomBytes(32).toString("base64") } })
    : randomBytes(32).toString("base64url");
}

function namespaceIds(apply: boolean, plan: string[]): Record<string, string> {
  const listed = () => JSON.parse(wrangler(["kv", "namespace", "list"])) as
    { id: string; title: string }[];
  const now = listed();
  const ids: Record<string, string> = {};
  for (const [binding, title] of Object.entries(NAMESPACES)) {
    const found = now.find((namespace) => namespace.title === title);
    if (found !== undefined) {
      ids[binding] = found.id;
      continue;
    }
    plan.push(`create the KV namespace ${title} (${binding})`);
    if (apply) {
      wrangler(["kv", "namespace", "create", title]);
      ids[binding] = listed().find((namespace) => namespace.title === title)!.id;
    } else {
      ids[binding] = `planned-${title}`;
    }
  }
  return ids;
}

function ensure(kind: "bucket" | "queue", name: string, apply: boolean, plan: string[]): void {
  const list = kind === "bucket" ? ["r2", "bucket", "list"] : ["queues", "list"];
  if (wrangler(list).includes(name)) {
    return;
  }
  plan.push(`create the ${kind === "bucket" ? "R2 bucket" : "queue"} ${name}`);
  if (apply) {
    wrangler(kind === "bucket" ? ["r2", "bucket", "create", name] : ["queues", "create", name]);
  }
}

function secretsOn(path: string): string[] {
  try {
    return (JSON.parse(wrangler(["secret", "list", "--format", "json", "--config", path])) as
      { name: string }[]).map((secret) => secret.name);
  } catch {
    return [];  // a Worker not deployed yet holds none
  }
}

async function main(): Promise<void> {
  const apply = flag("--yes");
  const routes = flag("--routes");
  const rotate = new Set((option("--rotate") ?? "").split(",").filter(Boolean));
  const plan: string[] = [];
  for (const worker of WORKERS) {
    if (!existsSync(join(PLATFORM, worker, "wrangler.jsonc"))) {
      throw new Error(`platform/${worker} is missing: its slice has not landed`);
    }
  }
  for (const name of ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "WORKOS_API_KEY",
                      "WORKOS_CLIENT_ID", "OWNER_ISSUER", "E2B_API_KEY"]) {
    if (!input(name)) {
      throw new Error(`${name} is not set (the environment, or /etc/tinyassets/env)`);
    }
  }
  const upstream = await upstreamFrom(input("OWNER_ISSUER"), input("WORKOS_CLIENT_ID"));
  plan.push(`set the doors' OWNER_ISSUER to ${upstream.issuer}, held to its discovery`);
  const ids = namespaceIds(apply, plan);
  ensure("bucket", BUCKET, apply, plan);
  const sandbox = configOf("sandbox")["queues"] as { producers?: { queue: string }[] } | undefined;
  for (const { queue } of sandbox?.producers ?? []) {
    ensure("queue", queue, apply, plan);
  }
  const out = mkdtempSync(join(tmpdir(), "tinyassets-deploy-"));
  try {
    const paths: Record<string, string> = {};
    for (const worker of WORKERS) {
      mkdirSync(join(out, worker));
      paths[worker] = join(out, worker, "wrangler.json");
      writeFileSync(paths[worker]!, JSON.stringify(deployedConfig(worker, ids, upstream, routes),
                                                   null, 2));
    }
    // Secrets first: `secrets.required` refuses a deploy without them.
    const held = Object.fromEntries(Object.keys(SECRETS).map((w) => [w, secretsOn(paths[w]!)]));
    const made: Record<string, string> = {};
    for (const { worker, name } of secretPlan(held, rotate)) {
      const value = MADE.includes(name) ? (made[name] ??= madeValue(name)) : input(name);
      if (!value) {
        if (OPTIONAL.includes(name)) {
          continue;
        }
        throw new Error(`${name} has no value for tinyassets-${worker}`);
      }
      plan.push(`set the secret ${name} on tinyassets-${worker}`);
      if (apply) {
        wrangler(["secret", "put", name, "--config", paths[worker]!], value);
      }
    }
    for (const worker of WORKERS) {
      plan.push(`deploy tinyassets-${worker}${worker === "doors" && routes
        ? ` with its routes: ${[...ROUTES, EGRESS_DOMAIN].join(", ")}` : " with no route"}`);
      if (apply) {
        wrangler(["deploy", "--config", paths[worker]!]);
      }
    }
    const control = ids["CONTROL"]!.startsWith("planned-") ? ""
      : spawnSync(process.execPath, [join(dirname(require.resolve("wrangler/package.json")),
                                          "bin", "wrangler.js"),
                                     "kv", "key", "get", "c:control", "--namespace-id",
                                     ids["CONTROL"]!, "--remote"],
                  { cwd: PLATFORM, encoding: "utf8" }).stdout.trim();
    if (control === "" || /not found/i.test(control)) {
      plan.push("write the control record closed (outside off, canary none, inbound off, the " +
                "founder's tier numbers): there is none");
      if (apply) {
        wrangler(["kv", "key", "put", "c:control", JSON.stringify(DARK_CONTROL),
                  "--namespace-id", ids["CONTROL"]!, "--remote"]);
      }
    }
    plan.push(`WorkOS redirect URI ${CALLBACK}: ${
      await registerRedirect(input("WORKOS_API_KEY")!, apply)}`);
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
  console.log(`${apply ? "Changed" : "Would change"}:\n${plan.map((step) => `- ${step}`)
    .join("\n")}`);
  if (!apply) {
    console.log("\nNothing was changed. Run again with --yes to apply.");
    return;
  }
  // Fact 11: after a deploy, the handles canary. The dark deploy moved no public route, so
  // it holds the live door as it was.
  const canary = spawnSync("python3", [join(REPO, "scripts", "mcp_public_canary.py"),
                                       "--assert-handles"], { stdio: "inherit", cwd: REPO });
  if (canary.status !== 0) {
    throw new Error(`the handles canary failed (exit ${canary.status})`);
  }
  if (flag("--h2")) {
    execFileSync(process.execPath, ["--experimental-strip-types",
                                    join(PLATFORM, "deploy", "h2-probes.ts")],
                 { stdio: "inherit", env: process.env });
  } else {
    console.log("H2's probes wait for a routed door and a probe owner's key: run again with " +
                "--yes --h2 (h2-probes.ts names what it needs).");
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error: Error) => {
    console.error(`deploy stopped: ${error.message}`);
    process.exit(1);
  });
}
