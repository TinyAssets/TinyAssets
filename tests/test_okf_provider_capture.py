"""C12 reads stay within C6's transport envelope without mixing generations."""
from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from tests.test_okf_transfer import box, write  # noqa: F401
from tinyassets.boxes.provider import WriteConflict
from tinyassets.okf.bundle import PAGE_BYTES, Entry, InvalidBundle, KnowledgeBundle, capture_files


class BoundedProvider:
    """Real local storage behind the production transport's request limits."""
    def __init__(self, provider):
        self.provider = provider
        self.pages = []

    def read_many(self, handle, paths, *, max_total):
        assert max_total <= 4 * 1024 * 1024 and len(paths) <= 1000
        assert len(json.dumps({"paths": paths, "max_total": max_total}).encode()) < 8 * 1024 * 1024
        self.pages.append(("many", len(paths), max_total))
        result = self.provider.read_many(handle, paths, max_total=max_total)
        wire = {"files": {p: {"$bytes": base64.b64encode(b).decode("ascii")}
                          for p, b in result.files.items()}, "missing": result.missing,
                "generation": result.generation}
        assert len(json.dumps(wire).encode()) < 8 * 1024 * 1024
        return result

    def read(self, handle, path, *, offset, max_bytes):
        assert max_bytes <= 4 * 1024 * 1024
        self.pages.append(("slice", offset, max_bytes))
        return self.provider.read(handle, path, offset=offset, max_bytes=max_bytes)

    def __getattr__(self, name):
        return getattr(self.provider, name)


def test_large_snapshot_and_one_large_file_cross_bounded_transport(box):  # noqa: F811
    provider, handle = box
    sources = {"workspace/large": b"L" * (5 * 1024 * 1024),
               "workspace/one": b"A" * (1024 * 1024),
               "workspace/two": b"B" * (1024 * 1024)}
    for n, (path, data) in enumerate(sources.items()):
        write(box, path, data, f"seed-{n}")
    bounded = BoundedProvider(provider)
    view = capture_files(bounded, handle, ["/cc/" + path for path in sources],
                         max_total=32 * 1024 * 1024)
    assert view.files == {"/cc/" + p: data for p, data in sources.items()}
    assert len([page for page in bounded.pages if page[0] == "slice"]) == 3
    assert view.generation == provider.read_many(handle, [], max_total=0).generation


def test_many_missing_paths_still_use_bounded_request_count(box):  # noqa: F811
    provider, handle = box
    bounded = BoundedProvider(provider)
    paths = [f"/cc/workspace/absent-{i}" for i in range(1001)]
    view = capture_files(bounded, handle, paths, max_total=32 * 1024 * 1024)
    assert view.files == {} and set(view.missing) == set(paths)
    assert [page[1] for page in bounded.pages if page[1]] == [1000, 1]


def test_long_unicode_paths_fit_request_and_response_envelopes(box):  # noqa: F811
    from tinyassets.okf.transfer import safe_path

    provider, handle = box
    prefix = "workspace/" + ("😀" * 62 + "/") * 14
    paths = [prefix + f"file-{i}" for i in range(1000)]
    for path in paths:
        safe_path(path)
    # This legal set exceeds 8 MiB after JSON's default Unicode escaping,
    # even though its files are missing and contribute no data bytes.
    paths = ["/cc/" + path for path in paths]
    assert len(json.dumps({"paths": paths}).encode()) > 8 * 1024 * 1024
    bounded = BoundedProvider(provider)
    view = capture_files(bounded, handle, paths, max_total=32 * 1024 * 1024)
    assert set(view.missing) == set(paths)
    assert len([p for p in bounded.pages if p[1]]) > 1


def test_generation_change_between_pages_refuses_mixed_snapshot(box, monkeypatch):  # noqa: F811
    provider, handle = box
    for n in range(3):
        write(box, f"workspace/{n}", b"A" * (1024 * 1024), f"seed-{n}")
    read_many = provider.read_many
    changed = False
    def read_then_change(handle, paths, **kwargs):
        nonlocal changed
        result = read_many(handle, paths, **kwargs)
        if paths and not changed:
            changed = True
            provider.write(handle, "concurrent-owner", "/cc/workspace/2", b"new", max_bytes=3)
        return result
    monkeypatch.setattr(provider, "read_many", read_then_change)
    with pytest.raises(WriteConflict):
        capture_files(provider, handle, [f"/cc/workspace/{n}" for n in range(3)],
                      max_total=32 * 1024 * 1024)


def test_generation_change_during_large_file_refuses_mixed_bytes(box, monkeypatch):  # noqa: F811
    provider, handle = box
    data = b"A" * (PAGE_BYTES + 1)
    write(box, "workspace/large", data)
    read = provider.read
    def read_then_change(handle, path, **kwargs):
        result = read(handle, path, **kwargs)
        if kwargs["offset"] == 0:
            provider.write(handle, "replace", path, b"B" * len(data), max_bytes=len(data))
        return result
    monkeypatch.setattr(provider, "read", read_then_change)
    with pytest.raises(WriteConflict):
        capture_files(provider, handle, ["/cc/workspace/large"], max_total=2 * PAGE_BYTES)


def test_total_capture_bound_applies_across_pages(box):  # noqa: F811
    provider, handle = box
    for n in range(2):
        write(box, f"workspace/{n}", b"A" * (PAGE_BYTES - 100), f"seed-{n}")
    with pytest.raises(InvalidBundle, match="bound"):
        capture_files(provider, handle, ["/cc/workspace/0", "/cc/workspace/1"],
                      max_total=PAGE_BYTES)


def test_knowledge_bundle_uses_bounded_messages_and_checks_write_before_staging(box):  # noqa: F811
    provider, handle = box
    bounded = BoundedProvider(provider)
    bundle = KnowledgeBundle(bounded, handle)
    before = bundle.snapshot()
    records = {str(i): Entry(str(i), "note", "A" * (1024 * 1024), {}) for i in range(3)}
    bundle.commit(before, records, operation_id="multiple-pages")
    assert bundle.snapshot().entries == records
    before = bundle.snapshot()
    with pytest.raises(InvalidBundle, match="write envelope"):
        bundle.commit(before, {"large": Entry("large", "note", "A" * PAGE_BYTES, {})},
                      operation_id="too-large")
    assert bundle.snapshot() == before


def test_invalid_file_page_is_not_silently_shortened(box, monkeypatch):  # noqa: F811
    provider, handle = box
    write(box, "workspace/large", b"A" * (PAGE_BYTES + 1))
    read = provider.read
    monkeypatch.setattr(provider, "read", lambda *a, **k: replace(read(*a, **k), data=b""))
    with pytest.raises(InvalidBundle, match="page"):
        capture_files(provider, handle, ["/cc/workspace/large"], max_total=2 * PAGE_BYTES)
