/** Test helpers for workerd tests in more than one workspace (they import `cloudflare:test`). */
import { runInDurableObject } from "cloudflare:test";

export type Row = Record<string, SqlStorageValue>;

/** Rows a query returns in `stub`'s own storage. */
export function sqlIn(stub: DurableObjectStub, statement: string,
                      ...params: SqlStorageValue[]): Promise<Row[]> {
  return runInDurableObject(stub, (_instance, state) =>
    state.storage.sql.exec<Row>(statement, ...params).toArray());
}
