/**
 * What a sealed credential holds (research §1.3, §2.4), and where it may go.
 *
 * The plaintext inside a blob carries the secret together with the source data it was
 * connected under: the pi-ai provider and API that speak to it, the base URL, how the
 * secret is sent (`auth`), and the endpoints it may reach. Those values come from the
 * source record at connect time (data, never code) and are sealed with the secret, so a
 * loop bug, a hook or a crafted request cannot point a real key at another host: the
 * gateway sends a secret only where the opened plaintext says (`allows`), never to a URL a
 * request names.
 *
 * Every value is checked when it is sealed and again when it is opened, so a blob written
 * by older code that no longer fits fails loudly rather than being half-read.
 */
import { Failure } from "./failure";
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
  /** `provider`: pi-ai's own refresh for `upstream.provider`; `rfc6749`: the generic grant. */
  readonly refresh_with: "provider" | "rfc6749";
}

/** An OAuth connection's tokens, with its client. */
export interface OAuthGrant extends OAuthClient {
  readonly access: string;
  readonly refresh?: string;
  /** Epoch ms the access token expires, or null when the server gave no lifetime. */
  readonly expires_at: number | null;
  readonly scope?: string;
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

interface Common {
  readonly upstream: Upstream;
  readonly endpoints: readonly Endpoint[];
  readonly sealed_at: number;
}

/** The sealed plaintext. */
export type Credential =
  | (Common & { readonly kind: "api_key"; readonly secret: string })
  | (Common & { readonly kind: "oauth2"; readonly oauth: OAuthGrant });

/**
 * The pi-ai 1.4.0 APIs the gateway runs: each sends every request through `fetch`, so the
 * call's endpoint guard sees it, either as `options.fetch` (`anthropic-messages.js:422`,
 * `openai-responses.js:138`, `openai-completions.js:186`, `mistral-conversations.js:180`,
 * `azure-openai-responses.js:128`, `pi-messages.js:271`) or, for Google's two, which
 * refuse a custom fetch, through the global-fetch interposer (`egress.ts`). Every other
 * API is refused as `unsupported_source`: Bedrock signs with a Node HTTP handler outside
 * `fetch`, and `openai-codex-responses` is the legacy ChatGPT backend (research §2.6:
 * Sign in with ChatGPT reaches only `POST /v1/responses`, through `openai-responses`),
 * with a WebSocket transport and per-session caches kept in module state.
 */
export const SUPPORTED_APIS: readonly string[] = Object.freeze([
  "anthropic-messages", "openai-responses", "openai-completions", "mistral-conversations",
  "azure-openai-responses", "pi-messages", "google-generative-ai", "google-vertex",
]);

const ID = /^[a-z0-9][a-z0-9-]{0,63}$/;
const HEADER = /^[a-z0-9][a-z0-9-]{0,63}$/;
const SCHEME = /^(?:[A-Za-z][A-Za-z0-9-]{0,31})?$/;
/** Visible ASCII: a secret is sent in a header, so it never holds a space, CR or LF. */
const TOKEN = /^[\x21-\x7e]+$/;
/** An exact path, or a prefix ending `/*` (`/*` alone is every path at the origin). */
const PATH = /^\/(?:[A-Za-z0-9._~!$&'()+,;=:@-][A-Za-z0-9._~!$&'()+,;=:@/-]*)?(?:\/?\*)?$/;
const METHODS: readonly string[] = Object.freeze(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]);
/** Encoded separators and dots, which an upstream may decode past a path check. */
const ENCODED = /%(?:2f|5c|2e)|\\/i;

const SECRET_MAX = 16 * 1024;
const ENDPOINTS_MAX = 16;
const PATHS_MAX = 64;
const MODELS_MAX = 64;
const URL_MAX = 2048;

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Refuses `value` unless its keys are all in `allowed` and include every one of `required`. */
function shape(value: unknown, required: readonly string[], optional: readonly string[] = []):
    Record<string, unknown> {
  if (!object(value)) {
    throw new Failure("invalid_source");
  }
  const keys = Object.keys(value);
  if (required.some((key) => !keys.includes(key))
      || keys.some((key) => !required.includes(key) && !optional.includes(key))) {
    throw new Failure("invalid_source");
  }
  return value;
}

