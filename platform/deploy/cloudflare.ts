/**
 * Cloudflare, as the deploy kit and the import window speak to it over its REST API with the
 * account's token: the zone and its account, the account's Workers and their secrets' names,
 * KV namespaces, keys and values, R2 buckets, queues with their messages and consumers, and the
 * zone's Worker routes, DNS records and Workers custom domains. Every call is checked: a failed
 * listing or read throws, never reads as "absent", and a body that is not JSON throws. Every
 * listing is read to its last page.
 * The kit's other Cloudflare tool is wrangler (`deploy.ts`), which deploys and sets secrets.
 *
 * Node runs this file as it is (type stripping), as the kit does.
 */
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

const CF_API = "https://api.cloudflare.com/client/v4";

/** A Cloudflare account's id: 32 hex. */
export const ACCOUNT_ID = /^[0-9a-f]{32}$/;

/** Cloudflare's answer envelope, as every API but a KV value's answers. */
interface Envelope {
  readonly success?: unknown;
  readonly result?: unknown;
  readonly errors?: readonly { readonly code?: number }[];
  /** A listing's paging: a cursor, or the page, its size and the listing's whole count. */
  readonly result_info?: { readonly cursor?: unknown; readonly page?: unknown;
                           readonly per_page?: unknown; readonly total_count?: unknown };
}

/** One answer: its status and its JSON body. */
interface Answer {
  readonly status: number;
  readonly body: unknown;
}

function envelope(answer: Answer): Envelope {
  return typeof answer.body === "object" && answer.body !== null ? answer.body as Envelope : {};
}

function codes(answer: Answer): string {
  return JSON.stringify((envelope(answer).errors ?? []).map((error) => error.code ?? 0));
}

/**
 * The query that reads a listing's next page, or null at its last: the cursor it answered,
 * or the next page number while `page * per_page` is short of `total_count` (wrangler's own
 * two rules, `hasCursor` and `hasMorePages`, in its `fetchListResult`).
 */
function nextPage(info: Envelope["result_info"]): string | null {
  if (typeof info?.cursor === "string" && info.cursor !== "") {
    return `cursor=${encodeURIComponent(info.cursor)}`;
  }
  const { page, per_page: size, total_count: total } = info ?? {};
  return typeof page === "number" && typeof size === "number" && typeof total === "number"
    && page * size < total ? `page=${page + 1}` : null;
}

/** A listing's rows: an array of objects, or a throw naming `what`. */
function rows(result: unknown, what: string): Record<string, unknown>[] {
  if (!Array.isArray(result)
      || !result.every((row) => typeof row === "object" && row !== null)) {
    throw new Error(`${what} failed (its answer holds no listing)`);
  }
  return result as Record<string, unknown>[];
}

/** An HTTP pull consumer's settings, as the Queues API takes them. */
export interface PullSettings {
  readonly batch_size: number;
  readonly max_retries: number;
  readonly visibility_timeout_ms: number;
}

/** A message pulled from a queue: its id, its body as the API hands it, and its lease. */
export interface Pulled {
  readonly id: string;
  readonly body: unknown;
  readonly lease: string;
}

/** A Worker route on the zone, as Cloudflare's routes API lists it. */
export interface Route {
  readonly id: string;
  readonly pattern: string;
  readonly script?: string;
}

/**
 * One change of the zone's routes, in order, each to the Worker `script` (null: none): a
 * handover's and a handback's alike. `from` is the Worker whose route it changes.
 */
export type RouteChange =
  | { readonly method: "POST"; readonly pattern: string; readonly script: string | null }
  | { readonly method: "PUT"; readonly id: string; readonly pattern: string;
      readonly script: string | null; readonly from: string }
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
 * The handover of `patterns` to `script`, in order: each pattern another route holds moved
 * in place (`PUT`), each missing one added (`POST`), and after each, every other route's
 * narrower pattern deleted, so its paths fall to `script` with no gap. A wider pattern of
 * another Worker is left as it is, and a narrower one of `script` under it may take nothing:
 * Cloudflare's known issue "Route specificity" sends `x/*` URLs to another Worker's `x*`. So
 * `patterns` name such a base whole, and it moves in place.
 */
