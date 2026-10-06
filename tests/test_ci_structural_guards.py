"""The fast runner must not turn missing/skipped guards into a green gate."""
from types import SimpleNamespace

import pytest

from scripts import ci_structural_guards as guards


@pytest.mark.parametrize("failed,skipped", [(True, False), (False, True)])
def test_failed_or_skipped_guard_has_repair_and_fails_runner(monkeypatch, failed, skipped):
    def run(args, plugins):
        report = SimpleNamespace(
            failed=failed, skipped=skipped, sections=[],
            nodeid="tests/test_storage_registry_complete.py::test_every_on_disk_name_is_classified",
        )
        plugins[0].pytest_runtest_logreport(report)
        assert "ROOT_ENTRIES" in report.sections[0][1]
        assert "Reproduce: python -m pytest" in report.sections[0][1]
        return 0  # pytest's ordinary exit for a skipped case

    monkeypatch.setattr(guards.pytest, "main", run)
    assert guards.main() == 1


def test_collection_errors_have_repair_and_nonzero_exit(monkeypatch):
    def run(args, plugins):
        report = SimpleNamespace(
            failed=True, skipped=False, sections=[], nodeid="tests/broken.py",
        )
        plugins[0].pytest_collectreport(report)
        assert "collection/import error" in report.sections[0][1]
        return 2

    monkeypatch.setattr(guards.pytest, "main", run)
    assert guards.main() == 2


def test_clean_guards_pass(monkeypatch):
    monkeypatch.setattr(guards.pytest, "main", lambda args, plugins: 0)
    assert guards.main() == 0
