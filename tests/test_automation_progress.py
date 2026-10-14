"""Real temporary SQLite/file tests; no model-output simulation."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "automation_progress", Path(__file__).parents[1] / "tinyassets/automation_progress.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)



commit_artifact = module.commit_artifact

class ProgressTests(unittest.TestCase):
    def test_preserves_prior_content_and_does_not_mutate_inputs(self):
        first = commit_artifact({}, work_id="one", artifact="first")
        second = commit_artifact(first, work_id="two", artifact="second")
        self.assertEqual(second["completed"]["one"], first["completed"]["one"])
        self.assertEqual(list(first["completed"]), ["one"])
        self.assertEqual(list(second["completed"]), ["one", "two"])

    def test_repeated_id_refused(self):
        first = commit_artifact({}, work_id="one", artifact="first")
        with self.assertRaisesRegex(ValueError, "work_already_completed"):
            commit_artifact(first, work_id="one", artifact="changed")

    def test_renaming_same_artifact_does_not_evade_deduplication(self):
        first = commit_artifact({}, work_id="one", artifact="first")
        with self.assertRaisesRegex(ValueError, "artifact_already_completed"):
            commit_artifact(first, work_id="renamed", artifact=" first ")

    def test_corruption_is_not_silently_reset(self):
        first = commit_artifact({}, work_id="one", artifact="first")
        first["completed"]["one"]["artifact"] = "changed"
        with self.assertRaisesRegex(ValueError, "checkpoint_corrupt"):
            commit_artifact(first, work_id="two", artifact="second")

    def test_empty_artifact_refused(self):
        with self.assertRaisesRegex(ValueError, "artifact_empty"):
            commit_artifact({}, work_id="one", artifact=" ")

    def test_malformed_checkpoint_refused(self):
        with self.assertRaisesRegex(ValueError, "checkpoint_invalid"):
            commit_artifact({"completed": []}, work_id="one", artifact="content")

    def test_malformed_id_refused(self):
        with self.assertRaisesRegex(ValueError, "work_id_invalid"):
            commit_artifact({}, work_id="\n", artifact="content")


if __name__ == "__main__":
    unittest.main()
