from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPO_ROOT / "packaging" / "registry" / "generate_server_json.py"
SERVER_JSON_PATH = REPO_ROOT / "packaging" / "registry" / "server.json"
CANONICAL_REMOTE_URL = "https://tinyassets.io/mcp"


def _load_generate_server_json() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_server_json", GENERATOR_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_advertises_only_canonical_mcp() -> None:
    document = _load_generate_server_json()._build_document()

    assert document["name"] == "io.github.TinyAssets/tinyassets"
    assert document["title"] == "TinyAssets"
    assert document["remotes"] == [
        {
            "type": "streamable-http",
            "url": CANONICAL_REMOTE_URL,
        }
    ]
    assert "mcp-directory" not in json.dumps(document)


def test_remote_registry_version_is_independent_from_local_mcpb_version() -> None:
    generator = _load_generate_server_json()
    document = generator._build_document()

    assert document["version"] == generator.REGISTRY_VERSION
    assert generator.REGISTRY_VERSION == "0.2.0"
    assert generator._read_mcpb_version() == "0.1.0"


def test_committed_registry_manifest_matches_generated_document() -> None:
    committed_document = json.loads(SERVER_JSON_PATH.read_text(encoding="utf-8"))
    generated_document = _load_generate_server_json()._build_document()

    assert committed_document == generated_document


def test_registry_generator_check_runs_directly_from_repository_root() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
