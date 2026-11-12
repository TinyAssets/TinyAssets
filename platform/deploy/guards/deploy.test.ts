/**
 * The deploy kit (`platform/deploy/deploy.ts`), never run against a live account here: each
 * mode's row runs whole against a fake Cloudflare, WorkOS, E2B and wrangler (`world`), and
 * its plan and every call it makes are read back; its steps' parts are held one by one.
 *
 * What it holds: the kit names every Worker, binding, namespace and secret the platform's
 * configs declare; the config it deploys is the checked-in one with only the namespace ids,
 * the doors' upstream and (in `routes`) the routes filled; it writes User Management's
 * sign-in, the JWKS from the owner issuer's live discovery held to it exactly, and checks
 * WorkOS's authorize takes the doors' client and callback before anyone signs in; it
 * registers the WorkOS redirect URI only when missing and never deletes one; and it sets the
 * routing key on the doors and the owner together or not at all.
 *
 * Slice L's review (H2, research 3b and 3c): a failed lookup is never "absent"; a held
 * keyring is never replaced, and is rotated only by adding a key; the control record is
 * written only when the API answers that it is absent; an absent discovery field is unknown,
 * and unknown stops the kit; the kit runs from an approved manual workflow whose secrets come
 * from the environment's store. The sign-in probe takes only the sign-in's two unserved
 * paths, behind a control record that admits nobody, and `routes` then finds both held.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { describe, expect, it } from "vitest";

import { DARK, keyring as ringSecret } from "../../ci/fixtures";
import { PLATFORM, REPO } from "../../ci/paths";
import { workflow } from "../../ci/workflows";
import { WRANGLER, workerFolders } from "../../ci/workers";
import { type Domain, openBlob, sealBlob } from "../../contract/src/envelope";
import {
  AUTHORIZE_PATH, E2B_HOOK_PATH, PUBLIC_ORIGIN, UPSTREAM_CALLBACK, WEB_CLIENT, WEB_REDIRECT,
} from "../../doors/src/config";
import { APP_RESOURCE } from "../../contract/src/identity";
import { CONTROL_KEY, outsideOpen, ownerOpen, parseControl } from "../../contract/src/control";
import { oidcUpstream, upstreamConfig } from "../../doors/src/oidc";
import { E2b } from "../../sandbox/src/e2b";
import { type CfAnswer, cfApi, Cloudflare, handback, handover, type Route } from "../cloudflare";
import {
  admitsNobody, authorizeCheck, authorizeLink, codeDigests, DARK_CONTROL, deployedConfig,
  E2B_HOOK, heldHook, hookSetsSecret, type Io, keyring, keyringAdd, keyringNew,
  lifecycleHook, listed, lockRefusal, maskInLog, MIGRATED, type Mode, MODES, parse,
  perform, PROBE_SIGNS, probeLink, probeSteps, PUBLIC_INPUTS, registerRedirect, type RouteRecord,
  secretPlan, upstreamFrom, versionOf, wranglerFor,
} from "../deploy";
import { bundles, pairTag, treeImage } from "../../sandbox/template/pair";
import {
  boundBy, BUCKETS, CALLBACK, CONFIGS, DOORS, EGRESS_DOMAIN, KEYRINGS, MADE, NAMES,
  NAMESPACES, OPTIONAL, PROBE_ROUTES, QUEUES, RETIRED, retiredWorkers, ROUTES, SECRET_INPUTS,
  SECRETS, WORKERS, ZONE,
} from "../facts";
import { INPUTS as H2_INPUTS } from "../h2-probes";
import platform from "../platform.json" with { type: "json" };

type Json = Record<string, unknown>;

const configOf = (worker: string) => CONFIGS.get(worker)!;
const UPSTREAM = { issuer: "https://auth.test",
                   record: { client_id: "c",
                             authorization_endpoint:
                               "https://api.workos.com/user_management/authorize",
                             token_endpoint: "https://api.workos.com/user_management/authenticate",
                             jwks_uri: "https://auth.test/j",
                             token_issuer: "https://api.workos.com/user_management/c",
                             user_delete: "https://api.workos.com/user_management/users/{sub}",
                             authorize_params: { provider: "authkit" } } };
const IDS = Object.fromEntries(Object.keys(NAMESPACES).map((binding) => [binding, `id-${binding}`]));
const NONE = () => undefined;
const RING = ringSecret("k1");
const ROTATED = ringSecret("k2", ["k1", "k2"]);
const E2B_KEY = "e2b_test_key";
const ACCOUNT = "a".repeat(32);
const TEMPLATE = String((configOf("sandbox")["vars"] as Json)["E2B_TEMPLATE"]);
/** The runner and probes the fake bundle writes, and the pair they make with the pin. */
const RUNNER = "the runner";
const PROBE_BUNDLE = "the probes";
const sha = (text: string) => createHash("sha256").update(text).digest("hex");
const PAIR = pairTag(TEMPLATE, sha(RUNNER), sha(PROBE_BUNDLE));

/** A Cloudflare API that answers each path with the answer `answers` names, or a 500. */
function api(answers: Record<string, CfAnswer | Error>) {
  const calls: string[] = [];
  const call = async (path: string, init?: RequestInit): Promise<CfAnswer> => {
    calls.push(`${init?.method ?? "GET"} ${path}`);
    const answer = answers[path];
    if (answer instanceof Error) {
      throw answer;
    }
    return answer ?? { status: 500, body: { success: false, errors: [{ code: 10000 }] } };
  };
  return Object.assign(call, { calls });
}

const ok = (result: unknown): CfAnswer => ({ status: 200, body: { success: true, result } });

/** The production inputs a full run holds: fake values, never a real one. */
const ENV: Record<string, string> = {
  CLOUDFLARE_API_TOKEN: "cf-token", WORKOS_CLIENT_ID: "client_test",
  OWNER_ISSUER: "https://auth.test", WORKOS_API_KEY: "workos-key", E2B_API_KEY: E2B_KEY,
  ROUTING_MAC_KEY: "mac-key", GATEWAY_KEYRING: RING, FILES_KEYRING: RING,
  E2B_WEBHOOK_SECRET: "hook-secret", INBOUND_SLACK_SIGNING_SECRET: "slack-secret",
  PATH: "/bin", HOME: "/home/test",
};
/** What the plan job holds. */
const PUBLIC = Object.fromEntries(PUBLIC_INPUTS.map((name) => [name, ENV[name]!]));

/** The old proxy's live routes, and the site's. */
const LIVE: readonly Route[] = [
  { id: "r1", pattern: "tinyassets.io/mcp*", script: "tinyassets-mcp-proxy" },
  { id: "r2", pattern: "tinyassets.io/app*", script: "tinyassets-mcp-proxy" },
  { id: "r3", pattern: "tinyassets.io/api*", script: "tinyassets-mcp-proxy" },
  { id: "r4", pattern: "tinyassets.io/.well-known/oauth-protected-resource*",
    script: "tinyassets-mcp-proxy" },
  { id: "r5", pattern: "tinyassets.io/.well-known/oauth-authorization-server*",
    script: "tinyassets-mcp-proxy" },
  { id: "r6", pattern: "tinyassets.io/site*", script: "site" },
];

interface WorldOptions {
  /** Everything a dark run makes is there already. */
  readonly held?: boolean;
  /** The probe's two routes are the doors' already. */
  readonly probed?: boolean;
  /** E2B holds the pair's tag. */
  readonly paired?: boolean;
  /** This tree's image, as `image.ts` computes it: the pin by default. */
  readonly image?: string;
  /** The migrations guard reports this lock for every Worker. */
  readonly lock?: Record<string, string[]>;
  /** A wrangler command that fails. */
  readonly failing?: string;
  /** KV keys whose write Cloudflare refuses. */
  readonly refusing?: string;
  /** The Worker the egress host's custom domain serves already. */
  readonly egress?: string;
  /** DNS records the egress host holds, by type. */
  readonly dns?: readonly string[];
  /** Cloudflare's detach leaves the DNS record the custom domain's attach made. */
  readonly keepsDns?: boolean;
  /** The DNS listing ignores its name filter: the zone's other records come back too. */
  readonly noisyDns?: boolean;
  /** A route change the API refuses (400): `METHOD pattern`. */
  readonly refusingRoute?: string;
  /** DNS deletes the API refuses (403): a token without DNS Edit. */
  readonly refusingDns?: boolean;
  /** The R2 buckets and queues the account lists, in place of none (fresh) or all (held). */
  readonly listed?: { readonly buckets: readonly string[]; readonly queues: readonly string[] };
  /** The account still holds the retired Workers (`platform.json`). */
  readonly retired?: boolean;
  /** Cloudflare refuses a Worker's delete: another Worker still references it. */
  readonly refusingDelete?: boolean;
}

/** The world's clock. */
const NOW = new Date("2026-11-07T12:00:00.000Z");

/**
 * A fake of everything the kit reaches, as one ordered event log: Cloudflare's API and
 * wrangler (the account's state changing as the kit changes it), WorkOS, the owner issuer,
 * E2B, python and the migrations guard.
 */
function world(options: WorldOptions = {}) {
  const held = options.held === true;
  const events: string[] = [];
  const logs: string[] = [];
  const secretsPut: Record<string, string> = {};
  /** The account each wrangler command was given, in order. */
  const accounts: (string | undefined)[] = [];
  let pairPassed = options.paired !== false;
  const namespaces = new Map(held ? Object.values(NAMESPACES).map((title) => [title, `ns-${title}`])
    : []);
  const secrets = new Map<string, string[]>(held ? Object.entries(SECRETS).map(([worker, names]) =>
    [NAMES[worker]!, [...names]]) : []);
  if (held) {
    secrets.set(NAMES["codemode"]!, []);
  }
  for (const name of options.retired === true ? RETIRED : []) {
    secrets.set(name, []);
  }
  const routes = new Map((options.probed === true || held
    ? [...LIVE, { id: "p1", pattern: "tinyassets.io/authorize*", script: DOORS },
       { id: "p2", pattern: "tinyassets.io/oauth/*", script: DOORS }] : LIVE)
    .map((route) => [route.id, { ...route }]));
  const hooks: Json[] = held ? [{ id: "w1", url: E2B_HOOK.url, enabled: false,
                                  events: [...E2B_HOOK.events] }] : [];
  /** KV, by namespace id and key: the control record when held. */
  const kv = new Map<string, string>(held
    ? [[`ns-${NAMESPACES["CONTROL"]}/${CONTROL_KEY}`, JSON.stringify(DARK_CONTROL)]] : []);
  /** The zone's routes after each route change, in order. */
  const tables: Route[][] = [];
  /** The egress host's custom domains, by id. */
  const domains = new Map<string, { id: string; hostname: string; service: string }>(
    options.egress === undefined ? []
      : [["d0", { id: "d0", hostname: EGRESS_DOMAIN, service: options.egress }]]);
  /**
   * The outward changes left before the run is killed (undefined: never): a route's, the
   * custom domain's (wrangler's triggers deploy, a detach) and the webhook's.
   */
  const kill = { after: undefined as number | undefined };
  const killed = () => kill.after !== undefined && kill.after-- <= 0;
  /** Writes that answer as done and change nothing: the hook's, the custom domain's. */
  const inert = new Set<"hook" | "domain" | "dns">();
  /** The zone's DNS records: the egress host's given ones, and others on other names. */
  const dns = [
    ...(options.dns ?? []).map((type, at) => ({ id: `x${at}`, type, name: EGRESS_DOMAIN,
                                                 content: "192.0.2.1" })),
    { id: "o1", type: "A", name: ZONE, content: "192.0.2.2" },
    { id: "o2", type: "CNAME", name: `www.${ZONE}`, content: ZONE },
    { id: "o3", type: "TXT", name: EGRESS_DOMAIN, content: "v=spf1 -all" },
  ];
  const ATTACHED = { id: "dns-egress", type: "AAAA", name: EGRESS_DOMAIN, content: "100::" };
  let made = 0;
  const json = (body: unknown, status = 200) => Response.json(body, { status });
  const cf = (path: string, method: string, body: string | undefined): Response => {
    if (path === `/zones?name=${ZONE}`) {
      return json({ success: true, result: [{ id: "z", account: { id: ACCOUNT } }] });
    }
    if (path === "/zones/z/workers/routes" && method === "GET") {
      return json({ success: true, result: [...routes.values()] });
    }
    if (path === `/accounts/${ACCOUNT}/workers/domains?hostname=${EGRESS_DOMAIN}`) {
      return json({ success: true, result: [...domains.values()] });
    }
    if (path.startsWith(`/accounts/${ACCOUNT}/workers/domains/`) && method === "DELETE") {
      if (killed()) {
        throw new Error("the run was killed");
      }
      if (!inert.has("domain")) {
        domains.delete(path.split("/").at(-1)!);
        if (options.keepsDns !== true) {
          dns.splice(0, dns.length, ...dns.filter((record) => record.id !== ATTACHED.id));
        }
      }
      return json({ success: true, result: null });
    }
    if (path === `/zones/z/dns_records?name.exact=${EGRESS_DOMAIN}&per_page=100`) {
      return json({ success: true, result: dns.filter((record) => options.noisyDns === true
        || record.name === EGRESS_DOMAIN) });
    }
    if (path.startsWith("/zones/z/dns_records/") && method === "DELETE") {
      if (killed()) {
        throw new Error("the run was killed");
      }
      if (options.refusingDns === true) {
        return json({ success: false, errors: [{ code: 10000 }] }, 403);
      }
      const id = path.split("/").at(-1)!;
      if (!inert.has("dns")) {
        dns.splice(0, dns.length, ...dns.filter((record) => record.id !== id));
      }
      return json({ success: true, result: { id } });
    }
    if (path.startsWith("/zones/z/workers/routes")) {
      if (killed()) {
        throw new Error("the run was killed");
      }
      const id = path.split("/").at(-1)!;
      const route = JSON.parse(body ?? "{}") as Route;
      const held = routes.get(id)?.pattern;
      if (options.refusingRoute !== undefined
          && options.refusingRoute === `${method} ${route.pattern ?? held}`) {
        return json({ success: false, errors: [{ code: 10020 }] }, 400);
      }
      if (method === "POST") {
        routes.set(`n${made}`, { ...route, id: `n${made}` });
        made += 1;
      } else if (method === "PUT") {
        routes.set(id, { ...route, id });
      } else {
        routes.delete(id);
      }
      tables.push([...routes.values()].map((each) => ({ ...each })));
      return json({ success: true, result: {} });
    }
    if (/^\/accounts\/a+\/workers\/scripts$/.test(path)) {
      return json({ success: true, result: [...secrets.keys()].map((id) => ({ id })) });
    }
    const script = /^\/accounts\/a+\/workers\/scripts\/([a-z-]+)$/.exec(path);
    if (script !== null && method === "DELETE") {
      if (options.refusingDelete === true) {
        return json({ success: false, errors: [{ code: 10064 }] }, 400);
      }
      secrets.delete(script[1]!);
      return json({ success: true, result: null });
    }
    const listing = /^\/accounts\/a+\/workers\/scripts\/([a-z-]+)\/secrets$/.exec(path);
    if (listing !== null) {
      const names = secrets.get(listing[1]!);
      return names === undefined
        ? json({ success: false, errors: [{ code: 10007 }] }, 404)
        : json({ success: true, result: names.map((name) => ({ name, type: "secret_text" })) });
    }
    if (path.startsWith(`/accounts/${ACCOUNT}/storage/kv/namespaces?`)) {
      return json({ success: true, result: [...namespaces].map(([title, id]) => ({ id, title })) });
    }
    const value = /^\/accounts\/a+\/storage\/kv\/namespaces\/([^/]+)\/values\/(.+)$/.exec(path);
    if (value !== null) {
      const key = `${value[1]}/${decodeURIComponent(value[2]!)}`;
      if (method === "PUT") {
        if (options.refusing !== undefined && key.endsWith(`/${options.refusing}`)) {
          return json({ success: false, errors: [{ code: 10000 }] }, 500);
        }
        kv.set(key, body ?? "");
        return json({ success: true });
      }
      const stored = kv.get(key);
      return stored === undefined ? json({ success: false, errors: [{ code: 10009 }] }, 404)
        : new Response(stored, { status: 200 });
    }
    return json({ success: false, errors: [{ code: 1 }] }, 500);
  };
  const fetcher = async (url: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? "GET";
    const body = init?.body === undefined ? undefined : String(init.body);
    const at = new URL(url);
    if (at.host === "api.cloudflare.com") {
      const path = url.slice("https://api.cloudflare.com/client/v4".length);
      events.push(`cf ${method} ${path}`);
      return cf(path, method, body);
    }
    if (at.host === "auth.test") {
      events.push("issuer discovery");
      return json({ issuer: "https://auth.test", jwks_uri: "https://auth.test/jwks" });
    }
    if (at.pathname === "/user_management/authorize") {
      events.push("workos authorize");
      return new Response(null, { status: 302, headers: { Location: "https://auth.test/sign-in" } });
    }
    if (at.pathname === "/user_management/redirect_uris") {
      events.push(`workos ${method} redirect_uris`);
      return method === "POST" ? json({}, 201)
        : json({ data: held || options.probed === true ? [{ uri: CALLBACK }] : [] });
    }
    if (at.host === "api.e2b.app") {
      expect((init?.headers as Record<string, string>)["X-API-Key"]).toBe(E2B_KEY);
      events.push(`e2b ${method} ${at.pathname}${body === undefined ? "" : ` ${body}`}`);
      if (at.pathname.startsWith("/templates/aliases/")) {
        return json({ templateID: "t1" });
      }
      if (at.pathname.endsWith("/tags/exists")) {
        const tag = at.searchParams.get("tag") ?? "";
        return json({ exists: tag.includes("-r") ? pairPassed : true });
      }
      if (method !== "GET" && killed()) {
        throw new Error("the run was killed");
      }
      if (at.pathname === "/events/webhooks") {
        if (method === "POST") {
          hooks.push({ id: "w1", ...JSON.parse(body!) as Json });
          return json({ id: "w1" }, 201);
        }
        return json(hooks);
      }
      const hook = hooks.find((each) => at.pathname === `/events/webhooks/${String(each["id"])}`);
      if (hook !== undefined && method === "PATCH" && !inert.has("hook")) {
        Object.assign(hook, JSON.parse(body!) as Json);
      }
      return json({});
    }
    events.push(`unexpected ${method} ${url}`);
    return new Response("", { status: 599 });
  };
  const spawn = ((command: string, argv: readonly string[] = [],
                  options_: { env?: Record<string, string>; input?: string } = {}) => {
    if (argv[0] === WRANGLER) {
      accounts.push(options_.env?.["CLOUDFLARE_ACCOUNT_ID"]);
      const args = argv.slice(1).map((arg) => arg.endsWith(".json")
        ? `${basename(join(arg, ".."))}/${basename(arg)}` : arg);
      events.push(`wrangler ${args.join(" ")}`);
      if (options.failing !== undefined && args.join(" ").startsWith(options.failing)) {
        return { status: 1, stdout: "", stderr: "the API refused it" };
      }
      if (args[0] === "secret") {
        secretsPut[`${basename(join(argv[argv.length - 1]!, ".."))}:${args[2]}`] =
          options_.input ?? "";
      }
      if (args[0] === "kv") {
        namespaces.set(args[3]!, `made-${args[3]}`);
      }
      if (args[0] === "triggers") {
        if (killed()) {
          return { status: 1, stdout: "", stderr: "the run was killed" };
        }
        // One custom domain a host: the doors' already is kept as it is.
        if (![...domains.values()].some((domain) => domain.service === DOORS)) {
          domains.set("d1", { id: "d1", hostname: EGRESS_DOMAIN, service: DOORS });
          if (!dns.some((record) => record.id === ATTACHED.id)) {
            dns.push({ ...ATTACHED });
          }
        }
      }
      const buckets = options.listed?.buckets ?? (held ? BUCKETS : []);
      const queues = options.listed?.queues ?? (held ? QUEUES : []);
      const stdout = args[1] === "bucket" && args[2] === "list"
        ? buckets.map((bucket) => `name:  ${bucket}\n`).join("")
        : args[0] === "queues" && args[1] === "list"
          ? queues.map((queue, n) => `│ q${n + 1} │ ${queue} │\n`).join("") : args[1] === "upload"
            ? "Worker Version ID: 0f7e3a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b\n" : "";
      return { status: 0, stdout, stderr: "" };
    }
    if (command === "python3") {
      events.push(`python3 ${basename(argv[0]!)}${argv.slice(1).map((arg) => ` ${arg}`).join("")}${
        options_.env === undefined ? "" : ` env=${Object.keys(options_.env).sort().join(",")}`}`);
      return { status: 0, stdout: "", stderr: "" };
    }
    if (command === process.execPath && argv.some((arg) => arg.endsWith("checks.ts"))) {
      events.push(`node ${argv.slice(argv.findIndex((arg) => arg.endsWith("checks.ts")))
        .map((arg) => basename(arg)).join(" ")} env=${Object.keys(options_.env ?? {}).sort()
        .join(",")}`);
      pairPassed = true;
      return { status: 0, stdout: "", stderr: "" };
    }
    if (String(argv[0]).endsWith("vitest.mjs")) {
      const worker = basename(String((options_ as { cwd?: string }).cwd));
      events.push(`guard ${worker}`);
      writeFileSync(options_.env!["MIGRATION_DIGESTS"]!, JSON.stringify(options.lock ?? locks(worker)));
      return { status: 0, stdout: "", stderr: "" };
    }
    events.push(`unexpected spawn ${command} ${argv.join(" ")}`);
    return { status: 1, stdout: "", stderr: "" };
  }) as unknown as Io["spawn"];
  const exec = ((_command: string, argv: readonly string[] = [],
                 options_: { env?: Record<string, string> } = {}) => {
    if (String(argv[0]).endsWith("bundle.mjs")) {
      events.push("bundle the runner and the probes");
      writeFileSync(join(argv[1]!, "runner.mjs"), RUNNER);
      writeFileSync(join(argv[1]!, "probes.mjs"), PROBE_BUNDLE);
    } else if (String(argv.at(-1)).endsWith("image.ts")) {
      events.push("the tree's image");
      return `${options.image ?? TEMPLATE}\n`;
    } else {
      events.push(`exec ${basename(String(argv.at(-1)))} env has ${[...H2_INPUTS_LIST, "E2B_API_KEY"]
        .filter((name) => options_.env?.[name] !== undefined).join(",")}`);
    }
    return "";
  }) as unknown as Io["exec"];
  // What the kit prints joins the one timeline, by its first line.
  const log = (line: string) => {
    logs.push(line);
    events.push(`log ${line.trim().split("\n")[0]}`);
  };
  const io: Io = { fetch: fetcher, spawn, exec, log, now: () => NOW };
  return { io, events, logs, secretsPut, routes, hooks, accounts, kv, tables, kill, domains,
           inert, dns };
}

