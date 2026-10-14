"""File-canonical OKF entries with one compare-and-swap commit point.

Entries staged by an interrupted transaction are unreachable from current.json and
are not knowledge. A commit replaces only current.json, atomically, under the box's
generation fence. The caller retains its original snapshot and operation id
when retrying an uncertain outcome; the BoxProvider journal owns idempotency.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import yaml

from tinyassets.boxes.provider import BoxBusy, BoxHandle, BoxProvider, WriteMode

ROOT = "/cc/brain/knowledge"
INDEX = ROOT + "/current.json"


class InvalidBundle(ValueError):
    pass


class _UniqueKeys(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise InvalidBundle("Frontmatter keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def document(metadata: dict, body: str) -> bytes:
    """JSON is a YAML subset; retain all unknown frontmatter fields verbatim in value."""
    return (
        "---\n"
        + json.dumps(metadata, ensure_ascii=False, allow_nan=False, indent=2)
        + "\n---\n"
        + body
    ).encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidBundle("JSON keys must be unique")
        result[key] = value
    return result


def parse(raw: bytes) -> tuple[dict, str]:
    text = raw.decode("utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise InvalidBundle("An OKF file needs YAML frontmatter")
    header, body = text[4:].split("\n---\n", 1)
    # YAML 1.1 treats JSON scientific notation (1e+20) as text. Preserve JSON's
    # numeric semantics for our own output before accepting owner-authored YAML.
    try:
        metadata = json.loads(header, object_pairs_hook=_unique_pairs)
    except json.JSONDecodeError:
        metadata = yaml.load(header, Loader=_UniqueKeys)
    if not isinstance(metadata, dict):
        raise InvalidBundle("Frontmatter must be an object")
    # Reject nonportable YAML values and NaN instead of silently changing them.
    json.dumps(metadata, allow_nan=False)
    return metadata, body


@dataclass(frozen=True)
class Entry:
    id: str
    type: str
    body: str
    metadata: dict[str, Any]

    def encode(self):
        if (
            not isinstance(self.id, str)
            or not self.id
            or not isinstance(self.type, str)
            or not self.type
            or not isinstance(self.body, str)
        ):
            raise InvalidBundle("Entries need a nonempty id/type and a text body")
        if set(self.metadata) & {"id", "type"}:
            raise InvalidBundle("id and type are dedicated entry fields")
        return document({**self.metadata, "id": self.id, "type": self.type}, self.body)

    @classmethod
    def decode(cls, raw):
        metadata, body = parse(raw)
        entry_id, entry_type = metadata.pop("id", None), metadata.pop("type", None)
        if not isinstance(entry_id, str) or not entry_id or not isinstance(entry_type, str):
            raise InvalidBundle("Entry id and type must be nonempty strings")
        entry = cls(entry_id, entry_type, body, metadata)
        entry.encode()
        return entry


@dataclass(frozen=True)
class BundleSnapshot:
    scope: tuple[str, str, int]
    generation: int
    manifest: dict
    entries: dict[str, Entry]
    paths: dict[str, str]
    log: str


def _paths(manifest):
    if (
        not isinstance(manifest, dict)
        or manifest.get("okf_version") != "0.1"
        or manifest.get("format") != "knowledge-v1"
    ):
        raise InvalidBundle("Unsupported knowledge bundle format")
    paths = manifest.get("entries")
    if not isinstance(paths, dict):
        raise InvalidBundle("Bundle entries must map ids to source files")
    revision = manifest.get("revision")
    if (
        not isinstance(revision, str)
        or len(revision) != 64
        or any(c not in "0123456789abcdef" for c in revision)
    ):
        raise InvalidBundle("Invalid knowledge revision")
    for key, path in paths.items():
        name = hashlib.sha256(key.encode()).hexdigest() if isinstance(key, str) else ""
        if not key or path != f"revisions/{revision}/entries/{name}.md":
            raise InvalidBundle("Invalid knowledge entry reference")
    if len(set(paths.values())) != len(paths):
        raise InvalidBundle("Two ids cannot share one source file")
    return paths


class KnowledgeBundle:
    def __init__(self, provider: BoxProvider, handle: BoxHandle, *, read_bytes=64 * 1024 * 1024):
        self.provider, self.handle, self.read_bytes = provider, handle, read_bytes

    @property
    def scope(self):
        return (self.handle.command_center_id, self.handle.account_id, self.handle.epoch)

    def snapshot(self) -> BundleSnapshot:
        for _ in range(3):
            first = self.provider.read_many(self.handle, [INDEX], max_total=self.read_bytes)
            if INDEX in first.missing:
                return BundleSnapshot(
                    self.scope,
                    first.generation,
                    {"okf_version": "0.1", "format": "knowledge-v1", "entries": {}},
                    {},
                    {},
                    "# Log\n",
                )
            metadata = json.loads(first.files[INDEX], object_pairs_hook=_unique_pairs)
            paths = _paths(metadata)
            revision_root = ROOT + "/revisions/" + metadata["revision"]
            # One coherent view includes the commit point and all source files.
            view = self.provider.read_many(
                self.handle,
                [
                    INDEX,
                    revision_root + "/index.md",
                    revision_root + "/log.md",
                    *[ROOT + "/" + p for p in paths.values()],
                ],
                max_total=self.read_bytes,
            )
            if view.files.get(INDEX) != first.files[INDEX]:
                continue
            if view.missing:
                raise InvalidBundle("A committed knowledge source file is missing")
            header, _ = parse(view.files[revision_root + "/index.md"])
            if header != {"okf_version": "0.1"}:
                raise InvalidBundle("OKF index frontmatter must contain only okf_version")
            entries = {
                key: Entry.decode(view.files[ROOT + "/" + path]) for key, path in paths.items()
            }
            if any(entry.id != key for key, entry in entries.items()):
                raise InvalidBundle("Entry id differs from its manifest reference")
            return BundleSnapshot(
                self.scope,
                view.generation,
                metadata,
                entries,
                paths,
                view.files[revision_root + "/log.md"].decode("utf-8"),
            )
        raise BoxBusy("Knowledge commit changed during three coherent reads")

    def commit(
        self, before: BundleSnapshot, changes: dict[str, Entry | None], *, operation_id: str
    ):
        """Stage a conformant revision, then atomically switch the commit pointer.

        CAS fences every write, including staging. Concurrent owner edits or a
        different writer cause refusal before they can be overwritten. Retries
        MUST pass the original before/changes/operation_id, never a fresh snapshot.
        None removes an entry from the new revision; history is not purged.
        """
        if before.scope != self.scope:
            raise InvalidBundle("A snapshot belongs to one owner, command center and epoch")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("A stable operation_id is required")
        final = dict(before.entries)
        for key, entry in changes.items():
            if not isinstance(key, str) or not key or (entry is not None and entry.id != key):
                raise InvalidBundle("Changed entry id must match its key")
            if entry is None:
                final.pop(key, None)
            else:
                final[key] = entry
        transaction = hashlib.sha256(operation_id.encode()).hexdigest()
        revision = f"revisions/{transaction}"
        paths = {
            key: f"{revision}/entries/{hashlib.sha256(key.encode()).hexdigest()}.md"
            for key in sorted(final)
        }
        files = {paths[key]: final[key].encode() for key in sorted(final)}
        body = "# Knowledge\n\n" + "".join(
            f"- [Entry {i + 1}](entries/{path.rsplit('/', 1)[1]})\n"
            for i, path in enumerate(paths.values())
        )
        files[revision + "/index.md"] = document({"okf_version": "0.1"}, body)
        # Stable operation hash is safe display text and deterministic on retry.
        log = before.log + f"\n## Commit {transaction}\n\n"
        log += f"{len(changes)} changes; {len(final)} current entries.\n"
        files[revision + "/log.md"] = log.encode("utf-8")
        manifest = {
            **before.manifest,
            "entries": paths,
            "operation_id": operation_id,
            "revision": transaction,
        }
        _paths(manifest)
        data = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) + sum(map(len, files.values())) > self.read_bytes:
            raise InvalidBundle("Knowledge revision exceeds the coherent read bound")
        generation = before.generation
        for path, content in files.items():
            name = hashlib.sha256(path.encode()).hexdigest()
            receipt = self.provider.write(
                self.handle,
                f"okf:{transaction}:{name}",
                ROOT + "/" + path,
                content,
                max_bytes=len(content),
                mode=WriteMode.CAS,
                expect_generation=generation,
            )
            generation = receipt.generation
        return self.provider.write(
            self.handle,
            f"okf:{transaction}:commit",
            INDEX,
            data,
            max_bytes=len(data),
            mode=WriteMode.CAS,
            expect_generation=generation,
        )

    def search(self, query: str, *, limit=20):
        """A disposable lexical index over the current source snapshot, never a second store."""
        if not 1 <= limit <= 1000:
            raise ValueError("Search result limit must be between 1 and 1000")
        snapshot = self.snapshot()
        words = query.casefold().split()
        scored = [
            (sum(entry.body.casefold().count(word) for word in words), key, entry)
            for key, entry in snapshot.entries.items()
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [entry for score, _, entry in scored if score or not words][:limit]
