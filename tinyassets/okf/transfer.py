"""Shared private export manifest and inert, bounded import ingestion.

The HTTP owner door must retain Preview server-side and authorize the bound
owner before selection. This module never publishes, activates or executes a
bundle. Quarantine's root is supplied by platform-owned storage, never a box.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from tinyassets.boxes.provider import BoxHandle, BoxProvider, WriteConflict
from tinyassets.credential_shape import looks_like_credential
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.manifest import build_manifest

MANIFEST = "command-center.json"
LIMITATIONS = "Credential detection cannot prove a file is free of personal data."
CONTENT_ROOTS = frozenset({"agents", "workspace", "wiki", "brain", "workflows", "memory", "ui"})
NEVER = frozenset(
    {
        ".git",
        ".env",
        ".ssh",
        "credentials",
        ".credentials",
        "sessions",
        ".sessions",
        "session-logs",
        ".agent-sessions",
        "browser",
        "browser-state",
        "browser-profile",
    }
)
CONTACT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+?\d[\s().-]*){10,15}")


@dataclass(frozen=True)
class Limits:
    compressed: int = 8 * 1024 * 1024
    expanded: int = 32 * 1024 * 1024
    files: int = 2000
    depth: int = 16
    seconds: float = 10.0


def safe_path(path: str, *, depth=16):
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise InvalidBundle("Invalid bundle path")
    parts = path.split("/")
    if len(parts) > depth or any(
        not p
        or p in (".", "..")
        or p[-1:] in (".", " ")
        or ":" in p
        or "\\" in p
        or any(ord(c) < 32 for c in p)
        or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p)
        for p in parts
    ):
        raise InvalidBundle("Unsafe bundle path")
    return unicodedata.normalize("NFC", path).casefold()


def _scope(handle):
    return handle.command_center_id, handle.account_id, handle.epoch


def _never(path):
    return any(
        part.casefold() in NEVER or part.casefold().startswith(".env.") for part in path.split("/")
    )


def _empty_environment_example(path, data):
    return path == ".env.example" and bool(re.fullmatch(rb"(?:[A-Z][A-Z0-9_]*=\n)+", data))


def _agents(paths):
    return [
        path.split("/")[1]
        for path in sorted(paths)
        if re.fullmatch(r"agents/[^/]+/AGENTS\.md", path)
    ]


@dataclass(frozen=True)
class Preview:
    scope: tuple[str, str, int]
    generation: int
    files: Mapping[str, bytes]
    blocked: Mapping[str, str]
    binary: frozenset[str]
    limitations: str = LIMITATIONS
    contacts: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def preview(provider: BoxProvider, handle: BoxHandle, paths: list[str], *, limits=Limits()):
    """Capture arbitrary content for review; nothing is included automatically.

    Contacts are indexed within the captured bytes and redacted unless the
    owner selects that occurrence for a private export. Secrets stay blocked.
    """
    if len(paths) > limits.files or len(set(paths)) != len(paths):
        raise InvalidBundle("Too many or duplicate preview paths")
    normalized = set()
    for path in paths:
        key = safe_path(path, depth=limits.depth)
        if key in normalized:
            raise InvalidBundle("Case or Unicode path collision")
        normalized.add(key)
        if path.split("/")[0] not in CONTENT_ROOTS or _never(path):
            raise InvalidBundle("This path cannot be exported")
    view = provider.read_many(handle, ["/cc/" + path for path in paths], max_total=limits.expanded)
    if view.missing:
        raise InvalidBundle("Preview source file is missing")
    files, blocked, binary, contacts = {}, {}, set(), {}
    for path in paths:
        data = view.files["/cc/" + path]
        files[path] = data
        try:
            content = data.decode("utf-8")
            if "\x00" in content:
                binary.add(path)
        except UnicodeDecodeError:
            binary.add(path)
            content = data.decode("utf-8", errors="replace")
        if looks_like_credential(path) or looks_like_credential(content):
            blocked[path] = "detected credential"
        elif CONTACT.search(path):
            blocked[path] = "contact details in a file path"
        elif matches := tuple(match.group() for match in CONTACT.finditer(content)):
            if path in binary:
                blocked[path] = "contact details in an uninspectable binary"
            else:
                contacts[path] = matches
    return Preview(
        _scope(handle),
        view.generation,
        MappingProxyType(files),
        MappingProxyType(blocked),
        frozenset(binary),
        contacts=MappingProxyType(contacts),
    )


def export_zip(
    provider: BoxProvider,
    handle: BoxHandle,
    reviewed: Preview,
    *,
    selected: list[str],
    named_binaries: list[str] = (),
    included_contacts: Mapping[str, list[int]] | None = None,
    profile="export",
):
    """Export only approved, unchanged preview bytes. Never a publishing action."""
    if reviewed.scope != _scope(handle):
        raise InvalidBundle("Preview belongs to another owner, center or epoch")
    # An empty coherent read also detects unjournalled owner changes to the box.
    if provider.read_many(handle, [], max_total=0).generation != reviewed.generation:
        raise WriteConflict("Export source changed after preview; preview again")
    if profile not in {"share", "export"}:
        raise InvalidBundle("Unknown bundle profile")
    if len(set(selected)) != len(selected) or not set(selected) <= reviewed.files.keys():
        raise InvalidBundle("Select only named preview files once")
    if not set(named_binaries) <= set(selected):
        raise InvalidBundle("Binary approval must name a selected file")
    if any(path in reviewed.blocked for path in selected):
        raise InvalidBundle("Approval cannot override a blocked file")
    if not (set(selected) & reviewed.binary) <= set(named_binaries):
        raise InvalidBundle("An uninspectable binary requires named approval")
    if profile == "share" and any(p.split("/")[0] not in {"agents", "ui"} for p in selected):
        raise InvalidBundle("Share includes only the harness and layouts")
    included_contacts = {} if included_contacts is None else included_contacts
    if not isinstance(included_contacts, dict) or not set(included_contacts) <= set(selected):
        raise InvalidBundle("Contact approval must name a selected file")
    if profile == "share" and included_contacts:
        raise InvalidBundle("Share cannot include private contacts")
    for path, indices in included_contacts.items():
        if (
            not isinstance(indices, list)
            or any(type(i) is not int for i in indices)
            or len(set(indices)) != len(indices)
            or not set(indices) <= set(range(len(reviewed.contacts.get(path, ()))))
        ):
            raise InvalidBundle("Select only named contact occurrences once")
    files = {path: reviewed.files[path] for path in sorted(selected)}
    for path in set(files) & reviewed.contacts.keys():
        approved = set(included_contacts.get(path, ()))
        occurrence = iter(range(len(reviewed.contacts[path])))
        files[path] = CONTACT.sub(
            lambda match: match.group() if next(occurrence) in approved else "[contact removed]",
            files[path].decode("utf-8"),
        ).encode("utf-8")
    if profile == "export":
        from tinyassets.okf.export_assets import generated_files

        generated = generated_files()
        if set(files) & set(generated):
            raise InvalidBundle("Selected content conflicts with generated export files")
        files.update(generated)
    manifest = build_manifest(
        profile=profile, name="Command center", description="", files=files,
        agents=_agents(files), workflows=[], ui="", automations=[], model="", connections=[],
    )
    # Hashes and sizes are schema-generated integrity fields, not user content.
    files[MANIFEST] = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    if profile == "share" and sum(map(len, files.values())) > 256 * 1024:
        raise InvalidBundle("Share bundle exceeds the 256 KiB envelope")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            info = zipfile.ZipInfo(path)
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, data)
    return result.getvalue()


@dataclass(frozen=True)
class ValidatedBundle:
    files: Mapping[str, bytes]
    profile: str
    sha256: str


def ingest_zip(raw: bytes, *, limits=Limits()) -> ValidatedBundle:
    """Validate the entire archive before exposing any file to quarantine storage."""
    if len(raw) > limits.compressed:
        raise InvalidBundle("Compressed import exceeds its bound")
    deadline = time.monotonic() + limits.seconds
    files, seen, spellings, expanded = {}, {}, {}, 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > limits.files:
                raise InvalidBundle("Import file count exceeds its bound")
            for member in members:
                if time.monotonic() > deadline:
                    raise InvalidBundle("Import processing time exceeds its bound")
                if member.orig_filename != member.filename or member.external_attr & 0x400:
                    raise InvalidBundle("NUL paths and reparse points are not allowed")
                path = member.filename[:-1] if member.is_dir() else member.filename
                key = safe_path(path, depth=limits.depth)
                if key in seen:
                    raise InvalidBundle("Case or Unicode path collision")
                seen[key] = member.is_dir()
                parts = path.split("/")
                for i in range(1, len(parts) + 1):
                    spelling = "/".join(parts[:i])
                    normalized = unicodedata.normalize("NFC", spelling).casefold()
                    if spellings.setdefault(normalized, spelling) != spelling:
                        raise InvalidBundle("Case or Unicode directory collision")
                kind = stat.S_IFMT(member.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (
                    kind == stat.S_IFDIR and not member.is_dir()
                ):
                    raise InvalidBundle("Import allows only regular files and directories")
                if member.flag_bits & 1:
                    raise InvalidBundle("Encrypted import is not inspectable")
                if member.is_dir() or ".git" in key.split("/"):
                    continue
                if member.file_size > limits.expanded - expanded:
                    raise InvalidBundle("Expanded import exceeds its bound")
                data = bytearray()
                with archive.open(member) as stream:
                    while chunk := stream.read(min(64 * 1024, limits.expanded - expanded + 1)):
                        expanded += len(chunk)
                        if expanded > limits.expanded or time.monotonic() > deadline:
                            raise InvalidBundle(
                                "Expanded import or processing time exceeds its bound"
                            )
                        data.extend(chunk)
                files[path] = bytes(data)
        for path in seen:
            parts = path.split("/")
            if any(seen.get("/".join(parts[:i])) is False for i in range(1, len(parts))):
                raise InvalidBundle("File conflicts with a directory path")
        manifest = json.loads(files[MANIFEST], object_pairs_hook=_unique_object)
        if (
            manifest.get("format_version") != 1
            or manifest.get("profile") not in {"share", "export"}
            or set(manifest) != {
                "format_version", "profile", "name", "description", "agents", "files",
                "workflows", "ui", "automations", "needs",
            }
        ):
            raise InvalidBundle("Unsupported bundle manifest")
        listed = manifest["files"]
        if not isinstance(listed, list) or any(
            not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}
            or not isinstance(item["path"], str) or type(item["size"]) is not int
            for item in listed
        ):
            raise InvalidBundle("Invalid manifest file descriptors")
        expected = {item["path"]: item for item in listed}
        if len(expected) != len(listed) or set(expected) != set(files) - {MANIFEST}:
            raise InvalidBundle("Manifest must describe exactly the imported files")
        if "agents" in manifest and manifest["agents"] != _agents(expected):
            raise InvalidBundle("Manifest roster differs from included agent instructions")
        for path, descriptor in expected.items():
            if descriptor != {
                "path": path,
                "size": len(files[path]),
                "sha256": hashlib.sha256(files[path]).hexdigest(),
            }:
                raise InvalidBundle("Imported file differs from manifest")
            if _never(path) and not _empty_environment_example(path, files[path]):
                raise InvalidBundle("Bundle contains a never-exported path")
        if manifest["profile"] == "share" and (
            expanded > 256 * 1024 or any(p.split("/")[0] not in {"agents", "ui"} for p in expected)
        ):
            raise InvalidBundle("Share exceeds its content or size envelope")
        return ValidatedBundle(
            MappingProxyType(files), manifest["profile"], hashlib.sha256(raw).hexdigest()
        )
    except (
        zipfile.BadZipFile,
        KeyError,
        ValueError,
        UnicodeError,
        TypeError,
        AttributeError,
    ) as exc:
        if isinstance(exc, InvalidBundle):
            raise
        raise InvalidBundle("Malformed bundle archive or manifest") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidBundle("Duplicate manifest key")
        result[key] = value
    return result


def quarantine(bundle: ValidatedBundle, *, platform_root: Path) -> Path:
    """Copy validated bytes to fresh platform-owned storage; create no activation.

    The caller must resolve platform_root outside every agent-writable box.
    This function does not accept a caller-chosen destination or execute files.
    """
    root = Path(platform_root)
    if root.is_symlink() or not root.is_dir():
        raise InvalidBundle("Quarantine needs a platform-owned regular directory")
    destination = Path(tempfile.mkdtemp(prefix="import-", dir=root))
    try:
        for path, data in bundle.files.items():
            safe_path(path)
            target = destination / "payload" / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        (destination / "status.json").write_text(
            json.dumps(
                {
                    "state": "quarantined",
                    "archive_sha256": bundle.sha256,
                    "profile": bundle.profile,
                }
            ),
            encoding="utf-8",
        )
        return destination
    except BaseException:
        shutil.rmtree(destination)
        raise
