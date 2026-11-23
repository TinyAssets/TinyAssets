/**
 * The platform's deploy facts, each read from the one place it is written: the six Workers'
 * `wrangler.jsonc` (names, bindings, required secrets, buckets, queues, Durable Object
 * migrations, and so the deploy order), `platform.json` (what no config can say, each entry
 * with its reason, and so each Worker's record, `WORKER_FACTS`), the shared apps' file it
 * names (their signing secrets) and `contract/identity.json` (the doors' public identity).
 * The deploy kit, its workflow's guard, the import job and the founder's setup read them
 * here or from those files; none holds a list of its own.
 *
 * Node runs this file as it is (type stripping), as the kit does. Run with `--import`, it prints
 * the import job's names (`IMPORT_NAMES`) as JSON, for the job's bundle.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { PLATFORM } from "../ci/paths.ts";
import { type Config, readWrangler, workerFolders } from "../ci/wrangler.ts";
import identity from "../contract/identity.json" with { type: "json" };
import { CLOCK_SECRETS } from "../contract/src/canary.ts";
import { EGRESS_HOST } from "../contract/src/egress.ts";
import {
  HOST, ORIGIN, routeTakes, UPSTREAM_CALLBACK, ZONE,
} from "../contract/src/identity.ts";
import type { ImportNames } from "../contract/src/import-names.ts";
import { OPERATOR_OPS, type OperatorOp } from "../contract/src/operator-message.ts";
import platform from "./platform.json" with { type: "json" };

/** Each Worker's checked-in config, by its folder under `platform/`. */
export const CONFIGS: ReadonlyMap<string, Config> = new Map(workerFolders().map((worker) =>
  [worker, Object.freeze(readWrangler(join(PLATFORM, worker)))]));

function configOf(worker: string): Config {
  const config = CONFIGS.get(worker);
  if (config === undefined) {
    throw new Error(`platform/${worker} holds no Worker config`);
  }
  return config;
}

/** Each Worker's script name, its config's `name`, by folder: each set, and none shared. */
export function workerNames(configs: ReadonlyMap<string, Config>):
    Readonly<Record<string, string>> {
  const names = [...configs].map(([worker, config]) => [worker, config["name"]] as const);
  const wrong = names.filter(([, name], i) => typeof name !== "string"
    || names.findIndex(([, other]) => other === name) !== i);
  if (wrong.length > 0) {
    throw new Error(`platform/${wrong[0]![0]}'s config names no Worker of its own`);
  }
  return Object.freeze(Object.fromEntries(names)) as Readonly<Record<string, string>>;
}

/** Each Worker's script name, by folder. */
export const NAMES = workerNames(CONFIGS);

/** The Worker whose script is `name`, by folder. */
function workerNamed(name: string, by: string): string {
  const worker = Object.keys(NAMES).find((each) => NAMES[each] === name);
  if (worker === undefined) {
    throw new Error(`platform/${by} binds ${name}, which is no platform Worker`);
  }
  return worker;
}

/** The Workers `worker` binds: its services, and the scripts its Durable Objects live in. */
export function boundBy(worker: string): string[] {
  const config = configOf(worker);
  const services = (config["services"] ?? []) as { service: string }[];
  const objects = ((config["durable_objects"] as { bindings?: { script_name?: string }[] }
                    | undefined)?.bindings ?? []);
  return [...new Set([...services.map((service) => service.service),
                      ...objects.flatMap((object) => object.script_name ?? [])])]
    .map((name) => workerNamed(name, worker));
}

/** The Workers in deploy order: each after every Worker it binds, ties by folder name. */
export const WORKERS: readonly string[] = (() => {
  const order: string[] = [];
  const left = [...CONFIGS.keys()];
  while (left.length > 0) {
    const next = left.find((worker) => boundBy(worker).every((bound) => order.includes(bound)));
    if (next === undefined) {
      throw new Error(`the Workers ${left.join(", ")} bind each other in a cycle`);
    }
    order.push(next);
    left.splice(left.indexOf(next), 1);
  }
  return Object.freeze(order);
})();

/** The KV namespaces, by binding, and their titles in the account (`platform.json`). */
export const NAMESPACES: Readonly<Record<string, string>> = (() => {
  const titles: Readonly<Record<string, string>> = platform.namespaces.titles;
  for (const worker of CONFIGS.keys()) {
    for (const { binding } of (configOf(worker)["kv_namespaces"] ?? []) as { binding: string }[]) {
      if (titles[binding] === undefined) {
        throw new Error(`platform/${worker} binds the KV namespace ${binding}, which ` +
                        "platform.json gives no title");
      }
    }
  }
  return Object.freeze({ ...titles });
})();

