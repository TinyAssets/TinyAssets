"""The boxhostd installer is the only ship list for its sibling files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.boxhostd_install_files import (
    INSTALLER,
    InstallFileError,
    referenced_basenames,
    ship_list,
)

ROOT = Path(__file__).resolve().parents[1]
DERIVED_SHIP = (
    'python scripts/boxhostd_install_files.py > "$list"',
    'mapfile -t boxhostd_files < "$list"',
    '"${boxhostd_files[@]}"',
)


def _workflows() -> list[Path]:
    directory = ROOT / ".github/workflows"
    return sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])


def assert_workflow_ships_derived_list(text: str, referenced: list[str]) -> None:
    if "install-boxhostd-preflight.sh" not in text:
        return
    missing = [line for line in DERIVED_SHIP if line not in text]
    if missing:
        raise AssertionError(
            "a workflow that runs the boxhostd installer must copy "
            f"scripts/boxhostd_install_files.py output; missing {missing}"
        )
    named = [name for name in referenced if name in text]
    if named:
        raise AssertionError(
            f"workflow names installer siblings itself; the ship list is the installer: {named}"
        )


def test_installer_references_resolve_and_include_the_unit_renderer() -> None:
    installer = (ROOT / INSTALLER).read_text(encoding="utf-8")
    referenced = referenced_basenames(installer)
    paths = ship_list(ROOT)
    assert "box-store-unit.sh" in referenced
    assert "deploy/box-store-unit.sh" in paths
    assert set(referenced) <= {Path(path).name for path in paths}
    assert paths[0] == INSTALLER
    assert len(paths) == len({Path(path).name for path in paths})
    for path in paths:
        assert (ROOT / path).is_file()


def test_cli_prints_the_ship_list() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/boxhostd_install_files.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ship_list(ROOT)


def test_only_deploy_prod_runs_the_installer_and_ships_the_derived_list() -> None:
    referenced = referenced_basenames((ROOT / INSTALLER).read_text(encoding="utf-8"))
    runners = [path for path in _workflows() if "install-boxhostd-preflight.sh" in path.read_text()]
    assert [path.name for path in runners] == ["deploy-prod.yml"]
    for path in runners:
        assert_workflow_ships_derived_list(path.read_text(encoding="utf-8"), referenced)


def test_hardcoded_scp_list_is_rejected() -> None:
    hardcoded = "scp deploy/install-boxhostd-preflight.sh deploy/boxhostd_idle.py host:/tmp/\n"
    with pytest.raises(AssertionError, match="must copy"):
        assert_workflow_ships_derived_list(hardcoded, ["boxhostd_idle.py"])


def test_naming_a_sibling_in_the_workflow_is_rejected() -> None:
    text = "\n".join(DERIVED_SHIP) + "\ninstall-boxhostd-preflight.sh\nbox-store-unit.sh\n"
    with pytest.raises(AssertionError, match="box-store-unit.sh"):
        assert_workflow_ships_derived_list(text, ["box-store-unit.sh"])


def test_missing_sibling_reference_fails(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "install-boxhostd-preflight.sh").write_text(
        'bash "${source_dir}/box-store-unit.sh"\n',
        encoding="utf-8",
    )
    with pytest.raises(InstallFileError, match="box-store-unit.sh"):
        ship_list(tmp_path)


def test_ambiguous_basename_fails(tmp_path: Path) -> None:
    for base in ("deploy", "scripts"):
        directory = tmp_path / base
        directory.mkdir()
        (directory / "box-store-unit.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "deploy/install-boxhostd-preflight.sh").write_text(
        'bash "${source_dir}/box-store-unit.sh"\n',
        encoding="utf-8",
    )
    with pytest.raises(InstallFileError, match="exactly one"):
        ship_list(tmp_path)


def test_indirect_list_must_be_literal_filenames() -> None:
    text = 'for name in "${files[@]}"; do install "${source_dir}/${name}"; done\n'
    with pytest.raises(InstallFileError, match="literal file list"):
        referenced_basenames(text)


def test_unparsed_source_dir_reference_fails() -> None:
    with pytest.raises(InstallFileError, match="unparsed"):
        referenced_basenames('cp "${source_dir}/nested/file.sh" /opt\n')
