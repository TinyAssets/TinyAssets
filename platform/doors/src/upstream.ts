/**
 * The owner's upstream sign-in (research §2): WorkOS AuthKit, behind this interface.
 *
 * The doors run the flow (`authorize.ts`): server-side PKCE, `prompt=login` on every
 * authorization, the browser-bound `state` of the provider's `beginUpstream`. An
 * upstream only builds its sign-in URL and verifies the callback. This slice has no
 * live one: `UNWIRED` refuses loudly, and the tests run a fake. The AuthKit slice
 * implements it (tasks 2): its authorize URL, the code exchange, and the ID token's
 * signature, issuer, audience and `auth_time` against its JWKS, refusing a token without
 * `auth_time` (there is no clock fallback). The binder names the owner from the issuer
 * and subject: AuthKit's (`OWNER_ISSUER`) subjects stay owner ids as they are, so no
 * identity migration is owed at cutover, and any later issuer's are namespaced.
 */
export interface SignIn {
  /** The verified ID token's issuer (`iss`). */
  readonly issuer: string;
  /** The provider's stable subject (`sub`); with the issuer, it names the owner (`binder.ts`). */
  readonly subject: string;
  /** When the person signed in, whole seconds: the ID token's `auth_time`. */
  readonly authenticated_at: number;
}

export interface Upstream {
  /**
   * Where to send the browser: the provider's authorize URL for this `state`, asking for
   * a fresh sign-in (`prompt=login`) with this S256 challenge, returning to
   * `redirect_uri`. The doors check every one of those in the URL it returns.
   */
  authorizeUrl(request: { state: string; code_challenge: string; redirect_uri: string }):
    string;
  /** The verified sign-in a callback's code is, given the PKCE verifier. */
  finish(callback: { code: string; code_verifier: string; redirect_uri: string }):
    Promise<SignIn>;
}

export const UNWIRED_MESSAGE = "the upstream sign-in is not wired yet (tasks 2: AuthKit)";

/** No live upstream: every sign-in fails loudly, never a fallback. */
export const UNWIRED: Upstream = Object.freeze({
  authorizeUrl: (): string => {
    throw new Error(UNWIRED_MESSAGE);
  },
  finish: (): Promise<SignIn> => Promise.reject(new Error(UNWIRED_MESSAGE)),
});
