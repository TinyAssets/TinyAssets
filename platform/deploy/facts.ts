/**
 * The platform's deploy facts, each read from the one place it is written: the six Workers'
 * `wrangler.jsonc` (names, bindings, required secrets, buckets, queues, and so the deploy
 * order), `platform.json` (what no config can say, each entry with its reason),
 * `doors/shared-apps.json` (the shared apps' signing secrets) and `contract/identity.json`
 * (the doors' public identity). The deploy kit, its workflow's guard, the environment guard
 * and the founder's setup read them here or from those files; none holds a list of its own.
 *
 * Node runs this file as it is (type stripping), as the kit does. Run with `--import`, it prints
 * the import job's names (`IMPORT_NAMES`) as JSON, for the job's bundle.
 */
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { PLATFORM } from "../ci/paths.ts";
import { type Config, readWrangler, workerFolders } from "../ci/wrangler.ts";
import identity from "../contract/identity.json" with { type: "json" };
import sharedApps from "../doors/shared-apps.json" with { type: "json" };
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

/** The doors Worker, which the routes are handed to. */
export const DOORS = NAMES["doors"]!;

/** The KV namespaces, by binding, and their titles in the account (`platform.json`). */
export const NAMESPACES: Readonly<Record<string, string>> = Object.freeze(
  platform.namespaces.titles);

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

/**
 * The shared apps' signing secrets (`doors/shared-apps.json`): the doors hold each when it is
 * given, and without one that app's door refuses loudly, so no config requires them.
 */
export const OPTIONAL: readonly string[] = Object.freeze(sharedApps.apps.map((app) => app.secret));

/** Each Worker's secrets, in the order the kit sets them (`platform.json`). */
export const SECRETS: Readonly<Record<string, readonly string[]>> = Object.freeze(
  Object.fromEntries(platform.secret_order.workers.map((worker) =>
    [worker, Object.freeze([...required(worker), ...(worker === "doors" ? OPTIONAL : [])])])));

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

/** The zone and origin the doors serve (`identity.json`). */
export const ZONE = identity.host;
export const ORIGIN = `https://${ZONE}`;
/** The doors' public routes (mode `routes`, the cutover's), on the zone. */
export const ROUTES: readonly string[] = Object.freeze(
  identity.routes.map((path) => `${ZONE}${path}`));
/** The sandbox's egress host, a custom domain of the doors Worker (mode `routes`). */
export const EGRESS_DOMAIN = identity.egress;
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
 * The live names the import job reaches, each read where it is written: the staging bucket and
 * the import queue the doors bind, the dead-letter queue of the doors' consumer of that queue,
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
    queue,
    dead_letter_queue: deadLetters,
    files_bucket: boundAs("files", configOf("files")["r2_buckets"] as Buckets, "BLOBS").bucket_name,
    directory: platform.namespaces.titles.DIRECTORY,
    final_backup_bucket: platform.buckets.unbound.final_backup,
  });
})();

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.slice(2).join(" ") !== "--import") {
    throw new Error("facts.ts takes one flag, --import: the import job's names, as JSON");
  }
  process.stdout.write(`${JSON.stringify(IMPORT_NAMES)}\n`);
}
