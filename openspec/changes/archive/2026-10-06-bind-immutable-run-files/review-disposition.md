# Approved pre-build disposition

2026-09-19. Proposal reviewed: `6bb366d46e1e6392b3fe75442281d3c83f0f1a37`.
Claude Fable 5.1 session 80912, 600-second bound, exit 0 after 245 seconds.
Full substantive stream: `shape-review.md`; terminal verdict ADAPT. The lead
read it in full and authorized the following precise corrections before build.
This is shape approval, not exact-code approval or deployed/live acceptance.

1. Accept post-exit declared output handoff, remove active-node capture RPC.
   Require demonstrated managed namespace writer quiescence, not plain process
   group termination, plus atomic cross-process source exclusion until copy ends.
   A sole-holder observation is not sufficient: new acquisitions/readers/writers
   must be excluded too. Initially workspace capture is POSIX-only; ordinary
   authoring capture/readback on Windows and Linux both need native release proof.
2. Extract byte-only existing transfer reservations without workspace jobs or
   effect charges. Do not activate unused tier storage values. One explicit
   operational subsystem ceiling, unset/invalid refusal, is configured by normal
   global platform rollout before exposure. Never a per-user setup patch or new
   entitlement/price. Physical free space already excludes retained bytes.
   ENOSPC releases unused retention only after verified cleanup while retaining
   actual transport consumption.
3. Publish durable bodies under physical coordinator alone, then author fence ->
   runs-store commit. Hold operation exclusion across both; collector must prove
   no live publishing/committing operation before orphan reclamation. Never wait
   on the blocking coordinator under DB writer locks. Extract bounded stable-file
   helpers, but keep run-file records out of the whole-file blob JSON index.
4. Add receiver-owned run admission snapshot/start authority in the existing
   runs store. Inputs remain only on the run row. All delivery origins migrate to
   one run-keyed guard with explicit active legacy-worker fencing; no dual-guard
   gap, second queue or automatic replay of started effects. The review's phrase
   'key rename' does not replace migration proof.

Qualifications 1, 2 and 4 above are the lead's explicit refinements of the
reviewer's shorthand. In particular, Windows supported-path proof is NOT later
hardening, a count check is NOT exclusion, and failed copy accounting is NOT
necessarily zero transport. All were accepted before implementation.

One patch/branch and 12-task boundary retained. Start red-first with manifest
preservation, byte-only reservations and generic custody/admission core; keep the
full capability open until adapters, migration, release gates and ordinary
two-owner rendered live proof pass. No further broad shape review is required
unless a new irreversible boundary appears. Exact-head cross-family review still
gates landing. No production/user-account actions are authorized by this file.
