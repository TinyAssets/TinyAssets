"""Shared private export manifest and inert, bounded import ingestion.

The HTTP owner door must retain Preview server-side and authorize the bound
owner before selection. This module never publishes, activates or executes a
bundle. The owner door retains validated bytes in existing platform package custody.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from tinyassets.boxes.provider import BoxHandle, BoxProvider, WriteConflict
from tinyassets.command_center_packages import HARNESS_ROOT_DIRS, HARNESS_ROOT_FILES
from tinyassets.credential_shape import looks_like_credential
from tinyassets.okf.agent_export import (
    model_variables,
    portable_settings,
    roster,
    settings_bytes,
    settings_document,
    settings_path,
)
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.manifest import build_manifest

MANIFEST = "command-center.json"
LIMITATIONS = "Credential detection cannot prove a file is free of personal data."
CONTENT_ROOTS = frozenset({"agents", "workspace", "wiki", "brain", "workflows", "memory", "ui"})
# Reuse the shared-package root harness allowlist. Root memory requires an
# item-selection workflow; it is not silently included with the harness.
ROOT_HARNESS = HARNESS_ROOT_FILES - {"MEMORY.md"}
CONTENT_ROOTS = CONTENT_ROOTS | HARNESS_ROOT_DIRS
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
# Token boundaries prevent restarting an unbounded email search at every byte
# of a long prose token. Each candidate has finite email/phone component bounds.
CONTACT = re.compile(
    r"(?<![\w.+-])[\w.+-]{1,64}@[\w.-]{1,253}\.[A-Za-z]{2,63}(?![\w.-])"
    r"|(?<![\w+])\+?(?:\d[\s().-]{0,8}){9,14}\d(?!\w)"
)


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
    parts = [part.casefold() for part in path.split("/")]
    if "wiki" in parts:
        wiki = parts[parts.index("wiki") + 1:]
        from tinyassets.wiki.okf_export import private_source

        if private_source("/".join(wiki)):
            return True
    return any(
        part in NEVER or part.startswith(".env.") for part in parts
    )


def _empty_environment_example(path, data):
    return path == ".env.example" and bool(re.fullmatch(rb"(?:[A-Z][A-Z0-9_]*=\n)+", data))


def _agents(paths):
    return [
        path.split("/")[1]
        for path in sorted(paths)
        if re.fullmatch(r"agents/[^/]+/AGENTS\.md", path)
    ]


def discover(provider: BoxProvider, handle: BoxHandle, *, limits=Limits()):
    """List eligible regular files, refusing a partial or stale discovery."""
    generation = provider.read_many(handle, [], max_total=0).generation
    pending, found, visited = list(sorted(CONTENT_ROOTS | ROOT_HARNESS)), [], 0
    deadline = time.monotonic() + limits.seconds
    while pending:
        path = pending.pop()
        if _never(path):
            continue
        safe_path(path, depth=limits.depth)
        info = provider.stat(handle, "/cc/" + path)
        if info is None:
            continue
        visited += 1
        if visited > limits.files or time.monotonic() > deadline:
            raise InvalidBundle("File discovery exceeds its bound; use explicit paths")
        if info.kind == "file":
            found.append({"path": path, "size": info.size})
        elif info.kind == "dir":
            cursor, cursors = None, set()
            while True:
                page = provider.list(handle, "/cc/" + path, cursor=cursor)
                pending.extend(path + "/" + entry.name for entry in page.entries)
                if len(pending) + visited > limits.files or time.monotonic() > deadline:
                    raise InvalidBundle("File discovery exceeds its bound; use explicit paths")
                cursor = page.next_cursor
                if cursor is None:
                    break
                if cursor in cursors:
                    raise InvalidBundle("File discovery cursor did not advance")
                cursors.add(cursor)
    if provider.read_many(handle, [], max_total=0).generation != generation:
        raise WriteConflict("Files changed during discovery; list again")
    return {"files": sorted(found, key=lambda item: item["path"]), "generation": generation}


@dataclass(frozen=True)
class Preview:
    scope: tuple[str, str, int]
    generation: int
    files: Mapping[str, bytes]
    blocked: Mapping[str, str]
    binary: frozenset[str]
    limitations: str = LIMITATIONS
    contacts: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    rules: Mapping[str, bytes] = field(default_factory=dict)


def preview(provider: BoxProvider, handle: BoxHandle, paths: list[str], *, limits=Limits(),
            policy=None):
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
        if (path not in ROOT_HARNESS and path.split("/")[0] not in CONTENT_ROOTS) or _never(path):
            raise InvalidBundle("This path cannot be exported")
    from tinyassets.agent_definition import SettingsError, agent_directory, parse_settings
    from tinyassets.okf.policy_export import preview_agents

    identities, identity_paths = {}, {}
    if policy is not None:
        for agent in preview_agents(paths):
            try:
                directory = "" if agent == "main" else agent_directory(agent)
            except SettingsError as exc:
                raise InvalidBundle("Invalid canonical agent directory") from exc
            identity_paths[agent] = (directory + "/" if directory else "") + "settings.yaml"
    source_paths = set(paths) | set(identity_paths.values())
    view = provider.read_many(handle, ["/cc/" + path for path in sorted(source_paths)],
                              max_total=limits.expanded)
    if set(view.missing) & {"/cc/" + path for path in paths}:
        raise InvalidBundle("Preview source file is missing")
    for agent, path in identity_paths.items():
        try:
            settings = parse_settings(view.files.get("/cc/" + path))
        except SettingsError as exc:
            raise InvalidBundle("Cannot resolve the agent's canonical policy identity") from exc
        identities[agent] = "main" if agent == "main" else settings.agent.id or agent
    files, blocked, binary, contacts = {}, {}, set(), {}
    settings_numbers = {path: n for n, path in enumerate(
        sorted(p for p in paths if settings_path(p)), 1
    )}
    for path in paths:
        data = view.files["/cc/" + path]
        if path in settings_numbers:
            data = portable_settings(data, number=settings_numbers[path])
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
        elif matches := _contacts(path, content)[0]:
            if path in binary:
                blocked[path] = "contact details in an uninspectable binary"
            else:
                contacts[path] = matches
    rules = _policy_rules(policy, handle, identities)
    if (sum(map(len, files.values())) + sum(map(len, rules.values())) > limits.expanded
            or len(files) + len(rules) > limits.files):
        raise InvalidBundle("Files and rule projection exceed preview bounds")
    return Preview(
        _scope(handle),
        view.generation,
        MappingProxyType(files),
        MappingProxyType(blocked),
        frozenset(binary),
        contacts=MappingProxyType(contacts),
        rules=MappingProxyType(rules),
    )


def _policy_rules(policy, handle, identities):
    if policy is None:
        return {}
    from tinyassets.okf.policy_export import portable_rules

    return portable_rules(policy, owner=handle.account_id, center=handle.command_center_id,
                          agents=identities)


def _contacts(path, content, approved=None):
    """Settings contacts are string values, never numbers or field names."""
    matches = []

    def replace(match):
        index = len(matches)
        matches.append(match.group())
        return match.group() if approved is None or index in approved else "[contact removed]"

    def visit(value):
        if isinstance(value, str):
            return CONTACT.sub(replace, value)
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            if any(CONTACT.search(key) for key in value):
                raise InvalidBundle("Contact details cannot be settings field names")
            return {key: visit(child) for key, child in value.items()}
        return value

    if settings_path(path):
        content = settings_bytes(visit(settings_document(content.encode("utf-8")))).decode("utf-8")
    else:
        content = CONTACT.sub(replace, content)
    return tuple(matches), content


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
    if profile == "share" and any(
        p not in ROOT_HARNESS and p.split("/")[0] not in {"agents", "ui"} | HARNESS_ROOT_DIRS
        for p in selected
    ):
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
        files[path] = _contacts(path, files[path].decode("utf-8"), approved)[1].encode("utf-8")
    relocated, occupied = {}, set()
    for path, data in files.items():
        target = "agents/main/" + path if (
            path in ROOT_HARNESS or path.split("/")[0] in HARNESS_ROOT_DIRS
        ) else path
        key = safe_path(target)
        if key in occupied:
            raise InvalidBundle("Root harness conflicts with a selected agent file")
        relocated[target] = data
        occupied.add(key)
    files = relocated
    agents = _agents(files)
    for agent in agents:
        path = f"agents/{agent}/rules.json"
        if path in reviewed.rules:
            if safe_path(path) in occupied:
                raise InvalidBundle("Selected content conflicts with broker-owned agent rules")
            files[path] = reviewed.rules[path]
            occupied.add(safe_path(path))
    agent_roster = roster(files, agents)
    if profile == "export":
        from tinyassets.okf.export_assets import generated_files
        from tinyassets.wiki.okf_export import render_okf_files

        curated = {path: data for path, data in files.items()
                   if path.startswith("wiki/pages/") and path.endswith(".md")
                   and path not in reviewed.binary}
        if curated:
            converted, report = render_okf_files({p[5:]: b for p, b in curated.items()})
            if report["issues"]:
                raise InvalidBundle("A selected wiki page uses a reserved OKF filename")
            for path in curated:
                del files[path]
            occupied = {safe_path(p) for p in files}
            for path, data in converted.items():
                target = "wiki/" + path
                key = safe_path(target)
                if key in occupied:
                    raise InvalidBundle("Selected wiki content conflicts with the OKF bundle")
                occupied.add(key)
                files[target] = data

        generated = generated_files()
        generated[".env.example"] += "".join(
            f"{name}=\n" for name in model_variables(agent_roster)
        ).encode("ascii")
        if set(files) & set(generated):
            raise InvalidBundle("Selected content conflicts with generated export files")
        files.update(generated)
    manifest = build_manifest(
        profile=profile, name="Command center", description="", files=files,
        agents=agents, workflows=[], ui="", automations=[], model="", connections=[
            f"connection-{entry['model_env'][6:]}" for entry in agent_roster if entry["model_env"]
        ], roster=agent_roster,
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
            or set(manifest) - {"roster"} != {
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
        if "roster" in manifest and manifest["roster"] != roster(files, manifest["agents"]):
            raise InvalidBundle("Manifest roster differs from included settings")
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
