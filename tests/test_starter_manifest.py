"""Published manifest identity and hostile-path contract."""

import pytest

from tinyassets.starter_manifest import SeedFile, SeedManifest, digest


def test_published_starter_release_is_immutable():
    from tinyassets.starter_release import starter_manifest

    manifest = starter_manifest()
    assert (manifest.version, manifest.sha256) == (
        '2-wake-when', 'a00b36f7ef39005da172f24108f60bef9bf5d3dfe8143c1af536a2034f75c935',
    ), 'Starter content changed: publish a new version and update its hash together.'


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
