/**
 * The AI apps an owner may connect (`apps.json`, data; autoconnect_research.md §2.3, §3):
 * which vendor a signed-in client is, how each vendor's client registers with our sign-in,
 * and each app's connect step. No code here names a vendor.
 *
 * - **Who a client is** (`vendorOf`), at sign-in: a Client ID Metadata Document's client id
 *   is its URL, which the authorization server fetched, so its host names the vendor
 *   **verified**; any other client (registered, dynamically or by us) is known only by its
 *   self-declared name, a word of which names the vendor **unverified**. A client matching
 *   no app is no vendor (null), never a guess.
 * - **Dynamic registration** (`registrationRefusal`): the founder's ruling (2026-11-04)
 *   allows it for the vendors that offer no CIMD, as data (`registration: "dcr"`), the one
 *   pre-login exception to no anonymous traffic. With `DCR_ENABLED` on, a registration is
 *   taken only when its `client_name` names a `dcr` app, so no client registers itself as a
 *   CIMD vendor's name and no other client registers at all.
 * - **Connect** (`connectUrl`): the public connect page's anchor for the app, which shows
 *   its current steps; agents hand the link, never copied steps.
 *
 * `checkedApps` holds the file to its shape, loudly, wherever it is read.
 */
import raw from "../apps.json" with { type: "json" };

export interface AppConnect {
  /** A deep link where the vendor has one. */
  readonly link?: string;
  /** The sentence the user sends where connecting is a chat act. */
  readonly say?: string;
  /** Short UI steps, in plain words. */
  readonly steps: readonly string[];
  /** Text to copy (a config file, terminal commands). */
  readonly code?: string;
}

export interface App {
  readonly id: string;
  readonly name: string;
  readonly surfaces: readonly string[];
  readonly registration: "cimd" | "dcr";
  readonly match: { readonly hosts: readonly string[]; readonly names: readonly string[] };
  readonly connect: AppConnect;
  readonly source: string;
  readonly checked: string;
}

export interface Apps {
  readonly server: string;
  readonly page: string;
  readonly apps: readonly App[];
}

/** A vendor as a sign-in knew it: an app's id, and whether its CIMD host verified it. */
export interface Vendor {
  readonly id: string;
  readonly verified: boolean;
}

const ID = /^[a-z][a-z0-9-]{0,31}$/;
const HOST = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const NAME = /^[A-Za-z0-9][A-Za-z0-9 .-]{0,31}$/;

function text(value: unknown, what: string, max = 1024): string {
  if (typeof value !== "string" || value.trim() === "" || value.length > max) {
    throw new Error(`apps.json: ${what} is text of at most ${max} characters`);
  }
  return value;
}

function texts(value: unknown, what: string, pattern?: RegExp): readonly string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string"
      || item.trim() === "" || (pattern !== undefined && !pattern.test(item)))) {
    throw new Error(`apps.json: ${what} is a list of ${pattern === undefined ? "texts" : pattern}`);
  }
  return Object.freeze([...value] as string[]);
}

function https(value: unknown, what: string): string {
  const url = new URL(text(value, what, 2048));
  if (url.protocol !== "https:") {
    throw new Error(`apps.json: ${what} is an https URL`);
  }
  return value as string;
}

