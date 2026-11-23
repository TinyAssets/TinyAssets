/**
 * Where a sealed credential may go: the source values the binder
 * hands the gateway to seal (the pi-ai provider and API, the base URL, how the secret is
 * sent, the endpoints it may reach, an OAuth client), the egress scope a committed
 * connection keeps, the rules that judge a request against them, and the reads of what a
 * key can pay for (`CreditRead`) and of the models its vendor lists it (`ListingRead`). The
 * gateway seals and
 * opens by these (`gateway/src/credential.ts`), the source records are checked by them
 * (`sources/src/check.ts`), and the owner's object admits a sandbox call by them
 * (`owner/src/egress.ts`), so the three can never disagree.
 *
 * Every value is checked when it is sealed and again when it is opened, so a blob written
 * by older code that no longer fits fails loudly rather than being half-read; anything else
 * is `invalid_source`, the gateway's fixed code.
 *
 * A credential is a model source's (`Upstream`, `ModelScope`) or a service's (`ServiceUpstream`,
 * `ServiceScope`): a key the owner gave for one URL of an outside service, an MCP server of
 * its agents' (`serviceSource`), sent with no metering and only to that URL.
 */
import { type ApiRules, apiRules, declaredApi, freeCall } from "./api-rules.ts";
import { keyed, list, object, shape, text, valueAtPath } from "./checks.ts";
import { Refusal } from "./refusal.ts";
import { checkedRecord, type ModelRecord } from "./model-record.ts";

/** One place a credential may be sent: an HTTPS origin, its paths and its methods. */
export interface Endpoint {
  readonly origin: string;
  /** Exact paths, or prefixes written `/prefix/*`. */
  readonly paths: readonly string[];
  readonly methods: readonly string[];
}

/** How the secret is sent: a header, with an optional scheme before it (`Bearer`). */
export interface AuthTemplate {
  readonly header: string;
  readonly scheme: string;
}

/**
 * One API a model source's models speak: pi-ai's API id, and the base URL its models are at
 * (pi-ai's catalog has a provider's models at a base each API's adapter appends its path to).
 */
export interface UpstreamApi {
  readonly api: string;
  readonly base_url: string;
}

/**
 * A model source's data, sealed with its secret: the vendor's (its provider, how its key is
 * sent, its setup check) once, and each API its models speak, all at one origin, since a call
 * through the egress door names its path alone. A declared endpoint speaks one.
 */
export interface Upstream {
  /** A pi-ai provider id, or `custom` for a declared endpoint. */
  readonly provider: string;
  readonly apis: readonly UpstreamApi[];
  readonly auth: AuthTemplate;
  /** A `GET` that answers 2xx when the secret is good; one of the endpoints. */
  readonly verify_url?: string;
  /** For `custom` only: the models it serves, each its whole record (`model-record.ts`). */
  readonly models?: readonly ModelRecord[];
}

/**
 * A service's data, sealed with its secret: the one URL it is sent to and how (its header and
 * scheme, as the owner gave them: data, never a vendor's code). No API, no models, no meter.
 */
export interface ServiceUpstream {
  readonly kind: "service";
  readonly base_url: string;
  readonly auth: AuthTemplate;
}

/**
 * The provider a service's connection names (`owner/src/connections.ts`): no pi-ai provider's
 * id, so no model path ever takes it for one, and the connection's scope says which service.
 */
export const SERVICE_PROVIDER = "service";

/** Whether an upstream is a service's, not a model source's. */
export function isService(upstream: Upstream | ServiceUpstream): upstream is ServiceUpstream {
  return "kind" in upstream;
}

/** How the gateway authenticates to a token endpoint (RFC 6749 §2.3). */
export type ClientAuth =
  | { readonly method: "none" }
  | { readonly method: "client_secret_basic" | "client_secret_post"; readonly secret: string };

/** An OAuth connection's client and endpoints, as the source record declares them. */
export interface OAuthClient {
  readonly client_id: string;
  readonly client_auth: ClientAuth;
  readonly token_endpoint: string;
  readonly revocation_endpoint?: string;
  /** RFC 8707 resource indicator, sent on the exchange and on refresh. */
  readonly resource?: string;
}

/**
 * A source whose authorization code buys an API key, not a token pair (its records'
 * protocol `pkce_user_key_v1`, OpenRouter's PKCE flow): its code and verifier are posted as JSON to
 * `endpoint`, which answers `{key}`, and the key is sealed as an API key.
 */
export interface KeyExchange {
  readonly endpoint: string;
}

/** What the binder hands the gateway to seal: the source record's values. */
export interface Source {
  readonly upstream: Upstream;
  readonly endpoints: readonly Endpoint[];
  readonly oauth?: OAuthClient;
  readonly key_exchange?: KeyExchange;
}

