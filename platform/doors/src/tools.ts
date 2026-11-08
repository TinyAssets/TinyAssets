/**
 * The external tools (research §4.3): one tool per outside-callable owner door, served on
 * `/mcp` and as `POST /api/tools/<name>`. The tools are the owner's door table's
 * (`contract/doors.json`, read by `contract/src/doors.ts`): each tool's name, hints,
 * description, capability and arguments are its door's row, and the profiles that choose
 * among them are the contract's `profiles.json`. Here, a door's call on the bound object,
 * which a tool and an owner-surface route make alike.
 */
import type { Binding, OwnerDO } from "../../owner/src/owner-do";
import { doorRow, takesRequest } from "../../contract/src/doors";

/**
 * Call the door named `door` on the bound object. The arguments go as the door's request,
 * which the object checks field by field; a door that takes none gets none.
 */
export function callDoor(stub: DurableObjectStub<OwnerDO>, binding: Binding, door: string,
                         request: unknown): Promise<unknown> {
  // An RPC stub's method is called as itself: `call` and `apply` would be RPC names.
  const method = (stub as unknown as Record<string, unknown>)[doorRow(door).door] as
    (...args: unknown[]) => Promise<unknown>;
  return Reflect.apply(method, stub, takesRequest(doorRow(door)) ? [binding, request]
                                                                 : [binding]);
}
