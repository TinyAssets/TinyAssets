"""Quota mutations need independent inode and limit read-back."""

import subprocess

import pytest

from deploy import box_project_readback as readback


@pytest.mark.parametrize("found", [(0, False), (300001, False), (300002, True)])
def test_tree_refuses_root_project_or_inherit_mismatch(tmp_path, found):
    tree = tmp_path / "300001"
    tree.mkdir()
    with pytest.raises(RuntimeError, match="project read-back mismatch"):
        readback.verify_tree(tree, 300001, True, reader=lambda _: found)


def test_tree_refuses_descendant_mismatch_and_cleared_root_retaining_project(tmp_path):
    tree = tmp_path / "300001"
    child = tree / "center" / "payload"
    child.parent.mkdir(parents=True)
    child.write_text("payload")

    def reader(path):
        return (300002, True) if path == child else (300001, True)

    with pytest.raises(RuntimeError, match="payload"):
        readback.verify_tree(tree, 300001, True, reader=reader)
    with pytest.raises(RuntimeError, match="project read-back mismatch"):
        readback.verify_tree(tree, 0, False, reader=lambda _: (300001, True))


@pytest.mark.parametrize("found", [0, 1023, 1025])
def test_owner_refuses_wrong_hard_limit(tmp_path, found):
    tree = tmp_path / "300001"
    tree.mkdir()
    with pytest.raises(RuntimeError, match="hard limit read-back mismatch"):
        readback.verify_owner(
            tmp_path, tree, 300001, 1024 * 1024,
            reader=lambda _: (300001, True), limit_reader=lambda *_: found)


@pytest.mark.parametrize("report", ["", "#300002 0 0 1024 0\n",
                                    "#300001 0 0 0 0\n#300001 0 0 1024 0\n"])
def test_report_refuses_missing_duplicate_or_zero_status_without_project(monkeypatch, tmp_path,
                                                                            report):
    monkeypatch.setattr(readback.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, report, ""))
    with pytest.raises(RuntimeError, match="quota report"):
        readback.read_hard_limit(tmp_path, 300001)


def test_report_reads_exact_project_hard_limit(monkeypatch, tmp_path):
    report = "Project ID Used Soft Hard Warn/Grace\n#300001 4 0 1024 00 [------]\n"
    monkeypatch.setattr(readback.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, report, ""))
    assert readback.read_hard_limit(tmp_path, 300001) == 1024
