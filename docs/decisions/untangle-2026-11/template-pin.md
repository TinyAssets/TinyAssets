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

- [ ] A broken or lost carry-out reply refuses the pending start before the
  old sandbox launches it. The PR's existing failed carry-in control still
  serves the start on the old sandbox.
- [ ] When the vendor deletes the old sandbox and its acknowledgement is lost,
  the error path also deletes the replacement. An independent diagnostic
  observed two deletes, zero surviving sandboxes and a refused start.
  Separate an uncertain deletion from a failed copy. Record which sandbox
  holds the verified copy across retries and Worker restarts before any
  subsequent deletion. Preserve a recoverable copy when either acknowledgement
  is missing.
- [ ] Passing these control tests also requires preserving each pending file
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
- [ ] Before promotion, a transfer failure should leave the old sandbox
  available. After verifying a complete transfer, record the destination
  as authoritative and the original as pending cleanup before deleting it.
  The existing vendor deletion accepts both 204 and 404. Design and verify
  cleanup across restart, uncertain creation, and account erasure. A reread
  of two IDs alone does not establish which copy was verified.
