# Recorder completion (2026-11-18)

On main `f934871ad`, an RPC child printed every baseline line and exited 9.
The recording command failed. A later comparison accepted its four files.
An interrupted rerun could also leave older families alongside new ones.

The collector is the one caller that observes every child's exit. Each run now
owns a directory inside `.record-state`. A retry moves the earlier directory
away before it starts publishing its own transcripts. Each writer renames from
its own directory, so a superseded writer cannot replace a newer file. Failed
families retain their transcripts and diagnostic logs. Once all four children
exit, the collector records each successful nonempty family's digest and null
for each failure. Comparison checks the selected result and the four public
files on both sides. Keep the whole output directory when archiving a capture.

## Comparing across the collector change

Use a separate output directory for each worktree. An older writer cannot
participate in the new directory ownership protocol. For a fresh comparison
with an older base, set `base` and `head` to the absolute worktree paths. The
following commands stop at the first failed recording or verification and keep
both sets of transcripts available for review.

```sh
set -e
capture=$(mktemp -d)
(cd "$base/platform" && npm run record -- "$capture/base")
(cd "$head/platform" && npm run record -- "$capture/head")
(cd "$head/platform" && npm run record:diff -- "$capture/head" "$capture/head")
(cd "$base/platform" && npm run record:diff -- "$capture/base" "$capture/head") > "$capture/comparison.log"
```

Read `$capture/comparison.log` even when the final command exits 1. The older CLI can exit before a pipe has carried all its output. A regular output file preserves the complete comparison and the command keeps its original exit status.

For a previously completed older capture, use the existing immutable archive in
the final command in place of `$capture/base` and omit the first recording.
First verify the original exit evidence belongs to that commit and those four
files, including successful coverage of every group for a sharded run. Compare
from the older worktree only after the newer collector has verified its side.
When original completion cannot be established, make a fresh base capture.
Two captures from the updated collector use its ordinary `record:diff` directly.

On Linux, the ordinary npm scripts from `f934871ad` and `02c2f1f0` recorded synthetic transcripts in separate directories. The newer collector verified its own capture. On Node 22.23.5 and 24.22.3 the older CLI returned 1 for a discovery difference and delivered 65,551 of 540,247 bytes through a pipe. Both final family results were missing. Directing the same command into a regular file delivered all 540,247 bytes and both final results, byte for byte with the corrected comparator on `df58c760`. Equal legacy inputs returned 0. The newer collector rejected a changed transcript after capture. These checks exercised the CLI and filesystem with substitute recorder children.
