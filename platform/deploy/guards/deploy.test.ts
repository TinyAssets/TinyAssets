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
 * Each lookup is checked too: a failed one is never "absent"; a held
 * keyring is never replaced, and is rotated only by adding a key; the control record is
 * written only when the API answers that it is absent; an absent discovery field is unknown,
 * and unknown stops the kit; the kit runs from an approved manual workflow whose secrets come
 * from the environment's store.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

import { beforeAll, describe, expect, it, vi } from "vitest";

import { DARK, keyring as ringSecret } from "../../ci/fixtures";
import { PLATFORM, REPO } from "../../ci/paths";
import { workflow } from "../../ci/workflows";
import { deployProblems, locksOf, migrated, readLock } from "../../ci/kit/locks";
import { WRANGLER, workerFolders } from "../../ci/wrangler";
import identity from "../../contract/identity.json" with { type: "json" };
import { EGRESS_HOST } from "../../contract/src/egress";
import { OWNER_ENTRY } from "../../contract/src/ids";
import { IMPORT_KEY } from "../../contract/src/import-message";
import { type Domain, openBlob, parseRing, sealBlob } from "../../contract/src/envelope";
import {
  APP_RESOURCE, HOST, ORIGIN, routeTakes, UPSTREAM_CALLBACK, WEB_CLIENT, WEB_REDIRECT, ZONE,
} from "../../contract/src/identity";
import {
  answerKey, answerOf, LOCATE_PAGE, macHolds, type OperatorMessage, operatorMessage,
} from "../../contract/src/operator-message";
import { PLATFORM_CALLER } from "../../contract/src/binding";
import { QUEUES as QUEUE_NAMES } from "../../contract/src/queue-names";
import {
  closedToAll, CONTROL_KEY, outsideOpen, ownerOpen, parseControl,
} from "../../contract/src/control";
import { E2bAdmin } from "../../sandbox/e2b/admin";
import { annotate, maskLine, output, runLink, runOf, summarize } from "../actions";
import {
  Cloudflare, describeStep, handback, handover, type Route,
} from "../cloudflare";
import {
  authorizeCheck, deployedConfig, firstPartyLink, forwardOnly,
  E2B_HOOK, heldHook, hookSetsSecret, type Io, keyring, keyringAdd, keyringNew, keyringRetire,
  lifecycleHook, lockCounts, type Mode, MODES, parse,
  holding, perform, PUBLIC_INPUTS, registerRedirect, type Rolled, rolloutOf, type RolloutRecord, routedConfig,
  type RouteRecord, secretPlan, upstreamFrom, versionFlags, versionOf, wranglerFor,
} from "../deploy";
import {
  type Bundle, type Bundles, BUNDLES, bundles, pairTag, treeImage,
} from "../../sandbox/template/pair";
import {
  boundBy, BUCKET_LOCKS, BUCKETS, CONFIGS, DOORS, FILES_BUCKET, FORWARD, forwardCounts,
  IMPORT_NAMES,
  KEYRINGS, MADE, MIGRATES, NAMES, NAMESPACES, OPERATOR_NAMES, OPTIONAL, QUEUES, RETIRED,
  retiredWorkers, ROUTES, SECRET_INPUTS, SECRETS, WORKERS, workerNames,
} from "../facts";
import { INPUTS as CANARY_INPUTS } from "../canary";
import { INPUTS as H2_INPUTS } from "../h2-probes";
import { BACK_INPUTS, RESTORE_KEY } from "../offsite";
import {
  ERASE_LOSES, keyHashes, POLL_MS, RESTORE_LOSES, rotationKey, RUN_MS,
} from "../operator";
import platform from "../platform.json" with { type: "json" };
import { night, nightsAnswer } from "./copy-nights";

// The kit here is the cutover's: the doors' host is the zone, whatever host the identity names
// today (the live stage's, while it runs), so a change of `host` changes no case. A host under
// the zone is the kit's other case, run over an identity of its own (`at a host under the
// zone`, below).
vi.mock("../../contract/identity.json", async (actual) => {
  const { default: real } = await actual<{ default: { zone: string } }>();
  return { default: { ...real, host: real.zone } };
});

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
/** The runner and probes the fake build makes, and the pair they make with the pin. */
const RUNNER = "the runner";
const PROBE_BUNDLE = "the probes";
const sha = (text: string) => createHash("sha256").update(text).digest("hex");
const bundled = (text: string): Bundle => ({ bytes: Buffer.from(text), sha: sha(text) });
const PAIR = pairTag(TEMPLATE, sha(RUNNER), sha(PROBE_BUNDLE));
/** The commit the fake checkout is at. */
const COMMIT = "c0ffee".padEnd(40, "1");
/** Each migrated Worker's locks as they pin it, by path: what the locks' command line prints. */
const PINNED: Readonly<Record<string, Readonly<Record<string, readonly string[]>>>> =
  Object.fromEntries(await Promise.all(migrated().map(async (worker) =>
    [worker, Object.fromEntries(Object.keys(await locksOf(worker))
      .map((path) => [path, readLock(path).deployed]))])));

/** A Cloudflare answer, as a fake gives it: its status and its JSON body. */
interface CfAnswer {
  readonly status: number;
  readonly body: unknown;
}

const CF_API = "https://api.cloudflare.com/client/v4";

/** The client over a fake answering each call by its path from `/client/v4`. */
function client(call: (path: string, init?: RequestInit) => Promise<CfAnswer>): Cloudflare {
  return new Cloudflare("cf-token", async (url, init) => {
    const { status, body } = await call(url.slice(CF_API.length), init);
    return Response.json(body, { status });
  });
}

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
  ROUTING_MAC_KEY: "mac-key", GATEWAY_KEYRING: RING, FILES_KEYRING: RING, OPERATOR_KEYRING: RING,
  E2B_WEBHOOK_SECRET: "hook-secret", INBOUND_SLACK_SIGNING_SECRET: "slack-secret",
  PLATFORM_CANARY_TOKEN: "clock-token", PUSHOVER_USER_KEY: "pager-user",
  PUSHOVER_APP_TOKEN: "pager-token", PATH: "/bin", HOME: "/home/test",
  OFFSITE_BUCKET: "tinyassets-offsite-abcd1234", OFFSITE_B2_READ_KEY_ID: "b2-read-id",
  OFFSITE_B2_READ_KEY: "b2-read-secret", OFFSITE_R2_WRITE_ACCESS_KEY_ID: "r2-write-id",
  OFFSITE_R2_WRITE_SECRET_ACCESS_KEY: "r2-write-secret",
};
/** What the plan job holds. */
const PUBLIC = Object.fromEntries(PUBLIC_INPUTS.map((name) => [name, ENV[name]!]));

/** The options of its own each mode needs, by the dispatch's input that gives each. */
const NEEDED: Partial<Record<Mode, Readonly<Record<string, string>>>> = {
  handback: { RECORD: "7001" }, rollback: { RECORD: "7001" }, owner: { OWNER: "user_alice", ACT: "tier=paid" },
  restore: { OWNER: "user_alice" }, copyback: { OWNER: "user_alice" },
  erase: { OWNER: "user_alice" },
  rotation: { KEYRING: "FILES_KEYRING" }, retire: { KEYRING: "GATEWAY_KEYRING" } };
/** `mode`'s needed options, as its command line gives them. */
const needed = (mode: Mode): string[] => Object.entries(NEEDED[mode] ?? {})
  .flatMap(([name, value]) => [`--${name.toLowerCase()}`, value]);

/**
 * The old proxy's live routes, and the site's, written on the doors' host, which is the zone
 * here: a host under it shares none of them (`at a host under the zone`, below).
 */
const LIVE: readonly Route[] = [
  { id: "r1", pattern: `${HOST}/mcp*`, script: "tinyassets-mcp-proxy" },
  { id: "r2", pattern: `${HOST}/app*`, script: "tinyassets-mcp-proxy" },
  { id: "r3", pattern: `${HOST}/api*`, script: "tinyassets-mcp-proxy" },
  { id: "r4", pattern: `${HOST}/.well-known/oauth-protected-resource*`,
    script: "tinyassets-mcp-proxy" },
  { id: "r5", pattern: `${HOST}/.well-known/oauth-authorization-server*`,
    script: "tinyassets-mcp-proxy" },
  { id: "r6", pattern: `${HOST}/site*`, script: "site" },
];

interface WorldOptions {
  /** Everything a dark run makes is there already. */
  readonly held?: boolean;
  /** E2B holds the pair's tag. */
  readonly paired?: boolean;
  /** This tree's image, as `image.ts` computes it: the pin by default. */
  readonly image?: string;
  /** The migration locks' command line refuses every Worker so. */
  readonly lockRefused?: string;
  /** What the locks pin, by Worker and lock path, in place of the checked-in locks. */
  readonly pinned?: Readonly<Record<string, Readonly<Record<string, readonly string[]>>>>;
  /** A wrangler command that fails. */
  readonly failing?: string;
  /** A Cloudflare listing the API refuses (500), by its path from the account. */
  readonly refusingListing?: string;
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
  /** DNS makes and deletes the API refuses (403): a token without DNS Edit. */
  readonly refusingDns?: boolean;
  /** Each bucket's lock rules, in place of none (fresh) or the kit's (held). */
  readonly locks?: Readonly<Record<string, readonly unknown[]>>;
  /** The R2 buckets and queues the account lists, in place of none (fresh) or all (held). */
  readonly listed?: { readonly buckets: readonly string[]; readonly queues: readonly string[] };
  /** The account still holds the retired Workers (`platform.json`). */
  readonly retired?: boolean;
  /** Cloudflare refuses a Worker's delete: another Worker still references it. */
  readonly refusingDelete?: boolean;
  /**
   * How the doors answer each message sent to the operator queue: its outcome, or none (it is
   * never answered). Each is answered at once by default, with its args as its result.
   */
  readonly doors?: (message: OperatorMessage) => Parameters<typeof answerOf>[2] | null;
  /** The owners the directory holds a record of. */
  readonly owners?: readonly string[];
  /** The doors hold the import window's key. */
  readonly windowOpen?: boolean;
  /** The names rclone lists under a copy back's prefix: the copy's and R2's. */
  readonly copied?: { readonly copy: readonly string[]; readonly r2: readonly string[] };
  /** Each Worker's active deployment, by script, in place of none (fresh) or `HELD_SERVING`. */
  readonly serving?: Readonly<Record<string, readonly Served[]>>;
  /** Cloudflare refuses each deployment the kit makes over its API, with this code. */
  readonly refusingDeploy?: number;
  /** Secrets a held account's Workers lack: optional ones the founder never stored. */
  readonly lacking?: readonly string[];
}

/** A version a fake Worker serves: its id, its share and its tag. */
interface Served {
  readonly version: string;
  readonly percentage: number;
  readonly tag: string | null;
}

/** The commit a held account's versions were deployed from, as their tags carry it. */
const BEFORE = "b".repeat(12);

/**
 * A held account's deployments: each Worker serving one version an earlier run tagged, with
 * the locks' counts the checked-in locks pin, so a rollback takes it back there.
 */
const HELD_SERVING: Readonly<Record<string, readonly Served[]>> = Object.fromEntries(
  WORKERS.map((worker) => {
    const counts = Object.keys(PINNED[worker] ?? {}).sort().map((path) =>
      PINNED[worker]![path]!.length);
    return [NAMES[worker]!, [{ version: `before-${worker}`, percentage: 100,
                               tag: `${BEFORE}${counts.length === 0 ? "" : `-m${counts.join(".")}`}` }]];
  }));

/** The world's clock. */
const NOW = new Date("2026-11-07T12:00:00.000Z");

/**
 * Each Worker's active deployment, by script, as a map, and every deployment before it: each
 * one set is a new deployment, as Cloudflare lists them, newest first.
 */
class Deployed extends Map<string, { version: string; percentage: number }[]> {
  readonly #past = new Map<string, { version: string; percentage: number }[][]>();

  override set(script: string, serving: { version: string; percentage: number }[]): this {
    const now = this.get(script);
    if (now !== undefined) {
      this.#past.set(script, [now, ...this.#past.get(script) ?? []]);
    }
    return super.set(script, serving);
  }

