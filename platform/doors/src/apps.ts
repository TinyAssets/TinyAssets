/**
 * The AI apps an owner may connect (`apps.json`, data; autoconnect_research.md §2.3, §3):
 * which vendor a signed-in client is, how each vendor's client registers with our sign-in,
 * and each app's connect step. No code here names a vendor.
 *
 * - **Who a client is** (`vendorOf`), at sign-in: a Client ID Metadata Document's client id
 *   is its URL, which the authorization server fetched; it names the vendor **verified** only
 *   when that host is the app's and every redirect the document registered is one of the
 *   app's own (`match.redirects`), so a document a stranger publishes on a vendor's host
 *   that sends access elsewhere is not the vendor's (review M2). Any other client
 *   (registered, dynamically or by us) is known only by its self-declared name, a word of
 *   which names the vendor **unverified**. A client matching no app is no vendor (null).
 * - **Dynamic registration** (`registrationRefusal`): the founder's ruling (2026-11-04)
 *   allows it for the vendors that offer no CIMD, as data (`registration: "dcr"`), the one
 *   pre-login exception to no anonymous traffic. With `DCR_ENABLED` on, a registration is
 *   taken only when its `client_name` names a `dcr` app and every one of its `redirect_uris`
 *   is that app's (review M3): an approval under a vendor's name sends codes only to that
 *   vendor. A `dcr` app with no redirects recorded has dynamic registration off.
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
  readonly match: { readonly hosts: readonly string[]; readonly names: readonly string[];
                     readonly redirects: readonly string[] };
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

/** A URI as a redirect compares it: a loopback http one on no port, else as it is. */
function comparable(uri: string): URL | null {
  let url: URL;
  try {
    url = new URL(uri);
  } catch {
    return null;
  }
  if (url.protocol === "http:" && LOOPBACK.includes(url.hostname)) {
    url.port = "";
  }
  return url;
}

const LOOPBACK: readonly string[] = Object.freeze(["localhost", "127.0.0.1", "[::1]"]);

/**
 * An app's redirect patterns: each an https URI, a loopback http one (matched on any port,
 * RFC 8252 §7.3), or a custom scheme's (a native app's), with no query or fragment; one that
 * ends with `/` is a path prefix, any other is matched whole.
 */
function redirectPatterns(value: unknown, id: string): readonly string[] {
  const patterns = texts(value, `${id}.match.redirects`);
  for (const pattern of patterns) {
    const url = comparable(pattern);
    const loopback = url !== null && url.protocol === "http:" && LOOPBACK.includes(url.hostname);
    if (url === null || url.search !== "" || url.hash !== "" || url.host === ""
        || (url.protocol === "http:" && !loopback) || url.pathname === "") {
      throw new Error(`apps.json: ${id}.match.redirects holds https, loopback or custom ` +
                      `scheme URIs with a host and no query: ${pattern}`);
    }
  }
  return patterns;
}

/** Whether `uri` is one of `app`'s redirects. */
export function ownRedirect(app: App, uri: string): boolean {
  const url = comparable(uri);
  return url !== null && url.search === "" && url.hash === ""
    && app.match.redirects.some((pattern) => {
      const own = comparable(pattern)!;
      return own.protocol === url.protocol && own.host === url.host
        && (own.pathname.endsWith("/") ? url.pathname.startsWith(own.pathname)
                                       : url.pathname === own.pathname);
    });
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
        || Object.keys(match).sort().join() !== "hosts,names,redirects") {
      throw new Error(`apps.json: ${id}.match has exactly hosts, names and redirects`);
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
                             names: texts(match["names"], `${id}.match.names`, NAME),
                             redirects: redirectPatterns(match["redirects"], id) }),
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

/** The app with `id`, or null. */
export function appOf(id: string, apps: readonly App[] = appList()): App | null {
  return apps.find((app) => app.id === id) ?? null;
}

/** The public connect page's place for an app: its current steps. */
export function connectUrl(id: string): string {
  return `${checkedApps().page}#${id}`;
}

/** The app a host is, or is under (`chat.example.com` is `example.com`'s). */
function byHost(host: string, apps: readonly App[]): App | null {
  const at = host.toLowerCase().replace(/\.$/, "");
  return apps.find((app) => app.match.hosts.some((h) => at === h || at.endsWith(`.${h}`)))
    ?? null;
}

/** The app a self-declared name names: one of its words, case aside. */
function byName(name: string, apps: readonly App[]): App | null {
  const words = new Set(name.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w !== ""));
  return apps.find((app) => app.match.names.some((n) => {
    const parts = n.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w !== "");
    return parts.length > 0 && parts.every((part) => words.has(part));
  })) ?? null;
}

/**
 * Which vendor a signed-in client is. A Client ID Metadata Document's client (its id an https
 * URL): the app its host is under, verified only when every redirect it registered is that
 * app's (`redirects`; review M2), else that app unverified, or null under no app's host. Any
 * other client: the app a word of its self-declared name names, unverified, or null.
 */
export function vendorOf(clientId: string, clientName: string | null,
                         redirects: readonly string[] = [],
                         apps: readonly App[] = appList()): Vendor | null {
  let url: URL | null = null;
  try {
    url = new URL(clientId);
  } catch {
    url = null;
  }
  if (url !== null && url.protocol === "https:") {
    const app = byHost(url.hostname, apps);
    return app === null ? null
      : { id: app.id, verified: redirects.length > 0
                                && redirects.every((uri) => ownRedirect(app, uri)) };
  }
  const named = clientName === null ? null : byName(clientName, apps);
  return named === null ? null : { id: named.id, verified: false };
}

/**
 * Why a dynamic registration is refused, or null to take it: its `client_name` must name an
 * app whose client registers dynamically (`registration: "dcr"`), and every one of its
 * `redirect_uris` must be that app's (review M3); an app with none recorded takes none.
 */
export function registrationRefusal(metadata: Record<string, unknown>,
                                    apps: readonly App[] = appList()): string | null {
  const name = metadata["client_name"];
  const app = typeof name === "string" ? byName(name, apps) : null;
  if (app === null || app.registration !== "dcr") {
    return "Dynamic registration is only for the apps that offer no Client ID Metadata " +
      "Document; name the app in client_name.";
  }
  const uris = metadata["redirect_uris"];
  if (app.match.redirects.length === 0) {
    return `Dynamic registration for ${app.name} is off until its redirect URIs are recorded.`;
  }
  if (!Array.isArray(uris) || uris.length === 0
      || !uris.every((uri) => typeof uri === "string" && ownRedirect(app, uri))) {
    return `A registration named for ${app.name} sends access only to ${app.name}'s own ` +
      "redirect URIs.";
  }
  return null;
}
