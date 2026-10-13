from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCHEMA_URL = (
    "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
)
# GitHub org namespace. Publishing needs mcp-publisher logged in as the
# TinyAssets org. This generator does not publish.
REGISTRY_NAME = "io.github.TinyAssets/tinyassets"
REGISTRY_VERSION = "0.2.0"
TITLE = "TinyAssets"
DESCRIPTION = (
    "Create, browse, remix, collaborate on, and run durable AI workflow nodes from MCP hosts."
)
REPOSITORY_URL = "https://github.com/TinyAssets/TinyAssets"
WEBSITE_URL = "https://tinyassets.io/connect"
REMOTE_URL = "https://tinyassets.io/mcp"
ICON_URL = "https://raw.githubusercontent.com/TinyAssets/TinyAssets/main/assets/icon.png"

MCPB_MANIFEST_PATH = REPO_ROOT / "packaging" / "mcpb" / "manifest.json"
BUNDLE_PATH = REPO_ROOT / "packaging" / "dist" / "tinyassets-universe-server.mcpb"
OUTPUT_PATH = REPO_ROOT / "packaging" / "registry" / "server.json"


def _read_mcpb_version() -> str:
    manifest = json.loads(MCPB_MANIFEST_PATH.read_text(encoding="utf-8"))
    version = manifest.get("version", "").strip()
    if not version:
        raise RuntimeError(f"Missing version in {MCPB_MANIFEST_PATH}")
    return version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_url(version: str) -> str:
    filename = f"tinyassets-universe-server-{version}.mcpb"
    return (
        "https://github.com/TinyAssets/TinyAssets/releases/download/"
        f"v{version}/{filename}"
    )


def _build_document(*, include_package: bool = False) -> dict[str, object]:
    document: dict[str, object] = {
        "$schema": SCHEMA_URL,
        "name": REGISTRY_NAME,
        "title": TITLE,
        "description": DESCRIPTION,
        "version": REGISTRY_VERSION,
        "repository": {
            "url": REPOSITORY_URL,
            "source": "github",
        },
        "websiteUrl": WEBSITE_URL,
        "icons": [
            {
                "src": ICON_URL,
                "mimeType": "image/png",
                "sizes": ["512x512"],
            }
        ],
        "remotes": [
            {
                "type": "streamable-http",
                "url": REMOTE_URL,
            }
        ],
    }

    if include_package:
        package_version = _read_mcpb_version()
        if not BUNDLE_PATH.is_file():
            raise FileNotFoundError(
                f"Built MCPB bundle not found: {BUNDLE_PATH}. Run the MCPB pack step first."
            )
        document["packages"] = [
            {
                "registryType": "mcpb",
                "identifier": _release_url(package_version),
                "version": package_version,
                "fileSha256": _sha256(BUNDLE_PATH),
                "transport": {
                    "type": "stdio",
                },
            }
        ]

    return document


def _validate(document: dict[str, object]) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError(
            "jsonschema is required for --validate. "
            "Install it in the current Python environment first."
        ) from exc

    with urllib.request.urlopen(SCHEMA_URL) as response:
        schema = json.load(response)
    jsonschema.Draft7Validator(schema).validate(document)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the MCP Registry server.json for TinyAssets."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the generated document differs from server.json.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the generated document against the official schema.",
    )
    parser.add_argument(
        "--include-package",
        action="store_true",
        help=(
            "Include the local MCPB package release metadata. Use only after "
            "packaging/dist/tinyassets-universe-server.mcpb and the matching "
            "GitHub release asset exist."
        ),
    )
    args = parser.parse_args()

    document = _build_document(include_package=args.include_package)

    if args.validate:
        _validate(document)
        print("Generated document passes the official server.json schema.")

    rendered = json.dumps(document, indent=2) + "\n"

    if args.check:
        current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
        if current != rendered:
            raise SystemExit("server.json is out of date with the built MCPB bundle.")
        print("server.json matches the generated document.")
        return

    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