/** Each kind of storage a config names, in deploy order, each name once. */
function named(field: (config: Config) => readonly string[]): readonly string[] {
  return Object.freeze([...new Set(WORKERS.flatMap((worker) => field(configOf(worker))))]);
}

/** `retired`, the scripts the kit deletes, when none is a Worker this tree deploys (`names`). */
export function retiredWorkers(retired: readonly string[],
                               names: Readonly<Record<string, string>>): readonly string[] {
  const kept = retired.filter((name) => Object.values(names).includes(name));
  if (kept.length > 0) {
    throw new Error(`platform.json retires ${kept.join(", ")}, which a config still names`);
  }
  return Object.freeze([...retired]);
}

/**
 * The Workers the platform no longer has, by script name (`platform.json`): the kit deletes
 * each the account still holds.
 */
export const RETIRED = retiredWorkers(platform.retired.workers, NAMES);

/**
 * Each Worker's count of `changes` naming it, by folder (`platform.json` `forward`): what its
 * version's tag carries as `-f<n>`, which a rollback below it cannot follow. Each change names
 * Workers `names` holds, each once, and why; anything else throws.
 */
export function forwardCounts(changes: readonly unknown[],
                              names: Readonly<Record<string, string>>):
    Readonly<Record<string, number>> {
  const counts = Object.fromEntries(Object.keys(names).map((worker) => [worker, 0]));
  for (const [at, change] of changes.entries()) {
    const { workers, why } = (change ?? {}) as { workers?: unknown; why?: unknown };
    if (!Array.isArray(workers) || workers.length === 0 || new Set(workers).size !== workers.length
        || !workers.every((worker) => typeof worker === "string" && Object.hasOwn(counts, worker))
        || typeof why !== "string" || why.trim() === "") {
      throw new Error(`platform.json's forward change ${at + 1} names no Worker of this tree, ` +
                      "each once, with why");
    }
    for (const worker of workers as string[]) {
      counts[worker]! += 1;
    }
  }
  return Object.freeze(counts);
}

/** Each Worker's forward count, by folder (`forwardCounts`). */
export const FORWARD = forwardCounts(platform.forward.changes, NAMES);

/** The stages a deploy takes each Worker through (`platform.json` `stages`). */
export interface Stages {
  /** The shares of traffic its new version serves in turn, rising, the last 100. */
  readonly shares: readonly number[];
  /** How long the doors' share below 100 is held before its smoke. */
  readonly soakMs: number;
}

/**
 * `stages`, as `platform.json` writes them, read: shares rising from 0 or more, the last 100,
 * and a soak of whole seconds above 0. Anything else throws.
 */
export function stagesOf(stages: { readonly shares?: unknown; readonly soak_s?: unknown }):
    Stages {
  const { shares, soak_s: soak } = stages;
  if (!Array.isArray(shares) || shares.at(-1) !== 100
      || !shares.every((share, at) => typeof share === "number" && share >= 0
        && (at === 0 || share > shares[at - 1]))
      || !Number.isInteger(soak) || Number(soak) <= 0) {
    throw new Error("platform.json's stages are no shares rising from 0 up to 100 with a soak " +
                    "of whole seconds above 0");
  }
  return Object.freeze({ shares: Object.freeze([...shares as number[]]),
                         soakMs: Number(soak) * 1000 });
}

/** The deploy's stages (`stagesOf`). */
export const STAGES = stagesOf(platform.stages);

/** The R2 buckets the kit makes: the ones the Workers bind, then the unbound (`platform.json`). */
export const BUCKETS: readonly string[] = Object.freeze([...new Set([
  ...named((config) =>
    ((config["r2_buckets"] ?? []) as { bucket_name: string }[]).map((bucket) => bucket.bucket_name)),
  ...Object.values(platform.buckets.unbound)])]);

/**
 * The queues the kit makes: the ones the Workers send to (their producers'; a consumer only
 * reads), then each consumer's dead-letter queue, where the queue itself sends what its
 * consumer gives up on (the import window's, `doors/wrangler.jsonc`).
 */
export const QUEUES = named((config) => {
  const queues = config["queues"] as { producers?: { queue: string }[];
                                       consumers?: { dead_letter_queue?: string }[] } | undefined;
  return [...(queues?.producers ?? []).map((producer) => producer.queue),
          ...(queues?.consumers ?? []).flatMap((consumer) =>
            consumer.dead_letter_queue === undefined ? [] : [consumer.dead_letter_queue])];
});

