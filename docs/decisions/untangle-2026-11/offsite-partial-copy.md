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

The off-account copy's first live nights and credentials remain founder operations. Restore
transport and concurrent erasure remain on their existing branches and review gates.