/** The file, held to its shape: each app's every field, ids, hosts and names distinct. */
export function checkedApps(value: unknown = raw): Apps {
  const file = value as Record<string, unknown>;
  if (typeof file !== "object" || file === null || !Array.isArray(file["apps"])) {
    throw new Error("apps.json: an object with apps");
  }
  const apps = (file["apps"] as unknown[]).map((entry, at): App => {
    const app = entry as Record<string, unknown>;
    const where = `apps[${at}]`;
    const keys = Object.keys(app ?? {}).sort().join();
    if (keys !== "checked,connect,id,match,name,registration,source,surfaces") {
      throw new Error(`apps.json: ${where} has exactly id, name, surfaces, registration, ` +
                      "match, connect, source and checked");
    }
    const id = text(app["id"], `${where}.id`, 32);
    if (!ID.test(id)) {
      throw new Error(`apps.json: ${where}.id is lower-case letters, digits and -`);
    }
    if (app["registration"] !== "cimd" && app["registration"] !== "dcr") {
      throw new Error(`apps.json: ${id}.registration is cimd or dcr`);
    }
    const match = app["match"] as Record<string, unknown>;
    if (typeof match !== "object" || match === null
        || Object.keys(match).sort().join() !== "hosts,names") {
      throw new Error(`apps.json: ${id}.match has exactly hosts and names`);
    }
    const connect = app["connect"] as Record<string, unknown>;
    if (typeof connect !== "object" || connect === null
        || Object.keys(connect).some((key) => !["link", "say", "steps", "code"].includes(key))) {
      throw new Error(`apps.json: ${id}.connect has link, say, steps and code only`);
    }
    if (!DATE.test(text(app["checked"], `${id}.checked`, 10))) {
      throw new Error(`apps.json: ${id}.checked is a YYYY-MM-DD date`);
    }
    const sources = text(app["source"], `${id}.source`, 2048);
    if (!/^https:\/\//.test(sources)) {
      throw new Error(`apps.json: ${id}.source names the vendor page its steps were read from`);
    }
    const steps = texts(connect["steps"], `${id}.connect.steps`);
    if (steps.length === 0) {
      throw new Error(`apps.json: ${id}.connect.steps names at least one step`);
    }
    return Object.freeze({
      id, name: text(app["name"], `${id}.name`, 80),
      surfaces: texts(app["surfaces"], `${id}.surfaces`),
      registration: app["registration"],
      match: Object.freeze({ hosts: texts(match["hosts"], `${id}.match.hosts`, HOST),
                             names: texts(match["names"], `${id}.match.names`, NAME) }),
      connect: Object.freeze({
        ...(connect["link"] === undefined ? {} : { link: https(connect["link"], `${id}.link`) }),
        ...(connect["say"] === undefined ? {} : { say: text(connect["say"], `${id}.say`) }),
        steps,
        ...(connect["code"] === undefined ? {} : { code: text(connect["code"], `${id}.code`) }),
      }),
      source: sources, checked: app["checked"] as string,
    });
  });
  const seen = (what: string, all: readonly string[]) => {
    if (new Set(all).size !== all.length) {
      throw new Error(`apps.json: every ${what} is one app's`);
    }
  };
  seen("id", apps.map((app) => app.id));
  seen("host", apps.flatMap((app) => app.match.hosts));
  seen("name", apps.flatMap((app) => app.match.names.map((name) => name.toLowerCase())));
  return Object.freeze({ server: https(file["server"], "server"), page: https(file["page"], "page"),
                         apps: Object.freeze(apps) });
}

/** The apps, as `apps.json` holds them, checked. */
export function appList(): readonly App[] {
  return checkedApps().apps;
}

/** The public connect page's place for an app: its current steps. */
export function connectUrl(id: string): string {
  return `${checkedApps().page}#${id}`;
}

/** The app a host is, or is under (`chat.example.com` is `example.com`'s). */
function byHost(host: string): App | null {
  const at = host.toLowerCase().replace(/\.$/, "");
  return appList().find((app) => app.match.hosts.some((h) => at === h || at.endsWith(`.${h}`)))
    ?? null;
}

/** The app a self-declared name names: one of its words, case aside. */
function byName(name: string): App | null {
  const words = new Set(name.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w !== ""));
  return appList().find((app) => app.match.names.some((n) => {
    const parts = n.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w !== "");
    return parts.length > 0 && parts.every((part) => words.has(part));
  })) ?? null;
}

/**
 * Which vendor a signed-in client is: by its client id's host when the id is a Client ID
 * Metadata Document's https URL (verified), else by its self-declared name (unverified);
 * null when no app matches.
 */
export function vendorOf(clientId: string, clientName: string | null): Vendor | null {
  let url: URL | null = null;
  try {
    url = new URL(clientId);
  } catch {
    url = null;
  }
  if (url !== null && url.protocol === "https:") {
    const app = byHost(url.hostname);
    return app === null ? null : { id: app.id, verified: true };
  }
  const named = clientName === null ? null : byName(clientName);
  return named === null ? null : { id: named.id, verified: false };
}

/**
 * Why a dynamic registration is refused, or null to take it: its `client_name` must name an
 * app whose client registers dynamically (`registration: "dcr"`).
 */
export function registrationRefusal(metadata: Record<string, unknown>): string | null {
  const name = metadata["client_name"];
  const app = typeof name === "string" ? byName(name) : null;
  if (app === null || app.registration !== "dcr") {
    return "Dynamic registration is only for the apps that offer no Client ID Metadata " +
      "Document; name the app in client_name.";
  }
  return null;
}