/** A pi-ai provider's or API's id. */
export const ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const HEADER = /^[a-z0-9][a-z0-9-]{0,63}$/;
const SCHEME = /^(?:[A-Za-z][A-Za-z0-9-]{0,31})?$/;
/** Visible ASCII: a secret is sent in a header, so it never holds a space, CR or LF. */
export const TOKEN = /^[\x21-\x7e]+$/;
const SCOPE = /^[\x20-\x7e]*$/;
const SCOPE_MAX = 4096;
/** An exact path, or a prefix ending `/*` (`/*` alone is every path at the origin). */
const PATH = /^\/(?:[A-Za-z0-9._~!$&'()+,;=:@-][A-Za-z0-9._~!$&'()+,;=:@/-]*)?(?:\/?\*)?$/;
const METHODS: readonly string[] = Object.freeze(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]);
/** Encoded separators and dots, which an upstream may decode past a path check. */
const ENCODED = /%(?:2f|5c|2e)|\\/i;

export const SECRET_MAX = 16 * 1024;
export const ENDPOINTS_MAX = 16;
const PATHS_MAX = 64;
const MODELS_MAX = 64;
/** The most APIs one source may name: more than the gateway runs (`api-rules.ts`). */
const APIS_MAX = 8;
const URL_MAX = 2048;

/** Whether `host` (as the URL parser gives it) is an IP literal rather than a name. */
function ipLiteral(host: string): boolean {
  return host.startsWith("[") || /^[0-9.]+$/.test(host) || /^0x/i.test(host);
}

/**
 * An `https:` URL with a hostname (not an IP literal, no trailing dot), no user info and
 * no fragment, written in its canonical form: exactly as the URL parser writes it back
 * (`href`), but for a bare origin, which may leave off the root's `/`. So an empty `?` or
 * `#`, an upper-case host or a dot segment never reaches a seal, and what is sealed is
 * what every later parse reads.
 */
function httpsUrl(value: unknown, query = false): URL {
  if (typeof value !== "string" || value.length > URL_MAX) {
    throw new Refusal("invalid_source");
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Refusal("invalid_source");
  }
  const canonical = url.href === value || (url.pathname === "/" && url.href === `${value}/`);
  if (!canonical || url.protocol !== "https:" || url.username !== "" || url.password !== ""
      || value.includes("#") || (value.includes("?") && (!query || url.search === ""))
      || ipLiteral(url.hostname)
      || url.hostname.endsWith(".") || ENCODED.test(url.pathname)) {
    throw new Refusal("invalid_source");
  }
  return url;
}

/** An `https:` origin, written exactly as `URL.origin` writes it. */
function origin(value: unknown): string {
  const url = httpsUrl(value);
  if (url.origin !== value) {
    throw new Refusal("invalid_source");
  }
  return url.origin;
}

export function endpoint(value: unknown): Endpoint {
  const fields = shape(value, ["origin", "paths", "methods"]);
  const methods = list(fields["methods"], 1, METHODS.length, (method) => {
    if (typeof method !== "string" || !METHODS.includes(method)) {
      throw new Refusal("invalid_source");
    }
    return method;
  });
  if (new Set(methods).size !== methods.length) {
    throw new Refusal("invalid_source");
  }
  return Object.freeze({
    origin: origin(fields["origin"]),
    paths: Object.freeze(list(fields["paths"], 1, PATHS_MAX, (path) => {
      const checked = text(path, PATH, 512);
      if (checked.includes("*") && !checked.endsWith("/*")) {
        throw new Refusal("invalid_source");
      }
      return checked;
    })),
    methods: Object.freeze(methods),
  });
}

/** An API id the gateway runs (`api-rules.ts`); any other is refused when it is sealed. */
function supported(api: string): string {
  if (apiRules(api) === null) {
    throw new Refusal("invalid_source");
  }
  return api;
}

/** How a secret is sent, checked. */
function authTemplate(value: unknown): AuthTemplate {
  const auth = shape(value, ["header", "scheme"]);
  return Object.freeze({ header: text(auth["header"], HEADER),
                         scheme: text(auth["scheme"], SCHEME) });
}

/**
 * A service's base URL and kind, held to its endpoints: the URL exactly its one endpoint's
 * one path, at that endpoint's origin, with no query, so a key is bound to the URL it was
 * given for and nothing else there.
 */
function serviceBase(kind: unknown, value: unknown, endpoints: readonly Endpoint[]): string {
  const base = httpsUrl(value);
  const [only] = endpoints;
  if (kind !== "service" || endpoints.length !== 1 || only!.origin !== base.origin
      || only!.paths.length !== 1 || only!.paths[0] !== base.pathname || base.search !== "") {
    throw new Refusal("invalid_source");
  }
  return value as string;
}

