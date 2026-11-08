/**
 * Cloudflare, as the deploy kit speaks to it over its REST API with the account's token: the
 * zone and its account, Workers' secret listings, KV namespaces and values, and the zone's
 * Worker routes. Every lookup is checked: a failed listing or read throws, never reads as
 * "absent". The kit's other Cloudflare tool is wrangler (`deploy.ts`), which deploys, sets
 * secrets and lists and makes buckets and queues.
 *
 * Node runs this file as it is (type stripping), as the kit does.
 */
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

const CF_API = "https://api.cloudflare.com/client/v4";

/** Cloudflare's API answer: its status and its parsed body, or a throw on no answer. */
export interface CfAnswer {
  readonly status: number;
  readonly body: { success?: boolean; result?: unknown; errors?: { code?: number }[] };
}

/** One call of the API, by its path from `/client/v4`. */
export type CfApi = (path: string, init?: RequestInit) => Promise<CfAnswer>;

/** The API with the account's token, over `fetcher`. */
export function cfApi(token: string, fetcher: Fetch = (url, init) => fetch(url, init)): CfApi {
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

/** A Worker route on the zone, as Cloudflare's routes API lists it. */
export interface Route {
  readonly id: string;
  readonly pattern: string;
  readonly script?: string;
}

/** One change of a handover, in order. */
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
  readonly method: RouteStep["method"];
  readonly pattern: string;
}

