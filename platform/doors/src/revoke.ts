/**
 * The owner's revoke of an MCP client (research §1.5; checklist 16): the owner's object
 * first, so the refusal is immediate wherever KV has not caught up, then the provider,
 * so the client's refresh tokens die too and its next refresh is `invalid_grant`.
 *
 * The object's revoke is a narrowing the owner's account makes without a session. A
 * provider grant is revoked when its `client_id`, at our issuer, is the revoked outside
 * client, whatever installation (redirect URI) it came from. The next authorization by
 * that client shows the consent screen (`authorize.ts`), and the object admits it only
 * from a sign-in after the revoke.
 */
import { type Bound, mcpClient } from "./binder";
import { type DoorsEnv, MCP_RESOURCE } from "./config";
import { authorizationServer } from "./oauth";

export interface Revoked {
  readonly client: string;
  readonly revoked: boolean;
  readonly generation: number;
  /** The provider grants revoked with it. */
  readonly grants: number;
}

/** The provider's largest page of grants. */
export const GRANT_PAGE = 1000;

/**
 * Revoke `client` in the owner's object, then every provider grant of it: every page of
 * the owner's grants is read before any is revoked, so a revoke never shifts the pages.
 */
export async function revokeClient(env: DoorsEnv, { binding, stub }: Bound, client: unknown,
                                   pageSize: number = GRANT_PAGE): Promise<Revoked> {
  const done = await stub.grantRevoke(binding, { client: client as string });
  const oauth = authorizationServer(env).getOAuthApi(env);
  const matching: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await oauth.listUserGrants(binding.owner, cursor === undefined
      ? { limit: pageSize } : { limit: pageSize, cursor });
    for (const grant of page.items) {
      if (grant.resource === MCP_RESOURCE && await mcpClient(grant.clientId) === done.client) {
        matching.push(grant.id);
      }
    }
    cursor = page.cursor;
  } while (cursor !== undefined);
  for (const id of matching) {
    await oauth.revokeGrant(id, binding.owner);
  }
  return { ...done, grants: matching.length };
}
