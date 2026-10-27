import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { Collections, LIMITS, type Limits } from "../src/collections";
import type { Binding, OwnerDO } from "../src/owner-do";

export type Stub = DurableObjectStub<OwnerDO>;

/** A fresh owner object; each test gets its own, so no test sees another's rows. */
export function ownerObject(): Stub {
  return env.OWNER.getByName(crypto.randomUUID());
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

export function as(caller: string, owner = "alice"): Binding {
  return { owner, caller };
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
