/**
 * The platform's deploy facts, each read from the one place it is written: the six Workers'
 * `wrangler.jsonc` (names, bindings, required secrets, buckets, queues, Durable Object
 * migrations, and so the deploy order), `platform.json` (what no config can say, each entry
 * with its reason, and so each Worker's record, `WORKER_FACTS`), the shared apps' file it
 * names (their signing secrets) and `contract/identity.json` (the doors' public identity).
 * The deploy kit, its workflow's guard, the environment guard and the founder's setup read
 * them here or from those files; none holds a list of its own.
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
import type { ImportNames } from "../contract/src/import-names.ts";
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

/** Each Worker's script name, its config's `name`, by folder. */
export const NAMES: Readonly<Record<string, string>> = Object.freeze(Object.fromEntries(
  [...CONFIGS].map(([worker, config]) => [worker, String(config["name"])])));

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
}

/** What the kit gives one Worker beyond its checked-in config, by folder. */
export interface WorkerFacts {
  /** The shared apps' signing secrets it holds when each is given (its `shared_apps` file). */
  readonly optional: readonly string[];
  /** Whether it is deployed with the owner issuer and WorkOS's record (`identity`). */
  readonly upstream: boolean;
  /** The routes mode `routes` hands it: identity.json's, and the egress custom domain. */
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

/** The zone and origin the doors serve (`identity.json`). */
export const ZONE = identity.host;
export const ORIGIN = `https://${ZONE}`;
/** The doors' public routes (mode `routes`, the cutover's), on the zone. */
export const ROUTES: readonly string[] = Object.freeze(
  identity.routes.map((path) => `${ZONE}${path}`));
/** The sandbox's egress host, a custom domain of the doors Worker (mode `routes`). */
export const EGRESS_DOMAIN = identity.egress;

/** Each Worker's record, by folder: what `platform.json` gives it, and what its config says. */
export const WORKER_FACTS: Readonly<Record<string, WorkerFacts>> = Object.freeze(
  Object.fromEntries([...CONFIGS].map(([worker, config]) => {
    const given = GIVEN[worker] ?? {};
    const serves = worker === IDENTITY;
    return [worker, Object.freeze({
      optional: given.shared_apps === undefined ? Object.freeze([])
        : sharedSecrets(worker, given.shared_apps),
      upstream: serves,
      routes: Object.freeze(serves ? [...ROUTES.map((pattern) => ({ pattern, zone_name: ZONE })),
                                      { pattern: EGRESS_DOMAIN, custom_domain: true }] : []),
      migrates: ((config["migrations"] ?? []) as unknown[]).length > 0,
    })];
  })));

/** The Workers `--migrate` may name: those whose config declares Durable Object migrations. */
export const MIGRATES: readonly string[] = Object.freeze(
  WORKERS.filter((worker) => WORKER_FACTS[worker]!.migrates));

/** Every shared app's signing secret, each on the Worker whose file lists it. */
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

/** Where a sign-in starts, and where the upstream returns it. */
export const AUTHORIZE = `${ORIGIN}${identity.signin.authorize}`;
export const CALLBACK = `${ORIGIN}${identity.signin.callback}`;

/** Whether the route `pattern` takes the URL `url` (host and path, no scheme). */
export function takes(pattern: string, url: string): boolean {
  return pattern.endsWith("*") ? url.startsWith(pattern.slice(0, -1)) : url === pattern;
}

/**
 * The sign-in probe's routes (mode `probe`): the routes that take the sign-in's own two paths,
 * which no Worker serves before the cutover, so handing them to the doors early serves no
 * user.
 */
export const PROBE_ROUTES: readonly string[] = Object.freeze(ROUTES.filter((pattern) =>
  [AUTHORIZE, CALLBACK].some((url) => takes(pattern, url.slice("https://".length)))));
/** The doors' own app's sign-in: its client, redirect and resource. */
export const FIRST_PARTY = Object.freeze({
  client: `${ORIGIN}${identity.first_party.client}`,
  redirect: `${ORIGIN}${identity.first_party.redirect}`,
  resource: `${ORIGIN}${identity.first_party.resource}`,
});
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

/**
 * The live names the import job reaches, each read where it is written: the staging bucket the
 * doors bind, the dead-letter queue of the doors' consumer of the queue they bind as `IMPORTS`,
 * the files Worker's bucket, the directory's namespace title and the final backup's bucket
 * (`platform.json`). The job runs on its import host, away from this tree, so the window's
 * bundle step writes them beside the job (`--import`).
 */
export const IMPORT_NAMES = (() => {
  const doors = configOf("doors");
  const queues = doors["queues"] as { producers?: { binding?: string; queue: string }[];
                                      consumers?: { queue: string; dead_letter_queue?: string }[] }
                                    | undefined;
  const queue = boundAs("doors", queues?.producers, "IMPORTS").queue;
  const deadLetters = queues?.consumers?.find((each) => each.queue === queue)?.dead_letter_queue;
  if (deadLetters === undefined) {
    throw new Error(`platform/doors consumes ${queue} with no dead-letter queue`);
  }
  return Object.freeze({
    staging_bucket: boundAs("doors", doors["r2_buckets"] as Buckets, "IMPORT_STAGING").bucket_name,
    dead_letter_queue: deadLetters,
    files_bucket: boundAs("files", configOf("files")["r2_buckets"] as Buckets, "BLOBS").bucket_name,
    directory: platform.namespaces.titles.DIRECTORY,
    final_backup_bucket: platform.buckets.unbound.final_backup,
  } satisfies ImportNames);
})();

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.slice(2).join(" ") !== "--import") {
    throw new Error("facts.ts takes one flag, --import: the import job's names, as JSON");
  }
  process.stdout.write(`${JSON.stringify(IMPORT_NAMES)}\n`);
}