function serviceUpstream(value: unknown, endpoints: readonly Endpoint[]): ServiceUpstream {
  const fields = shape(value, ["kind", "base_url", "auth"]);
  return Object.freeze({ kind: "service",
                         base_url: serviceBase(fields["kind"], fields["base_url"], endpoints),
                         auth: authTemplate(fields["auth"]) });
}

/** What a service's key is sealed with (`serviceSource`). */
export interface ServiceSource {
  readonly upstream: ServiceUpstream;
  readonly endpoints: readonly Endpoint[];
}

/**
 * An upstream as a sealed blob holds it: a service's, or a model source's. A model source's
 * sealed before upstreams named their APIs (`api`, `base_url`) is read as its one row, until a
 * keyring sweep has resealed every such blob (`apis-contract.md`).
 */
export function upstream(value: unknown, endpoints: readonly Endpoint[]):
    Upstream | ServiceUpstream {
  if (object(value) && "kind" in value) {
    return serviceUpstream(value, endpoints);
  }
  if (object(value) && "api" in value) {
    const { api, base_url, ...rest } = shape(value, ["provider", "api", "base_url", "auth"],
                                             ["verify_url", "models"]);
    return modelUpstream({ ...rest, apis: [{ api, base_url }] }, endpoints);
  }
  return modelUpstream(value, endpoints);
}

/**
 * A source's APIs, each a supported one (`supported`) at its own base URL, every base at one
 * origin among `endpoints`, no API twice.
 */
function upstreamApis(value: unknown, endpoints: readonly Endpoint[]): readonly UpstreamApi[] {
  const apis = list(value, 1, APIS_MAX, (row) => {
    const fields = shape(row, ["api", "base_url"]);
    httpsUrl(fields["base_url"]);
    return Object.freeze({ api: supported(text(fields["api"], ID)),
                           base_url: fields["base_url"] as string });
  });
  const origins = new Set(apis.map((row) => new URL(row.base_url).origin));
  if (origins.size !== 1 || !endpoints.some((entry) => origins.has(entry.origin))
      || new Set(apis.map((row) => row.api)).size !== apis.length) {
    throw new Refusal("invalid_source");
  }
  return Object.freeze(apis);
}

/**
 * A model source's upstream; a declared endpoint (`custom`) speaks one API, one with a
 * declared adapter.
 */
function modelUpstream(value: unknown, endpoints: readonly Endpoint[]): Upstream {
  const fields = shape(value, ["provider", "apis", "auth"], ["verify_url", "models"]);
  const provider = text(fields["provider"], ID);
  const apis = upstreamApis(fields["apis"], endpoints);
  const verify = fields["verify_url"];
  if (verify !== undefined && !allows(endpoints, "GET", httpsUrl(verify, true))) {
    throw new Refusal("invalid_source");
  }
  const custom = provider === "custom";
  if (custom !== (fields["models"] !== undefined)
      || (custom && (apis.length !== 1 || !declaredApi(apis[0]!.api)))) {
    throw new Refusal("invalid_source");
  }
  return Object.freeze({
    provider,
    apis,
    auth: authTemplate(fields["auth"]),
    ...(verify === undefined ? {} : { verify_url: verify as string }),
    ...(custom ? { models: Object.freeze(list(fields["models"], 1, MODELS_MAX, checkedRecord)) } : {}),
  });
}

function clientAuth(value: unknown): ClientAuth {
  const fields = shape(value, ["method"], ["secret"]);
  const method = fields["method"];
  if (method === "none" && fields["secret"] === undefined) {
    return Object.freeze({ method });
  }
  if (method === "client_secret_basic" || method === "client_secret_post") {
    return Object.freeze({ method, secret: text(fields["secret"], TOKEN, SECRET_MAX) });
  }
  throw new Refusal("invalid_source");
}

export const CLIENT_KEYS = Object.freeze(["client_id", "client_auth", "token_endpoint"]);
export const CLIENT_OPTIONAL = Object.freeze(["revocation_endpoint", "resource"]);

export function oauthClient(fields: Record<string, unknown>): OAuthClient {
  const revocation = fields["revocation_endpoint"];
  const resource = fields["resource"];
  return {
    client_id: text(fields["client_id"], TOKEN, 512),
    client_auth: clientAuth(fields["client_auth"]),
    token_endpoint: httpsUrl(fields["token_endpoint"]).href,
    ...(revocation === undefined ? {} : { revocation_endpoint: httpsUrl(revocation).href }),
    ...(resource === undefined ? {} : { resource: httpsUrl(resource, true).href }),
  };
}