/** One change of a handback, in order, each to the script the record holds. */
export type BackStep =
  | { readonly method: "POST"; readonly pattern: string; readonly script: string | null }
  | { readonly method: "PUT"; readonly id: string; readonly pattern: string;
      readonly script: string | null; readonly from: string }
  | { readonly method: "DELETE"; readonly id: string; readonly pattern: string;
      readonly from: string };

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
                         routes: readonly Route[], script: string): BackStep[] {
  const steps: BackStep[] = [];
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

/** A handback's step, as the plan says it. */
export function describeBack(step: BackStep): string {
  return step.method === "POST"
    ? `add the route ${step.pattern} back for ${step.script ?? "no Worker"}`
    : step.method === "PUT"
      ? `move the route ${step.pattern} from ${step.from} back to ${step.script ?? "no Worker"}`
      : `delete ${step.from}'s route ${step.pattern}, which the handover added`;
}

/** The kit's one Cloudflare client: each call checked, a failure never read as absent. */
export class Cloudflare {
  readonly #api: CfApi;

  constructor(api: CfApi) {
    this.#api = api;
  }

  /**
   * The zone `name`: its id, and the id of the account that holds it. That account is the
   * one the doors' routes need their Workers in, so the kit reads it here and holds no
   * account id of its own. A failed read, or any answer but one zone with its account,
   * throws.
   */
  async zone(name: string): Promise<{ id: string; account: string }> {
    const zones = await this.#api(`/zones?name=${name}`);
    const rows = Array.isArray(zones.body.result)
      ? zones.body.result as { id?: unknown; account?: { id?: unknown } }[] : [];
    const id = rows[0]?.id;
    const account = rows[0]?.account?.id;
    if (zones.status !== 200 || zones.body.success !== true || rows.length !== 1
        || typeof id !== "string" || typeof account !== "string"
        || !/^[0-9a-f]{32}$/.test(account)) {
      throw new Error(`the zone ${name} could not be read (${zones.status}, ${rows.length} found)`);
    }
    return { id, account };
  }

  /**
   * A Worker's secrets' names, or null when the API answers that the Worker does not exist
   * (404 with Cloudflare's 10007, "workers.api.error.script_not_found"); any other failure
   * throws: a failed lookup is never "no secrets".
   */
  async secrets(account: string, script: string): Promise<string[] | null> {
    const answer = await this.#api(`/accounts/${account}/workers/scripts/${script}/secrets`);
    if (answer.status === 404 && codes(answer).includes(10007)) {
      return null;
    }
    if (answer.status !== 200 || answer.body.success !== true
        || !Array.isArray(answer.body.result)) {
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
  async value(account: string, namespace: string, key: string): Promise<unknown> {
    const answer = await this.#api(`/accounts/${account}/storage/kv/namespaces/${
      namespace}/values/${encodeURIComponent(key)}`);
    if (answer.status === 200) {
      return answer.body;
    }
    if (answer.status === 404 && codes(answer).includes(10009)) {
      return undefined;
    }
    throw new Error(`reading ${key} failed (${answer.status}, ${JSON.stringify(codes(answer))})`);
  }

  /** `value`, as JSON, written to the KV key; the API's status. */
  async put(account: string, namespace: string, key: string, value: unknown): Promise<number> {
    const answer = await this.#api(`/accounts/${account}/storage/kv/namespaces/${
      namespace}/values/${encodeURIComponent(key)}`, {
      method: "PUT", body: JSON.stringify(value),
      headers: { "Content-Type": "application/json" } });
    return answer.status;
  }

  /** The account's KV namespaces by title, every page read; a failure throws. */
  async namespaces(account: string): Promise<Map<string, string>> {
    const found = new Map<string, string>();
    for (let page = 1; ; page += 1) {
      const answer = await this.#api(
        `/accounts/${account}/storage/kv/namespaces?per_page=100&page=${page}`);
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

  /** The zone's Worker routes; a failed read throws. */
  async routes(zone: string): Promise<Route[]> {
    const routes = await this.#api(`/zones/${zone}/workers/routes`);
    if (routes.status !== 200 || routes.body.success !== true
        || !Array.isArray(routes.body.result)) {
      throw new Error(`the zone's routes could not be read (${routes.status})`);
    }
    return routes.body.result as Route[];
  }

  /** Runs a handover's steps in order, each answer checked; the first failure stops it. */
  async handOver(zone: string, script: string, steps: readonly RouteStep[]): Promise<void> {
    for (const step of steps) {
      const path = `/zones/${zone}/workers/routes${step.method === "POST" ? "" : `/${step.id}`}`;
      const answer = await this.#api(path, step.method === "DELETE" ? { method: "DELETE" } : {
        method: step.method, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pattern: step.pattern, script }) });
      if (answer.status !== 200 || answer.body.success !== true) {
        throw new Error(`${describeStep(step, script)} failed (${answer.status}, ${
          JSON.stringify(codes(answer))}); the steps before it are done, the rest are not`);
      }
    }
  }

  /** Runs a handback's steps in order, each answer checked; the first failure stops it. */
  async handBack(zone: string, steps: readonly BackStep[]): Promise<void> {
    for (const step of steps) {
      const path = `/zones/${zone}/workers/routes${step.method === "POST" ? "" : `/${step.id}`}`;
      const answer = await this.#api(path, step.method === "DELETE" ? { method: "DELETE" } : {
        method: step.method, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pattern: step.pattern,
                               ...(step.script === null ? {} : { script: step.script }) }) });
      if (answer.status !== 200 || answer.body.success !== true) {
        throw new Error(`${describeBack(step)} failed (${answer.status}, ${
          JSON.stringify(codes(answer))}); the steps before it are done, the rest are not: run ` +
          "the handback again");
      }
    }
  }

  /**
   * The Workers custom domains on `hostname` (Cloudflare's Workers domains API, as wrangler's
   * own SDK copy lists them): each its id and the Worker it serves. A failed read throws.
   */
  async domains(account: string, hostname: string): Promise<{ id: string; service: string }[]> {
    const answer = await this.#api(`/accounts/${account}/workers/domains?hostname=${hostname}`);
    const rows = Array.isArray(answer.body.result)
      ? answer.body.result as { id?: unknown; hostname?: unknown; service?: unknown }[] : null;
    if (answer.status !== 200 || answer.body.success !== true || rows === null
        || !rows.every((row) => typeof row.id === "string" && typeof row.service === "string")) {
      throw new Error(`the custom domains on ${hostname} could not be read (${answer.status})`);
    }
    return rows.filter((row) => row.hostname === undefined || row.hostname === hostname)
      .map((row) => ({ id: String(row.id), service: String(row.service) }));
  }

  /**
   * The zone's address records (`ADDRESS`: what a custom domain's own record would replace)
   * named exactly `name`: each its id, type and content. The listing is asked for by
   * `name.exact` (the DNS records reference), and its rows are filtered here by exact name
   * and type as well, so a filter the API ignores neither blocks nor passes wrongly. A failed
   * read throws.
   */
  async dnsRecords(zone: string, name: string): Promise<DnsRecord[]> {
    const answer = await this.#api(`/zones/${zone}/dns_records?name.exact=${name}&per_page=100`);
    if (answer.status !== 200 || answer.body.success !== true
        || !Array.isArray(answer.body.result)) {
      throw new Error(`the zone's DNS records for ${name} could not be read (${answer.status})`);
    }
    return (answer.body.result as { id?: unknown; type?: unknown; name?: unknown;
                                    content?: unknown }[])
      .filter((row) => row.name === name && ADDRESS.includes(String(row.type)))
      .map((row) => ({ id: String(row.id), type: String(row.type),
                       content: String(row.content) }));
  }

  /** Deletes the DNS record `record`; a refusal throws. */
  async deleteDns(zone: string, record: DnsRecord, name: string): Promise<void> {
    const answer = await this.#api(`/zones/${zone}/dns_records/${record.id}`,
                                   { method: "DELETE" });
    if (answer.status !== 200 || answer.body.success === false) {
      throw new Error(`deleting the DNS record ${record.type} ${name} (${record.content}) failed (${
        answer.status}, ${JSON.stringify(codes(answer))}): run the handback again`);
    }
  }

  /** Detaches the custom domain `id` from its Worker; a refusal throws. */
  async detach(account: string, id: string, hostname: string): Promise<void> {
    const answer = await this.#api(`/accounts/${account}/workers/domains/${id}`,
                                   { method: "DELETE" });
    if (answer.status !== 200 || answer.body.success === false) {
      throw new Error(`detaching the custom domain ${hostname} failed (${answer.status}, ${
        JSON.stringify(codes(answer))}): run the handback again`);
    }
  }
}
