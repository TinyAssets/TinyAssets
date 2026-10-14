"""The curated wiki writer inside the protected command-center export.

Input is already captured, reviewed and scrubbed by the owner transfer door.
This module performs no filesystem I/O and supplies no second export route.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath as Path
from typing import Any, Mapping

from tinyassets.api.wiki import _parse_frontmatter

OKF_VERSION = "0.1"
_RESERVED_FILENAMES = frozenset({"index.md", "log.md"})
_EXCLUDED_ROOTS = frozenset({"drafts", "raw", "daemon-wiki"})
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_SAFE_PLAIN_SCALAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _./:@+\-]*$")


def private_source(path: str) -> bool:
    """Apply the same wiki exclusions even when wiki is nested in workspace."""
    parts = path.casefold().split("/")
    return "soul.md" in parts or bool(set(parts) & _EXCLUDED_ROOTS)


def render_okf_files(sources: Mapping[str, bytes], *, exported_at: str | None = None):
    """Convert selected wiki-relative pages into one in-memory OKF bundle.

    Unselected sources cannot contribute content, titles, links or metadata.
    Non-curated files are the transfer caller's separately reviewed content.
    """
    exported_at = exported_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    excluded, reserved, eligible = [], [], []
    for path in sorted(sources):
        if private_source(path):
            excluded.append({"path": path, "reason": "private wiki source"})
        elif path.startswith("pages/") and path.endswith(".md"):
            if Path(path).name.casefold() in _RESERVED_FILENAMES:
                reserved.append(path)
            else:
                eligible.append(path)
    link_index = _build_link_index(eligible)
    concepts, unresolved, files = [], [], {}
    for source in eligible:
        relative = source.removeprefix("pages/")
        meta, body = _parse_frontmatter(sources[source].decode("utf-8"))
        concept = _concept_frontmatter(meta, body, source, relative, exported_at)
        body = _convert_wikilinks(body, source=relative, link_index=link_index,
                                 unresolved_links=unresolved)
        files[relative] = _render_markdown(concept, body).encode("utf-8")
        concepts.append({"id": relative.removesuffix(".md"), "path": relative,
                         "title": str(concept["title"]),
                         "description": str(concept.get("description", "")),
                         "timestamp": str(concept["timestamp"])})
    files["index.md"] = _render_index(concepts).encode("utf-8")
    files["log.md"] = _render_log(concepts).encode("utf-8")
    valid_index, index_meta = _validate_index("index.md", files["index.md"].decode())
    valid_log = _validate_log(files["log.md"].decode())
    return files, {
        "okf_version": OKF_VERSION, "conformant": valid_index and valid_log,
        "counts": {"concepts_exported": len(concepts),
                   "excluded_by_privacy": len(excluded), "unresolved_links": len(unresolved)},
        "concepts": concepts, "excluded_files": excluded, "unresolved_links": unresolved,
        "reserved_files": {"index.md": {"valid": valid_index, **index_meta},
                           "log.md": {"valid": valid_log}},
        "issues": [f"{path}: source reserved filename was not exported as a concept"
                   for path in reserved],
    }


def _build_link_index(sources: list[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for source in sources:
        bundle_rel = source.removeprefix("pages/")
        concept_id = bundle_rel.removesuffix(".md")
        keys = {
            Path(source).stem,
            concept_id,
            f"pages/{concept_id}",
            bundle_rel,
            f"pages/{bundle_rel}",
        }
        for key in keys:
            normalized = _normalize_wikilink_target(key)
            if normalized and normalized not in index:
                index[normalized] = bundle_rel
    return index


def _concept_frontmatter(
    meta: dict[str, str],
    body: str,
    source_rel: str,
    bundle_rel: str,
    exported_at: str,
) -> dict[str, Any]:
    concept_type = _first_nonempty(meta.get("type", ""), meta.get("kind", ""), "note")
    title = _first_nonempty(meta.get("title", ""), _first_h1(body), _title_from_slug(bundle_rel))
    timestamp = _first_nonempty(meta.get("updated", ""), exported_at)

    output: dict[str, Any] = {
        "type": concept_type,
        "title": title,
        "timestamp": timestamp,
        "workflow_original_path": source_rel,
    }
    for optional_key in ("description", "resource"):
        value = meta.get(optional_key, "").strip()
        if value:
            output[optional_key] = value
    tags = _parse_list_value(meta.get("tags", ""))
    if tags:
        output["tags"] = tags

    category = Path(bundle_rel).parts[0] if len(Path(bundle_rel).parts) > 1 else ""
    if category:
        output.setdefault("workflow_category", category)
    for key, value in meta.items():
        normalized_key = _workflow_key(key)
        if normalized_key and value.strip():
            output[f"workflow_{normalized_key}"] = value.strip()
    return output


def _convert_wikilinks(
    body: str,
    *,
    source: str,
    link_index: dict[str, str],
    unresolved_links: list[dict[str, str]],
) -> str:
    def replace(match: re.Match[str]) -> str:
        raw_target = match.group(1).strip()
        target, separator, alias = raw_target.partition("|")
        target = target.strip()
        label = alias.strip() if separator else target
        resolved = link_index.get(_normalize_wikilink_target(target))
        if not resolved:
            unresolved_links.append({"source": source, "target": target})
            return label
        return f"[{label}](/{resolved})"

    return _WIKILINK_RE.sub(replace, body)


def _render_index(concepts: list[dict[str, str]]) -> str:
    lines = ["---", f'okf_version: "{OKF_VERSION}"', "---", "", "# Index", ""]
    for concept in sorted(concepts, key=lambda item: item["path"]):
        description = concept.get("description", "")
        suffix = f" - {description}" if description else ""
        lines.append(f"- [{concept['title']}]({concept['path']}){suffix}")
    lines.append("")
    return "\n".join(lines)


def _render_log(concepts: list[dict[str, str]]) -> str:
    by_date: dict[str, list[dict[str, str]]] = {}
    for concept in concepts:
        date = _timestamp_date(concept.get("timestamp", ""))
        by_date.setdefault(date, []).append(concept)
    if not by_date:
        today = datetime.now(timezone.utc).date().isoformat()
        by_date[today] = []

    lines = ["# Bundle Update Log", ""]
    for date in sorted(by_date.keys(), reverse=True):
        lines.append(f"## {date}")
        entries = by_date[date]
        if entries:
            for concept in sorted(entries, key=lambda item: item["path"]):
                lines.append(
                    f"* **Export**: Added [{concept['title']}](/{concept['path']})."
                )
        else:
            lines.append("* **Creation**: Created empty OKF export bundle.")
        lines.append("")
    return "\n".join(lines)


def _validate_index(rel: str, text: str) -> tuple[bool, dict[str, str]]:
    if rel == "index.md" and text.startswith("---\n"):
        meta = _parse_yamlish_frontmatter(text)
        okf_version = meta.get("okf_version", "") if meta else ""
        return meta == {"okf_version": OKF_VERSION}, {"okf_version": okf_version}
    return (not text.startswith("---\n")), {}


def _validate_log(text: str) -> bool:
    has_date_heading = bool(
        re.search(r"^## \d{4}-\d{2}-\d{2}$", text, re.MULTILINE)
    )
    return not text.startswith("---\n") and has_date_heading


def _parse_yamlish_frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    try:
        raw_meta, _body = text.removeprefix("---\n").split("\n---\n", 1)
    except ValueError:
        return None
    meta: dict[str, str] = {}
    current_key = ""
    for line in raw_meta.splitlines():
        if not line.strip():
            continue
        if line.startswith("  "):
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            return None
        current_key = key.strip()
        meta[current_key] = value.strip().strip('"')
    return meta if current_key or not raw_meta.strip() else None


def _render_markdown(meta: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.extend(_render_yaml_key_value(key, value))
    lines.extend(["---", ""])
    return "\n".join(lines) + body.lstrip("\n")


def _render_yaml_key_value(key: str, value: Any) -> list[str]:
    if isinstance(value, list):
        if not value:
            return []
        return [f"{key}:"] + [f"  - {_format_yaml_scalar(item)}" for item in value]
    text = str(value)
    if "\n" in text:
        return [f"{key}: |"] + [f"  {line}" for line in text.splitlines()]
    return [f"{key}: {_format_yaml_scalar(text)}"]


def _format_yaml_scalar(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return '""'
    lowered = text.lower()
    if (
        _SAFE_PLAIN_SCALAR_RE.match(text)
        and lowered not in {"null", "true", "false", "yes", "no", "on", "off"}
    ):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_list_value(value: str) -> list[str]:
    text = value.strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
        return [item.strip().strip("'\"") for item in text.split(",") if item.strip()]
    if "\n" in text:
        items: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                items.append(stripped[2:].strip().strip("'\""))
        return [item for item in items if item]
    return [text.strip("'\"")]


def _first_h1(body: str) -> str:
    match = _H1_RE.search(body)
    return match.group(1).strip() if match else ""


def _title_from_slug(bundle_rel: str) -> str:
    return Path(bundle_rel).stem.replace("-", " ").replace("_", " ").title()


def _first_nonempty(*values: str) -> str:
    for value in values:
        stripped = value.strip()
        if stripped:
            return stripped
    return ""


def _timestamp_date(timestamp: str) -> str:
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", timestamp.strip())
    if match:
        return match.group(1)
    return datetime.now(timezone.utc).date().isoformat()


def _workflow_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", key.strip().lower()).strip("_")


def _normalize_wikilink_target(target: str) -> str:
    normalized = target.strip().removeprefix("/").removesuffix(".md").replace("\\", "/")
    return normalized.lower()
