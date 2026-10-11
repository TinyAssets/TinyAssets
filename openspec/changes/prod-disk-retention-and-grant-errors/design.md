## Context

Read-only production inspection confirmed the 64 KiB retention receipt limit rejects a 225,370-byte release-state document. Containerd represents digest pulls in both RepoTags and RepoDigests; that shape is already accepted. Warnings and SuccessExitStatus=1 hide incomplete passes.

## Goals / Non-Goals

Prevent cache growth from disabling tool jails and make resource/authority refusals diagnosable. No production mutations in this lane and no change to grant authorization.

## Decisions

- Keep ID-based selection and registry recovery proof, lock/revalidate before deletion, and preserve container references and explicit tags (including cutover). Keep one prior image, use the actual captured rollback when available, and read bounded release evidence with room for containment metadata.
- Run the checkout's retention code before prefetch and recheck before deploy. Require at least 10 GiB free and less than 80% used before a new image pull; reuse the same helper for post-canary cleanup. No volume or builder pruning.
- Add canary-only disk measurements to pulse. Feed 80% warning / 90% critical into the existing uptime issue and Pushover path, with no page for a disk-only warning.
- Carry a fixed GrantPathRefused type across broker IPC. Format method/path from the caller's original request at the effector, never from broker secrets, redirects or the allowlist.
- Add shared_disk_full to the existing capability classifier, preserving bounded log labels.

## Risks / Trade-offs

Registry unavailability can block pruning; deploy fails before pull if cleanup cannot complete. Explicit pins and in-use images may prevent headroom recovery; fail loudly instead of deleting them. Monitoring observes the daemon root/data filesystems; host admission measures the image store as well.

## Migration Plan

No data migration. Deploy workflow stages the fixed helper before pulling, so first deploy does not depend on an obsolete installed timer. Rollback uses the retained previous digest; revert the PR if needed.
