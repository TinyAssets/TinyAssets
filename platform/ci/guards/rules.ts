/**
 * What the ci workspace still owes the platform's guards (`ci/kit/owed.ts`): its own files',
 * the workflows' inline modules, which its layer guard reads (`ci/kit/layers.ts`'s `INLINE`),
 * and, to the manifest's guard, each file the manifest leaves still holding what goes, by its
 * path, until the PR its group names fixes its entry: none may be left when the cutover runs.
 */
import type { Owed } from "../kit/owed";

const cutover = (what: string): Owed => Object.freeze({ guard: "cutover", what, fix: "B24" });

export const OWED: readonly Owed[] = Object.freeze([
  // Mostly the old stack: split, each old part into a file of its own the manifest deletes,
  // once #5712 (untangle/p9-cleanup) and #5696 land (untangle/manifest-split-docs).
  cutover("docs/architecture.md"),
  cutover("docs/host-actions.md"),
  // The queue helpers' and main-red's reading of the old suite's junit and quarantine
  // (untangle/p6-queue-helpers).
  cutover("scripts/main_red.py"),
  cutover("scripts/test_inventory.py"),
  // The hygiene gate's pytest half and the carried process tests' old-suite fixtures, reshaped
  // as the tests move beside their code (untangle/p6-tests-beside).
  cutover("scripts/test_hygiene_gate.py"),
  cutover("scripts/tests/test_queue_attempts.py"),
  cutover("scripts/tests/test_review_check.py"),
  cutover("scripts/tests/test_rulebook_ratchet.py"),
]);