/** The source record values the binder hands over to be sealed, checked. */
export function source(value: unknown, oauth: boolean): Source {
  const fields = shape(value, oauth ? ["upstream", "endpoints", "oauth"] : ["upstream", "endpoints"]);
  const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
  const checked = { upstream: modelUpstream(fields["upstream"], endpoints), endpoints };
  if (!oauth) {
    return Object.freeze(checked);
  }
  return Object.freeze({
    ...checked,
    oauth: Object.freeze(oauthClient(shape(fields["oauth"], CLIENT_KEYS, CLIENT_OPTIONAL))),
  });
}

/**
 * The methods MCP's streamable HTTP transport sends its one endpoint (spec 2025-11-25,
 * Transports: a message `POST`, the stream's `GET`, the session's end `DELETE`).
 */
const SERVICE_METHODS: readonly string[] = Object.freeze(["POST", "GET", "DELETE"]);

/** A header's value as text: printable ASCII. */
const HEADER_TEXT = /^[\x20-\x7e]*$/;

/**
 * The headers MCP's streamable HTTP transport sends beyond HTTP's own, by lower-case name,
 * with the values each may take: 2025-11-25's session and resumption, 2026-07-28's method and
 * name, and its `Mcp-Param-{Name}` for each tool parameter a server marks `x-mcp-header` (a
 * prefix, `*`).
 */
const SERVICE_HEADERS: Readonly<Record<string, RegExp>> = Object.freeze({
  "mcp-protocol-version": /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/,
  "mcp-session-id": HEADER_TEXT, "last-event-id": HEADER_TEXT,
  "mcp-method": HEADER_TEXT, "mcp-name": HEADER_TEXT, "mcp-param-*": HEADER_TEXT,
});

/** The query and header rules a scope's calls are held to beyond its sealed endpoints. */
type CallRules = Pick<ApiRules, "query" | "headers" | "betas">;
/** A service's: the transport's headers, and no query (its sealed URL has none). */
const SERVICE_RULES: CallRules = Object.freeze({
  query: Object.freeze({}), headers: SERVICE_HEADERS, betas: Object.freeze({}),
});

/** An API's rules, which every scope's API has (`supported`). */
function rulesOf(api: string): ApiRules {
  const rules = apiRules(api);
  if (rules === null) {
    throw new Error(`the scope's API ${api} has no rules`);
  }
  return rules;
}

/**
 * The rules a call of `method path` on `scope` is held to, any one of which may take a value:
 * a service's, the transport's; a model call's (`scopeApi`), its API's alone; any other (a
 * read, a free call), those of every API the scope speaks, each of which may make it.
 */
function callRules(scope: EgressScope, method: string, path: string): readonly CallRules[] {
  if ("kind" in scope) {
    return [SERVICE_RULES];
  }
  const own = scopeApi(scope, method, path);
  return own === null ? scope.apis.map((row) => rulesOf(row.api)) : [rulesOf(own.api)];
}

/**
 * The values a key the owner gave for the service at `url` is sealed with: that URL alone
 * (its origin, its exact path, no query), on the transport's methods, its secret sent as
 * `auth` says, at a host that is no IP address (`httpsUrl`). The door that takes the owner's
 * URL holds its host to the egress table's own rule too (`doors/src/connect.ts`).
 */
export function serviceSource(url: unknown, auth: unknown): ServiceSource {
  const parsed = httpsUrl(url);
  if (parsed.search !== "") {
    throw new Refusal("invalid_source");
  }
  const endpoints = Object.freeze([endpoint({ origin: parsed.origin, paths: [parsed.pathname],
                                              methods: SERVICE_METHODS })]);
  return Object.freeze({
    upstream: serviceUpstream({ kind: "service", base_url: parsed.href, auth }, endpoints),
    endpoints });
}

/**
 * What a pasted key is sealed with, checked: a model source record's values (`source`), or
 * a service's (`serviceSource`'s, held to its own rule again).
 */
export function keySource(value: unknown): Source | ServiceSource {
  const fields = shape(value, ["upstream", "endpoints"]);
  if (!(object(fields["upstream"]) && "kind" in fields["upstream"])) {
    return source(value, false);
  }
  const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
  return Object.freeze({ upstream: serviceUpstream(fields["upstream"], endpoints), endpoints });
}

/** The source record values of a key-exchange source (`KeyExchange`), checked. */
export function keyExchangeSource(value: unknown): Source & { readonly key_exchange: KeyExchange } {
  const fields = shape(value, ["upstream", "endpoints", "key_exchange"]);
  const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
  const exchange = shape(fields["key_exchange"], ["endpoint"]);
  return Object.freeze({
    upstream: modelUpstream(fields["upstream"], endpoints),
    endpoints,
    key_exchange: Object.freeze({ endpoint: httpsUrl(exchange["endpoint"]).href }),
  });
}