  /** `script`'s deployments, newest first. */
  history(script: string): { version: string; percentage: number }[][] {
    const now = this.get(script);
    return now === undefined ? [] : [now, ...this.#past.get(script) ?? []];
  }
}

/**
 * A fake of everything the kit reaches, as one ordered event log: Cloudflare's API and
 * wrangler (the account's state changing as the kit changes it), WorkOS, the owner issuer,
 * E2B, the canary, the pair's checks, the migration locks' command line, H2's probes and
 * rclone.
 */
function world(options: WorldOptions = {}) {
  const held = options.held === true;
  const events: string[] = [];
  const logs: string[] = [];
  const secretsPut: Record<string, string> = {};
  /** Each `--secrets-file` wrangler was given: its Worker, path, mode and names. */
  const secretFiles: { worker: string; path: string; mode: number; names: string[] }[] = [];
  /** The account each wrangler command was given, in order. */
  const accounts: (string | undefined)[] = [];
  let pairPassed = options.paired !== false;
  const namespaces = new Map(held ? Object.values(NAMESPACES).map((title) => [title, `ns-${title}`])
    : []);
  const secrets = new Map<string, string[]>(held ? Object.entries(SECRETS).map(([worker, names]) =>
    [NAMES[worker]!, names.filter((name) => !(options.lacking ?? []).includes(name))]) : []);
  if (held) {
    secrets.set(NAMES["codemode"]!, []);
  }
  if (options.windowOpen === true) {
    secrets.get(NAMES["doors"]!)!.push(IMPORT_KEY);
  }
  for (const name of options.retired === true ? RETIRED : []) {
    secrets.set(name, []);
  }
  const routes = new Map(LIVE.map((route) => [route.id, { ...route }]));
  const hooks: Json[] = held ? [{ id: "w1", name: E2B_HOOK.name, url: E2B_HOOK.url,
                                  enabled: false, events: [...E2B_HOOK.events] }] : [];
  /** KV, by namespace id and key: the control record when held. */
  const kv = new Map<string, string>(held
    ? [[`ns-${NAMESPACES["CONTROL"]}/${CONTROL_KEY}`, JSON.stringify(DARK)],
       ...(options.owners ?? []).map((owner): [string, string] =>
         [`ns-${NAMESPACES["DIRECTORY"]}/${OWNER_ENTRY}${owner}`, '{"s":"admitted","t":1}'])]
    : []);
  /** The zone's routes after each route change, in order. */
  const tables: Route[][] = [];
  /** The egress host's custom domains, by id. */
  const domains = new Map<string, { id: string; hostname: string; service: string }>(
    options.egress === undefined ? []
      : [["d0", { id: "d0", hostname: EGRESS_HOST, service: options.egress }]]);
  /**
   * The outward changes left before the run is killed (undefined: never): a route's, the
   * custom domain's (wrangler's triggers deploy, a detach) and the webhook's.
   */
  const kill = { after: undefined as number | undefined };
  const killed = () => kill.after !== undefined && kill.after-- <= 0;
  /** Writes that answer as done and change nothing: the hook's, the custom domain's, KV's. */
  const inert = new Set<"hook" | "domain" | "dns" | "kv" | "deploy">();
  /** The zone's DNS records: the egress host's given ones, and others on other names. */
  const dns: { id: string; type: string; name: string; content: string; proxied?: unknown }[] = [
    ...(options.dns ?? []).map((type, at) => ({ id: `x${at}`, type, name: EGRESS_HOST,
                                                 content: "192.0.2.1" })),
    { id: "o1", type: "A", name: ZONE, content: "192.0.2.2" },
    { id: "o2", type: "CNAME", name: `www.${ZONE}`, content: ZONE },
    { id: "o3", type: "TXT", name: EGRESS_HOST, content: "v=spf1 -all" },
  ];
  const ATTACHED = { id: "dns-egress", type: "AAAA", name: EGRESS_HOST, content: "100::" };
  let made = 0;
  /** The account's R2 buckets and queues, and what the run made, in order. */
  const buckets = new Set(options.listed?.buckets ?? (held ? BUCKETS : []));
  const queues = new Set(options.listed?.queues ?? (held ? QUEUES : []));
  const created: string[] = [];
  /** The messages sent to a queue, by its id, and the doors' answers, by key in their bucket. */
  const sent: { queue: string; message: OperatorMessage }[] = [];
  const answers = new Map<string, unknown>();
  /** Each bucket's lock rules: a held account's as the kit sets them, unless `locks` says. */
  const locks = new Map<string, unknown[]>(Object.entries(BUCKET_LOCKS).map(([bucket, days]) =>
    [bucket, [...options.locks?.[bucket] ?? (held ? [{ id: "kept", enabled: true,
      condition: { type: "Age", maxAgeSeconds: days * 86_400 } }] : [])]]));
  /** Each version the account holds, by id: its Worker and its tag. */
  const versions = new Map<string, { script: string; tag: string | null }>();
  /** Each Worker's active deployment, by script: a held account's one version before. */
  const deployments = new Deployed();
  for (const [script, serving] of Object.entries(options.serving ?? (held ? HELD_SERVING : {}))) {
    deployments.set(script, serving.map(({ version, percentage }) => ({ version, percentage })));
    for (const { version, tag } of serving) {
      versions.set(version, { script, tag });
    }
  }
  let uploads = 0;
  /** A new version of `script` tagged `tag`: its id. */
  const version = (script: string, tag: string | null): string => {
    uploads += 1;
    const id = `0f7e3a3c-1b2d-4e5f-8a9b-${String(uploads).padStart(12, "0")}`;
    versions.set(id, { script, tag });
    return id;
  };
  const json = (body: unknown, status = 200) => Response.json(body, { status });
  const cf = (path: string, method: string, body: string | undefined): Response => {
    const account = `/accounts/${ACCOUNT}`;
    if (options.refusingListing !== undefined && method === "GET"
        && path.startsWith(`${account}/${options.refusingListing}`)) {
      return json({ success: false, errors: [{ code: 10000 }] }, 500);
    }
    const deploying = /^\/accounts\/a+\/workers\/scripts\/([a-z-]+)\/deployments(\?per_page=100)?$/
      .exec(path);
    if (deploying !== null && method === "GET") {
      // The latest alone on the default page, as the kit reads it; every one on the long page.
      const history = deployments.history(deploying[1]!);
      return json({ success: true, result: { deployments: (deploying[2] === undefined
        ? history.slice(0, 1) : history).map((serving, at) => ({ id: `d${at}`,
          strategy: "percentage", versions: serving.map((each) =>
            ({ version_id: each.version, percentage: each.percentage })) })) },
                    result_info: { page: 1, per_page: 100, total_count: history.length } });
    }
    if (deploying !== null && method === "POST") {
      if (options.refusingDeploy !== undefined) {
        return json({ success: false, errors: [{ code: options.refusingDeploy }] }, 400);
      }
      const given = JSON.parse(body!) as { strategy: string;
                                           versions: { version_id: string; percentage: number }[] };
      expect(given.strategy).toBe("percentage");
      for (const each of given.versions) {
        expect(versions.get(each.version_id)?.script).toBe(deploying[1]);
      }
      if (!inert.has("deploy")) {
        deployments.set(deploying[1]!, given.versions.map((each) =>
          ({ version: each.version_id, percentage: each.percentage })));
      }
      return json({ success: true, result: { id: "d2" } });
    }
    const versioned = /^\/accounts\/a+\/workers\/scripts\/([a-z-]+)\/versions\/([^/]+)$/
      .exec(path);
    if (versioned !== null) {
      const held = versions.get(versioned[2]!);
      return held === undefined || held.script !== versioned[1]
        ? json({ success: false, errors: [{ code: 10007 }] }, 404)
        : json({ success: true, result: { id: versioned[2], annotations: held.tag === null ? {}
          : { "workers/tag": held.tag, "workers/message": COMMIT } } });
    }
    const given = method === "POST" ? JSON.parse(body!) as Record<string, string> : {};
    if (path === `${account}/storage/kv/namespaces` && method === "POST") {
      created.push(`KV namespace ${given["title"]}`);
      namespaces.set(given["title"]!, `made-${given["title"]}`);
      return json({ success: true, result: { id: `made-${given["title"]}` } });
    }
    if (path.startsWith(`${account}/r2/buckets/`) && path.endsWith("/lock")) {
      const locked = path.slice(`${account}/r2/buckets/`.length, -"/lock".length);
      if (!buckets.has(locked)) {
        return json({ success: false, errors: [{ code: 10006 }] }, 404);
      }
      if (method === "PUT") {
        created.push(`R2 lock ${locked} ${body}`);
        locks.set(locked, (JSON.parse(body!) as { rules: unknown[] }).rules);
      }
      return json({ success: true, result: method === "PUT" ? {}
        : { rules: locks.get(locked) ?? [] } });
    }
    if (path === `${account}/r2/buckets`) {
      if (method === "POST") {
        created.push(`R2 bucket ${given["name"]}`);
        buckets.add(given["name"]!);
        locks.set(given["name"]!, []);
      }
      return json({ success: true, result: method === "POST" ? {}
        : { buckets: [...buckets].map((name) => ({ name })) } });
    }
    const sending = /^\/accounts\/a+\/queues\/([^/]+)\/messages(\/batch)?$/.exec(path);
    if (sending !== null && method === "POST") {
      const given = JSON.parse(body!) as { body?: unknown; messages?: { body: unknown }[] };
      for (const each of sending[2] === undefined ? [given] : given.messages!) {
        const message = operatorMessage(each.body);
        sent.push({ queue: sending[1]!, message });
        const outcome = (options.doors ?? ((m) => ({ result: m.args })))(message);
        if (outcome !== null) {
          answers.set(answerKey(message.ask), answerOf(message, message.at, outcome));
        }
      }
      return json({ success: true, result: null });
    }
    const listingKeys = /^\/accounts\/a+\/storage\/kv\/namespaces\/([^/]+)\/keys\?prefix=(.*)$/
      .exec(path);
    if (listingKeys !== null) {
      const under = `${listingKeys[1]}/${decodeURIComponent(listingKeys[2]!)}`;
      return json({ success: true, result: [...kv.keys()].filter((key) => key.startsWith(under))
        .map((key) => ({ name: key.slice(listingKeys[1]!.length + 1) })) });
    }
    const object = `${account}/r2/buckets/${OPERATOR_NAMES.bucket}/objects/`;
    if (path.startsWith(object) && method === "GET") {
      const answer = answers.get(path.slice(object.length));
      return answer === undefined ? json({ success: false, errors: [{ code: 10007 }] }, 404)
        : json(answer);
    }
    if (path === `${account}/queues`) {
      if (method === "POST") {
        created.push(`queue ${given["queue_name"]}`);
        queues.add(given["queue_name"]!);
      }
      return json({ success: true, result: method === "POST" ? {}
        : [...queues].map((name, n) => ({ queue_id: `q${n + 1}`, queue_name: name })) });
    }
    if (path === `/zones?name=${ZONE}`) {
      return json({ success: true, result: [{ id: "z", account: { id: ACCOUNT } }] });
    }
    if (path === "/zones/z/workers/routes" && method === "GET") {
      return json({ success: true, result: [...routes.values()] });
    }
    if (path === `/accounts/${ACCOUNT}/workers/domains?hostname=${EGRESS_HOST}`) {
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
    const listed = /^\/zones\/z\/dns_records\?name\.exact=([^&]+)&per_page=100$/.exec(path);
    if (listed !== null) {
      return json({ success: true, result: dns.filter((record) => options.noisyDns === true
        || record.name === listed[1]) });
    }
    if (path === "/zones/z/dns_records" && method === "POST") {
      if (options.refusingDns === true) {
        return json({ success: false, errors: [{ code: 10000 }] }, 403);
      }
      const record = { id: `dns-made${dns.length}`, type: given["type"]!, name: given["name"]!,
                       content: given["content"]!, proxied: given["proxied"] as unknown };
      dns.push(record);
      // Killed here, the record is made and its answer lost.
      if (killed()) {
        throw new Error("the run was killed");
      }
      return json({ success: true, result: record });
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
        if (!inert.has("kv")) {
          kv.set(key, body ?? "");
        }
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
        : json({ data: held ? [{ uri: UPSTREAM_CALLBACK }] : [] });
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
      const file = argv.indexOf("--secrets-file");
      const args = argv.slice(1).map((arg, at) => at === file ? "<file>"
        : arg.endsWith(".json") ? `${basename(join(arg, ".."))}/${basename(arg)}` : arg);
      events.push(`wrangler ${args.join(" ")}`);
      if (file >= 0) {
        const path = argv[file + 1]!;
        const worker = basename(join(argv[argv.indexOf("--config") + 1]!, ".."));
        const values = JSON.parse(readFileSync(path, "utf8")) as Record<string, string>;
        secretFiles.push({ worker, path, mode: statSync(path).mode & 0o777,
                           names: Object.keys(values) });
        for (const [name, value] of Object.entries(values)) {
          secretsPut[`${worker}:${name}`] = value;
        }
      }
      if (options.failing !== undefined && args.join(" ").startsWith(options.failing)) {
        return { status: 1, stdout: "", stderr: "the API refused it" };
      }
      if (args[0] === "secret") {
        secretsPut[`${basename(join(argv[argv.length - 1]!, ".."))}:${args[2]}`] =
          options_.input ?? "";
      }
      if (args[0] === "triggers") {
        if (killed()) {
          return { status: 1, stdout: "", stderr: "the run was killed" };
        }
        // One custom domain a host: the doors' already is kept as it is.
        if (![...domains.values()].some((domain) => domain.service === DOORS)) {
          domains.set("d1", { id: "d1", hostname: EGRESS_HOST, service: DOORS });
          if (!dns.some((record) => record.id === ATTACHED.id)) {
            dns.push({ ...ATTACHED });
          }
        }
      }
      // A version made, and deployed unless it is only uploaded; or an upload deployed.
      const config = argv.indexOf("--config");
      const script = config < 0 ? undefined
        : NAMES[basename(join(argv[config + 1]!, ".."))];
      const tagged = argv.indexOf("--tag") < 0 ? null : argv[argv.indexOf("--tag") + 1]!;
      let stdout = "";
      if (script !== undefined && (args[0] === "deploy" || args[1] === "upload")) {
        const id = version(script, tagged);
        stdout = `Worker Version ID: ${id}\n`;
        if (args[0] === "deploy") {
          deployments.set(script, [{ version: id, percentage: 100 }]);
        }
      } else if (script !== undefined && args[0] === "versions" && args[1] === "deploy") {
        deployments.set(script, [{ version: args[2]!.replace(/@100$/, ""), percentage: 100 }]);
      }
      return { status: 0, stdout, stderr: "" };
    }
    if (command === process.execPath && argv.some((arg) => arg.endsWith("canary.ts"))) {
      events.push(["node", ...argv.slice(argv.findIndex((arg) => arg.endsWith("canary.ts")))
        .map((arg) => basename(arg))].join(" "));
      return { status: 0, stdout: "", stderr: "" };
    }
    if (command === process.execPath && argv.some((arg) => arg.endsWith("checks.ts"))) {
      events.push(`node ${argv.slice(argv.findIndex((arg) => arg.endsWith("checks.ts")))
        .map((arg) => basename(arg)).join(" ")} env=${Object.keys(options_.env ?? {}).sort()
        .join(",")}`);
      pairPassed = true;
      return { status: 0, stdout: "", stderr: "" };
    }
    if (String(argv[0]).endsWith(join("ci", "kit", "locks.ts")) && argv[1] === "digests") {
      events.push(`locks ${argv[2]}`);
      return options.lockRefused === undefined
        ? { status: 0, stdout: JSON.stringify((options.pinned ?? PINNED)[argv[2]!]), stderr: "" }
        : { status: 1, stdout: "", stderr: `${options.lockRefused}\n` };
    }
    if (command === "rclone") {
      const at = argv.indexOf("--files-from");
      events.push(`rclone ${argv.join(" ")}${at < 0 ? ""
        : ` (${readFileSync(argv[at + 1]!, "utf8").split("\n").filter(Boolean).join(",")})`}`);
      // The erasure marks' listings (`--include`) find none.
      const listing = argv[0] === "lsf" && !argv.includes("--include")
        ? (argv.at(-1)!.startsWith("back:") ? options.copied?.copy : options.copied?.r2) ?? []
        : [];
      return { status: 0, stdout: listing.join("\n"), stderr: "" };
    }
    events.push(`unexpected spawn ${command} ${argv.join(" ")}`);
    return { status: 1, stdout: "", stderr: "" };
  }) as unknown as Io["spawn"];
  const exec = ((_command: string, argv: readonly string[] = [],
                 options_: { env?: Record<string, string> } = {}) => {
    events.push(`exec ${basename(String(argv.at(-1)))} env has ${[...H2_INPUTS_LIST, "E2B_API_KEY"]
      .filter((name) => options_.env?.[name] !== undefined).join(",")}`);
    return "";
  }) as unknown as Io["exec"];
  const bundles = (): Bundles => {
    events.push("bundle the runner and the probes");
    return { runner: bundled(RUNNER), probes: bundled(PROBE_BUNDLE) };
  };
  const treeImage = (): string => {
    events.push("the tree's image");
    return options.image ?? TEMPLATE;
  };
  // What the kit prints joins the one timeline, by its first line.
  const log = (line: string) => {
    logs.push(line);
    events.push(`log ${line.trim().split("\n")[0]}`);
  };
  // The clock moves on only while the kit waits for an answer.
  let clock = NOW.getTime();
  const io: Io = { fetch: fetcher, spawn, exec, bundles, treeImage, commit: () => COMMIT, log,
                   now: () => new Date(clock), sleep: async (ms) => { clock += ms; } };
  return { io, events, logs, secretsPut, secretFiles, secrets, routes, hooks, accounts, kv, tables,
           kill, domains, inert, dns, created, sent, deployments, versions };
}

const H2_INPUTS_LIST = Object.values(H2_INPUTS);

/** The kit's two entries a run goes through: this tree's, or one loaded with another identity. */
type Kit = Pick<typeof import("../deploy"), "perform" | "parse">;

/**
 * `mode` run whole in `w`, with `flags` and `env`, by `kit`: its work, or its refusal. An
 * applied run is the workflow's two jobs: the plan job's run (`--public`) writes its plan to a
 * file, then the apply job's run is held to it, which first makes the plan job's steps again.
 * Each job has a log of its own, so `w` keeps only the apply job's, after the plan made again.
 */
async function runMode(w: ReturnType<typeof world>, mode: Mode, flags: string[] = [],
                       env: Record<string, string> = ENV, kit: Kit = { perform, parse }) {
  const { perform, parse } = kit;
  if (!flags.includes("--yes")) {
    return perform(parse(["--mode", mode, ...flags], env, w.io));
  }
  const folder = mkdtempSync(join(tmpdir(), "ta-plan-"));
  try {
    const plan = join(folder, "plan.json");
    const [events, logs] = [w.events.length, w.logs.length];
    await perform(parse(["--mode", mode, "--public", "--plan", plan,
                         ...flags.filter((flag) => flag !== "--yes")], env, w.io));
    const job = w.events.splice(events);
    w.logs.splice(logs);
    const reported = job.findIndex((event) => event.startsWith("log Run "));
    const planned = job.slice(0, reported < 0 ? undefined : reported);
    const work = await perform(parse(["--mode", mode, ...flags, "--plan", plan], env, w.io));
    expect(w.events.slice(events, events + planned.length)).toEqual(planned);
    w.events.splice(events, planned.length);
    w.logs.splice(logs, planned.filter((event) => event.startsWith("log ")).length);
    return work;
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
}

/** The plan as the kit reports it, under the run's id: "changed" or "would change", a line a step. */
function summary(heading: string, plan: readonly string[]): string {
  return `Run ${runOf(ENV, NOW).id} ${heading}:\n${plan.map((line) => `- ${line}`).join("\n")}`;
}

/** The doors' upstream as a plan says it: the issuer, and the client and JWKS it deploys. */
const ISSUED = "set the doors' OWNER_ISSUER to https://auth.test, held to its discovery, and " +
  "their UPSTREAM to the WorkOS client client_test and the JWKS https://auth.test/jwks";

/** The apply job's first line: the plan job's plan holds on the live state. */
const HELD = "the plan job's plan, made again from the live state, is the one approved, and " +
  "each line below is held to its line there as it is said";

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

  it("names each Worker by its config's name, set in every config and shared by none", () => {
    const owner = configOf("owner");
    expect(workerNames(new Map([["owner", owner]]))).toEqual({ owner: NAMES["owner"] });
    expect(() => workerNames(new Map([["owner", owner], ["files", owner]])))
      .toThrow("platform/files's config names no Worker of its own");
    expect(() => workerNames(new Map([["owner", { ...owner, name: undefined }]])))
      .toThrow("platform/owner's config names no Worker of its own");
  });

  it("retires only a Worker no config names", () => {
    expect(retiredWorkers(["tinyassets-old"], NAMES)).toEqual(["tinyassets-old"]);
    expect(() => retiredWorkers(["tinyassets-old", NAMES["owner"]!], NAMES))
      .toThrow(`platform.json retires ${NAMES["owner"]}, which a config still names`);
  });

  it("counts each Worker's forward changes, each naming Workers of this tree once, with why",
     () => {
    expect(FORWARD).toEqual(forwardCounts(platform.forward.changes, NAMES));
    expect(Object.keys(FORWARD).sort()).toEqual([...WORKERS].sort());
    const why = "its app's page asks /app/api in a shape the version before cannot read";
    expect(forwardCounts([{ workers: ["doors"], why }, { workers: ["doors", "owner"], why }],
                         NAMES)).toEqual({ ...Object.fromEntries(WORKERS.map((worker) =>
      [worker, 0])), doors: 2, owner: 1 });
    for (const bad of [{ workers: [], why }, { workers: ["doors", "doors"], why },
                       { workers: ["tinyassets-doors"], why }, { workers: ["doors"] },
                       { workers: ["doors"], why: " " }, null]) {
      expect(() => forwardCounts([{ workers: ["owner"], why }, bad], NAMES), JSON.stringify(bad))
        .toThrow("platform.json's forward change 2 names no Worker of this tree, each once, " +
                 "with why");
    }
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
      const deployed = deployedConfig(worker, IDS, UPSTREAM);
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
    const routed = routedConfig("doors", IDS, UPSTREAM)["routes"];
    expect(routed).toEqual([...ROUTES.map((pattern) => ({ pattern, zone_name: ZONE })),
                            { pattern: EGRESS_HOST, custom_domain: true }]);
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
        writeFileSync(config, JSON.stringify(routedConfig(worker, IDS, UPSTREAM)));
        execFileSync(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir",
                                        join(out, worker), "--config", config],
                     { cwd: PLATFORM, stdio: "pipe",
                       env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
      }
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("writes the dark record (`control.json`) when none is held, every outside door closed",
     async () => {
    const w = world();
    await runMode(w, "dark", ["--yes"]);
    const written = [...w.kv].filter(([key]) => key.endsWith(`/${CONTROL_KEY}`))
      .map(([, text]) => JSON.parse(text) as unknown);
    expect(written).toEqual([DARK]);
    const control = parseControl(written[0]);
    expect(control).toMatchObject({ outside: "off", ordinary: "canary", canary: [],
                                    inbound: false });
    expect(control?.tiers).not.toBeNull();
  });

  it("prints the import job's names for its bundle, each storage this kit makes, and the doors",
     () => {
    const run = (...flags: string[]) => spawnSync(process.execPath, [
      "--experimental-strip-types", "--no-warnings", join(PLATFORM, "deploy", "facts.ts"),
      ...flags], { cwd: PLATFORM, encoding: "utf8" });
    const printed = run("--import");
    expect([printed.status, printed.stderr]).toEqual([0, ""]);
    const names = JSON.parse(printed.stdout) as Record<string, string>;
    expect(names).toEqual(IMPORT_NAMES);
    const { dead_letter_queue: deadLetters, directory, doors, ...buckets } = names;
    expect(doors).toBe(DOORS);
    expect(QUEUES).toContain(deadLetters);
    expect(Object.values(NAMESPACES)).toContain(directory);
    expect(Object.values(buckets).filter((name) => !BUCKETS.includes(name))).toEqual([]);
    expect(new Set(Object.values(names)).size).toBe(Object.keys(names).length);
    expect(run().status).not.toBe(0);
  });
});

describe("the command line", () => {
  const w = world();

  it("takes one mode, by --mode, and refuses a run with none or another", () => {
    expect(Object.keys(MODES)).toEqual(["plan", "dark", "routes", "h2", "pair",
                                        "handback", "rollback", "control", "owner", "restore", "copyback",
                                        "recover", "erase",
                                        "rotation", "retire"]);
    for (const mode of Object.keys(MODES) as Mode[]) {
      expect(parse(["--mode", mode, ...needed(mode)], ENV, w.io).mode).toBe(mode);
    }
    for (const argv of [[], ["--mode"], ["--mode", "apply"], ["--mode", "constructor"],
                        ["--yes", "--routes"], ["--mode", "--yes"]]) {
      expect(() => parse(argv, ENV, w.io), argv.join(" ")).toThrow(
        "--mode is one of plan, dark, routes, h2, pair, handback, rollback, control, owner");
    }
    for (const argv of [["--mode", "dark", "--mode", "routes"], ["--mode", "dark", "--mode", "dark"]]) {
      expect(() => parse(argv, ENV, w.io), argv.join(" ")).toThrow(
        "--mode is given once: a run is one mode");
    }
  });

  it("holds only the planning modes to their options: h2 and pair read none", () => {
    for (const mode of ["h2", "pair"]) {
      const run = parse(["--mode", mode, "--public", "--yes", "--plan", "plan.json",
                         "--rotate", "FILES_KEYRING", "--migrate", "doors"], ENV, w.io);
      expect([run.mode, run.reach, run.apply]).toEqual([mode, "public", true]);
    }
  });

  it("refuses a run that would change what its job cannot", () => {
    expect(() => parse(["--mode", "dark", "--public", "--yes"], ENV, w.io))
      .toThrow("--public is the plan job's: it never changes anything");
    expect(() => parse(["--mode", "plan", "--yes"], ENV, w.io))
      .toThrow("mode plan changes nothing: it takes no --yes");
    // --migrate names only a Worker whose config declares Durable Object migrations.
    expect(MIGRATES).toEqual(WORKERS.filter((worker) =>
      ((configOf(worker)["migrations"] ?? []) as unknown[]).length > 0));
    for (const worker of [...WORKERS.filter((each) => !MIGRATES.includes(each)), "nowhere"]) {
      expect(() => parse(["--mode", "dark", "--migrate", `owner,${worker}`], ENV, w.io), worker)
        .toThrow(`${worker} holds no Durable Object migration: --migrate names only ` +
                 MIGRATES.join(", "));
    }
    expect([...parse(["--mode", "dark", "--migrate", MIGRATES.join(",")], ENV, w.io).migrate])
      .toEqual(MIGRATES);
    const run = parse(["--mode", "routes", "--yes", "--plan", "plan.json", "--rotate",
                       "ROUTING_MAC_KEY,FILES_KEYRING", "--migrate", "owner"], ENV, w.io);
    expect([run.reach, run.apply, [...run.rotate], [...run.migrate]])
      .toEqual(["secrets", true, ["ROUTING_MAC_KEY", "FILES_KEYRING"], ["owner"]]);
    expect(parse(["--mode", "plan", "--public"], ENV, w.io).reach).toBe("public");
  });
});

describe("the plan the apply job runs", () => {
  const RUN = { ...ENV, GITHUB_RUN_ID: "7001", GITHUB_RUN_ATTEMPT: "1" };

  /** The plan job's run of `mode` in `w`: the file it writes, in a folder of its own. */
  async function planned(w: ReturnType<typeof world>, mode: Mode, flags: string[] = [],
                         env: Record<string, string> = RUN): Promise<string> {
    const plan = join(mkdtempSync(join(tmpdir(), "ta-plan-")), "plan.json");
    await perform(parse(["--mode", mode, "--public", "--plan", plan, ...flags], env, w.io));
    return plan;
  }
  /** The apply job's run of `mode` in `w`, held to `plan`: its refusal, and what it changed. */
  async function refused(w: ReturnType<typeof world>, mode: Mode, plan: string,
                         flags: string[] = [], env: Record<string, string> = RUN) {
    const at = w.events.length;
    const why = await perform(parse(["--mode", mode, "--yes", "--plan", plan, ...flags], env,
                                    w.io)).then(() => "applied", (error: Error) => error.message);
    rmSync(join(plan, ".."), { recursive: true, force: true });
    return { why, changed: w.events.slice(at).filter((event) => CHANGE.test(event)) };
  }

  it("applies only with the plan its run's plan job wrote", () => {
    const w = world();
    expect(() => parse(["--mode", "dark", "--yes"], ENV, w.io)).toThrow(
      "--yes applies the plan it was approved on: --plan names the file its run's plan job " +
      "wrote");
    expect(() => parse(["--mode", "dark", "--plan", "plan.json"], ENV, w.io)).toThrow(
      "--plan is the plan job's file (--public writes it) and the apply job's (--yes is held " +
      "to it)");
  });

  it("writes what was asked and each line, and no run's id or time, which the heading names",
     async () => {
    const w = world({ held: true });
    const plans = [];
    for (const id of ["7001", "7002"]) {
      const plan = await planned(w, "routes", ["--rotate", "ROUTING_MAC_KEY"],
                                 { ...RUN, GITHUB_RUN_ID: id });
      plans.push(JSON.parse(readFileSync(plan, "utf8")) as Json);
      rmSync(join(plan, ".."), { recursive: true, force: true });
    }
    expect(plans.map((each) => each["run"])).toEqual(["7001", "7002"]);
    expect(plans[0]!["plan"]).toEqual(plans[1]!["plan"]);
    expect(plans[0]!["plan"]).toMatchObject({
      asked: { mode: "routes", rotate: ["ROUTING_MAC_KEY"], migrate: [], record: null } });
    expect(JSON.stringify(plans[0]!["plan"])).not.toMatch(/7001|2026-11-07/);
    expect(w.logs.filter((line) => line.startsWith("Run ")).map((line) => line.split(":")[0]))
      .toEqual(["Run 7001 would change", "Run 7002 would change"]);
  });

  it("stops before any change when the live state plans something else", async () => {
    const w = world({ held: true });
    const plan = await planned(w, "routes");
    // Between the jobs, the site's route goes.
    w.routes.delete("r6");
    const { why, changed } = await refused(w, "routes", plan);
    expect(why).toMatch(/^the live state no longer plans what was approved, so nothing is changed: /);
    expect(why).toContain("approved, and not planned now: record the zone's 6 routes");
    expect(why).toContain("planned now, and never approved: record the zone's 5 routes");
    expect(changed).toEqual([]);
  });

  it("stops before any change on a plan someone else wrote, or no plan", async () => {
    const forged = world({ held: true });
    const plan = await planned(forged, "dark");
    const file = JSON.parse(readFileSync(plan, "utf8")) as { plan: { lines: string[] } };
    file.plan.lines = file.plan.lines.filter((line) =>
      !line.startsWith(`upload and deploy a version of ${DOORS}`));
    writeFileSync(plan, JSON.stringify(file));
    expect(await refused(forged, "dark", plan)).toEqual({ changed: [], why: expect.stringContaining(
      `planned now, and never approved: upload and deploy a version of ${DOORS}`) });
    const shapeless = world({ held: true });
    const empty = await planned(shapeless, "dark");
    writeFileSync(empty, "{}");
    expect(await refused(shapeless, "dark", empty)).toEqual({ changed: [],
      why: `${empty} is not a plan the plan job wrote; nothing is changed` });
    const none = world({ held: true });
    expect(await refused(none, "dark", join(tmpdir(), "ta-no-plan", "plan.json"))).toEqual({
      changed: [], why: `there is no plan at ${join(tmpdir(), "ta-no-plan", "plan.json")}: the ` +
        "apply job applies only the plan its run's plan job wrote (--public --plan); nothing is " +
        "changed" });
  });

  it("applies, line by line, the plan job's plan, each line left to it with its answer",
     async () => {
    const LATER = ": checked by the apply job before any change";
    const lacking = ["INBOUND_SLACK_SIGNING_SECRET", "PLATFORM_CANARY_TOKEN"];
    const without = Object.fromEntries(Object.entries(RUN).filter(([name]) =>
      !lacking.includes(name)));
    for (const [mode, options, env] of [
      ["dark", {}, RUN], ["dark", { held: true }, RUN], ["dark", { held: true, lacking }, RUN],
      ["dark", { held: true, lacking }, without], ["routes", { held: true }, RUN],
      ["routes", { held: true, lacking }, without]] as const) {
      const w = world(options);
      const plan = await planned(w, mode, [], env);
      const approved = (JSON.parse(readFileSync(plan, "utf8")) as { plan: { lines: string[] } })
        .plan.lines;
      const work = await perform(parse(["--mode", mode, "--yes", "--plan", plan], env, w.io));
      rmSync(join(plan, ".."), { recursive: true, force: true });
      const applied = work.plan.slice(1);
      // The custom domain's DNS record, which routes writes into its record once made.
      const did = applied.filter((line) => line.startsWith("record in deploy:routes:"));
      const said = applied.filter((line) => !did.includes(line));
      expect([mode, options, said.length]).toEqual([mode, options, approved.length]);
      said.forEach((line, at) => {
        const shown = approved[at]!;
        expect([mode, at, shown.endsWith(LATER) ? line.startsWith(shown.slice(0, -LATER.length))
          && !line.endsWith(LATER) : line === shown]).toEqual([mode, at, true]);
      });
    }
  });

  it("applies a first deploy whose WorkOS client lacks the callback: the plan job leaves the " +
     "early sign-in check to it", async () => {
    // WorkOS refuses the callback until the apply job registers it.
    const w = world();
    const fetch = w.io.fetch;
    let registered = false;
    Object.assign(w.io, { fetch: async (url: string, init?: RequestInit) => {
      const at = new URL(url).pathname;
      registered ||= at === "/user_management/redirect_uris" && init?.method === "POST";
      return at === "/user_management/authorize" && !registered
        ? new Response(null, { status: 302, headers: {
          Location: "https://error.workos.com/redirect-uri-invalid?redirect_uri=x" } })
        : fetch(url, init);
    } });
    const plan = await planned(w, "dark");
    const subject = `WorkOS's authorize of the doors' client and ${UPSTREAM_CALLBACK}`;
    expect((JSON.parse(readFileSync(plan, "utf8")) as { plan: { lines: string[] } }).plan.lines)
      .toContain(`${subject}: checked by the apply job before any change`);
    const work = await perform(parse(["--mode", "dark", "--yes", "--plan", plan], RUN, w.io));
    rmSync(join(plan, ".."), { recursive: true, force: true });
    expect(work.plan).toContain(`${subject}: it takes them, sending the browser to auth.test's ` +
                                "sign-in");
  });

  it("holds each line to the approved one, a line left to it by its subject, and ends with it",
     () => {
    const LATER = "checked by the apply job before any change";
    const approved = ["a", `the subject: ${LATER}`, "b"];
    const applied = (lines: readonly string[]) => {
      const hold = holding(approved);
      lines.forEach(hold.next);
      hold.done();
    };
    // Its answer in place of the words, and once every line is said, what the run did after.
    expect(() => applied(["a", "the subject: present", "b", "an answer"])).not.toThrow();
    const second = "line 2 is not the approved plan's";
    for (const [lines, why] of [
      [["a", "another subject: present", "b"], second],
      [["a", `the subject: ${LATER}`, "b"], second],
      [["a", "the subject: ", "b"], second],
      [["b"], `line 1 is not the approved plan's, so it stops before that change: approved "a"`],
      [["a", "the subject: present"], `ended before the approved plan's line 3 ("b")`],
    ] as const) {
      expect(() => applied(lines), lines.join("|")).toThrow(why);
    }
  });

  it("stops at the first line its own steps say that the plan job did not, before its change",
     async () => {
    // Between the plan made again and this job's own steps, a held secret goes from the doors.
    const w = world({ held: true });
    const plan = await planned(w, "dark");
    const listing = `/workers/scripts/${DOORS}/secrets`;
    const fetch = w.io.fetch;
    let listed = 0;
    Object.assign(w.io, { fetch: async (url: string, init?: RequestInit) => {
      if (url.endsWith(listing) && ++listed === 2) {
        w.secrets.get(DOORS)!.splice(w.secrets.get(DOORS)!.indexOf("WORKOS_API_KEY"), 1);
      }
      return fetch(url, init);
    } });
    const { why, changed } = await refused(w, "dark", plan);
    expect(why).toMatch(new RegExp(`^this job's line \\d+ is not the approved plan's, so it ` +
      "stops before that change, having made no change but what the approved lines before it " +
      "name: approved \"record " +
      "each Worker's version as this run's rollout record"));
    expect(why).toContain(`and this job's "set the secret WORKOS_API_KEY on ${DOORS} with its ` +
                          'version"');
    expect(changed).toEqual([]);
  });

  it("stops before any change on another run's plan", async () => {
    const w = world({ held: true });
    const plan = await planned(w, "dark");
    expect(await refused(w, "dark", plan, [], { ...RUN, GITHUB_RUN_ID: "7002" })).toEqual({
      changed: [], why: `the plan at ${plan} is run 7001's, not this run's (7002): the apply ` +
        "job applies only its own run's plan; nothing is changed" });
  });

  it("stops before any change on a dispatch or a variable the plan did not have", async () => {
    const asked = world({ held: true });
    const plan = await planned(asked, "dark", ["--rotate", "ROUTING_MAC_KEY"]);
    const { why, changed } = await refused(asked, "dark", plan);
    expect(why).toContain('the dispatch\'s rotate was ["ROUTING_MAC_KEY"], and is []');
    expect(changed).toEqual([]);
    // The repository's variable, edited between the jobs: the doors would deploy another client.
    const varied = world({ held: true });
    const client = await planned(varied, "dark");
    const other = await refused(varied, "dark", client, [], { ...RUN, WORKOS_CLIENT_ID: "client_b" });
    expect(other.why).toContain("planned now, and never approved: set the doors' OWNER_ISSUER " +
      "to https://auth.test, held to its discovery, and their UPSTREAM to the WorkOS client " +
      "client_b");
    expect(other.changed).toEqual([]);
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
      "locks owner", "locks sandbox", "cf GET /zones?name=tinyassets.io", "issuer discovery",
      "the tree's image", "e2b GET /templates/t1/tags/exists", "bundle the runner and the probes",
      "e2b GET /templates/t1/tags/exists",
      "e2b GET /events/webhooks", "workos GET redirect_uris", "workos POST redirect_uris",
      "workos authorize", "cf GET /accounts", `cf POST /accounts/${ACCOUNT}/storage/kv/namespaces`,
      `cf POST /accounts/${ACCOUNT}/r2/buckets`, `cf POST /accounts/${ACCOUNT}/queues`,
      `cf POST /accounts/${ACCOUNT}/queues`,
      "wrangler deploy --config files/", "wrangler deploy --config doors/", "cf PUT /accounts",
      "e2b POST /events/webhooks", "log Run ", "node canary.ts"]);
    const first = w.events.findIndex((event) =>
      /^(cf POST|wrangler secret|cf PUT)/.test(event));
    expect(at[11]).toBeLessThan(first);
    // The webhook is registered disabled, with the doors' secret, after the doors hold it.
    expect(w.events[at[20]!]).toContain('"enabled":false');
    expect(w.events[at[20]!]).toContain('"signatureSecret":"hook-secret"');
    // The plan is reported whole, every step's line in it, before the canary.
    expect(work.plan).toEqual([HELD, ...DARK_FRESH]);
    expect(w.logs).toEqual([summary("changed", [HELD, ...DARK_FRESH])]);
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

  it("stops before anything without an input its row reads, naming where it comes from",
     async () => {
    const w = world();
    const full: Record<string, string> = { ...ENV, [H2_INPUTS.a]: "probe-key",
                                           [H2_INPUTS.b]: "probe-key-b" };
    for (const mode of Object.keys(MODES) as Mode[]) {
      const { inputs } = MODES[mode];
      const record = needed(mode);
      for (const [names, flags, where] of [
        [inputs.public, ["--public"], "the workflow's inputs"],
        [inputs.secret, [], "the production environment's secrets"]] as const) {
        for (const name of names) {
          const { [name]: _dropped, ...without } = full;
          await expect(runMode(w, mode, [...flags, ...record], without), `${mode} ${name}`)
            .rejects.toThrow(`${name} is not set (${where})`);
        }
      }
    }
    expect(w.events).toEqual([]);
    // A deploy reads every secret a config requires, and the plan job's inputs alone.
    expect(MODES.dark.inputs).toEqual({ public: PUBLIC_INPUTS, secret: SECRET_INPUTS,
                                        optional: OPTIONAL });
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
    const control = w.events.findIndex(writesControl);
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
    expect(w.events.some(writesControl)).toBe(RETIRED.length === 0);
  });

  it("plans the same steps without --yes, and changes nothing", async () => {
    const w = world();
    const work = await runMode(w, "plan");
    expect(w.events.filter((event) => /^(wrangler [a-z ]*(create|put|deploy|upload)|cf (PUT|POST|DELETE)|e2b (POST|PATCH)|workos POST|node canary)/
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
                `the pair ${PAIR}'s standing checks in this E2B team: checked by the apply job ` +
                "before any change",
                `WorkOS redirect URI ${UPSTREAM_CALLBACK}: checked by the apply job before any change`,
                `E2B lifecycle webhook ${E2B_HOOK.url}: checked by the apply job before any change`]);
    // With none held, the control record is planned, and the hook's word comes after it, last.
    const fresh = await runMode(world(), "dark", ["--public"], PUBLIC);
    const [last2, last] = fresh.plan.slice(-2);
    expect([last2?.startsWith("write the control record closed"), last]).toEqual([
      true, `E2B lifecycle webhook ${E2B_HOOK.url}: checked by the apply job before any change`]);
  });

  it("deploys a held Worker as a version, and a named migration whole, each tagged with the " +
     "commit and its locks' counts", async () => {
    // The sandbox's locks listed out of their paths' order: the tag counts in that order.
    const w = world({ held: true, pinned: {
      owner: { "owner/migrations.json": ["a", "b", "c"] },
      sandbox: { "sandbox/migrations/SandboxSlots.json": ["d"],
                 "sandbox/migrations/SandboxControl.json": ["e", "f"] } } });
    await runMode(w, "dark", ["--yes", "--migrate", "owner"]);
    const tagged = (tag: string) => `--tag ${tag} --message ${COMMIT}`;
    expect(w.events.filter((event) => event.startsWith("wrangler deploy")
      || event.startsWith("wrangler versions upload"))).toEqual([
      ...["codemode", "files", "gateway"].map((worker) =>
        `wrangler versions upload --config ${worker}/wrangler.json ${tagged("c0ffee111111")}`),
      `wrangler versions upload --config sandbox/wrangler.json ${tagged("c0ffee111111-m2.1")}`,
      `wrangler deploy --config owner/wrangler.json ${tagged("c0ffee111111-m3")}`,
      `wrangler versions upload --config doors/wrangler.json ${tagged("c0ffee111111")}`]);
    // Nothing held is made again, and the control record is left alone: the one write is the
    // run's rollout record.
    expect(w.events.filter((event) => /create|cf PUT|secret put/.test(event))).toEqual([
      `${CONTROL_WRITE}namespaces/${CONTROL_NS}/values/${encodeURIComponent(
        "deploy:rollout:local-20261107T120000Z")}`]);
  });

  it("sets each secret only with the version it deploys, from a file of its names alone, " +
     "mode 0600, removed after the run", async () => {
    // A fresh account: every Worker's secrets, each with its first deploy.
    const w = world();
    await runMode(w, "dark", ["--yes"]);
    expect(w.events.filter((event) => /^wrangler (versions )?secret/.test(event))).toEqual([]);
    expect(w.secretFiles.map(({ worker, names }) => [worker, names])).toEqual(
      WORKERS.filter((worker) => worker in SECRETS).map((worker) => [worker, [...SECRETS[worker]!]]));
    expect(w.events.filter((event) => event.includes("--secrets-file")).map((event) =>
      event.split(" ")[3])).toEqual(w.secretFiles.map(({ worker }) => `${worker}/wrangler.json`));
    for (const { path, mode } of w.secretFiles) {
      expect([path, mode.toString(8), existsSync(path)]).toEqual([path, "600", false]);
    }
    // A held account rotating the routing key: the doors' and the owner's versions carry it,
    // alone, and no other Worker's carries a file.
    const rotated = world({ held: true });
    await runMode(rotated, "dark", ["--yes", "--rotate", "ROUTING_MAC_KEY"]);
    expect(rotated.secretFiles.map(({ worker, names }) => [worker, names])).toEqual([
      ["owner", ["ROUTING_MAC_KEY"]], ["doors", ["ROUTING_MAC_KEY"]]]);
    expect(rotated.secretsPut).toEqual({ "owner:ROUTING_MAC_KEY": ENV["ROUTING_MAC_KEY"],
                                         "doors:ROUTING_MAC_KEY": ENV["ROUTING_MAC_KEY"] });
    // An upload that fails stops the run, its file removed all the same.
    const failed = world({ held: true, failing: "versions upload --config owner/" });
    await expect(runMode(failed, "dark", ["--yes", "--rotate", "ROUTING_MAC_KEY"]))
      .rejects.toThrow("wrangler versions upload --config failed (exit 1)");
    expect(failed.secretFiles.map(({ worker, path }) => [worker, existsSync(path)]))
      .toEqual([["owner", false]]);
  });

  it("stops before any change when a migration lock does not pin what the code ships",
     async () => {
    const w = world({ lockRefused: "the migration lock owner/migrations.json pins 1" });
    await expect(runMode(w, "dark", ["--yes"])).rejects.toThrow(
      "owner's migration locks refuse the deploy (exit 1): the migration lock " +
      "owner/migrations.json pins 1");
    expect(w.events).toEqual(["locks owner"]);
  });

  it.each([
    ["the import window's", QUEUE_NAMES.IMPORTS, IMPORT_NAMES.dead_letter_queue,
     IMPORT_NAMES.staging_bucket],
    ["the operator channel's", QUEUE_NAMES.OPERATOR, OPERATOR_NAMES.dead_letter_queue,
     OPERATOR_NAMES.bucket],
  ])("makes %s queue, dead-letter queue and bucket, each only when its listing lacks it, in a " +
     "fresh, a held and a partial account", async (_what, queue, deadLetters, bucket) => {
    const MADE_HERE = [`R2 bucket ${bucket}`, `queue ${queue}`, `queue ${deadLetters}`];
    const made = (w: ReturnType<typeof world>) => w.created
      .filter((each) => each.startsWith("R2 bucket") || each.startsWith("queue"));
    const its = (w: ReturnType<typeof world>) => made(w)
      .filter((each) => MADE_HERE.includes(each));
    expect(BUCKETS).toEqual([FILES_BUCKET, IMPORT_NAMES.staging_bucket, OPERATOR_NAMES.bucket,
                             IMPORT_NAMES.final_backup_bucket]);
    expect(QUEUES).toEqual([QUEUE_NAMES.RING, QUEUE_NAMES.ERASURES, QUEUE_NAMES.IMPORTS,
                            QUEUE_NAMES.OPERATOR, IMPORT_NAMES.dead_letter_queue,
                            OPERATOR_NAMES.dead_letter_queue]);
    const fresh = world();
    await runMode(fresh, "dark", ["--yes"]);
    expect(its(fresh)).toEqual(MADE_HERE);
    const held = world({ held: true });
    await runMode(held, "dark", ["--yes"]);
    expect(made(held)).toEqual([]);
    // The queue made but not its dead-letter queue nor the bucket: only those two are made.
    const partial = world({ held: true, listed: {
      buckets: BUCKETS.filter((each) => each !== bucket),
      queues: QUEUES.filter((each) => each !== deadLetters) } });
    const work = await runMode(partial, "dark", ["--yes"]);
    expect(made(partial)).toEqual([`R2 bucket ${bucket}`, `queue ${deadLetters}`]);
    expect(work.plan).toEqual(expect.arrayContaining([
      `create the R2 bucket ${bucket}`, `create the queue ${deadLetters}`]));
    expect(work.plan).not.toContain(`create the queue ${queue}`);
  });

  it("locks every object of the operator bucket for a year, setting the rule only when the " +
     "bucket holds no rule or another, and plans it without --yes", async () => {
    const RULE = { id: "kept-365-days", enabled: true, prefix: "",
                   condition: { type: "Age", maxAgeSeconds: 365 * 86_400 } };
    const LINE = `lock every object of the R2 bucket ${OPERATOR_NAMES.bucket} for 365 days`;
    const locked = (w: ReturnType<typeof world>) => w.created
      .filter((each) => each.startsWith("R2 lock"));
    expect(BUCKET_LOCKS).toEqual({ [OPERATOR_NAMES.bucket]: 365 });
    // A fresh account: the bucket made, then its rule set.
    const fresh = world();
    const made = await runMode(fresh, "dark", ["--yes"]);
    expect(made.plan).toContain(LINE);
    expect(locked(fresh))
      .toEqual([`R2 lock ${OPERATOR_NAMES.bucket} ${JSON.stringify({ rules: [RULE] })}`]);
    expect(fresh.created.indexOf(`R2 bucket ${OPERATOR_NAMES.bucket}`))
      .toBeLessThan(fresh.created.indexOf(locked(fresh)[0]!));
    // Held as the kit sets it: nothing planned, nothing set.
    const held = world({ held: true });
    expect((await runMode(held, "dark", ["--yes"])).plan.filter((line) => line.startsWith("lock")))
      .toEqual([]);
    expect(locked(held)).toEqual([]);
    // A shorter lock, a rule on a prefix alone, a disabled rule, or a second rule: replaced.
    for (const rules of [[{ ...RULE, condition: { type: "Age", maxAgeSeconds: 86_400 } }],
                         [{ ...RULE, prefix: "answers/" }], [{ ...RULE, enabled: false }],
                         [RULE, { ...RULE, id: "other" }]]) {
      const other = world({ held: true, locks: { [OPERATOR_NAMES.bucket]: rules } });
      const work = await runMode(other, "dark", ["--yes"]);
      expect(work.plan).toContain(`${LINE}, in place of its ${rules.length} lock rules`);
      expect(locked(other)).toHaveLength(1);
    }
    // The plan job: the bucket it would make planned locked, nothing set.
    const plan = world();
    expect((await runMode(plan, "dark", [])).plan).toContain(LINE);
    expect(locked(plan)).toEqual([]);
  });

  it("makes the off-site copy's bucket, which no Worker binds, only when its listing lacks it",
     async () => {
    const made = (w: ReturnType<typeof world>) => w.created
      .filter((each) => each.startsWith("R2 bucket"));
    expect([...CONFIGS.values()].flatMap((config) =>
      ((config["r2_buckets"] ?? []) as { bucket_name: string }[]).map((each) => each.bucket_name)))
      .not.toContain(IMPORT_NAMES.final_backup_bucket);
    const fresh = world();
    await runMode(fresh, "dark", ["--yes"]);
    expect(made(fresh)).toContain(`R2 bucket ${IMPORT_NAMES.final_backup_bucket}`);
    const held = world({ held: true });
    await runMode(held, "dark", ["--yes"]);
    expect(made(held)).toEqual([]);
    const lacking = world({ held: true, listed: {
      buckets: BUCKETS.filter((each) => each !== IMPORT_NAMES.final_backup_bucket), queues: QUEUES } });
    const work = await runMode(lacking, "dark", ["--yes"]);
    expect(made(lacking)).toEqual([`R2 bucket ${IMPORT_NAMES.final_backup_bucket}`]);
    expect(work.plan).toContain(`create the R2 bucket ${IMPORT_NAMES.final_backup_bucket}`);
    // The plan job lists it and makes nothing.
    const plan = world({ held: true, listed: {
      buckets: BUCKETS.filter((each) => each !== IMPORT_NAMES.final_backup_bucket), queues: QUEUES } });
    const planned = await runMode(plan, "dark", []);
    expect(made(plan)).toEqual([]);
    expect(planned.plan).toContain(`create the R2 bucket ${IMPORT_NAMES.final_backup_bucket}`);
  });

  it("stops on a failed bucket or queue listing, never reading it as absent", async () => {
    for (const [listing, what] of [["r2/buckets", "R2 buckets"], ["queues", "queues"]] as const) {
      const w = world({ refusingListing: listing });
      await expect(runMode(w, "dark", ["--yes"])).rejects.toThrow(
        `listing the ${what} failed (500, [10000])`);
      expect(w.created.filter((each) => each.startsWith(listing === "queues" ? "queue"
        : "R2 bucket"))).toEqual([]);
    }
  });

  it("reads whether a Worker exists from the account's Workers listing, never its secrets",
     async () => {
    // A fresh account: every Worker deployed whole, and no secret listing read.
    const fresh = world();
    await runMode(fresh, "dark", ["--yes"]);
    expect(fresh.events.filter((event) => event.endsWith("/secrets"))).toEqual([]);
    expect(fresh.events.filter((event) => event.startsWith("wrangler deploy")).length)
      .toBe(WORKERS.length);
    // A held one: each listed Worker's secrets read, each deployed as a version.
    const held = world({ held: true });
    await runMode(held, "dark", ["--yes"]);
    expect(held.events.filter((event) => event.endsWith("/secrets"))).toEqual(
      Object.keys(SECRETS).map((worker) =>
        `cf GET /accounts/${ACCOUNT}/workers/scripts/${NAMES[worker]}/secrets`));
    expect(held.events.filter((event) => event.startsWith("wrangler deploy"))).toEqual([]);
    // A listing that fails stops the run before any secret or Worker.
    const refused = world({ held: true, refusingListing: "workers/scripts" });
    await expect(runMode(refused, "dark", ["--yes"])).rejects.toThrow(
      "the Workers listing failed (500, [10000])");
    expect(refused.events.some((event) => /^wrangler (secret|deploy|versions)/.test(event)))
      .toBe(false);
  });

  it("stops when the pair has not passed, and plans only the pair job's check without --yes",
     async () => {
    await expect(runMode(world({ paired: false }), "dark", ["--yes"])).rejects.toThrow(
      `the pair ${PAIR} has not passed its standing checks in this E2B team: run the pair job ` +
      "(--mode pair) first; nothing is deployed");
    const work = await runMode(world({ paired: false }), "plan");
    expect(work.plan).toContain(`the pair ${PAIR}'s standing checks in this E2B team: not ` +
                                "passed yet: the pair job runs its check (--mode pair) before " +
                                "the apply job");
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
                     "e2b PATCH /events/webhooks/w1 {\"enabled\":true}", "log Run ",
                     "node canary.ts --side doors --unkeyed"]);
    // The routes are read once, before any change; the doors' last version is deployed before
    // a route moves; the hook is enabled only after the last route and the custom domain.
    expect(w.events.filter((event) => event === "cf GET /zones/z/workers/routes")).toHaveLength(1);
    expect(w.events.findLastIndex((event) => event.startsWith("wrangler versions deploy")))
      .toBeLessThan(w.events.indexOf("cf PUT /zones/z/workers/routes/r1"));
    expect(work.plan).toEqual([HELD, ...ROUTES_HELD]);
    expect(w.logs).toContain(summary("changed", [HELD, ...ROUTES_HELD]));
    // Every wrangler command, the routes' triggers deploy too, is handed the zone's account.
    expect(w.accounts.length).toBeGreaterThan(0);
    expect(new Set(w.accounts)).toEqual(new Set([ACCOUNT]));
    for (const path of [`${HOST}/mcp`, `${HOST}/api/x`, `${HOST}/oauth/token`,
                        `${HOST}/.well-known/oauth-protected-resource/mcp`,
                        `${HOST}${identity.hook}`]) {
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

  it("in the plan job says the hook last, after the routes, where the apply job does", async () => {
    const work = await runMode(world({ held: true }), "routes", ["--public"], PUBLIC);
    expect(work.plan.at(-1)).toBe(`E2B lifecycle webhook ${E2B_HOOK.url}, after the routes: ${
      "checked by the apply job before any change"}`);
  });
});

/** A workflow run's own environment: its run id and attempt, which a route record keys on. */
const RUN = { ...ENV, GITHUB_RUN_ID: "7001", GITHUB_RUN_ATTEMPT: "1" };
const KEY = "deploy:routes:7001";
const CONTROL_NS = `ns-${NAMESPACES["CONTROL"]}`;
const RECORD_AT = `/accounts/${ACCOUNT}/storage/kv/namespaces/${CONTROL_NS}/values/${
  encodeURIComponent(KEY)}`;
/** What changes the account, WorkOS, E2B or the zone: every write the kit can make. */
const CHANGE = /^(wrangler (secret put|deploy|versions upload|versions deploy|triggers deploy)|cf (PUT|POST|DELETE) |e2b (POST|PATCH)|workos POST)/;
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
        domain: { hostname: EGRESS_HOST, service: null, created: [{ id: "dns-egress", type: "AAAA", content: "100::" }] },
        // The zone holds the site's own record: routes makes none, and a handback deletes none.
        host: { name: HOST, held: true, made: null },
        hook: { url: E2B_HOOK.url, present: true, enabled: false } });
      // It holds no secret, and not the account.
      const text = w.kv.get(`${CONTROL_NS}/${KEY}`)!;
      for (const secret of [ACCOUNT, ...[...SECRET_INPUTS, ...OPTIONAL, "CLOUDFLARE_API_TOKEN"]
        .map((name) => ENV[name]).filter((value) => value !== undefined)]) {
        expect(text).not.toContain(secret);
      }
      // The plan says how to hand it back, and the summary holds the record too.
      expect(work.plan).toContain(`record the zone's ${table.length} routes and the handover's 10 ` +
        "changes, the egress domain (none) and E2B's lifecycle webhook as this " +
        `run's route record in ${NAMESPACES["CONTROL"]}, before any change: hand back with --mode ` +
        "handback --record <this run's id>; the webhook it records: disabled");
      const summaryText = readFileSync(file, "utf8");
      expect(summaryText).toContain(`Route record ${KEY}:`);
      // The copy is the record as first written, before the attach's DNS record joined it.
      expect(summaryText).toContain(JSON.stringify({ ...stored(w), domain: {
        ...stored(w).domain, created: null } }, null, 2));
      // The run's id, which the handback takes, heads the plan.
      expect(summaryText).toContain("Run 7001 changed:");
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("stops routes before any change while the egress host is another Worker's or holds a " +
     "DNS record, which the custom domain would replace", async () => {
    for (const [options, why] of [
      [{ egress: "someone-else" }, `${EGRESS_HOST} is someone-else's custom domain: routes ` +
       "would take it, and a handback could not give it back; nothing is changed"],
      [{ dns: ["A", "AAAA"] }, `${EGRESS_HOST} holds 2 DNS record(s) (A, AAAA): the doors' ` +
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
      `routes keeps its route record in ${NAMESPACES["CONTROL"]}, which a dark run makes: run dark ` +
      "first; nothing is changed");
    expect(fresh.events.filter((event) => CHANGE.test(event))).toEqual([]);
    const planned = await runMode(world(), "routes", [], RUN);
    expect(planned.plan).toContain("record the zone's routes as this run's route record: " +
                                   `${NAMESPACES["CONTROL"]} is not made yet, so the apply job stops ` +
                                   "until a dark run makes it");
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
    expect(again.plan).toContain(`keep this run's route record as attempt 1 wrote it at ${
      NOW.toISOString()}, before its first change: hand back with --mode handback --record ` +
      "<this run's id>");
    const later = await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ID: "7002" });
    // The doors hold the old proxy's three in place, in the zone's order, and the five added.
    expect(later.plan.find((line) => line.startsWith("record "))).toBe(`record the zone's 9 routes and the handover's 0 changes, the ` +
      `egress domain (${DOORS}) and E2B's lifecycle webhook as this run's route ` +
      `record in ${NAMESPACES["CONTROL"]}, before any change: hand back with --mode handback ` +
      "--record <this run's id>; " +
      `${DOORS} holds ${["mcp*", "app*", "api*", "hooks/inbound/*", "hooks/e2b/*", "authorize*",
                         "oauth/*", ".well-known/oauth-*"].map((path) => `${HOST}/${path}`).join(", ")} ` +
      "already: the record of the routes run that moved them is the one to hand back, and it " +
      "covers this run's changes too; the webhook it records: enabled");
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
    expect(fresh.plan).toContain("record the zone's 6 routes and the handover's 9 changes, the " +
      "egress domain (none) and E2B's lifecycle webhook as this run's route record " +
      `in ${NAMESPACES["CONTROL"]}, before any change: hand back with --mode handback --record ` +
      `<this run's id>; ${DOORS} holds ${HOST}/mcp* already: the record of the ` +
      "routes run that moved them is the one to hand back, and it covers this run's changes " +
      "too; the webhook it records: disabled");
    // The first run's record does: its handback, after the second run, puts the zone back.
    const before = kept(stored(w).routes);
    await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ID: "7002" });
    await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(kept(w.routes.values())).toEqual(before);
  });

  describe("at a host under the zone", () => {
    // The kit as it runs while the identity's host is a name under the zone (the live
    // stage's): its modules loaded afresh over that identity. The zone keeps the old proxy's
    // routes (`LIVE`, on this tree's host, the zone), which the host shares none of.
    const STAGE = `stage.${ZONE}`;
    const STAGE_HOOK = `https://${STAGE}${identity.hook}`;
    const PLACED = `AAAA ${STAGE} (100::)`;
    let stage: Kit;
    beforeAll(async () => {
      vi.resetModules();
      vi.doMock("../../contract/identity.json", () => ({ default: { ...identity, host: STAGE } }));
      stage = await import("../deploy");
      vi.doUnmock("../../contract/identity.json");
      vi.resetModules();
    });
    const onStage = (w: ReturnType<typeof world>) =>
      w.dns.filter((record) => record.name === STAGE);

    it("makes the host's proxied record once the route record is kept and before any deploy, " +
       "keeps it, and its handback deletes exactly it", async () => {
      const w = world({ held: true });
      const apex = kept(w.routes.values());
      const work = await runMode(w, "routes", ["--yes"], RUN, stage);
      const [write, made, again, deployed] = order(w.events, [
        `cf PUT ${RECORD_AT}`, "cf POST /zones/z/dns_records", `cf PUT ${RECORD_AT}`,
        "wrangler versions upload"]);
      expect(made).toBe(write! + 1);
      expect(again).toBeLessThan(deployed!);
      expect(onStage(w)).toEqual([{ id: "dns-made3", type: "AAAA", name: STAGE,
                                    content: "100::", proxied: true }]);
      expect(stored(w).host).toEqual({ name: STAGE, held: false, made: [
        { id: "dns-made3", type: "AAAA", content: "100::" }] });
      expect(work.plan).toContain(`make the DNS record ${PLACED}, proxied, for the doors' ` +
        "routes on it, before anything is deployed: the route record keeps it, for a handback " +
        "to delete exactly it");
      expect(work.plan).toContain(`record in ${KEY} the DNS record made on ${STAGE}: ${PLACED}`);
      // Every route is added on the host; the zone's are left as they were.
      expect(w.events.filter((event) => /^cf (PUT|DELETE) \/zones\/z\/workers/.test(event)))
        .toEqual([]);
      expect(kept(w.routes.values())).toEqual(kept([...apex, ...identity.routes.map((path) =>
        ({ pattern: `${STAGE}${path}`, script: DOORS }))]));
      // The one hook moves to the host, and is enabled.
      expect(w.hooks).toEqual([expect.objectContaining({ id: "w1", url: STAGE_HOOK,
                                                         enabled: true })]);
      expect(work.plan).toContain(`E2B lifecycle webhook ${STAGE_HOOK}, after the routes: ` +
        `present, its URL (${E2B_HOOK.url} before), enabled set, enabled`);
      // The handback: the routes, then the host's record, only it.
      const from = w.events.length;
      const back = await runMode(w, "handback", ["--yes", "--record", "7001"], ENV, stage);
      expect(back.plan).toContain(`delete the DNS record routes made on ${STAGE} for the doors' ` +
                                  `routes, ${PLACED}, where it is still there`);
      const ran = w.events.slice(from);
      const [routed, deleted] = order(ran, ["cf DELETE /zones/z/workers/routes",
                                            "cf DELETE /zones/z/dns_records/dns-made3"]);
      expect(deleted).toBeGreaterThan(routed!);
      expect(ran.filter((event) => event.startsWith("cf DELETE /zones/z/dns_records/")))
        .toEqual(["cf DELETE /zones/z/dns_records/dns-made3"]);
      expect([onStage(w), kept(w.routes.values())]).toEqual([[], apex]);
      expect(w.dns.map((record) => record.id)).toEqual(["o1", "o2", "o3"]);
      expect(w.hooks[0]).toMatchObject({ url: STAGE_HOOK, enabled: false });
    });

    it("leaves a placeholder the host held before routes, and its handback deletes none",
       async () => {
      const w = world({ held: true });
      w.dns.push({ id: "p1", type: "AAAA", name: STAGE, content: "100::" });
      const work = await runMode(w, "routes", ["--yes"], RUN, stage);
      expect(work.plan).toContain(`${STAGE} holds its DNS record already, ${PLACED}: none is made`);
      expect(stored(w).host).toEqual({ name: STAGE, held: true, made: null });
      expect(w.events.some((event) => event.startsWith("cf POST /zones/z/dns_records")))
        .toBe(false);
      const back = await runMode(w, "handback", ["--yes", "--record", "7001"], ENV, stage);
      expect(back.plan.some((line) => line.includes(`routes made on ${STAGE}`))).toBe(false);
      expect(onStage(w).map((record) => record.id)).toEqual(["p1"]);
    });

    it("stops before any change while the host holds any other address record, the plan job " +
       "too", async () => {
      const w = world({ held: true });
      w.dns.push({ id: "f1", type: "A", name: STAGE, content: "192.0.2.7" },
                 { id: "p1", type: "AAAA", name: STAGE, content: "100::" });
      const why = `${STAGE} holds 1 DNS record(s) the doors' routes would take the requests of ` +
        "(A 192.0.2.7), and a handback could not give them back; nothing is changed";
      await expect(runMode(w, "routes", ["--yes"], RUN, stage)).rejects.toThrow(why);
      expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
      await expect(runMode(w, "routes", ["--public"], PUBLIC, stage)).rejects.toThrow(why);
    });

    it("stops at a refused make with only the route record written; the record added by hand, " +
       "its re-run keeps that one as the run's, for its handback to delete", async () => {
      const options = { held: true, refusingDns: true };
      const w = world(options);
      await expect(runMode(w, "routes", ["--yes"], RUN, stage)).rejects.toThrow(
        `making the DNS record ${PLACED} failed (403, [10000])`);
      expect(w.events.filter((event) => CHANGE.test(event)))
        .toEqual([`cf PUT ${RECORD_AT}`, "cf POST /zones/z/dns_records"]);
      expect(stored(w).host).toEqual({ name: STAGE, held: false, made: null });
      // The checklist's fallback: the founder adds it, and re-runs the run.
      options.refusingDns = false;
      w.dns.push({ id: "p1", type: "AAAA", name: STAGE, content: "100::" });
      const again = await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ATTEMPT: "2" },
                                  stage);
      expect(again.plan).toContain(`${STAGE} holds ${PLACED}, and held none when this run's ` +
        "route record was kept: the record keeps it as this run's, for a handback to delete");
      expect(stored(w).host).toEqual({ name: STAGE, held: false,
                                       made: [{ id: "p1", type: "AAAA", content: "100::" }] });
      await runMode(w, "handback", ["--yes", "--record", "7001"], ENV, stage);
      expect(onStage(w)).toEqual([]);
    });

    it("records on a re-run the record its first attempt made before it was written in, and " +
       "makes none again", async () => {
      const w = world({ held: true });
      w.kill.after = 0;
      await expect(runMode(w, "routes", ["--yes"], RUN, stage)).rejects.toThrow(
        "the run was killed");
      expect([stored(w).host, onStage(w).length]).toEqual([
        { name: STAGE, held: false, made: null }, 1]);
      // Handed back now, the record it never wrote in is named, never deleted.
      await expect(runMode(w, "handback", ["--yes", "--record", "7001"], ENV, stage)).rejects
        .toThrow(`${STAGE} holds the DNS record AAAA (100::), and held none before routes: the ` +
                 "one routes made, not recorded, for a hand to remove");
      w.kill.after = undefined;
      const from = w.events.length;
      await runMode(w, "routes", ["--yes"], { ...RUN, GITHUB_RUN_ATTEMPT: "2" }, stage);
      expect(w.events.slice(from).filter((event) =>
        event.startsWith("cf POST /zones/z/dns_records"))).toEqual([]);
      expect([stored(w).host, onStage(w).length]).toEqual([
        { name: STAGE, held: false, made: [{ id: "dns-made3", type: "AAAA", content: "100::" }] },
        1]);
    });
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
    order(ran, ["log Run ", "log The handles canary (Fact 11) waits for the old stack"]);
    expect(w.logs.at(-1)).toContain(
      "dispatch the Handles canary workflow (handles-canary.yml), the old stack's own check");
    expect(ran.some((event) => event.startsWith("node canary"))).toBe(false);
    // Every path the record served is served after each change.
    const served = PROBES.filter((path) => servedBy(before as Route[], path) !== undefined);
    for (const table of w.tables) {
      for (const path of served) {
        expect([path, servedBy(table, path)]).not.toEqual([path, undefined]);
      }
    }
    expect(work.plan).toEqual([
      HELD,
      `the route record ${KEY}: the zone's ${before.length} routes as run 7001 read them at ${
        NOW.toISOString()}, and the handover's 10 changes`,
      `E2B lifecycle webhook ${E2B_HOOK.url}: disable it, as it was before routes`,
      `detach the custom domain ${EGRESS_HOST} from ${DOORS}, as it was before routes`,
      `add the route ${HOST}/.well-known/oauth-protected-resource* back for ` +
      "tinyassets-mcp-proxy",
      `add the route ${HOST}/.well-known/oauth-authorization-server* back for ` +
      "tinyassets-mcp-proxy",
      ...["mcp*", "api*", "app*"].map((path) =>
        `move the route ${HOST}/${path} from ${DOORS} back to tinyassets-mcp-proxy`),
      ...["hooks/inbound/*", "hooks/e2b/*", "authorize*", "oauth/*", ".well-known/oauth-*"].map(
        (path) => `delete ${DOORS}'s route ${HOST}/${path}, which the handover added`),
      `delete the DNS record(s) the custom domain made, AAAA ${EGRESS_HOST} (100::), where ` +
      "the detach leaves them",
      "the zone's routes, the egress domain and the webhook read back as the record has them",
    ]);
    // Run again, it changes nothing.
    const at = w.events.length;
    const again = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(w.events.slice(at).filter((event) => ROUTE_CHANGE.test(event))).toEqual([]);
    expect(again.plan.slice(2)).toEqual([
      `E2B lifecycle webhook ${E2B_HOOK.url}: not enabled already`,
      `the custom domain ${EGRESS_HOST}: detached already`,
      "the zone's routes are the record's already: nothing to hand back",
      `delete the DNS record(s) the custom domain made, AAAA ${EGRESS_HOST} (100::), where ` +
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
      `there is no route record deploy:routes:7999 in ${NAMESPACES["CONTROL"]}: name the id a routes ` +
      "run's job summary gives; nothing is changed");
    await expect(runMode(world(), "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      /there is no route record deploy:routes:7001/);
    const { w } = await cutOver();
    const good = stored(w);
    const route = good.routes[0]!;
    const added = good.moved.find((each) => each.method === "POST")!;
    for (const [bad, why] of [
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
      [{ ...good, host: undefined }, "holds no host record in shape"],
      [{ ...good, host: { ...good.host, name: `other.${ZONE}` } }, "holds no host record in shape"],
      [{ ...good, host: { ...good.host, made: [{ id: "d", type: "AAAA", content: "100::" }] } },
       "holds no host record in shape"],
      [{ ...good, host: { ...good.host, held: false, made: [{ id: "d", type: "AAAA" }] } },
       "holds no host record in shape"],
      [{ ...good, hook: { ...good.hook, url: "https://elsewhere.test/hook" } },
       "holds no lifecycle webhook in shape"],
      [{ ...good, hook: { url: E2B_HOOK.url, present: false, enabled: true } },
       "holds no lifecycle webhook in shape"],
    ] as const) {
      w.kv.set(`${CONTROL_NS}/${KEY}`, JSON.stringify(bad));
      const at = w.events.length;
      await expect(runMode(w, "handback", ["--yes", "--record", "7001"]), why).rejects.toThrow(
        `the record ${KEY} ${why}`);
      expect(w.events.slice(at).filter((event) => CHANGE.test(event))).toEqual([]);
    }
    // One that is not JSON stops it too: never read as an empty record.
    w.kv.set(`${CONTROL_NS}/${KEY}`, "not json");
    const at = w.events.length;
    await expect(runMode(w, "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      `/values/${encodeURIComponent(KEY)} with a body that is not JSON (200)`);
    expect(w.events.slice(at).filter((event) => CHANGE.test(event))).toEqual([]);
  });

  it("leaves the custom domain and the hook where the record has them already the doors'",
     async () => {
    // Attached to the doors and enabled before routes: the record says so, the handback leaves
    // both, and reads back only the routes for them.
    const w = world({ held: true, egress: DOORS });
    w.hooks[0]!["enabled"] = true;
    await runMode(w, "routes", ["--yes"], RUN);
    expect([stored(w).domain, stored(w).hook]).toEqual([
      { hostname: EGRESS_HOST, service: DOORS, created: null },
      { url: E2B_HOOK.url, present: true, enabled: true }]);
    const at = w.events.length;
    const work = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(work.plan.slice(2, 4)).toEqual([
      `E2B lifecycle webhook ${E2B_HOOK.url}: enabled before routes, left`,
      `the custom domain ${EGRESS_HOST}: ${DOORS}'s before routes, left`]);
    expect(w.events.slice(at).filter((event) => /^(e2b PATCH|cf DELETE \/accounts)/.test(event)))
      .toEqual([]);
    expect([w.domains.size, w.hooks[0]!["enabled"]]).toEqual([1, true]);
  });

  it("disables a hook routes registered, as it was absent before: never deleted", async () => {
    const w = world({ held: true });
    w.hooks.length = 0;
    await runMode(w, "routes", ["--yes", "--rotate", E2B_HOOK.secret], RUN);
    expect(stored(w).hook).toEqual({ url: E2B_HOOK.url, present: false, enabled: false });
    expect(w.hooks[0]!["enabled"]).toBe(true);
    const work = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(work.plan[2]).toBe(`E2B lifecycle webhook ${E2B_HOOK.url}: disable it, as it was ` +
                              "absent before routes (a disabled hook sends nothing)");
    expect(w.hooks).toEqual([expect.objectContaining({ id: "w1", enabled: false })]);
    expect(w.events.some((event) => event.startsWith("e2b DELETE"))).toBe(false);
  });

  it("stops when the hook or the custom domain reads back unlike the record", async () => {
    for (const [inert, why] of [
      ["hook", `E2B lifecycle webhook ${E2B_HOOK.url} is enabled (the record has it disabled)`],
      ["domain", `${EGRESS_HOST} is ${DOORS}'s custom domain (the record has none)`],
      ["dns", `the DNS record AAAA ${EGRESS_HOST} (100::) the custom domain made is still there`],
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
    w.domains.set("d1", { id: "d1", hostname: EGRESS_HOST, service: "someone-else" });
    await expect(runMode(w, "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      `changed since routes, so nothing is handed back: ${EGRESS_HOST} is someone-else's ` +
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
    const what = "the record to hand back (a routes run's) or roll back (a dark or routes " +
      "run's), by the run id its job summary names (letters, digits and -)";
    for (const argv of [["--mode", "handback"], ["--mode", "handback", "--record"]]) {
      expect(() => parse(argv, ENV, w.io), argv.join(" "))
        .toThrow(`mode handback needs --record: ${what}`);
    }
    for (const id of ["a/b", "x".repeat(65)]) {
      expect(() => parse(["--mode", "handback", "--record", id], ENV, w.io), id)
        .toThrow(`--record is ${what}`);
    }
    for (const mode of ["plan", "dark", "routes", "control", "owner"]) {
      expect(() => parse(["--mode", mode, "--record", "7001"], ENV, w.io)).toThrow(
        `mode ${mode} takes no --record: it is mode handback and rollback's`);
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
      || event.startsWith("node canary"))).toEqual([]);
    expect(work.plan).toHaveLength(14);
    expect(work.plan[1]).toBe(`E2B lifecycle webhook ${E2B_HOOK.url}: checked by the apply job ` +
                              "before any change");
    expect(w.logs.at(-1)).toBe("\nNothing was changed.");
  });

  it("never touches a route the record and the handover do not hold, and stops before any " +
     "change on a route changed since routes, naming it", async () => {
    const { w, before } = await cutOver();
    w.routes.set("u1", { id: "u1", pattern: `${HOST}/later*`, script: "site" });
    const work = await runMode(w, "handback", ["--yes", "--record", "7001"]);
    expect(work.plan).toContain(`leave the route ${HOST}/later* (site's): neither the ` +
                                "record nor the handover holds it");
    expect(kept(w.routes.values())).toEqual(kept([...before,
      { pattern: `${HOST}/later*`, script: "site" }]));
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
      [changed(`${HOST}/hooks/e2b/*`, "someone-else"), `${HOST}/hooks/e2b/* is ` +
       `someone-else's, neither as the handover left it (${DOORS}'s) nor as the record has it ` +
       "(absent)"],
      // A recorded route the handover never changed.
      [changed(`${HOST}/site*`, "site-2"), `${HOST}/site* is site-2's, the record ` +
       "has it site's and the handover never changed it"],
      [changed(`${HOST}/site*`, undefined), `${HOST}/site* is absent, the record ` +
       "has it site's and the handover never changed it"],
      // A route it moved, moved again; one it deleted, made again for another Worker.
      [changed(`${HOST}/mcp*`, "someone-else"), `${HOST}/mcp* is someone-else's, ` +
       `neither as the handover left it (${DOORS}'s) nor as the record has it ` +
       "(tinyassets-mcp-proxy's)"],
      [changed(`${HOST}/.well-known/oauth-protected-resource*`, "someone-else"),
       `${HOST}/.well-known/oauth-protected-resource* is someone-else's, neither as the ` +
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
    const pattern = `${HOST}/.well-known/oauth-authorization-server*`;
    const w = world({ held: true, refusingRoute: `POST ${pattern}` });
    const before = kept(w.routes.values());
    await runMode(w, "routes", ["--yes"], RUN);
    const from = w.events.length;
    await expect(runMode(w, "handback", ["--yes", "--record", "7001"])).rejects.toThrow(
      `add the route ${pattern} back for tinyassets-mcp-proxy failed (400, [10020]); the steps ` +
      "before it are done, the rest are not: run it again for the rest");
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
                         ["--yes"], RUN)).rejects.toThrow(`${EGRESS_HOST} holds 1 DNS record(s) (CNAME)`);
  });

  for (const keepsDns of [false, true]) {
    it(`removes the DNS record the custom domain made ${keepsDns ? "when the detach leaves it"
      : "only if the detach leaves it"}, and a later routes runs again`, async () => {
      const w = world({ held: true, keepsDns });
      await runMode(w, "routes", ["--yes"], RUN);
      expect(w.dns.filter((record) => record.name === EGRESS_HOST).map((record) => record.id))
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
    w.dns.push({ id: "later", type: "A", name: EGRESS_HOST, content: "192.0.2.9" });
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
          `${EGRESS_HOST} holds the DNS record AAAA (100::), and held no address record ` +
          "before routes: the custom domain's own, not recorded, for a hand to remove");
        // Never deleted: only a recorded record is matched exactly.
        expect(w.dns.some((each) => each.id === "dns-egress")).toBe(true);
      } else {
        const work = await handback;
        expect(work.plan).toContain(`the DNS records the custom domain made on ${EGRESS_HOST} ` +
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
      `deleting the DNS record AAAA ${EGRESS_HOST} (100::) failed (403, [10000]): run the ` +
      "handback again");
    expect(kept(refused.routes.values())).toEqual(before);
  });
});

describe("the rollout record and mode rollback", () => {
  const ROLLOUT = "deploy:rollout:7001";
  const OURS = COMMIT.slice(0, 12);
  /** The run's rollout record as KV holds it. */
  const recordIn = (w: ReturnType<typeof world>) =>
    JSON.parse(w.kv.get(`${CONTROL_NS}/${ROLLOUT}`) ?? "null") as RolloutRecord | null;
  /** The deployments the kit made over Cloudflare's API, by Worker, in order. */
  const deployed = (w: ReturnType<typeof world>) => w.events.flatMap((event) =>
    /^cf POST \/accounts\/a+\/workers\/scripts\/([a-z-]+)\/deployments$/.exec(event)?.[1] ?? []);
  /** What each Worker serves, by script: each version and its share. */
  const serving = (w: ReturnType<typeof world>) => Object.fromEntries([...w.deployments]
    .map(([script, versions]) => [script, versions.map((each) =>
      `${each.version}@${each.percentage}`).join(",")]));
  /** Each Worker's version before, as a held account serves it. */
  const BEFORE_ALL = Object.fromEntries(WORKERS.map((worker) =>
    [NAMES[worker]!, `before-${worker}@100`]));
  /** The locks with the owner's pinning one migration more than its version before's tag. */
  const BUMPED = { ...PINNED, owner: Object.fromEntries(Object.entries(PINNED["owner"]!).map(
    ([path, pinned]) => [path, [...pinned, "the next migration"]])) };

  it("keeps each Worker's version and tag before the first deploy, and a re-run attempt keeps " +
     "what its first read", async () => {
    const w = world({ held: true });
    await runMode(w, "dark", ["--yes"], RUN);
    const write = w.events.indexOf(`${CONTROL_WRITE}namespaces/${CONTROL_NS}/values/${
      encodeURIComponent(ROLLOUT)}`);
    expect(write).toBeGreaterThan(w.events.findLastIndex((event) => event.endsWith("/secrets")));
    expect(write).toBeLessThan(w.events.findIndex((event) => event.startsWith("wrangler ")));
    expect(recordIn(w)).toEqual({
      kind: "tinyassets-rollout", run: "7001", attempt: "1", at: NOW.toISOString(), commit: COMMIT,
      workers: WORKERS.map((worker) => ({
        name: NAMES[worker], before: { version: `before-${worker}`,
                                       tag: HELD_SERVING[NAMES[worker]!]![0]!.tag },
        counts: Object.fromEntries(Object.entries(PINNED[worker] ?? {}).map(([path, pinned]) =>
          [path, pinned.length])),
        whole: false, secrets: [], forward: 0 })) });
    // Its every new version is tagged with the run's commit.
    expect(Object.values(serving(w)).map((each) =>
      w.versions.get(each.replace(/@100$/, ""))!.tag!.slice(0, 12))).toEqual(WORKERS.map(() => OURS));
    // A re-run attempt keeps the record its first attempt wrote, whatever is deployed now.
    const first = recordIn(w);
    const again = await runMode(w, "dark", ["--yes"], { ...RUN, GITHUB_RUN_ATTEMPT: "2" });
    expect(recordIn(w)).toEqual(first);
    expect(again.plan).toContain(`keep this run's rollout record as attempt 1 wrote it at ${
      NOW.toISOString()}, before its first deploy: roll back with --mode rollback --record ` +
      "<this run's id>");
    // A split stops a re-run attempt too, before any deploy.
    const gateway = NAMES["gateway"]!;
    w.deployments.set(gateway, [{ ...w.deployments.get(gateway)![0]!, percentage: 90 },
                                { version: "before-gateway", percentage: 10 }]);
    w.events.splice(0);
    await expect(runMode(w, "dark", ["--yes"], { ...RUN, GITHUB_RUN_ATTEMPT: "3" })).rejects
      .toThrow(`${gateway} is split (`);
    expect(w.events.filter((event) => event.startsWith("wrangler "))).toEqual([]);
  });

  it("stops before any deploy when a Worker serves two versions, or its record is refused",
     async () => {
    const split = world({ held: true, serving: { ...HELD_SERVING, [NAMES["gateway"]!]: [
      { version: "gw-new", percentage: 10, tag: OURS }, { version: "gw-old", percentage: 90,
                                                          tag: BEFORE }] } });
    await expect(runMode(split, "dark", ["--yes"], RUN)).rejects.toThrow(
      `${NAMES["gateway"]} is split (gw-new at 10%, gw-old at 90%): end the split first ` +
      "(deploy.ts --mode rollback, or its Deployments in the dashboard); nothing is changed");
    expect(split.events.filter((event) => CHANGE.test(event))).toEqual([]);
    const refused = world({ held: true, refusing: ROLLOUT });
    await expect(runMode(refused, "dark", ["--yes"], RUN)).rejects.toThrow(
      `writing ${ROLLOUT} failed (500)`);
    expect(refused.events.filter((event) => event.startsWith("wrangler "))).toEqual([]);
  });

  it("plans an optional secret the doors lack, and the doors' way back, as the apply job's to " +
     "tell; sets none it is not given, so a rollback takes them back", async () => {
    // The shared app's and the canary clock's, unset in production (run 45014046824).
    const lacking = ["INBOUND_SLACK_SIGNING_SECRET", "PLATFORM_CANARY_TOKEN"];
    const without = Object.fromEntries(Object.entries(RUN).filter(([name]) =>
      !lacking.includes(name)));
    const doors = (lines: readonly string[]) => lines.filter((line) =>
      line.startsWith(`${DOORS}: `) || line.includes(` on ${DOORS} with its version`));
    const setLine = (name: string) => `set the secret ${name} on ${DOORS} with its version if ` +
      "the run is given it";
    const unless = `${DOORS}: serving before-doors (${HELD_SERVING[DOORS]![0]!.tag}); a ` +
      `rollback takes it back there unless the run sets ${lacking.join(", ")} on it`;
    const LATER = "checked by the apply job before any change";
    const w = world({ held: true, lacking });
    expect(doors((await runMode(w, "dark", ["--public"], PUBLIC)).plan)).toEqual([
      ...lacking.map((name) => `${setLine(name)}: ${LATER}`), `${unless}: ${LATER}`]);
    const work = await runMode(w, "dark", ["--yes"], without);
    expect(doors(work.plan)).toEqual([
      ...lacking.map((name) => `${setLine(name)}: it is not, so it stays unset`),
      `${unless}: it sets none of them`]);
    expect(recordIn(w)!.workers.find((each) => each.name === DOORS)!.secrets).toEqual([]);
    expect([w.secretFiles, w.secretsPut]).toEqual([[], {}]);
    w.events.splice(0);
    await runMode(w, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(serving(w)).toEqual(BEFORE_ALL);
    // Given them, the run sets both with the doors' version, and a rollback stops at it.
    const given = world({ held: true, lacking });
    expect(doors((await runMode(given, "dark", ["--yes"], RUN)).plan)).toEqual([
      ...lacking.map((name) => `${setLine(name)}: it is`),
      `${unless}: it sets ${lacking.join(", ")}, so it stops at it: the run set ${
        lacking.join(", ")} on it, which its version before holds otherwise (Cloudflare ` +
      "refuses that rollback, and the kit never forces one)"]);
    expect(recordIn(given)!.workers.find((each) => each.name === DOORS)!.secrets)
      .toEqual(lacking);
    expect(given.secretFiles.map(({ worker, names }) => [worker, names]))
      .toEqual([["doors", lacking]]);
  });

  it("takes the run's Workers back callers first, each read back, then the canary; again, it " +
     "changes nothing", async () => {
    const w = world({ held: true });
    await runMode(w, "dark", ["--yes"], RUN);
    expect(serving(w)).not.toEqual(BEFORE_ALL);
    w.events.splice(0);
    const work = await runMode(w, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(deployed(w)).toEqual([...WORKERS].reverse().map((worker) => NAMES[worker]));
    expect(serving(w)).toEqual(BEFORE_ALL);
    expect(work.plan.slice(1)).toEqual([
      `the rollout record ${ROLLOUT}: the ${WORKERS.length} Workers run 7001 deployed at ${
        NOW.toISOString()}, taken back in the reverse of its deploy order`,
      ...[...WORKERS].reverse().map((worker) => expect.stringMatching(new RegExp(
        `^roll ${NAMES[worker]} back to before-${worker} \\(${BEFORE}[-m0-9.]*\\) at 100%, ` +
        `from 0f7e3a3c-\\S+ \\(${OURS}[-m0-9.]*\\) at 100%$`))),
      `read each Worker's deployment back: ${[...WORKERS].reverse().map((worker) =>
        NAMES[worker]).join(", ")} serve their versions before the run`]);
    // Each deployment is read back before the next is made, and the canary follows the last.
    const name = NAMES["doors"]!;
    order(w.events, [`cf POST /accounts/${ACCOUNT}/workers/scripts/${name}/deployments`,
                     `cf GET /accounts/${ACCOUNT}/workers/scripts/${name}/deployments`,
                     `cf POST /accounts/${ACCOUNT}/workers/scripts/${NAMES["owner"]}/deployments`,
                     "log Run ", "node canary.ts"]);
    w.events.splice(0);
    const again = await runMode(w, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(deployed(w)).toEqual([]);
    expect(again.plan.at(-1)).toBe("every Worker serves its version before the run already");
  });

  it("plans with the token alone and changes nothing without --yes", async () => {
    const w = world({ held: true });
    await runMode(w, "dark", ["--yes"], RUN);
    w.events.splice(0);
    const planned = await runMode(w, "rollback", ["--public", "--record", "7001"],
                                  { ...PUBLIC, GITHUB_RUN_ID: "7002" });
    expect(planned.plan).toHaveLength(WORKERS.length + 1);
    expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
  });

  it("stops at the first Worker the run made forward-only, and with none to take back changes " +
     "nothing", async () => {
    // The owner's version adds a migration: the doors go back, the owner and its callees stay.
    const bumped = world({ held: true, pinned: BUMPED });
    await runMode(bumped, "dark", ["--yes"], RUN);
    const counts = (pinned: Readonly<Record<string, readonly string[]>>) => Object.keys(pinned)
      .sort().map((path) => pinned[path]!.length).join(".");
    bumped.events.splice(0);
    const work = await runMode(bumped, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(deployed(bumped)).toEqual([NAMES["doors"]]);
    expect(work.plan).toContainEqual(expect.stringMatching(new RegExp(
      `^leave ${NAMES["owner"]} on 0f7e3a3c-\\S+ \\(${OURS}-m${counts(BUMPED.owner)}\\) at 100%: ` +
      `the run's version pins -m${counts(BUMPED.owner)} and its version before -m${
        counts(PINNED["owner"]!)}: every object the newer one migrated would refuse to start ` +
      "on it$")));
    for (const worker of WORKERS.slice(0, WORKERS.indexOf("owner"))) {
      expect(work.plan).toContainEqual(expect.stringMatching(new RegExp(
        `^leave ${NAMES[worker]} on .*: it comes before ${NAMES["owner"]} in the deploy order, ` +
        `and ${NAMES["owner"]} stays on the run's version$`)));
    }
    // A record whose counts or tag before are not Cloudflare's tags is never rolled back.
    const held = recordIn(bumped)!;
    for (const owner of [{ counts: Object.fromEntries(Object.entries(PINNED["owner"]!).map(
                           ([path, pinned]) => [path, pinned.length])) },
                         { before: { version: "before-owner", tag: `${BEFORE}-m99` } }]) {
      bumped.kv.set(`${CONTROL_NS}/${ROLLOUT}`, JSON.stringify({ ...held, workers:
        held.workers.map((each) => each.name === NAMES["owner"] ? { ...each, ...owner } : each) }));
      bumped.events.splice(0);
      await expect(runMode(bumped, "rollback", ["--yes", "--record", "7001"], RUN)).rejects
        .toThrow(`the record ${ROLLOUT} holds ${NAMES["owner"]} as before `);
      expect(bumped.events.filter((event) => CHANGE.test(event))).toEqual([]);
    }
    // The routing key set on the doors and the owner: the doors stay, so nothing goes back.
    const rotated = world({ held: true });
    await runMode(rotated, "dark", ["--yes", "--rotate", "ROUTING_MAC_KEY"], RUN);
    rotated.events.splice(0);
    await expect(runMode(rotated, "rollback", ["--yes", "--record", "7001"], RUN)).rejects
      .toThrow(`nothing can be rolled back: ${NAMES["doors"]} stays on run 7001's version (the ` +
               "run set ROUTING_MAC_KEY on it, which its version before holds otherwise " +
               "(Cloudflare refuses that rollback, and the kit never forces one)). Roll forward " +
               "with a fix, or close the control record's gates (--mode control); nothing is " +
               "changed");
    expect(rotated.events.filter((event) => CHANGE.test(event))).toEqual([]);
  });

  it("names why each Worker is forward-only, from the record's facts alone", () => {
    const rolled = (change: Partial<Rolled>): Rolled => ({
      name: "w", before: { version: "v", tag: `${BEFORE}-m3.1` }, counts: { a: 3, b: 1 },
      whole: false, secrets: [], forward: 0, ...change });
    expect(forwardOnly(rolled({}))).toBeNull();
    expect(forwardOnly(rolled({ counts: { a: 2, b: 1 } }))).toBeNull();
    expect(forwardOnly(rolled({ counts: {}, before: { version: "v", tag: null } }))).toBeNull();
    expect(forwardOnly(rolled({ before: null })))
      .toBe("the run deployed it first: no version comes before it");
    expect(forwardOnly(rolled({ whole: true }))).toBe(
      "the run deployed it whole (--migrate), and Cloudflare rolls back across no Durable " +
      "Object class change");
    expect(forwardOnly(rolled({ secrets: ["K1", "K2"] }))).toMatch(/^the run set K1, K2 on it/);
    // The counts in their lock paths' order, held one by one.
    expect(forwardOnly(rolled({ counts: { b: 2, a: 3 } }))).toBe(
      "the run's version pins -m3.2 and its version before -m3.1: every object the newer one " +
      "migrated would refuse to start on it");
    for (const tag of [null, BEFORE, `${BEFORE}-m3`, `${BEFORE}-m3.1.1`]) {
      expect(forwardOnly(rolled({ before: { version: "v", tag } })), String(tag)).toBe(
        `its version before is tagged ${tag ?? "with nothing"}, which names no migration ` +
        "counts to hold the run's -m3.1 to: unknown");
    }
    // A forward count above the version before's stops it; one at or below it, or none, not.
    const marked = (forward: number, tag: string | null) =>
      forwardOnly(rolled({ forward, before: { version: "v", tag } }));
    const cannot = "it writes, or is called, in a way its version before cannot follow " +
      "(platform.json forward)";
    expect(marked(1, `${BEFORE}-m3.1`)).toBe(
      `the run's version is marked -f1 and its version before with none: ${cannot}`);
    expect(marked(3, `${BEFORE}-m3.1-f2`)).toBe(
      `the run's version is marked -f3 and its version before -f2: ${cannot}`);
    expect([marked(2, `${BEFORE}-m3.1-f2`), marked(1, `${BEFORE}-m3.1-f2`),
            marked(0, `${BEFORE}-m3.1-f2`)]).toEqual([null, null, null]);
    for (const tag of [null, "v1.2", `${BEFORE}-f0`, `${BEFORE}-m3.1-f`]) {
      expect(marked(1, tag), String(tag)).toBe(`its version before is tagged ${
        tag ?? "with nothing"}, which names no forward count to hold the run's -f1 to: unknown`);
    }
    // The count rides the tag after the locks' counts, and is left out at 0.
    expect(versionFlags(COMMIT, { b: 1, a: 3 }, 2))
      .toEqual(["--tag", `${OURS}-m3.1-f2`, "--message", COMMIT]);
    expect(versionFlags(COMMIT, {}, 1)).toEqual(["--tag", `${OURS}-f1`, "--message", COMMIT]);
    expect(versionFlags(COMMIT, {}, 0)).toEqual(["--tag", OURS, "--message", COMMIT]);
  });

  it("holds every Worker to the record first: a deploy since stops it, a split of the run's " +
     "own is taken back", async () => {
    const w = world({ held: true });
    await runMode(w, "dark", ["--yes"], RUN);
    const gateway = NAMES["gateway"]!;
    const ours = w.deployments.get(gateway)!;
    w.versions.set("later", { script: gateway, tag: "d00d00d00d00" });
    w.deployments.set(gateway, [{ version: "later", percentage: 100 }]);
    w.events.splice(0);
    await expect(runMode(w, "rollback", ["--yes", "--record", "7001"], RUN)).rejects.toThrow(
      `deployed since run 7001, so nothing is rolled back: ${gateway} serves later ` +
      `(d00d00d00d00) at 100%, neither its version before the run nor one of commit ${OURS}`);
    expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
    // A split of the run's version and the one before, as a run stopped mid-rollout leaves it.
    w.deployments.set(gateway, [{ ...ours[0]!, percentage: 10 },
                                { version: "before-gateway", percentage: 90 }]);
    await runMode(w, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(serving(w)).toEqual(BEFORE_ALL);
  });

  it("holds the record's version before to Cloudflare's deployments, and its Workers to the " +
     "tree's deploy order", async () => {
    // The gateway served an older real version before the one the run found.
    const w = world({ held: true });
    const gateway = NAMES["gateway"]!;
    const older = { version: "older-gateway", tag: "a".repeat(12) };
    w.versions.set(older.version, { script: gateway, tag: older.tag });
    w.deployments.set(gateway, [{ version: older.version, percentage: 100 }]);
    w.deployments.set(gateway, [{ version: "before-gateway", percentage: 100 }]);
    await runMode(w, "dark", ["--yes"], RUN);
    const held = recordIn(w)!;
    const plant = (workers: readonly Rolled[]) => {
      w.kv.set(`${CONTROL_NS}/${ROLLOUT}`, JSON.stringify({ ...held, workers }));
      w.events.splice(0);
    };
    // Named as the one before, with its real tag: every tag agrees, and the history does not.
    plant(held.workers.map((each) => each.name === gateway ? { ...each, before: older } : each));
    await expect(runMode(w, "rollback", ["--yes", "--record", "7001"], RUN)).rejects.toThrow(
      `the record ${ROLLOUT} holds ${gateway} as serving ${older.version} before the run, and ` +
      `Cloudflare's deployments listing has no deployment of it alone followed by one of ` +
      `commit ${OURS}: nothing is changed`);
    expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
    // A Worker left out, or the order turned, so a callee would go back before its caller.
    const names = WORKERS.map((worker) => NAMES[worker]).join(", ");
    for (const workers of [held.workers.filter((each) => each.name !== DOORS),
                           [...held.workers].reverse()]) {
      plant(workers);
      await expect(runMode(w, "rollback", ["--yes", "--record", "7001"], RUN)).rejects.toThrow(
        `the record ${ROLLOUT} names the Workers ${workers.map((each) => each.name).join(", ")
        }, and this tree deploys ${names}, in that order: nothing is changed (a rollback by ` +
        "hand follows checklist.md)");
      expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
    }
    plant(held.workers);
    await runMode(w, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(serving(w)).toEqual(BEFORE_ALL);
  });

  it("takes a later run of the same commit back to the earlier's version, and the earlier back " +
     "after a later run's rollback", async () => {
    const w = world({ held: true });
    await runMode(w, "dark", ["--yes"], RUN);
    const first = serving(w);
    await runMode(w, "dark", ["--yes"], { ...RUN, GITHUB_RUN_ID: "7002" });
    await runMode(w, "rollback", ["--yes", "--record", "7002"], RUN);
    expect(serving(w)).toEqual(first);
    await runMode(w, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(serving(w)).toEqual(BEFORE_ALL);
  });

  it("stops at a Worker the run marked forward (-f), its count held to Cloudflare's tag",
     async () => {
    // A tree whose forward list names the owner once: its version's tag carries -f1.
    const w = world({ held: true });
    await runMode(w, "dark", ["--yes"], RUN);
    const owner = NAMES["owner"]!;
    const ours = w.deployments.get(owner)![0]!.version;
    const held = recordIn(w)!;
    const count = (forward: number) => {
      w.kv.set(`${CONTROL_NS}/${ROLLOUT}`, JSON.stringify({ ...held, workers: held.workers.map(
        (each) => each.name === owner ? { ...each, forward } : each) }));
      w.events.splice(0);
    };
    // The record's count is held to the tag: one the tag lacks is no record of the run's.
    count(1);
    await expect(runMode(w, "rollback", ["--yes", "--record", "7001"], RUN)).rejects.toThrow(
      `the record ${ROLLOUT} holds ${owner} as before ${HELD_SERVING[owner]![0]!.tag}, counts `);
    expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
    w.versions.set(ours, { script: owner, tag: `${w.versions.get(ours)!.tag}-f1` });
    const work = await runMode(w, "rollback", ["--yes", "--record", "7001"], RUN);
    expect(deployed(w)).toEqual([DOORS]);
    expect(work.plan).toContainEqual(expect.stringMatching(new RegExp(
      `^leave ${owner} on ${ours} \\(${OURS}-m[0-9.]+-f1\\) at 100%: the run's version is ` +
      "marked -f1 and its version before with none: it writes, or is called, in a way its " +
      "version before cannot follow \\(platform.json forward\\)$")));
  });

  it("stops loudly when Cloudflare refuses a deployment, or one reads back otherwise",
     async () => {
    const refused = world({ held: true, refusingDeploy: 10220 });
    await runMode(refused, "dark", ["--yes"], RUN);
    await expect(runMode(refused, "rollback", ["--yes", "--record", "7001"], RUN)).rejects
      .toThrow(`deploying before-doors of ${NAMES["doors"]} failed (400, [10220])`);
    const inert = world({ held: true });
    await runMode(inert, "dark", ["--yes"], RUN);
    inert.inert.add("deploy");
    inert.events.splice(0);
    await expect(runMode(inert, "rollback", ["--yes", "--record", "7001"], RUN)).rejects
      .toThrow(new RegExp(`^${NAMES["doors"]}'s deployment read back as 0f7e3a3c-\\S+ at 100%, ` +
                          "not before-doors at 100%: the rollback stops here$"));
    expect(deployed(inert)).toEqual([NAMES["doors"]]);
  });

  it("takes only a record a deploy wrote, in shape", async () => {
    await expect(runMode(world({ held: true }), "rollback", ["--yes", "--record", "9999"], RUN))
      .rejects.toThrow(`there is no rollout record deploy:rollout:9999 in ${
        NAMESPACES["CONTROL"]}: name the id a dark or routes run's job summary gives; nothing ` +
        "is changed");
    const good = { kind: "tinyassets-rollout", run: "1", attempt: null, at: "t", commit: COMMIT,
                   workers: [{ name: "w", before: null, counts: {}, whole: false, secrets: [],
                                forward: 0 }] };
    expect(rolloutOf(good, "k")).toEqual(good);
    const one = good.workers[0]!;
    for (const [bad, why] of [
      [{ ...good, kind: "tinyassets-routes" }, "is not a rollout record"],
      [{ ...good, commit: "c0ffee" }, "is out of shape"],
      [{ ...good, workers: [{ ...one, counts: { a: -1 } }] }, "holds a Worker out of shape"],
      [{ ...good, workers: [{ ...one, secrets: ["K", "K"] }] }, "holds a Worker out of shape"],
      [{ ...good, workers: [{ ...one, forward: 0.5 }] }, "holds a Worker out of shape"],
      [{ ...good, workers: [{ ...one, forward: undefined }] }, "holds a Worker out of shape"],
      [{ ...good, workers: [{ ...one, before: { version: 1 } }] }, "holds a Worker out of shape"],
      [{ ...good, workers: [one, one] }, "holds a Worker twice"],
    ] as const) {
      expect(() => rolloutOf(bad, "k"), why).toThrow(`the record k ${why}`);
    }
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

  it("runs H2's probes with the environment once it holds their keys, and in the plan job " +
     "runs nothing", async () => {
    const keys = { [H2_INPUTS.a]: "probe-key", [H2_INPUTS.b]: "probe-key-b" };
    const w = world();
    await runMode(w, "h2", ["--yes"], { ...ENV, ...keys });
    expect(w.events).toEqual([
      "exec h2-probes.ts env has TA_PROBE_KEY,TA_PROBE_KEY_B,E2B_API_KEY"]);
    const keyless = world();
    await expect(runMode(keyless, "h2", ["--yes"], { ...ENV, [H2_INPUTS.a]: "probe-key" }))
      .rejects.toThrow(`${H2_INPUTS.b} is not set (the production environment's secrets)`);
    expect(keyless.events).toEqual([]);
    const plan = world();
    await runMode(plan, "h2", ["--public"], PUBLIC);
    expect([plan.events, plan.logs]).toEqual([[], []]);
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

  it("stops on a record the doors would refuse at every sign-in", async () => {
    await expect(upstreamFrom("https://auth.test", "c",
                              discovery({ ...good, jwks_uri: "https://auth.test/j?v=1" })))
      .rejects.toThrow("UPSTREAM's jwks_uri is an https URL");
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
    const { state, code_challenge: challenge, ...rest } = Object.fromEntries(asked.searchParams);
    expect(rest).toEqual({ provider: "authkit", response_type: "code", client_id: "c",
                           redirect_uri: UPSTREAM_CALLBACK, code_challenge_method: "S256",
                           max_age: "0" });
    expect(state).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
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

  it("leaves an unlisted callback to the apply job while it cannot be listed, and " +
     "stops on every other refusal", async () => {
    const answering = (location: string) => async () =>
      new Response(null, { status: 302, headers: { Location: location } });
    expect(await authorizeCheck(UPSTREAM, true, answering(
      "https://error.workos.com/redirect-uri-invalid?redirect_uri=x")))
      .toMatch(/: checked by the apply job before any change$/);
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
      return init?.method === "POST" ? Response.json({ uri: UPSTREAM_CALLBACK }, { status: 201 })
        : Response.json({ data: uris.map((uri) => ({ uri })) });
    };
    expect(await registerRedirect("k", true, workos([UPSTREAM_CALLBACK, "https://other.test/cb"])))
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
    const both = secretPlan({ doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET",
                                      "OPERATOR_KEYRING"],
                              owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                              sandbox: ["E2B_API_KEY"], files: ["FILES_KEYRING"] },
                            new Set(), NONE);
    // The doors' optional secrets: the shared app's and the canary clock's.
    expect(both).toEqual(OPTIONAL.map((name) => ({ worker: "doors", name })));
    expect(() => secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(), NONE))
      .toThrow("only one of the doors and the owner holds ROUTING_MAC_KEY: pass --rotate " +
               "ROUTING_MAC_KEY to set both again");
    expect(secretPlan({ doors: ["ROUTING_MAC_KEY"], owner: [] }, new Set(["ROUTING_MAC_KEY"]),
                      NONE).filter(({ name }) => name === "ROUTING_MAC_KEY")).toHaveLength(2);
  });

  it("never replaces a held keyring, and rotates one only by adding a key", () => {
    const held = { doors: [...SECRETS["doors"]!], owner: ["ROUTING_MAC_KEY"],
                   gateway: ["GATEWAY_KEYRING"], sandbox: ["E2B_API_KEY"],
                   files: ["FILES_KEYRING"] };
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
    // The files Worker's data keys' ring, by the same rule.
    expect(() => secretPlan(held, new Set(["FILES_KEYRING"]), values(RING)))
      .toThrow(/FILES_KEYRING holds/);
    expect(secretPlan(held, new Set(["FILES_KEYRING"]), values(ROTATED)))
      .toEqual([{ worker: "files", name: "FILES_KEYRING" }]);
  });

  it("refuses to deploy unless each migration lock pins every migration it ships, in order, " +
     "by the locks' own command line, and counts what each pins", async () => {
    // The real command line's answer, whatever the checked-in locks pin: the kit's own.
    const run = parse(["--mode", "dark"], ENV, { ...world().io, spawn: spawnSync });
    for (const worker of migrated()) {
      const locks = await locksOf(worker);
      const refused = deployProblems(worker, locks, Object.fromEntries(Object.keys(locks)
        .map((path) => [path, readLock(path)])));
      if (refused.length === 0) {
        expect(lockCounts(worker, run), worker).toEqual(Object.fromEntries(Object.keys(locks)
          .map((path) => [path, readLock(path).deployed.length])));
      } else {
        expect(() => lockCounts(worker, run), worker).toThrow(
          `${worker}'s migration locks refuse the deploy (exit 1): ${refused.join("\n")}`);
      }
    }
    // A Worker that states no lock is refused, never read as nothing to check.
    expect(() => lockCounts("doors", run))
      .toThrow(/^doors's migration locks refuse the deploy \(exit 1\)/);
  });

  it("lists the account's Workers, and deletes one unforced, never reading a failure as " +
     "done", async () => {
    const listing = "/accounts/a/workers/scripts";
    const script = "/accounts/a/workers/scripts/tinyassets-old";
    expect(await client(api({ [listing]: ok([{ id: "tinyassets-old" }, { id: "x" }]) }))
      .workers("a")).toEqual(["tinyassets-old", "x"]);
    const deleted = api({ [script]: ok(null) });
    await client(deleted).deleteWorker("a", "tinyassets-old");
    expect(deleted.calls).toEqual([`DELETE ${script}`]);
    for (const answer of [
      { status: 500, body: {} }, { status: 403, body: { success: false, errors: [{ code: 10000 }] } },
      { status: 200, body: { success: false } },
    ] as CfAnswer[]) {
      await expect(client(api({ [listing]: answer })).workers("a"))
        .rejects.toThrow(/the Workers listing failed/);
      await expect(client(api({ [script]: answer })).deleteWorker("a", "tinyassets-old"))
        .rejects.toThrow(/deleting the Worker tinyassets-old failed/);
    }
    await expect(client(api({ [listing]: ok({}) })).workers("a"))
      .rejects.toThrow(/the Workers listing failed/);
  });

  it("never reads a failed secret listing as no secrets, a missing Worker's neither", async () => {
    const path = `/accounts/a/workers/scripts/${DOORS}/secrets`;
    const cf = (answers: Record<string, CfAnswer | Error>) => client(api(answers));
    expect(await cf({ [path]: ok([{ name: "WORKOS_API_KEY", type: "secret_text" }]) })
      .secrets("a", DOORS)).toEqual(["WORKOS_API_KEY"]);
    for (const answer of [
      { status: 500, body: {} }, { status: 403, body: { success: false, errors: [{ code: 10000 }] } },
      { status: 404, body: { success: false, errors: [{ code: 10007 }] } },
      { status: 404, body: { success: false, errors: [{ code: 7003 }] } },
      { status: 200, body: { success: false } }, { status: 200, body: { success: true } },
    ] as CfAnswer[]) {
      await expect(cf({ [path]: answer }).secrets("a", DOORS))
        .rejects.toThrow(/failed/);
    }
    await expect(cf({ [path]: new Error("network down") }).secrets("a", DOORS))
      .rejects.toThrow(/network down/);
  });

  it("calls the control record absent only when the API answers it is", async () => {
    const path = "/accounts/a/storage/kv/namespaces/n/values/c%3Acontrol";
    const cf = (answer: CfAnswer) => client(api({ [path]: answer }));
    expect(await cf({ status: 200, body: { ordinary: "none" } } as CfAnswer)
      .value("a", "n", CONTROL_KEY)).toEqual({ ordinary: "none" });
    expect(await cf({ status: 404, body: { success: false, errors: [{ code: 10009 }] } })
      .value("a", "n", CONTROL_KEY)).toBeUndefined();
    for (const answer of [{ status: 500, body: {} }, { status: 404, body: {} },
                          { status: 401, body: { errors: [{ code: 10000 }] } }] as CfAnswer[]) {
      await expect(cf(answer).value("a", "n", CONTROL_KEY)).rejects.toThrow(/failed/);
    }
    // A stored value that is not JSON is no record: it throws, never reads as `{}`.
    const text = new Cloudflare("cf-token", async () => new Response("not json"));
    await expect(text.value("a", "n", CONTROL_KEY)).rejects.toThrow(
      "Cloudflare answered GET /accounts/a/storage/kv/namespaces/n/values/c%3Acontrol with a " +
      "body that is not JSON (200)");
  });

  it("reads every listing past its first 100, by page or by cursor, and makes by REST",
     async () => {
    const rows = (n: number, from = 0) => Array.from({ length: n }, (_, i) => i + from);
    // By page number while page * per_page is short of total_count: KV namespaces, queues.
    const paged = (path: string, row: (i: number) => Json) => {
      const total = 150;
      return Object.fromEntries([1, 2].map((page) => [
        `${path}${page === 1 ? "" : `${path.includes("?") ? "&" : "?"}page=${page}`}`,
        { status: 200, body: { success: true, result: rows(page === 1 ? 100 : 50, (page - 1) * 100)
          .map(row), result_info: { page, per_page: 100, count: page === 1 ? 100 : 50,
                                    total_count: total } } }]));
    };
    const kv = paged("/accounts/a/storage/kv/namespaces?per_page=100",
                     (i) => ({ id: `i${i}`, title: `t${i}` }));
    const namespaces = await client(api(kv)).namespaces("a");
    expect([namespaces.size, namespaces.get("t149")]).toEqual([150, "i149"]);
    const queues = await client(api(paged("/accounts/a/queues",
                                          (i) => ({ queue_id: `q${i}`, queue_name: `n${i}` }))))
      .queues("a");
    expect([queues.size, queues.get("n120")]).toEqual([150, "q120"]);
    // By cursor while it answers one: R2 buckets (`{ buckets }`), a namespace's keys.
    const cursored = (path: string, page: (rows: Json[]) => unknown, row: (i: number) => Json) => ({
      [path]: { status: 200, body: { success: true, result: page(rows(100).map(row)),
                                     result_info: { cursor: "c/2", per_page: 100 } } },
      [`${path}${path.includes("?") ? "&" : "?"}cursor=c%2F2`]: {
        status: 200, body: { success: true, result: page(rows(30, 100).map(row)),
                             result_info: { cursor: "", per_page: 100 } } } });
    expect((await client(api(cursored("/accounts/a/r2/buckets", (buckets) => ({ buckets }),
                                      (i) => ({ name: `b${i}` })))).buckets("a")).length)
      .toBe(130);
    expect((await client(api(cursored("/accounts/a/storage/kv/namespaces/n/keys?prefix=o%3A",
                                      (keys) => keys, (i) => ({ name: `o:${i}` }))))
      .keys("a", "n", "o:")).at(-1)).toBe("o:129");
    // A failed later page fails the listing: never its first page read as the whole.
    const first = Object.entries(kv)[0]!;
    await expect(client(api(Object.fromEntries([first]))).namespaces("a"))
      .rejects.toThrow("listing the KV namespaces failed (500, [10000])");
    // Each make is one POST, its answer checked; a namespace's id is its answer's.
    const made = api({ "/accounts/a/storage/kv/namespaces": ok({ id: "k1" }),
                       "/accounts/a/r2/buckets": ok({ name: "b" }),
                       "/accounts/a/queues": ok({ queue_id: "q" }) });
    expect(await client(made).makeNamespace("a", "t")).toBe("k1");
    await client(made).makeBucket("a", "b");
    await client(made).makeQueue("a", "q");
    expect(made.calls).toEqual(["POST /accounts/a/storage/kv/namespaces",
                                "POST /accounts/a/r2/buckets", "POST /accounts/a/queues"]);
    await expect(client(api({})).makeBucket("a", "b"))
      .rejects.toThrow("making the R2 bucket b failed (500, [10000])");
    // Wrangler's runner throws on any non-zero exit, with its stderr alone.
    const w = world({ failing: "versions upload" });
    const run = wranglerFor(parse(["--mode", "dark"], ENV, w.io), ACCOUNT);
    expect(() => run(["versions", "upload"]))
      .toThrow("wrangler versions upload failed (exit 1): the API refused it");
  });

  it("hands each route from the old proxy to the doors one at a time, no path unserved",
     async () => {
    const fake = zoneApi(LIVE);
    const cloudflare = client(fake);
    const { id: zone, account } = await cloudflare.zone(ZONE);
    expect(account).toBe("a".repeat(32));
    const steps = handover(await cloudflare.routes(zone), DOORS, ROUTES);
    // Identical patterns move in place, and a narrower old one is deleted after the doors'
    // wider one exists: the old proxy is left with no route.
    expect(steps.filter((each) => each.method === "PUT").map((each) => each.pattern))
      .toEqual([`${HOST}/mcp*`, `${HOST}/api*`, `${HOST}/app*`]);
    expect(steps.filter((each) => each.method === "DELETE").map((each) => each.pattern))
      .toEqual([`${HOST}/.well-known/oauth-protected-resource*`,
                `${HOST}/.well-known/oauth-authorization-server*`]);
    const added = steps.findIndex((each) => each.pattern === `${HOST}/.well-known/oauth-*`);
    expect(steps.findIndex((each) => each.method === "DELETE")).toBeGreaterThan(added);
    await cloudflare.reroute(zone, steps, (each) => describeStep(each, DOORS));
    for (const path of PROBES.slice(0, -1)) {
      expect([path, servedBy(fake.table.values(), path)]).toEqual([path, DOORS]);
    }
    expect([...fake.table.values()].filter((route) => route.script === "tinyassets-mcp-proxy"))
      .toEqual([]);
    expect(servedBy(fake.table.values(), `${HOST}/site/x`)).toBe("site");
    // Run again, it changes nothing.
    expect(handover([...fake.table.values()], DOORS, ROUTES)).toEqual([]);
  });

  it("starts the doors' own first-party sign-in, a fresh S256 challenge each run", () => {
    const link = new URL(firstPartyLink());
    expect(`${link.origin}${link.pathname}`).toBe(`${ORIGIN}${identity.signin.authorize}`);
    const { code_challenge: challenge, state, ...rest } = Object.fromEntries(link.searchParams);
    expect(rest).toEqual({ response_type: "code", client_id: WEB_CLIENT,
                           redirect_uri: WEB_REDIRECT, code_challenge_method: "S256",
                           resource: APP_RESOURCE });
    expect(challenge).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(state).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(new URL(firstPartyLink()).searchParams.get("code_challenge")).not.toBe(challenge);
  });

  it("stops the handover at a refused change, saying what is done", async () => {
    const fake = zoneApi(LIVE, "PUT /zones/z/workers/routes/r1");
    const cloudflare = client(fake);
    const steps = handover(await cloudflare.routes("z"), DOORS, ROUTES);
    await expect(cloudflare.reroute("z", steps, (each) => describeStep(each, DOORS)))
      .rejects.toThrow(`move the route ${HOST}/mcp* from tinyassets-mcp-proxy to ` +
                       `${DOORS} failed (500, [10000]); the steps before it are done, the rest ` +
                       "are not: run it again for the rest");
    expect(fake.calls.filter((call) => call.startsWith("DELETE"))).toEqual([]);
    await expect(client(api({})).zone(ZONE)).rejects.toThrow(
      "reading the zone tinyassets.io failed (500, [10000])");
    // The account is the zone's own: one zone, with its account, or the kit stops.
    const zones = (result: unknown) => client(api({ "/zones?name=tinyassets.io": ok(result) }));
    await expect(zones([{ id: "z" }]).zone(ZONE)).rejects.toThrow(/could not be read/);
    await expect(zones([{ id: "z", account: { id: "a".repeat(32) } },
                        { id: "y", account: { id: "b".repeat(32) } }]).zone(ZONE))
      .rejects.toThrow(/2 found/);
    await expect(zones([{ id: "z", account: { id: "../x" } }]).zone(ZONE))
      .rejects.toThrow(/could not be read/);
    await expect(client(api({})).routes("z")).rejects.toThrow(
      "reading the zone's routes failed (500, [10000])");
    // A pull whose answer holds no pull is a failed read, never an empty dead-letter queue.
    await expect(client(api({ "/accounts/a/queues/q/messages/pull": ok(null) }))
      .pull("a", "q", 100, 60_000)).rejects.toThrow("its answer holds no pull");
  });

  it("sends the account's token on every Cloudflare call, and refuses a body that is not " +
     "JSON", async () => {
    const sent: { url: string; headers: unknown }[] = [];
    const cloudflare = new Cloudflare("cf-token", async (url, init) => {
      sent.push({ url, headers: init?.headers });
      return new Response("not json", { status: 502 });
    });
    await expect(cloudflare.makeQueue("a", "q")).rejects.toThrow(
      "Cloudflare answered POST /accounts/a/queues with a body that is not JSON (502)");
    expect(sent).toEqual([{ url: "https://api.cloudflare.com/client/v4/accounts/a/queues",
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
    const out = mkdtempSync(join(tmpdir(), "keyring-"));
    try {
      keyringNew(join(out, "ring.json"), NONE);
      const theirs = keyring(readFileSync(join(out, "ring.json"), "utf8"), "the keyring");
      expect([theirs.active, [...theirs.keys.keys()]])
        .toEqual([ring.active, [...ring.keys.keys()]]);
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("makes a first keyring with keyring-new, run as the release runs it", () => {
    // Node's type stripping, so the envelope and the E2B client it imports run there too.
    const out = mkdtempSync(join(tmpdir(), "keyring-"));
    try {
      execFileSync(process.execPath, ["--experimental-strip-types",
                                      join(PLATFORM, "deploy", "deploy.ts"), "keyring-new",
                                      join(out, "ring.json")], { stdio: "pipe" });
      const ring = keyring(readFileSync(join(out, "ring.json"), "utf8"), "the keyring");
      expect([ring.active, [...ring.keys.keys()]]).toEqual(["k1", ["k1"]]);
      expect(ring.keys.get("k1")!.byteLength).toBe(32);
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

  it("retires a keyring's kids but its active one beside the old file, which it keeps, and " +
     "refuses a ring of one key", () => {
    const out = mkdtempSync(join(tmpdir(), "keyring-"));
    const logged: string[] = [];
    try {
      const file = join(out, "ring.json");
      const old = ringSecret("k3", ["k1", "k2", "k3"]);
      writeFileSync(file, old);
      keyringRetire(file, (line) => logged.push(line));
      const kept = keyring(readFileSync(`${file}.retire`, "utf8"), "the keyring");
      const before = keyring(old, "the keyring");
      expect([kept.active, [...kept.keys.keys()]]).toEqual(["k3", ["k3"]]);
      expect(kept.keys.get("k3")).toEqual(before.keys.get("k3"));
      expect(readFileSync(file, "utf8")).toBe(old);
      expect(logged).toEqual([`wrote ${file}.retire, k3 alone (k1, k2 retired): keep ${file} ` +
        "offline, the one way back for them, store the new file as the keyring it was, then " +
        "run mode retire"]);
      // Never over a retired ring written before.
      expect(() => keyringRetire(file, NONE)).toThrow(/EEXIST/);
      expect(() => keyringRetire(`${file}.retire`, NONE)).toThrow(
        `the keyring in ${file}.retire holds its active key k3 alone: nothing to retire`);
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
    // The key on every call, and JSON named for a write's body.
    const json = method === "GET" ? {} : { "Content-Type": "application/json" };
    expect(init?.headers).toEqual({ "X-API-Key": E2B_KEY, ...json });
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
  return { hooks, calls, e2b: new E2bAdmin(E2B_KEY, fetcher) };
}

describe("E2B's lifecycle webhook", () => {
  const EVENTS = ["sandbox.lifecycle.paused", "sandbox.lifecycle.killed"];

  it("is registered disabled in dark, and enabled only with the routes", async () => {
    // dark, none held: registered disabled, with the stored secret, for the two events. A
    // hook of another name, at whatever URL, is not ours.
    const dark = e2bHooks([{ id: "x", name: "another", url: E2B_HOOK.url, enabled: true }]);
    expect(await lifecycleHook(dark.e2b,
        { secret: "s1", setSecret: false, enable: false }, true)).toBe("registered, disabled");
    expect(dark.hooks[1]).toEqual({ id: "w1", name: E2B_HOOK.name, url: E2B_HOOK.url,
                                    events: EVENTS, enabled: false, signatureSecret: "s1" });
    // dark again, the doors' secret not set this run: nothing sent; the other hook untouched.
    const calls = dark.calls.length;
    expect(await lifecycleHook(dark.e2b, { secret: "s2", setSecret: false, enable: false }, true))
      .toBe("present, unchanged, disabled");
    expect(dark.calls.slice(calls)).toEqual(["GET /events/webhooks"]);
    expect(dark.hooks[0]).toEqual({ id: "x", name: "another", url: E2B_HOOK.url, enabled: true });
    // routes: enabled; a routes run later changes nothing; dark after it never turns it off.
    expect(await lifecycleHook(dark.e2b, { secret: "s2", setSecret: false, enable: true }, true))
      .toBe("present, enabled set, enabled");
    expect(dark.calls.at(-1)).toBe('PATCH /events/webhooks/w1 {"enabled":true}');
    expect(await lifecycleHook(dark.e2b, { secret: "s2", setSecret: false, enable: true }, true))
      .toBe("present, unchanged, enabled");
    await lifecycleHook(dark.e2b, { secret: "s2", setSecret: false, enable: false }, true);
    expect(dark.hooks[1]!["enabled"]).toBe(true);
    // A held hook whose events drifted is given its two events back.
    const drifted = e2bHooks([{ id: "a", name: E2B_HOOK.name, url: E2B_HOOK.url, enabled: false,
                                events: ["sandbox.lifecycle.paused"] }]);
    await lifecycleHook(drifted.e2b, { secret: "s", setSecret: false, enable: false }, true);
    expect(drifted.hooks[0]!["events"]).toEqual(EVENTS);
    // A held hook the identity's host has moved off is moved to the identity's URL, the one
    // hook, never a second beside it; the plan says so before the apply.
    const before = `https://stage.${ZONE}${identity.hook}`;
    const moved = e2bHooks([{ id: "a", name: E2B_HOOK.name, url: before, enabled: false,
                              events: EVENTS }]);
    expect(await lifecycleHook(moved.e2b, { secret: "s", setSecret: false, enable: false },
                               false)).toBe(`present, its URL (${before} before) set, disabled`);
    expect(moved.calls).toEqual(["GET /events/webhooks"]);
    expect(await lifecycleHook(moved.e2b, { secret: "s", setSecret: false, enable: true }, true))
      .toBe(`present, its URL (${before} before), enabled set, enabled`);
    expect(moved.calls.at(-1)).toBe(
      `PATCH /events/webhooks/a ${JSON.stringify({ url: E2B_HOOK.url, enabled: true })}`);
    expect(moved.hooks).toEqual([{ id: "a", name: E2B_HOOK.name, url: E2B_HOOK.url,
                                   enabled: true, events: EVENTS }]);
    // routes with none held: registered enabled.
    expect(await lifecycleHook(e2bHooks([]).e2b,
        { secret: "s", setSecret: false, enable: true }, true))
      .toBe("registered, enabled");
    // Without apply, only the listing is read: the kit's check before any change.
    const planned = e2bHooks([]);
    expect(await lifecycleHook(planned.e2b, { secret: "s", setSecret: true, enable: true }, false))
      .toBe("missing: registered, enabled");
    expect(planned.calls).toEqual(["GET /events/webhooks"]);
    // A failed listing or write stops the kit; two hooks of the name are refused.
    await expect(lifecycleHook(e2bHooks([], "GET").e2b,
        { secret: "s", setSecret: false, enable: false }, false))
      .rejects.toThrow("E2B GET /events/webhooks answered 500");
    await expect(lifecycleHook(e2bHooks([], "POST").e2b,
        { secret: "s", setSecret: false, enable: false }, true))
      .rejects.toThrow("E2B POST /events/webhooks answered 500");
    await expect(lifecycleHook(e2bHooks([{ id: "a", name: E2B_HOOK.name, url: E2B_HOOK.url }],
                                        "PATCH").e2b,
        { secret: "s", setSecret: true, enable: false }, true))
      .rejects.toThrow("E2B PATCH /events/webhooks/a answered 500");
    await expect(heldHook(e2bHooks([{ id: "a", name: E2B_HOOK.name, url: E2B_HOOK.url },
                                    { id: "b", name: E2B_HOOK.name, url: "https://x.test/" }])
      .e2b)).rejects.toThrow(`E2B holds 2 webhooks named ${E2B_HOOK.name}: keep one`);
    // A route hands the hook's path to the doors.
    expect(ROUTES.some((pattern) => routeTakes(pattern, `${HOST}${identity.hook}`))).toBe(true);
  });

  it("changes its secret only with the doors' copy: both or neither", async () => {
    // The doors hold E2B_WEBHOOK_SECRET and production's value was replaced: without
    // --rotate the doors keep theirs, so E2B keeps its own too.
    const held = { doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY", "E2B_WEBHOOK_SECRET"],
                   owner: ["ROUTING_MAC_KEY"], gateway: ["GATEWAY_KEYRING"],
                   sandbox: ["E2B_API_KEY"], files: ["FILES_KEYRING"] };
    const setting = (plan: { worker: string; name: string }[]) =>
      plan.map((each) => ({ ...each, sets: true }));
    const kept = setting(secretPlan(held, new Set(), null));
    expect(kept.some((each) => each.name === "E2B_WEBHOOK_SECRET")).toBe(false);
    const neither = e2bHooks([{ id: "a", name: E2B_HOOK.name, url: E2B_HOOK.url, enabled: true,
                                events: EVENTS,
                                signatureSecret: "old" }]);
    await lifecycleHook(neither.e2b,
        { secret: "new", setSecret: hookSetsSecret(kept), enable: true }, true);
    expect(neither.hooks[0]!["signatureSecret"]).toBe("old");
    // With --rotate E2B_WEBHOOK_SECRET the doors take the new value, and E2B does too.
    const rotated = setting(secretPlan(held, new Set(["E2B_WEBHOOK_SECRET"]), null));
    expect(rotated).toContainEqual({ worker: "doors", name: "E2B_WEBHOOK_SECRET", sets: true });
    const both = e2bHooks([{ id: "a", name: E2B_HOOK.name, url: E2B_HOOK.url, enabled: true,
                             events: EVENTS,
                             signatureSecret: "old" }]);
    await lifecycleHook(both.e2b,
        { secret: "new", setSecret: hookSetsSecret(rotated), enable: true }, true);
    expect(both.hooks[0]!["signatureSecret"]).toBe("new");
    // The doors missing it (a first deploy) sets both as well.
    const first = setting(secretPlan({ ...held, doors: ["ROUTING_MAC_KEY", "WORKOS_API_KEY"] },
                                     new Set(), null));
    expect(hookSetsSecret(first)).toBe(true);
    // An optional secret the run is not given is set on neither.
    expect(hookSetsSecret(first.map((each) => ({ ...each, sets: false })))).toBe(false);
  });

  it("starts the pair equal: a dark run that makes the hook while the doors hold a secret " +
     "goes on only setting theirs too", async () => {
    // The doors hold an old E2B_WEBHOOK_SECRET, E2B holds no hook: the plan job cannot list
    // the hook, so the apply job stops before any secret or Worker, asking for --rotate.
    const w = world({ held: true });
    w.hooks.length = 0;
    await expect(runMode(w, "dark", ["--yes"])).rejects.toThrow(
      `E2B holds no lifecycle webhook, and ${DOORS} holds E2B_WEBHOOK_SECRET, which the webhook ` +
      "this run registers must match: dispatch again with --rotate E2B_WEBHOOK_SECRET to set " +
      "both; no secret is set and no Worker deployed");
    expect(w.events.filter((event) => CHANGE.test(event))).toEqual([]);
    // With it, the run sets the doors' copy from production, and registers the hook with the
    // same value.
    await runMode(w, "dark", ["--yes", "--rotate", E2B_HOOK.secret]);
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
});

describe("the Actions job", () => {
  it("masks a value in an Actions log, and prints nothing outside one", () => {
    expect(maskLine({ GITHUB_ACTIONS: "true" }, "a".repeat(32)))
      .toBe(`::add-mask::${"a".repeat(32)}`);
    for (const env of [{}, { GITHUB_ACTIONS: "" }]) {
      expect(maskLine(env, "b".repeat(32))).toBeNull();
    }
  });

  it("adds to the job's summary as it is, and writes nowhere outside Actions", () => {
    const out = mkdtempSync(join(tmpdir(), "ta-summary-"));
    try {
      const file = join(out, "summary.md");
      summarize({ GITHUB_STEP_SUMMARY: file }, "one\n");
      summarize({ GITHUB_STEP_SUMMARY: file }, "two");
      expect(readFileSync(file, "utf8")).toBe("one\ntwo");
      summarize({}, "three");
      summarize({ GITHUB_STEP_SUMMARY: "" }, "three");
      expect([readdirSync(out), readFileSync(file, "utf8")]).toEqual([["summary.md"], "one\ntwo"]);
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("names a run by the workflow run's id and attempt, and one by hand by its time", () => {
    expect(runOf({ GITHUB_RUN_ID: "7001", GITHUB_RUN_ATTEMPT: "2" }, NOW))
      .toEqual({ id: "7001", attempt: "2" });
    expect(runOf({ GITHUB_RUN_ID: "" }, NOW)).toEqual({ id: "local-20261107T120000Z", attempt: null });
  });

  it("links the run's page, and none by hand", () => {
    expect(runLink({ GITHUB_SERVER_URL: "https://github.com", GITHUB_REPOSITORY: "o/r",
                     GITHUB_RUN_ID: "7001" })).toBe("https://github.com/o/r/actions/runs/7001");
    expect(runLink({ GITHUB_SERVER_URL: "https://github.com", GITHUB_REPOSITORY: "o/r" }))
      .toBeNull();
  });

  it("annotates the run, escaped as the runner reads it, and prints it plainly by hand", () => {
    expect(annotate({ GITHUB_ACTIONS: "true" }, "error", "A: b, c", "50% done\nthen not"))
      .toBe("::error title=A%3A b%2C c::50%25 done%0Athen not");
    expect(annotate({}, "notice", "Copy", "not configured")).toBe("Copy: not configured");
  });

  it("writes a step's output for a later job, and nowhere outside Actions", () => {
    const out = mkdtempSync(join(tmpdir(), "ta-output-"));
    try {
      const file = join(out, "output");
      output({ GITHUB_OUTPUT: file }, "configured", "false");
      output({}, "configured", "true");
      expect([readdirSync(out), readFileSync(file, "utf8")]).toEqual([["output"],
                                                                      "configured=false\n"]);
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });
});

describe("the template and the pair", () => {
  it("checks the sandbox's E2B template has a ready build, and stops on a failed lookup",
     async () => {
    const headers: unknown[] = [];
    const e2b = (alias: number, exists: unknown, tag = 200) => new E2bAdmin(E2B_KEY,
      async (url, init) => {
        headers.push(init?.headers);
        return url.includes("/aliases/") ? Response.json({ templateID: "t1" }, { status: alias })
          : Response.json({ exists }, { status: tag });
      });
    const template = "plan-fixture:v1";
    expect(await e2b(200, true).ready(template)).toBe(true);
    expect(await e2b(200, false).ready(template)).toBe(false);
    expect(await e2b(404, true).ready(template)).toBe(false);
    await expect(e2b(500, true).ready(template))
      .rejects.toThrow("E2B GET /templates/aliases/plan-fixture answered 500");
    await expect(e2b(200, true, 503).ready(template))
      .rejects.toThrow("E2B GET /templates/t1/tags/exists answered 503");
    await expect(e2b(200, true).ready("plan-fixture")).rejects.toThrow(/name:tag/);
    // Both lookups send the key.
    expect(headers.length).toBeGreaterThan(0);
    for (const each of headers) {
      expect(each).toEqual({ "X-API-Key": E2B_KEY });
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
    // This tree's image, as image.ts prints it, under the pinned template's E2B name: the
    // kit's pin check reads it so.
    expect(treeImage()).toMatch(new RegExp(`^${TEMPLATE.split(":")[0]}:s[0-9a-f]{16}$`));
  });

  it("plans a tree whose image is not the pin, and refuses to apply it", async () => {
    const image = "plan-fixture:s0123456789abcdef";
    const w = world({ image });
    const work = await runMode(w, "plan");
    expect(work.plan).toContain(`the pinned template ${TEMPLATE} is not this tree's image ` +
      `${image}: build it (sandbox-template.yml, build) and pin ` +
      "its passed pair");
    await expect(runMode(world({ image }), "dark", ["--yes"]))
      .rejects.toThrow(`the pinned template ${TEMPLATE} is not this tree's image ` +
        `${image}: build it (sandbox-template.yml, build) and pin ` +
        "its passed pair; nothing is deployed");
  });

  it("versions a later deploy by the id wrangler prints", () => {
    expect(versionOf(`Uploaded ${NAMES["owner"]}\nWorker Version ID: ` +
                     "0f7e3a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b\n"))
      .toBe("0f7e3a3c-1b2d-4e5f-8a9b-0c1d2e3f4a5b");
    expect(() => versionOf(`Uploaded ${NAMES["owner"]}`)).toThrow(/Version ID/);
  });
});

describe("the operator path's modes", () => {
  /** The control record as KV holds it, parsed; and what the run kept, by its key. */
  const live = (w: ReturnType<typeof world>, key = CONTROL_KEY): unknown => {
    const held = w.kv.get(`${CONTROL_NS}/${key}`);
    return held === undefined ? undefined : JSON.parse(held);
  };
  const KEPT = `deploy:control:${runOf(ENV, NOW).id}`;
  const writes = (w: ReturnType<typeof world>) =>
    w.events.filter((event) => event.startsWith("cf PUT"));
  const EDITS = "outside=on ordinary=all canary+=user_bob tiers.free.runs=6";
  const OPENED = { ...DARK, outside: "on", ordinary: "all", canary: ["user_bob"],
                   tiers: { ...DARK.tiers, free: { ...DARK.tiers.free, runs: 6 } } };

  it("control plans each field's difference from the live record, and with no --yes writes " +
     "nothing", async () => {
    for (const reach of [["--public"], []]) {
      const w = world({ held: true });
      const work = await runMode(w, "control", [...reach, "--set", EDITS],
                                 reach.length > 0 ? PUBLIC : ENV);
      expect(work.plan).toEqual([
        'set the control record\'s outside: "off" → "on"',
        'set the control record\'s ordinary: "canary" → "all"',
        'set the control record\'s canary: [] → ["user_bob"]',
        "set the control record's tiers.free.runs: 5 → 6",
        `keep the previous record's bytes in ${NAMESPACES["CONTROL"]} under this run's id, to ` +
        "put back with --replace"]);
      expect(writes(w)).toEqual([]);
      expect(live(w)).toEqual(DARK);
    }
  });

  it("control keeps the previous record, writes the edited one, and reads it back: the " +
     "cutover's gates opened", async () => {
    const w = world({ held: true });
    const work = await runMode(w, "control", ["--yes", "--set", EDITS]);
    expect(work.plan.at(-1)).toBe("read the control record back: it is the one written");
    expect(live(w, KEPT)).toEqual(DARK);
    expect(live(w)).toEqual(OPENED);
    const read = { kind: "read", control: parseControl(live(w))! } as const;
    expect([outsideOpen(read, "user_alice"), ownerOpen(read, "user_alice")]).toEqual([true, true]);
    // The previous kept before the write, and the write read back after it.
    const at = order(w.events, [`cf PUT ${RECORD_AT.replace(/[^/]+$/, encodeURIComponent(KEPT))}`,
                                `cf PUT ${RECORD_AT.replace(/[^/]+$/, encodeURIComponent(CONTROL_KEY))}`,
                                `cf GET ${RECORD_AT.replace(/[^/]+$/, encodeURIComponent(CONTROL_KEY))}`]);
    expect(at).toHaveLength(3);
    // Again in the same run (a re-run attempt): no difference, nothing written, the kept as it was.
    const again = await runMode(w, "control", ["--yes", "--set", EDITS]);
    expect(again.plan).toEqual([HELD, "the control record stands as asked: no difference"]);
    await runMode(w, "control", ["--yes", "--set",
                                  "outside=off canary-=user_bob canary-=user_eve signins.claude=false"]);
    expect(live(w, KEPT)).toEqual(DARK);
    expect(live(w)).toEqual({ ...OPENED, outside: "off", canary: [], signins: { claude: false } });
  });

  it.each([
    ["a gate's value no reader takes", "outside=maybe"],
    ["a field no reader takes", "closed=true"],
    ["a list where a gate is", "inbound+=x"],
    ["a tier past its ceiling", "tiers.paid.runs=1001"],
  ])("control never writes %s", async (_what, edit) => {
    const w = world({ held: true });
    await expect(runMode(w, "control", ["--yes", "--set", edit]))
      .rejects.toThrow(/no reader takes \(controlOf\)|is no list to add to/);
    expect(writes(w)).toEqual([]);
    expect(live(w)).toEqual(DARK);
  });

  it("control shows a missing, malformed or non-JSON record as it is, and replaces it only " +
     "with --replace, its bytes kept as they were", async () => {
    for (const held of [undefined, '{"outside":"on"}', '{"outside": "on", ', "off\n"]) {
      const w = world({ held: true });
      if (held === undefined) {
        w.kv.delete(`${CONTROL_NS}/${CONTROL_KEY}`);
      } else {
        w.kv.set(`${CONTROL_NS}/${CONTROL_KEY}`, held);
      }
      await expect(runMode(w, "control", ["--yes", "--set", "outside=off"])).rejects.toThrow(
        `the control record is ${held === undefined ? "missing" : `one no reader takes: ${held}`}` +
        "; give the whole record with --replace; nothing is changed");
      expect(writes(w)).toEqual([]);
      const work = await runMode(w, "control", ["--yes", "--replace", JSON.stringify(DARK),
                                                "--set", "inbound=true"]);
      expect(live(w)).toEqual({ ...DARK, inbound: true });
      expect(w.kv.get(`${CONTROL_NS}/${KEPT}`)).toBe(held);
      expect(work.plan).toContain(held === undefined ? "keep no previous record: there is none"
        : `keep the previous record's bytes in ${NAMESPACES["CONTROL"]} under this run's id, ` +
          "to put back with --replace");
      expect(work.plan.at(-1)).toBe("read the control record back: it is the one written");
    }
  });

  it.each([
    ["__proto__=1"], ["__proto__.outside=on"], ["constructor.name=x"], ["tiers.__proto__.runs=1"],
    ["signins.__proto__=true"], ["canary.__proto__=1"],
  ])("control refuses %s, a key no reader keeps, and touches no prototype", async (edit) => {
    const w = world({ held: true });
    await expect(runMode(w, "control", ["--yes", "--set", edit]))
      .rejects.toThrow(/no reader takes \(controlOf\)|is inside no object/);
    expect(writes(w)).toEqual([]);
    expect(live(w)).toEqual(DARK);
    expect(Object.keys(Object.prototype)).toEqual([]);
    expect(({} as Record<string, unknown>)["outside"]).toBeUndefined();
  });

  it("control says when the record it writes admits no owner: the kill switch", async () => {
    const w = world({ held: true });
    await runMode(w, "control", ["--yes", "--set", "outside=on canary+=user_bob"]);
    const work = await runMode(w, "control", ["--yes", "--set", "outside=off ordinary=none"]);
    expect(work.plan).toContain("the record then admits no owner: closed to all");
    const control = parseControl(live(w))!;
    expect(closedToAll(control)).toBe(true);
    expect(outsideOpen({ kind: "read", control }, "user_bob")).toBe(false);
    // The dark record is closed to all, outside on or off; a canary owner opens it.
    const dark = world({ held: true });
    expect((await runMode(dark, "control", ["--set", "outside=on"])).plan)
      .toContain("the record then admits no owner: closed to all");
    expect((await runMode(dark, "control", ["--set", "outside=on canary+=user_bob"])).plan)
      .not.toContain("the record then admits no owner: closed to all");
  });

  it("control stops loudly when the record read back is not the one written", async () => {
    const w = world({ held: true });
    w.inert.add("kv");
    await expect(runMode(w, "control", ["--yes", "--set", "outside=on"])).rejects.toThrow(
      `the control record read back is not the one written: ${JSON.stringify(DARK)}`);
  });

  it("control holds the read-back to the bytes written, not to a record they parse alike",
     async () => {
    const w = world({ held: true });
    const at = `${CONTROL_NS}/${CONTROL_KEY}`;
    const fetch = w.io.fetch;
    // KV answers the record written, reformatted: the same JSON, other bytes.
    const io = { ...w.io, fetch: async (url: string, init?: RequestInit) => {
      const answer = await fetch(url, init);
      if (init?.method === "PUT" && url.endsWith(encodeURIComponent(CONTROL_KEY))) {
        w.kv.set(at, JSON.stringify(JSON.parse(w.kv.get(at)!), null, 1));
      }
      return answer;
    } };
    await expect(runMode({ ...w, io }, "control", ["--yes", "--set", "outside=on"]))
      .rejects.toThrow("the control record read back is not the one written: {\n");
  });

  it("control refuses an account no dark run made, and edits out of grammar", async () => {
    await expect(runMode(world(), "control", ["--yes", "--set", "outside=on"])).rejects.toThrow(
      `the control record is kept in ${NAMESPACES["CONTROL"]}, which a dark run makes`);
    const w = world({ held: true });
    for (const edit of ["outside", "=on", "tiers..free=1", "a+b=1"]) {
      expect(() => parse(["--mode", "control", "--set", edit], ENV, w.io), edit)
        .toThrow(`--set's edit ${edit} is not <path>=<value>`);
    }
    expect(() => parse(["--mode", "control", "--replace", "{"], ENV, w.io)).toThrow(
      "--replace is the whole control record, as JSON, that the edits start from in place of " +
      "the live one");
    expect(() => parse(["--mode", "dark", "--set", "outside=on"], ENV, w.io))
      .toThrow("mode dark takes no --set: it is mode control's");
  });

  it("owner plans its ask, and with --yes sends it MAC'd to the operator queue and says the " +
     "doors' answer between the owner's state before and after", async () => {
    const flags = ["--owner", "user_alice", "--act", "tier=paid"];
    const planned = world({ held: true });
    expect((await runMode(planned, "owner", ["--public", ...flags], PUBLIC)).plan).toEqual([
      'ask the doors owner.tier {"tier":"paid"} for user_alice, through the operator channel, ' +
      "between its state (owner.state) before and after"]);
    expect(planned.sent).toEqual([]);
    // The doors' owner as each op leaves it: the tier set between the two reads.
    let tier = "free";
    const w = world({ held: true, doors: ({ op, args }) => {
      if (op === "owner.tier") {
        tier = String(args["tier"]);
        return { result: { tier } };
      }
      return { result: { state: "admitted", tier } };
    } });
    const work = await runMode(w, "owner", ["--yes", ...flags]);
    expect(work.plan.slice(-3)).toEqual([
      'user_alice before owner.tier: {"state":"admitted","tier":"free"}',
      'the doors answered owner.tier for user_alice: {"tier":"paid"}',
      'user_alice after owner.tier: {"state":"admitted","tier":"paid"}']);
    expect(w.sent.map(({ message }) => [message.op, message.args])).toEqual([
      ["owner.state", {}], ["owner.tier", { tier: "paid" }], ["owner.state", {}]]);
    expect(w.sent[0]!.message.at).toBe(NOW.getTime() / 1000);
    const { queue, message } = w.sent[1]!;
    expect(queue).toBe(`q${QUEUES.indexOf(QUEUE_NAMES.OPERATOR) + 1}`);
    expect(message).toMatchObject({ op: "owner.tier", owner: "user_alice",
                                    args: { tier: "paid" } });
    expect(await macHolds(parseRing(RING)!, message)).toBe(true);
  });

  it.each([
    ["suspend=abuse", "owner.suspend", { reason: "abuse" }],
    ["lift=billing", "owner.lift", { reason: "billing" }],
    ["admit", "owner.admit", {}],
    ["state", "owner.state", {}],
    ["rewrap", "owner.rewrap", {}],
    ["reseal", "owner.reseal", {}],
  ])("owner sends --act %s as %s, between two owner.state", async (act, op, args) => {
    const w = world({ held: true });
    await runMode(w, "owner", ["--yes", "--owner", "user_alice", "--act", act]);
    expect(w.sent.map(({ message }) => [message.op, message.args])).toEqual([
      ["owner.state", {}], [op, args], ["owner.state", {}]]);
  });

  it("owner stops loudly on a refusal, and with no answer in its wait", async () => {
    const refusing = world({ held: true, doors: ({ op }) => op === "owner.state"
      ? { result: { state: "pending" } }
      : { refused: { code: "refused", message: "an owner the platform never admitted is not " +
                                                "suspended" } } });
    await expect(runMode(refusing, "owner", ["--yes", "--owner", "user_alice", "--act",
                                             "suspend=abuse"])).rejects.toThrow(
      "the doors refused owner.suspend for user_alice (refused): an owner the platform never " +
      "admitted is not suspended");
    // The state refused first, as an erased owner's is: the op is never sent.
    const erased = world({ held: true, doors: () => ({
      refused: { code: "owner_erased", message: "the owner is erased" } }) });
    await expect(runMode(erased, "owner", ["--yes", "--owner", "user_alice", "--act",
                                           "tier=paid"])).rejects.toThrow(
      "the doors refused owner.state for user_alice (owner_erased): the owner is erased");
    expect(erased.sent.map(({ message }) => message.op)).toEqual(["owner.state"]);
    const silent = world({ held: true, doors: () => null });
    await expect(runMode(silent, "owner", ["--yes", "--owner", "user_alice", "--act", "admit"]))
      .rejects.toThrow(/the doors did not answer the operator message [0-9a-f]{32}/);
  });

  it("owner needs one owner and one act, each in its grammar", () => {
    const w = world();
    const acts = "--act is one of state, tier=<tier>, suspend=<reason>, lift=<reason>, admit, " +
                 "rewrap, reseal";
    for (const act of ["tier", "admit=now", "state=all", "erase=x", "tier=paid=x"]) {
      const argv = ["--mode", "owner", "--owner", "user_alice", "--act", act];
      if (act === "tier=paid=x") {
        // The value is all after the first =: the owner's door refuses it.
        expect(parse(argv, ENV, w.io).act).toEqual({ op: "owner.tier", args: { tier: "paid=x" } });
        continue;
      }
      expect(() => parse(argv, ENV, w.io), act).toThrow(acts);
    }
    expect(() => parse(["--mode", "owner", "--act", "admit"], ENV, w.io))
      .toThrow("mode owner needs --owner: the owner principal the op is asked for");
    expect(() => parse(["--mode", "owner", "--owner", "user_alice"], ENV, w.io))
      .toThrow("mode owner needs --act: the op asked of the doors");
    expect(() => parse(["--mode", "owner", "--owner", " x", "--act", "admit"], ENV, w.io))
      .toThrow("--owner is the owner principal the op is asked for");
    expect(() => parse(["--mode", "control", "--owner", "user_alice"], ENV, w.io))
      .toThrow("mode control takes no --owner: it is mode owner, restore, copyback and erase's");
  });
});

/** A generation `restore` takes: a namespace and an id, as `restore.source` answers them. */
const FROM = `${"a".repeat(32)}/${"b".repeat(16)}`;

describe("mode restore", () => {
  const FRESH = "c".repeat(16);
  const CONFIRMED = `wipe-user_alice-${FRESH}`;
  const [OWN, OTHER, BOBS, GONE] = ["a", "1", "2", "3"].map((digit) => digit.repeat(32)) as
    [string, string, string, string];
  /**
   * The doors as a restore's ops find them, for an owner whose object stands as `source`
   * says: pinned and its present state written on the second pin, wiped, begun, two steps,
   * done, then admitted; and the files bucket's namespaces in two pages: two of the owner's
   * (`OWN`, which `FROM` names, and `OTHER`, older), another owner's, and one erased.
   */
  const restoring = (source: { state: string; begins: boolean; restore: unknown },
                     failed = false) => {
    let steps = 0;
    let pins = 0;
    return (message: OperatorMessage) => {
      switch (message.op) {
        case "restore.pin":
          pins++;
          return { result: { step: "pinned", namespace: "a".repeat(32), gen: "b".repeat(16),
                             fresh: FRESH, written: pins > 1,
                             confirm: pins > 1 ? CONFIRMED : null } };
        case "restore.wipe":
          return { result: { step: "wiped", namespace: "a".repeat(32), gen: "b".repeat(16),
                             fresh: FRESH, written: true, confirm: null } };
        case "restore.release":
          return { result: { released: true } };
        case "restore.begin":
          return { result: { step: "index", done: false } };
        case "restore.step":
          steps++;
          return { result: steps < 2 ? { step: "apply", done: false }
            : failed ? { step: "failed", done: false, failed: "board's row t1 differs" }
            : { step: "done", done: true, gen: "b".repeat(16), rows: 12, chunks: 3, keys: [],
                state: "admitted", reasons: [] } };
        case "owner.admit":
          return { result: { state: "admitted", reasons: [] } };
        case "restore.namespaces":
          return { result: message.args["cursor"] === ""
            ? { namespaces: [OWN, OTHER], cursor: "page-2" }
            : { namespaces: [BOBS, GONE], cursor: null } };
        case "restore.locate":
          return { result: { located: {
            [OWN]: { owner: "user_alice", gen: "b".repeat(16), at: 2_000 },
            [OTHER]: { owner: "user_alice", gen: "d".repeat(16), at: 1_000 },
            [BOBS]: { owner: "user_bob", gen: "e".repeat(16), at: 3_000 }, [GONE]: "erased" } } };
        case "restore.hold":
          return { result: { namespace: OWN, held: OTHER } };
        default:
          return { result: { namespace: source.begins ? null : OWN, generations: [],
                             ...source } };
      }
    };
  };
  const LOST = { state: "pending", begins: true, restore: null };
  const LIVE = { state: "admitted", begins: false, restore: null };
  const flags = ["--owner", "user_alice", "--generation", FROM];
  const ops = (w: ReturnType<typeof world>) =>
    w.sent.map(({ message }) => [message.op, message.args]);
  const PIN = ["restore.pin", { namespace: OWN, gen: "b".repeat(16) }];
  /** A lost object's find: the bucket's namespaces, page by page, then each located. */
  const FOUND = [["restore.namespaces", { cursor: "" }], ["restore.namespaces",
    { cursor: "page-2" }], ["restore.locate", { namespaces: [OWN, OTHER, BOBS, GONE].join(",") }]];

  it("plans the restore and what the owner loses; with --yes, into a lost owner's empty " +
     "object, begins it, steps it until done and admits the owner", async () => {
    const planned = world({ held: true });
    expect((await runMode(planned, "restore", ["--public", ...flags], PUBLIC)).plan).toEqual([
      `restore user_alice from generation ${"b".repeat(16)} of namespace ${"a".repeat(32)}, ` +
      "into its own object only while it is pending and empty (a live owner held first, its " +
      "present state written as a fresh generation, and its object wiped once --confirm " +
      "names that generation), then admit it, through the operator channel; for a restore no " +
      "pin holds (a lost object's), every other namespace R2 holds naming the owner found " +
      "(restore.namespaces, restore.locate) and held by the one it restores (restore.hold) " +
      `before it is admitted: ${RESTORE_LOSES}`]);
    expect(planned.sent).toEqual([]);
    const w = world({ held: true, doors: restoring(LOST) });
    const work = await runMode(w, "restore", ["--yes", ...flags]);
    expect(ops(w)).toEqual([["restore.source", {}], ...FOUND,
      ["restore.begin", { namespace: OWN, gen: "b".repeat(16) }],
      ["restore.hold", { namespace: OTHER, gen: "d".repeat(16) }],
      ["restore.step", {}], ["restore.step", {}], ["owner.admit", {}]]);
    expect(w.sent.filter(({ message }) => message.op.startsWith("restore.n")
      || message.op === "restore.locate").map(({ message }) => message.owner))
      .toEqual([PLATFORM_CALLER, PLATFORM_CALLER, PLATFORM_CALLER]);
    expect(work.plan).toContain(`held each of the 1 other namespaces R2 holds naming user_alice ` +
                                `by ${OWN}, so its erasure takes them`);
    expect(work.plan).toContain(`restored user_alice from ${"b".repeat(16)}: 12 rows, 3 chunks ` +
                                "read back, the owner admitted as at the generation");
  });

  it("finds a lost object's namespaces in R2 with no --generation, newest first, and restores " +
     "from none the bucket marks erased", async () => {
    const w = world({ held: true, doors: restoring(LOST) });
    const work = await runMode(w, "restore", ["--yes", "--owner", "user_alice"]);
    expect(ops(w)).toEqual([["restore.source", {}], ...FOUND]);
    expect(work.plan.at(-1)).toBe(`R2 holds 2 namespaces naming user_alice, newest first: ` +
      `${OWN}/${"b".repeat(16)} (1970-01-01T00:00:02.000Z), ${OTHER}/${"d".repeat(16)} ` +
      "(1970-01-01T00:00:01.000Z): run again with --generation <namespace>/<gen>, and every " +
      "other is held by the one it restores");
    const erased = world({ held: true, doors: restoring(LOST) });
    await expect(runMode(erased, "restore", ["--yes", "--owner", "user_alice", "--generation",
                                             `${GONE}/${"b".repeat(16)}`])).rejects.toThrow(
      `the namespace ${GONE} is erased (its mark, or its owner's erasure): nothing is restored ` +
      "from it");
    expect(ops(erased)).toEqual([["restore.source", {}], ...FOUND]);
  });

  it("into a live owner: pins until its present state is written, then stops and names " +
     "the words; with them, wipes, restores and admits, in order", async () => {
    const held = world({ held: true, doors: restoring(LIVE) });
    const stopped = await runMode(held, "restore", ["--yes", ...flags]);
    expect(ops(held)).toEqual([["restore.source", {}], PIN, PIN]);
    expect(stopped.plan.join("\n")).toContain(`run again with --confirm ${CONFIRMED}; to give ` +
                                              "the pin up, --confirm release-user_alice and " +
                                              "no --generation");
    const w = world({ held: true, doors: restoring(LIVE) });
    await runMode(w, "restore", ["--yes", ...flags, "--confirm", CONFIRMED]);
    expect(ops(w)).toEqual([["restore.source", {}], PIN, PIN,
      ["restore.wipe", { confirm: CONFIRMED }],
      ["restore.begin", { namespace: "a".repeat(32), gen: "b".repeat(16) }],
      ["restore.step", {}], ["restore.step", {}], ["owner.admit", {}]]);
  });

  it("stops on words that are not the pin's, on a refusal, and on a failed restore, " +
     "never wiping or admitting past it", async () => {
    const wrong = world({ held: true, doors: restoring(LIVE) });
    await expect(runMode(wrong, "restore", ["--yes", ...flags, "--confirm",
                                            "wipe-user_alice-x"])).rejects.toThrow(
      `--confirm wipe-user_alice-x is not the wipe's words for user_alice: ${CONFIRMED}`);
    expect(ops(wrong).map(([op]) => op)).not.toContain("restore.wipe");
    const refusing = world({ held: true, doors: (message: OperatorMessage) =>
      message.op === "restore.pin"
        ? { refused: { code: "refused", message: "the pin is refused" } }
        : restoring(LIVE)(message) });
    await expect(runMode(refusing, "restore", ["--yes", ...flags, "--confirm", CONFIRMED]))
      .rejects.toThrow("the doors refused restore.pin for user_alice (refused): the pin is " +
                       "refused");
    expect(ops(refusing).map(([op]) => op)).toEqual(["restore.source", "restore.pin"]);
    // A pin that never writes: the run stops loudly once its wait is past, never wiping.
    const writing = world({ held: true, doors: (message: OperatorMessage) =>
      message.op === "restore.pin"
        ? { result: { step: "pinned", namespace: "a".repeat(32), gen: "b".repeat(16),
                      fresh: FRESH, written: false, confirm: null } }
        : restoring(LIVE)(message) });
    await expect(runMode(writing, "restore", ["--yes", ...flags, "--confirm", CONFIRMED]))
      .rejects.toThrow(`user_alice's present state was not written within ${RUN_MS /
        60_000} minutes, and the owner stays held: run again to carry it on, or give the pin ` +
        "up with --confirm release-user_alice and no --generation");
    const pins = ops(writing).filter(([op]) => op === "restore.pin").length;
    expect(pins).toBeGreaterThan(1);
    expect(pins).toBeLessThanOrEqual(RUN_MS / POLL_MS + 1);
    expect(ops(writing).map(([op]) => op)).not.toContain("restore.wipe");
    const failing = world({ held: true, doors: restoring(LOST, true) });
    await expect(runMode(failing, "restore", ["--yes", ...flags])).rejects.toThrow(
      "the restore of user_alice failed its check, and the owner is not admitted: board's row " +
      "t1 differs");
    expect(ops(failing).map(([op]) => op)).not.toContain("owner.admit");
  });

  it("asks the owner's source with no --generation, gives a pin up with release, and only " +
     "admits an owner restored from that generation already", async () => {
    const w = world({ held: true, doors: restoring(LIVE) });
    await runMode(w, "restore", ["--yes", "--owner", "user_alice"]);
    expect(ops(w)).toEqual([["restore.source", {}]]);
    const released = world({ held: true, doors: restoring(LIVE) });
    await runMode(released, "restore", ["--yes", "--owner", "user_alice", "--confirm",
                                        "release-user_alice"]);
    expect(ops(released)).toEqual([["restore.release", {}]]);
    await expect(runMode(world({ held: true }), "restore", ["--yes", "--owner", "user_alice",
                                                            "--confirm", CONFIRMED]))
      .rejects.toThrow(`--confirm ${CONFIRMED} with no --generation is release-user_alice`);
    const done = world({ held: true, doors: restoring({ state: "admitted", begins: false,
      restore: { step: "done", gen: "b".repeat(16), fresh: FRESH } }) });
    await runMode(done, "restore", ["--yes", ...flags]);
    expect(ops(done)).toEqual([["restore.source", {}], ["owner.admit", {}]]);
    // A lost object's restore done, run again: its other namespaces are held before the admit.
    const lost = world({ held: true, doors: restoring({ state: "admitted", begins: false,
      restore: { step: "done", gen: "b".repeat(16), fresh: null } }) });
    await runMode(lost, "restore", ["--yes", ...flags]);
    expect(ops(lost)).toEqual([["restore.source", {}], ...FOUND,
      ["restore.hold", { namespace: OTHER, gen: "d".repeat(16) }], ["owner.admit", {}]]);
  });

  it("takes a generation only as <namespace>/<gen>, and only for mode restore", () => {
    const w = world();
    for (const text of ["a/b", `${"a".repeat(32)}`, `${FROM}/c`, FROM.toUpperCase()]) {
      expect(() => parse(["--mode", "restore", "--owner", "user_alice", "--generation", text],
                         ENV, w.io), text).toThrow(
        "--generation is the generation to restore from, <namespace>/<gen> as restore.source " +
        "answers them");
    }
    expect(() => parse(["--mode", "owner", "--owner", "user_alice", "--act", "admit",
                        "--generation", FROM], ENV, w.io))
      .toThrow("mode owner takes no --generation: it is mode restore's");
    expect(() => parse(["--mode", "restore", "--owner", "user_alice", "--confirm", "yes"],
                       ENV, w.io)).toThrow(
      "--confirm is the words that confirm a restore's wipe, wipe-<owner>-<gen> as " +
      "restore.pin answers them, or give its pin up, release-<owner>, an erasure, " +
      "erase-<owner>, or a keyring's retirement, retire-<keyring>-<kids> as its apply job " +
      "names them");
  });
});

describe("mode copyback", () => {
  const NS = "a".repeat(32);
  const PREFIX = `o/${NS}/`;
  /** The copy back's settings. */
  const BACK = Object.fromEntries([...BACK_INPUTS.public, ...BACK_INPUTS.secret]
    .map((name) => [name, ENV[name]!]));
  /**
   * The doors answering restore.source as an object holding `namespace`, or an erased one, and
   * restore.names in two pages: the chunk `2` and the generation `3`, then nothing more.
   */
  const source = (namespace: string | null, erased = false) => (message: OperatorMessage) =>
    erased ? { refused: { code: "owner_erased" as const, message: "the owner is erased" } }
      : { result: message.op === "restore.source"
          ? { namespace, generations: [], state: "admitted", begins: false, restore: null }
          : message.op === "restore.names"
          ? message.args["from"] === 0 ? { chunks: ["2"], generations: ["3"], next: 1 }
            : { chunks: [], generations: ["3"], next: null }
          : message.args };
  const copied = { copy: ["c/1", "c/2", "m/3"], r2: ["c/1"] };
  const recorded = `ns-${NAMESPACES["CONTROL"]}/${RESTORE_KEY}`;
  const rclone = (w: ReturnType<typeof world>) =>
    w.events.filter((event) => event.startsWith("rclone "));
  const AT = "2026-11-01T00:00:00Z";

  it("plans the copy back; with --yes, asks the owner's object its namespace, records the " +
     "restore, then writes only what R2 lacks, as the copy stood at --at, and reads it back",
     async () => {
    const planned = world({ held: true });
    expect((await runMode(planned, "copyback", ["--public", "--owner", "user_alice"],
                          { ...PUBLIC, OFFSITE_BUCKET: BACK["OFFSITE_BUCKET"]! })).plan).toEqual([
      `copy back into ${FILES_BUCKET} the objects of user_alice's namespace that it lacks and ` +
      "the owner's object names (restore.names: a row of it each, so nothing its own " +
      "collection deleted comes back), as the off-account copy stands, never one it holds, " +
      `after writing the restore record ${RESTORE_KEY}: the namespace the owner's own object ` +
      "names (restore.source through the operator channel, which the doors refuse for an " +
      "erased or erasing owner), and none the files bucket marks erased (its marks read from " +
      "R2 and the copy first, a loud stop when they can't be), each object written read back " +
      "byte for byte"]);
    expect(planned.sent).toEqual([]);
    const w = world({ held: true, doors: source(NS), copied });
    const work = await runMode(w, "copyback", ["--yes", "--owner", "user_alice", "--at", AT],
                               ENV);
    // The object's names asked page by page once R2 is listed, and again after the write.
    expect(w.sent.map(({ message }) => [message.op, message.args])).toEqual([
      ["restore.source", {}], ...[0, 1, 0, 1].map((from) => ["restore.names", { from }])]);
    // Begun after three asks: restore.source and the two pages of names.
    expect(JSON.parse(w.kv.get(recorded)!)).toEqual({ kind: "tinyassets-offsite-restore",
                                                     began: (NOW.getTime() + 3 * POLL_MS) / 1000 });
    // The restore record before the first byte: the copy back's, and after the listings.
    order(w.events, ["cf POST /accounts/", "rclone lsf", "rclone lsf",
                     `${CONTROL_WRITE}namespaces/ns-${NAMESPACES["CONTROL"]}/values/` +
                     encodeURIComponent(RESTORE_KEY), "rclone copy"]);
    const bucket = BACK["OFFSITE_BUCKET"]!;
    expect(rclone(w).map((event) => event.replace(/--files-from \S+/, "--files-from <list>")))
      .toEqual([
        `rclone lsf --files-only -R --include /e/* now:${bucket}`,
        `rclone lsf --files-only -R --include /e/* back:${bucket}`,
        `rclone lsf --files-only -R --include /e/* r2:${FILES_BUCKET}`,
        `rclone lsf --files-only -R --use-server-modtime --fast-list r2:${FILES_BUCKET}/${PREFIX}`,
        `rclone lsf --files-only -R --use-server-modtime --fast-list back:${bucket}/${PREFIX}`,
        `rclone copy back:${bucket}/${PREFIX} r2:${FILES_BUCKET}/${PREFIX} --files-from <list> ` +
        "--ignore-existing --stats-one-line --stats 1m --stats-log-level NOTICE (c/2,m/3)",
        `rclone check back:${bucket}/${PREFIX} r2:${FILES_BUCKET}/${PREFIX} --one-way ` +
        "--download --files-from <list> (c/2,m/3)"]);
    expect(work.plan).toContain(`wrote 2 objects under ${PREFIX} into ${FILES_BUCKET}, as ` +
                                `the copy held them at ${AT}, each one R2 lacked and the ` +
                                "owner's object names, and read each back byte for byte (0 " +
                                "more it lacks no row names)");
  });

  it("writes nothing and records nothing when R2 lacks nothing the copy holds", async () => {
    const w = world({ held: true, doors: source(NS), copied: { copy: ["c/1"], r2: ["c/1"] } });
    await runMode(w, "copyback", ["--yes", "--owner", "user_alice"], ENV);
    expect([w.kv.has(recorded), rclone(w).map((event) => event.split(" ")[1])])
      .toEqual([false, ["lsf", "lsf", "lsf", "lsf"]]);
  });

  it("applies the erasure first: an erased owner, or an object that names no namespace, is " +
     "refused before anything is read from the copy or written", async () => {
    for (const [doors, refused] of [
      [source(NS, true), "the doors refused restore.source for user_alice " +
       "(owner_erased)"],
      [source(null), "user_alice's object names no namespace, so nothing says which of the " +
       "copy's is its own"],
    ] as const) {
      const w = world({ held: true, doors, copied });
      await expect(runMode(w, "copyback", ["--yes", "--owner", "user_alice"], ENV))
        .rejects.toThrow(refused);
      expect([w.kv.has(recorded), rclone(w)]).toEqual([false, []]);
    }
  });

  it("refuses, before any step, a run without the copy's keys, and a time outside the " +
     "copy's window", async () => {
    for (const name of Object.keys(BACK)) {
      const { [name]: _, ...without } = ENV;
      const w = world({ held: true, doors: source(NS), copied });
      await expect(runMode(w, "copyback", ["--yes", "--owner", "user_alice"], without))
        .rejects.toThrow(`${name} is not set`);
      expect([w.sent, w.kv.has(recorded), rclone(w)]).toEqual([[], false, []]);
    }
    for (const at of ["2026-10-17T11:59:59Z", "2026-11-07T12:00:01Z"]) {
      const w = world({ held: true, doors: source(NS), copied });
      await expect(runMode(w, "copyback", ["--yes", "--owner", "user_alice", "--at", at], ENV))
        .rejects.toThrow(`not at ${at}: a version older than 22 days since it was hidden is ` +
                         "gone");
      expect([w.sent, rclone(w)]).toEqual([[], []]);
    }
    expect(() => parse(["--mode", "copyback", "--owner", "user_alice", "--at", "yesterday"],
                       ENV, world().io))
      .toThrow("--at is the time the off-account copy is read back as it stood at");
  });
});

describe("mode recover", () => {
  const [AA, BB] = ["a", "b"].map((hex) => hex.repeat(32));
  const GEN = "1".repeat(16);
  /** The copy as it stood: two namespaces, each with a root; the new account's R2 is empty. */
  const copied = { copy: [`o/${AA}/c/1`, `o/${AA}/m/${GEN}`, `o/${BB}/m/${GEN}`], r2: [] };
  /** The doors locating each namespace to its own owner, and beginning each restore. */
  const doors = (message: OperatorMessage) => ({ result: message.op === "restore.locate"
    ? { located: Object.fromEntries(String(message.args["namespaces"]).split(",")
        .map((ns) => [ns, { owner: `user_${ns.slice(0, 2)}`, gen: GEN }])) }
    : { step: "index", gen: GEN } });
  const recorded = `ns-${NAMESPACES["CONTROL"]}/${RESTORE_KEY}`;
  const AT = "2026-11-01T00:00:00Z";
  const BUCKET = ENV["OFFSITE_BUCKET"]!;

  it("plans the recover; with --yes, records, writes the copy back, then locates each " +
     "namespace's owner, addressed to the platform, and begins each one's restore", async () => {
    const planned = world({ held: true });
    expect((await runMode(planned, "recover", ["--public"],
                          { ...PUBLIC, OFFSITE_BUCKET: BUCKET })).plan).toEqual([
      `write back into ${FILES_BUCKET}, as the off-account copy stands, every object it lacks ` +
      "of each namespace that no erasure mark names and that holds a root, never one it holds, " +
      `after writing the restore record ${RESTORE_KEY} and then every mark R2 lacks (the marks ` +
      "read from R2 and the copy first, a loud stop when they can't be), each read back byte " +
      "for byte; then locate each namespace's owner from its newest generation " +
      "(restore.locate, addressed to the platform) and begin that owner's restore from it " +
      "(restore.begin), through the operator channel: two namespaces naming one owner stop " +
      "the run before any restore begins; a namespace a marked one holds is left out, one a " +
      "written one holds is not begun, and one whose owner takes no restore (live in this " +
      "account) is held by the owner's own (restore.hold), so its erasure takes it, and the " +
      "run stops naming it"]);
    expect(planned.sent).toEqual([]);
    const w = world({ held: true, doors, copied });
    const work = await runMode(w, "recover", ["--yes", "--at", AT], ENV);
    expect(w.sent.map(({ message }) => [message.op, message.owner, message.args])).toEqual([
      ["restore.locate", PLATFORM_CALLER, { namespaces: `${AA},${BB}` }],
      ["restore.begin", "user_aa", { namespace: AA, gen: GEN }],
      ["restore.begin", "user_bb", { namespace: BB, gen: GEN }]]);
    expect(JSON.parse(w.kv.get(recorded)!)).toMatchObject({ kind: "tinyassets-offsite-restore" });
    // The record before the first byte, and every byte before the first ask.
    order(w.events, ["rclone lsf", `${CONTROL_WRITE}namespaces/ns-${NAMESPACES["CONTROL"]}/` +
                     `values/${encodeURIComponent(RESTORE_KEY)}`, `rclone copy back:${BUCKET}/`,
                     `rclone check back:${BUCKET}/`, "cf POST /accounts/"]);
    expect(work.plan).toContain(`began user_bb's restore from ${BB}/${GEN}: mode restore ` +
                                `--owner user_bb --generation ${BB}/${GEN} carries it on`);
  });

  it("holds a namespace whose owner's begin is refused by that owner's own, then stops naming " +
     "it", async () => {
    const w = world({ held: true, copied, doors: (message) =>
      message.op === "restore.begin" && message.owner === "user_aa"
        ? { refused: { code: "refused" as const, message: "the owner is admitted" } }
        : message.op === "restore.hold" ? { result: { namespace: BB, held: AA } }
        : doors(message) });
    await expect(runMode(w, "recover", ["--yes"], ENV)).rejects.toThrow(
      `1 of 2 restores were not begun, each written back: user_aa from ${AA}/${GEN}: refused ` +
      "(refused): the owner is admitted; held by the owner's own namespace, so its erasure " +
      "takes it, and not restored");
    expect(w.sent.map(({ message }) => [message.op, message.owner]).slice(1)).toEqual([
      ["restore.begin", "user_aa"], ["restore.begin", "user_bb"], ["restore.hold", "user_aa"]]);
    expect(w.sent.at(-1)!.message.args).toEqual({ namespace: AA, gen: GEN });
  });

  it("locates the namespaces a page of LOCATE_PAGE a message", async () => {
    const many = Array.from({ length: LOCATE_PAGE + 1 },
                            (_, at) => at.toString(16).padStart(2, "0").repeat(16));
    const w = world({ held: true, doors, copied: { copy: many.map((ns) => `o/${ns}/m/${GEN}`),
                                                  r2: [] } });
    await runMode(w, "recover", ["--yes"], ENV);
    expect(w.sent.filter(({ message }) => message.op === "restore.locate")
      .map(({ message }) => String(message.args["namespaces"]).split(",").length))
      .toEqual([LOCATE_PAGE, 1]);
  });

  it("stops loudly, beginning nothing, when the doors refuse a page of locates", async () => {
    const w = world({ held: true, copied, doors: (message) => message.op === "restore.locate"
      ? { refused: { code: "invalid" as const, message: "no" } } : doors(message) });
    await expect(runMode(w, "recover", ["--yes"], ENV)).rejects.toThrow(
      `the doors refused (invalid: no) restore.locate of the 2 namespaces from ${AA}: nothing ` +
      "is begun");
    expect(w.sent.map(({ message }) => message.op)).toEqual(["restore.locate"]);
  });

  it("takes no --owner, and refuses a run without the copy's keys before any step", async () => {
    expect(() => parse(["--mode", "recover", "--owner", "user_alice"], ENV, world().io))
      .toThrow("mode recover takes no --owner");
    for (const name of [...BACK_INPUTS.secret, "OPERATOR_KEYRING"]) {
      const { [name]: _, ...without } = ENV;
      const w = world({ held: true, doors, copied });
      await expect(runMode(w, "recover", ["--yes"], without)).rejects.toThrow(`${name} is not set`);
      expect([w.sent, w.kv.has(recorded),
              w.events.filter((event) => event.startsWith("rclone "))]).toEqual([[], false, []]);
    }
  });
});

describe("mode erase", () => {
  const WORDS = "erase-user_alice";
  const sent = (w: ReturnType<typeof world>) =>
    w.sent.map(({ message }) => [message.op, message.owner, message.args]);

  it("without its words ends naming them, asking nothing, though the run says --yes",
     async () => {
    for (const flags of [["--public"], ["--yes"]]) {
      const w = world({ held: true });
      const work = await runMode(w, "erase", [...flags, "--owner", "user_alice"],
                                 flags[0] === "--public" ? PUBLIC : ENV);
      expect(work.plan.at(-1)).toBe(
        `erase user_alice only with its words: run again with --confirm ${WORDS}`);
      expect(w.sent).toEqual([]);
    }
  });

  it("stops on words that are not the owner's, asking nothing", async () => {
    for (const words of ["erase-user_bob", "erase-user_alice2", "wipe-user_alice-x",
                         "release-user_alice"]) {
      const w = world({ held: true });
      await expect(runMode(w, "erase", ["--yes", "--owner", "user_alice", "--confirm", words]),
                   words).rejects.toThrow(
        `--confirm ${words} is not the erasure's words for user_alice: ${WORDS}`);
      expect(w.sent).toEqual([]);
    }
  });

  it("plans the erasure with its words, and with --yes sends owner.erase MAC'd with them " +
     "and says the doors' answer", async () => {
    const flags = ["--owner", "user_alice", "--confirm", WORDS];
    const planned = world({ held: true });
    expect((await runMode(planned, "erase", ["--public", ...flags], PUBLIC)).plan).toEqual([
      "erase user_alice through the operator channel, as its own door erases it; a lost " +
      `object only once R2 holds no namespace naming it: ${ERASE_LOSES}`]);
    expect(planned.sent).toEqual([]);
    const w = world({ held: true, doors: () => ({ result: { erasing: true } }) });
    const work = await runMode(w, "erase", ["--yes", ...flags]);
    expect(sent(w)).toEqual([["restore.source", "user_alice", {}],
                             ["owner.erase", "user_alice", { confirm: WORDS }]]);
    expect(await macHolds(parseRing(RING)!, w.sent[1]!.message)).toBe(true);
    expect(work.plan.at(-1)).toBe('the doors answered owner.erase for user_alice: ' +
                                  '{"erasing":true}');
    const refusing = world({ held: true, doors: (message) => message.op === "owner.erase" ? {
      refused: { code: "refused", message: "the platform holds no owner of that name" } }
      : { result: {} } });
    await expect(runMode(refusing, "erase", ["--yes", ...flags])).rejects.toThrow(
      "the doors refused owner.erase for user_alice (refused): the platform holds no owner " +
      "of that name");
  });

  it("refuses a lost object while R2 holds a namespace naming it, which its erasure would " +
     "not reach; erases one R2 holds none of", async () => {
    const NS = "a".repeat(32);
    const lost = (owner: string) => (message: OperatorMessage) => ({ result:
      message.op === "restore.namespaces" ? { namespaces: [NS], cursor: null }
      : message.op === "restore.locate" ? { located: { [NS]: { owner, gen: "b".repeat(16),
                                                              at: 0 } } }
      : { state: "pending", namespace: null, restore: null, erasing: true } });
    const found = world({ held: true, doors: lost("user_alice") });
    await expect(runMode(found, "erase", ["--yes", "--owner", "user_alice", "--confirm", WORDS]))
      .rejects.toThrow("user_alice's object is lost and R2 holds 1 namespaces naming it, which " +
                       "its erasure would not reach: restore it first (mode restore), then " +
                       "erase it");
    expect(sent(found).map(([op]) => op)).toEqual(["restore.source", "restore.namespaces",
                                                   "restore.locate"]);
    const none = world({ held: true, doors: lost("user_bob") });
    await runMode(none, "erase", ["--yes", "--owner", "user_alice", "--confirm", WORDS]);
    expect(sent(none).at(-1)).toEqual(["owner.erase", "user_alice", { confirm: WORDS }]);
  });

  it("is mode erase's alone: --act never asks owner.erase", () => {
    const w = world();
    for (const act of ["erase", `erase=${WORDS}`]) {
      expect(() => parse(["--mode", "owner", "--owner", "user_alice", "--act", act], ENV, w.io),
             act).toThrow(new RegExp("^--act is one of state, tier=<tier>, suspend=<reason>, " +
                                     "lift=<reason>, admit, rewrap, reseal$"));
    }
    expect(() => parse(["--mode", "owner", "--owner", "user_alice", "--act", "admit",
                        "--confirm", WORDS], ENV, w.io))
      .toThrow("mode owner takes no --confirm: it is mode restore, erase and retire's");
    expect(() => parse(["--mode", "erase", "--confirm", WORDS], ENV, w.io))
      .toThrow("mode erase needs --owner: the owner principal the op is asked for");
  });
});

describe("modes rotation and retire", () => {
  const OWNERS = ["user_alice", "user_bob", "user_carol"];
  const FILES = { ...ENV, FILES_KEYRING: ROTATED };
  const ACTIVE = parseRing(ROTATED)!;
  /** `ROTATED` less k1, as keyring-retire writes it. */
  const SHRUNK = JSON.stringify({ active: "k2", keys: { k2: Buffer.from(
    ACTIVE.keys.get("k2")!).toString("base64") } });
  const rewrapped = () => ({ result: { rewrapped: true, roots: 1, left: 0, kid: "k2" } });
  const recordIn = (w: ReturnType<typeof world>, name: string) =>
    JSON.parse(w.kv.get(`${CONTROL_NS}/${rotationKey(name, "k2")}`) ?? "null") as
      { passes: { swept: number | null; owing: string[] }[] } | null;

  it("plans the sweep with the token alone, and never sweeps while the doors hold the import " +
     "window's key", async () => {
    const planned = world({ held: true, owners: OWNERS });
    expect((await runMode(planned, "rotation", ["--public", "--keyring", "FILES_KEYRING"],
                          PUBLIC)).plan).toEqual([expect.stringMatching(
      /^ask the doors owner\.rewrap for every owner the directory lists, through the operator /)]);
    expect(planned.sent).toEqual([]);
    for (const flags of [["--public"], ["--yes"]]) {
      const open = world({ held: true, owners: OWNERS, windowOpen: true });
      await expect(runMode(open, "rotation", [...flags, "--keyring", "FILES_KEYRING"],
                           flags[0] === "--public" ? PUBLIC : FILES)).rejects.toThrow(
        `${NAMES["doors"]} holds IMPORT_KEY: the import window is open`);
      expect(open.sent).toEqual([]);
    }
  });

  it("asks every owner the directory lists, its record swept; with no --yes says where the " +
     "record stands and asks nothing", async () => {
    const w = world({ held: true, owners: OWNERS, doors: rewrapped });
    const work = await runMode(w, "rotation", ["--yes", "--keyring", "FILES_KEYRING"], FILES);
    expect(w.sent.map(({ message }) => [message.op, message.owner]))
      .toEqual(OWNERS.map((owner) => ["owner.rewrap", owner]));
    expect(work.plan.at(-1)).toMatch(/^pass 1 swept every owner to left 0, the last at /);
    expect(recordIn(w, "FILES_KEYRING")!.passes).toEqual([expect.objectContaining({
      owing: [], swept: expect.any(Number) })]);
    const read = await runMode(w, "rotation", ["--keyring", "FILES_KEYRING"], FILES);
    expect(read.plan.at(-1)).toMatch(
      /^pass 1 of FILES_KEYRING's rotation to k2: after user_carol, 0 owing, swept at /);
    expect(w.sent).toHaveLength(OWNERS.length);
  });

  it("retires a gateway kid only with the words its apply job names, setting the stored ring " +
     "on the gateway, then the canary", async () => {
    const w = world({ held: true });
    const key = rotationKey("GATEWAY_KEYRING", "k2");
    w.kv.set(`${CONTROL_NS}/${key}`, JSON.stringify({
      kind: "tinyassets-rotation", keyring: "GATEWAY_KEYRING", active: "k2",
      keys: keyHashes(ACTIVE), passes: [{ began: 1, through: "user_carol", owing: [], swept: 2 }] }));
    const env = { ...ENV, GATEWAY_KEYRING: SHRUNK, GITHUB_TOKEN: "gh-token" };
    const words = "retire-GATEWAY_KEYRING-k1";
    const named = await runMode(w, "retire", ["--yes", "--keyring", "GATEWAY_KEYRING"], env);
    expect(named.plan.at(-1)).toBe("retire k1 from GATEWAY_KEYRING only with its words: keep " +
                                   `the old ring file offline, then run again with --confirm ${words}`);
    for (const wrong of ["retire-GATEWAY_KEYRING-k2", "retire-FILES_KEYRING-k1"]) {
      await expect(runMode(w, "retire", ["--yes", "--keyring", "GATEWAY_KEYRING", "--confirm",
                                         wrong], env)).rejects.toThrow(
        `--confirm ${wrong} is not the retirement's words for GATEWAY_KEYRING: ${words}`);
    }
    // The full ring still stored: nothing is set.
    await expect(runMode(w, "retire", ["--yes", "--keyring", "GATEWAY_KEYRING", "--confirm",
                                       words], { ...env, GATEWAY_KEYRING: ROTATED }))
      .rejects.toThrow("the stored GATEWAY_KEYRING is not the rotation record's ring less k1");
    expect(w.events.filter((event) => event.startsWith("wrangler"))).toEqual([]);
    // The gateway serving two versions: `secret put` would deploy at once, so nothing is set.
    const gateway = NAMES["gateway"]!;
    const held = w.deployments.get(gateway)!;
    w.deployments.set(gateway, [{ version: "gw-new", percentage: 10 },
                                { ...held[0]!, percentage: 90 }]);
    await expect(runMode(w, "retire", ["--yes", "--keyring", "GATEWAY_KEYRING", "--confirm",
                                       words], env)).rejects.toThrow(
      `${gateway} is split (gw-new at 10%, ${held[0]!.version} at 90%): end the split first`);
    expect(w.events.filter((event) => event.startsWith("wrangler"))).toEqual([]);
    w.deployments.set(gateway, held);
    w.events.splice(0);
    const work = await runMode(w, "retire", ["--yes", "--keyring", "GATEWAY_KEYRING",
                                             "--confirm", words], env);
    expect(work.plan.slice(-1)).toEqual([`set the secret GATEWAY_KEYRING on ${
      NAMES["gateway"]}, k1 retired`]);
    expect(w.events.filter((event) => /^(wrangler|node)/.test(event))).toEqual([
      `wrangler secret put GATEWAY_KEYRING --name ${NAMES["gateway"]}`,
      expect.stringMatching(/^node canary\.ts/)]);
    expect(Object.values(w.secretsPut)).toEqual([SHRUNK]);
  });

  it("retires a files kid only from a pass begun after the latest restore from the " +
     "off-account copy, which it reads from the control namespace", async () => {
    const w = world({ held: true });
    const began = NOW.getTime() / 1000 - 30 * 86_400;
    w.kv.set(`${CONTROL_NS}/${rotationKey("FILES_KEYRING", "k2")}`, JSON.stringify({
      kind: "tinyassets-rotation", keyring: "FILES_KEYRING", active: "k2",
      keys: keyHashes(ACTIVE), passes: [{ began, through: "user_carol", owing: [],
                                          swept: began + 3600 }] }));
    // The copy's nights, newest first: last night, and the morning after the pass.
    const nights = [NOW.getTime() - 12 * 3600_000, (began + 18 * 3600) * 1000]
      .map((at) => night(new Date(at).toISOString(), "success"));
    const fetch = w.io.fetch;
    const io = { ...w.io, fetch: async (url: string, init?: RequestInit) =>
      nightsAnswer(url, nights) ?? fetch(url, init) };
    const env = { ...ENV, FILES_KEYRING: SHRUNK, GITHUB_TOKEN: "gh-token" };
    const named = await runMode({ ...w, io }, "retire", ["--yes", "--keyring", "FILES_KEYRING"],
                                env);
    expect(named.plan.at(-1)).toBe("retire k1 from FILES_KEYRING only with its words: keep the " +
      "old ring file offline, then run again with --confirm retire-FILES_KEYRING-k1");
    // A restore from the copy after the pass began: no pass has rewrapped what it put back.
    w.kv.set(`${CONTROL_NS}/${RESTORE_KEY}`, JSON.stringify({
      kind: "tinyassets-offsite-restore", began: began + 86_400 }));
    await expect(runMode({ ...w, io }, "retire", ["--yes", "--keyring", "FILES_KEYRING"], env))
      .rejects.toThrow(`a restore from the off-account copy began at ${
        new Date((began + 86_400) * 1000).toISOString()}, after every pass of ${
        rotationKey("FILES_KEYRING", "k2")} that swept began`);
    // A record out of shape is a loud stop.
    w.kv.set(`${CONTROL_NS}/${RESTORE_KEY}`, JSON.stringify({ began: began + 86_400 }));
    await expect(runMode({ ...w, io }, "retire", ["--yes", "--keyring", "FILES_KEYRING"], env))
      .rejects.toThrow(`the restore record ${RESTORE_KEY} is out of shape`);
    expect(w.events.filter((event) => event.startsWith("wrangler secret"))).toEqual([]);
  });
});

describe("platform-deploy.yml", () => {
  type Job = { if?: string; needs?: string | string[]; environment?: string;
               "timeout-minutes"?: number; env?: Record<string, string>;
               permissions?: Record<string, string>;
               steps: { name?: string; run?: string; env?: Record<string, string>; uses?: string;
                        with?: Record<string, string | number | boolean> }[] };
  const file = workflow("platform-deploy.yml") as {
    on: Json; jobs: Record<string, Job> };
  const { plan, pair, apply } = file.jobs as Record<string, Job>;
  const runs = (job: Job) => job.steps.map((each) => each.run ?? "").join("\n");
  /** The modes the dispatch offers, each a row of the kit's (the pair is its own job). */
  const offered = (((file.on["workflow_dispatch"] as Json)["inputs"] as Json)["mode"] as
    { options: Mode[] }).options;
  const io = world().io;

  it("is run by hand alone, and reaches no host", () => {
    expect(Object.keys(file.on)).toEqual(["workflow_dispatch"]);
    expect(offered.filter((mode) => !Object.hasOwn(MODES, mode))).toEqual([]);
    for (const job of [plan!, pair!, apply!]) {
      expect(runs(job)).not.toMatch(/ssh|scp|droplet|\/etc\/tinyassets/i);
    }
  });

  /**
   * Whether Actions runs a job whose `if` is `expression` in `context` (each name it reads, by
   * its dotted path): the expression syntax the workflow's `if`s use, read as Actions reads it.
   */
  function runsIf(expression: string, context: Readonly<Record<string, string>>): boolean {
    const tokens = expression.match(/\(|\)|&&|\|\||==|!=|!|'[^']*'|[A-Za-z_][\w.-]*(\(\))?/g)!;
    expect(tokens.join(""), expression).toBe(expression.replace(/\s+/g, ""));
    let at = 0;
    const next = () => tokens[at++];
    const primary = (): string | boolean => {
      const token = next()!;
      if (token === "(") {
        const value = or();
        expect(next()).toBe(")");
        return value;
      }
      if (token === "!") {
        return !truthy(primary());
      }
      if (token.startsWith("'")) {
        return token.slice(1, -1);
      }
      if (token === "always()") {
        return true;
      }
      expect(Object.keys(context), token).toContain(token);
      return context[token]!;
    };
    const truthy = (value: string | boolean) => value !== false && value !== "";
    const compared = (): string | boolean => {
      const left = primary();
      if (tokens[at] === "==" || tokens[at] === "!=") {
        const equal = next() === "==";
        return (left === primary()) === equal;
      }
      return left;
    };
    const and = (): string | boolean => {
      let value = compared();
      while (tokens[at] === "&&") {
        next();
        const right = compared();
        value = truthy(value) && truthy(right);
      }
      return value;
    };
    const or = (): string | boolean => {
      let value = and();
      while (tokens[at] === "||") {
        next();
        const right = and();
        value = truthy(value) || truthy(right);
      }
      return value;
    };
    const value = or();
    expect(at, expression).toBe(tokens.length);
    return truthy(value);
  }

  it("runs the pair for the rows that deploy and apply, and applies each row that applies, " +
     "after the plan and, when its row deploys, the pair", () => {
    expect([pair!.needs, apply!.needs]).toEqual(["plan", ["plan", "pair"]]);
    for (const mode of offered) {
      const row = MODES[mode];
      for (const ref of ["refs/heads/main", "refs/heads/branch"]) {
        const main = ref === "refs/heads/main";
        const context = { "github.ref": ref, "inputs.mode": mode };
        expect([mode, ref, runsIf(plan!.if!, context)]).toEqual([mode, ref, main]);
        // The pair job needs the plan's success, as a job with no always() does.
        expect([mode, ref, runsIf(pair!.if!, context)])
          .toEqual([mode, ref, main && row.deploys && row.applies]);
        for (const planned of ["success", "failure"]) {
          for (const paired of ["success", "skipped", "failure"]) {
            const at = { ...context, "needs.plan.result": planned, "needs.pair.result": paired };
            expect([mode, ref, planned, paired, runsIf(apply!.if!, at)]).toEqual([
              mode, ref, planned, paired, main && row.applies && planned === "success"
                && (paired === "success" || (!row.deploys && paired === "skipped"))]);
          }
        }
      }
    }
  });

  /**
   * Every command `job`'s steps run, each argument whole, its scripts run as the job runs them
   * with `inputs` as the dispatch's, `node`, `npm` and `python3` recording their arguments in
   * place of running.
   */
  function invoked(job: Job, inputs: Record<string, string>): { argv: string[]; env: string[] }[] {
    const bin = mkdtempSync(join(tmpdir(), "workflow-"));
    try {
      const log = join(bin, "log");
      for (const tool of ["node", "npm", "python3"]) {
        writeFileSync(join(bin, tool), `#!/bin/sh\n{ printf '%s' "${tool}"; for arg in "$@"; ` +
                      `do printf '\\037%s' "$arg"; done; printf '\\t%s\\n' "$(env | ` +
                      `cut -d= -f1 | grep -E '_KEY$|_TOKEN$|_SECRET$' | sort | tr '\\n' ' ')"; ` +
                      `} >> "${log}"\n`, { mode: 0o755 });
      }
      for (const each of job.steps.filter((one) => one.run !== undefined)) {
        const ran = spawnSync("bash", ["-c", each.run!], {
          cwd: REPO, encoding: "utf8",
          env: { PATH: `${bin}:/usr/bin:/bin`, RUNNER_TEMP, ...inputs,
                 ...Object.fromEntries(Object.keys(each.env ?? {}).map((name) => [name, "x"])) } });
        expect([each.name, ran.status, ran.stderr]).toEqual([each.name, 0, ""]);
      }
      return readFileSync(log, "utf8").split("\n").filter(Boolean).map((line) => {
        const [argv, env] = line.split("\t");
        return { argv: argv!.split("\x1f"), env: (env ?? "").trim().split(" ").filter(Boolean) };
      });
    } finally {
      rmSync(bin, { recursive: true, force: true });
    }
  }
  const KIT = ["node", "--experimental-strip-types", "platform/deploy/deploy.ts"];
  /** The runner's temporary folder, which Actions sets, and the plan file the jobs hand on. */
  const RUNNER_TEMP = "/runner/temp";
  const PLAN = ["--plan", `${RUNNER_TEMP}/deploy-plan.json`];
  /** The dispatch's inputs, each handed to the kit as its option of the same name. */
  const DISPATCH = ["MODE", "ROTATE", "MIGRATE", "RECORD", "SET", "REPLACE", "OWNER", "ACT",
                    "GENERATION", "CONFIRM", "KEYRING", "AT"];
  const dispatched = (inputs: Record<string, string>) =>
    DISPATCH.flatMap((name) => [`--${name.toLowerCase()}`, inputs[name]!]);
  /** `mode` dispatched with what it needs and nothing else. */
  const dispatchOf = (mode: Mode): Record<string, string> => ({
    ...Object.fromEntries(DISPATCH.map((name) => [name, ""])), MODE: mode, ...NEEDED[mode] });
  /** Dispatches that give the options a mode may go without. */
  const OPTIONS: Record<string, string>[] = [{ ...dispatchOf("dark"), ROTATE: "ROUTING_MAC_KEY", MIGRATE: "owner" },
                   { ...dispatchOf("control"), SET: "outside=on canary+=user_bob",
                     REPLACE: JSON.stringify(DARK) },
                   { ...dispatchOf("restore"), GENERATION: FROM,
                     CONFIRM: `wipe-user_alice-${"c".repeat(16)}` },
                   { ...dispatchOf("erase"), CONFIRM: "erase-user_alice" },
                   { ...dispatchOf("retire"), CONFIRM: "retire-GATEWAY_KEYRING-k1" },
                   { ...dispatchOf("copyback"), AT: "2026-11-01T00:00:00Z" },
                   { ...dispatchOf("recover"), AT: "2026-11-01T00:00:00Z" }];
  /** What the kit reads of a run's own options. */
  const ownOf = (run: ReturnType<typeof parse>) =>
    [run.record, run.edits.length, run.replace, run.owner, run.act?.op ?? null,
     run.generation?.gen ?? null, run.confirm, run.keyring, run.at?.toISOString() ?? null];
  const wanted = (inputs: Record<string, string>) =>
    [inputs["RECORD"] || null, inputs["SET"]!.split(" ").filter(Boolean).length,
     inputs["REPLACE"] ? JSON.parse(inputs["REPLACE"]) : undefined, inputs["OWNER"] || null,
     inputs["ACT"] ? `owner.${inputs["ACT"].split("=")[0]}` : null,
     inputs["GENERATION"] ? inputs["GENERATION"].split("/")[1] : null,
     inputs["CONFIRM"] || null, inputs["KEYRING"] || null,
     inputs["AT"] ? new Date(inputs["AT"]).toISOString() : null];

  it("plans in a job of its own, with the Cloudflare token alone, the chosen mode's plan", () => {
    expect(plan!.environment).toBe("production");
    expect(Object.values(plan!.env ?? {}).some((value) => /secrets\.(?!CLOUDFLARE_API_TOKEN)/
      .test(value))).toBe(false);
    for (const inputs of [...offered.map(dispatchOf), ...OPTIONS]) {
      const ran = invoked(plan!, inputs);
      expect(ran.map((each) => each.argv)).toEqual([["npm", "ci"], [
        ...KIT, "--mode", inputs["MODE"]!, "--public", ...PLAN, ...dispatched(inputs).slice(2)]]);
      // Each is a run the kit takes, and one that changes nothing.
      const run = parse(ran[1]!.argv.slice(KIT.length), PUBLIC, io);
      expect([run.mode, run.reach, run.apply, ...ownOf(run)])
        .toEqual([inputs["MODE"], "public", false, ...wanted(inputs)]);
    }
  });

  it("checks the pair after the plan, in production, with the E2B key alone", () => {
    expect(pair!.environment).toBe("production");
    expect(pair!.env).toBeUndefined();
    // No Python: the checks are checks.ts, on the E2B client (image.py's SDK builds alone).
    expect(invoked(pair!, {})).toEqual([
      { argv: ["npm", "ci"], env: [] },
      { argv: [...KIT, "--mode", "pair"], env: [...MODES.pair.inputs.secret] }]);
    expect(JSON.stringify(pair)).not.toMatch(/setup-python|pip install/);
  });

  it("gives the pair check longer than the checks' throwaway sandboxes live", () => {
    // The probes' own plan, as checks.ts runs it: each sandbox's lifetime, one after another.
    const out = mkdtempSync(join(tmpdir(), "ta-plan-"));
    try {
      bundles(out);
      const probes = JSON.parse(execFileSync(process.execPath, [join(out, BUNDLES.probes), "plan"],
                                             { encoding: "utf8" })) as
        { sandboxes: { timeout_s: number }[] };
      expect(probes.sandboxes).toHaveLength(3);
      expect(Number(pair!["timeout-minutes"]) * 60)
        .toBeGreaterThan(probes.sandboxes.reduce((sum, each) => sum + each.timeout_s, 0));
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });

  it("applies in production the mode's own run", () => {
    expect(apply!.environment).toBe("production");
    // Its token reads only: the canary after each mode lists the off-account copy's nights.
    expect(apply!.permissions).toEqual({ contents: "read", actions: "read" });
    for (const inputs of [...offered.filter((mode) => MODES[mode].applies).map(dispatchOf),
                          ...OPTIONS]) {
      const ran = invoked(apply!, inputs);
      expect(ran.map((each) => each.argv)).toEqual([["npm", "ci"], [
        ...KIT, "--mode", inputs["MODE"]!, "--yes", ...PLAN, ...dispatched(inputs).slice(2)]]);
      const run = parse(ran[1]!.argv.slice(KIT.length), ENV, io);
      expect([run.mode, run.reach, run.apply, ...ownOf(run), [...run.rotate], [...run.migrate]])
        .toEqual([inputs["MODE"], "secrets", true, ...wanted(inputs),
                  inputs["ROTATE"]!.split(",").filter(Boolean), inputs["MIGRATE"]!.split(",")
                    .filter(Boolean)]);
    }
    // The account is read from the zone, never held as an input.
    expect(PUBLIC_INPUTS).not.toContain("CLOUDFLARE_ACCOUNT_ID");
    expect(JSON.stringify(file)).not.toContain("CLOUDFLARE_ACCOUNT_ID");
  });

  it("hands the apply job this run's plan, the file the plan job wrote, before it applies",
     () => {
    const step = (job: Job, action: string) =>
      job.steps.findIndex((each) => each.uses?.startsWith(`actions/${action}@`));
    const kit = (job: Job) => job.steps.findIndex((each) => each.run?.includes(KIT.at(-1)!));
    const [kept, read] = [plan!.steps[step(plan!, "upload-artifact")]!,
                          apply!.steps[step(apply!, "download-artifact")]!];
    // The plan job keeps the file it wrote, and fails with none; the apply job reads it back
    // where its kit reads it, before the kit runs, from this run alone (no run-id).
    expect([kit(plan!) < step(plan!, "upload-artifact"),
            step(apply!, "download-artifact") < kit(apply!)]).toEqual([true, true]);
    expect(kept.with).toMatchObject({ name: read.with!["name"], "if-no-files-found": "error" });
    expect([String(kept.with!["path"]).replace("${{ runner.temp }}", RUNNER_TEMP),
            `${String(read.with!["path"]).replace("${{ runner.temp }}", RUNNER_TEMP)}/` +
            "deploy-plan.json"]).toEqual([PLAN[1], PLAN[1]]);
    expect(Object.keys(read.with!).sort()).toEqual(["name", "path"]);
  });

  it("hands the plan and apply jobs exactly what their rows read", () => {
    // The dispatch's own inputs, which both jobs pass to the kit.
    const dispatch = DISPATCH;
    // The platform canary's own inputs, so Fact 11's canary runs after each mode.
    const canary = Object.values(CANARY_INPUTS);
    const rows = offered.map((mode) => MODES[mode]);
    const each = (names: readonly string[]) => [...new Set(names)].sort();
    const publics = each(rows.flatMap((row) => row.inputs.public));
    const secrets = each(rows.filter((row) => row.applies).flatMap((row) =>
      [...row.inputs.secret, ...row.inputs.optional, ...canary]).filter((name) =>
      !publics.includes(name)));
    // Every secret a config requires is among them: a deploy reads each.
    expect(SECRET_INPUTS.filter((name) => !secrets.includes(name))).toEqual([]);
    expect(Object.keys(plan!.env!).sort()).toEqual(each([...publics, ...dispatch]));
    expect(Object.keys(apply!.env!).sort()).toEqual(each([...publics, ...secrets, ...dispatch]));
    for (const name of secrets) {
      expect([name, apply!.env![name]]).toEqual([name, `\${{ secrets.${name} }}`]);
    }
    for (const env of [plan!.env!, apply!.env!]) {
      for (const name of publics) {
        expect([name, env[name]]).toEqual([name, expect.stringMatching(
          new RegExp(`^\\$\\{\\{ (secrets|vars)\\.${name} \\}\\}$`))]);
      }
      for (const name of dispatch) {
        expect([name, env[name]]).toEqual([name, `\${{ inputs.${name.toLowerCase()} }}`]);
      }
    }
  });
});

/** URLs the doors' routes cover, and the last, which they do not. */
const PROBES = [`${HOST}/mcp`, `${HOST}/mcp?x=1`, `${HOST}/api/tools/a`,
                `${HOST}/.well-known/oauth-protected-resource/mcp`,
                `${HOST}/.well-known/oauth-authorization-server`,
                `${HOST}/app/api/keys`, `${HOST}/oauth/token`,
                `${HOST}/authorize`, `${HOST}/hooks/inbound/x`,
                `${HOST}/hooks/e2b/lifecycle`,
                `${HOST}/app`, `${HOST}/app?upgrade=1`, `${HOST}/site/x`];

/** The Worker Cloudflare sends `path` to: the most specific matching pattern's. */
function servedBy(routes: Iterable<Route>, path: string): string | undefined {
  const stem = (route: Route) => route.pattern.replace(/\*$/, "").length;
  let best: Route | undefined;
  for (const route of routes) {
    if (routeTakes(route.pattern, path) && (best === undefined || stem(route) > stem(best))) {
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

/** A write of a KV value, as the control record's is: the event the kit's KV puts log. */
const CONTROL_WRITE = `cf PUT /accounts/${ACCOUNT}/storage/kv/`;
/** Whether `event` writes the control record itself, in whichever namespace it is. */
const writesControl = (event: string) => event.startsWith(CONTROL_WRITE)
  && event.endsWith(`/values/${encodeURIComponent(CONTROL_KEY)}`);

/** The rollout record's line of a deploy's plan. */
const RECORDED = `record each Worker's version as this run's rollout record in ${
  NAMESPACES["CONTROL"]}, before its first deploy: roll back with --mode rollback --record ` +
  "<this run's id>";

/** A fresh dark run's plan, in order: what the founder's first `dark` changes. */
const DARK_FRESH = [
  "the migration locks pin every migration the owner and sandbox Workers ship",
  ISSUED,
  `E2B's ready build of ${TEMPLATE}: present`,
  `the pair ${PAIR}'s standing checks in this E2B team: passed`,
  `WorkOS redirect URI ${UPSTREAM_CALLBACK}: registered`,
  `WorkOS's authorize of the doors' client and ${UPSTREAM_CALLBACK}: it takes them, sending ` +
  "the browser to auth.test's sign-in",
  ...Object.entries(NAMESPACES).map(([binding, title]) =>
    `create the KV namespace ${title} (${binding})`),
  ...BUCKETS.map((name) => `create the R2 bucket ${name}`),
  `lock every object of the R2 bucket ${OPERATOR_NAMES.bucket} for 365 days`,
  ...QUEUES.map((name) => `create the queue ${name}`),
  ...Object.entries(SECRETS).flatMap(([worker, names]) =>
    names.map((name) => `set the secret ${name} on ${NAMES[worker]} with its version${
      OPTIONAL.includes(name) ? " if the run is given it: it is" : ""}`)),
  RECORDED,
  ...WORKERS.map((worker) => `${NAMES[worker]}: deployed by this run first, so a rollback stops ` +
    "at it"),
  ...WORKERS.map((worker) => `deploy ${NAMES[worker]}, touching no route`),
  "write the control record closed (outside off, canary none, inbound off, the founder's " +
  "tier numbers): the API answered there is none",
  `E2B lifecycle webhook ${E2B_HOOK.url}: registered, disabled`,
];

/** A routes run's plan over a held account: the cutover's. */
const ROUTES_HELD = [
  "the migration locks pin every migration the owner and sandbox Workers ship",
  ISSUED,
  `E2B's ready build of ${TEMPLATE}: present`,
  `the pair ${PAIR}'s standing checks in this E2B team: passed`,
  `record the zone's ${LIVE.length} routes and the handover's 10 changes, the egress ` +
  "domain (none) and E2B's lifecycle webhook as this run's route record in " +
  `${NAMESPACES["CONTROL"]}, before any change: hand back with --mode handback --record ` +
  "<this run's id>; the webhook it records: disabled",
  `WorkOS redirect URI ${UPSTREAM_CALLBACK}: present`,
  `WorkOS's authorize of the doors' client and ${UPSTREAM_CALLBACK}: it takes them, sending ` +
  "the browser to auth.test's sign-in",
  RECORDED,
  ...WORKERS.map((worker) => `${NAMES[worker]}: serving before-${worker} (${
    HELD_SERVING[NAMES[worker]!]![0]!.tag}); a rollback takes it back there`),
  ...WORKERS.map((worker) => `upload and deploy a version of ${NAMES[worker]}, touching no route`),
  ...["mcp*", "api*", "app*"].map((path) =>
    `move the route ${HOST}/${path} from tinyassets-mcp-proxy to ${DOORS}`),
  `add the route ${HOST}/hooks/inbound/* for ${DOORS}`,
  `add the route ${HOST}/hooks/e2b/* for ${DOORS}`,
  `add the route ${HOST}/authorize* for ${DOORS}`,
  `add the route ${HOST}/oauth/* for ${DOORS}`,
  `add the route ${HOST}/.well-known/oauth-* for ${DOORS}`,
  `delete tinyassets-mcp-proxy's narrower route ` +
  `${HOST}/.well-known/oauth-protected-resource*, its paths now ${DOORS}'s`,
  `delete tinyassets-mcp-proxy's narrower route ` +
  `${HOST}/.well-known/oauth-authorization-server*, its paths now ${DOORS}'s`,
  `add the custom domain ${EGRESS_HOST} for ${DOORS} (wrangler triggers deploy)`,
  "record in deploy:routes:local-20261107T120000Z the DNS records the custom domain made: AAAA",
  `E2B lifecycle webhook ${E2B_HOOK.url}, after the routes: present, enabled set, enabled`,
];
