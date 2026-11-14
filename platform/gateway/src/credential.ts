/**
 * What a sealed credential holds (research §1.3, §2.4): the secret together with the source
 * data it was connected under (`contract/src/egress-scope.ts`: the pi-ai provider and API
 * that speak to it, the base URL, how the secret is sent, and the endpoints it may reach).
 * Those values come from the source record at connect time (data, never code) and are
 * sealed with the secret, so a loop bug, a hook or a crafted request cannot point a real key
 * at another host: the gateway sends a secret only where the opened plaintext says
 * (`allows`), never to a URL a request names.
 *
 * Every value is checked when it is sealed and again when it is opened, so a blob written
 * by older code that no longer fits fails loudly rather than being half-read.
 */
import { count, list, shape } from "../../contract/src/checks";
import {
  CLIENT_KEYS, CLIENT_OPTIONAL, endpoint, type Endpoint, ENDPOINTS_MAX, oauthClient,
  type OAuthClient, scope, secret, upstream, type Upstream,
} from "../../contract/src/egress-scope";
import { Refusal } from "../../contract/src/refusal";

/** An OAuth connection's tokens, with its client. */
export interface OAuthGrant extends OAuthClient {
  readonly access: string;
  readonly refresh?: string;
  /** Epoch ms the access token expires, or null when the server gave no lifetime. */
  readonly expires_at: number | null;
  readonly scope?: string;
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

function grant(value: unknown): OAuthGrant {
  const fields = shape(value, [...CLIENT_KEYS, "access", "expires_at"],
                       [...CLIENT_OPTIONAL, "refresh", "scope"]);
  const expires = fields["expires_at"];
  const refresh = fields["refresh"];
  const granted = fields["scope"];
  return Object.freeze({
    ...oauthClient(fields),
    access: secret(fields["access"]),
    expires_at: expires === null ? null : count(expires, 0),
    ...(refresh === undefined ? {} : { refresh: secret(refresh) }),
    ...(granted === undefined ? {} : { scope: scope(granted) }),
  });
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
      return Object.freeze({ ...common, kind: "oauth2", oauth: grant(fields["oauth"]) });
    }
  } catch {
    // Falls through to the one refusal below.
  }
  throw new Refusal("credential_refused");
}

/** The secret a credential sends upstream: the API key, or the OAuth access token. */
export function bearer(credential: Credential): string {
  return credential.kind === "api_key" ? credential.secret : credential.oauth.access;
}

