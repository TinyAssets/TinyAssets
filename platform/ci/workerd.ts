/** Test helpers for workerd tests in more than one workspace (they import `cloudflare:test`). */
import { runInDurableObject } from "cloudflare:test";

export type Row = Record<string, SqlStorageValue>;

/** Rows a query returns in `stub`'s own storage. */
export function sqlIn(stub: DurableObjectStub, statement: string,
                      ...params: SqlStorageValue[]): Promise<Row[]> {
  return runInDurableObject(stub, (_instance, state) =>
    state.storage.sql.exec<Row>(statement, ...params).toArray());
}

/**
 * Wait until `check` holds: an event the test caused, or a record it reads, polled every
 * 10 ms; `tries` is a liveness bound only. It polls on workerd's own timer, which the fake
 * clock leaves alone, so a test on the fake clock polls the same (`vi.waitFor` would move it).
 */
export async function until(check: () => Promise<boolean> | boolean, what: string,
                            tries = 500): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (await check()) {
      return;
    }
    await scheduler.wait(10);
  }
  throw new Error(`timed out waiting for ${what}`);
}
