---
severity: P1
title: Served Codex launch exceeds the provider cell argument bound
filed: '2026-10-09'
summary: The served app-server flags alone contain 41 arguments, but provider cells accept at most 32. The production launch is refused before Codex starts.
---

**Filed / verified:** 2026-10-09, production image `60e87a890883`.

Source (verbatim): `ValueError: invalid provider config` from
`role_provider_cell.validate` when launching `codex_app_server.SERVED_LAUNCH_ARGS`
in the migrated-volume real-stream oracle.

`tinyassets/providers/codex_launch_contract.py:SERVED_LAUNCH_ARGS` has 41 entries,
before the executable, effort and model-catalog arguments.
`tinyassets/role_provider_cell.py:MAX_ARGS` is 32. The served adapter passes
those arguments without compaction. This is independent of the stdin-close
revocation bug.

The network fixture uses equivalent `--disable=value` / `--config=value`
spellings to fit its extra fake-endpoint settings inside the unchanged bound.
That proves the app-server stream driver, not the production argv assembly.
Resolve in the launch contract with a real served-launch test; preserve the
cell's argument and byte bounds.
