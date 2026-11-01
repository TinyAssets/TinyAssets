/**
 * The sandbox's lease and its token, through the binder (tasks 4 G4; files research §5.2):
 * what the sandbox slice (8b) calls to open, renew and close the sandbox's one lease.
 *
 * The lease token is the sandbox's identity on the egress door until E2B grants workload
 * identity (a private beta, research §13 item 2; then only the door's identity check is
 * swapped): 256 random bits, base64url, minted here and returned once, for 8b to put in
 * E2B's static transform on `egress.tinyassets.io` (`x-ta-sandbox`), so it never sits in
 * the VM's environment or files. Only its SHA-256 is kept: in the owner's object, on the
 * lease row (the truth), and as the directory's key (`l:`, the routing hint).
 *
 * The order is the API keys' (`api-keys.ts`), the object first:
 * - **Open**: the object takes the lease, bound to the token's hash (`sandboxLeaseOpen`),
 *   then the directory entry is written, then the token is returned once, with what 8b
 *   sets the sandbox up from: E2B's network document for the owner's egress policy, the
 *   token in its transform (`networkDocument`), and the model clients' environment (base
 *   URLs on the egress door and placeholder keys, `sandboxEnv`). A refused open writes no
 *   entry; an entry that fails to write leaves a lease whose token reaches nothing, which
 *   lapses in a minute.
 * - **Renew**: the object first, then the entry rewritten to the new expiry.
 * - **Close**: the object first, so the token is refused at once and the lease's streams
 *   end, then the entry deleted, so a dead token stops at the edge.
 */
import { networkDocument, type NetworkPlan } from "../../owner/src/network";
import type { Lease } from "../../owner/src/files";

import type { Bound } from "./binder";
import { type DoorsEnv, now } from "./config";
import { deleteLease, putLease } from "./directory";

/** A lease token: 256 random bits as base64url, 43 characters. */
export const LEASE_TOKEN = /^[A-Za-z0-9_-]{43}$/;

/** A fresh lease token. */
export function newLeaseToken(): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The SHA-256 hex of a token's UTF-8, as the object and the directory keep it. */
export async function leaseTokenHash(token: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** What an open hands the sandbox slice, once. */
export interface OpenedLease {
  /** The lease token, for E2B's transform; never shown again. */
  readonly token: string;
  readonly lease: Lease;
  /** E2B's network for the owner's egress policy, the token in its transform. */
  readonly network: NetworkPlan;
  /** The model clients' environment: base URLs on the egress door, placeholder keys. */
  readonly env: Readonly<Record<string, string>>;
}

function leaseOf(lease: Lease): Lease {
  return { lease_id: lease.lease_id, epoch: lease.epoch, expires_at: lease.expires_at,
           base: lease.base };
}

/** The lease's directory entry, as the object holds it; expiry in whole seconds. */
function entryOf(owner: string, lease: Lease): { o: string; l: string; e: number } {
  return { o: owner, l: lease.lease_id, e: Math.floor(lease.expires_at) };
}

/** Open the owner's sandbox lease: the object, then the directory, then the token once. */
export async function sandboxLeaseOpen(env: DoorsEnv, { binding, stub }: Bound,
                                       request: { holder: string }): Promise<OpenedLease> {
  const token = newLeaseToken();
  const hash = await leaseTokenHash(token);
  const lease = await stub.sandboxLeaseOpen(binding, { holder: request.holder, token: hash });
  await putLease(env, hash, entryOf(binding.owner, lease), now());
  const config = await stub.sandboxConfig(binding);
  return { token, lease: leaseOf(lease),
           network: networkDocument(config.egress, config.generation, token), env: config.env };
}

/** Renew it: the object, then the directory entry's expiry. */
export async function sandboxLeaseRenew(env: DoorsEnv, { binding, stub }: Bound,
                                        request: { lease_id: string; epoch: number }):
    Promise<Lease> {
  const lease = await stub.sandboxLeaseRenew(binding, { lease_id: request.lease_id,
                                                        epoch: request.epoch });
  await putLease(env, lease.token, entryOf(binding.owner, lease), now());
  return leaseOf(lease);
}

/** Close it: the object, so the token is refused and its streams end, then the entry. */
export async function sandboxLeaseClose(env: DoorsEnv, { binding, stub }: Bound,
                                        request: { lease_id: string; epoch: number }):
    Promise<{ released: boolean }> {
  const closed = await stub.sandboxLeaseClose(binding, { lease_id: request.lease_id,
                                                         epoch: request.epoch });
  if (closed.token !== null) {
    await deleteLease(env, closed.token);
  }
  return { released: closed.released };
}
