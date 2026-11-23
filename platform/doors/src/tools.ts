/**
 * The external tools (research §4.3): one tool per outside-callable owner door, served on
 * `/mcp` and as `POST /api/tools/<name>`. The tools are the owner's door table's
 * (`contract/doors.json`, read by `contract/src/doors.ts`): each tool's name, hints,
 * description, capability and arguments are its door's row, and the profiles that choose
 * among them are the contract's `profiles.json`. Here, a door's call on the bound object,
 * which a tool and an owner-surface route make alike.
 */
import type { Binding } from "../../contract/src/binding";
import { doorRow, takesRequest, takesStop } from "../../contract/src/doors";
import type { DoorAnswer } from "../../contract/src/owner";
import type { OwnerDoors } from "../../contract/src/owner-doors";

/**
 * A door the doors name in code (an owner-surface route's): one of the contract's owner doors
 * (`contract/src/owner-doors.ts`), each a row of the door table, so a name written in code
 * that is no door fails to compile.
 */
export type DoorName = keyof OwnerDoors & string;

/**
 * Call the door named `door` on the bound object, its row read first (an unknown name is a
 * bug). The arguments go as the door's request, which the object checks field by field; a
 * door that takes none gets none. Its answer is the contract's for that door (`DoorAnswer`):
 * an object the doors pass on, for a name only the table knows.
 *
 * `left` is the caller's leaving: aborted once it stops waiting for this call. A door whose
 * work runs while its caller waits (its row's `stop`: `code`'s script) is handed a stop after
 * its request, a stream closed when `left` aborts or the call ends; and, since it crosses the
 * RPC call, broken when this side of the call is gone, so the object sees those ends too.
 */
export function callDoor<D extends string>(stub: DurableObjectStub<OwnerDoors>, binding: Binding,
                                           door: D, request: unknown, left: AbortSignal):
    Promise<DoorAnswer<D>> {
  // An RPC stub's method is called as itself: `call` and `apply` would be RPC names.
  const row = doorRow(door);
  const method = (stub as unknown as Record<string, unknown>)[row.door] as
    (...args: unknown[]) => Promise<DoorAnswer<D>>;
  const args = takesRequest(row) ? [binding, request] : [binding];
  if (!takesStop(row)) {
    return Reflect.apply(method, stub, args);
  }
  const { readable, writable } = new IdentityTransformStream();
  const writer = writable.getWriter();
  const stop = () => {
    writer.close().catch(() => undefined);  // the object let go of it when its call ended
  };
  left.addEventListener("abort", stop, { once: true });
  if (left.aborted) {
    stop();
  }
  return Reflect.apply(method, stub, [...args, readable]).finally(() => {
    left.removeEventListener("abort", stop);
    stop();
  });
}
