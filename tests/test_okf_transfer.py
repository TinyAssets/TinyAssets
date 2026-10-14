from __future__ import annotations

import io
import json
import stat
import zipfile

import pytest
import yaml

from tinyassets.boxes.local import LocalBoxProvider
from tinyassets.boxes.provider import WriteConflict
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.manifest import build_manifest
from tinyassets.okf.transfer import (
    MANIFEST,
    Limits,
    export_zip,
    ingest_zip,
    preview,
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
    manifest = manifest or json.dumps(build_manifest(
        profile=profile, name="Test", description="", files=files,
        agents=sorted(p.split("/")[1] for p in files
                      if p.startswith("agents/") and p.endswith("/AGENTS.md")),
        workflows=[], ui="", automations=[], model="", connections=[],
    )).encode()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr(MANIFEST, manifest)
        for path, data in [*files.items(), *extra]:
            output.writestr(path, data)
    return result.getvalue()


def test_private_preview_export_and_inert_ingestion_round_trip(box):
    content = b"Use the files to learn.\n"
    write(box, "agents/main/AGENTS.md", content)
    view = preview(*box, ["agents/main/AGENTS.md"])
    assert "cannot prove" in view.limitations
    assert not view.blocked
    empty = ingest_zip(export_zip(*box, view, selected=[]))
    assert "agents/main/AGENTS.md" not in empty.files
    assert {MANIFEST, "run/agent.py", ".env.example", "README.md"} <= empty.files.keys()
    raw = export_zip(*box, view, selected=["agents/main/AGENTS.md"])
    imported = ingest_zip(raw)
    assert imported.files["agents/main/AGENTS.md"] == content


def test_actual_root_harness_settings_and_roster_round_trip(box):
    from tinyassets.okf.transfer import discover

    sources = {
        "AGENTS.md": b"Use the owner's selected files.",
        "skills/notes/SKILL.md": b"Read notes before writing.",
        "settings.yaml": json.dumps({
            "schema_version": 1,
            "model": {"id": "source-model-identifier", "connection": "source-private-connection",
                      "effort": "high"},
            "loop": {"compaction": {"trigger_tokens": 12345678901}},
            "agent": {"owner": "source-private-owner", "name": "Notes",
                      "configuration": {"contact": "person@example.com"}},
        }).encode(),
        "agents/editor/AGENTS.md": b"Edit selected prose.",
        "agents/editor/settings.yaml": b"model: another-source-model\n",
    }
    for i, (path, raw) in enumerate(sources.items()):
        write(box, path, raw, f"source-{i}")
    write(box, "requests.json", b"private platform state", "private")
    write(box, "MEMORY.md", b"unselected private memory", "memory")
    listed = {row["path"] for row in discover(*box)["files"]}
    assert set(sources) <= listed
    assert not {"requests.json", "MEMORY.md"} & listed
    view = preview(*box, list(sources))
    assert not view.blocked
    assert view.contacts == {"settings.yaml": ("person@example.com",)}
    assert b"source-private" not in b"".join(view.files.values())
    assert b"source-model" not in b"".join(view.files.values())
    imported = ingest_zip(export_zip(*box, view, selected=list(sources)))
    assert imported.files["agents/main/AGENTS.md"] == sources["AGENTS.md"]
    assert imported.files["agents/main/skills/notes/SKILL.md"] == sources["skills/notes/SKILL.md"]
    assert "AGENTS.md" not in imported.files
    settings = yaml.safe_load(imported.files["agents/main/settings.yaml"])
    assert settings["loop"]["compaction"]["trigger_tokens"] == 12345678901
    assert settings["agent"]["configuration"]["contact"] == "[contact removed]"
    assert "owner" not in settings["agent"]
    manifest = json.loads(imported.files[MANIFEST])
    assert manifest["agents"] == ["editor", "main"]
    assert [row["model_env"] for row in manifest["roster"]] == ["AGENT_1", "AGENT_2"]
    assert b"AGENT_1_MODEL_NAME=\n" in imported.files[".env.example"]
    assert b"AGENT_2_MODEL_API_KEY=\n" in imported.files[".env.example"]
    included = ingest_zip(export_zip(*box, view, selected=list(sources),
                                    included_contacts={"settings.yaml": [0]}))
    assert yaml.safe_load(included.files["agents/main/settings.yaml"])["agent"][
        "configuration"]["contact"] == "person@example.com"


@pytest.mark.parametrize("approved", [[], [1]])
def test_settings_scalars_survive_contact_review_export_and_import(box, approved):
    from tinyassets.agent_definition import parse_settings

    configuration = {
        "numbers": [1e-20, 1e20, -1.2e30, 12345678901, True, None, "1e+20"],
        "contacts": ["first@example.com", "second@example.com"],
    }
    source = yaml.safe_dump({
        "schema_version": 1,
        "loop": {"retry": {"backoff_seconds": 0.00001}},
        "agent": {"configuration": configuration},
    }).encode()
    assert parse_settings(source).loop.backoff_seconds == 0.00001
    write(box, "AGENTS.md", b"Use selected files.")
    write(box, "settings.yaml", source, "settings")
    view = preview(*box, ["AGENTS.md", "settings.yaml"])
    assert view.contacts == {"settings.yaml": ("first@example.com", "second@example.com")}
    bundle = ingest_zip(export_zip(
        *box, view, selected=list(view.files), included_contacts={"settings.yaml": approved},
    ))
    for raw in (view.files["settings.yaml"], bundle.files["agents/main/settings.yaml"]):
        settings = parse_settings(raw)
        assert type(settings.loop.backoff_seconds) is float
        assert settings.loop.backoff_seconds == 0.00001
        numbers = settings.agent.configuration["numbers"]
        assert numbers == configuration["numbers"]
        assert list(map(type, numbers)) == list(map(type, configuration["numbers"]))
    actual = parse_settings(bundle.files["agents/main/settings.yaml"]).agent.configuration
    assert actual["contacts"] == [
        contact if i in approved else "[contact removed]"
        for i, contact in enumerate(configuration["contacts"])
    ]


def test_agent_settings_references_do_not_exempt_arbitrary_credentials(box):
    write(box, "AGENTS.md", b"Use files.")
    raw = {"schema_version": 1, "model": {"id": "a" * 32, "connection": "b" * 32},
           "agent": {"owner": "c" * 32}}
    write(box, "settings.yaml", json.dumps(raw).encode(), "settings")
    view = preview(*box, ["AGENTS.md", "settings.yaml"])
    assert not view.blocked
    raw["agent"]["configuration"] = {"note": "Bearer abcdef1234567890qwerty0987654321"}
    write(box, "settings.yaml", json.dumps(raw).encode(), "secret-note")
    view = preview(*box, ["settings.yaml"])
    assert view.blocked == {"settings.yaml": "detected credential"}
    with pytest.raises(InvalidBundle, match="cannot override"):
        export_zip(*box, view, selected=["settings.yaml"])


def test_selected_root_and_nested_main_harness_cannot_overwrite_each_other(box):
    write(box, "AGENTS.md", b"root")
    write(box, "agents/main/AGENTS.md", b"nested", "nested")
    view = preview(*box, ["AGENTS.md", "agents/main/AGENTS.md"])
    with pytest.raises(InvalidBundle, match="conflicts"):
        export_zip(*box, view, selected=list(view.files))


def test_roster_cannot_claim_a_different_settings_reference_on_import(box):
    write(box, "AGENTS.md", b"Use files.")
    write(box, "settings.yaml", b"model: local-model", "settings")
    view = preview(*box, ["AGENTS.md", "settings.yaml"])
    bundle = ingest_zip(export_zip(*box, view, selected=list(view.files)))
    files = dict(bundle.files)
    manifest = json.loads(files.pop(MANIFEST))
    manifest["roster"][0]["model_env"] = "AGENT_999"
    with pytest.raises(InvalidBundle, match="roster"):
        ingest_zip(archive(files, manifest=json.dumps(manifest).encode()))


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


def test_contacts_are_redacted_unless_individually_selected_and_share_is_restricted(box):
    write(box, "workspace/private.md", b"Contact person@example.com")
    view = preview(*box, ["workspace/private.md"])
    assert not view.blocked
    assert view.contacts["workspace/private.md"] == ("person@example.com",)
    redacted = ingest_zip(export_zip(*box, view, selected=["workspace/private.md"]))
    assert b"example.com" not in redacted.files["workspace/private.md"]
    included = ingest_zip(export_zip(
        *box, view, selected=["workspace/private.md"],
        included_contacts={"workspace/private.md": [0]},
    ))
    assert included.files["workspace/private.md"] == b"Contact person@example.com"
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
    assert not sentinel.exists()
    assert not any(p.startswith(".git/") for p in result.files)
    assert result.files["run/agent.py"] == code


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


@pytest.mark.parametrize("data", [
    b"MODEL_API_KEY=actual-value\n", b"X=$(touch proof)\n", b"X=\nexport Y=\n",
])
def test_environment_example_cannot_carry_values_or_shell_code(data):
    with pytest.raises(InvalidBundle, match="never-exported"):
        ingest_zip(archive({".env.example": data}))


def test_share_contacts_always_redacted_and_private_selections_refused(box):
    path = "agents/main/AGENTS.md"
    write(box, path, b"Contact a@example.com or b@example.com")
    view = preview(*box, [path])
    raw = export_zip(*box, view, selected=[path], profile="share")
    assert b"example.com" not in ingest_zip(raw).files[path]
    with pytest.raises(InvalidBundle, match="private contacts"):
        export_zip(*box, view, selected=[path], profile="share", included_contacts={path: [0]})


def test_contact_scan_is_bounded_on_large_plain_text_and_preserves_items():
    import subprocess
    import sys

    # A subprocess deadline keeps a regressed quadratic regex from wedging CI.
    subprocess.run([
        sys.executable, "-c",
        "from tinyassets.okf.transfer import CONTACT; "
        "assert CONTACT.search('a' * (8 * 1024 * 1024)) is None; "
        "assert [m.group() for m in CONTACT.finditer("
        "'Call +1 (212) 555-0123 or a@example.com')] == "
        "['+1 (212) 555-0123', 'a@example.com']",
    ], check=True, timeout=10)


def test_discovery_lists_only_eligible_files(box):
    from tinyassets.okf.transfer import discover

    for number, path in enumerate([
        "workspace/note.md", "agents/main/AGENTS.md", "workspace/.env",
        "wiki/soul.md", "wiki/raw/private.md", "wiki/drafts/idea.md",
        "workspace/wiki/daemon-wiki/private.md", "wiki/topics/topic.md",
    ]):
        write(box, path, b"content", str(number))
    assert [item["path"] for item in discover(*box)["files"]] == [
        "agents/main/AGENTS.md", "wiki/topics/topic.md", "workspace/note.md",
    ]
    with pytest.raises(InvalidBundle, match="bound"):
        discover(*box, limits=Limits(files=2))


def test_discovery_refuses_changed_tree(box, monkeypatch):
    from tinyassets.okf.transfer import discover

    provider, _ = box
    write(box, "workspace/note", b"first")
    original = provider.list
    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        write(box, "workspace/note", b"second", "during-list")
        return result
    monkeypatch.setattr(provider, "list", changing)
    with pytest.raises(WriteConflict):
        discover(*box)


@pytest.mark.parametrize("path", ["wiki/soul.md", "wiki/raw/a", "wiki/drafts/a",
                                  "workspace/wiki/daemon-wiki/a"])
def test_wiki_private_sources_cannot_be_selected_directly(box, path):
    with pytest.raises(InvalidBundle, match="cannot be exported"):
        preview(*box, [path])


def test_selected_wiki_uses_one_curated_writer_without_unselected_metadata(box):
    write(box, "wiki/pages/one.md", b"# One\nSee [[two]] and person@example.com")
    write(box, "wiki/pages/two.md", b"# Private unselected title", "second")
    view = preview(*box, ["wiki/pages/one.md"])
    bundle = ingest_zip(export_zip(*box, view, selected=["wiki/pages/one.md"]))
    assert "wiki/pages/one.md" not in bundle.files
    assert {"wiki/one.md", "wiki/index.md", "wiki/log.md"} <= bundle.files.keys()
    all_wiki = b"".join(data for path, data in bundle.files.items() if path.startswith("wiki/"))
    assert b"Private unselected title" not in all_wiki
    assert b"person@example.com" not in all_wiki
    assert b"[contact removed]" in bundle.files["wiki/one.md"]
    assert b"[[two]]" not in bundle.files["wiki/one.md"]
    assert b"type: note" in bundle.files["wiki/one.md"]


@pytest.mark.parametrize("other", ["wiki/one.md", "wiki/ONE.md", "wiki/index.md"])
def test_generated_wiki_does_not_overwrite_selected_content(box, other):
    write(box, "wiki/pages/one.md", b"# One")
    write(box, other, b"Another selected file", "second")
    view = preview(*box, ["wiki/pages/one.md", other])
    with pytest.raises(InvalidBundle, match="conflicts"):
        export_zip(*box, view, selected=list(view.files))


def test_selected_binary_with_markdown_suffix_is_not_parsed_as_wiki(box):
    path = "wiki/pages/image.md"
    write(box, path, b"\xff\x00binary")
    view = preview(*box, [path])
    result = ingest_zip(export_zip(*box, view, selected=[path], named_binaries=[path]))
    assert result.files[path] == b"\xff\x00binary"


def test_reserved_curated_source_is_not_silently_dropped(box):
    path = "wiki/pages/log.md"
    write(box, path, b"# My selected log")
    view = preview(*box, [path])
    with pytest.raises(InvalidBundle, match="reserved"):
        export_zip(*box, view, selected=[path])
