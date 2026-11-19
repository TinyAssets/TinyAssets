# starter-release-version as built (2026-11-18)

An owner only schedules starter reconciliation when the carried version is
newer than its settled version (`Starter.next`). On main `f934871a` an edit to
the managed scratch skill built at the existing version 7. All five owner
package checks and the generated-files check passed after `npm run generate`.
A workerd test read a settled owner's file through its door: the same-version
update left the old skill text in place; at version 8 the same edit installed.
A personal file continues to keep the owner's text across an update.

The existing comparison with `origin/main` now checks the version and, when
equal, compares the packages as the owner actually reads them. A lower version
fails even with unchanged files. At the same version an edit to file text or
widening metadata fails; a change to the OKF watcher's tag inventory alone
passes. The generator's existing dropped-path check also serves the comparison
with main. Local regeneration can still be repeated while developing a release.

No starter content or version changes in this branch.

### The two directory plugins also consume the starter manifest

- [x] On `ac79618d`, both adding scratch to the plugins and removing schedule
  from them regenerated two different directory packages without changing their
  version. The existing release guard passed both changes because the owner
  receives the same starter files. The starter manifest requires a version
  change for changes in which skills it sends to the plugins.
- [x] The generator now exposes its existing selection of workspace paths.
  At an equal version the guard compares that selection on the current and
  previous packages alongside the existing owner comparison. Both original
  mutations failed at this assertion. A new version passed. Explicit inclusion
  of an already included skill, a plugin flag on the personal USER.md and a
  watcher-tag-only edit all passed at the same version.
- [x] All six ordinary owner starter guards and eleven ordinary OKF cases
  passed on Node 22. The separate size case was excluded. Regenerating the
  starter, both plugin trees and Doors onboarding changed zero tracked bytes.
  The runner's environment emitted an experimental proxy warning on the first
  full guard run. Disabling that warning alone restored its stderr check.
- [x] Independently exercised this branch's original owner release guard with
  the packages in PR #5692 (`3f8be4de9`) and the digest branch (`7d314f374`).
  Both the watcher and the combined v8 passed against v7 main. The combined
  v8 failed against each separately published v8 package used as a simulated
  base. The combined v9 passed against both. The actual generators produced
  both 1.0.9 plugin manifests and all seven intended skill/reference files in
  each directory. The digest branch records the owner and import measurements.
- [ ] Generator-only listing metadata, assets and prompts have a separate
  release-policy question. The directory submission checklist makes plugin
  versions follow starter versions. It does not specify when a listing-only
  edit requires a new starter version. Doors onboarding is a separate generated
  consumer. Confirm the directory policy before extending the check.
- [x] The separate OKF size case passed on the tree staged at `368ab37a`
  (one passed, eleven outside the selection). Its first run stopped when the
  sandbox binding types were missing from a temporary generator output. A
  serial two-CPU retry with the repository workflow present ran the ordinary
  watcher apply and full regeneration to completion.