function text(value: unknown, pattern: RegExp, max = 1024): string {
  if (typeof value !== "string" || value.length > max || !pattern.test(value)) {
    throw new Failure("invalid_source");
  }
  return value;
}

function count(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new Failure("invalid_source");
  }
  return value;
}

function list<T>(value: unknown, min: number, max: number, each: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    throw new Failure("invalid_source");
  }
  return value.map(each);
}

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
    throw new Failure("invalid_source");
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Failure("invalid_source");
  }
  const canonical = url.href === value || (url.pathname === "/" && url.href === `${value}/`);
  if (!canonical || url.protocol !== "https:" || url.username !== "" || url.password !== ""
      || value.includes("#") || (value.includes("?") && (!query || url.search === ""))
      || ipLiteral(url.hostname)
      || url.hostname.endsWith(".") || ENCODED.test(url.pathname)) {
    throw new Failure("invalid_source");
  }
  return url;
}

/** An `https:` origin, written exactly as `URL.origin` writes it. */
function origin(value: unknown): string {
  const url = httpsUrl(value);
  if (url.origin !== value) {
    throw new Failure("invalid_source");
  }
  return url.origin;
}

function endpoint(value: unknown): Endpoint {
  const fields = shape(value, ["origin", "paths", "methods"]);
  const methods = list(fields["methods"], 1, METHODS.length, (method) => {
    if (typeof method !== "string" || !METHODS.includes(method)) {
      throw new Failure("invalid_source");
    }
    return method;
  });
  if (new Set(methods).size !== methods.length) {
    throw new Failure("invalid_source");
  }
  return Object.freeze({
    origin: origin(fields["origin"]),
    paths: Object.freeze(list(fields["paths"], 1, PATHS_MAX, (path) => {
      const checked = text(path, PATH, 512);
      if (checked.includes("*") && !checked.endsWith("/*")) {
        throw new Failure("invalid_source");
      }
      return checked;
    })),
    methods: Object.freeze(methods),
  });
}

/** An API id the gateway runs; any other is refused when it is sealed. */
function supported(api: string): string {
  if (!SUPPORTED_APIS.includes(api)) {
    throw new Failure("invalid_source");
  }
  return api;
}

