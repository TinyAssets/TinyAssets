# Off-account partial copy retirement gate as built (2026-11-18)

The `FILES_KEYRING` retirement gate used a successful nightly copy as proof that Backblaze
could hold objects under the retiring key. When every completed copy job had failed, the gate
reported that the copy had never passed and therefore held nothing. Rclone can upload objects
before returning failure. That result did not prove the destination was empty.

`firstNightAfter` now distinguishes a job that was skipped before copying from any job that
began. A failed attempt means the copy may hold objects. Retirement then waits for a successful
night after the settled rotation pass and for that night's retention gate. An always-skipped
copy still proves that no copy operation reached Backblaze. The retire apply job and offsite
copy job use one concurrency group, so the completed-job query cannot race an active upload.

The focused rotation suite covers success before and after the pass, a skipped copy, and a
failed first attempt. The workflow guard holds the shared concurrency boundary. The failure
case is a negative control for the former fail-open result. Malformed run `created_at` and copy
`completed_at` values also refuse instead of making an invalid retirement date.
No account, bucket, workflow or production setting was read or changed.

Validation on the branch passed 286 deploy tests with one existing skip, Node 22 TypeScript,
the seven pre-commit invariants, the 2,230-entry test-shape registry, and OpenSpec validation.
The branch also integrated with the other staged rebuild work: that combined tree passed 300
deploy tests with the same skip and Node 22 TypeScript. The wire, tools, and RPC behavior
families produced the expected 804, 7, and 13,622 records. The final 73,945-record family was
stopped after it filled the shared temporary filesystem and stopped producing output, so this
branch does not claim a complete behavior-record comparison. GitHub actionlint remains the
workflow-expression evidence because actionlint is not installed in this workspace.

The off-account copy's first live nights and credentials remain founder operations. Restore
transport and concurrent erasure remain on their existing branches and review gates.
