#!/usr/bin/env python3
"""Dependency-free ta client. Mounted read-only; executes extensions IN the jail."""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from pathlib import Path

SOCKET = "/tmp/ta.sock"
MAX_MESSAGE = 8 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def remote(message):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(600)
        client.connect(SOCKET)
        client.sendall(json.dumps(message).encode() + b"\n")
        with client.makefile("rb") as stream:
            raw = stream.readline(MAX_MESSAGE + 1)
    if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
        raise ValueError("invalid or oversized daemon response")
    answer = json.loads(raw)
    if "error" in answer:
        raise ValueError(json.dumps(answer))
    return answer


def extensions(roots):
    """Manifests are untrusted workspace files, read only by this jailed process."""
    found = {}
    for scope, directory in roots.items():
        root = Path(directory)
        if not root.is_dir():
            continue
        for package in sorted(root.iterdir()):
            if not NAME.fullmatch(package.name) or not package.is_dir():
                continue
            manifest = package / "extension.json"
            if not manifest.is_file():
                continue
            try:
                with manifest.open("rb") as source:
                    raw = source.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise ValueError("manifest too large")
                spec = json.loads(raw, object_pairs_hook=_unique_keys)
                executable = (package / spec["executable"]).resolve()
                if not executable.is_relative_to(package.resolve()):
                    raise ValueError("executable leaves package")
                if not isinstance(spec["tools"], list):
                    raise ValueError("tools must be a list")
                pending = {}
                for tool in spec["tools"]:
                    if (not NAME.fullmatch(tool["name"])
                            or not isinstance(tool["description"], str)
                            or not isinstance(tool["arguments"], dict)):
                        raise ValueError("invalid tool definition")
                    name = f"ext:{scope}:{package.name}:{tool['name']}"
                    if name in found or name in pending:
                        raise ValueError("duplicate extension tool")
                    pending[name] = {
                        "name": name, "description": tool["description"],
                        "arguments": tool["arguments"],
                        "executable": str(executable), "tool": tool["name"],
                    }
            except (ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
                # stderr keeps search/call stdout valid JSON. Do not partially
                # register a package whose later entry is invalid or duplicate.
                print(f"ta: skipped extension {manifest}: {str(exc)[:300]}", file=sys.stderr)
                continue
            found.update(pending)
    return found


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise ValueError("usage: ta search <words> | describe <name> | <name> --json '<args>'")
    catalog = remote({"op": "catalog"})
    local = extensions(catalog["extension_roots"])
    capabilities = {item["name"]: item for item in catalog["capabilities"]}
    capabilities.update(local)
    if argv[0] == "search":
        words = [word.lower() for word in argv[1:]]
        return [
            {"name": name, "description": item["description"]}
            for name, item in sorted(capabilities.items())
            if all(word in (name + " " + item["description"]).lower() for word in words)
        ]
    if argv[0] == "describe" and len(argv) == 2:
        item = capabilities[argv[1]]
        return {key: value for key, value in item.items() if key not in ("executable", "tool")}
    if len(argv) != 3 or argv[1] != "--json":
        raise ValueError("a call requires <name> --json '<args>'")
    arguments = json.loads(argv[2])
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    name = argv[0]
    if name in local:
        item = local[name]
        result = subprocess.run(
            [item["executable"], item["tool"], json.dumps(arguments)],
            stdout=subprocess.PIPE, check=True,
        )
        return json.loads(result.stdout)
    if name not in capabilities:
        raise ValueError(f"unknown capability: {name}")
    return remote({"op": "call", "name": name, "arguments": arguments})["result"]


if __name__ == "__main__":
    try:
        result = main()
        print(json.dumps(result, ensure_ascii=False))
        if isinstance(result, dict) and (result.get("error") or result.get("error_kind")):
            sys.exit(1)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
