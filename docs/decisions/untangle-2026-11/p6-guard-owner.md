# p6-guard-owner as built (2026-11-18)

P6 decides the proposed refusal of new Python guards of platform paths (d3 §B8).
Review the assertion's owner. A new platform guarantee needs a test that the
owning workspace runs and the cutover retains. A Python caller or process
runner can still test its side of the boundary. The builder and reviewer use
the same rule in the untangle skill.

The migration lock shows why the suite matters. The first PR added the workspace
proofs. The second retired two migration tests, four workflow-leg tests and ten
environment tests. The third supplied the base history and enabled the workspace
migration check. On f934871ad all six tests in owner/guards/migrations.test.ts
passed. Changing only the first deployed digest in a disposable checkout failed
the lock test. Restoring the digest passed all six.
[#5429](https://github.com/TinyAssets/TinyAssets/pull/5429),
[#5449](https://github.com/TinyAssets/TinyAssets/pull/5449),
[#5451](https://github.com/TinyAssets/TinyAssets/pull/5451).

A path-text veto misses ownership in both directions. The ten removed environment
test definitions checked workflows and contained no literal platform/ path.
At the same time a queue classifier's input contains platform test names, and
the setup client checks the real App form in platform/deploy/review-app.html.
The npm launch-integrity check deliberately runs outside npm. The Python test
of the PR tamper checker reads real workspace tests as inputs. All 27 focused
consumer and process cases passed.

At f934871ad, 14 of 291 legacy pytest modules contained the text platform/.
Applied to each module alone, the existing tree_reads helper classified nine
as possible platform readers. The full selection also follows imports and git.
It correctly classified a test reading a checked-in platform path. It also
classified a comparison against a platform path in a diagnostic record, where
no file was opened. This conservative behavior is appropriate when selecting
tests. It cannot establish which component a test proves. P6 therefore drops
the automatic path-text refusal and keeps the ownership check in review.
