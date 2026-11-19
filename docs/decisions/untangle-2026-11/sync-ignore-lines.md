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
