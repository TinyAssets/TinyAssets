/**
 * Where a sealed credential may go (research §1.3, §2.4; G4): the source values the binder
 * hands the gateway to seal (the pi-ai provider and API, the base URL, how the secret is
 * sent, the endpoints it may reach, an OAuth client), the egress scope a committed
 * connection keeps, and the rules that judge a request against them. The gateway seals and
 * opens by these (`gateway/src/credential.ts`), the source records are checked by them
 * (`sources/src/check.ts`), and the owner's object admits a sandbox call by them
 * (`owner/src/egress.ts`), so the three can never disagree.
 *
 * Every value is checked when it is sealed and again when it is opened, so a blob written
 * by older code that no longer fits fails loudly rather than being half-read; anything else
 * is `invalid_source`, the gateway's fixed code.
 */
import { count, list, shape, text } from "./checks";
import { Refusal } from "./refusal";
import { checkedRecord, type ModelRecord } from "./model-record";

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

/** The source data sealed with the secret (§2.4). */
export interface Upstream {
  /** A pi-ai provider id, or `custom` for a declared endpoint. */
  readonly provider: string;
  /** The pi-ai API id its models speak. */
  readonly api: string;
  readonly base_url: string;
  readonly auth: AuthTemplate;
  /** A `GET` that answers 2xx when the secret is good; one of the endpoints. */
  readonly verify_url?: string;
  /** For `custom` only: the models it serves, each its whole record (`model-record.ts`). */
  readonly models?: readonly ModelRecord[];
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
 * A source whose authorization code buys an API key, not a token pair (the Python's
 * `pkce_user_key_v1`, OpenRouter's PKCE flow): its code and verifier are posted as JSON to
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

/**
 * The pi-ai 1.4.0 APIs the gateway runs: those a sandbox call can be metered on (each has
 * its rules, `api-rules.ts` `ruledApis`, which a gateway test holds equal), since egress is
 * the one model path and an API with no rules could be sealed but never used. Each sends every request through `fetch`, so the call's
 * endpoint guard sees it, either as `options.fetch` (`anthropic-messages.js:422`,
 * `openai-responses.js:138`, `openai-completions.js:186`, `mistral-conversations.js:180`) or,
 * for Google's, which refuses a custom fetch, through the global-fetch interposer
 * (`egress.ts`). Every other API is refused as `invalid_source`: Bedrock signs with a Node
 * HTTP handler outside `fetch`, and `openai-codex-responses` is the legacy ChatGPT backend
 * (research §2.6: Sign in with ChatGPT reaches only `POST /v1/responses`, through
 * `openai-responses`), with a WebSocket transport and per-session caches kept in module state.
 */
export const SUPPORTED_APIS: readonly string[] = Object.freeze([
  "anthropic-messages", "openai-responses", "openai-completions", "mistral-conversations",
  "google-generative-ai",
]);

/** The supported APIs whose adapters refuse `options.fetch` (held by `egress.ts`). */
export const GLOBAL_FETCH_APIS: readonly string[] = Object.freeze(["google-generative-ai"]);

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

/** An API id the gateway runs; any other is refused when it is sealed. */
function supported(api: string): string {
  if (!SUPPORTED_APIS.includes(api)) {
    throw new Refusal("invalid_source");
  }
  return api;
}

export function upstream(value: unknown, endpoints: readonly Endpoint[]): Upstream {
  const fields = shape(value, ["provider", "api", "base_url", "auth"], ["verify_url", "models"]);
  const auth = shape(fields["auth"], ["header", "scheme"]);
  const provider = text(fields["provider"], ID);
  const base = httpsUrl(fields["base_url"]);
  if (!endpoints.some((entry) => entry.origin === base.origin)) {
    throw new Refusal("invalid_source");
  }
  const verify = fields["verify_url"];
  if (verify !== undefined && !allows(endpoints, "GET", httpsUrl(verify, true))) {
    throw new Refusal("invalid_source");
  }
  const custom = provider === "custom";
  if (custom !== (fields["models"] !== undefined)) {
    throw new Refusal("invalid_source");
  }
  return Object.freeze({
    provider,
    api: supported(text(fields["api"], ID)),
    base_url: fields["base_url"] as string,
    auth: Object.freeze({ header: text(auth["header"], HEADER), scheme: text(auth["scheme"], SCHEME) }),
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
  const checked = { upstream: upstream(fields["upstream"], endpoints), endpoints };
  if (!oauth) {
    return Object.freeze(checked);
  }
  return Object.freeze({
    ...checked,
    oauth: Object.freeze(oauthClient(shape(fields["oauth"], CLIENT_KEYS, CLIENT_OPTIONAL))),
  });
}

/** The source record values of a key-exchange source (`KeyExchange`), checked. */
export function keyExchangeSource(value: unknown): Source & { readonly key_exchange: KeyExchange } {
  const fields = shape(value, ["upstream", "endpoints", "key_exchange"]);
  const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
  const exchange = shape(fields["key_exchange"], ["endpoint"]);
  return Object.freeze({
    upstream: upstream(fields["upstream"], endpoints),
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
 * Where a sealed credential may be forwarded (G4): its base URL and its endpoints, none of
 * them secret. The setup check hands them to the binder, which commits them with the
 * connection, so the owner's object can refuse a sandbox request to an endpoint that is not
 * sealed before anything reaches the gateway; the gateway still checks the sealed ones.
 */
export interface EgressScope {
  /** The sealed API, pi-ai's id: its rules (`api-rules.ts`) shape every metered call. */
  readonly api: string;
  readonly base_url: string;
  readonly endpoints: readonly Endpoint[];
  /** pi-ai's own model call for the sealed API (`meter.ts`), or null: none can be metered. */
  readonly meter: Meter | null;
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
  const fields = shape(value, ["api", "base_url", "endpoints", "meter"]);
  const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
  const base = httpsUrl(fields["base_url"]);
  if (!endpoints.some((entry) => entry.origin === base.origin)) {
    throw new Refusal("invalid_source");
  }
  return Object.freeze({ api: supported(text(fields["api"], ID, 64)),
                         base_url: fields["base_url"] as string, endpoints,
                         meter: meterOf(fields["meter"]) });
}

/**
 * Whether `method path` is the scope's metered model call: its meter's method, and its
 * path with a model id (no separator) where the meter names one. The gateway reads the
 * model id the same way (`meteredModel`); this is the owner's check at open.
 */
export function meteredCall(scope: EgressScope, method: string, path: string): boolean {
  const meter = scope.meter;
  if (meter === null || method !== meter.method) {
    return false;
  }
  const bare = path.split("?")[0]!;
  const [head, tail] = meter.path.split("{model}");
  if (tail === undefined) {
    return bare === meter.path;
  }
  return bare.startsWith(head!) && bare.endsWith(tail) && bare.length > head!.length + tail.length
    && /^[^/:?#]+$/.test(bare.slice(head!.length, bare.length - tail.length));
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
    const named = meter.model_field.reduce<unknown>((at, key) =>
      (typeof at === "object" && at !== null && !Array.isArray(at)
        ? (at as Record<string, unknown>)[key] : undefined), body);
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
 * The URL a sandbox request for `path` (its path and query, as the egress door hands it
 * on) is sent to: the path at the base URL's origin, and only when it stays there and
 * `method` and the path are sealed (`allows`). Null otherwise. The gateway's `forward` and
 * the owner's `egressOpen` both ask this, so the two can never disagree.
 */
export function egressTarget(scope: EgressScope, method: string, path: string): URL | null {
  if (!path.startsWith("/")) {
    return null;
  }
  const origin = new URL(scope.base_url).origin;
  let target: URL;
  try {
    target = new URL(path, origin);
  } catch {
    return null;
  }
  return target.origin === origin && allows(scope.endpoints, method, target) ? target : null;
}