/** A secret as it is sealed: visible ASCII, so it can only ever be one header value. */
export function secret(value: unknown): string {
  return text(value, TOKEN, SECRET_MAX);
}

/** A granted scope as it is sealed (RFC 6749 §3.3): printable ASCII, spaces between. */
export function scope(value: unknown): string {
  return text(value, SCOPE, SCOPE_MAX);
}

/** Whether `path` is one of `paths`: equal to an exact entry, or under a `/*` prefix. */
function pathAllowed(paths: readonly string[], path: string): boolean {
  return paths.some((entry) => entry.endsWith("/*")
    ? path.startsWith(entry.slice(0, -1))
    : path === entry);
}

/**
 * Whether a credential sealed for `endpoints` may be sent as `method url`: a URL with
 * no user info at one of the sealed origins (each `https:`, so the URL is too), a sealed
 * method there, and a sealed path, with no encoded separator or dot an upstream could
 * decode past the check.
 */
export function allows(endpoints: readonly Endpoint[], method: string, url: URL): boolean {
  if (url.username !== "" || url.password !== "" || ENCODED.test(url.pathname)) {
    return false;
  }
  return endpoints.some((entry) => entry.origin === url.origin
                                   && entry.methods.includes(method)
                                   && pathAllowed(entry.paths, url.pathname));
}

/**
 * One test of a key's limits answer (`CreditRead`): the value at `path` is `equals`, or a
 * number at most `at_most`.
 */
export type NoCredit =
  | { readonly path: readonly string[]; readonly equals: boolean }
  | { readonly path: readonly string[]; readonly at_most: number };

/**
 * Where a source reports what a key can pay for, from its source record (`credit`): a `GET`
 * of `url` with the sealed secret, whose JSON answer says the key can pay for nothing when
 * any of `none_when` holds. Every test's path must be in the answer (its value may be null),
 * so an answer of another shape is a failed read, never a key that pays.
 */
export interface CreditRead {
  readonly url: string;
  readonly none_when: readonly NoCredit[];
}

const PATH_KEY = /^[A-Za-z0-9_-]{1,64}$/;
const PATH_MAX = 8;
const NO_CREDIT_MAX = 8;

/** A path through a read's JSON answer (`valueAtPath`): 1 to `PATH_MAX` plain keys. */
function answerPath(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > PATH_MAX
      || !value.every((key) => typeof key === "string" && PATH_KEY.test(key))) {
    throw new Refusal("invalid_request");
  }
  return Object.freeze([...value] as string[]);
}

/**
 * A read's URL: an `https:` URL written as the URL parser writes it, with no query or
 * fragment. The gateway sends it only where the sealed endpoints allow a `GET` (`allows`),
 * so it reaches nothing a sandbox's read could not.
 */
function readUrl(value: unknown): string {
  if (typeof value !== "string") {
    throw new Refusal("invalid_request");
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Refusal("invalid_request");
  }
  if (url.protocol !== "https:" || url.href !== value || url.search !== "" || url.hash !== "") {
    throw new Refusal("invalid_request");
  }
  return url.href;
}

function noCredit(value: unknown): NoCredit {
  const path = answerPath(object(value) ? value["path"] : undefined);
  if (keyed(value, ["path", "equals"]) && typeof value["equals"] === "boolean") {
    return Object.freeze({ path, equals: value["equals"] });
  }
  if (keyed(value, ["path", "at_most"]) && typeof value["at_most"] === "number"
      && Number.isFinite(value["at_most"])) {
    return Object.freeze({ path, at_most: value["at_most"] });
  }
  throw new Refusal("invalid_request");
}

/**
 * A credit read's `url` (`readUrl`) and `none_when`, checked (`invalid_request`): one to
 * `NO_CREDIT_MAX` tests. Which other keys it may come with is its holder's: a source
 * record's (`sources/src/check.ts`) or the owner's call (`credit_read`).
 */
export function creditRead(value: unknown): CreditRead {
  if (!object(value) || !Array.isArray(value["none_when"]) || value["none_when"].length < 1
      || value["none_when"].length > NO_CREDIT_MAX) {
    throw new Refusal("invalid_request");
  }
  return Object.freeze({ url: readUrl(value["url"]),
                         none_when: Object.freeze(value["none_when"].map(noCredit)) });
}

/**
 * Whether a key whose limits answer is `body` can pay for a priced model: none of `read`'s
 * tests holds. Null when a test's path is not in the answer: an answer of another shape.
 */
