# L0: root harness classification verification

The companion `2026-10-04-muse-pi-gap-audit.md` preserves the supplied audit
verbatim after its added header; its line references describe main `9e96ff9595`.
Its lanes are the work plan, subject to the authoritative founder direction of
2026-10-04: no platform LLM; users bring their own and connect any platform
through general shapes without per-platform code; users control their command
center, including replacing the main agent, with cross-user isolation the only
invariant. Plumbing follows pi.dev (few tools, one extension mechanism, ta CLI),
the starter agent is editable, and bubble UX follows Meta Muse. Conflicting
recommendations in the historical audit do not override that direction.

## Verified finding

- `memory_items._read` and `_change` read/write home-root `MEMORY.md`.
- `command_center_packages.HARNESS_ROOT_FILES` accepts root `AGENTS.md`,
  `identity.md`, `MEMORY.md`, and `settings.yaml`. `model_need` reads the root
  settings file as an advisory package model requirement; this is not evidence
  that every home is seeded with settings or that runtime settings are live.
- `command_center_packages.destination` remaps package root harness files to
  `agents/<slug>/` in the recipient. Root main-agent content and installed roster
  content therefore occupy different locations.
- Inventory classifies immediate home entries only. `classify(name)` returns
  `None` for an unknown name, which inventory
  puts in `unclassified` and `incomplete_reasons`. `complete` becomes false and
  the CLI returns 2. No files are moved or lost by the read-only inventory.
- Only the inventory currently consumes this registry. Cutover migration phases
  remain unimplemented (tasks 3/4 in `command-center-cutover`), so an actual
  migration failure is not claimed. The omission blocks its inventory prerequisite.
  `migration_ready` remains false even after this fix because deferred coverage
  is independent of filename classification.

## Fix and proof

Both missing exact names are user content. Add them to `USER_NAMES`; retain the
unknown-name refusal and platform classifications. The parameterized inventory
regression checks all four root harness names, installation destinations, CLI
success, user placement, and unchanged source bytes and metadata. Before the
fix: 2 failed (`MEMORY.md`, `settings.yaml`), 2 passed. Existing test names and
assertions are preserved. The plugin runtime mirror is regenerated.

The independent unclassified `agents/` directory is recorded in
`docs/concerns/command-center-layout-roster-directory.md`; L0 does not resolve
every remaining cutover prerequisite.

## Validation

`test_command_center_inventory.py`, `test_command_center_packages.py`, and
`test_memory_items.py`: Windows Python 3.14, 173 passed / 3 skipped; Linux oracle
Python 3.11.16 with bubblewrap, 176 passed / 0 skipped. Ruff passes for all touched
Python, and the regenerated plugin import probe passes. A separate temporary-home
probe confirms the memory API writes root `MEMORY.md` and a roster home reports
`agents` as unclassified. Temporary homes stay outside the repository.
