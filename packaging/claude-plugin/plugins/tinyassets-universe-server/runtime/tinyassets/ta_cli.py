#!/usr/bin/env python3
"""Dependency-free ta client. Mounted read-only; executes extensions IN the jail."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import uuid
from pathlib import Path

SOCKET = "/tmp/ta.sock"
MAX_MESSAGE = 8 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def remote(message):
    endpoint = os.environ.get("TA_SOCKET", SOCKET)
    bridged = "TA_SOCKET" in os.environ
    wire = {"request": uuid.uuid4().hex, "message": message} if bridged else message
    for attempt in range(2 if bridged else 1):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(600)
                client.connect("\0" + endpoint[1:] if endpoint.startswith("@") else endpoint)
                client.sendall(json.dumps(wire).encode() + b"\n")
                with client.makefile("rb") as stream:
                    raw = stream.readline(MAX_MESSAGE + 1)
            if not raw:
                raise ConnectionError("ta reply lost")
            break
        except OSError:
            if not bridged or attempt:
                raise ValueError("ta outcome unknown; do not retry blindly") from None
    if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
        raise ValueError("invalid or oversized daemon response")
    answer = json.loads(raw)
    if "error" in answer:
        raise ValueError(json.dumps(answer))
    return answer


def _staged(path):
    """A remote box stages the same read-only revision bytes per launch."""
    mount = "/ta/extensions/"
    root = os.environ.get("TA_EXTENSION_ROOT")
    if root is None or not path.startswith(mount):
        return path
    return os.path.join(root, path[len(mount):])


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
                # v2 requires revision-bound activation and lifecycle dispatch.
                # Until those exist, never execute it as a legacy tool package:
                # ignoring its version would bypass the activation contract.
                if not isinstance(spec, dict):
                    raise ValueError("manifest must be an object")
                version = spec.get("schema_version", 1)
                if type(version) is not int or version != 1:
                    raise ValueError("unsupported extension schema_version; activation unavailable")
                if {"hooks", "commands", "cards"} & spec.keys():
                    raise ValueError("lifecycle contributions require activated schema_version 2")
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


def main(argv=None, *, dispatch=None, load_extensions=True):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise ValueError("usage: ta search <words> | describe <name> | <name> --json '<args>'")
    invoke = dispatch or remote
    catalog = invoke({"op": "catalog"})
    if "error" in catalog:
        return catalog
    local = extensions(catalog["extension_roots"]) if load_extensions else {}
    capabilities = {item["name"]: item for item in catalog["capabilities"]}
    capabilities.update({item["name"]: item
                         for item in catalog.get("extension_capabilities", [])})
    if catalog.get("extension_error"):
        print(f"ta: extensions unavailable: {catalog['extension_error']}", file=sys.stderr)
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
    if argv[0] == "call":
        argv = argv[1:]
    if len(argv) != 3 or argv[1] != "--json":
        raise ValueError("a call requires <name> --json '<args>'")
    arguments = json.loads(argv[2])
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    name = argv[0]
    if name == "extension:event":
        if set(arguments) != {"version", "event", "payload"} or arguments["version"] != 1:
            raise ValueError("invalid extension event envelope")
        if len(argv[2].encode()) > 65536:
            raise ValueError("extension event exceeds 64 KiB")
        results = []
        for key, item in sorted(capabilities.items()):
            if item.get("kind") == "hooks" and item.get("event") == arguments["event"]:
                value = main([key, "--json", json.dumps(arguments)], dispatch=dispatch,
                             load_extensions=load_extensions)
                results.append({"hook": key, "result": value})
                if isinstance(value, dict) and (value.get("error") or value.get("error_kind")):
                    return {"error": "extension_hook_failed", "results": results}
        return {"event": arguments["event"], "results": results}
    if name in local:
        item = local[name]
        result = subprocess.run(
            [item["executable"], item["tool"], json.dumps(arguments)],
            stdout=subprocess.PIPE, check=True,
        )
        return json.loads(result.stdout)
    if name not in capabilities:
        raise ValueError(f"unknown capability: {name}")
    response = invoke({"op": "call", "name": name, "arguments": arguments})
    result = response.get("result", response)
    if (name.startswith("extension:") and isinstance(result, dict)
            and "extension_execution" in result):
        # An extension's executable runs only inside the jail, never in the
        # in-process host dispatch (which reads no local manifests either).
        if not load_extensions:
            return {"error": "extension execution requires the bash tool jail"}
        launch = result["extension_execution"]
        completed = subprocess.run(
            [_staged(launch["executable"]), launch["entry"], json.dumps(launch["arguments"])],
            cwd=_staged(launch["cwd"]), stdout=subprocess.PIPE, check=True,
        )
        return json.loads(completed.stdout)
    return result


if __name__ == "__main__":
    try:
        result = main()
        print(json.dumps(result, ensure_ascii=False))
        if isinstance(result, dict) and (result.get("error") or result.get("error_kind")):
            sys.exit(1)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
