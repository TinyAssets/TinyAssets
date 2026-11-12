/**
 * The queues' messages that cross Workers and name only an owner (`contract/services.json`
 * `queues`): the sandbox Worker's ring (`RING`, which the doors take to that owner's object)
 * and the owner's erasure (`ERASURES`, which the doors take to make the erasure's outside
 * record). The consumer checks each message (`doors/src/{ring,erase}.ts`).
 */
export interface OwnerMessage {
  readonly owner: string;
}
