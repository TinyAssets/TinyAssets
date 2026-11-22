# wake-canonical as built (2026-11-22)

Owed by #5714's review: `contract/src/wake-kinds.ts` `canonicalJson`, a second canonical
JSON beside `contract/src/canonical.ts`. #5729 (`small-shape.md` §1) already removed it, so
this branch checked that change and swept the platform for the copies it left.

## 1. The wake copy: gone on main, its digests unchanged

`wake-kinds.ts` imports `canonical`; no `canonicalJson` is left anywhere. Its one persisted
output is `subscriptionId` (`sub_` and 32 hex of the SHA-256): the doors derive it, the owner
object stores it, an unsubscribe derives it again. `canonicalJson` as it stood before #5729
(`f3fe84ec5^`) and `canonical` give the same bytes, so the same id, for a routine fire's
arguments, an MCP Events subscription's (a client principal, `command_center_id`, `board`,
two rooms), `{}`, and a value with integer-like keys, non-ASCII and `U+2028` text, `-0`,
`1e21`, `null` and a NUL. They part only on `undefined` and non-plain objects, which no
parsed request holds (`small-shape.md`'s table). No stored id moves.
`contract/guards/checkers.test.ts` `subscriptionId` already pins one id to its digest.

## 2. The copies left, each to `canonical`

- One canonical JSON, `canonical.ts`, for every value compared or digested by its keys
  sorted, so the operator channel and the kit's rotation record stop spelling their own.
  - `contract/src/operator-message.ts` `argsText`: the args' JSON with the keys sorted, by
    hand. `r2.md` item 3 kept it apart because the platform then held four canonical JSONs;
    it holds one now. Over the args `argsOf` admits (flat, each key `ARG`, so never
    integer-like; strings, safe integers, booleans) the two are byte-identical: both sort by
    UTF-16 code units and write each value with `JSON.stringify`. The acceptance record's
    `digest` is kept in the operator bucket for good and a redelivery is held to it
    (`doors/src/operator.ts`), so no record moves either. `argsDigest` takes
    `sha256Hex(canonical(args))`, `operatorAnswer` compares the args by `canonical`, and the
    kit's dry-run line (`deploy/deploy.ts` `ownerAsk`) shows `canonical(args)`. `argsText`
    is gone, and `argsDigest`, which nothing outside the module read, is no longer exported.
  - `deploy/operator.ts` `heldRotation`: compared a rotation record's kid hashes with
    `JSON.stringify(Object.entries(...).sort())` on each side. Nothing stored: an equality
    of two string maps, which `canonical` decides the same way.
- `doors/test/operator.test.ts` `remac` digests the args by `canonical`, the format's words.

**Test:** `contract/guards/checkers.test.ts` `acceptanceOf` pins the digest of `{}` and of
unsorted args (non-ASCII and `U+2028` text, a control character, a negative safe integer,
a boolean, an empty string) to the hex main's `argsText` gives.

## Swept and not copies

Every `JSON.stringify` beside a sort in `platform/*/src`, `deploy/` and `ci/`:
`envelope.ts` `ringText` and `segments.ts` (fixed field order, read back by their own
parsers), `admission.ts` (a sorted list of reasons), `changelog.ts` `keyOf` (SQLite's
`json_array`), `doors/src/definition.ts` `canonical` (a URI, not JSON), and the tests'
`Object.keys(...).sort()` (set comparisons).

## Found and left

- `argsDigest` and `acceptanceOf` are async around a synchronous `sha256Hex`. Making them
  sync changes every caller's signature for no behaviour; not this branch's shape.
