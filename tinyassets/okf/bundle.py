"""File-canonical OKF entries with one compare-and-swap commit point.

Entries staged by an interrupted transaction are unreachable from current.json and
are not knowledge. A commit replaces only current.json, atomically, under the box's
generation fence. The caller retains its original snapshot and operation id
when retrying an uncertain outcome; the BoxProvider journal owns idempotency.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

from tinyassets.boxes.provider import (
    BoxBusy,
    BoxHandle,
    BoxProvider,
    Snapshot,
    WriteConflict,
    WriteMode,
)

ROOT = "/cc/brain/knowledge"
INDEX = ROOT + "/current.json"
# Stay below C6's 4-MiB data envelope, including framing overhead. This is
# the OKF page/write bound; the full revision may span many such messages.
PAGE_BYTES = 2 * 1024 * 1024
PAGE_FILES = 1000
# JSON escapes non-ASCII path names. Reserve a separate metadata budget so
# paths plus base64 data and per-file framing fit C6's 8-MiB message envelope.
PAGE_PATH_BYTES = 2 * 1024 * 1024
# History is immutable, content-addressed pages; only its head is in current.json.
HISTORY_PAGE_BYTES = 64 * 1024


class InvalidBundle(ValueError):
    pass


def capture_files(provider, handle, paths, *, max_total):
    """One generation across bounded messages; never an oversized RPC request."""
    generation = provider.read_many(handle, [], max_total=0).generation
    files, missing, pending = {}, [], []
    pending_bytes = pending_path_bytes = used = 0

    def check(actual):
        if actual != generation:
            raise WriteConflict("Source changed during paged capture; preview again")

    def flush():
        nonlocal used, pending_bytes, pending_path_bytes
        if not pending:
            return
        view = provider.read_many(handle, pending, max_total=min(PAGE_BYTES, max_total - used))
        check(view.generation)
        if set(view.files) & set(view.missing) or set(view.files) | set(view.missing) != set(
            pending
        ):
            raise InvalidBundle("Provider snapshot differs from requested paths")
        size = sum(map(len, view.files.values()))
        if size > min(PAGE_BYTES, max_total - used):
            raise InvalidBundle("Provider snapshot exceeds the capture bound")
        used += size
        files.update(view.files)
        missing.extend(view.missing)
        pending.clear()
        pending_bytes = 0
        pending_path_bytes = 0

    for path in paths:
        path_bytes = len(json.dumps(path, ensure_ascii=True)) + 32
        if path_bytes > PAGE_PATH_BYTES:
            raise InvalidBundle("Source path exceeds the transport envelope")
        info = provider.stat(handle, path)
        size = info.size if info is not None else 0
        if size + pending_bytes > max_total - used:
            raise InvalidBundle("Source files exceed the capture bound")
        if size > PAGE_BYTES:
            flush()
            if size > max_total - used:
                raise InvalidBundle("Source files exceed the capture bound")
            chunks, offset = [], 0
            while offset < size:
                bound = min(PAGE_BYTES, size - offset)
                part = provider.read(handle, path, offset=offset, max_bytes=bound)
                check(part.generation)
                if part.size != size:
                    raise WriteConflict("Source file size changed during paged capture")
                if not part.data or len(part.data) > bound:
                    raise InvalidBundle("Invalid bounded provider file page")
                chunks.append(part.data)
                offset += len(part.data)
            files[path] = b"".join(chunks)
            used += size
        else:
            if pending and (
                pending_bytes + size > PAGE_BYTES
                or len(pending) >= PAGE_FILES
                or pending_path_bytes + path_bytes > PAGE_PATH_BYTES
            ):
                flush()
            pending.append(path)
            pending_bytes += size
            pending_path_bytes += path_bytes
    flush()
    check(provider.read_many(handle, [], max_total=0).generation)
    return Snapshot(files, tuple(missing), generation)


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
    try:
        text = raw.decode("utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise InvalidBundle("An OKF file needs YAML frontmatter")
        header, body = text[4:].split("\n---\n", 1)
        # YAML 1.1 treats JSON scientific notation (1e+20) as text. Preserve
        # JSON's exact numeric semantics before accepting owner-authored YAML.
        try:
            metadata = json.loads(header, object_pairs_hook=_unique_pairs)
        except json.JSONDecodeError:
            if len(header.encode("utf-8")) > 1024 * 1024:
                raise InvalidBundle("Owner-authored YAML frontmatter exceeds 1 MiB") from None
            depth = 0
            # Scan before construction: aliases can otherwise turn a small file
            # into exponential work in the later JSON portability check.
            for event in yaml.parse(header, Loader=yaml.SafeLoader):
                if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                    raise InvalidBundle("Frontmatter anchors and aliases are refused")
                if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                    depth += 1
                    if depth > 64:
                        raise InvalidBundle("Frontmatter is nested too deeply")
                elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                    depth -= 1
            metadata = yaml.load(header, Loader=_UniqueKeys)
        if not isinstance(metadata, dict):
            raise InvalidBundle("Frontmatter must be an object")
        # Reject nonportable YAML values and NaN instead of changing them.
        json.dumps(metadata, allow_nan=False)
        return metadata, body
    except InvalidBundle:
        raise
    except (UnicodeError, yaml.YAMLError, RecursionError, TypeError, ValueError):
        raise InvalidBundle("Frontmatter must contain portable UTF-8 JSON/YAML values") from None


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
    sequences = manifest.get("deletion_sequences", {})
    if not isinstance(sequences, dict) or any(
        not isinstance(key, str) or type(value) is not int or not 0 <= value < 2**63
        for key, value in sequences.items()
    ):
        raise InvalidBundle("Invalid canonical deletion sequences")
    for kind in ("log", "tombstones"):
        head = manifest.get(kind + "_history")
        if head is not None and (
            not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{64}", head)
        ):
            raise InvalidBundle("Invalid canonical history reference")
    tombstones = manifest.get("tombstones", {})
    if (
        not isinstance(tombstones, dict)
        or set(tombstones) & set(paths)
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, dict)
            or not isinstance(value.get("metadata"), dict)
            or not isinstance(value.get("operation"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["operation"])
            for key, value in tombstones.items()
        )
    ):
        raise InvalidBundle("Invalid canonical deletion tombstones")
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
        self.access_check = None  # Optional trusted session lifetime guard.
        self._history_cache = {}  # Content hashes are immutable within this bound session.

    @property
    def scope(self):
        return (self.handle.command_center_id, self.handle.account_id, self.handle.epoch)

    def _history(self, before, kind):
        if self.access_check is not None:
            self.access_check()
        digest = before.manifest.get(kind + "_history")
        while digest is not None:
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise InvalidBundle("Invalid canonical history reference")
            page = self._history_cache.get(digest)
            if page is None:
                path = ROOT + "/history/" + digest + ".json"
                # The digest, not the live box generation, authenticates immutable
                # history. An unrelated concurrent write must not break this read.
                view = self.provider.read(self.handle, path, max_bytes=PAGE_BYTES)
                raw = view.data
                if view.size != len(raw) or hashlib.sha256(raw).hexdigest() != digest:
                    raise InvalidBundle("Canonical history is missing or changed")
                page = json.loads(raw, object_pairs_hook=_unique_pairs)
            if (
                not isinstance(page, dict)
                or set(page) != {"previous", "values", "kind"}
                or page["kind"] != kind
            ):
                raise InvalidBundle("Invalid canonical history page")
            if not isinstance(page["values"], str if kind == "log" else dict):
                raise InvalidBundle("Invalid canonical history values")
            self._history_cache[digest] = page
            yield page["values"]
            digest = page["previous"]

    def operation_committed(self, before, operation_id):
        marker = "## Commit " + hashlib.sha256(operation_id.encode()).hexdigest() + "\n"
        return marker in before.log or any(marker in log for log in self._history(before, "log"))

    def forgotten(self, before, keys):
        pending = set(keys)
        if not pending:
            return False
        if pending & before.manifest.get("tombstones", {}).keys():
            return True
        return any(pending & page.keys() for page in self._history(before, "tombstones"))

    @staticmethod
    def _archive(manifest, files, kind, values):
        raw = json.dumps(
            {"kind": kind, "previous": manifest.get(kind + "_history"), "values": values},
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        digest = hashlib.sha256(raw).hexdigest()
        files["history/" + digest + ".json"] = raw
        manifest[kind + "_history"] = digest

    @staticmethod
    def _remember_sequence(sequences, metadata):
        identity = metadata.get("identity")
        if (
            isinstance(identity, list)
            and len(identity) == 1
            and isinstance(identity[0], list)
            and len(identity[0]) == 2
            and identity[0][0] == "integer"
        ):
            try:
                value = int(identity[0][1])
            except (TypeError, ValueError):
                raise InvalidBundle("Invalid deleted integer identity") from None
            if str(value) != identity[0][1] or not -(2**63) <= value < 2**63:
                raise InvalidBundle("Invalid deleted integer identity")
            key = json.dumps([metadata.get("source"), metadata.get("table")])
            sequences[key] = max(sequences.get(key, 0), value)

    def snapshot(self) -> BundleSnapshot:
        if self.access_check is not None:
            self.access_check()
        for _ in range(3):
            first = capture_files(self.provider, self.handle, [INDEX], max_total=self.read_bytes)
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
            view = capture_files(
                self.provider,
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
        self,
        before: BundleSnapshot,
        changes: dict[str, Entry | None],
        *,
        operation_id: str,
        transient_deletions=frozenset(),
    ):
        """Stage a conformant revision, then atomically switch the commit pointer.

        CAS fences every write, including staging. Concurrent owner edits or a
        different writer cause refusal before they can be overwritten. Retries
        MUST pass the original before/changes/operation_id, never a fresh snapshot.
        None removes an entry from the new revision; history is not purged.
        """
        if self.access_check is not None:
            self.access_check()
        if before.scope != self.scope:
            raise InvalidBundle("A snapshot belongs to one owner, command center and epoch")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("A stable operation_id is required")
        transaction = hashlib.sha256(operation_id.encode()).hexdigest()
        if self.operation_committed(before, operation_id):
            raise InvalidBundle("Operation already committed; read its canonical result")
        if any(key not in changes or changes[key] is not None for key in transient_deletions):
            raise InvalidBundle("Transient deletion must name an actual removed record")
        if self.forgotten(before, [key for key, entry in changes.items() if entry is not None]):
            raise InvalidBundle("Cannot revive a forgotten canonical identity")
        tombstones = dict(before.manifest.get("tombstones", {}))
        sequences = dict(before.manifest.get("deletion_sequences", {}))
        for marker in tombstones.values():
            self._remember_sequence(sequences, marker["metadata"])
        final = dict(before.entries)
        for key, entry in changes.items():
            if not isinstance(key, str) or not key or (entry is not None and entry.id != key):
                raise InvalidBundle("Changed entry id must match its key")
            if entry is None:
                prior = final.pop(key, None)
                if prior is not None and key not in transient_deletions:
                    metadata = {
                        name: prior.metadata[name]
                        for name in ("source", "table", "identity")
                        if name in prior.metadata
                    }
                    tombstones[key] = {
                        "operation": transaction,
                        "type": prior.type,
                        "metadata": metadata,
                    }
                    self._remember_sequence(sequences, metadata)
            else:
                if key in tombstones:
                    raise InvalidBundle("Cannot revive a forgotten canonical identity")
                final[key] = entry
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
        manifest = dict(before.manifest)
        log = before.log
        if len(log.encode()) > HISTORY_PAGE_BYTES:
            self._archive(manifest, files, "log", log)
            log = "# Log\n"
        log += f"\n## Commit {transaction}\n\n"
        log += f"{len(changes)} changes; {len(final)} current entries.\n"
        files[revision + "/log.md"] = log.encode("utf-8")
        if len(json.dumps(tombstones).encode()) > HISTORY_PAGE_BYTES:
            page, size = {}, 0
            for key, marker in tombstones.items():
                item_size = len(json.dumps({key: marker}, ensure_ascii=False).encode())
                if page and size + item_size > HISTORY_PAGE_BYTES:
                    self._archive(manifest, files, "tombstones", page)
                    page, size = {}, 0
                page[key] = marker
                size += item_size
            if page:
                self._archive(manifest, files, "tombstones", page)
            tombstones = {}
        manifest.update(
            {
                "entries": paths,
                "tombstones": tombstones,
                "deletion_sequences": sequences,
                "operation_id": operation_id,
                "revision": transaction,
            }
        )
        _paths(manifest)
        data = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) + sum(map(len, files.values())) > self.read_bytes:
            raise InvalidBundle("Knowledge revision exceeds the coherent read bound")
        if any(len(raw) > PAGE_BYTES for raw in [data, *files.values()]):
            raise InvalidBundle("A canonical knowledge file exceeds the bounded write envelope")
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
