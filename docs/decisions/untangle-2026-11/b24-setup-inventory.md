# b24-setup-inventory as built (2026-11-18)

B24's inventory of the setup retires the Python command, its test and the review App
manifest form at the cutover. The manifest also names their workflow references, the
test's duration, and the helpers and imports whose last caller is that test or the
deploy kit's Python keyring case. The box-preflight test keeps its YAML loader and the
deploy kit keeps its keyring parser, input checks and rotation tests.

The secret-kind table serves two readers today. The Python setup uses it to choose its
makers, while the deploy kit uses it to identify keyrings. Its reason now describes
the kinds both need. The name and values stay in place. The final backup bucket in
that same file stays through the hold.

The two operational procedures live in the existing founder instructions in
`docs/host-actions.md`. The WorkOS migration first confirms the intended production
key before removing its repository copies. The installed review App can recover a key
under its existing identity. Its installation scope, environment restrictions and
ruleset pin still apply. Only the one-time creation form goes with the setup. Replacing
the App itself requires the existing ruleset change process.

This is one part of B24. The rest of the repository inventory and the temporary-tree
dry run of all three phases remain open, on `untangle/b24-manifest` (its line in tasks.md).
