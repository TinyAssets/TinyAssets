/**
 * The queues' messages that cross Workers and name only an owner (`contract/services.json`
 * `queues`): the sandbox Worker's ring (`RING`, which the doors take to that owner's object)
 * and the owner's erasure (`ERASURES`, which the doors take to make the erasure's outside
 * record). Each queue's name is its producer's config's (`queue-names.ts`, generated).
 */
import { Refusal } from "./refusal";
import { principal } from "./text";

export interface OwnerMessage {
  readonly owner: string;
}

/**
 * An owner message as its consumer reads it (`doors/src/{ring,erase}.ts`): exactly `{owner}`,
 * an owner principal, or `invalid`. A message grants nothing, so the consumer drops one out
 * of shape, never guessing at it.
 */
export function ownerMessage(body: unknown): OwnerMessage {
  if (typeof body !== "object" || body === null || Array.isArray(body)
      || Object.keys(body).join() !== "owner") {
    throw new Refusal("invalid", "an owner message is exactly its owner");
  }
  return Object.freeze({ owner: principal((body as { owner: unknown }).owner) });
}
