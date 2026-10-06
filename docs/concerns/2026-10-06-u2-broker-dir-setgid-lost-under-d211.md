---
severity: P2
title: D211's narrow-only permissions drop the broker directories' setgid bit
filed: '2026-10-06'
summary: egress relocate now leaves /data/.broker and .broker/.outbound-proxy without S_ISGID, so the production-image role_image_oracle fails at its broker-directory readback on U2 images since D211
---

# Broker private directories lose setgid under D211 (U2)

**Found:** 2026-10-06, U2 continuation verification. **Severity:** P2 while
the split is OFF; it needs a decision before activation. **Owner:** U2
migration lane (lead decision D211 is involved).

`python -I -B /app/scripts/role_image_oracle.py` (the root production-image
oracle in `delivery.md`) stops at line 664:
`(.broker | .broker/.outbound-proxy) == (1002, 1101, 0o2700)` fails. It fails
the same way on U2's pre-merge candidate
`sha256:96669aed7c51ce455ce2684df0ffd27ad8fb9400362470ff345b583abc5e9c11` and on
the post-merge candidate `sha256:9668b66c…`. So it predates the U1 merge and
D218. The image oracle rows after line 664 did not run on either image.

Cause: since D211, `role_egress_migration._permissions` computes
`mode &= current`. A freshly created `.broker` (mkdir 0700) can therefore never
gain `S_ISGID` from `PRIVATE_DIR_MODE = 0o2700`. A relocated `.outbound-proxy`
keeps its legacy 0700 under D211's live-mode rule. Without setgid, entries the
broker creates later are `1002:1002`, not `1002:1101`. Whether any daemon
reader (groups 1100/1101/1102) needs group 1101 on new entries in those trees
has not been measured.

Decide one of these:
- setgid is a group-inheritance invariant, not a widened permission: a fresh
  mkdir gets the exact policy mode, and D211 narrows only rwx bits;
- or the oracle encodes the pre-D211 contract and its expectation changes.

Measure first, then fix one place. Never weaken the oracle without the
decision.
