## Context

The production failure is owner-measure, whose reader is imported from role_content. Wiki's raw mkdir creates an empty daemon-owned category; owner publication then refuses its parent, and every later tool's storage scan rejects that inode. Production has exactly this debris at wiki/drafts/principles (UID/GID 1001). A migrated restore normalizes the label and hides the trigger. Production is read-only for this lane.

## Decisions

Keep descriptor-pinned staging, owner UID, namespace proof, seccomp and publication validation intact. Diagnose and repair the failing operation in that boundary. Extend the authenticated completion with a bounded stderr tail; escape control characters when logging. The daemon's shared cell context reports kind, exit and tail for protocol failures, including zero-exit malformed replies. Provider stream consumers keep their existing stderr pipes.

All wiki directory creation uses ensure_data_directory, which delegates admitted owner paths to the existing content cell. Before tool disk observation, repair only empty daemon-owned immediate categories under the pinned owner wiki/pages and wiki/drafts parents. The owner cell creates replacements; rmdir atomically refuses nonempty or symlink targets; no-replace publication preserves a concurrent owner's destination. Other labels and trees remain refused. No capabilities, ownership changes or new mounts are introduced.

## Risks / Trade-offs

Stderr can contain owner material: retain only a small bounded tail, no stdout or request payload; keep bootstrap exception hooks data-free. Never forward another cell's diagnostics.

Recovery is limited to 10,000 entries per category parent. A nonempty daemon directory remains an explicit failure rather than being recursively deleted or relabelled.

## Verification and rollback

Restore the newest backup into a disposable volume; exercise direct bytes, directory-only publication and a pending owner-owned brain before real tool calls. Compare baseline and fixed images, run affected Linux suites and repo gates, then one Claude review. Revert the code commit to roll back; no data migration.
