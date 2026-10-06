"""Published manifest identity and hostile-path contract."""

import pytest

from tinyassets.starter_manifest import SeedFile, SeedManifest, digest


@pytest.mark.parametrize(
    "path",
    [
        "../AGENTS.md",
        "/AGENTS.md",
        "a//b",
        "a/./b",
        "a\\b",
        "C:/b",
        "a\x00b",
        ".runtime/a",
        "a/../b",
        "a./b",
        "a /b",
    ],
)
def test_reject_escaping_or_ambiguous_paths(path):
    with pytest.raises(ValueError):
        SeedFile(path, b"template")


def test_manifest_identity_covers_content_history_and_paths():
    file = SeedFile("agents/helper/AGENTS.md", b"stock", (digest(b"old"),), True)
    manifest = SeedManifest("starter-agent-v1", "1", (file,))
    assert manifest.sha256 == SeedManifest("starter-agent-v1", "1", (file,)).sha256
    assert manifest.sha256 != SeedManifest("starter-agent-v1", "2", (file,)).sha256
    assert (
        manifest.sha256
        != SeedManifest("starter-agent-v1", "1", (SeedFile(file.path, file.content),)).sha256
    )
    with pytest.raises(ValueError):
        SeedManifest("starter-agent-v1", "1", (file, file))


@pytest.mark.parametrize("paths", [("a", "a/b"), ("AGENTS.md", "agents.md")])
def test_manifest_rejects_conflicting_files(paths):
    with pytest.raises(ValueError):
        SeedManifest("starter-agent-v1", "1", tuple(SeedFile(p, b"x") for p in paths))
