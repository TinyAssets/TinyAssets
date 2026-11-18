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
