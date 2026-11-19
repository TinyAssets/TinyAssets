# Sync-ignore lines in the runner and the owner (2026-11-18)

The founder adopted Git's byte-oriented ignore semantics. An owner may add a
custom rule that holds back edits to an already-kept file. The shipped default
rules only hide new files. A change to the rule file also yields an owner notice
(`contract/src/sync-ignore.ts`, `sandbox/runner/sync.ts`, `owner/src/files.ts`).

The runner preserved a file's first UTF-8 byte-order mark while the owner
decoder stripped it. The matcher's literal-name index stripped such a mark in
*any* rule, and its treatment depended on whether the pattern used that index.
The matcher and owner also trimmed leading whitespace to classify a default or
comment. An owner rule for a filename beginning with a space could therefore
leave the runner syncing its edits or suppress the owner's notice.

Both readers now retain the original spelling of decoded lines. The parser
removes one mark at the start of the file, original line endings and unescaped
trailing spaces. Rule names, default classification, notices and unapplied
line reports consume that spelling. A mark after the first position stays in
the filename. Both the runner and the in-memory contract require a complete
line inside `LIST_BYTES`, including the separator if the file continues.
The runner reports the bytes beyond that read, measured from the file's size.
The old report also counted bytes it had read while finding the last complete
line. A file one byte over the limit with a newline in its last allowed byte
could therefore report two. The importer supplies a fixed list of the
existing defaults. The sandbox sizing measurements and the ceiling are unchanged.

On main `f934871ad` and combined `2c8a2d4a2`, 15 UTF-8 rule files and 15
ordinary paths disagreed with `git check-ignore` in 14 of the 225 comparisons.
The corrected matcher agreed throughout on Node 22 and Node 24, recognized the space-prefixed
`node_modules` rule as custom and retained the real rule ` #memo` in the
owner's list. The existing differential also passed against all 310 GitHub
templates and 120 generated lists. Tests cover a multibyte rule ending
immediately before, at and after the read limit, with and without a newline.

## Rule files containing invalid UTF-8 or NUL

The sync agent reports invalid UTF-8 **filenames** separately. A rule file's
contents may still contain arbitrary bytes. Both previous readers already
converted invalid bytes to U+FFFD. That conversion can hold back an ordinary
valid filename the owner never named. A one-byte FF rule matched U+FFFD in
the runner; Git left that filename alone. Skipping a malformed line would also
give wrong answers. Git matches the bracket class with bytes `5b 61 ff 5d` against
`a`, and matches `2a a9` against UTF-8 © (c2 a9).

An embedded NUL is valid UTF-8 but also needs file-level interpretation.
With `one NUL two LF` (hex `6f 6e 65 00 74 77 6f 0a`), Git ignored `one`.
Both the old and corrected runners sent the tracked edit to `one`.
A following ordinary line still works in Git.

The open dependency is to keep original rule bytes through line boundaries, tokenization,
duplicate-rule identity, indexing and byte accounting. Keep the owner's
human-readable line report distinct from byte identity, respect the existing
read bound and check mixed-byte rules against Git and the real file-sync path.
This correction covers well-formed UTF-8 text rules without NUL. A full matcher review and the
standing pinned-template performance checks remain open.

## Repeated wildcard precedence

- [x] On `e07b1dcf93decd82b98fb5712e9a4973b51e4a24`, Git ignored `xa` for the file lines `x?`, `!x[ab]`, `x?`, but the shared matcher sent it. The compiler updated the repeated rule number without moving its accepting state. This also affected anchored paths and directory walks.
- [x] Reuse each admitted rule's states and place them at its last occurrence when compiling. A permanent Git differential on Node 22 failed on the previous source and passed on the corrected source, covering anchored and unanchored rules, both polarities, files, directories and descendants.
- [x] On `92a4a8afd1b738682c52a01934dd3d86a89a5657`, the ordinary contract suite passed 300 tests, with 18 size-tagged tests excluded. Both separate Git comparisons passed, using all 310 pinned GitHub templates and 120 generated lists.
- [ ] Two caller regressions now exercise the unfinished raw-byte path. The runner must send the genuine replacement-character filename when a raw FF rule does not name it. An owner replacing FF with the actual UTF-8 spelling of U+FFFD must see the new-rule notice. Recheck against Git, both ordinary entry points, raw size accounting and the pinned sandbox measurements.

## Raw rule reader under verification

Both permanent consumer tests on `c657e4d9264d313bcd36a2805df79ec65b03c1e5` failed on the intended assertions. The runner offered a tracked edit Git ignored and held back a genuine replacement-character name. The owner omitted the new-rule warning when the raw spelling changed. An independent Node 24 run using a real SQLite mirror and the owner reaction confirmed both outcomes. At exactly 8,192 states both anchored and basename automata kept the last repeated rule's precedence on `92a4a8af`.

The candidate reads each physical byte range once for its line boundary. The shared parser removes the physical terminal CR before interpreting NUL, then trims unescaped trailing spaces. The compiler uses the original end offset for the list ceiling, byte identities for duplicate rules and defaults, and strictly decodes complete unescaped literals before indexing valid path names. The owner compares the same normalized byte identities.

The new runner regression passed on the initial candidate, and three focused contract checks passed after the type annotation correction. The two template and generated-list comparisons passed on the initial candidate. A concurrent full contract run and a runner-file run lost a test process to SIGKILL. The first scoped typecheck identified a missing TextDecoder option and the required generated runner module. Rebuild the runner, verify the corrected typecheck and repeat the two suites serially. The real owner notice check, broader caller comparison and pinned performance and memory checks remain open at this checkpoint.
