from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile

import pytest

from tinyassets.boxes.local import LocalBoxProvider
from tinyassets.boxes.provider import WriteConflict
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.transfer import (
    MANIFEST,
    Limits,
    export_zip,
    ingest_zip,
    preview,
    quarantine,
)


@pytest.fixture
def box(tmp_path):
    provider = LocalBoxProvider(
        boxes_root=tmp_path / "boxes",
        state_dir=tmp_path / "state",
        owner_of={"a": "owner-a", "b": "owner-b"}.get,
        allow_unisolated=True,
    )
    yield provider, provider.bind("a", account_id="owner-a")
    provider.close()


def write(box, path, data, op="write"):
    provider, handle = box
    provider.write(handle, op, "/cc/" + path, data, max_bytes=len(data))


def archive(files, *, profile="export", extra=(), manifest=None):
    manifest = (
        manifest
        or json.dumps(
            {
                "format_version": 1,
                "profile": profile,
                "files": {
                    path: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for path, data in files.items()
                },
            }
        ).encode()
    )
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr(MANIFEST, manifest)
        for path, data in [*files.items(), *extra]:
            output.writestr(path, data)
    return result.getvalue()


def test_private_preview_export_and_inert_quarantine_round_trip(box, tmp_path):
    content = b"Use the files to learn.\n"
    write(box, "agents/main/AGENTS.md", content)
    view = preview(*box, ["agents/main/AGENTS.md"])
    assert "cannot prove" in view.limitations
    assert not view.blocked
    empty = ingest_zip(export_zip(*box, view, selected=[]))
    assert set(empty.files) == {MANIFEST}
    raw = export_zip(*box, view, selected=["agents/main/AGENTS.md"])
    imported = ingest_zip(raw)
    root = tmp_path / "platform-quarantine"
    root.mkdir(mode=0o700)
    target = quarantine(imported, platform_root=root)
    assert (target / "payload/agents/main/AGENTS.md").read_bytes() == content
    assert json.loads((target / "status.json").read_text())["state"] == "quarantined"
    assert not (target / "activation.json").exists()
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "payload/agents/main/AGENTS.md").stat().st_mode & 0o777 == 0o600


def test_preview_is_owner_bound_and_stale_preview_requires_review_again(box):
    provider, handle = box
    write(box, "workspace/note.md", b"My note")
    view = preview(*box, ["workspace/note.md"])
    other = provider.bind("b", account_id="owner-b")
    with pytest.raises(InvalidBundle, match="another owner"):
        export_zip(provider, other, view, selected=["workspace/note.md"])
    write(box, "workspace/note.md", b"Changed after preview", "edit")
    with pytest.raises(WriteConflict):
        export_zip(*box, view, selected=["workspace/note.md"])


def test_detected_credential_cannot_be_approved(box):
    write(box, "workspace/note.md", b"Bearer abcdef1234567890qwerty0987654321")
    view = preview(*box, ["workspace/note.md"])
    assert view.blocked == {"workspace/note.md": "detected credential"}
    with pytest.raises(InvalidBundle, match="cannot override"):
        export_zip(*box, view, selected=["workspace/note.md"])


@pytest.mark.parametrize("content", [b"\x00valid utf8 but binary", b"\xffopaque bytes"])
def test_binary_needs_exact_named_approval(box, content):
    write(box, "workspace/image.bin", content)
    view = preview(*box, ["workspace/image.bin"])
    assert view.binary == {"workspace/image.bin"}
    with pytest.raises(InvalidBundle, match="named approval"):
        export_zip(*box, view, selected=["workspace/image.bin"])
    raw = export_zip(
        *box, view, selected=["workspace/image.bin"], named_binaries=["workspace/image.bin"]
    )
    assert ingest_zip(raw).files["workspace/image.bin"] == content