/** A Worker's required secrets: its config's `secrets.required`. */
function required(worker: string): readonly string[] {
  return ((configOf(worker)["secrets"] as { required?: string[] } | undefined)?.required ?? []);
}

/** What `platform.json` gives one Worker, by its folder (`workers`). */
interface Given {
  readonly identity?: boolean;
  readonly shared_apps?: string;
  readonly clock?: boolean;
}

/** What the kit gives one Worker beyond its checked-in config, by folder. */
export interface WorkerFacts {
  /**
   * The secrets it holds when each is given: the shared apps' signing secrets (its
   * `shared_apps` file) and the canary clock's (`clock`, `contract/canary.json`).
   */
  readonly optional: readonly string[];
  /** Whether it is deployed with the owner issuer and WorkOS's record (`identity`). */
  readonly upstream: boolean;
  /** The routes mode `routes` hands it: identity.json's, and egress.json's custom domain. */
  readonly routes: readonly Readonly<Record<string, string | boolean>>[];
  /** Whether its config declares Durable Object migrations, so `--migrate` may name it. */
  readonly migrates: boolean;
}

/** The signing secrets of the shared apps in `worker`'s file `file`. */
function sharedSecrets(worker: string, file: string): readonly string[] {
  const { apps } = JSON.parse(readFileSync(join(PLATFORM, worker, file), "utf8")) as
    { apps?: { secret?: unknown }[] };
  if (!Array.isArray(apps) || !apps.every((app) => typeof app?.secret === "string")) {
    throw new Error(`platform/${worker}/${file} lists no shared apps, each with its secret`);
  }
  return Object.freeze(apps.map((app) => app.secret as string));
}

const GIVEN: Readonly<Record<string, Given>> = platform.workers.folders;

/** The Worker that serves `contract/identity.json`, by folder (`platform.json`): exactly one. */
export const IDENTITY = (() => {
  for (const worker of Object.keys(GIVEN)) {
    configOf(worker);
  }
  const serving = Object.keys(GIVEN).filter((worker) => GIVEN[worker]!.identity === true);
  if (serving.length !== 1) {
    throw new Error(`platform.json gives the identity to ${serving.length} Workers, not one`);
  }
  return serving[0]!;
})();

/** The doors Worker, which serves the identity: the routes are handed to it. */
export const DOORS = NAMES[IDENTITY]!;

/** The doors' public routes (mode `routes`, the cutover's), on the doors' host. */
export const ROUTES: readonly string[] = Object.freeze(
  identity.routes.map((path) => `${HOST}${path}`));

/** One rule of the edge kill (`platform.json` `edge_kill`), as the kit keeps it on the zone. */
export interface EdgeRule {
  /** What it blocks, as mode edge's `--kill` names it. */
  readonly name: "outside" | "all";
  /** Its reference among the zone's custom rules, by which the kit finds it. */
  readonly ref: string;
  /** Its name in the dashboard. */
  readonly description: string;
  readonly expression: string;
}

/**
 * The edge kill's rules for the identity's `routes` on `host` and the egress host `egress`,
 * from `given` (`platform.json` `edge_kill`): `outside` the routes it names, `all` every route
 * and the egress host. Each route is in `outside` or `left`, never both and never neither, and
 * each ends in its one `*`, so a request whose path starts with the rest is a URL the route
 * takes (`routeTakes`: Cloudflare matches the URL, whose query follows the path). A host is
 * held in lower case, as the doors read it (`new URL`); a path as it is, as the doors match
 * it. No regex: the zone's plan may lack `matches`. Anything else throws.
 */