function upstream(value: unknown, endpoints: readonly Endpoint[]): Upstream {
  const fields = shape(value, ["provider", "api", "base_url", "auth"], ["verify_url", "models"]);
  const auth = shape(fields["auth"], ["header", "scheme"]);
  const provider = text(fields["provider"], ID);
  const base = httpsUrl(fields["base_url"]);
  if (!endpoints.some((entry) => entry.origin === base.origin)) {
    throw new Failure("invalid_source");
  }
  const verify = fields["verify_url"];
  if (verify !== undefined && !allows(endpoints, "GET", httpsUrl(verify, true))) {
    throw new Failure("invalid_source");
  }
  const custom = provider === "custom";
  if (custom !== (fields["models"] !== undefined)) {
    throw new Failure("invalid_source");
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
  throw new Failure("invalid_source");
}

/**
 * The sources whose OAuth refresh runs through pi-ai's own provider flow, by pi-ai
 * provider id, each with the token endpoint that flow posts to: only Sign in with
 * ChatGPT (research §2.6), whose refresh pi-ai keeps by OpenAI's rotation rules.
 * Consumer subscriptions (Claude Pro and Max, Copilot and the rest) never enter the
 * gateway, so no other provider's OAuth flow is ever run here.
 */
export const PROVIDER_REFRESH: Readonly<Record<string, string>> = Object.freeze({
  openai: "https://auth.openai.com/api/accounts/oauth/token",
});

const CLIENT_KEYS = Object.freeze(["client_id", "client_auth", "token_endpoint", "refresh_with"]);
const CLIENT_OPTIONAL = Object.freeze(["revocation_endpoint", "resource"]);

function oauthClient(fields: Record<string, unknown>, sealed: Upstream): OAuthClient {
  const refreshWith = fields["refresh_with"];
  if (refreshWith !== "provider" && refreshWith !== "rfc6749") {
    throw new Failure("invalid_source");
  }
  if (refreshWith === "provider" && (!Object.hasOwn(PROVIDER_REFRESH, sealed.provider)
      || fields["token_endpoint"] !== PROVIDER_REFRESH[sealed.provider])) {
    throw new Failure("invalid_source");
  }
  const revocation = fields["revocation_endpoint"];
  const resource = fields["resource"];
  return {
    client_id: text(fields["client_id"], TOKEN, 512),
    client_auth: clientAuth(fields["client_auth"]),
    token_endpoint: httpsUrl(fields["token_endpoint"]).href,
    refresh_with: refreshWith,
    ...(revocation === undefined ? {} : { revocation_endpoint: httpsUrl(revocation).href }),
    ...(resource === undefined ? {} : { resource: httpsUrl(resource, true).href }),
  };
}

function grant(value: unknown, sealed: Upstream): OAuthGrant {
  const fields = shape(value, [...CLIENT_KEYS, "access", "expires_at"],
                       [...CLIENT_OPTIONAL, "refresh", "scope"]);
  const expires = fields["expires_at"];
  const refresh = fields["refresh"];
  const scope = fields["scope"];
  return Object.freeze({
    ...oauthClient(fields, sealed),
    access: text(fields["access"], TOKEN, SECRET_MAX),
    expires_at: expires === null ? null : count(expires, 0),
    ...(refresh === undefined ? {} : { refresh: text(refresh, TOKEN, SECRET_MAX) }),
    ...(scope === undefined ? {} : { scope: text(scope, /^[\x20-\x7e]*$/, 4096) }),
  });
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
    oauth: Object.freeze(oauthClient(shape(fields["oauth"], CLIENT_KEYS, CLIENT_OPTIONAL),
                                     checked.upstream)),
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

/** The sealed plaintext, checked; a blob that no longer fits fails as `credential_refused`. */
export function credential(value: unknown): Credential {
  try {
    const fields = shape(value, ["kind", "upstream", "endpoints", "sealed_at"],
                         ["secret", "oauth"]);
    const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
    const common = {
      upstream: upstream(fields["upstream"], endpoints),
      endpoints,
      sealed_at: count(fields["sealed_at"], 0),
    };
    if (fields["kind"] === "api_key" && fields["oauth"] === undefined) {
      return Object.freeze({ ...common, kind: "api_key", secret: secret(fields["secret"]) });
    }
    if (fields["kind"] === "oauth2" && fields["secret"] === undefined) {
      return Object.freeze({ ...common, kind: "oauth2", oauth: grant(fields["oauth"], common.upstream) });
    }
  } catch {
    // Falls through to the one refusal below.
  }
  throw new Failure("credential_refused");
}

/** The secret a credential sends upstream: the API key, or the OAuth access token. */
export function bearer(credential: Credential): string {
  return credential.kind === "api_key" ? credential.secret : credential.oauth.access;
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
  readonly base_url: string;
  readonly endpoints: readonly Endpoint[];
}

/** A scope checked whole, by the grammar a seal is held to; anything else is `invalid_source`. */
export function egressScope(value: unknown): EgressScope {
  const fields = shape(value, ["base_url", "endpoints"]);
  const endpoints = Object.freeze(list(fields["endpoints"], 1, ENDPOINTS_MAX, endpoint));
  const base = httpsUrl(fields["base_url"]);
  if (!endpoints.some((entry) => entry.origin === base.origin)) {
    throw new Failure("invalid_source");
  }
  return Object.freeze({ base_url: fields["base_url"] as string, endpoints });
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
