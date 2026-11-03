/**
 * The doors Worker (ADR-032 Decision 2): the binder, our authorization server, the
 * stateless MCP door, `/api` keys, `/hooks/inbound` and the owner surface (`doors.ts`).
 * Not deployed: its checked-in config has no routes and placeholder KV ids, and the deploy
 * kit (`platform/deploy/`) writes the deployed one. The upstream sign-in is the configured
 * owner issuer's (`oidc.ts`), or `UNWIRED` until the deploy configures it, so a sign-in
 * fails loudly.
 */
import { type DoorsEnv, ISSUER_NOT_CONFIGURED, UPSTREAM_NOT_CONFIGURED } from "./config";
import { serve } from "./doors";
import { oidcUpstream, upstreamConfig } from "./oidc";
import { bundledSources } from "./sources";
import { UNWIRED, type Upstream } from "./upstream";

/**
 * The owner's sign-in as the Worker is configured: `UNWIRED` while both the issuer and its
 * record are unset; the live one when both are set; a throw when one is set without the
 * other, or the secret is missing (a half-configured deploy fails loudly, never signs in).
 */
export function upstreamOf(env: DoorsEnv): Upstream {
  const issuer = env.OWNER_ISSUER === ISSUER_NOT_CONFIGURED;
  const record = env.UPSTREAM === UPSTREAM_NOT_CONFIGURED;
  if (issuer && record) {
    return UNWIRED;
  }
  if (issuer || record) {
    throw new Error("OWNER_ISSUER and UPSTREAM are configured together, or neither is");
  }
  return oidcUpstream(env.OWNER_ISSUER, upstreamConfig(env.UPSTREAM), env.WORKOS_API_KEY ?? "");
}

export default Object.freeze({
  fetch: (request: Request, env: DoorsEnv, ctx: ExecutionContext): Promise<Response> =>
    serve(request, env, ctx, upstreamOf(env), bundledSources()),
});