export function edgeRules(routes: readonly string[], host: string, egress: string,
                          given: { readonly outside: readonly string[];
                                   readonly left: readonly string[] }): readonly EdgeRule[] {
  const both = given.outside.filter((path) => given.left.includes(path));
  const unnamed = routes.filter((path) => !given.outside.includes(path)
    && !given.left.includes(path));
  const unknown = [...given.outside, ...given.left].filter((path) => !routes.includes(path));
  const unsafe = routes.filter((path) => !/^\/[^"\\*]*\*$/.test(path));
  if (both.length + unnamed.length + unknown.length + unsafe.length > 0) {
    throw new Error("platform.json's edge_kill names each identity route once, in outside or " +
                    `left, and only those (in both: ${both.join(" ") || "none"}; in neither: ${
                      unnamed.join(" ") || "none"}; no route: ${unknown.join(" ") || "none"}), ` +
                    `each a path ending in its one * (not so: ${unsafe.join(" ") || "none"})`);
  }
  const on = (name: string) => `lower(http.host) eq "${name.toLowerCase()}"`;
  const paths = (list: readonly string[]) => list.map((path) =>
    `starts_with(http.request.uri.path, "${path.slice(0, -1)}")`).join(" or ");
  const rule = (name: EdgeRule["name"], expression: string): EdgeRule => Object.freeze({
    name, ref: `tinyassets-kill-${name}`, description: `TinyAssets kill: ${name}`, expression });
  return Object.freeze([
    rule("outside", `${on(host)} and (${paths(given.outside)})`),
    rule("all", `(${on(host)} and (${paths(routes)})) or ${on(egress)}`)]);
}

/** The edge kill's rules for this tree's identity (`edgeRules`). */
export const EDGE_RULES = edgeRules(identity.routes, HOST, EGRESS_HOST, platform.edge_kill);

/** Each Worker's record, by folder: what `platform.json` gives it, and what its config says. */
export const WORKER_FACTS: Readonly<Record<string, WorkerFacts>> = Object.freeze(
  Object.fromEntries([...CONFIGS].map(([worker, config]) => {
    const given = GIVEN[worker] ?? {};
    const serves = worker === IDENTITY;
    return [worker, Object.freeze({
      optional: Object.freeze([
        ...given.shared_apps === undefined ? [] : sharedSecrets(worker, given.shared_apps),
        ...given.clock === true ? CLOCK_SECRETS : []]),
      upstream: serves,
      routes: Object.freeze(serves ? [...ROUTES.map((pattern) => ({ pattern, zone_name: ZONE })),
                                      { pattern: EGRESS_HOST, custom_domain: true }] : []),
      migrates: ((config["migrations"] ?? []) as unknown[]).length > 0,
    })];
  })));

/** The Workers `--migrate` may name: those whose config declares Durable Object migrations. */
export const MIGRATES: readonly string[] = Object.freeze(
  WORKERS.filter((worker) => WORKER_FACTS[worker]!.migrates));

/** Every optional secret, each on the Worker that holds it. */
export const OPTIONAL: readonly string[] = Object.freeze(
  WORKERS.flatMap((worker) => WORKER_FACTS[worker]!.optional));

/** Each Worker's secrets, in the order the kit sets them (`platform.json`). */
export const SECRETS: Readonly<Record<string, readonly string[]>> = Object.freeze(
  Object.fromEntries(platform.secret_order.workers.map((worker) =>
    [worker, Object.freeze([...required(worker), ...WORKER_FACTS[worker]!.optional])])));

/** Every secret a config requires, each once: what only the apply job holds. */
export const SECRET_INPUTS: readonly string[] = Object.freeze(
  [...new Set(Object.keys(SECRETS).flatMap(required))]);

/** The secrets more than one Worker requires: each set on all of them, or none. */
export const SHARED: readonly string[] = Object.freeze(SECRET_INPUTS.filter((name) =>
  WORKERS.filter((worker) => required(worker).includes(name)).length > 1));

/** The secrets the founder's setup makes, each by its kind (`platform.json`). */
export const MADE: Readonly<Record<string, string>> = Object.freeze(platform.made.secrets);

/** The keyrings: each rotated only by adding a key. */
export const KEYRINGS: readonly string[] = Object.freeze(
  Object.keys(MADE).filter((name) => MADE[name] === "keyring"));

/** A keyring mode `rotation` sweeps: the op it asks of each owner, and whether the copy holds it. */
export interface Rotated {
  readonly op: OperatorOp;
  readonly copied: boolean;
}

/**
 * The keyrings the platform seals stored blobs under, by name (`platform.json` `keyrings`):
 * each a keyring, its op one of the operator's own `owner.` ops.
 */
export const ROTATED: Readonly<Record<string, Rotated>> = Object.freeze(Object.fromEntries(
  Object.entries(platform.keyrings.rotated).map(([name, { op, copied }]) => {
    if (!KEYRINGS.includes(name) || !Object.hasOwn(OPERATOR_OPS, op) || !op.startsWith("owner.")) {
      throw new Error(`platform.json rotates ${name} by ${op}: a keyring, by an owner op`);
    }
    return [name, Object.freeze({ op: op as OperatorOp, copied })];
  })));

/**
 * The keyrings whose values live as long as the sandboxes that carry them (`platform.json`
 * `keyrings`), each a keyring no op sweeps: mode retire reads E2B's routes instead.
 */
export const SANDBOXED: readonly string[] = Object.freeze(
  platform.keyrings.sandboxed.map((name) => {
    if (!KEYRINGS.includes(name) || Object.hasOwn(ROTATED, name)) {
      throw new Error(`platform.json reads ${name}'s values from sandboxes: a keyring no op ` +
                      "sweeps");
    }
    return name;
  }));

/** The keyrings mode `retire` takes old kids out of: those swept, and those in sandboxes. */
export const RETIRES: readonly string[] = Object.freeze([...Object.keys(ROTATED), ...SANDBOXED]);

/** Where a sign-in starts. */
export const AUTHORIZE = `${ORIGIN}${identity.signin.authorize}`;

/** The URL E2B's lifecycle webhook is sent to: the doors' hook. */
export const E2B_HOOK_URL = `${ORIGIN}${identity.hook}`;

/** The entry of `worker`'s config list `entries` that binds `binding`: loudly, when none does. */
function boundAs<T extends { binding?: string }>(worker: string, entries: readonly T[] | undefined,
                                                 binding: string): T {
  const entry = (entries ?? []).find((each) => each.binding === binding);
  if (entry === undefined) {
    throw new Error(`platform/${worker} binds no ${binding}`);
  }
  return entry;
}

type Buckets = readonly { binding: string; bucket_name: string }[] | undefined;
type Queues = { producers?: { binding?: string; queue: string }[];
                consumers?: { queue: string; dead_letter_queue?: string }[] } | undefined;

/** The bucket `worker` binds as `binding`. */
function bucketOf(worker: string, binding: string): string {
  return boundAs(worker, configOf(worker)["r2_buckets"] as Buckets, binding).bucket_name;
}

/** The dead-letter queue of the doors' consumer of the queue they send to as `binding`. */
function deadLettersOf(binding: string): string {
  const queues = configOf("doors")["queues"] as Queues;
  const queue = boundAs("doors", queues?.producers, binding).queue;
  const deadLetters = queues?.consumers?.find((each) => each.queue === queue)?.dead_letter_queue;
  if (deadLetters === undefined) {
    throw new Error(`platform/doors consumes ${queue} with no dead-letter queue`);
  }
  return deadLetters;
}

/**
 * Each locked bucket's days, by its name (`platform.json` `bucket_locks`), each the bucket a
 * Worker binds by the binding it names: a binding no config has fails loudly.
 */
export const BUCKET_LOCKS: Readonly<Record<string, number>> = Object.freeze(Object.fromEntries(
  Object.entries(platform.bucket_locks.days).map(([binding, days]) => {
    const worker = WORKERS.find((each) => ((configOf(each)["r2_buckets"] ?? []) as
      { binding: string }[]).some((bucket) => bucket.binding === binding));
    if (worker === undefined) {
      throw new Error(`platform.json locks the bucket bound as ${binding}, which no config binds`);
    }
    return [bucketOf(worker, binding), days];
  })));

/**
 * The operator channel's names the kit's sending side reads (`operator.ts`) beside the queue's
 * own (`QUEUES.OPERATOR`): the bucket its answers are in, and the dead-letter queue a message
 * the doors gave up on is left in.
 */
export const OPERATOR_NAMES = Object.freeze({ bucket: bucketOf("doors", "OPERATOR_LOG"),
                                              dead_letter_queue: deadLettersOf("OPERATOR") });

/** The files Worker's bucket: every owner's chunks and generations, and the copy's source. */
export const FILES_BUCKET = bucketOf("files", "BLOBS");

/**
 * The live names the import job reaches, each read where it is written: the staging bucket the
 * doors bind, the dead-letter queue of the doors' consumer of the queue they bind as `IMPORTS`,
 * the files Worker's bucket, the directory's namespace title, the final backup's bucket
 * (`platform.json`), and the doors' Worker, whose `IMPORT_KEY` the window's close deletes. The
 * job runs on its import host, away from this tree, so the window's bundle step writes them
 * beside the job (`--import`).
 */
export const IMPORT_NAMES = Object.freeze({
  staging_bucket: bucketOf("doors", "IMPORT_STAGING"),
  dead_letter_queue: deadLettersOf("IMPORTS"),
  files_bucket: FILES_BUCKET,
  directory: platform.namespaces.titles.DIRECTORY,
  final_backup_bucket: platform.buckets.unbound.final_backup,
  doors: DOORS,
} satisfies ImportNames);

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.slice(2).join(" ") !== "--import") {
    throw new Error("facts.ts takes one flag, --import: the import job's names, as JSON");
  }
  process.stdout.write(`${JSON.stringify(IMPORT_NAMES)}\n`);
}