const H2_INPUTS_LIST = Object.values(H2_INPUTS);

/** Each Worker's checked-in migration locks, as its guard reports them. */
function locks(worker: string): Record<string, string[]> {
  const files = worker === "owner" ? ["owner/migrations.json"]
    : readdirSync(join(PLATFORM, worker, "migrations")).map((file) => `${worker}/migrations/${file}`);
  return Object.fromEntries(files.map((file) => [file, (JSON.parse(readFileSync(
    join(PLATFORM, file), "utf8")) as { deployed: string[] }).deployed]));
}

/** `mode` run whole in `w`, with `flags` and `env`: its work, or its refusal. */
async function runMode(w: ReturnType<typeof world>, mode: Mode, flags: string[] = [],
                       env: Record<string, string> = ENV) {
  return perform(parse(["--mode", mode, ...flags], env, w.io));
}

/** The plan as the kit reports it: "Changed" or "Would change", a line a step. */
function summary(heading: string, plan: readonly string[]): string {
  return `${heading}:\n${plan.map((line) => `- ${line}`).join("\n")}`;
}

/** The indexes of `needles` in `events`, each the first after the one before it. */
function order(events: readonly string[], needles: readonly string[]): number[] {
  let from = 0;
  return needles.map((needle) => {
    const at = events.findIndex((event, index) => index >= from && event.startsWith(needle));
    expect([needle, at], events.join("\n")).not.toEqual([needle, -1]);
    from = at + 1;
    return at;
  });
}