export function handover(routes: readonly Route[], script: string,
                         patterns: readonly string[]): RouteChange[] {
  const steps: RouteChange[] = [];
  const deleted = new Set<string>();
  for (const pattern of patterns) {
    const held = routes.find((route) => route.pattern === pattern);
    if (held === undefined) {
      steps.push({ method: "POST", pattern, script });
    } else if (held.script !== script) {
      steps.push({ method: "PUT", id: held.id, pattern, script,
                   from: held.script ?? "no Worker" });
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

/** A handover's change to `script`, as the plan says it. */
export function describeStep(step: RouteChange, script: string): string {
  return step.method === "PUT" ? `move the route ${step.pattern} from ${step.from} to ${script}`
    : step.method === "POST" ? `add the route ${step.pattern} for ${script}`
    : `delete ${step.from}'s narrower route ${step.pattern}, its paths now ${script}'s`;
}

/** A DNS record as the kit reads it: its id, type and content. */
export interface DnsRecord {
  readonly id: string;
  readonly type: string;
  readonly content: string;
}

/** The DNS record types a Workers custom domain's own record takes the host's place of. */
export const ADDRESS: readonly string[] = Object.freeze(["A", "AAAA", "CNAME"]);

/** A route as a record keeps it: its pattern, and the Worker it points to (null: none). */
export interface Kept {
  readonly pattern: string;
  readonly script: string | null;
}

/** One change a handover made, as its record keeps it: the method and the pattern. */
export interface Moved {
  readonly method: RouteChange["method"];
  readonly pattern: string;
}

/**
 * The handback of a handover to `script` that `moved` the zone's routes from `before`, on the
 * zone's `routes` now: the reverse of the handover, each change checked against the zone so a
 * rerun changes only what is not back yet. In this order, so no path the record served is
 * ever served by nothing:
 * 1. each pattern the handover deleted, added back with its record's Worker (it takes its
 *    paths back from `script`'s wider pattern);
 * 2. each pattern the handover moved in place, moved back in place;
 * 3. each pattern the handover added, deleted, and only while it is still `script`'s: its
 *    paths fall to whatever wider pattern the record holds, as before the handover.
 * Nothing else is touched: no route the handover did not change, and no route it added that
 * now points elsewhere (`unlike` then names it).
 */
export function handback(before: readonly Kept[], moved: readonly Moved[],
                         routes: readonly Route[], script: string): RouteChange[] {
  const steps: RouteChange[] = [];
  const now = (pattern: string) => routes.find((route) => route.pattern === pattern);
  const restore = (pattern: string) => {
    const wanted = before.find((route) => route.pattern === pattern)!.script;
    const held = now(pattern);
    if (held === undefined) {
      steps.push({ method: "POST", pattern, script: wanted });
    } else if ((held.script ?? null) !== wanted) {
      steps.push({ method: "PUT", id: held.id, pattern, script: wanted,
                   from: held.script ?? "no Worker" });
    }
  };
  for (const each of moved.filter((one) => one.method === "DELETE")) {
    restore(each.pattern);
  }
  for (const each of moved.filter((one) => one.method === "PUT")) {
    restore(each.pattern);
  }
  for (const each of moved.filter((one) => one.method === "POST")) {
    const held = now(each.pattern);
    if (held !== undefined && held.script === script) {
      steps.push({ method: "DELETE", id: held.id, pattern: each.pattern, from: script });
    }
  }
  return steps;
}

/**
 * How the zone's `routes` differ from the record's: each route of `before` missing or another
 * Worker's, and each pattern the handover added still there. Empty when they are back.
 */
export function unlike(before: readonly Kept[], moved: readonly Moved[],
                       routes: readonly Route[]): string[] {
  const differences: string[] = [];
  for (const kept of before) {
    const held = routes.find((route) => route.pattern === kept.pattern);
    if (held === undefined) {
      differences.push(`${kept.pattern} is missing (the record has it ${
        kept.script ?? "no Worker"}'s)`);
    } else if ((held.script ?? null) !== kept.script) {
      differences.push(`${kept.pattern} is ${held.script ?? "no Worker"}'s (the record has it ${
        kept.script ?? "no Worker"}'s)`);
    }
  }
  for (const each of moved.filter((one) => one.method === "POST")) {
    const held = routes.find((route) => route.pattern === each.pattern);
    if (held !== undefined) {
      differences.push(`${each.pattern} is still there, ${held.script ?? "no Worker"}'s (the ` +
                       "record does not hold it)");
    }
  }
  return differences;
}

/**
 * How the zone's `routes` have changed since `routes` ran, beyond the states a handover and
 * its handback pass through: every route of `before` the handover did not change must be as
 * the record has it; a pattern it moved, the doors' or back as recorded; one it deleted,
 * absent or back; one it added, the doors' or gone. Anything else names a route changed by
 * someone else, which a handback must not paper over. Empty when the zone is on the path.
 */
export function drift(before: readonly Kept[], moved: readonly Moved[],
                      routes: readonly Route[], script: string): string[] {
  const differences: string[] = [];
  const now = (pattern: string) => {
    const held = routes.find((route) => route.pattern === pattern);
    return held === undefined ? undefined : held.script ?? null;
  };
  const name = (worker: string | null | undefined) =>
    worker === undefined ? "absent" : `${worker ?? "no Worker"}'s`;
  const changed = new Set(moved.map((each) => each.pattern));
  for (const kept of before.filter((route) => !changed.has(route.pattern))) {
    if (now(kept.pattern) !== kept.script) {
      differences.push(`${kept.pattern} is ${name(now(kept.pattern))}, the record has it ${
        name(kept.script)} and the handover never changed it`);
    }
  }
  for (const each of moved) {
    const recorded = before.find((route) => route.pattern === each.pattern)?.script;
    const allowed = each.method === "PUT" ? [script, recorded]
      : each.method === "DELETE" ? [undefined, recorded] : [script, undefined];
    if (!allowed.includes(now(each.pattern))) {
      differences.push(`${each.pattern} is ${name(now(each.pattern))}, neither as the handover ` +
                       `left it (${name(allowed[0])}) nor as the record has it (${
                         name(allowed[1])})`);
    }
  }
  return differences;
}

/** A handback's change, as the plan says it. */
export function describeBack(step: RouteChange): string {
  return step.method === "POST"
    ? `add the route ${step.pattern} back for ${step.script ?? "no Worker"}`
    : step.method === "PUT"
      ? `move the route ${step.pattern} from ${step.from} back to ${step.script ?? "no Worker"}`
      : `delete ${step.from}'s route ${step.pattern}, which the handover added`;
}

/**
 * The one Cloudflare client, the account's token on every call over `fetcher`: each answer
 * checked, a failure never read as absent.
 */
export class Cloudflare {
  readonly #token: string;
  readonly #fetch: Fetch;

  constructor(token: string, fetcher: Fetch) {
    this.#token = token;
    this.#fetch = fetcher;
  }

  /** One call, by its path from `/client/v4`: its status and JSON body. Any other body throws. */
  async #call(path: string, init: RequestInit = {}): Promise<Answer> {
    const response = await this.#fetch(`${CF_API}${path}`, {
      ...init, headers: { Authorization: `Bearer ${this.#token}`, ...(init.headers ?? {}) } });
    const text = await response.text();
    try {
      return { status: response.status, body: JSON.parse(text) as unknown };
    } catch {
      throw new Error(`Cloudflare answered ${init.method ?? "GET"} ${path} with a body that is ` +
                      `not JSON (${response.status})`);
    }
  }

  /** A call's envelope when it answered success; any other answer throws: `what` failed. */
  async #ok(what: string, path: string, init?: RequestInit): Promise<Envelope> {
    const answer = await this.#call(path, init);
    const body = envelope(answer);
    if (answer.status !== 200 || body.success !== true) {
      throw new Error(`${what} failed (${answer.status}, ${codes(answer)})`);
    }
    return body;
  }

  /** A JSON body's write, its envelope as `#ok` checks it. */
  async #send(what: string, method: string, path: string, body: unknown): Promise<Envelope> {
    return this.#ok(what, path, { method, body: JSON.stringify(body),
                                  headers: { "Content-Type": "application/json" } });
  }

  /** Each page's result of the listing at `path`, read to its last page (`nextPage`). */
  async #pages(what: string, path: string): Promise<unknown[]> {
    const results: unknown[] = [];
    for (let query: string | null = ""; query !== null;) {
      const page = await this.#ok(what, query === "" ? path
        : `${path}${path.includes("?") ? "&" : "?"}${query}`);
      results.push(page.result);
      query = nextPage(page.result_info);
    }
    return results;
  }

  /** Every row of the listing at `path`, each page's result an array of them. */
  async #rows(what: string, path: string): Promise<Record<string, unknown>[]> {
    return (await this.#pages(what, path)).flatMap((result) => rows(result, what));
  }

  /**
   * The zone `name`: its id, and the id of the account that holds it. That account is the
   * one the doors' routes need their Workers in, so the kit reads it here and holds no
   * account id of its own. A failed read, or any answer but one zone with its account,
   * throws.
   */
  async zone(name: string): Promise<{ id: string; account: string }> {
    const found = await this.#rows(`reading the zone ${name}`, `/zones?name=${name}`);
    const id = found[0]?.["id"];
    const account = (found[0]?.["account"] as { id?: unknown } | undefined)?.id;
    if (found.length !== 1 || typeof id !== "string" || typeof account !== "string"
        || !ACCOUNT_ID.test(account)) {
      throw new Error(`the zone ${name} could not be read (${found.length} found)`);
    }
    return { id, account };
  }

  /** The account's Worker scripts, by name: the one answer to whether a Worker exists. */
  async workers(account: string): Promise<string[]> {
    return (await this.#rows("the Workers listing", `/accounts/${account}/workers/scripts`))
      .map((script) => String(script["id"]));
  }

  /**
   * A deployed Worker's secrets' names. Any answer but success throws, a missing Worker's
   * too: whether it exists is the Workers listing's (`workers`), never a failed lookup's.
   */
  async secrets(account: string, script: string): Promise<string[]> {
    return (await this.#rows(`the secret listing of ${script}`,
                             `/accounts/${account}/workers/scripts/${script}/secrets`))
      .map((secret) => String(secret["name"]));
  }

  /**
   * The Worker `script` deleted, with the Durable Object namespaces it implements. Never
   * forced: Cloudflare refuses while another Worker still references it, and any answer but
   * success throws.
   */
  async deleteWorker(account: string, script: string): Promise<void> {
    await this.#ok(`deleting the Worker ${script}`, `/accounts/${account}/workers/scripts/${script}`,
                   { method: "DELETE" });
  }

  /**
   * A KV key's value, as the JSON it is stored as, or undefined only when the API answers
   * that it is absent (404 with Cloudflare's 10009, "key not found"); any other answer throws.
   */
  async value(account: string, namespace: string, key: string): Promise<unknown> {
    const answer = await this.#call(`/accounts/${account}/storage/kv/namespaces/${
      namespace}/values/${encodeURIComponent(key)}`);
    if (answer.status === 200) {
      return answer.body;
    }
    if (answer.status === 404 && (envelope(answer).errors ?? []).some((error) =>
      error.code === 10009)) {
      return undefined;
    }
    throw new Error(`reading ${key} failed (${answer.status}, ${codes(answer)})`);
  }

  /** `value`, as JSON, written to the KV key; the API's status. */
  async put(account: string, namespace: string, key: string, value: unknown): Promise<number> {
    const answer = await this.#call(`/accounts/${account}/storage/kv/namespaces/${
      namespace}/values/${encodeURIComponent(key)}`, {
      method: "PUT", body: JSON.stringify(value),
      headers: { "Content-Type": "application/json" } });
    return answer.status;
  }

  /** The account's KV namespaces: each one's id, by its title. */
  async namespaces(account: string): Promise<Map<string, string>> {
    return new Map((await this.#rows("listing the KV namespaces",
                                     `/accounts/${account}/storage/kv/namespaces?per_page=100`))
      .map((row) => [String(row["title"]), String(row["id"])]));
  }

  /** The KV namespace `title` made: its id. */
  async makeNamespace(account: string, title: string): Promise<string> {
    const { result } = await this.#send(`making the KV namespace ${title}`, "POST",
                                        `/accounts/${account}/storage/kv/namespaces`, { title });
    const id = (result as { id?: unknown } | null)?.id;
    if (typeof id !== "string") {
      throw new Error(`making the KV namespace ${title} answered no id`);
    }
    return id;
  }

  /** The names of a KV namespace's keys under `prefix`. */
  async keys(account: string, namespace: string, prefix: string): Promise<string[]> {
    const path = `/accounts/${account}/storage/kv/namespaces/${namespace}/keys`;
    return (await this.#rows(`listing the keys under ${prefix}`,
                             `${path}?prefix=${encodeURIComponent(prefix)}`))
      .map((key) => String(key["name"]));
  }

  /** The account's R2 buckets, by name (the listing's result is `{ buckets }`). */
  async buckets(account: string): Promise<string[]> {
    const what = "listing the R2 buckets";
    return (await this.#pages(what, `/accounts/${account}/r2/buckets`))
      .flatMap((result) => rows((result as { buckets?: unknown } | null)?.buckets, what))
      .map((bucket) => String(bucket["name"]));
  }

  /** The R2 bucket `name` made. */
  async makeBucket(account: string, name: string): Promise<void> {
    await this.#send(`making the R2 bucket ${name}`, "POST", `/accounts/${account}/r2/buckets`,
                     { name });
  }

  /** The account's queues: each one's id, by its name. */
  async queues(account: string): Promise<Map<string, string>> {
    return new Map((await this.#rows("listing the queues", `/accounts/${account}/queues`))
      .map((queue) => [String(queue["queue_name"]), String(queue["queue_id"])]));
  }

  /** The queue `name` made. */
  async makeQueue(account: string, name: string): Promise<void> {
    await this.#send(`making the queue ${name}`, "POST", `/accounts/${account}/queues`,
                     { queue_name: name });
  }

  /** `body` sent as one JSON message to the queue `queue` (its id). */
  async send(account: string, queue: string, body: unknown): Promise<void> {
    await this.#send(`sending a message to the queue ${queue}`, "POST",
                     `/accounts/${account}/queues/${queue}/messages`,
                     { body, content_type: "json" });
  }

  /** The consumers of the queue `queue` (its id), by type (`worker` or `http_pull`). */
  async consumers(account: string, queue: string): Promise<string[]> {
    return (await this.#rows(`listing the consumers of the queue ${queue}`,
                             `/accounts/${account}/queues/${queue}/consumers`))
      .map((consumer) => String(consumer["type"]));
  }

  /** An HTTP pull consumer added to the queue `queue` (its id), with `settings`. */
  async addPullConsumer(account: string, queue: string, settings: PullSettings): Promise<void> {
    await this.#send(`adding a pull consumer to the queue ${queue}`, "POST",
                     `/accounts/${account}/queues/${queue}/consumers`,
                     { type: "http_pull", settings });
  }

  /**
   * Up to `batch` messages pulled from the queue `queue` (its id), each leased for
   * `visibilityMs`, and the queue's backlog: every message not yet acknowledged, leased too.
   */
  async pull(account: string, queue: string, batch: number, visibilityMs: number):
      Promise<{ backlog: number; messages: Pulled[] }> {
    const { result } = await this.#send(`pulling from the queue ${queue}`, "POST",
                                        `/accounts/${account}/queues/${queue}/messages/pull`,
                                        { batch_size: batch, visibility_timeout_ms: visibilityMs });
    // An answer with no pull in it is a failed read, never an empty queue.
    if (typeof result !== "object" || result === null) {
      throw new Error(`pulling from the queue ${queue} failed (its answer holds no pull)`);
    }
    const pulled = result as { message_backlog_count?: unknown;
                               messages?: { id?: unknown; body?: unknown; lease_id?: unknown }[] };
    const backlog = pulled.message_backlog_count;
    return { backlog: Number.isSafeInteger(backlog) ? backlog as number : 0,
             messages: (pulled.messages ?? []).map((message) => ({
               id: typeof message.id === "string" ? message.id : "", body: message.body,
               lease: typeof message.lease_id === "string" ? message.lease_id : "" })) };
  }

  /** The pulled messages of `leases` acknowledged, so deleted, in calls of 100. */
  async ack(account: string, queue: string, leases: readonly string[]): Promise<void> {
    for (let at = 0; at < leases.length; at += 100) {
      await this.#send(`acknowledging messages of the queue ${queue}`, "POST",
                       `/accounts/${account}/queues/${queue}/messages/ack`,
                       { acks: leases.slice(at, at + 100).map((lease_id) => ({ lease_id })),
                         retries: [] });
    }
  }

  /** The KV namespace's keys `keys` deleted, in calls of 1000. */
  async deleteKeys(account: string, namespace: string, keys: readonly string[]): Promise<void> {
    for (let at = 0; at < keys.length; at += 1000) {
      await this.#send("deleting KV keys", "POST",
                       `/accounts/${account}/storage/kv/namespaces/${namespace}/bulk/delete`,
                       keys.slice(at, at + 1000));
    }
  }

  /** The zone's Worker routes. */
  async routes(zone: string): Promise<Route[]> {
    return await this.#rows("reading the zone's routes", `/zones/${zone}/workers/routes`) as
      unknown as Route[];
  }

  /**
   * The zone's routes changed by `steps` in order, a handover's or a handback's, each answer
   * checked: the first failure stops it, said as `say` says the step.
   */
  async reroute(zone: string, steps: readonly RouteChange[],
                say: (step: RouteChange) => string): Promise<void> {
    for (const step of steps) {
      const path = `/zones/${zone}/workers/routes${step.method === "POST" ? "" : `/${step.id}`}`;
      const answer = await this.#call(path, step.method === "DELETE" ? { method: "DELETE" } : {
        method: step.method, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pattern: step.pattern,
                               ...(step.script === null ? {} : { script: step.script }) }) });
      if (answer.status !== 200 || envelope(answer).success !== true) {
        throw new Error(`${say(step)} failed (${answer.status}, ${codes(answer)}); the steps ` +
                        "before it are done, the rest are not: run it again for the rest");
      }
    }
  }

  /**
   * The Workers custom domains on `hostname` (Cloudflare's Workers domains API, as wrangler's
   * own SDK copy lists them): each its id and the Worker it serves. A failed read throws.
   */
  async domains(account: string, hostname: string): Promise<{ id: string; service: string }[]> {
    const what = `reading the custom domains on ${hostname}`;
    const found = await this.#rows(what, `/accounts/${account}/workers/domains?hostname=${hostname}`);
    if (!found.every((row) => typeof row["id"] === "string" && typeof row["service"] === "string")) {
      throw new Error(`${what} answered a domain with no id or Worker`);
    }
    return found.filter((row) => row["hostname"] === undefined || row["hostname"] === hostname)
      .map((row) => ({ id: String(row["id"]), service: String(row["service"]) }));
  }

  /**
   * The zone's address records (`ADDRESS`: what a custom domain's own record would replace)
   * named exactly `name`: each its id, type and content. The listing is asked for by
   * `name.exact` (the DNS records reference), and its rows are filtered here by exact name
   * and type as well, so a filter the API ignores neither blocks nor passes wrongly. A failed
   * read throws.
   */
  async dnsRecords(zone: string, name: string): Promise<DnsRecord[]> {
    return (await this.#rows(`reading the zone's DNS records for ${name}`,
                             `/zones/${zone}/dns_records?name.exact=${name}&per_page=100`))
      .filter((row) => row["name"] === name && ADDRESS.includes(String(row["type"])))
      .map((row) => ({ id: String(row["id"]), type: String(row["type"]),
                       content: String(row["content"]) }));
  }

  /** Deletes the DNS record `record`; a refusal throws. */
  async deleteDns(zone: string, record: DnsRecord, name: string): Promise<void> {
    const answer = await this.#call(`/zones/${zone}/dns_records/${record.id}`,
                                    { method: "DELETE" });
    if (answer.status !== 200 || envelope(answer).success === false) {
      throw new Error(`deleting the DNS record ${record.type} ${name} (${record.content}) failed (${
        answer.status}, ${codes(answer)}): run the handback again`);
    }
  }

  /** Detaches the custom domain `id` from its Worker; a refusal throws. */
  async detach(account: string, id: string, hostname: string): Promise<void> {
    const answer = await this.#call(`/accounts/${account}/workers/domains/${id}`,
                                    { method: "DELETE" });
    if (answer.status !== 200 || envelope(answer).success === false) {
      throw new Error(`detaching the custom domain ${hostname} failed (${answer.status}, ${
        codes(answer)}): run the handback again`);
    }
  }
}
