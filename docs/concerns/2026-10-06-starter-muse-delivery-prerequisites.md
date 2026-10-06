---
severity: P2
title: Muse starter content has no provisioning or ta skill-discovery consumer yet
filed: '2026-10-06'
summary: K3 can publish and test the editable package, but existing main does not install starter_agent_files or expose indexed skills through ta.
---

K3 inspected main after PR #4514. `tinyassets/starter_skills.py` publishes
`starter_agent_files()`, but its only caller is a test. `seed_okf_bundle()`
installs only connect, share-after-publish and capabilities skills. The existing
starter seed-lifecycle and starter-agent-out-of-plumbing changes own provisioning
and renderer cutover. Implementing another installer in K3 would violate the
founder's package-only constraint and collide with those lanes.

Likewise, `ta_capabilities.engine_dispatch()` exposes granted served tools,
connections and extensions; it excludes read/write/edit/bash and does not expose
`skill_index()`. K3 skills are discoverable in a materialized workspace through
the index and readable as files, but direct `ta search` skill discovery is not
implemented by the current primitive. The K2 discovery cutover must provide it.

Acceptance still needed after those consumers land:

- Fresh-account installation through real provisioning, preserving existing
  custom/empty/deleted files through its receipt policy.
- Skills discoverable/readable through the actual ta skill-discovery route.
- Governing agent installs/activates the layout and creates verified schedules
  from the starter templates, followed by a real-user app pass at deployed SHA.

K3 tests materialize the published bundle, exercise actual owner notify through
the ta CLI with a local transport adapter, and render the actual UI in the shipped
browser sandbox. Those are package proofs, not end-to-end delivery proof.