describe("the deploy kit's facts", () => {
  it("deploys every Worker the platform holds, each after every Worker it binds", () => {
    // The Workers the production deploy takes, pinned on purpose: a folder with a config of
    // its own joins the deploy only once this list is changed in a reviewed change.
    expect(workerFolders()).toEqual(["codemode", "doors", "files", "gateway", "owner", "sandbox"]);
    expect([...WORKERS].sort()).toEqual(workerFolders());
    for (const worker of WORKERS) {
      for (const bound of boundBy(worker)) {
        expect([worker, bound, WORKERS.indexOf(bound) < WORKERS.indexOf(worker)])
          .toEqual([worker, bound, true]);
      }
    }
    // The doors bind the owner by its objects' script alone: the order holds it too.
    expect(boundBy("doors")).toContain("owner");
  });

  it("retires only a Worker no config names", () => {
    expect(retiredWorkers(["tinyassets-old"], NAMES)).toEqual(["tinyassets-old"]);
    expect(() => retiredWorkers(["tinyassets-old", NAMES["owner"]!], NAMES))
      .toThrow("platform.json retires tinyassets-owner, which a config still names");
  });

  it("holds only what no config says, and that for exactly what the configs declare", () => {
    // A title for each KV binding the configs make, and none for a binding none makes.
    const bindings = new Set(WORKERS.flatMap((worker) =>
      ((configOf(worker)["kv_namespaces"] ?? []) as { binding: string }[]).map((kv) => kv.binding)));
    expect(Object.keys(NAMESPACES).sort()).toEqual([...bindings].sort());
    // A place in the secret order for each Worker that holds a secret, and only for those.
    expect([...platform.secret_order.workers].sort()).toEqual(WORKERS.filter((worker) =>
      ((configOf(worker)["secrets"] as { required?: string[] } | undefined)?.required ?? [])
        .length > 0).sort());
    // The founder's setup makes only secrets a config requires; the keyrings are its own.
    expect(Object.keys(MADE).filter((name) => !SECRET_INPUTS.includes(name))).toEqual([]);
    expect(KEYRINGS.length).toBeGreaterThan(0);
    // The shared apps' signing secrets, which no config requires, ride on the doors.
    expect(OPTIONAL.filter((name) => SECRET_INPUTS.includes(name))).toEqual([]);
    expect(OPTIONAL.every((name) => SECRETS["doors"]!.includes(name))).toBe(true);
  });

  it("deploys the checked-in config with only its ids, the upstream and the routes filled", () => {
    for (const worker of WORKERS) {
      const checked = configOf(worker);
      const deployed = deployedConfig(worker, IDS, UPSTREAM, false);
      const { kv_namespaces: kv, main, vars, assets, build, $schema: _schema, ...rest } =
        checked as Json;
      const { kv_namespaces: deployedKv, main: deployedMain, vars: deployedVars,
              assets: deployedAssets, build: deployedBuild, ...others } = deployed;
      expect([worker, others]).toEqual([worker, rest]);
      expect(String(deployedMain)).toBe(join(PLATFORM, worker, String(main)));
      // Written outside the Worker's folder: the app files' directory is absolute too.
      expect(deployedAssets).toEqual(assets === undefined ? undefined : {
        ...(assets as Json), directory: join(PLATFORM, worker, String((assets as Json)["directory"])) });
      // And a custom build runs in the Worker's own folder, not wrangler's.
      expect(deployedBuild).toEqual(build === undefined ? undefined : {
        ...(build as Json), cwd: join(PLATFORM, worker, String((build as Json)["cwd"] ?? ".")) });
      expect(deployedKv).toEqual(((kv ?? undefined) as { binding: string }[] | undefined)
        ?.map((namespace) => ({ ...namespace, id: IDS[namespace.binding] })));
      expect(deployed["routes"]).toBeUndefined();
      if (worker === "doors") {
        expect(deployedVars).toEqual({ ...(vars as Json), OWNER_ISSUER: UPSTREAM.issuer,
                                       UPSTREAM: JSON.stringify(UPSTREAM.record) });
      } else {
        expect(deployedVars).toEqual(vars);
      }
    }
    const routed = deployedConfig("doors", IDS, UPSTREAM, true)["routes"];
    expect(routed).toEqual([...ROUTES.map((pattern) => ({ pattern, zone_name: ZONE })),
                            { pattern: EGRESS_DOMAIN, custom_domain: true }]);
  });

  it("bundles every Worker from the config it stages, as wrangler deploys it", { tags: ["large"] },
     () => {
    // The kit writes each config outside its Worker's folder (`deployedConfig`), so a path
    // that stays relative resolves elsewhere; the first dark run stopped on the sandbox's
    // custom build for exactly that. Only a real bundle of the staged config shows it.
    const out = mkdtempSync(join(tmpdir(), "ta-staged-"));
    try {
      for (const worker of WORKERS) {
        const config = join(out, `${worker}.json`);
        writeFileSync(config, JSON.stringify(deployedConfig(worker, IDS, UPSTREAM, true)));
        execFileSync(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir",
                                        join(out, worker), "--config", config],
                     { cwd: PLATFORM, stdio: "pipe",
                       env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
      }
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("writes a control record its gates read, every outside door closed", () => {
    const control = parseControl(DARK_CONTROL);
    expect(control).toMatchObject({ outside: "off", ordinary: "canary", canary: [],
                                    inbound: false });
    expect(control?.tiers).not.toBeNull();
    // The tests' dark record is this one, the founder's tier numbers with it.
    expect(DARK_CONTROL).toEqual(DARK);
  });
});

describe("the command line", () => {
  const w = world();

  it("takes one mode, by --mode, and refuses a run with none or another", () => {
    expect(Object.keys(MODES)).toEqual(["plan", "dark", "probe", "routes", "h2", "pair",
                                        "handback"]);
    for (const mode of Object.keys(MODES)) {
      const record = mode === "handback" ? ["--record", "7001"] : [];
      expect(parse(["--mode", mode, ...record], ENV, w.io).mode).toBe(mode);
    }
    for (const argv of [[], ["--mode"], ["--mode", "apply"], ["--mode", "constructor"],
                        ["--yes", "--routes"], ["--mode", "--yes"]]) {
      expect(() => parse(argv, ENV, w.io), argv.join(" ")).toThrow(
        "--mode is one of plan, dark, probe, routes, h2, pair, handback");
    }
    for (const argv of [["--mode", "dark", "--mode", "routes"], ["--mode", "dark", "--mode", "dark"]]) {
      expect(() => parse(argv, ENV, w.io), argv.join(" ")).toThrow(
        "--mode is given once: a run is one mode");
    }
  });

  it("holds only the planning modes to their options: h2 and pair read none", () => {
    for (const mode of ["h2", "pair"]) {
      const run = parse(["--mode", mode, "--public", "--yes", "--rotate", "FILES_KEYRING",
                         "--migrate", "doors"], ENV, w.io);
      expect([run.mode, run.reach, run.apply]).toEqual([mode, "public", true]);
    }
  });

  it("refuses a run that would change what its job cannot", () => {
    expect(() => parse(["--mode", "dark", "--public", "--yes"], ENV, w.io))
      .toThrow("--public is the plan job's: it never changes anything");
    expect(() => parse(["--mode", "plan", "--yes"], ENV, w.io))
      .toThrow("mode plan changes nothing: it takes no --yes");
    expect(() => parse(["--mode", "dark", "--migrate", "owner,doors"], ENV, w.io))
      .toThrow("the doors hold no Durable Object migration: --migrate never names them");
    for (const option of ["--rotate", "--migrate"]) {
      expect(() => parse(["--mode", "probe", option, "owner"], ENV, w.io))
        .toThrow("mode probe deploys nothing: it takes no --rotate or --migrate");
    }
    const run = parse(["--mode", "routes", "--yes", "--rotate", "ROUTING_MAC_KEY,FILES_KEYRING",
                       "--migrate", "owner"], ENV, w.io);
    expect([run.reach, run.apply, [...run.rotate], [...run.migrate]])
      .toEqual(["secrets", true, ["ROUTING_MAC_KEY", "FILES_KEYRING"], ["owner"]]);
    expect(parse(["--mode", "plan", "--public"], ENV, w.io).reach).toBe("public");
  });
});

describe("a dark run", () => {
  it("checks everything before any change, then storage, secrets, Workers, the control " +
     "record and the hook, then the canary", async () => {
    const w = world();
    const work = await runMode(w, "dark", ["--yes"]);
    // In this order: every check (the locks, the zone, the discovery, the template and the
    // pair, the hook's listing, the redirect URI's registration and the early sign-in check),
    // then the namespaces, the bucket and the queues, the secrets, the Workers, the control
    // record, the hook, and the canary.
    const at = order(w.events, [
      "guard owner", "guard sandbox", "cf GET /zones?name=tinyassets.io", "issuer discovery",
      "the tree's image", "e2b GET /templates/t1/tags/exists", "bundle the runner and the probes",
      "e2b GET /templates/t1/tags/exists",
      "e2b GET /events/webhooks", "workos GET redirect_uris", "workos POST redirect_uris",
      "workos authorize", "cf GET /accounts", "wrangler kv namespace create",
      "wrangler r2 bucket create tinyassets-files", "wrangler queues create tinyassets-sandbox-ring",
      "wrangler queues create tinyassets-erasure", "wrangler secret put ROUTING_MAC_KEY",
      "wrangler deploy --config files/", "wrangler deploy --config doors/", "cf PUT /accounts",
      "e2b POST /events/webhooks", "log Changed:", "python3 cutover_handles_canary.py --side auto",
      "log H2's probes wait"]);
    const first = w.events.findIndex((event) =>
      /^(wrangler [a-z ]*create|wrangler secret|cf PUT)/.test(event));
    expect(at[11]).toBeLessThan(first);
    // The webhook is registered disabled, with the doors' secret, after the doors hold it.
    expect(w.events[at[21]!]).toContain('"enabled":false');
    expect(w.events[at[21]!]).toContain('"signatureSecret":"hook-secret"');
    // The plan is reported whole, every step's line in it, before the canary.
    expect(work.plan).toEqual(DARK_FRESH);
    expect(w.logs).toContain(summary("Changed", DARK_FRESH));
    expect(w.logs.slice(-1)).toEqual([
      "H2's probes wait for a routed door and a probe owner's key: run with --mode h2 " +
      "(h2-probes.ts names what it needs)."]);
    // Every wrangler command is handed the zone's account, never wrangler's own pick.
    expect(w.accounts.length).toBeGreaterThan(0);
    expect(new Set(w.accounts)).toEqual(new Set([ACCOUNT]));
    // Outside Actions, nothing is masked.
    expect(w.logs.some((line) => line.startsWith("::add-mask::"))).toBe(false);
  });

  it("in an Actions job masks the zone's account before anything can print it", async () => {
    const w = world();
    await runMode(w, "dark", ["--yes"], { ...ENV, GITHUB_ACTIONS: "true" });
    const zone = w.events.indexOf("cf GET /zones?name=tinyassets.io");
    const mask = w.events.indexOf(`log ::add-mask::${ACCOUNT}`);
    // Masked right after the zone names it: the first line that holds the account at all,
    // and before any wrangler command, whose errors quote /accounts/<id>/.
    expect([zone, mask]).toEqual([zone, zone + 1]);
    expect(w.events.findIndex((event) => event.includes(ACCOUNT))).toBe(mask);
    expect(mask).toBeLessThan(w.events.findIndex((event) => event.startsWith("wrangler")));
    expect(w.logs.filter((line) => line.startsWith("::add-mask::"))).toEqual(
      [`::add-mask::${ACCOUNT}`]);
  });

  it("stops before anything without an input it reads, naming where it comes from",
     async () => {
    const { WORKOS_API_KEY: _key, ...secretless } = ENV;
    const w = world();
    await expect(runMode(w, "dark", ["--yes"], secretless)).rejects.toThrow(
      "WORKOS_API_KEY is not set (the production environment's secrets)");
    const { OWNER_ISSUER: _issuer, ...varless } = PUBLIC;
    await expect(runMode(w, "dark", ["--public"], varless)).rejects.toThrow(
      "OWNER_ISSUER is not set (the workflow's inputs)");
    expect(w.events).toEqual([]);
  });

  it("stops before anything when a keyring is one the Workers would refuse", async () => {
    const w = world();
    await expect(runMode(w, "dark", ["--yes"], { ...ENV, FILES_KEYRING: "{}" }))
      .rejects.toThrow("FILES_KEYRING is {active, keys} as JSON");
    // A missing input is named before a keyring is read.
    const { WORKOS_API_KEY: _key, ...secretless } = ENV;
    await expect(runMode(w, "dark", ["--yes"], { ...secretless, FILES_KEYRING: "{}" }))
      .rejects.toThrow("WORKOS_API_KEY is not set (the production environment's secrets)");
    expect(w.events).toEqual([]);
  });

  it("never makes a secret's value: each it sets is the production environment's own",
     async () => {
    const w = world();
    await runMode(w, "dark", ["--yes"]);
    const expected = Object.fromEntries(Object.entries(SECRETS).flatMap(([worker, names]) =>
      names.map((name) => [`${worker}:${name}`, ENV[name]!])));
    expect(w.secretsPut).toEqual(expected);
  });

  it("deletes each retired Worker the account's Workers listing holds once every Worker is " +
     "deployed, never forced, and plans it without --yes", async () => {
    const line = (name: string) => `delete the retired Worker ${name} and its Durable ` +
      "Objects (no Worker of this tree binds it)";
    const deleting = (name: string) => `cf DELETE /accounts/${ACCOUNT}/workers/scripts/${name}`;
    const retiring = (plan: readonly string[]) =>
      plan.filter((each) => each.startsWith("delete the retired Worker "));
    const planned = world({ retired: true });
    expect(retiring((await runMode(planned, "plan")).plan)).toEqual(RETIRED.map(line));
    expect(planned.events.filter((event) => event.startsWith("cf DELETE"))).toEqual([]);
    const w = world({ retired: true });
    const work = await runMode(w, "dark", ["--yes"]);
    expect(retiring(work.plan)).toEqual(RETIRED.map(line));
    expect(w.events.filter((event) => event.startsWith("cf DELETE")))
      .toEqual(RETIRED.map(deleting));
    const deployed = Math.max(...w.events.map((event, at) =>
      /^wrangler (deploy|versions deploy) /.test(event) ? at : -1));
    const control = w.events.findIndex((event) => event.startsWith("cf PUT"));
    for (const name of RETIRED) {
      expect(w.events.indexOf(deleting(name))).toBeGreaterThan(deployed);
      expect(w.events.indexOf(deleting(name))).toBeLessThan(control);
      // Its existence is the account's Workers listing, never its secrets.
      expect(w.events).not.toContain(
        `cf GET /accounts/${ACCOUNT}/workers/scripts/${name}/secrets`);
    }
    // Gone, the next run plans none; a fresh account never held them.
    expect(retiring((await runMode(w, "dark", ["--yes"])).plan)).toEqual([]);
    expect(retiring((await runMode(world(), "plan")).plan)).toEqual([]);
  });

  it("stops loudly when Cloudflare refuses a retired Worker's delete, before the control " +
     "record", async () => {
    const w = world({ retired: true, refusingDelete: true });
    const ran = await runMode(w, "dark", ["--yes"]).then(() => "ran",
                                                           (error: Error) => error.message);
    // With nothing retired there is nothing to refuse, and the run goes on.
    expect(ran).toBe(RETIRED.length === 0 ? "ran"
      : `deleting the Worker ${RETIRED[0]} failed (400, [10064])`);
    expect(w.events.some((event) => event.startsWith("cf PUT"))).toBe(RETIRED.length === 0);
  });

  it("plans the same steps without --yes, and changes nothing", async () => {
    const w = world();
    const work = await runMode(w, "plan");
    expect(w.events.filter((event) => /^(wrangler [a-z ]*(create|put|deploy|upload)|cf (PUT|POST|DELETE)|e2b (POST|PATCH)|workos POST|python3)/
      .test(event))).toEqual([]);
    expect(work.plan.filter((line) => line.startsWith("create the") || line.startsWith("set the")
      || line.startsWith("deploy "))).toEqual(DARK_FRESH.filter((line) =>
      line.startsWith("create the") || line.startsWith("set the") || line.startsWith("deploy ")));
    expect(w.logs.slice(-1)).toEqual(["\nNothing was changed."]);
  });

  it("in the plan job reaches no E2B and no WorkOS key, and says what the apply job checks",
     async () => {
    const w = world({ held: true });
    const work = await runMode(w, "dark", ["--public"], PUBLIC);
    expect(w.events.filter((event) => event.startsWith("e2b") || event.startsWith("workos GET")
      || event.startsWith("workos POST"))).toEqual([]);
    expect(work.plan.filter((line) => line.endsWith("checked by the apply job before any change")))
      .toEqual([`E2B's ready build of ${TEMPLATE}: checked by the apply job before any change`,
                `the pair ${PAIR} passed its standing checks: checked by the apply job before ` +
                "any change",
                `WorkOS redirect URI ${CALLBACK}: checked by the apply job before any change`,
                `E2B lifecycle webhook ${E2B_HOOK.url}: checked by the apply job before any change`]);
    // With none held, the control record is planned, and the hook's word comes after it, last.
    const fresh = await runMode(world(), "dark", ["--public"], PUBLIC);
    const [last2, last] = fresh.plan.slice(-2);
    expect([last2?.startsWith("write the control record closed"), last]).toEqual([
      true, `E2B lifecycle webhook ${E2B_HOOK.url}: checked by the apply job before any change`]);
  });

  it("deploys a held Worker as a version, and a named migration whole", async () => {
    const w = world({ held: true });
    await runMode(w, "dark", ["--yes", "--migrate", "owner"]);
    expect(w.events.filter((event) => event.startsWith("wrangler deploy")
      || event.startsWith("wrangler versions upload"))).toEqual([
      ...["codemode", "files", "gateway", "sandbox"].map((worker) =>
        `wrangler versions upload --config ${worker}/wrangler.json`),
      "wrangler deploy --config owner/wrangler.json",
      "wrangler versions upload --config doors/wrangler.json"]);
    // Nothing held is made again, and the control record is left alone.
    expect(w.events.filter((event) => /create|cf PUT|secret put/.test(event))).toEqual([]);
  });

  it("stops before any change when a migration lock does not pin what the code ships",
     async () => {
    const w = world({ lock: { "owner/migrations.json": ["a".repeat(64)] } });
    await expect(runMode(w, "dark", ["--yes"])).rejects.toThrow(/PIN_MIGRATIONS/);
    expect(w.events.filter((event) => !event.startsWith("guard"))).toEqual([]);
  });

  it("makes the import window's queue, dead-letter queue and bucket, each only when its " +
     "listing lacks it, in a fresh, a held and a partial account", async () => {
    const WINDOW = ["r2 bucket create tinyassets-import-staging",
                    "queues create tinyassets-import", "queues create tinyassets-import-dlq"];
    const made = (w: ReturnType<typeof world>) => w.events
      .filter((event) => /^wrangler (r2 bucket|queues) create /.test(event))
      .map((event) => event.slice("wrangler ".length));
    expect(BUCKETS).toEqual(["tinyassets-files", "tinyassets-import-staging",
                             "tinyassets-final-backup"]);
    expect(QUEUES).toEqual(["tinyassets-sandbox-ring", "tinyassets-erasure", "tinyassets-import",
                            "tinyassets-import-dlq"]);
    const fresh = world();
    await runMode(fresh, "dark", ["--yes"]);
    expect(made(fresh)).toEqual(expect.arrayContaining(WINDOW));
    expect(made(fresh).filter((each) => each.includes("tinyassets-import"))).toEqual(WINDOW);
    const held = world({ held: true });
    await runMode(held, "dark", ["--yes"]);
    expect(made(held)).toEqual([]);
    // The queue made but not its dead-letter queue nor the bucket: only those two are made.
    const partial = world({ held: true, listed: {
      buckets: ["tinyassets-files", "tinyassets-final-backup"],
      queues: ["tinyassets-sandbox-ring", "tinyassets-erasure", "tinyassets-import"] } });
    const work = await runMode(partial, "dark", ["--yes"]);
    expect(made(partial)).toEqual(["r2 bucket create tinyassets-import-staging",
                                   "queues create tinyassets-import-dlq"]);
    expect(work.plan).toEqual(expect.arrayContaining([
      "create the R2 bucket tinyassets-import-staging", "create the queue tinyassets-import-dlq"]));
    expect(work.plan).not.toContain("create the queue tinyassets-import");
  });

  it("makes the off-site copy's bucket, which no Worker binds, only when its listing lacks it",
     async () => {
    const made = (w: ReturnType<typeof world>) => w.events
      .filter((event) => /^wrangler r2 bucket create /.test(event))
      .map((event) => event.slice("wrangler ".length));
    expect([...CONFIGS.values()].flatMap((config) =>
      ((config["r2_buckets"] ?? []) as { bucket_name: string }[]).map((each) => each.bucket_name)))
      .not.toContain("tinyassets-final-backup");
    const fresh = world();
    await runMode(fresh, "dark", ["--yes"]);
    expect(made(fresh)).toContain("r2 bucket create tinyassets-final-backup");
    const held = world({ held: true });
    await runMode(held, "dark", ["--yes"]);
    expect(made(held)).toEqual([]);
    const lacking = world({ held: true, listed: {
      buckets: ["tinyassets-files", "tinyassets-import-staging"],
      queues: ["tinyassets-sandbox-ring", "tinyassets-erasure", "tinyassets-import",
               "tinyassets-import-dlq"] } });
    const work = await runMode(lacking, "dark", ["--yes"]);
    expect(made(lacking)).toEqual(["r2 bucket create tinyassets-final-backup"]);
    expect(work.plan).toContain("create the R2 bucket tinyassets-final-backup");
    // The plan job lists it and makes nothing.
    const plan = world({ held: true, listed: {
      buckets: ["tinyassets-files", "tinyassets-import-staging"],
      queues: ["tinyassets-sandbox-ring", "tinyassets-erasure", "tinyassets-import",
               "tinyassets-import-dlq"] } });
    const planned = await runMode(plan, "dark", []);
    expect(made(plan)).toEqual([]);
    expect(planned.plan).toContain("create the R2 bucket tinyassets-final-backup");
  });

  it("stops on a failed wrangler listing, never reading it as absent", async () => {
    const w = world({ failing: "r2 bucket list" });
    await expect(runMode(w, "dark", ["--yes"])).rejects.toThrow(
      "wrangler r2 bucket list failed (exit 1): the API refused it");
    expect(w.events.some((event) => event.includes("create tinyassets-files"))).toBe(false);
  });

  it("stops when the pair has not passed, and plans only the pair job's check without --yes",
     async () => {
    await expect(runMode(world({ paired: false }), "dark", ["--yes"])).rejects.toThrow(
      `the pair ${PAIR} has not passed its standing checks in this E2B team: run the pair job ` +
      "(--mode pair) first; nothing is deployed");
    const work = await runMode(world({ paired: false }), "plan");
    expect(work.plan).toContain(`the pair ${PAIR} has not passed in this E2B team: the pair ` +
                                "job runs its check (--mode pair) before the apply job");
  });
});

describe("a routes run", () => {
  it("hands the routes over after every Worker, then the custom domain, then enables the hook",
     async () => {
    const w = world({ held: true });
    const work = await runMode(w, "routes", ["--yes"]);
    order(w.events, ["e2b GET /events/webhooks", "cf GET /zones/z/workers/routes",
                     "cf GET /accounts", "wrangler versions deploy",
                     "cf PUT /zones/z/workers/routes/r1", "cf POST /zones/z/workers/routes",
                     "cf DELETE /zones/z/workers/routes/r4", "wrangler triggers deploy",
                     "e2b PATCH /events/webhooks/w1 {\"enabled\":true}", "log Changed:",
                     "python3 cutover_handles_canary.py --side new --unkeyed",
                     "log H2's probes wait"]);
    // The routes are read once, before any change; the doors' last version is deployed before
    // a route moves; the hook is enabled only after the last route and the custom domain.
    expect(w.events.filter((event) => event === "cf GET /zones/z/workers/routes")).toHaveLength(1);
    expect(w.events.findLastIndex((event) => event.startsWith("wrangler versions deploy")))
      .toBeLessThan(w.events.indexOf("cf PUT /zones/z/workers/routes/r1"));
    expect(work.plan).toEqual(ROUTES_HELD);
    expect(w.logs).toContain(summary("Changed", ROUTES_HELD));
    // Every wrangler command, the routes' triggers deploy too, is handed the zone's account.
    expect(w.accounts.length).toBeGreaterThan(0);
    expect(new Set(w.accounts)).toEqual(new Set([ACCOUNT]));
    for (const path of ["tinyassets.io/mcp", "tinyassets.io/api/x", "tinyassets.io/oauth/token",
                        "tinyassets.io/.well-known/oauth-protected-resource/mcp",
                        `tinyassets.io${E2B_HOOK_PATH}`]) {
      expect([path, servedBy(w.routes.values(), path)]).toEqual([path, DOORS]);
    }
  });

  it("reads the zone's routes with the checks, before WorkOS's, storage or any change",
     async () => {
    const w = world({ held: true });
    await runMode(w, "routes", ["--yes"]);
    const read = w.events.indexOf("cf GET /zones/z/workers/routes");
    expect(read).toBeGreaterThan(w.events.indexOf("e2b GET /events/webhooks"));
    expect(read).toBeLessThan(w.events.indexOf("workos GET redirect_uris"));
    expect(read).toBeLessThan(w.events.findIndex((event) =>
      event.startsWith("cf GET /accounts")));
  });

  it("in the plan job says the hook waits, where dark's would be", async () => {
    const work = await runMode(world({ held: true }), "routes", ["--public"], PUBLIC);
    const hook = work.plan.findIndex((line) => line.startsWith("E2B lifecycle webhook"));
    expect(work.plan[hook - 1]).toMatch(/^upload and deploy a version of tinyassets-doors/);
    expect(work.plan[hook + 1]).toMatch(/^move the route /);
  });
});

/** A workflow run's own environment: its run id and attempt, which a route record keys on. */
const RUN = { ...ENV, GITHUB_RUN_ID: "7001", GITHUB_RUN_ATTEMPT: "1" };
const KEY = "deploy:routes:7001";
const CONTROL_NS = `ns-${NAMESPACES["CONTROL"]}`;
const RECORD_AT = `/accounts/${ACCOUNT}/storage/kv/namespaces/${CONTROL_NS}/values/${
  encodeURIComponent(KEY)}`;
/** What changes the account, WorkOS, E2B or the zone: every write the kit can make. */
const CHANGE = /^(wrangler (kv namespace create|r2 bucket create|queues create|secret put|deploy|versions upload|versions deploy|triggers deploy)|cf (PUT|POST|DELETE) |e2b (POST|PATCH)|workos POST)/;
const ROUTE_CHANGE = /^cf (PUT|POST|DELETE) \/zones\/z\/workers\/routes/;

/** A zone's routes as a record keeps them, by pattern: ids aside. */
function kept(routes: Iterable<{ pattern: string; script?: string | null }>) {
  return [...routes].map((route) => ({ pattern: route.pattern, script: route.script ?? null }))
    .sort((a, b) => a.pattern < b.pattern ? -1 : 1);
}

/** The record `w` holds under `key`. */
function stored(w: ReturnType<typeof world>, key = KEY): RouteRecord {
  return JSON.parse(w.kv.get(`${CONTROL_NS}/${key}`)!) as RouteRecord;
}

/** A held account cut over: routes run whole, its record kept. */
async function cutOver() {
  const w = world({ held: true });
  const before = kept(w.routes.values());
  await runMode(w, "routes", ["--yes"], RUN);
  expect(handover([...w.routes.values()], DOORS, ROUTES)).toEqual([]);
  w.tables.length = 0;
  return { w, before, from: w.events.length };
}

describe("the route record", () => {
  it("is kept before the first change, the zone's routes and the handover's changes, and the " +
     "job summary names it", async () => {
    const out = mkdtempSync(join(tmpdir(), "ta-summary-"));
    try {
      const file = join(out, "summary.md");
      const w = world({ held: true });
      const table = [...w.routes.values()];
      const work = await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_STEP_SUMMARY: file });
      const read = w.events.indexOf("cf GET /zones/z/workers/routes");
      const write = w.events.indexOf(`cf PUT ${RECORD_AT}`);
      const first = w.events.findIndex((event) => CHANGE.test(event)
        && event !== `cf PUT ${RECORD_AT}`);
      // Read, recorded, and only then anything changed: WorkOS's check comes after it too.
      expect([read > -1, write > read, first > write]).toEqual([true, true, true]);
      expect(write).toBeLessThan(w.events.indexOf("workos GET redirect_uris"));
      expect(w.events.findIndex((event) => ROUTE_CHANGE.test(event))).toBeGreaterThan(write);
      expect(stored(w)).toEqual({
        kind: "tinyassets-routes", zone: ZONE, zone_id: "z", run: "7001", attempt: "1",
        at: NOW.toISOString(),
        routes: table.map((route) => ({ pattern: route.pattern, script: route.script ?? null })),
        moved: handover(table, DOORS, ROUTES).map((each) =>
          ({ method: each.method, pattern: each.pattern })),
        // The attach's own DNS record, written in once the attach is done.
        domain: { hostname: EGRESS_DOMAIN, service: null, created: [{ id: "dns-egress", type: "AAAA", content: "100::" }] },
        hook: { url: E2B_HOOK.url, present: true, enabled: false } });
      // It holds no secret, and not the account.
      const text = w.kv.get(`${CONTROL_NS}/${KEY}`)!;
      for (const secret of [ACCOUNT, ...[...SECRET_INPUTS, ...OPTIONAL, "CLOUDFLARE_API_TOKEN"]
        .map((name) => ENV[name]).filter((value) => value !== undefined)]) {
        expect(text).not.toContain(secret);
      }
      // The plan says how to hand it back, and the summary holds the record too.
      expect(work.plan).toContain(`record the zone's ${table.length} routes and the handover's 8 ` +
        "changes, the egress domain (none) and E2B's lifecycle webhook (disabled) as " +
        `${KEY} in tinyassets-control, before any change: hand back with --mode handback ` +
        "--record 7001");
      const summaryText = readFileSync(file, "utf8");
      expect(summaryText).toContain(`Route record ${KEY}:`);
      // The copy is the record as first written, before the attach's DNS record joined it.
      expect(summaryText).toContain(JSON.stringify({ ...stored(w), domain: {
        ...stored(w).domain, created: null } }, null, 2));
      expect(summaryText).toContain("--mode handback --record 7001");
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("stops routes before any change while the egress host is another Worker's or holds a " +
     "DNS record, which the custom domain would replace", async () => {
    for (const [options, why] of [
      [{ egress: "someone-else" }, `${EGRESS_DOMAIN} is someone-else's custom domain: routes ` +
       "would take it, and a handback could not give it back; nothing is changed"],
      [{ dns: ["A", "AAAA"] }, `${EGRESS_DOMAIN} holds 2 DNS record(s) (A, AAAA): the doors' ` +
       "custom domain would replace them, and a handback could not give them back; nothing is " +
       "changed"],
    ] as const) {
      const w = world({ held: true, ...options });
      await expect(runMode(w, "routes", ["--yes"], RUN)).rejects.toThrow(why);
      expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
      // The plan job reads them too, so it shows the stop before the apply job.
      await expect(runMode(world({ held: true, ...options }), "routes", ["--public"], PUBLIC))
        .rejects.toThrow(why);
    }
  });

  it("stops routes before any change when it cannot be kept", async () => {
    // No control namespace: no dark run came first.
    const fresh = world();
    await expect(runMode(fresh, "routes", ["--yes"], RUN)).rejects.toThrow(
      "routes keeps its route record in tinyassets-control, which a dark run makes: run dark " +
      "first; nothing is changed");
    expect(fresh.events.filter((event) => CHANGE.test(event))).toEqual([]);
    const planned = await runMode(world(), "routes", [], RUN);
    expect(planned.plan).toContain(`record the zone's routes as ${KEY}: tinyassets-control is ` +
                                   "not made yet, so the apply job stops until a dark run makes it");
    // Cloudflare refuses the write.
    const refused = world({ held: true, refusing: KEY });
    await expect(runMode(refused, "routes", ["--yes"], RUN)).rejects.toThrow(
      `writing the route record ${KEY} failed (500): nothing is changed`);
    expect(refused.events.filter((event) => CHANGE.test(event))).toEqual([`cf PUT ${RECORD_AT}`]);
    // The key holds something that is not a record.
    const taken = world({ held: true });
    taken.kv.set(`${CONTROL_NS}/${KEY}`, JSON.stringify({ outside: "off" }));
    await expect(runMode(taken, "routes", ["--yes"], RUN)).rejects.toThrow(
      `the record ${KEY} is not a route record: nothing is changed`);
    expect(taken.events.filter((event) => CHANGE.test(event))).toEqual([]);
  });

  it("is kept as its first attempt wrote it on a re-run, and a later run's own changes nothing " +
     "when handed back", async () => {
    const w = world({ held: true });
    w.kill.after = 3;
    await expect(runMode(w, "routes", ["--yes"], RUN)).rejects.toThrow("the run was killed");
    const first = stored(w);
    w.kill.after = undefined;
    const again = await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ATTEMPT: "2" });
    // As the first attempt wrote it, with only the attach's DNS record added once it is done.
    expect(first.domain.created).toBeNull();
    expect(stored(w)).toEqual({ ...first, domain: { ...first.domain, created: [{ id: "dns-egress", type: "AAAA", content: "100::" }] } });
    expect(again.plan).toContain(`keep the route record ${KEY} as attempt 1 wrote it at ${
      NOW.toISOString()}, before its first change: hand back with --mode handback --record 7001`);
    const later = await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ID: "7002" });
    expect(later.plan.find((line) => line.startsWith("record "))).toBe(`record the zone's 11 routes and the handover's 0 changes, the ` +
      `egress domain (${DOORS}) and E2B's lifecycle webhook (enabled) as deploy:routes:7002 in ` +
      "tinyassets-control, before any change: hand back with --mode handback --record 7002; " +
      `${DOORS} holds ${ROUTES.filter((pattern) => !PROBE_ROUTES.includes(pattern)).join(", ")} ` +
      "already: the record of the routes run that moved them is the one to hand back, and it " +
      "covers this run's changes too");
    const at = w.events.length;
    await runMode(w, "handback", ["--yes", "--record", "7002"]);
    expect(w.events.slice(at).filter((event) => CHANGE.test(event))).toEqual([]);
  });

  it("names, when an earlier routes run moved some of the doors' routes, that its record is " +
     "the one to hand back", async () => {
    const w = world({ held: true });
    w.kill.after = 1;
    await expect(runMode(w, "routes", ["--yes"], RUN)).rejects.toThrow("the run was killed");
    w.kill.after = undefined;
    const fresh = await runMode(w, "routes", [], { ...RUN, GITHUB_RUN_ID: "7002" });
    expect(fresh.plan).toContain("record the zone's 8 routes and the handover's 7 changes, the " +
      "egress domain (none) and E2B's lifecycle webhook (disabled) as deploy:routes:7002 in " +
      "tinyassets-control, before any change: hand back with --mode " +
      `handback --record 7002; ${DOORS} holds tinyassets.io/mcp* already: the record of the ` +
      "routes run that moved them is the one to hand back, and it covers this run's changes too");
    // The first run's record does: its handback, after the second run, puts the zone back.
    const before = kept(stored(w).routes);
    await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ID: "7002" });
    await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(kept(w.routes.values())).toEqual(before);
  });
});