export function paysBy(read: CreditRead, body: unknown): boolean | null {
  let none = false;
  for (const test of read.none_when) {
    const value = valueAtPath(body, test.path);
    if (value === undefined) {
      return null;
    }
    none ||= "equals" in test ? value === test.equals
      : typeof value === "number" && value <= test.at_most;
  }
  return !none;
}

/**
 * Where a source's vendor lists the models it serves a key, from its source record
 * (`listing`): a `GET` of `url` with the sealed secret, whose JSON answer holds a list of
 * rows at `rows`, each a model it serves, its id at `id` and, where the vendor gives one, its
 * score at `score`, higher stronger.
 */
export interface ListingRead {
  readonly url: string;
  readonly rows: readonly string[];
  readonly id: readonly string[];
  readonly score?: readonly string[];
}

/**
 * A listing read's `url` (`readUrl`) and its paths, `score` optional, checked
 * (`invalid_request`). Which other keys it may come with is its holder's, as a credit read's
 * are (`listing_read`).
 */
export function listingRead(value: unknown): ListingRead {
  if (!object(value)) {
    throw new Refusal("invalid_request");
  }
  return Object.freeze({ url: readUrl(value["url"]), rows: answerPath(value["rows"]),
                         id: answerPath(value["id"]),
                         ...value["score"] === undefined ? {}
                           : { score: answerPath(value["score"]) } });
}

/** One model a listing serves, with its score; null where the vendor gives it none. */
export interface ListedModel {
  readonly id: string;
  readonly score: number | null;
}

/**
 * The models a listing answer `body` serves, by `read`: each row's id, with its score where
 * `read` names one and the row has a finite number there. Null for an answer of another
 * shape: no list at `rows`, no row, a row with no id, or, where `read` names a score, not one
 * score in it (a renamed path would otherwise read as nothing served, or nothing scored).
 */
export function listedBy(read: ListingRead, body: unknown): ListedModel[] | null {
  const rows = valueAtPath(body, read.rows);
  if (!Array.isArray(rows) || rows.length === 0) {
    return null;
  }
  const listed: ListedModel[] = [];
  for (const row of rows) {
    const id = valueAtPath(row, read.id);
    if (typeof id !== "string") {
      return null;
    }
    const score = read.score === undefined ? null : valueAtPath(row, read.score);
    listed.push(Object.freeze({
      id, score: typeof score === "number" && Number.isFinite(score) ? score : null }));
  }
  return read.score !== undefined && listed.every((one) => one.score === null) ? null : listed;
}

/**
 * Where a sealed credential may be forwarded: its base URL and its endpoints, none of
 * them secret. The setup check hands them to the binder, which commits them with the
 * connection, so the owner's object can refuse a sandbox request to an endpoint that is not
 * sealed before anything reaches the gateway; the gateway still checks the sealed ones. A
 * model source's (`ModelScope`) or a service's (`ServiceScope`).
 */
export type EgressScope = ModelScope | ServiceScope;

/** A service's scope: its one URL, never metered (`ServiceUpstream`). */
export interface ServiceScope {
  readonly kind: "service";
  readonly base_url: string;
  readonly endpoints: readonly Endpoint[];
}

/** One sealed API of a model source's scope. */
export interface ScopeApi {
  /** pi-ai's id: its rules (`api-rules.ts`) shape every metered call on it. */
  readonly api: string;
  /** Where its models are: the base a run on one of them is given (`egress.ts`). */
  readonly base_url: string;
  /** pi-ai's own model call for the API (`meter.ts`), or null: none on it can be metered. */
  readonly meter: Meter | null;
}

/** A model source's scope: its sealed APIs, all at one origin, and its endpoints. */
export interface ModelScope {
  readonly apis: readonly ScopeApi[];
  readonly endpoints: readonly Endpoint[];
}

/** Where a model call of a sealed API names its model and caps its output (`meter.ts`). */
export interface Meter {
  readonly method: string;
  /** The call's path; `{model}` where the model id is a path segment's text. */
  readonly path: string;
  /** The body field naming the model, or null when the path names it. */
  readonly model_field: readonly string[] | null;
  /** The body field capping the output tokens. */
  readonly output_field: readonly string[];
}

const FIELD = /^[A-Za-z0-9_]{1,64}$/;

function field(value: unknown): readonly string[] {
  return Object.freeze(list(value, 1, 4, (key) => text(key, FIELD, 64)));
}

