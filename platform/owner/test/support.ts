import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { Collections, LIMITS, type Limits } from "../src/collections";
import type { Binding, OwnerDO } from "../src/owner-do";

export type Stub = DurableObjectStub<OwnerDO>;

/** An owner of its own and its object, reached as a binder reaches it: by name. */
export interface Owner {
  /** The owner, unique to the test, so no test sees another's rows. */
  readonly name: string;
  readonly stub: Stub;
  /** The binder's binding: `caller` on `owner`'s object (this owner by default). */
  as(caller: string, owner?: string): Binding;
}

export function ownerObject(): Owner {
  const name = `owner-${crypto.randomUUID()}`;
  return {
    name,
    stub: env.OWNER.getByName(name),
    as: (caller, owner = name) => ({ owner, caller }),
  };
}

/**
 * The error an RPC call fails with. Awaited inside a try, because handing the call's
 * promise to `expect(...).rejects` leaves its rejection reported as unhandled.
 */
export async function failure(call: () => Promise<unknown>): Promise<Error> {
  try {
    await call();
  } catch (error) {
    return error as Error;
  }
  throw new Error("expected the call to fail");
}

/** The primitive on `stub`'s own storage, with some bounds lowered for the test. */
export function inObject<R>(stub: Stub, limits: Partial<Limits>,
                            act: (store: Collections, state: DurableObjectState) => R): Promise<R> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    act(new Collections(state.storage, { ...LIMITS, ...limits }), state));
}

export type RowTuple = [string, string, number, number, string, number, string];

/** The stored rows as `(collection, row_id, revision, seq, writer, private, body)`. */
export function rows(stub: Stub): Promise<RowTuple[]> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Record<string, string | number>>(
      "SELECT collection, row_id, revision, seq, writer, private, body FROM collection_rows " +
        "ORDER BY seq",
    ).toArray().map((r) => [r["collection"], r["row_id"], r["revision"], r["seq"], r["writer"],
                            r["private"], r["body"]] as RowTuple));
}

export function etags(stub: Stub): Promise<Record<string, string>> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    Object.fromEntries(state.storage.sql.exec<{ row_id: string; etag: string }>(
      "SELECT row_id, etag FROM collection_rows",
    ).toArray().map((r) => [r.row_id, r.etag])));
}

export type HistoryTuple = [string, string, number, string, string, string];

/** The stored history as `(collection, key, revision, op, body_json, writer)`. */
export function history(stub: Stub): Promise<HistoryTuple[]> {
  return runInDurableObject(stub, (_instance: OwnerDO, state) =>
    state.storage.sql.exec<Record<string, string | number>>(
      "SELECT collection, key, revision, op, body_json, writer FROM collection_history " +
        "ORDER BY collection, key, revision",
    ).toArray().map((r) => [r["collection"], r["key"], r["revision"], r["op"], r["body_json"],
                            r["writer"]] as HistoryTuple));
}
