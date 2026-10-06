---
severity: P2
title: Planned preview cell profile prevents Chromium sandbox startup
filed: '2026-10-05'
summary: 'The per-role UID split assigns ui-preview cell-deny, but the shipped sandboxed Chromium renderer fails under cell-deny and cell-links. The existing cell-nested profile renders successfully. Startup remains inactive; expanding the allowed namespace scope needs the explicit lead decision required by the build standing rule.'
---

This concerns the staged `feat/per-role-uid-split` build, not an observed live
preview outage. D56 in its design and delivery records contains the stop and
the production image digest. Reproduce with:

```text
python scripts/role_preview_profile_probe.py --image tinyassets-uid-relays:d55
```

The actual shipped renderer reports `No usable sandbox!` under cell-deny and
cell-links. With cell-nested, the identical synthetic preview delivers a PNG
without page errors. All runs use uid1003, zero capabilities, no-new-privileges,
private namespaces, Chromium's sandbox enabled, and no owner data mounts.
This diagnostic is not launcher or cross-owner acceptance.

Pending decision: permit ui-preview to use cell-nested, then implement and
prove its actual launcher route and all paired reader/isolation checks. No
runtime policy is changed by this evidence. Disabling Chromium's sandbox or
retaining privileges is not an authorized workaround. Resolve this finding
only once the authorized profile and actual class acceptance are implemented.