function meterOf(value: unknown): Meter | null {
  if (value === null) {
    return null;
  }
  const fields = shape(value, ["method", "path", "model_field", "output_field"]);
  const method = fields["method"];
  if (typeof method !== "string" || !METHODS.includes(method)) {
    throw new Refusal("invalid_source");
  }
  const path = text(fields["path"], /^\/[\x21-\x7e]{0,511}$/, 512);
  const holes = path.split("{model}").length - 1;
  const named = fields["model_field"] === null ? null : field(fields["model_field"]);
  if (holes > 1 || (holes === 1) === (named !== null)) {
    throw new Refusal("invalid_source");
  }
  return Object.freeze({ method, path, model_field: named,
                         output_field: field(fields["output_field"]) });
}

/** A scope checked whole, by the grammar a seal is held to; anything else is `invalid_source`. */
export function egressScope(value: unknown): EgressScope {
  if (object(value) && "kind" in value) {
    const fields = shape(value, ["kind", "base_url", "endpoints"]);
    const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
    return Object.freeze({ kind: "service",
                           base_url: serviceBase(fields["kind"], fields["base_url"], endpoints),
                           endpoints });
  }
  // A scope committed before it named its APIs (`api`, `base_url`, `meter`) is its one row;
  // it goes once no stored or exported connection holds one (`apis-contract.md`).
  const old = object(value) && "api" in value;
  const fields = old ? shape(value, ["api", "base_url", "endpoints", "meter"])
    : shape(value, ["apis", "endpoints"]);
  const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
  const rows = list(old ? [{ api: fields["api"], base_url: fields["base_url"],
                             meter: fields["meter"] }] : fields["apis"], 1, APIS_MAX,
                    (row) => shape(row, ["api", "base_url", "meter"]));
  const apis = upstreamApis(rows.map(({ api, base_url }) => ({ api, base_url })), endpoints);
  return Object.freeze({
    apis: Object.freeze(apis.map((row, index) =>
      Object.freeze({ ...row, meter: meterOf(rows[index]!["meter"]) }))),
    endpoints });
}

/** The origin every call on `scope` goes to: its APIs' one origin (`upstreamApis`). */
function scopeOrigin(scope: EgressScope): string {
  return new URL("kind" in scope ? scope.base_url : scope.apis[0]!.base_url).origin;
}

