# Wake subscription IDs as built (2026-11-18)

1. `contract-copies.md` item 3 puts canonical JSON in `contract/src/canonical.ts`.
   The wake contract still sorted and serialized JSON itself, both for the size of
   subscription arguments and for the digest that identifies a stored callback. Both
   now use `canonical`. Only those two calls used the old serializer.
2. The MCP Events door validates JSON arguments and fills in `board` and `rooms`
   before deriving the ID for subscribe or unsubscribe. The owner app's door fills
   the same defaults and supplies the validated agent ID. Both doors hand these
   values to the owner. The owner's RPC contract requires the app's `board` and
   `rooms`; the MCP handler validates the arguments again before storing them.
3. The compatibility vectors in `contract/guards/checkers.test.ts` pin the previous
   IDs from both doors, including non-ASCII room text. An independent replay on
   Node 22 and 24 compared the two implementations with Python's sorted UTF-8 JSON
   for 512 synthetic IDs. Both retained the 1,024-character argument limit.
