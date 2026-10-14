#!/usr/bin/env python3
"""Run an exported command center locally (POSIX, Python 3.11+, no account).

This is owner-controlled host code, not a sandbox. Review imported code before
running it. Bash has your host privileges; each command asks by default.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

TOOLS = [
    {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {
            key: {"type": "string"} for key in keys
        }, "required": keys, "additionalProperties": False},
    }}
    for name, description, keys in (
        ("read", "Read a UTF-8 file relative to the export root.", ["path"]),
        ("write", "Replace a UTF-8 file relative to the export root.", ["path", "content"]),
        ("edit", "Replace one unique exact text occurrence in a file.", ["path", "old", "new"]),
        ("bash", "Run a shell command with the owner's configured consent.", ["command"]),
    )
]
MAX_FILE_BYTES = 1024 * 1024


class LocalTools:
    def __init__(self, root: Path, *, approve=None):
        if os.name != "posix":
            raise RuntimeError("This runner currently requires POSIX file-descriptor operations")
        self.root = root.resolve(strict=True)
        self.approve = approve or self._ask
        try:
            rules = json.loads(self.read("run/rules.json"))
        except FileNotFoundError:
            rules = {}
        if not isinstance(rules, dict) or set(rules) - {"bash"}:
            raise ValueError("run/rules.json supports only a bash policy")
        self.bash_policy = rules.get("bash", "ask")
        if self.bash_policy not in ("ask", "allow", "deny"):
            raise ValueError("bash policy must be ask, allow or deny")

    @staticmethod
    def _ask(command):
        if not sys.stdin.isatty():
            return False
        try:
            return input(f"Run with your host privileges?\n{command}\nType yes: ") == "yes"
        except (EOFError, KeyboardInterrupt):
            return False

    def _parent(self, path, *, writing=False):
        if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
            raise ValueError("Use a relative POSIX file path")
        parts = path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("Use a relative POSIX file path without traversal")
        if any(p.lower() in (".env", ".git", ".ssh", ".aws", ".credentials")
               or p.lower().startswith((".env.", ".credential-vault")) for p in parts):
            raise PermissionError("Credential and repository-private files are excluded")
        if writing and (parts[0] == "run" or path == "command-center.json"):
            raise PermissionError("Runner policy, code and manifest are read-only to tools")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd, parts[-1]
        except BaseException:
            os.close(fd)
            raise

    def read(self, path):
        parent, name = self._parent(path)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                import stat
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ValueError("Only unlinked regular files are readable")
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("File exceeds the local tool read limit")
            return data.decode("utf-8")
        finally:
            os.close(parent)

    def write(self, path, content):
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the local tool write limit")
        parent, name = self._parent(path, writing=True)
        temporary = ".runner-" + secrets.token_hex(12)
        try:
            import stat
            try:
                existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
                raise ValueError("Only unlinked regular files can be replaced")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            return "Written"
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def edit(self, path, old, new):
        current = self.read(path)
        if not old or current.count(old) != 1:
            raise ValueError("old must identify exactly one nonempty occurrence")
        return self.write(path, current.replace(old, new, 1))

    def bash(self, command):
        if self.bash_policy == "deny" or (
            self.bash_policy == "ask" and not self.approve(command)
        ):
            return "Refused: command lacks owner consent"
        result = subprocess.run(["bash", "-lc", command], cwd=self.root,
                                capture_output=True, text=True, timeout=120, check=False)
        return json.dumps({"exit_code": result.returncode,
                           "stdout": result.stdout[-32000:], "stderr": result.stderr[-32000:]})

    def call(self, name, arguments):
        schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in TOOLS}
        if name not in schemas:
            raise ValueError("Unknown tool")
        if (not isinstance(arguments, dict) or set(arguments) != set(schemas[name]["required"])
                or not all(isinstance(value, str) for value in arguments.values())):
            raise ValueError("Tool arguments must match the declared string fields")
        return getattr(self, name)(**arguments)


def complete(base_url, model, messages, *, api_key=""):
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.query or parsed.fragment):
        raise ValueError("MODEL_BASE_URL must be an HTTP(S) API base URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Use MODEL_API_KEY, not credentials in the URL")
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Remote model endpoints require HTTPS")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "messages": messages, "tools": TOOLS}).encode(),
        headers=headers)

    # Do not forward authorization to a redirect target chosen by a server.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    with urllib.request.build_opener(NoRedirect).open(request, timeout=120) as response:
        body = response.read(4 * MAX_FILE_BYTES + 1)
    if len(body) > 4 * MAX_FILE_BYTES:
        raise ValueError("Model response exceeds the local runner limit")
    return json.loads(body)["choices"][0]["message"]


def run(root, message, *, agent="main", model_call, max_calls=20, approve=None):
    if not agent or len(PurePosixPath(agent).parts) != 1 or agent in (".", ".."):
        raise ValueError("agent must be one directory name")
    if not 1 <= max_calls <= 100:
        raise ValueError("max_calls must be between 1 and 100")
    tools = LocalTools(Path(root), approve=approve)
    instructions = tools.read(f"agents/{agent}/AGENTS.md")
    messages = [{"role": "system", "content": instructions}, {"role": "user", "content": message}]
    for _ in range(max_calls):
        answer = model_call(messages)
        if not isinstance(answer, dict) or answer.get("role") != "assistant":
            raise ValueError("Expected an assistant model response")
        calls = answer.get("tool_calls")
        if not calls:
            if not isinstance(answer.get("content"), str):
                raise ValueError("Expected a final text response")
            return answer["content"]
        if not isinstance(calls, list) or not 1 <= len(calls) <= 32:
            raise ValueError("Expected a bounded tool call list")
        messages.append(answer)
        for call in calls:
            if (not isinstance(call, dict) or call.get("type") != "function"
                    or not isinstance(call.get("id"), str) or not call["id"]):
                raise ValueError("Malformed tool call")
            try:
                function = call["function"]
                output = tools.call(function["name"], json.loads(function["arguments"]))
            except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
                output = f"Tool failed: {type(exc).__name__}: {exc}"
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
    raise RuntimeError("Model-call limit reached; the task is unfinished")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--agent", default="main")
    parser.add_argument("--max-calls", type=int, default=20)
    args = parser.parse_args()
    model = os.environ.get("MODEL_NAME", "")
    if not model:
        parser.error("Set MODEL_NAME and MODEL_BASE_URL for your own endpoint")
    def model_call(messages):
        return complete(os.environ.get("MODEL_BASE_URL", "http://localhost:11434/v1"), model,
                        messages, api_key=os.environ.get("MODEL_API_KEY", ""))
    print(run(args.root, args.message, agent=args.agent, model_call=model_call,
              max_calls=args.max_calls))


if __name__ == "__main__":
    main()
