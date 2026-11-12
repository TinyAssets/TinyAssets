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
 *
 * The owner surface's `grants/set` refuses a set to `revoked` (`app.ts`), so a client is
 * revoked only here or by its key's revoke: no path that revokes a client in the object
 * leaves its refresh tokens alive (rename review N1). An API key's client revoked here (the
 * owner app's "Turn off", which holds the grant row, not the key id) also loses its key's
 * directory entry, as `keys/revoke` deletes it, so no entry outlives its key.
 *
 * Also here: a loopback client's grants replaced per installation, not per port
 * (`replaceInstallation`), and every grant of an erased owner (`revokeAll`).
 */
import type { GrantSummary, OAuthHelpers } from "@cloudflare/workers-oauth-provider";

import { type Bound, installationOf, loopback, mcpClient } from "./binder";
import { apiKeyOrigin } from "../../contract/src/binding";
import { type DoorsEnv, MCP_RESOURCE } from "./config";
import { deleteKey } from "./directory";
import { authorizationServer } from "./oauth";

interface ClientRevoked {
  readonly client: string;
  readonly revoked: boolean;
  readonly generation: number;
  /** The grant's revision after it, which the owner's next set names. */
  readonly revision: number;
  /** The provider grants revoked with it. */
  readonly grants: number;
}

/** The provider's largest page of grants. */
const GRANT_PAGE = 1000;

/** The ids of every one of the owner's provider grants that `keep` picks, all pages read. */
async function grantsWhere(oauth: OAuthHelpers, owner: string, pageSize: number,
                           keep: (grant: GrantSummary) => boolean | Promise<boolean>):
    Promise<string[]> {
  const matching: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await oauth.listUserGrants(owner, cursor === undefined
      ? { limit: pageSize } : { limit: pageSize, cursor });
    for (const grant of page.items) {
      if (await keep(grant)) {
        matching.push(grant.id);
      }
    }
    cursor = page.cursor;
  } while (cursor !== undefined);
  return matching;
}

/**
 * Before a loopback client's new grant: revoke its earlier grants from the same
 * installation, whatever port they used. The provider replaces a CIMD client's grants
 * only per exact redirect URI, and a native client's port changes with every sign-in
 * (RFC 8252 §7.3), so without this its grants would pile up, one per port. A redirect
 * that is not loopback is its own installation, and the provider's replacement holds.
 */
export async function replaceInstallation(env: DoorsEnv, owner: string, clientId: string,
                                          redirectUri: string,
                                          pageSize: number = GRANT_PAGE): Promise<number> {
  const url = new URL(redirectUri);
  if (url.protocol !== "http:" || !loopback(url.hostname)) {
    return 0;
  }
  const installation = installationOf(redirectUri);
  const oauth = authorizationServer(env).getOAuthApi(env);
  const earlier = await grantsWhere(oauth, owner, pageSize, (grant) =>
    grant.resource === MCP_RESOURCE && grant.clientId === clientId
    && grant.redirectUri !== undefined && installationOf(grant.redirectUri) === installation);
  for (const id of earlier) {
    await oauth.revokeGrant(id, owner);
  }
  return earlier.length;
}

/**
 * Every provider grant of the outside `client` the owner holds, revoked: every page of the
 * owner's grants is read before any is revoked, so a revoke never shifts the pages. The
 * count revoked.
 */
async function providerRevoke(env: DoorsEnv, owner: string, client: string,
                                     pageSize: number = GRANT_PAGE): Promise<number> {
  const oauth = authorizationServer(env).getOAuthApi(env);
  const matching = await grantsWhere(oauth, owner, pageSize, async (grant) =>
    grant.resource === MCP_RESOURCE && await mcpClient(grant.clientId) === client);
  for (const id of matching) {
    await oauth.revokeGrant(id, owner);
  }
  return matching.length;
}

/**
 * Revoke `client` in the owner's object, then every provider grant of it
 * (`providerRevoke`). When the client is one of the owner's API keys (the object's key
 * store names it), the key's directory entry is deleted after the grant's revoke, as
 * `keys/revoke` deletes it.
 */
export async function revokeClient(env: DoorsEnv, { binding, stub }: Bound, client: unknown,
                                   pageSize: number = GRANT_PAGE): Promise<ClientRevoked> {
  const done = await stub.grantRevoke(binding, { client: client as string });
  const { keys } = await stub.keyList(binding);
  const key = keys.find((row) => apiKeyOrigin(row.key_id).client === done.client);
  if (key !== undefined) {
    await deleteKey(env, key.key_id);
  }
  return { ...done, grants: await providerRevoke(env, binding.owner, done.client, pageSize) };
}

/**
 * Every OAuth grant the owner holds, outside clients' and its own devices' alike, revoked:
 * the owner's erasure (`erase.ts`), so no token of the owner's survives it.
 */
export async function revokeAll(env: DoorsEnv, owner: string,
                                pageSize: number = GRANT_PAGE): Promise<number> {
  const oauth = authorizationServer(env).getOAuthApi(env);
  const every = await grantsWhere(oauth, owner, pageSize, () => true);
  for (const id of every) {
    await oauth.revokeGrant(id, owner);
  }
  return every.length;
}
