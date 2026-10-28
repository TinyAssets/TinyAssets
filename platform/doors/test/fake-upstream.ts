/**
 * A fake upstream sign-in for the tests, behind the interface AuthKit will implement.
 *
 * It is stateless: its "authorization code" is the sign-in itself (the subject, the
 * sign-in time and the PKCE challenge the doors sent), which the test builds as a real
 * provider would issue it. `finish` checks the doors' verifier against that challenge,
 * as AuthKit's token endpoint does, so a flow whose PKCE is wrong fails here.
 */
import type { SignIn, Upstream } from "../src/upstream";

export const UPSTREAM_AUTHORIZE = "https://upstream.test/authorize";

interface FakeCode {
  readonly subject: string;
  readonly authenticated_at: number;
  readonly challenge: string;
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_")
    .replace(/=+$/, "");
}

/** The code the fake provider issues for this sign-in and challenge. */
export function fakeCode(code: FakeCode): string {
  return base64url(new TextEncoder().encode(JSON.stringify(code)));
}

export const FAKE_UPSTREAM: Upstream = Object.freeze({
  authorizeUrl: ({ state, code_challenge, redirect_uri }: {
    state: string; code_challenge: string; redirect_uri: string;
  }): string => {
    const url = new URL(UPSTREAM_AUTHORIZE);
    url.search = new URLSearchParams({
      response_type: "code", client_id: "fake-authkit", redirect_uri, state,
      code_challenge, code_challenge_method: "S256", prompt: "login",
    }).toString();
    return url.href;
  },
  finish: async ({ code, code_verifier }: { code: string; code_verifier: string }):
      Promise<SignIn> => {
    const padded = code.replace(/-/g, "+").replace(/_/g, "/");
    const parsed = JSON.parse(atob(padded)) as FakeCode;
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(code_verifier));
    if (base64url(new Uint8Array(digest)) !== parsed.challenge) {
      throw new Error("the fake upstream refuses a PKCE verifier that misses its challenge");
    }
    return { subject: parsed.subject, authenticated_at: parsed.authenticated_at };
  },
});
