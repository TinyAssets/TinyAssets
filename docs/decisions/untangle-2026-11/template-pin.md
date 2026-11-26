# template-pin (2026-11-21)

The sandbox image moved after the last pin (`s85528cdd2cd74efa`): #5745 dropped Kimi's install
and lock from the recipe, so `image.ts`'s tag no longer matched the pin and `apply` deploys were
refused. One `sandbox-template.yml` build on main at f1a539703 (run 44938392431, part of the
founder-approved dark redeploy) built `tinyassets-sandbox:s8661fc8113e20662` and passed every
standing check on throwaway sandboxes: the three sign-ins (claude-code, codex, gemini), the eleven
keyed routes (two turns each, the second resumed, no outside connects) and the matcher step
(4.46 ns, ceiling 6). Its pair is `s8661fc8113e20662-rcde54e5309ef50f5-p8cc7078f4f6dcaa5`.

This change is the run's own record: `passed.json` as the run uploaded it (artifact
`passed-json`) and the pin in `sandbox/wrangler.jsonc`. Nothing else changes.

- [ ] live: the dark redeploy runs its pair check on this pin.

### Interrupted sandbox replacement, November 26

The template replacement proposed in #5857 promises to serve an idle owner's
start from the original sandbox if the transfer fails. Main d662e63e still
resumes that sandbox. Three ordinary Worker regression tests use a synthetic
idle sandbox and the existing fake vendor. All three passed on main with the
tests added. The identical cases failed on PR code aa40cc0a. Both trees passed
full platform TypeScript.

- [ ] On #5857, a broken or lost carry-out reply refuses the pending start before the
  old sandbox launches it. The PR's existing failed carry-in control still
  serves the start on the old sandbox.
- [ ] On #5857, when the vendor deletes the old sandbox and its acknowledgement is lost,
  the error path also deletes the replacement. An independent diagnostic
  observed two deletes, zero surviving sandboxes and a refused start.
  Separate an uncertain deletion from a failed copy. Record which sandbox
  holds the verified copy across retries and Worker restarts before any
  subsequent deletion. Preserve a recoverable copy when either acknowledgement
  is missing.
- [ ] On #5857, passing these control tests also requires preserving each pending file
  and its original version. [The transfer can omit equal-length edits and
  executable-only changes](https://github.com/TinyAssets/TinyAssets/commit/acabc5e8ca13906bce6ec9725821a324eba1e829).
- [x] The next control returns the same 502 for a delete request lost before
  it takes effect and an acknowledgement lost afterwards. It evicts the real
  local Durable Object and retries the same start. The before-delete history
  retained the copy. After deletion, zero sandboxes remained; after eviction
  the retry fulfilled with a freshly created sandbox lacking the transfer.
  The full Worker/guard suite with four new tests passed 167 on main; the
  PR passed 166 and failed three. Both platform typechecks passed serially
  after overlapping attempts were killed.
- [ ] On #5857, before promotion, a transfer failure should leave the old sandbox
  available. After verifying a complete transfer, record the destination
  as authoritative and the original as pending cleanup before deleting it.
  The existing vendor deletion accepts both 204 and 404. Design and verify
  cleanup across restart, uncertain creation, and account erasure. A reread
  of two IDs alone does not establish which copy was verified.

- [x] Check which sandbox actually receives a successful retried launch after a Worker eviction. The local provider retains an unrelated paused sandbox with the same owner and template metadata. Each check ties the launch to its run ID and requires either the original sandbox or the one that received its carry. In the no-fault and lost-before-delete controls the PR preserved the right destination. With the delete acknowledgement lost, it reported a successful start on an empty sandbox. The full sandbox Worker and guard suite passed 170 tests on main `d662e63e` with the branch regressions. With the identical regressions on #5857 at `aa40cc0a`, 168 passed and four failed, including this new routing assertion. These checks use local Workerd and a synthetic provider.

- [x] Full platform TypeScript passed on both the standalone `511c9391` test tree and its rehearsal on #5857. Keeping the verified destination alive on an uncertain delete made both earlier copy-retention tests pass. The stronger routing test still failed after restart: four of five focused cases passed. Promoting the destination before the delete and ignoring the delete error passed all five, but left the original running when the delete request never arrived.
- [x] Test the effect on another owner using the real local Worker with a two-sandbox limit. After a successful delete or a lost acknowledgement, the retry used the copied workspace and the peer could start. After the lost request, the retry used the copy but the peer had to wait. In all three cases erasure cleaned the first owner's remaining sandboxes and preserved any running peer. These were three passing diagnostic cases on a disposable early-promotion candidate. A complete repair on #5857 still needs to finish retiring the old sandbox after restart, before erasure. The existing `owed` table also releases slots for running, resumed sandboxes, so its rows alone cannot authorize vendor deletion.

- [x] Reconciled three older sandbox rehearsals against the exact PR tree `aa40cc0a`. Reapplying the ordinary branch changes reproduced nine of ten working file instances byte for byte. The first pair of interruption tests and fake match `ce357f8d`; the later launch-destination tests match `511c9391`. The runner change and its state-format test match `dae00806`, and the list implementation and guard match `9031d5ce`. The tenth file has the matching indexed test plus the separate local fixture edit already on hold. No move test is missing upstream.
- [ ] Keep the four red interrupted-move cases visible on #5857. The shared create and capacity fixes are staged independently. At `8dbd70e7` with `1c3aed85`, the combined sandbox suite passed 184, with the four move failures and two distinct lost-create-reply failures. Full types passed. Resolve positive allocation identity and verified-copy retirement before accepting the move.
