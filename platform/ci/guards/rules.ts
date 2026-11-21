/**
 * What the ci workspace still owes the platform's guards (`ci/kit/owed.ts`): its own files',
 * the workflows' inline modules, which its layer guard reads (`ci/kit/layers.ts`'s `INLINE`),
 * and, to the manifest's guard, each file the manifest leaves still holding what goes, by its
 * path, until the PR its group names fixes its entry: none may be left when the cutover runs.
 */
import type { Owed } from "../kit/owed";

const cutover = (what: string): Owed => Object.freeze({ guard: "cutover", what, fix: "B24" });

export const OWED: readonly Owed[] = Object.freeze([
  // Mostly the old stack: each split, its old part into a file of its own the manifest
  // deletes, once the open PRs on them land (untangle/manifest-split-docs after #5696 and
  // #5343; untangle/manifest-split-tests).
  cutover("docs/architecture.md"),
  cutover("docs/host-actions.md"),
  cutover(".github/workflows/tests.yml"),
  // The old suite's hygiene half, the scope guard's old-suite paths, and the process tests'
  // old-suite fixtures, left after #5722 moved the tests beside their code
  // (untangle/p6-tests-beside).
  cutover(".github/workflows/pr-scope-guard.yml"),
  cutover("scripts/test_hygiene_gate.py"),
  cutover("scripts/tests/test_queue_attempts.py"),
  cutover("scripts/tests/test_review_check.py"),
  cutover("scripts/tests/test_rulebook_ratchet.py"),
  // Main-red's reading of the old suite's junit and quarantine (untangle/p6-queue-helpers).
  cutover("scripts/main_red.py"),
  // The site's links to the old specs, at the site's cutover PR (tasks.md §10 step 5).
  cutover("WebSite/site-react/app/developers/page.tsx"),
  cutover("WebSite/site-react/lib/site.ts"),
  cutover("WebSite/site-react/public/llms.txt"),
]);