def test_contact_is_blocked_and_share_cannot_export_workspace(box):
    write(box, "workspace/private.md", b"Contact person@example.com")
    view = preview(*box, ["workspace/private.md"])
    assert "contact details" in view.blocked["workspace/private.md"]
    write(box, "workspace/note.md", b"A useful note", "note")
    view = preview(*box, ["workspace/note.md"])
    with pytest.raises(InvalidBundle, match="harness"):
        export_zip(*box, view, selected=["workspace/note.md"], profile="share")


@pytest.mark.parametrize(
    "path",
    [
        "workspace/.env",
        "brain/.agent-sessions/x",
        "workspace/.git/config",
        "workspace/browser-profile/state",
        "memory/sessions/x",
    ],
)
def test_never_exported_content_is_not_even_previewed(box, path):
    with pytest.raises(InvalidBundle, match="cannot be exported"):
        preview(*box, [path])


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "C:/windows",
        "a\\b",
        "a/./b",
        "a//b",
        "a/nul.txt",
        "a/trailing.",
        "a/trailing ",
    ],
)
def test_import_path_attacks_fail_before_writing(path):
    with pytest.raises(InvalidBundle, match="path"):
        ingest_zip(archive({path: b"payload"}))


@pytest.mark.parametrize(
    "paths",
    [
        ("workspace/A", "workspace/a"),
        ("workspace/é", "workspace/e\u0301"),
        ("Workspace/a", "workspace/b"),
        ("workspace/a", "workspace/a/b"),
    ],
)
def test_collisions_include_implicit_directories(paths):
    with pytest.raises(InvalidBundle, match="collision|conflicts"):
        ingest_zip(archive({path: b"data" for path in paths}))


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK])
def test_import_rejects_special_files(kind):
    info = zipfile.ZipInfo("workspace/link")
    info.external_attr = (kind | 0o777) << 16
    with pytest.raises(InvalidBundle, match="regular files"):
        ingest_zip(archive({}, extra=[(info, b"/tmp/target")]))


def test_import_rejects_reparse_point():
    info = zipfile.ZipInfo("workspace/link")
    info.external_attr = ((stat.S_IFREG | 0o600) << 16) | 0x400
    with pytest.raises(InvalidBundle, match="reparse"):
        ingest_zip(archive({}, extra=[(info, b"target")]))


def test_import_excludes_git_without_running_hooks(tmp_path):
    sentinel = tmp_path / "executed"
    code = f"from pathlib import Path; Path({str(sentinel)!r}).touch()".encode()
    raw = archive({"run/agent.py": code}, extra=[(".git/hooks/post-checkout", code)])
    result = ingest_zip(raw)
    target = quarantine(result, platform_root=tmp_path)
    assert not sentinel.exists()
    assert not (target / "payload/.git").exists()
    assert (target / "payload/run/agent.py").read_bytes() == code


@pytest.mark.parametrize(
    "limits",
    [
        Limits(compressed=2),
        Limits(expanded=100),
        Limits(files=1),
        Limits(depth=1),
        Limits(seconds=0),
    ],
)
def test_import_resource_bounds(limits):
    with pytest.raises(InvalidBundle, match="bound|path"):
        ingest_zip(archive({"workspace/a": b"a" * 100000}), limits=limits)


def test_manifest_hash_mismatch_and_duplicate_keys():
    raw = archive({"workspace/a": b"value"})
    with zipfile.ZipFile(io.BytesIO(raw)) as incoming:
        manifest = incoming.read(MANIFEST)
    with pytest.raises(InvalidBundle, match="differs"):
        ingest_zip(archive({"workspace/a": b"changed"}, manifest=manifest))
    with pytest.raises(InvalidBundle, match="Duplicate"):
        ingest_zip(archive({}, manifest=b'{"format_version":1,"format_version":1}'))


def test_quarantine_removes_only_its_fresh_directory_on_failed_copy(tmp_path, monkeypatch):
    imported = ingest_zip(archive({"workspace/a": b"data"}))
    existing = tmp_path / "existing"
    existing.write_text("preserve")
    from tinyassets.okf import transfer

    monkeypatch.setattr(transfer.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        quarantine(imported, platform_root=tmp_path)
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_text() == "preserve"
