/**
 * The external tools (research §4.3): one tool per outside-callable owner door, served on
 * `/mcp` and as `POST /api/tools/<name>`. The tools are the owner's door table's
 * (`contract/doors.json`, read by `contract/src/doors.ts`): each tool's name, hints,
 * description, capability and arguments are its door's row, and the profiles that choose
 * among them are the contract's `profiles.json`. Here, a door's call on the bound object,
 * which a tool and an owner-surface route make alike.
 */
import type { Binding } from "../../contract/src/binding";
import { doorRow, takesRequest } from "../../contract/src/doors";
import type { OwnerDoors } from "../../contract/src/owner";

/**
 * A door the doors name in code (an owner-surface route's): one the contract's owner doors
 * name (`contract/src/owner.ts`), each a row of the door table, so a name written in code
 * that is no door fails to compile.
 */
export type DoorName = keyof OwnerDoors & string;

/**
 * Call the door named `door` on the bound object, its row read first (an unknown name is a
 * bug). The arguments go as the door's request, which the object checks field by field; a
 * door that takes none gets none.
 */
export function callDoor(stub: DurableObjectStub<OwnerDoors>, binding: Binding, door: string,
                         request: unknown): Promise<unknown> {
  // An RPC stub's method is called as itself: `call` and `apply` would be RPC names.
  const row = doorRow(door);
  const method = (stub as unknown as Record<string, unknown>)[row.door] as
    (...args: unknown[]) => Promise<unknown>;
  return Reflect.apply(method, stub, takesRequest(row) ? [binding, request] : [binding]);
}
