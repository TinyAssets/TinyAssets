Pushed to the same branch for PR #5229.

- HEAD: `c88238ca2078210df1cb3f11e28faa53636c46fd`
- Spec requires N10 owner-session authority for binding permission, including `extend`/`full` committer recording. Box commits and widening grant no permission.
- Added the requested scenario and named `test_box_committed_binding_does_not_satisfy_allowlist_or_ask`.
- Validation: targeted Linux oracle **35 passed**; ruff, strict OpenSpec validation and diff checks passed.
- Runtime implementation and the new regression test remain planned: this was a spec-only fix. Working tree clean.