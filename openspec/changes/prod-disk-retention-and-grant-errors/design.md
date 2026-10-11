## Context

Read-only production inspection confirmed the 64 KiB retention receipt limit rejects a 225,370-byte release-state document. Containerd represents digest pulls in both RepoTags and RepoDigests; that shape is already accepted. Warnings and SuccessExitStatus=1 hide incomplete passes.

## Goals / Non-Goals

Prevent cache growth from disabling tool jails and make resource/authority refusals diagnosable. No production mutations in this lane and no change to grant authorization.

## Decisions

- Keep ID-based selection and registry recovery proof, lock/revalidate before deletion, and preserve container references and explicit tags (including cutover). Keep the newest prior image through the canary window plus the actual captured rollback when available, and read bounded release evidence with room for containment metadata.
- Run the checkout's retention code before prefetch and recheck before deploy and again under the fail-safe host lock, exempting cached immutable images from a second download reserve. Require at least 10 GiB free and less than 80% used before a new image pull; reuse the same helper for post-canary cleanup. No volume or builder pruning.
- Add canary-only disk measurements to pulse. Reuse the uptime issue and Pushover path with an independent disk-pressure label: warning at 80%, paging at 90%, unknown measurements do not page, and disk pressure never triggers P0 restart triage.
- Carry a fixed GrantPathRefused type across broker IPC. Format method/path from the caller's original request at the effector, never from broker secrets, redirects or the allowlist.
- Add shared_disk_full to the existing capability classifier, preserving bounded log labels.

## Risks / Trade-offs

Registry unavailability can block pruning; deploy fails before pull if cleanup cannot complete. Explicit pins and in-use images may prevent headroom recovery; fail loudly instead of deleting them. Monitoring observes the daemon root/data filesystems; host admission measures the image store as well.

## Migration Plan

No data migration. Deploy workflow stages the fixed helper before pulling, so first deploy does not depend on an obsolete installed timer. Rollback uses the retained previous digest; revert the PR if needed.

## Cross-family review disposition

- AGREE: protect the immediate predecessor while the post-canary receipt still names an older rollback. Always retaining the newest older image closes that window; normal completed deploys still retain one rollback plus explicit pins.
- AGREE: disk warnings must not enter P0 restart triage. The existing alarm sink now handles an independent disk-pressure issue, unknown observations do not page, and warning-to-critical escalation uses the existing Pushover ladder.
- AGREE: cached prefetch must not require another 10 GiB. Admission recognizes exact cached digests, and the fail-safe repeats the check under the host lock immediately before pulling. A timer may reclaim an unadopted candidate during the wait; re-pulling then requires fresh headroom. It has no permanent retention exemption.
- DISAGREE_EVIDENCE: the opt-in does not block this production repair: read-only inspection confirms TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY=1, with the default count mode. Deliberately disabled cleanup, unavailable registry recovery or incomplete retention remains a loud refusal rather than silently permitting continued accumulation, as requested. No flag is silently enabled.