/** Whether `method path` is `meter`'s call: its method, and its path with a model id there. */
function meters(meter: Meter | null, method: string, path: string): boolean {
  if (meter === null || method !== meter.method) {
    return false;
  }
  const [head, tail] = meter.path.split("{model}");
  if (tail === undefined) {
    return path === meter.path;
  }
  return path.startsWith(head!) && path.endsWith(tail) && path.length > head!.length + tail.length
    && /^[^/:?#]+$/.test(path.slice(head!.length, path.length - tail.length));
}

/**
 * The scope's API whose model call `method path` (its target's path, `egressTarget`) is:
 * its meter's method, and its path with a model id (no separator) where the meter names one;
 * null for a call no API meters. The gateway reads the model id the same way
 * (`meteredModel`); this is the owner's check at open, and both hold the call to that API's
 * rules alone.
 */
export function scopeApi(scope: ModelScope, method: string, path: string): ScopeApi | null {
  return scope.apis.find((row) => meters(row.meter, method, path)) ?? null;
}

/**
 * The scope's API `model` speaks: the one its record names, or, naming none, the scope's one
 * API (`ModelRecord.api`); null for an API the scope is not sealed for.
 */
export function modelApi(scope: ModelScope, model: Pick<ModelRecord, "api">): ScopeApi | null {
  if (model.api === undefined) {
    return scope.apis.length === 1 ? scope.apis[0]! : null;
  }
  return scope.apis.find((row) => row.api === model.api) ?? null;
}

/** Whether `method path` is one of the scope's metered model calls (`scopeApi`). */
export function meteredCall(scope: ModelScope, method: string, path: string): boolean {
  return scopeApi(scope, method, path) !== null;
}

/**
 * Whether a call is one the scope's provider does not bill on any API it speaks (`freeCall`:
 * a read, or a cited free call such as a token count).
 */
export function freeOn(scope: ModelScope, provider: string, method: string,
                       path: string): boolean {
  return scope.apis.some((row) => freeCall(rulesOf(row.api), provider, method, path));
}

/**
 * The model id a call names under `meter` (`body`, its JSON; `path` without its query), or
 * null when it is not the meter's call: read where pi-ai's own call names it, a body field
 * or a path segment. The owner's object reads it at open to reserve the call for that
 * model, and the gateway again, to refuse a body naming another.
 */
export function meteredModel(meter: Meter, method: string, path: string,
                             body: unknown): string | null {
  if (method !== meter.method) {
    return null;
  }
  const [head, tail] = meter.path.split("{model}");
  if (tail === undefined) {
    if (path !== meter.path || meter.model_field === null) {
      return null;
    }
    const named = valueAtPath(body, meter.model_field);
    return typeof named === "string" && named.length > 0 ? named : null;
  }
  if (!path.startsWith(head!) || !path.endsWith(tail) || path.length <= head!.length + tail.length) {
    return null;
  }
  let named: string;
  try {
    named = decodeURIComponent(path.slice(head!.length, path.length - tail.length));
  } catch {
    return null;
  }
  return /^[^/:?#]+$/.test(named) ? named : null;
}

/**
 * Query parameters that carry an API key (Google's `key` among them), stripped from every
 * call, under any spelling of their names, as some servers read them case-blind.
 */
const CREDENTIAL_PARAMS: readonly string[] = Object.freeze(["key", "api_key", "access_token"]);

/**
 * `params` as they may be sent under `allowed` (`ApiRules.query`), written back by the
 * URL parser so what is sent is what was read: each credential parameter dropped, or null
 * when any other is not allowed or holds a value its rule does not take.
 */
function keptQuery(allowed: readonly Readonly<Record<string, RegExp>>[],
                   params: URLSearchParams): string | null {
  const kept = new URLSearchParams();
  for (const [name, value] of params) {
    if (CREDENTIAL_PARAMS.includes(name.toLowerCase())) {
      continue;
    }
    if (!allowed.some((rules) => Object.hasOwn(rules, name) && rules[name]!.test(value))) {
      return null;
    }
    kept.append(name, value);
  }
  return kept.toString();
}

/**
 * The URL a sandbox request for `path` (its path and query, as the egress door hands it
 * on) is sent to: the path at the base URL's origin, and only when it stays there and
 * `method` and the path are sealed (`allows`), with only the query its API's rules allow (a
 * service's: none). Null otherwise. The gateway's `forward` and the owner's `egressOpen`
 * both ask this, so the two can never disagree.
 */
export function egressTarget(scope: EgressScope, method: string, path: string): URL | null {
  if (!path.startsWith("/")) {
    return null;
  }
  const origin = scopeOrigin(scope);
  let target: URL;
  try {
    target = new URL(path, origin);
  } catch {
    return null;
  }
  if (target.origin !== origin || !allows(scope.endpoints, method, target)) {
    return null;
  }
  const query = keptQuery(callRules(scope, method, target.pathname).map((rules) => rules.query),
                          target.searchParams);
  if (query === null) {
    return null;
  }
  target.search = query;
  return target;
}

/**
 * HTTP's own headers, which every API and the transport take: `content-type` held to JSON in
 * UTF-8, since a metered body is the gateway's own UTF-8 JSON and the provider must read it as
 * the gateway parsed it.
 */
const BASE_HEADERS: Readonly<Record<string, RegExp>> = Object.freeze({
  accept: HEADER_TEXT,
  "content-type": /^application\/json(?: *; *charset=utf-8)?$/i,
  "user-agent": HEADER_TEXT,
});

/**
 * The header that says how a body's bytes are coded, which goes with the body when the body
 * goes as it came: a free call's, which streams through unread. A metered call's body is the
 * gateway's own, uncoded (a coded one is refused before it is read, `forward.ts`).
 */
const BODY_HEADERS: Readonly<Record<string, RegExp>> = Object.freeze({
  "content-encoding": HEADER_TEXT,
});

/** The value rule `rules` keeps a header named `name` by (an exact name, or a `*` prefix). */
function headerRule(rules: Readonly<Record<string, RegExp>>, name: string): RegExp | undefined {
  if (Object.hasOwn(rules, name)) {
    return rules[name];
  }
  return Object.entries(rules)
    .find(([key]) => key.endsWith("*") && name.startsWith(key.slice(0, -1)))?.[1];
}

/**
 * The headers of `sent`, a sandbox request, that go on with it: HTTP's own (`BASE_HEADERS`),
 * its API's (`ApiRules.headers`; a service's, the transport's) and, on a `free` call, its
 * API's betas and its body's coding (`BODY_HEADERS`), each with a value its rule takes. Every
 * other is dropped, every credential header among them, since no list names one: the sealed
 * one is the gateway's to set.
 */
export function egressHeaders(scope: EgressScope, method: string, path: string, free: boolean,
                              sent: Headers): Headers {
  const lists = [BASE_HEADERS, ...callRules(scope, method, path).flatMap((rules) =>
    [rules.headers, ...(free ? [rules.betas] : [])]), ...(free ? [BODY_HEADERS] : [])];
  const kept = new Headers();
  for (const [name, value] of sent) {
    if (lists.some((list) => headerRule(list, name)?.test(value) === true)) {
      kept.append(name, value);
    }
  }
  return kept;
}
