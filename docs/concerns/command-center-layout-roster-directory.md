---
severity: P2
title: Command-center layout table does not classify the agents/ roster directory
filed: '2026-10-04'
summary: classify("agents") returns None, so the command-center inventory refuses any home with an agents/ roster until the directory is classified
---

# Cutover inventory does not classify the installed roster directory

Found while verifying audit L0 on 2026-10-04 at main `9e96ff9595`.
`tinyassets/command_center_packages.py:1409` (`destination`) installs harness
files under `agents/<slug>/`. The inventory classifies immediate home entries
(`scripts/command_center_inventory.py:529`), but
`command_center_layout.classify("agents")` returns `None`. A home with installed
roster content therefore still has an unclassified entry and exits inventory
with code 2 even after root `MEMORY.md` and `settings.yaml` are classified.

Follow up in command-center-cutover: verify the roster's writers and trust
boundary, classify its home entry, and prove an installed-package home passes
that inventory prerequisite. This is separate from L0's two root filenames;
no migration implementation or production-home inspection was performed here.
