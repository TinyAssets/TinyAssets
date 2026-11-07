/**
 * The owner's upstream sign-in (research §2): WorkOS AuthKit, behind this interface.
 *
 * The doors run the flow (`authorize.ts`): server-side PKCE, a forced fresh sign-in
 * (`max_age=0`) on every authorization, the browser-bound `state` of the provider's
 * `beginUpstream`. An upstream only builds its sign-in URL, verifies the callback, and
 * deletes a person's identity at the owner's erasure. The live one is `oidc.ts`: User
 * Management's code exchange and its access token's signature, issuer, client, subject and
 * `auth_time` against the issuer's JWKS, refusing a token without `auth_time` (there is no
 * clock fallback). Until the
 * deploy configures it, `UNWIRED` refuses loudly (`upstreamOf`), and the tests run a fake.
 * The binder names the owner from the issuer and subject: AuthKit's (`OWNER_ISSUER`)
 * subjects stay owner ids as they are, so no identity migration is owed at cutover, and
 * any later issuer's are namespaced.
 */
export interface SignIn {
  /** The issuer whose sign-in this is (`OWNER_ISSUER` for AuthKit's). */
  readonly issuer: string;
  /** The provider's stable subject (`sub`); with the issuer, it names the owner (`binder.ts`). */
  readonly subject: string;
  /** When the person signed in, whole seconds: the verified token's `auth_time`. */
  readonly authenticated_at: number;
}

export interface Upstream {
  /**
   * Where to send the browser: the provider's authorize URL for this `state`, asking for
   * a fresh sign-in (`max_age=0`) with this S256 challenge, returning to
   * `redirect_uri`. The doors check every one of those in the URL it returns.
   */
  authorizeUrl(request: { state: string; code_challenge: string; redirect_uri: string }):
    string;
  /**
   * The verified sign-in a callback's code is, given the PKCE verifier. A failure throws an
   * Error whose message names the check or the status that failed, never a token's or a
   * secret's value: the callback shows it to the person signing in.
   */
  finish(callback: { code: string; code_verifier: string; redirect_uri: string }):
    Promise<SignIn>;
  /** The person's identity deleted at the issuer (the owner's erasure); gone already is done. */
  forget(subject: string): Promise<void>;
}

export const UNWIRED_MESSAGE = "the upstream sign-in is not wired yet (tasks 2: AuthKit)";

/** No live upstream: every sign-in fails loudly, never a fallback. */
export const UNWIRED: Upstream = Object.freeze({
  authorizeUrl: (): string => {
    throw new Error(UNWIRED_MESSAGE);
  },
  finish: (): Promise<SignIn> => Promise.reject(new Error(UNWIRED_MESSAGE)),
  forget: (): Promise<void> => Promise.reject(new Error(UNWIRED_MESSAGE)),
});
