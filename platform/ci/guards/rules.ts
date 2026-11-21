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
  // #5343; untangle/manifest-split-tests after #5720 and #5722).
  cutover("docs/architecture.md"),
  cutover("docs/host-actions.md"),
  cutover(".github/workflows/tests.yml"),
  // The old suite's shape, inventory and hygiene halves, and the process tests' old-suite
  // fixtures, as the tests move beside their code (#5722, untangle/p6-tests-beside).
  cutover(".github/workflows/pr-scope-guard.yml"),
  cutover("scripts/test_shape.py"),
  cutover("scripts/test_inventory.py"),
  cutover("scripts/test_hygiene_gate.py"),
  cutover("scripts/tests/test_queue_attempts.py"),
  cutover("scripts/tests/test_review_check.py"),
  cutover("scripts/tests/test_rulebook_ratchet.py"),
  // Main-red's reading of the old suite's junit and quarantine (untangle/p6-queue-helpers).
  cutover("scripts/main_red.py"),
]);