describe("a handback run", () => {
  it("puts the recorded routes back exactly, only what the handover changed, no path the " +
     "record served ever unserved, then reads the zone back", async () => {
    const { w, before, from } = await cutOver();
    const work = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(kept(w.routes.values())).toEqual(before);
    const ran = w.events.slice(from);
    // The hook disabled and the domain detached, as before routes, then the routes.
    expect(w.hooks).toEqual([expect.objectContaining({ id: "w1", enabled: false })]);
    expect([...w.domains.values()]).toEqual([]);
    expect(ran.filter((event) => /^(wrangler|workos)/.test(event))).toEqual([]);
    expect(ran.filter((event) => event.startsWith("e2b"))).toEqual([
      "e2b GET /events/webhooks", 'e2b PATCH /events/webhooks/w1 {"enabled":false}',
      "e2b GET /events/webhooks"]);
    const [disabled, detached, first] = order(ran, ["e2b PATCH /events/webhooks/w1",
      `cf DELETE /accounts/${ACCOUNT}/workers/domains/d1`, "cf POST /zones/z/workers/routes"]);
    expect([disabled! < detached!, detached! < first!]).toEqual([true, true]);
    const reads = ran.flatMap((event, at) => event === "cf GET /zones/z/workers/routes" ? [at] : []);
    const changes = ran.flatMap((event, at) => ROUTE_CHANGE.test(event) ? [at] : []);
    expect(reads).toHaveLength(2);
    expect([reads[0]! < disabled!, reads[1]! > changes.at(-1)!]).toEqual([true, true]);
    // The canary waits for the old stack's restart, and the run says where it runs.
    order(ran, ["log Changed:", "log The handles canary (Fact 11) waits for the old stack"]);
    expect(w.logs.at(-1)).toContain(
      "dispatch the Handles canary workflow (handles-canary.yml) with side old");
    expect(ran.some((event) => event.startsWith("python3"))).toBe(false);
    // Every path the record served is served after each change.
    const served = PROBES.filter((path) => servedBy(before as Route[], path) !== undefined);
    for (const table of w.tables) {
      for (const path of served) {
        expect([path, servedBy(table, path)]).not.toEqual([path, undefined]);
      }
    }
    expect(work.plan).toEqual([
      `the route record ${KEY}: the zone's ${before.length} routes as run 7001 read them at ${
        NOW.toISOString()}, and the handover's 8 changes`,
      `disable E2B lifecycle webhook ${E2B_HOOK.url}, as it was before routes`,
      `detach the custom domain ${EGRESS_DOMAIN} from ${DOORS}, as it was before routes`,
      "add the route tinyassets.io/.well-known/oauth-protected-resource* back for " +
      "tinyassets-mcp-proxy",
      "add the route tinyassets.io/.well-known/oauth-authorization-server* back for " +
      "tinyassets-mcp-proxy",
      `move the route tinyassets.io/mcp* from ${DOORS} back to tinyassets-mcp-proxy`,
      ...["api/*", "hooks/inbound/*", "hooks/e2b/*", "app/api/*", ".well-known/oauth-*"].map(
        (path) => `delete ${DOORS}'s route tinyassets.io/${path}, which the handover added`),
      `delete the DNS record(s) the custom domain made, AAAA ${EGRESS_DOMAIN} (100::), where ` +
      "the detach leaves them",
      "the zone's routes, the egress domain and the webhook read back as the record has them",
    ]);
    // The probe's routes, the doors' before the handover, stay theirs.
    for (const pattern of PROBE_ROUTES) {
      expect(before).toContainEqual({ pattern, script: DOORS });
    }
    // Run again, it changes nothing.
    const at = w.events.length;
    const again = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(w.events.slice(at).filter((event) => ROUTE_CHANGE.test(event))).toEqual([]);
    expect(again.plan.slice(1)).toEqual([
      `E2B lifecycle webhook ${E2B_HOOK.url}: not enabled already`,
      `the custom domain ${EGRESS_DOMAIN}: detached already`,
      "the zone's routes are the record's already: nothing to hand back",
      `delete the DNS record(s) the custom domain made, AAAA ${EGRESS_DOMAIN} (100::), where ` +
      "the detach leaves them",
      "the zone's routes, the egress domain and the webhook read back as the record has them"]);
  });

  // Ten outward changes each way: routes' eight route changes, the custom domain and the hook;
  // the handback's hook, domain and eight route changes.
  for (const [kill, keepsDns] of [...[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((at) => [at, false]),
                                  ...[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((at) => [at, true])] as
                                 [number, boolean][]) {
    it(`converges when killed after ${kill} outward changes, routes and the handback both${
      keepsDns ? ", the detach leaving the DNS record" : ""}`, async () => {
      const w = world({ held: true, keepsDns });
      const dns = JSON.stringify(w.dns);
      const before = kept(w.routes.values());
      const hooks = JSON.stringify(w.hooks);
      // Routes makes ten outward changes, so it is killed only at those.
      if (kill < 10) {
        w.kill.after = kill;
        await expect(runMode(w, "routes", ["--yes"], RUN)).rejects.toThrow("the run was killed");
        w.kill.after = undefined;
      }
      await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ATTEMPT: "2" });
      expect(handover([...w.routes.values()], DOORS, ROUTES)).toEqual([]);
      expect([w.domains.size, w.hooks[0]?.["enabled"]]).toEqual([1, true]);
      w.kill.after = kill;
      await expect(runMode(w, "handback", ["--yes", "--record", "7001"]))
        .rejects.toThrow("the run was killed");
      w.kill.after = undefined;
      await runMode(w, "handback", ["--yes", "--record", "7001"]);
      expect(kept(w.routes.values())).toEqual(before);
      expect([w.domains.size, JSON.stringify(w.hooks), JSON.stringify(w.dns)])
        .toEqual([0, hooks, dns]);
      const at = w.events.length;
      await runMode(w, "handback", ["--yes", "--record", "7001"]);
      expect(w.events.slice(at).filter((event) => CHANGE.test(event))).toEqual([]);
    });
  }

  it("refuses a missing or bad record, changing nothing", async () => {
    const missing = world({ held: true });
    await expect(runMode(missing, "handback", ["--yes", "--record", "7999"])).rejects.toThrow(
      "there is no route record deploy:routes:7999 in tinyassets-control: name the id a routes " +
      "run's job summary gives; nothing is changed");
    await expect(runMode(world(), "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      /there is no route record deploy:routes:7001/);
    const { w } = await cutOver();
    const good = stored(w);
    const route = good.routes[0]!;
    const added = good.moved.find((each) => each.method === "POST")!;
    for (const [bad, why] of [
      ["not json", "is not a route record"],
      [{ ...good, kind: "other" }, "is not a route record"],
      [{ ...good, zone: "example.com" }, "is of another zone"],
      [{ ...good, zone_id: "y" }, "is of another zone"],
      [{ ...good, routes: "all" }, "is out of shape"],
      [{ ...good, run: 7001 }, "is out of shape"],
      [{ ...good, routes: [...good.routes, { pattern: route.pattern, script: 1 }] },
       "holds a route out of shape"],
      [{ ...good, routes: [...good.routes, route] }, "holds a pattern twice"],
      [{ ...good, moved: [...good.moved, { method: "POST", pattern: route.pattern }] },
       "names a change its routes do not allow"],
      [{ ...good, moved: [...good.moved, { method: "PUT", pattern: added.pattern }] },
       "names a change its routes do not allow"],
      [{ ...good, moved: [{ method: "PATCH", pattern: route.pattern }] },
       "names a change its routes do not allow"],
      [{ ...good, domain: undefined }, "holds no egress domain in shape"],
      [{ ...good, domain: { hostname: "other.tinyassets.io", service: null } },
       "holds no egress domain in shape"],
      [{ ...good, hook: { ...good.hook, url: "https://elsewhere.test/hook" } },
       "holds no lifecycle webhook in shape"],
      [{ ...good, hook: { url: E2B_HOOK.url, present: false, enabled: true } },
       "holds no lifecycle webhook in shape"],
    ] as const) {
      w.kv.set(`${CONTROL_NS}/${KEY}`, typeof bad === "string" ? bad : JSON.stringify(bad));
      const at = w.events.length;
      await expect(runMode(w, "handback", ["--yes", "--record", "7001"]), why).rejects.toThrow(
        `the record ${KEY} ${why}`);
      expect(w.events.slice(at).filter((event) => CHANGE.test(event))).toEqual([]);
    }
  });

  it("leaves the custom domain and the hook where the record has them already the doors'",
     async () => {
    // Attached to the doors and enabled before routes: the record says so, the handback leaves
    // both, and reads back only the routes for them.
    const w = world({ held: true, egress: DOORS });
    w.hooks[0]!["enabled"] = true;
    await runMode(w, "routes", ["--yes"], RUN);
    expect([stored(w).domain, stored(w).hook]).toEqual([
      { hostname: EGRESS_DOMAIN, service: DOORS, created: null },
      { url: E2B_HOOK.url, present: true, enabled: true }]);
    const at = w.events.length;
    const work = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(work.plan.slice(1, 3)).toEqual([
      `E2B lifecycle webhook ${E2B_HOOK.url}: enabled before routes, left`,
      `the custom domain ${EGRESS_DOMAIN}: ${DOORS}'s before routes, left`]);
    expect(w.events.slice(at).filter((event) => /^(e2b PATCH|cf DELETE \/accounts)/.test(event)))
      .toEqual([]);
    expect([w.domains.size, w.hooks[0]!["enabled"]]).toEqual([1, true]);
  });

  it("disables a hook routes registered, as it was absent before: never deleted", async () => {
    const w = world({ held: true });
    w.hooks.length = 0;
    await runMode(w, "routes", ["--yes"], RUN);
    expect(stored(w).hook).toEqual({ url: E2B_HOOK.url, present: false, enabled: false });
    expect(w.hooks[0]!["enabled"]).toBe(true);
    const work = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(work.plan[1]).toBe(`disable E2B lifecycle webhook ${E2B_HOOK.url}, as it was absent ` +
                              "before routes (a disabled hook sends nothing)");
    expect(w.hooks).toEqual([expect.objectContaining({ id: "w1", enabled: false })]);
    expect(w.events.some((event) => event.startsWith("e2b DELETE"))).toBe(false);
  });

  it("stops when the hook or the custom domain reads back unlike the record", async () => {
    for (const [inert, why] of [
      ["hook", `E2B lifecycle webhook ${E2B_HOOK.url} is enabled (the record has it disabled)`],
      ["domain", `${EGRESS_DOMAIN} is ${DOORS}'s custom domain (the record has none)`],
      ["dns", `the DNS record AAAA ${EGRESS_DOMAIN} (100::) the custom domain made is still there`],
    ] as const) {
      const w = world({ held: true, keepsDns: inert === "dns" });
      await runMode(w, "routes", ["--yes"], RUN);
      w.inert.add(inert);
      await expect(runMode(w, "handback", ["--yes", "--record", "7001"]), inert).rejects.toThrow(
        `the handback read back unlike the record: ${why}`);
      // A domain still attached keeps its DNS record: only a detached one's is removed.
      expect(w.dns.some((record) => record.id === "dns-egress")).toBe(inert !== "hook");
    }
    // A custom domain another Worker took since stops the handback before any change.
    const { w, from } = await cutOver();
    w.domains.set("d1", { id: "d1", hostname: EGRESS_DOMAIN, service: "someone-else" });
    await expect(runMode(w, "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      `changed since routes, so nothing is handed back: ${EGRESS_DOMAIN} is someone-else's ` +
      `custom domain, and routes attached it to ${DOORS}. Put each back as the record has it, ` +
      "then run the handback again");
    expect(w.events.slice(from).filter((event) => CHANGE.test(event))).toEqual([]);
    expect(w.domains.get("d1")?.service).toBe("someone-else");
  });

  it("plans the reverse of only what the handover changed, never deleting an addition another " +
     "Worker holds", () => {
    const proxy = "tinyassets-mcp-proxy";
    const before = [{ pattern: "tinyassets.io/mcp*", script: proxy },
                    { pattern: "tinyassets.io/.well-known/oauth-x*", script: proxy },
                    { pattern: "tinyassets.io/site*", script: "site" }];
    const moved = [{ method: "PUT", pattern: "tinyassets.io/mcp*" },
                   { method: "POST", pattern: "tinyassets.io/api/*" },
                   { method: "POST", pattern: "tinyassets.io/hooks/*" },
                   { method: "DELETE", pattern: "tinyassets.io/.well-known/oauth-x*" }] as const;
    const routes: Route[] = [{ id: "1", pattern: "tinyassets.io/mcp*", script: DOORS },
                             { id: "2", pattern: "tinyassets.io/api/*", script: "someone-else" },
                             { id: "3", pattern: "tinyassets.io/hooks/*", script: DOORS },
                             { id: "4", pattern: "tinyassets.io/site*", script: "site-2" }];
    expect(handback(before, moved, routes, DOORS)).toEqual([
      { method: "POST", pattern: "tinyassets.io/.well-known/oauth-x*", script: proxy },
      { method: "PUT", id: "1", pattern: "tinyassets.io/mcp*", script: proxy, from: DOORS },
      { method: "DELETE", id: "3", pattern: "tinyassets.io/hooks/*", from: DOORS }]);
  });

  it("takes one record by its id, and nothing it does not use", () => {
    const w = world();
    for (const argv of [["--mode", "handback"], ["--mode", "handback", "--record"],
                        ["--mode", "handback", "--record", "a/b"],
                        ["--mode", "handback", "--record", "x".repeat(65)]]) {
      expect(() => parse(argv, ENV, w.io), argv.join(" ")).toThrow(
        "mode handback hands back a routes run's record: --record <id>, the run id its job " +
        "summary names (letters, digits and -)");
    }
    for (const mode of ["plan", "dark", "probe", "routes"]) {
      expect(() => parse(["--mode", mode, "--record", "7001"], ENV, w.io)).toThrow(
        `mode ${mode} reads no route record: --record is mode handback's`);
    }
    expect(() => parse(["--mode", "handback", "--record", "7001", "--rotate", "FILES_KEYRING"],
                       ENV, w.io))
      .toThrow("mode handback deploys nothing: it takes no --rotate or --migrate");
    expect(() => parse(["--mode", "handback", "--record", "7001", "--public", "--yes"], ENV, w.io))
      .toThrow("--public is the plan job's: it never changes anything");
    expect(parse(["--mode", "handback", "--record", "local-20261107T120000Z"], ENV, w.io).record)
      .toBe("local-20261107T120000Z");
  });

  it("in the plan job reads the record and the zone with the Cloudflare token alone, and " +
     "changes nothing", async () => {
    const { w, from } = await cutOver();
    const work = await runMode(w, "handback", ["--public", "--record", "7001"],
                               { CLOUDFLARE_API_TOKEN: "cf-token" });
    expect(w.events.slice(from).filter((event) => CHANGE.test(event)
      || event.startsWith("python3"))).toEqual([]);
    expect(work.plan).toHaveLength(12);
    expect(work.plan[1]).toBe(`E2B lifecycle webhook ${E2B_HOOK.url}: checked by the apply job ` +
                              "before any change");
    expect(w.logs.at(-1)).toBe("\nNothing was changed.");
  });

  it("never touches a route the record and the handover do not hold, and stops before any " +
     "change on a route changed since routes, naming it", async () => {
    const { w, before } = await cutOver();
    w.routes.set("u1", { id: "u1", pattern: "tinyassets.io/later*", script: "site" });
    const work = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(work.plan).toContain("leave the route tinyassets.io/later* (site's): neither the " +
                                "record nor the handover holds it");
    expect(kept(w.routes.values())).toEqual(kept([...before,
      { pattern: "tinyassets.io/later*", script: "site" }]));
    const changed = (pattern: string, script: string | undefined) => async (zone: typeof w) => {
      const held = [...zone.routes.values()].find((each) => each.pattern === pattern);
      if (script === undefined) {
        zone.routes.delete(held!.id);
      } else {
        zone.routes.set(held?.id ?? "z9", { id: held?.id ?? "z9", pattern, script });
      }
    };
    for (const [change, why] of [
      // An addition of the handover's, pointed elsewhere since: not the doors' to delete.
      [changed("tinyassets.io/api/*", "someone-else"), "tinyassets.io/api/* is someone-else's, " +
       `neither as the handover left it (${DOORS}'s) nor as the record has it (absent)`],
      // A recorded route the handover never changed.
      [changed("tinyassets.io/site*", "site-2"), "tinyassets.io/site* is site-2's, the record " +
       "has it site's and the handover never changed it"],
      [changed("tinyassets.io/site*", undefined), "tinyassets.io/site* is absent, the record " +
       "has it site's and the handover never changed it"],
      // A route it moved, moved again; one it deleted, made again for another Worker.
      [changed("tinyassets.io/mcp*", "someone-else"), "tinyassets.io/mcp* is someone-else's, " +
       `neither as the handover left it (${DOORS}'s) nor as the record has it ` +
       "(tinyassets-mcp-proxy's)"],
      [changed("tinyassets.io/.well-known/oauth-protected-resource*", "someone-else"),
       "tinyassets.io/.well-known/oauth-protected-resource* is someone-else's, neither as the " +
       "handover left it (absent) nor as the record has it (tinyassets-mcp-proxy's)"],
    ] as const) {
      const zone = await cutOver();
      await change(zone.w);
      const table = JSON.stringify([...zone.w.routes.values()]);
      await expect(runMode(zone.w, "handback", ["--yes", "--record", "7001"]), why).rejects.toThrow(
        `changed since routes, so nothing is handed back: ${why}. Put each back as the record ` +
        "has it, then run the handback again");
      expect(zone.w.events.slice(zone.from).filter((event) => CHANGE.test(event))).toEqual([]);
      expect(JSON.stringify([...zone.w.routes.values()])).toBe(table);
      // The plan job shows it too, before the apply job.
      await expect(runMode(zone.w, "handback", ["--public", "--record", "7001"],
                           { CLOUDFLARE_API_TOKEN: "cf-token" })).rejects.toThrow(why);
    }
  });

  it("stops at a refused route change, the rest undone, no path the record served unserved",
     async () => {
    const pattern = "tinyassets.io/.well-known/oauth-authorization-server*";
    const w = world({ held: true, refusingRoute: `POST ${pattern}` });
    const before = kept(w.routes.values());
    await runMode(w, "routes", ["--yes"], RUN);
    const from = w.events.length;
    await expect(runMode(w, "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      `add the route ${pattern} back for tinyassets-mcp-proxy failed (400, [10020]); the steps ` +
      "before it are done, the rest are not: run the handback again");
    const ran = w.events.slice(from);
    const refused = ran.indexOf(`cf POST /zones/z/workers/routes`, ran.indexOf(
      "cf POST /zones/z/workers/routes") + 1);
    expect(refused).toBeGreaterThan(-1);
    // Nothing after the refusal: no doors' route deleted, so its paths stay served.
    expect(ran.slice(refused + 1).filter((event) => ROUTE_CHANGE.test(event))).toEqual([]);
    for (const path of PROBES.filter((each) => servedBy(before as Route[], each) !== undefined)) {
      expect([path, servedBy(w.routes.values(), path)]).not.toEqual([path, undefined]);
    }
  });

  it("finds the egress host's DNS records by exact name and type, whatever the listing returns",
     async () => {
    // The listing ignores its filter: the zone's other names, and a TXT on the egress host,
    // come back too. None blocks routes, and the handback removes none of them.
    const w = world({ held: true, noisyDns: true, keepsDns: true });
    const others = JSON.stringify(w.dns);
    await runMode(w, "routes", ["--yes"], RUN);
    expect(stored(w).domain.created).toEqual([
      { id: "dns-egress", type: "AAAA", content: "100::" }]);
    await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(JSON.stringify(w.dns)).toBe(others);
    // An address record on the host still stops routes, under the same listing.
    await expect(runMode(world({ held: true, noisyDns: true, dns: ["CNAME"] }), "routes",
                         ["--yes"], RUN)).rejects.toThrow(`${EGRESS_DOMAIN} holds 1 DNS record(s) (CNAME)`);
  });

  for (const keepsDns of [false, true]) {
    it(`removes the DNS record the custom domain made ${keepsDns ? "when the detach leaves it"
      : "only if the detach leaves it"}, and a later routes runs again`, async () => {
      const w = world({ held: true, keepsDns });
      await runMode(w, "routes", ["--yes"], RUN);
      expect(w.dns.filter((record) => record.name === EGRESS_DOMAIN).map((record) => record.id))
        .toEqual(["o3", "dns-egress"]);
      const from = w.events.length;
      await runMode(w, "handback", ["--yes", "--record", "7001"]);
      // Only that one, matched exactly, and only where Cloudflare left it.
      expect(w.events.slice(from).filter((event) => event.startsWith("cf DELETE /zones/z/dns")))
        .toEqual(keepsDns ? ["cf DELETE /zones/z/dns_records/dns-egress"] : []);
      expect(w.dns.map((record) => record.id)).toEqual(["o1", "o2", "o3"]);
      // A second cutover after the rollback is not stopped by a leftover record.
      await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ID: "7002" });
      expect([...w.domains.values()].map((domain) => domain.service)).toEqual([DOORS]);
    });
  }

  it("never deletes an address record on the host the attach did not make", async () => {
    const w = world({ held: true, keepsDns: true });
    await runMode(w, "routes", ["--yes"], RUN);
    w.dns.push({ id: "later", type: "A", name: EGRESS_DOMAIN, content: "192.0.2.9" });
    const from = w.events.length;
    await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(w.events.slice(from).filter((event) => event.startsWith("cf DELETE /zones/z/dns")))
      .toEqual(["cf DELETE /zones/z/dns_records/dns-egress"]);
    expect(w.dns.map((record) => record.id)).toEqual(["o1", "o2", "o3", "later"]);
  });

  for (const keepsDns of [true, false]) {
    it(`holds the host to no address record when the attach's were never recorded, ${keepsDns
      ? "naming the one the detach left" : "and passes once the detach removed it"}`,
       async () => {
      // Routes stopped between the attach and writing its DNS record in: the record has none,
      // yet proves the host held no address record before routes.
      const w = world({ held: true, keepsDns });
      await runMode(w, "routes", ["--yes"], RUN);
      const record = stored(w);
      w.kv.set(`${CONTROL_NS}/${KEY}`, JSON.stringify({ ...record, domain: { ...record.domain,
        created: null } }));
      const handback = runMode(w, "handback", ["--yes", "--record", "7001"]);
      if (keepsDns) {
        await expect(handback).rejects.toThrow("the handback read back unlike the record: " +
          `${EGRESS_DOMAIN} holds the DNS record AAAA (100::), and held no address record ` +
          "before routes: the custom domain's own, not recorded, for a hand to remove");
        // Never deleted: only a recorded record is matched exactly.
        expect(w.dns.some((each) => each.id === "dns-egress")).toBe(true);
      } else {
        const work = await handback;
        expect(work.plan).toContain(`the DNS records the custom domain made on ${EGRESS_DOMAIN} ` +
          "were not recorded (routes stopped between its attach and its record), so none is " +
          "deleted, and the read-back holds the host to no address record, as before routes");
      }
      // Every route is back either way: the DNS record serves no one.
      expect(handover([...w.routes.values()], DOORS, ROUTES).length).toBeGreaterThan(0);
    });
  }

  it("hands the routes back before deleting the attach's DNS record, so a refused delete " +
     "never holds them", async () => {
    const w = world({ held: true, keepsDns: true });
    const before = kept(w.routes.values());
    await runMode(w, "routes", ["--yes"], RUN);
    const from = w.events.length;
    await runMode(w, "handback", ["--yes", "--record", "7001"]);
    const ran = w.events.slice(from);
    expect(ran.findIndex((event) => event.startsWith("cf DELETE /zones/z/dns_records")))
      .toBeGreaterThan(ran.findLastIndex((event) => ROUTE_CHANGE.test(event)));
    expect(kept(w.routes.values())).toEqual(before);
    // A token without DNS Edit: the delete is refused, loudly, with every route back already.
    const refused = world({ held: true, keepsDns: true, refusingDns: true });
    await runMode(refused, "routes", ["--yes"], RUN);
    await expect(runMode(refused, "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      `deleting the DNS record AAAA ${EGRESS_DOMAIN} (100::) failed (403, [10000]): run the ` +
      "handback again");
    expect(kept(refused.routes.values())).toEqual(before);
  });
});

describe("a probe run", () => {
  it("hands the doors the sign-in's two paths alone, after the early sign-in check, then " +
     "prints the one link", async () => {
    const w = world({ held: true, probed: false });
    // The dark run's state, with the probe's routes not yet the doors'.
    for (const id of ["p1", "p2"]) {
      w.routes.delete(id);
    }
    const work = await runMode(w, "probe", ["--yes"]);
    const [, , authorized, first, second] = order(w.events, [
      "cf GET /accounts", "workos GET redirect_uris", "workos authorize",
      "cf POST /zones/z/workers/routes", "cf POST /zones/z/workers/routes"]);
    expect(authorized).toBeLessThan(first!);
    expect(second).toBeGreaterThan(first!);
    expect(w.events.filter((event) => /^(wrangler|e2b)/.test(event))).toEqual([]);
    expect(work.plan.slice(-2)).toEqual(PROBE_ROUTES.map((pattern) =>
      `add the route ${pattern} for ${DOORS}`));
    expect(w.logs.at(-1)!.startsWith(
      `\nSign in once, in a browser, at:\n${PUBLIC_ORIGIN}${AUTHORIZE_PATH}?`)).toBe(true);
    expect(w.logs.at(-1)).toContain(PROBE_SIGNS);
    // The old stack still serves /mcp: the canary holds whichever side answers.
    expect(w.events).toContain("python3 cutover_handles_canary.py --side auto");
  });

  it("stops unless a dark run came first, every gate closed and the callback listed",
     async () => {
    await expect(runMode(world(), "probe", ["--yes"])).rejects.toThrow(
      `${DOORS} is not deployed: the probe runs after a dark run`);
  });
});

describe("the pair and h2 modes", () => {
  it("runs the pair check with the E2B key alone, and skips it when E2B holds the tag",
     async () => {
    const w = world({ paired: false });
    await runMode(w, "pair");
    expect(w.events.filter((event) => event.startsWith("node "))).toEqual([
      "node checks.ts pair env=E2B_API_KEY,HOME,PATH"]);
    expect(w.logs).toEqual([`the pair ${PAIR} passed its standing checks, run now`]);
    const held = world();
    await runMode(held, "pair");
    expect(held.events.some((event) => event.startsWith("node "))).toBe(false);
    expect(held.logs).toEqual([`the pair ${PAIR} passed its standing checks in this E2B team`]);
    await expect(runMode(world(), "pair", [], { PATH: "/bin" }))
      .rejects.toThrow("E2B_API_KEY is not set (the production environment's secrets)");
  });

  it("runs H2's probes with the environment, and in the plan job only says so", async () => {
    const w = world();
    await runMode(w, "h2", ["--yes"], { ...ENV, TA_PROBE_KEY: "probe-key" });
    expect(w.events).toEqual(["exec h2-probes.ts env has TA_PROBE_KEY,E2B_API_KEY"]);
    const plan = world();
    await runMode(plan, "h2", ["--public"], PUBLIC);
    expect(plan.events).toEqual(["log Would change:"]);
    expect(plan.logs).toEqual(["Would change:\n- run H2's probes against the routed doors; " +
                               "nothing is changed"]);
  });
});

describe("the parts of a run", () => {
  const discovery = (body: Json) => async () => Response.json(body);
  const good = { issuer: "https://auth.test", jwks_uri: "https://auth.test/j" };

  it("writes User Management's sign-in for the client, the JWKS from the issuer's discovery " +
     "held to it exactly, in a record the doors accept", async () => {
    const made = await upstreamFrom("https://auth.test", "c", discovery(good));
    expect(made).toEqual(UPSTREAM);
    expect(made.record).toEqual({
      client_id: "c", authorization_endpoint: "https://api.workos.com/user_management/authorize",
      token_endpoint: "https://api.workos.com/user_management/authenticate",
      jwks_uri: "https://auth.test/j",
      token_issuer: "https://api.workos.com/user_management/c",
      user_delete: "https://api.workos.com/user_management/users/{sub}",
      authorize_params: { provider: "authkit" } });
    expect(() => upstreamConfig(JSON.stringify(made.record))).not.toThrow();
    await expect(upstreamFrom("https://auth.test", "c",
                              discovery({ ...good, issuer: "https://auth.test/" })))
      .rejects.toThrow(/not OWNER_ISSUER/);
    await expect(upstreamFrom("https://auth.test", "", discovery(good)))
      .rejects.toThrow(/WORKOS_CLIENT_ID/);
    await expect(upstreamFrom("https://auth.test", "c/../x", discovery(good)))
      .rejects.toThrow(/WORKOS_CLIENT_ID/);
    await expect(upstreamFrom("http://auth.test", "c", discovery(good))).rejects.toThrow();
    await expect(upstreamFrom("https://auth.test", "c", async () =>
      new Response("down", { status: 503 }))).rejects.toThrow(/503/);
  });

  it("stops on an absent or plain-http JWKS in the discovery: unknown never passes", async () => {
    await expect(upstreamFrom("https://auth.test", "c", discovery({ issuer: good.issuer })))
      .rejects.toThrow(/jwks_uri: unknown/);
    await expect(upstreamFrom("https://auth.test", "c",
                              discovery({ ...good, jwks_uri: "http://auth.test/j" })))
      .rejects.toThrow(/jwks_uri: unknown/);
  });

  it("builds the authorize URL the doors build, for the early sign-in check", () => {
    const doors = oidcUpstream(UPSTREAM.issuer, upstreamConfig(JSON.stringify(UPSTREAM.record)),
                               "k");
    expect(authorizeLink(UPSTREAM, "s1", "c1")).toBe(doors.authorizeUrl({
      state: "s1", code_challenge: "c1", redirect_uri: UPSTREAM_CALLBACK }));
  });

  it("checks, before any sign-in, that WorkOS's authorize sends the browser to AuthKit's " +
     "sign-in, and stops on a refused client or redirect", async () => {
    const sent: { url: string; init: RequestInit | undefined }[] = [];
    const answering = (status: number, location: string | null) =>
      async (url: string, init?: RequestInit) => {
        sent.push({ url, init });
        return new Response(null, { status,
                                    headers: location === null ? {} : { Location: location } });
      };
    expect(await authorizeCheck(UPSTREAM, false, answering(302,
      "https://auth.test/bootstrap?client_id=c&authorization_session_id=x&max_age=0")))
      .toMatch(/auth\.test's sign-in/);
    // The URL sent is the WorkOS one the doors build, with a throwaway state and challenge.
    const asked = new URL(sent[0]!.url);
    expect(sent[0]!.init?.redirect).toBe("manual");
    expect(`${asked.origin}${asked.pathname}`)
      .toBe("https://api.workos.com/user_management/authorize");
    expect(asked.href).toBe(authorizeLink(UPSTREAM, asked.searchParams.get("state")!,
                                          asked.searchParams.get("code_challenge")!));
    expect(asked.searchParams.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    await authorizeCheck(UPSTREAM, false, answering(302, "https://auth.test/bootstrap"));
    expect(new URL(sent[1]!.url).searchParams.get("state"))
      .not.toBe(asked.searchParams.get("state"));
    for (const [status, location, named] of [
      [302, "https://error.workos.com/sso/client-id-invalid", /client-id-invalid/],
      [302, "https://error.workos.com/redirect-uri-invalid?redirect_uri=x", /redirect-uri-invalid/],
      [302, "https://auth.test/oauth2/error?error=application_not_found", /oauth2\/error/],
      [302, "https://auth.test/oauth2/error", /oauth2\/error/],
      [302, "https://auth.test/?error=access_denied", /refused/],
      [302, "https://evil.test/bootstrap", /evil\.test/],
      [302, "http://auth.test/bootstrap", /refused/],
      [200, null, /answered 200/],
      [302, null, /answered 302/],
      [400, "https://auth.test/bootstrap", /answered 400/],
    ] as const) {
      await expect(authorizeCheck(UPSTREAM, false, answering(status, location)))
        .rejects.toThrow(named);
    }
  });

  it("reports an unlisted callback as dark's to register while it cannot be listed, and " +
     "stops on every other refusal", async () => {
    const answering = (location: string) => async () =>
      new Response(null, { status: 302, headers: { Location: location } });
    expect(await authorizeCheck(UPSTREAM, true, answering(
      "https://error.workos.com/redirect-uri-invalid?redirect_uri=x")))
      .toMatch(/dark registers it/);
    expect(await authorizeCheck(UPSTREAM, true, answering("https://auth.test/bootstrap")))
      .toMatch(/auth\.test's sign-in/);
    for (const location of ["https://error.workos.com/sso/client-id-invalid",
                            "https://error.workos.com/redirect-uri-invalid-not",
                            "https://evil.test/redirect-uri-invalid",
                            "https://auth.test/oauth2/error?error=application_not_found"]) {
      await expect(authorizeCheck(UPSTREAM, true, answering(location))).rejects.toThrow(/refused/);
    }
  });

  it("registers the WorkOS redirect URI only when missing, and never deletes one", async () => {
    const calls: string[] = [];
    const workos = (uris: string[]) => async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      return init?.method === "POST" ? Response.json({ uri: CALLBACK }, { status: 201 })
        : Response.json({ data: uris.map((uri) => ({ uri })) });
    };
    expect(await registerRedirect("k", true, workos([CALLBACK, "https://other.test/cb"])))
      .toBe("present");
    expect(await registerRedirect("k", false, workos(["https://other.test/cb"]))).toBe("missing");
    expect(await registerRedirect("k", true, workos(["https://other.test/cb"])))
      .toBe("registered");
    expect(calls.filter((call) => call.startsWith("POST"))).toEqual([
      "POST https://api.workos.com/user_management/redirect_uris"]);
    expect(calls.some((call) => call.startsWith("DELETE"))).toBe(false);
  });

  it("sets the routing key on the doors and the owner together, or not at all", () => {
    const none = secretPlan({ doors: [], owner: [], gateway: [], sandbox: [] }, new Set(), NONE);
    expect(none.filter(({ name }) => name === "ROUTING_MAC_KEY").map(({ worker }) => worker)
      .sort()).toEqual(["doors", "owner"]);
    const both = secretPlan({ doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET"],
                              owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                              sandbox: ["E2B_API_KEY"], files: ["FILES_KEYRING"] },
                            new Set(), NONE);
    expect(both).toEqual([{ worker: "doors", name: "INBOUND_SLACK_SIGNING_SECRET" }]);
    expect(() => secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(), NONE))
      .toThrow("only one of the doors and the owner holds ROUTING_MAC_KEY: pass --rotate " +
               "ROUTING_MAC_KEY to set both again");
    expect(secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(["ROUTING_MAC_KEY"]),
                      NONE).filter(({ name }) => name === "ROUTING_MAC_KEY")).toHaveLength(2);
  });

  it("never replaces a held keyring, and rotates one only by adding a key", () => {
    const held = { doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET",
                           "INBOUND_SLACK_SIGNING_SECRET"],
                   owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                   sandbox: ["E2B_API_KEY"], files: ["FILES_KEYRING"] };
    const values = (ring: string) => (name: string) =>
      name === "GATEWAY_KEYRING" || name === "FILES_KEYRING" ? ring : undefined;
    expect(secretPlan(held, new Set(), values(RING))).toEqual([]);
    expect(() => secretPlan(held, new Set(["GATEWAY_KEYRING"]), values(RING)))
      .toThrow(/adding a key/);
    expect(() => secretPlan(held, new Set(["GATEWAY_KEYRING"]),
                            values(JSON.stringify({ active: "k3", keys: { k1: "a", k2: "b" } }))))
      .toThrow(/active key among its keys/);
    // A ring either Worker would refuse (keys that are not 32 bytes) stops it too.
    expect(() => secretPlan(held, new Set(["GATEWAY_KEYRING"]),
                            values(JSON.stringify({ active: "k2", keys: { k1: "a", k2: "b" } }))))
      .toThrow(/GATEWAY_KEYRING is .*32 bytes/);
    expect(secretPlan(held, new Set(["GATEWAY_KEYRING"]), values(ROTATED)))
      .toEqual([{ worker: "gateway", name: "GATEWAY_KEYRING" }]);
    // The files Worker's data keys' ring, by the same rule (slice P).
    expect(() => secretPlan(held, new Set(["FILES_KEYRING"]), values(RING)))
      .toThrow(/FILES_KEYRING holds/);
    expect(secretPlan(held, new Set(["FILES_KEYRING"]), values(ROTATED)))
      .toEqual([{ worker: "files", name: "FILES_KEYRING" }]);
  });

  it("refuses to deploy unless each migration lock pins every migration it ships, in order " +
     "(review M5-2)", () => {
    const code = ["a".repeat(64), "b".repeat(64)];
    const first = { at: "2026-11-04T00:00:00.000Z", commit: "abc123" };
    const owner = "owner/migrations.json";
    expect(lockRefusal({ first_deploy: first, deployed: code }, code, owner)).toBeNull();
    // Unmarked: the first deploy pins first.
    expect(lockRefusal({ first_deploy: null, deployed: [] }, code, owner))
      .toMatch(/no first deploy/);
    // A migration the lock does not pin, one it pins that is gone, one changed, or reordered.
    for (const deployed of [[code[0]], [...code, "c".repeat(64)], [code[0], "c".repeat(64)],
                            [code[1], code[0]]]) {
      expect(lockRefusal({ first_deploy: first, deployed }, code, owner))
        .toMatch(/PIN_MIGRATIONS/);
    }
    expect(lockRefusal({ deployed: code }, code, owner)).toMatch(/out of shape/);
    expect(lockRefusal(null, code, owner)).toMatch(/out of shape/);
    // The sandbox Worker's objects are locked too (S25), each named, pinned in its folder.
    expect(MIGRATED).toContain("sandbox");
    expect(lockRefusal({ first_deploy: first, deployed: [code[0]] }, code,
                       "sandbox/migrations/SandboxSlots.json"))
      .toMatch(/^the migration lock sandbox\/migrations\/SandboxSlots\.json pins 1 .* in platform\/sandbox,/);
    // The kit's digests, from a guard that reports no lock (or the old bare list): refused,
    // never read as nothing to check.
    const reporting = (reported: unknown) => (_worker: string, out: string) => {
      writeFileSync(out, JSON.stringify(reported));
      return { status: 0, stdout: "" };
    };
    for (const worker of MIGRATED) {
      for (const reported of [{}, [], null, code]) {
        expect(() => codeDigests(worker, reporting(reported)))
          .toThrow(`${worker}'s migrations guard reported no migration lock`);
      }
    }
    const lock = { "sandbox/migrations/SandboxSlots.json": code };
    expect(codeDigests("sandbox", reporting(lock))).toEqual(lock);
  });

  it("lists the account's Workers, and deletes one unforced, never reading a failure as " +
     "done", async () => {
    const listing = "/accounts/a/workers/scripts";
    const script = "/accounts/a/workers/scripts/tinyassets-old";
    expect(await new Cloudflare(api({ [listing]: ok([{ id: "tinyassets-old" }, { id: "x" }]) }))
      .workers("a")).toEqual(["tinyassets-old", "x"]);
    const deleted = api({ [script]: ok(null) });
    await new Cloudflare(deleted).deleteWorker("a", "tinyassets-old");
    expect(deleted.calls).toEqual([`DELETE ${script}`]);
    for (const answer of [
      { status: 500, body: {} }, { status: 403, body: { success: false, errors: [{ code: 10000 }] } },
      { status: 200, body: { success: false } },
    ] as CfAnswer[]) {
      await expect(new Cloudflare(api({ [listing]: answer })).workers("a"))
        .rejects.toThrow(/the Workers listing failed/);
      await expect(new Cloudflare(api({ [script]: answer })).deleteWorker("a", "tinyassets-old"))
        .rejects.toThrow(/deleting the Worker tinyassets-old failed/);
    }
    await expect(new Cloudflare(api({ [listing]: ok({}) })).workers("a"))
      .rejects.toThrow(/the Workers listing failed/);
  });

  it("never reads a failed secret listing as no secrets", async () => {
    const path = "/accounts/a/workers/scripts/tinyassets-doors/secrets";
    const cf = (answers: Record<string, CfAnswer | Error>) => new Cloudflare(api(answers));
    expect(await cf({ [path]: ok([{ name: "WORKOS_API_KEY", type: "secret_text" }]) })
      .secrets("a", "tinyassets-doors")).toEqual(["WORKOS_API_KEY"]);
    expect(await cf({ [path]: { status: 404, body: { success: false, errors: [{ code: 10007 }] } } })
      .secrets("a", "tinyassets-doors")).toBeNull();
    for (const answer of [
      { status: 500, body: {} }, { status: 403, body: { success: false, errors: [{ code: 10000 }] } },
      { status: 404, body: { success: false, errors: [{ code: 7003 }] } },
      { status: 200, body: { success: false } }, { status: 200, body: { success: true } },
    ] as CfAnswer[]) {
      await expect(cf({ [path]: answer }).secrets("a", "tinyassets-doors"))
        .rejects.toThrow(/failed/);
    }
    await expect(cf({ [path]: new Error("network down") }).secrets("a", "tinyassets-doors"))
      .rejects.toThrow(/network down/);
  });

  it("calls the control record absent only when the API answers it is", async () => {
    const path = "/accounts/a/storage/kv/namespaces/n/values/c%3Acontrol";
    const cf = (answer: CfAnswer) => new Cloudflare(api({ [path]: answer }));
    expect(await cf({ status: 200, body: { ordinary: "none" } } as CfAnswer)
      .value("a", "n", CONTROL_KEY)).toEqual({ ordinary: "none" });
    expect(await cf({ status: 404, body: { success: false, errors: [{ code: 10009 }] } })
      .value("a", "n", CONTROL_KEY)).toBeUndefined();
    for (const answer of [{ status: 500, body: {} }, { status: 404, body: {} },
                          { status: 401, body: { errors: [{ code: 10000 }] } }] as CfAnswer[]) {
      await expect(cf(answer).value("a", "n", CONTROL_KEY)).rejects.toThrow(/failed/);
    }
  });

  it("never reads a failed namespace or wrangler listing as absent", async () => {
    const page = (n: number) => `/accounts/a/storage/kv/namespaces?per_page=100&page=${n}`;
    const full = Array.from({ length: 100 }, (_, i) => ({ id: `i${i}`, title: `t${i}` }));
    const found = await new Cloudflare(api({ [page(1)]: ok(full),
                                             [page(2)]: ok([{ id: "x", title: "tinyassets-control" }]) }))
      .namespaces("a");
    expect(found.get("tinyassets-control")).toBe("x");
    await expect(new Cloudflare(api({ [page(1)]: ok(full) })).namespaces("a"))
      .rejects.toThrow(/failed/);
    expect(listed("tinyassets-files-old\nother", "tinyassets-files")).toBe(false);
    expect(listed("name:           tinyassets-files\n", "tinyassets-files")).toBe(true);
    expect(listed("│ q1 │ tinyassets-sandbox-ring │ 0 │", "tinyassets-sandbox-ring")).toBe(true);
    // Wrangler's runner throws on any non-zero exit, with its stderr alone.
    const w = world({ failing: "queues list" });
    const run = wranglerFor(parse(["--mode", "dark"], ENV, w.io), ACCOUNT);
    expect(() => run(["queues", "list"])).toThrow("wrangler queues list failed (exit 1): the API refused it");
  });

  it("hands each route from the old proxy to the doors one at a time, no path unserved " +
     "(review D3)", async () => {
    const fake = zoneApi(LIVE);
    const cloudflare = new Cloudflare(fake);
    const { id: zone, account } = await cloudflare.zone(ZONE);
    expect(account).toBe("a".repeat(32));
    const steps = handover(await cloudflare.routes(zone), DOORS, ROUTES);
    // Identical patterns move in place; a narrower old one is deleted after the doors' wider
    // one exists; a wider old one (app*, api*) keeps only the paths the doors do not cover.
    expect(steps.filter((each) => each.method === "PUT").map((each) => each.pattern))
      .toEqual(["tinyassets.io/mcp*"]);
    expect(steps.filter((each) => each.method === "DELETE").map((each) => each.pattern))
      .toEqual(["tinyassets.io/.well-known/oauth-protected-resource*",
                "tinyassets.io/.well-known/oauth-authorization-server*"]);
    const added = steps.findIndex((each) => each.pattern === "tinyassets.io/.well-known/oauth-*");
    expect(steps.findIndex((each) => each.method === "DELETE")).toBeGreaterThan(added);
    await cloudflare.handOver(zone, DOORS, steps);
    for (const path of PROBES.slice(0, 10)) {
      expect([path, servedBy(fake.table.values(), path)]).toEqual([path, DOORS]);
    }
    expect(servedBy(fake.table.values(), "tinyassets.io/app")).toBe("tinyassets-mcp-proxy");
    expect(servedBy(fake.table.values(), "tinyassets.io/site/x")).toBe("site");
    // Run again, it changes nothing.
    expect(handover([...fake.table.values()], DOORS, ROUTES)).toEqual([]);
  });

  it("probes the sign-in on its two unheld paths alone; routes then finds both the doors'",
     async () => {
    expect(PROBE_ROUTES.every((pattern) => ROUTES.includes(pattern))).toBe(true);
    const fake = zoneApi(LIVE);
    const cloudflare = new Cloudflare(fake);
    const steps = probeSteps(await cloudflare.routes("z"));
    expect(steps).toEqual(PROBE_ROUTES.map((pattern) => ({ method: "POST", pattern })));
    await cloudflare.handOver("z", DOORS, steps);
    // Only the sign-in's paths move: every other path is served as before.
    for (const path of PROBES) {
      const doors = path === "tinyassets.io/authorize" || path === "tinyassets.io/oauth/token";
      expect([path, servedBy(fake.table.values(), path)])
        .toEqual([path, doors ? DOORS : fake.before.get(path)]);
    }
    // Run again, it changes nothing; routes hands over the rest, unchanged, and both are held.
    expect(probeSteps([...fake.table.values()])).toEqual([]);
    const after = handover([...fake.table.values()], DOORS, ROUTES);
    expect(after).toEqual(handover(LIVE, DOORS, ROUTES)
      .filter((each) => !PROBE_ROUTES.includes(each.pattern)));
    await cloudflare.handOver("z", DOORS, after);
    for (const path of PROBES.slice(0, 10)) {
      expect([path, servedBy(fake.table.values(), path)]).toEqual([path, DOORS]);
    }
    expect(handover([...fake.table.values()], DOORS, ROUTES)).toEqual([]);
  });

  it("refuses to probe a path any other Worker, or no Worker, serves", () => {
    for (const held of [
      { id: "x", pattern: "tinyassets.io/oauth/*", script: "tinyassets-mcp-proxy" },
      { id: "x", pattern: "tinyassets.io/oauth/token", script: "tinyassets-mcp-proxy" },
      { id: "x", pattern: "tinyassets.io/*", script: "site" },
      { id: "x", pattern: "tinyassets.io/authorize*" },
    ] as Route[]) {
      expect(() => probeSteps([...LIVE, held])).toThrow(/nothing is changed/);
    }
  });

  it("probes only behind a control record that admits nobody, as the doors read it", () => {
    const owners = ["user_a", "user_b"];
    const records: [unknown, boolean][] = [
      [DARK_CONTROL, true], [{ ...DARK_CONTROL, ordinary: "none" }, true],
      [{ ...DARK_CONTROL, canary: ["user_a"] }, false], [{ ...DARK_CONTROL, ordinary: "all" }, false],
      [undefined, false], [{}, false], [null, false],
    ];
    for (const [record, closed] of records) {
      expect([record, admitsNobody(record)]).toEqual([record, closed]);
      const control = parseControl(record);
      if (closed && control !== null) {
        for (const owner of owners) {
          expect(ownerOpen({ kind: "read", control }, owner)).toBe(false);
          expect(outsideOpen({ kind: "read", control }, owner)).toBe(false);
        }
      }
    }
    // A missing record opens the owner's sign-in: never a probe.
    expect(ownerOpen({ kind: "missing" }, "user_a")).toBe(true);
  });

  it("prints the doors' own first-party sign-in, a fresh S256 challenge each run", () => {
    const link = new URL(probeLink());
    expect(`${link.origin}${link.pathname}`).toBe(`${PUBLIC_ORIGIN}${AUTHORIZE_PATH}`);
    const { code_challenge: challenge, state, ...rest } = Object.fromEntries(link.searchParams);
    expect(rest).toEqual({ response_type: "code", client_id: WEB_CLIENT,
                           redirect_uri: WEB_REDIRECT, code_challenge_method: "S256",
                           resource: APP_RESOURCE });
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(state).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(new URL(probeLink()).searchParams.get("code_challenge")).not.toBe(challenge);
    expect(PROBE_SIGNS).toContain(`${WEB_REDIRECT}?error=temporarily_unavailable`);
    expect(PROBE_SIGNS).toContain(CALLBACK);
  });

  it("stops the handover at a refused change, saying what is done", async () => {
    const fake = zoneApi(LIVE, "PUT /zones/z/workers/routes/r1");
    const cloudflare = new Cloudflare(fake);
    const steps = handover(await cloudflare.routes("z"), DOORS, ROUTES);
    await expect(cloudflare.handOver("z", DOORS, steps)).rejects.toThrow(/the steps before it/);
    expect(fake.calls.filter((call) => call.startsWith("DELETE"))).toEqual([]);
    await expect(new Cloudflare(api({})).zone(ZONE)).rejects.toThrow(/could not be read/);
    // The account is the zone's own: one zone, with its account, or the kit stops.
    const zones = (result: unknown) => new Cloudflare(api({ "/zones?name=tinyassets.io": ok(result) }));
    await expect(zones([{ id: "z" }]).zone(ZONE)).rejects.toThrow(/could not be read/);
    await expect(zones([{ id: "z", account: { id: "a".repeat(32) } },
                        { id: "y", account: { id: "b".repeat(32) } }]).zone(ZONE))
      .rejects.toThrow(/2 found/);
    await expect(zones([{ id: "z", account: { id: "../x" } }]).zone(ZONE))
      .rejects.toThrow(/could not be read/);
    await expect(new Cloudflare(api({})).routes("z")).rejects.toThrow(/could not be read/);
  });

  it("sends the account's token on every Cloudflare call, and reads a body that is not JSON " +
     "as none", async () => {
    const sent: { url: string; headers: unknown }[] = [];
    const call = cfApi("cf-token", async (url, init) => {
      sent.push({ url, headers: init?.headers });
      return new Response("not json", { status: 502 });
    });
    expect(await call("/zones?name=x", { headers: { "Content-Type": "application/json" } }))
      .toEqual({ status: 502, body: {} });
    expect(sent).toEqual([{ url: "https://api.cloudflare.com/client/v4/zones?name=x",
                            headers: { Authorization: "Bearer cf-token",
                                       "Content-Type": "application/json" } }]);
  });

  it("takes the keyring the founder's setup makes in memory, in keyring-new's own shape",
     async () => {
    // scripts/platform_setup.py's maker, through a two-line driver: Python's ring is the one
    // stored, and the apply job's keyring() (the envelope's own reading) is the check it
    // must pass. A blob sealed under it opens under it.
    const made = execFileSync("python3", ["-I", "-c",
      "import sys; sys.path.insert(0, sys.argv[1]); " +
      "from scripts.platform_setup import make_keyring; sys.stdout.write(make_keyring())",
      REPO], { encoding: "utf8" });
    const ring = keyring(made, "FILES_KEYRING");
    expect([ring.active, [...ring.keys.keys()]]).toEqual(["k1", ["k1"]]);
    expect(ring.keys.get("k1")!.byteLength).toBe(32);
    const domain: Domain = { salt: "s", info: "i", context: "c" };
    const blob = await sealBlob(ring, domain, new TextEncoder().encode("plain"));
    expect(new TextDecoder().decode((await openBlob(ring, domain, blob, 1024))!.plain))
      .toBe("plain");
    // The kit's own maker writes the same shape, run as the release runs it (Node's type
    // stripping, so the envelope and the E2B client it imports run there too).
    const out = mkdtempSync(join(tmpdir(), "keyring-"));
    try {
      execFileSync(process.execPath, ["--experimental-strip-types",
                                      join(PLATFORM, "deploy", "deploy.ts"), "keyring-new",
                                      join(out, "ring.json")], { stdio: "pipe" });
      const theirs = keyring(readFileSync(join(out, "ring.json"), "utf8"), "the keyring");
      expect([theirs.active, [...theirs.keys.keys()]])
        .toEqual([ring.active, [...ring.keys.keys()]]);
      expect(theirs.keys.get("k1")!.byteLength).toBe(32);
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("makes a first keyring and adds a key under the kid after its highest, keeping the old",
     () => {
    const out = mkdtempSync(join(tmpdir(), "keyring-"));
    const logged: string[] = [];
    try {
      const file = join(out, "ring.json");
      keyringNew(file, (line) => logged.push(line));
      expect(() => keyringNew(file, NONE)).toThrow(/EEXIST/);
      const first = keyring(readFileSync(file, "utf8"), "the keyring");
      keyringAdd(file, (line) => logged.push(line));
      const added = keyring(readFileSync(file, "utf8"), "the keyring");
      expect([added.active, [...added.keys.keys()]]).toEqual(["k2", ["k1", "k2"]]);
      expect(added.keys.get("k1")).toEqual(first.keys.get("k1"));
      expect(added.keys.get("k2")).not.toEqual(first.keys.get("k1"));
      // Nothing of a ring is printed: only where it went.
      expect(logged.every((line) => !line.includes("{"))).toBe(true);
      // A ring that has retired k1 holds two kids, k2 and k3: the next is k4, never k3 again.
      const retired = ringSecret("k3", ["k2", "k3"]);
      writeFileSync(file, retired);
      keyringAdd(file, NONE);
      const next = keyring(readFileSync(file, "utf8"), "the keyring");
      const before = keyring(retired, "the keyring");
      expect([next.active, [...next.keys.keys()]]).toEqual(["k4", ["k2", "k3", "k4"]]);
      expect(next.keys.get("k3")).toEqual(before.keys.get("k3"));
      // One that has retired k1 and k2 holds k3 alone: the next is k4, never k2 again.
      writeFileSync(file, ringSecret("k3"));
      keyringAdd(file, NONE);
      expect([...keyring(readFileSync(file, "utf8"), "the keyring").keys.keys()])
        .toEqual(["k3", "k4"]);
      expect(() => { writeFileSync(file, "{}"); keyringAdd(file, NONE); })
        .toThrow("the keyring is {active, keys} as JSON");
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });
});

/** A fake of E2B's webhooks API: its hooks, and every call as `METHOD path body`. */
function e2bHooks(start: Json[], fail?: string) {
  const hooks = start.map((hook) => ({ ...hook }));
  const calls: string[] = [];
  const fetcher = async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const path = url.replace("https://api.e2b.app", "");
    calls.push(`${method} ${path}${init?.body ? ` ${String(init.body)}` : ""}`);
    // The key, and JSON named for every call, a listing as well as a write.
    expect(init?.headers).toEqual({ "X-API-Key": E2B_KEY, "Content-Type": "application/json" });
    if (fail === method) {
      return new Response("", { status: 500 });
    }
    if (method === "GET") {
      return Response.json(hooks);
    }
    const body = JSON.parse(String(init!.body)) as Json;
    if (method === "POST") {
      hooks.push({ id: "w1", ...body });
      return Response.json({ id: "w1" }, { status: 201 });
    }
    const hook = hooks.find((each) => `/events/webhooks/${each["id"]}` === path)!;
    Object.assign(hook, body);
    return Response.json(hook);
  };
  return { hooks, calls, e2b: new E2b(E2B_KEY, fetcher) };
}

describe("E2B's lifecycle webhook", () => {
  const EVENTS = ["sandbox.lifecycle.paused", "sandbox.lifecycle.killed"];

  it("is registered disabled in dark, and enabled only with the routes", async () => {
    // dark, none held: registered disabled, with the stored secret, for the two events.
    const dark = e2bHooks([{ id: "x", url: "https://elsewhere.test/hook", enabled: true }]);
    expect(await lifecycleHook(dark.e2b, "s1", true, false, false)).toBe("registered, disabled");
    expect(dark.hooks[1]).toEqual({ id: "w1", name: E2B_HOOK.name, url: E2B_HOOK.url,
                                    events: EVENTS, enabled: false, signatureSecret: "s1" });
    // dark again, the doors' secret not set this run: nothing sent; the other hook untouched.
    const calls = dark.calls.length;
    expect(await lifecycleHook(dark.e2b, "s2", true, false, false))
      .toBe("present, unchanged, disabled");
    expect(dark.calls.slice(calls)).toEqual(["GET /events/webhooks"]);
    expect(dark.hooks[0]).toEqual({ id: "x", url: "https://elsewhere.test/hook", enabled: true });
    // routes: enabled; a routes run later changes nothing; dark after it never turns it off.
    expect(await lifecycleHook(dark.e2b, "s2", true, true, false))
      .toBe("present, enabled set, enabled");
    expect(dark.calls.at(-1)).toBe('PATCH /events/webhooks/w1 {"enabled":true}');
    expect(await lifecycleHook(dark.e2b, "s2", true, true, false))
      .toBe("present, unchanged, enabled");
    await lifecycleHook(dark.e2b, "s2", true, false, false);
    expect(dark.hooks[1]!["enabled"]).toBe(true);
    // A held hook whose events drifted is given its two events back.
    const drifted = e2bHooks([{ id: "a", url: E2B_HOOK.url, enabled: false,
                                events: ["sandbox.lifecycle.paused"] }]);
    await lifecycleHook(drifted.e2b, "s", true, false, false);
    expect(drifted.hooks[0]!["events"]).toEqual(EVENTS);
    // routes with none held: registered enabled.
    expect(await lifecycleHook(e2bHooks([]).e2b, "s", true, true, false))
      .toBe("registered, enabled");
    // Without apply, only the listing is read: the kit's check before any change.
    const planned = e2bHooks([]);
    expect(await lifecycleHook(planned.e2b, "s", false, true, true))
      .toBe("missing: registered, enabled");
    expect(planned.calls).toEqual(["GET /events/webhooks"]);
    // A failed listing or write stops the kit; two hooks for the URL are refused.
    await expect(lifecycleHook(e2bHooks([], "GET").e2b, "s", false, false, false))
      .rejects.toThrow(/listed its webhooks with 500/);
    await expect(lifecycleHook(e2bHooks([], "POST").e2b, "s", true, false, false))
      .rejects.toThrow(/refused the webhook with 500/);
    await expect(lifecycleHook(e2bHooks([{ id: "a", url: E2B_HOOK.url }], "PATCH").e2b, "s", true,
                               false, true))
      .rejects.toThrow(/refused the webhook's change/);
    await expect(heldHook(e2bHooks([{ id: "a", url: E2B_HOOK.url },
                                    { id: "b", url: E2B_HOOK.url }]).e2b))
      .rejects.toThrow(/holds 2 webhooks/);
    // A route hands the hook's path to the doors.
    expect(ROUTES.some((pattern) => pattern.endsWith("*")
      && `${ZONE}${E2B_HOOK_PATH}`.startsWith(pattern.slice(0, -1)))).toBe(true);
  });

  it("changes its secret only with the doors' copy: both or neither", async () => {
    // The doors hold E2B_WEBHOOK_SECRET and production's value was replaced: without
    // --rotate the doors keep theirs, so E2B keeps its own too.
    const held = { doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET"],
                   owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                   sandbox: ["E2B_API_KEY"], files: ["FILES_KEYRING"] };
    const kept = secretPlan(held, new Set(), null);
    expect(kept.some((each) => each.name === "E2B_WEBHOOK_SECRET")).toBe(false);
    const neither = e2bHooks([{ id: "a", url: E2B_HOOK.url, enabled: true, events: EVENTS,
                                signatureSecret: "old" }]);
    await lifecycleHook(neither.e2b, "new", true, true, hookSetsSecret(kept));
    expect(neither.hooks[0]!["signatureSecret"]).toBe("old");
    // With --rotate E2B_WEBHOOK_SECRET the doors take the new value, and E2B does too.
    const rotated = secretPlan(held, new Set(["E2B_WEBHOOK_SECRET"]), null);
    expect(rotated).toContainEqual({ worker: "doors", name: "E2B_WEBHOOK_SECRET" });
    const both = e2bHooks([{ id: "a", url: E2B_HOOK.url, enabled: true, events: EVENTS,
                             signatureSecret: "old" }]);
    await lifecycleHook(both.e2b, "new", true, true, hookSetsSecret(rotated));
    expect(both.hooks[0]!["signatureSecret"]).toBe("new");
    // The doors missing it (a first deploy) sets both as well.
    const first = secretPlan({ ...held, doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY"] },
                             new Set(), null);
    expect(hookSetsSecret(first)).toBe(true);
  });

  it("starts the pair equal: a dark run that makes the hook sets the doors' secret too",
     async () => {
    // The doors hold an old E2B_WEBHOOK_SECRET, E2B holds no hook: the dark run sets the
    // doors' copy from production, and registers the hook with the same value.
    const w = world({ held: true });
    w.hooks.length = 0;
    await runMode(w, "dark", ["--yes"]);
    expect(w.secretsPut).toEqual({ "doors:E2B_WEBHOOK_SECRET": ENV["E2B_WEBHOOK_SECRET"] });
    const registered = w.events.find((event) => event.startsWith("e2b POST /events/webhooks"))!;
    expect(JSON.parse(registered.slice(registered.indexOf("{")))).toMatchObject({
      enabled: false, signatureSecret: ENV["E2B_WEBHOOK_SECRET"] });
    // A run that finds the hook held makes nothing and sets neither.
    const again = world({ held: true });
    await runMode(again, "dark", ["--yes"]);
    expect(again.secretsPut).toEqual({});
    expect(again.events.some((event) => /^e2b (POST|PATCH)/.test(event))).toBe(false);
  });

  it("masks the account id in an Actions log, and prints nothing outside one", () => {
    const lines: string[] = [];
    maskInLog("a".repeat(32), { GITHUB_ACTIONS: "true" }, (line) => lines.push(line));
    expect(lines).toEqual([`::add-mask::${"a".repeat(32)}`]);
    maskInLog("b".repeat(32), {}, (line) => lines.push(line));
    expect(lines).toHaveLength(1);
  });
});

describe("the template and the pair", () => {
  it("checks the sandbox's E2B template has a ready build, and stops on a failed lookup",
     async () => {
    const headers: unknown[] = [];
    const e2b = (alias: number, exists: unknown, tag = 200) => new E2b(E2B_KEY,
      async (url, init) => {
        headers.push(init?.headers);
        return url.includes("/aliases/") ? Response.json({ templateID: "t1" }, { status: alias })
          : Response.json({ exists }, { status: tag });
      });
    expect(await e2b(200, true).ready("tinyassets-sandbox:v1")).toBe(true);
    expect(await e2b(200, false).ready("tinyassets-sandbox:v1")).toBe(false);
    expect(await e2b(404, true).ready("tinyassets-sandbox:v1")).toBe(false);
    await expect(e2b(500, true).ready("tinyassets-sandbox:v1")).rejects.toThrow(/alias lookup/);
    await expect(e2b(200, true, 503).ready("tinyassets-sandbox:v1")).rejects.toThrow(/tag lookup/);
    await expect(e2b(200, true).ready("tinyassets-sandbox")).rejects.toThrow(/name:tag/);
    // Both lookups send the key and ask for JSON.
    expect(headers.length).toBeGreaterThan(0);
    for (const each of headers) {
      expect(each).toEqual({ "X-API-Key": E2B_KEY, Accept: "application/json" });
    }
  });

  it("names the pair it deploys by the template, the runner the sandbox Worker ships and the " +
     "probes beside it, the one pair tag the checks mark", async () => {
    const { runner, probes } = bundles();
    expect([runner.sha, probes.sha].every((each) => /^[0-9a-f]{64}$/.test(each))).toBe(true);
    expect(bundles().runner.sha).toBe(runner.sha);  // the same bytes every build
    const w = world();
    await runMode(w, "plan");
    expect(w.events).toContain("bundle the runner and the probes");
    expect(w.events.filter((event) => event === "the tree's image")).toHaveLength(1);
    expect(PAIR)
      .toBe(`${TEMPLATE}-r${sha(RUNNER).slice(0, 16)}-p${sha(PROBE_BUNDLE).slice(0, 16)}`);
    expect(() => pairTag(TEMPLATE, sha(RUNNER), "probes")).toThrow(/SHA-256/);
    // This tree's image, as image.ts prints it: the kit's pin check reads it so.
    expect(treeImage()).toMatch(/^tinyassets-sandbox:s[0-9a-f]{16}$/);
  });

  it("plans a tree whose image is not the pin, and refuses to apply it", async () => {
    const w = world({ image: "tinyassets-sandbox:s0123456789abcdef" });
    const work = await runMode(w, "plan");
    expect(work.plan).toContain(`the pinned template ${TEMPLATE} is not this tree's image ` +
      "tinyassets-sandbox:s0123456789abcdef: build it (sandbox-template.yml, build) and pin " +
      "its passed pair");
    await expect(runMode(world({ image: "tinyassets-sandbox:s0123456789abcdef" }), "dark",
                         ["--yes"]))
      .rejects.toThrow(`the pinned template ${TEMPLATE} is not this tree's image ` +
        "tinyassets-sandbox:s0123456789abcdef: build it (sandbox-template.yml, build) and pin " +
        "its passed pair; nothing is deployed");
  });

  it("versions a later deploy by the id wrangler prints", () => {
    expect(versionOf("Uploaded tinyassets-owner\nWorker Version ID: " +
                     "0f7e3a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b\n"))
      .toBe("0f7e3a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b");
    expect(() => versionOf("Uploaded tinyassets-owner")).toThrow(/Version ID/);
  });
});

describe("platform-deploy.yml", () => {
  type Job = { if?: string; needs?: string | string[]; environment?: string;
               "timeout-minutes"?: number; env?: Record<string, string>;
               steps: { name?: string; run?: string; env?: Record<string, string> }[] };
  const file = workflow("platform-deploy.yml") as {
    on: Json; jobs: Record<string, Job> };
  const { plan, pair, apply } = file.jobs as Record<string, Job>;
  const runs = (job: Job) => job.steps.map((each) => each.run ?? "").join("\n");

  it("is run by hand alone, and reaches no host", () => {
    expect(Object.keys(file.on)).toEqual(["workflow_dispatch"]);
    const modes = ((file.on["workflow_dispatch"] as Json)["inputs"] as Json)["mode"] as
      { options: string[] };
    // Every mode the workflow offers is a row of the kit's (the pair is its own job).
    expect(modes.options.filter((mode) => !Object.hasOwn(MODES, mode))).toEqual([]);
    for (const job of [plan!, pair!, apply!]) {
      expect(runs(job)).not.toMatch(/ssh|scp|droplet|\/etc\/tinyassets/i);
    }
  });

  /**
   * Every command `job`'s steps run, its scripts run as the job runs them with `inputs` as the
   * dispatch's, `node`, `npm` and `python3` recording their arguments in place of running.
   */
  function invoked(job: Job, inputs: Record<string, string>): { argv: string; env: string[] }[] {
    const bin = mkdtempSync(join(tmpdir(), "workflow-"));
    try {
      const log = join(bin, "log");
      for (const tool of ["node", "npm", "python3"]) {
        writeFileSync(join(bin, tool), `#!/bin/sh\nprintf '%s\\t%s\\n' "${tool} $*" "$(env | ` +
                      `cut -d= -f1 | grep -E '_KEY$|_TOKEN$|_SECRET$' | sort | tr '\\n' ' ')" ` +
                      `>> "${log}"\n`, { mode: 0o755 });
      }
      for (const each of job.steps.filter((one) => one.run !== undefined)) {
        const ran = spawnSync("bash", ["-c", each.run!], {
          cwd: REPO, encoding: "utf8",
          env: { PATH: `${bin}:/usr/bin:/bin`, ...inputs,
                 ...Object.fromEntries(Object.keys(each.env ?? {}).map((name) => [name, "x"])) } });
        expect([each.name, ran.status, ran.stderr]).toEqual([each.name, 0, ""]);
      }
      return readFileSync(log, "utf8").split("\n").filter(Boolean).map((line) => {
        const [argv, env] = line.split("\t");
        return { argv: argv!, env: (env ?? "").trim().split(" ").filter(Boolean) };
      });
    } finally {
      rmSync(bin, { recursive: true, force: true });
    }
  }
  const KIT = "node --experimental-strip-types platform/deploy/deploy.ts";
  const OPTIONS = { MODE: "dark", ROTATE: "ROUTING_MAC_KEY", MIGRATE: "owner", RECORD: "" };

  it("plans in a job of its own, with no production secret, the chosen mode's plan", () => {
    expect(plan!.environment).toBeUndefined();
    expect(Object.values(plan!.env ?? {}).some((value) => /secrets\.(?!CLOUDFLARE_API_TOKEN)/
      .test(value))).toBe(false);
    const modes = ((file.on["workflow_dispatch"] as Json)["inputs"] as Json)["mode"] as
      { options: string[] };
    for (const mode of modes.options) {
      const record = mode === "handback" ? "7001" : "";
      const ran = invoked(plan!, { MODE: mode, ROTATE: "", MIGRATE: "", RECORD: record });
      expect(ran.map((each) => each.argv)).toEqual(["npm ci", `${KIT} --mode ${mode} --public${
        record === "" ? "" : ` --record ${record}`}`]);
      // Each is a run the kit takes, and one that changes nothing.
      const run = parse(ran[1]!.argv.split(" ").slice(3), PUBLIC);
      expect([run.mode, run.reach, run.apply]).toEqual([mode, "public", false]);
    }
    expect(invoked(plan!, OPTIONS).map((each) => each.argv)[1])
      .toBe(`${KIT} --mode dark --public --rotate ROUTING_MAC_KEY --migrate owner`);
  });

  it("checks the pair after the plan, in production, with the E2B key alone, for the modes " +
     "that deploy Workers", () => {
    expect(pair!.needs).toBe("plan");
    expect(pair!.environment).toBe("production");
    expect(pair!.env).toBeUndefined();
    // No Python: the checks are checks.ts, on the E2B client (image.py's SDK builds alone).
    expect(invoked(pair!, {})).toEqual([
      { argv: "npm ci", env: [] },
      { argv: `${KIT} --mode pair`, env: ["E2B_API_KEY"] }]);
    expect(JSON.stringify(pair)).not.toMatch(/setup-python|pip install/);
    // The modes whose rows deploy Workers are the ones the pair job runs for.
    const deploying = (Object.keys(MODES) as Mode[]).filter((mode) =>
      MODES[mode].applies && MODES[mode].steps.some((each) => each.name === "workers"));
    expect(deploying).toEqual(["dark", "routes"]);
    expect(pair!.if).toContain(`(${deploying.map((mode) => `inputs.mode == '${mode}'`)
      .join(" || ")})`);
  });

  it("gives the pair check longer than the checks' throwaway sandboxes live", () => {
    // The probes' own plan, as checks.ts runs it: each sandbox's lifetime, one after another.
    const out = mkdtempSync(join(tmpdir(), "ta-plan-"));
    try {
      bundles(execFileSync, out);
      const plan = JSON.parse(execFileSync(process.execPath, [join(out, "probes.mjs"), "plan"],
                                           { encoding: "utf8" })) as
        { sandboxes: { timeout_s: number }[] };
      expect(plan.sandboxes).toHaveLength(3);
      expect(Number(pair!["timeout-minutes"]) * 60)
        .toBeGreaterThan(plan.sandboxes.reduce((sum, each) => sum + each.timeout_s, 0));
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("applies after the plan and the pair, in production, the mode's own run", () => {
    expect(apply!.needs).toEqual(["plan", "pair"]);
    expect(apply!.environment).toBe("production");
    expect(apply!.if).toContain("needs.pair.result == 'success'");
    // The modes that deploy nothing run without a pair: the ones the workflow offers whose
    // rows deploy no Worker.
    const offered = (((file.on["workflow_dispatch"] as Json)["inputs"] as Json)["mode"] as
      { options: Mode[] }).options.filter((mode) => mode !== "plan");
    const pairless = offered.filter((mode) =>
      !MODES[mode].steps.some((each) => each.name === "workers"));
    expect(pairless).toEqual(["probe", "h2", "handback"]);
    expect(apply!.if).toContain("(inputs.mode == 'h2' || inputs.mode == 'probe' || " +
                                "inputs.mode == 'handback')");
    for (const mode of offered) {
      const record = mode === "handback" ? "7001" : "";
      const ran = invoked(apply!, { MODE: mode, ROTATE: "", MIGRATE: "", RECORD: record });
      expect(ran.map((each) => each.argv)).toEqual(["npm ci", `${KIT} --mode ${mode} --yes${
        record === "" ? "" : ` --record ${record}`}`]);
      const run = parse(ran[1]!.argv.split(" ").slice(3), ENV);
      expect([run.mode, run.reach, run.apply, run.record])
        .toEqual([mode, "secrets", true, record === "" ? null : record]);
    }
    expect(invoked(apply!, OPTIONS).map((each) => each.argv)[1])
      .toBe(`${KIT} --mode dark --yes --rotate ROUTING_MAC_KEY --migrate owner`);
    // The account is read from the zone, never held as an input.
    expect(PUBLIC_INPUTS).not.toContain("CLOUDFLARE_ACCOUNT_ID");
    expect(JSON.stringify(file)).not.toContain("CLOUDFLARE_ACCOUNT_ID");
  });

  it("hands the plan and apply jobs exactly what the kit reads, every secret a config " +
     "requires among them", () => {
    // The dispatch's own inputs, which both jobs pass to the kit.
    const dispatch = ["MODE", "ROTATE", "MIGRATE", "RECORD"];
    // The handles canary's own bearer (scripts/_canary_common.py), so Fact 11's canary runs.
    const canary = execFileSync("python3", ["-I", "-c",
      "import sys; sys.path.insert(0, sys.argv[1]); " +
      "from scripts._canary_common import CANARY_TOKEN_ENV; sys.stdout.write(CANARY_TOKEN_ENV)",
      REPO], { encoding: "utf8" });
    const secrets = [...SECRET_INPUTS, ...OPTIONAL, canary, ...H2_INPUTS_LIST];
    expect(Object.keys(plan!.env!).sort()).toEqual([...PUBLIC_INPUTS, ...dispatch].sort());
    expect(Object.keys(apply!.env!).sort())
      .toEqual([...PUBLIC_INPUTS, ...secrets, ...dispatch].sort());
    for (const name of secrets) {
      expect([name, apply!.env![name]]).toEqual([name, `\${{ secrets.${name} }}`]);
    }
    for (const env of [plan!.env!, apply!.env!]) {
      for (const name of PUBLIC_INPUTS) {
        expect([name, env[name]]).toEqual([name, expect.stringMatching(
          new RegExp(`^\\$\\{\\{ (secrets|vars)\\.${name} \\}\\}$`))]);
      }
      for (const name of dispatch) {
        expect([name, env[name]]).toEqual([name, `\${{ inputs.${name.toLowerCase()} }}`]);
      }
    }
  });
});

/** Paths the doors' routes cover, and two they do not. */
const PROBES = ["tinyassets.io/mcp", "tinyassets.io/mcp?x=1", "tinyassets.io/api/tools/a",
                "tinyassets.io/.well-known/oauth-protected-resource/mcp",
                "tinyassets.io/.well-known/oauth-authorization-server",
                "tinyassets.io/app/api/keys", "tinyassets.io/oauth/token",
                "tinyassets.io/authorize", "tinyassets.io/hooks/inbound/x",
                "tinyassets.io/hooks/e2b/lifecycle",
                "tinyassets.io/app", "tinyassets.io/site/x"];

/** The Worker Cloudflare sends `path` to: the most specific matching pattern's. */
function servedBy(routes: Iterable<Route>, path: string): string | undefined {
  let best: Route | undefined;
  for (const route of routes) {
    const wild = route.pattern.endsWith("*");
    const stem = wild ? route.pattern.slice(0, -1) : route.pattern;
    if ((wild ? path.startsWith(stem) : path === stem)
        && (best === undefined || stem.length > best.pattern.replace(/\*$/, "").length)) {
      best = route;
    }
  }
  return best?.script;
}

/** A fake of Cloudflare's routes API over `start`, checking every path after each change. */
function zoneApi(start: readonly Route[], fail?: string) {
  const table = new Map(start.map((route) => [route.id, { ...route }]));
  const before = new Map(PROBES.map((path) => [path, servedBy(table.values(), path)]));
  const calls: string[] = [];
  let next = 100;
  const call = async (path: string, init?: RequestInit): Promise<CfAnswer> => {
    const method = init?.method ?? "GET";
    calls.push(`${method} ${path}`);
    if (path === "/zones?name=tinyassets.io") {
      return ok([{ id: "z", account: { id: "a".repeat(32) } }]);
    }
    if (method === "GET" && path === "/zones/z/workers/routes") {
      return ok([...table.values()]);
    }
    if (fail !== undefined && `${method} ${path}` === fail) {
      return { status: 500, body: { success: false, errors: [{ code: 10000 }] } };
    }
    const body = JSON.parse(String(init?.body ?? "{}")) as { pattern?: string; script?: string };
    const id = path.split("/").at(-1)!;
    if (method === "POST" && path === "/zones/z/workers/routes") {
      table.set(`n${next}`, { id: `n${next++}`, pattern: body.pattern!, script: body.script! });
    } else if (method === "PUT" && table.has(id)) {
      table.set(id, { id, pattern: body.pattern!, script: body.script! });
    } else if (method === "DELETE" && table.has(id)) {
      table.delete(id);
    } else {
      return { status: 404, body: { success: false, errors: [{ code: 10020 }] } };
    }
    for (const [probe, server] of before) {
      // A path served before the handover is served after every one of its changes.
      if (server !== undefined) {
        expect([probe, servedBy(table.values(), probe)]).not.toEqual([probe, undefined]);
      }
    }
    return ok({});
  };
  return Object.assign(call, { calls, table, before });
}

/** A fresh dark run's plan, in order: what the founder's first `dark` changes. */
const DARK_FRESH = [
  "the migration locks pin every migration the owner and sandbox Workers ship",
  "set the doors' OWNER_ISSUER to https://auth.test, held to its discovery",
  `the pair ${PAIR} passed its standing checks in this E2B team`,
  `WorkOS redirect URI ${CALLBACK}: registered`,
  `WorkOS's authorize takes the doors' client and ${CALLBACK}: it sent the browser to ` +
  "auth.test's sign-in",
  ...Object.entries(NAMESPACES).map(([binding, title]) =>
    `create the KV namespace ${title} (${binding})`),
  "create the R2 bucket tinyassets-files",
  "create the R2 bucket tinyassets-import-staging",
  "create the R2 bucket tinyassets-final-backup",
  "create the queue tinyassets-sandbox-ring",
  "create the queue tinyassets-erasure",
  "create the queue tinyassets-import",
  "create the queue tinyassets-import-dlq",
  "the Cloudflare token listed the account's KV namespaces, R2 buckets and queues",
  ...Object.entries(SECRETS).flatMap(([worker, names]) =>
    names.map((name) => `set the secret ${name} on ${NAMES[worker]}`)),
  ...WORKERS.map((worker) => `deploy ${NAMES[worker]}, touching no route`),
  "write the control record closed (outside off, canary none, inbound off, the founder's " +
  "tier numbers): the API answered there is none",
  `E2B lifecycle webhook ${E2B_HOOK.url}: registered, disabled`,
];

/** A routes run's plan over a held account: the cutover's. */
const ROUTES_HELD = [
  "the migration locks pin every migration the owner and sandbox Workers ship",
  "set the doors' OWNER_ISSUER to https://auth.test, held to its discovery",
  `the pair ${PAIR} passed its standing checks in this E2B team`,
  `record the zone's ${LIVE.length + 2} routes and the handover's 8 changes, the egress ` +
  "domain (none) and E2B's lifecycle webhook (disabled) as " +
  "deploy:routes:local-20261107T120000Z in tinyassets-control, before any change: hand back " +
  "with --mode handback --record local-20261107T120000Z",
  `WorkOS redirect URI ${CALLBACK}: present`,
  `WorkOS's authorize takes the doors' client and ${CALLBACK}: it sent the browser to ` +
  "auth.test's sign-in",
  "the Cloudflare token listed the account's KV namespaces, R2 buckets and queues",
  ...WORKERS.map((worker) => `upload and deploy a version of ${NAMES[worker]}, touching no route`),
  `move the route tinyassets.io/mcp* from tinyassets-mcp-proxy to ${DOORS}`,
  `add the route tinyassets.io/api/* for ${DOORS}`,
  `add the route tinyassets.io/hooks/inbound/* for ${DOORS}`,
  `add the route tinyassets.io/hooks/e2b/* for ${DOORS}`,
  `add the route tinyassets.io/app/api/* for ${DOORS}`,
  `add the route tinyassets.io/.well-known/oauth-* for ${DOORS}`,
  `delete tinyassets-mcp-proxy's narrower route ` +
  `tinyassets.io/.well-known/oauth-protected-resource*, its paths now ${DOORS}'s`,
  `delete tinyassets-mcp-proxy's narrower route ` +
  `tinyassets.io/.well-known/oauth-authorization-server*, its paths now ${DOORS}'s`,
  `add the custom domain ${EGRESS_DOMAIN} for ${DOORS} (wrangler triggers deploy)`,
  "record in deploy:routes:local-20261107T120000Z the DNS records the custom domain made: AAAA",
  `E2B lifecycle webhook ${E2B_HOOK.url}, after the routes: present, enabled set, enabled`,
];
