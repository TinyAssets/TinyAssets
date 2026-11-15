/**
 * What the owner's recorders share (`*.record.ts`, run by `npm run record`, `ci/record.ts`).
 */
import { until } from "../../../ci/workerd";
import type { OwnerDO } from "../../src/owner-do";

/** An export's page whose object is not yet written: ask again by its cursor (`ownerExport`). */
function pending(value: unknown): value is { pending: true; next: string; export_id: string } {
  return typeof value === "object" && value !== null
    && (value as { pending?: unknown }).pending === true;
}

/**
 * `door` called over RPC as `binding` with `request`, answered once it is written: a page
 * answered `pending` (its object not yet written, `ownerExport`) is asked again by its cursor,
 * as the door says to, until its object is there, so how far the generation had gone when it
 * was first asked never shows in a line.
 */
export async function asked(stub: DurableObjectStub<OwnerDO>, door: string, binding: unknown,
                            request: unknown): Promise<unknown> {
  const call = (body: unknown) =>
    (stub as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>)[door]!(
      binding, body);
  let value = await call(request);
  if (pending(value)) {
    const again = { cursor: value.next, export_id: value.export_id };
    await until(async () => !pending(value = await call(again)), `${door}'s object`, 3000);
  }
  return value;
}
