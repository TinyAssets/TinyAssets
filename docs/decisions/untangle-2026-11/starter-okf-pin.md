# Check the OKF reference against its reviewed pin

Main at `d662e63e` approves the upstream revision in the starter manifest. The watcher moves that pin in its reviewable draft and the starter generator copies it into the bundle. A guard separately fixed both the revision and spec blob to the previous release. A valid update therefore failed before testing the next release's actual text.

- [x] Replay the frozen spec and README from #5692 on current main. Both original blob IDs matched. The actual watcher projected starter 11 from 10. The starter and plugin generators preserved all 16 paths and changed one reference. Both plugin versions were 1.0.11. The original ordinary starter suite passed ten and failed the obsolete pin assertion. One size case was outside that run.
- [x] Compare the bundled pin to the source manifest. Keep the checks on repository identity, commit shape, the Git blob ID of the actual text, the provenance line, license, supported spec version and hidden characters. All eleven ordinary checks passed on both current version 10 and the projected version 11. Four separate changes to the generated data each failed the ordinary provenance test: differing manifest metadata, damaged reference text, lost provenance, and a malformed commit. Restoring the original data passed.
- [ ] Coordinator reviews the proposed upstream change and release ordering. This branch changes the guard and provides a rehearsal of a proposed upgrade. The version 11 package was generated locally. Tracked on #5692.

Verification of the staged guard `e93f8e65`: the separate size case also passed on each package, and full platform TypeScript passed on the branch. Two additional local Owner Workerd journeys began at the actual version 10 package, ran the projected version 11 package and finished with all 16 receipts. The untouched reference updated in place with no extra file. The edited reference kept the owner's exact text and put the proposed upstream bytes beside it in the normal `.ta-new` file. These are synthetic owners; the published draft is still version 8.

The ordinary Owner starter and starter-review Workerd files subsequently passed all 24 cases on version 10 and all 24 on the version 11 projection. Full platform TypeScript passed on the projection as well. PR #5692 links the independent guard and the two real upgrade journeys. The coordinator's upstream and release decision remains open.

- [x] Reconciled both local version 11 rehearsals with the active proposal #5692 at `3f8be4de` and main `d662e63e`. Read the two proposed upstream blobs directly and verified their Git hashes against the proposal's pin (`a0b7df14` for the spec and `43551612` for the README). On a fresh copy of main, ran the watcher's actual comparison and apply functions, the starter bundler and both plugin projections. Every one of the seven generated files was byte-identical to both saved rehearsals. Their eighth changed file was the reviewed-pin guard: applying `bc596859` to each rehearsal base reproduced it exactly, including on the same-commit-tag change `5d4d1082`. All sixteen working file instances are thus accounted for. The existing Worker and typecheck results above stand on their tested revisions. This check ran the generators and byte comparisons. Coordinator review of the proposed upstream change and release sequence remains open on #5692.

### Replaying an actual OKF update through the Owner Worker

The routine starter suite covers first installation of the real bundled reference and managed updates using test files. To check the actual old and proposed reference texts, use two disposable checkouts of the intended TinyAssets base. Install the locked platform dependencies in both. Keep the current bundle in the Owner checkout and copy in the opt-in test from this branch.

In the second checkout, run the existing watcher against the proposed upstream revision. For #5692 the TinyAssets base was `d662e63e`. Its upstream was `GoogleCloudPlatform/open-knowledge-format` at `8a8ff87bfd3847c34c9ddd4fbfd9296be2aee4a3`, fetched without tags as the proposal records none. Verify the upstream checkout's `HEAD:SPEC.md` and `HEAD:README.md` match the two blob IDs in the proposal's manifest. Run the watcher at the root of this candidate checkout.

```sh
node platform/starter/okf-watch.ts apply --upstream "$upstream" --body "$candidate/proposal.md"
```

Set `candidate` to the absolute path of that second checkout and run the following in the disposable Owner checkout.

```sh
cd platform/owner
cp test/okf-upgrade.test.ts.in test/okf-upgrade.test.ts
cp "$candidate/platform/starter/starter.json" test/okf-candidate.json
../node_modules/.bin/vitest run --project workerd test/okf-upgrade.test.ts
```

The test starts two settled owners with the actual base bundle. It checks where the proposed reference lands, whose edited bytes survive, the resulting starter version and the complete set of receipt paths. The candidate must have a newer version and different reference bytes. Its generated data and the runnable copy of the test stay in the disposable checkout.

On November 26 the normal watcher CLI and generators reproduced all seven outputs from the pinned upstream checkout on main `d662e63e`. The generated starter matched the entire saved Worker fixture byte for byte (SHA-256 `ff593f1eabfa8e68091f8d0a3275e4c30860093c57f5c538c9812e921e0c3a63`). Both journeys passed alone and again with the 24 routine starter cases on Node 22. All 26 passed and the full platform typecheck passed with the candidate and test present. The historical version 8 bundle from the proposal failed both cases against installed version 10. A bumped version containing the old reference also failed both, at the changed-text check.
