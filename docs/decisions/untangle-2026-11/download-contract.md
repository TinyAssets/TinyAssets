# File download review as built (2026-11-18)

Step 19 plans two readers of the same owner files. The browser needs a byte
stream. Today's outside tools return a single JSON text through
`doors/src/answers.ts`. Each reader needs to know which version its first byte
belongs to, and what happens if that version goes away before the last byte.

## What the owner already promises

On main `f934871ad` and the combined review tree `151c2a83f`, the real
`Files.write`, `fileRead`, `fileReadChunks` and `Files.purge` ran over
all 18 owner SQLite migrations on Node 22 and 24. The chunk store was an
in-memory test double. Files were synthetic.

| Read | Observed result on both trees |
| --- | --- |
| 4,096 workspace bytes | The entry and its inline bytes came back together. |
| 4,097 workspace bytes followed by an overwrite of 4 MiB + 1 byte | The older version returned the original 4,097 bytes. The replacement returned two chunks of exactly 4 MiB and 1 byte. |
| Purge after the overwrite | Reading the old version failed with `no such version`. The current version still returned its first chunk. |
| 123 temporary bytes followed by an overwrite of 4,097 bytes | The small bytes were readable inline. The new entry and its one-chunk plan were readable. Reading that current version through `fileReadChunks` failed with `no such version`. |
| One full chunk passed to the real tool formatter as a base64 `data` field | The minimal JSON result took 5,592,419 bytes. The formatter refused it at 50,000. A 36,000-byte slice in the same envelope passed. |

`owner/src/files.ts` gets the initial entry from the live path. Its chunk
door resolves the requested path and version from retained history. The
temporary area deliberately has no history (owner-object file history
requirement). The in-object pi execution environment that allocated these
directories was removed in #5389. The current hosted runner sets its
`TMPDIR` on the sandbox's own disk. Only tests call the owner's temporary
directory allocator; existing stored rows still participate in export and
restore. The temporary-file fixture exercised that existing storage shape,
not a newly served creation method. Keeping a version number in the client therefore pins the
identity of the bytes the caller asked for. It does not keep them alive.
The 36,000-byte control used a minimal synthetic envelope. Real metadata,
continuation and identifiers also consume the tool's encoded answer budget.

## What must precede release

- Decide which paths each surface offers. A temporary file needs either a
  supported current-version read or an explicit scope that excludes it.
  Keep the existing retention contract. A stream that already sent its
  headers must still signal incomplete delivery if further bytes cannot
  be read. Resuming from a newer file at the old offset would change bytes.
- Reuse the owner's version lookup and the existing range reader for binary
  windows where their semantics apply. Measure the complete tool result
  with the existing encoded-page machinery and test round trips over
  chunk boundaries. The owner app's text preview and the text tool each serve
  a different representation.
- ADR-037 requires streams or bounded chunk steps and release of the hold
  when the consumer finishes or cancels. Its PR 3b specifically covers
  outstanding RPC chunk holds. Review that transport before reusing the
  chunk door for slow browser downloads. Verify origin-version continuity,
  cancellation, memory as file size grows, and independent bounded reads
  in the presence of a slow consumer.

This review added the prerequisites to Step 19. The download route, public
tool, mixed-version behavior and live delivery are still open.
