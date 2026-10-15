# C6 CLI credential vault is copied into the executable owner box

`tinyassets/box_layout_migration.py::_copy` copies each center directory in full
to the owner box. That includes `.credential-vault.json` and any
`.credentials/codex/auth.json` artifact. `tinyassets/boxes/boxd.py` exposes the
same center tree to the owner's CLI and bash executions. The long-lived OAuth
refresh token is therefore present in guest-accessible persistent files after
the committed layout move, contrary to the one-run tmpfs lease in
`openspec/changes/one-box-per-owner/design.md` and ADR-022.

The native runner can now resolve and pin these bytes through its admitted box
handle without a host center path. This does not close the underlying custody
gap. Before the flip, the migration and credential deposit/refresh paths must
place the long-lived vault outside the guest tree, then lease only the selected
CLI document through `BoxLauncher`'s private channel. The full backup and
restore contract must cover that private custody store. Remove this finding
after a production-copy check proves the guest cannot read persistent token
bytes and the one-run refresh/writeback still works.
