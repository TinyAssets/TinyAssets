"""Run an admitted code node inside its owner's box."""

from __future__ import annotations

import json
import time

from tinyassets import node_sandbox as sandbox
from tinyassets.box_process import run_bound


def run(instance, binding, *, invoke, workspace, **request):
    timeout = request["timeout"]
    if (type(timeout) not in (int, float)
            or not 0 < timeout <= sandbox.MAX_WORKSPACE_TIMEOUT_SECONDS):
        raise ValueError("invalid node box timeout")
    guest_workspace = None
    profile = ""
    if workspace is not None:
        from tinyassets.boxes.provider import BOX_ROOT

        guest_root = BOX_ROOT + "/" + binding.handle.command_center_id
        source = workspace.bind_source
        if (not isinstance(source, str) or workspace.pass_fds
                or not source.startswith(guest_root + "/")
                or any(part in ("", ".", "..")
                       for part in source[len(guest_root) + 1:].split("/"))):
            raise ValueError("node workspace requires a box-backed lease")
        guest_workspace = {"root": source, "limits": workspace.limits.as_message()}
        profile = json.dumps(workspace.limits.rlimit_profile())
    errors = instance.validate_source(request["source_code"])
    if errors:
        return sandbox.SandboxResult(node_id=request["node_id"], success=False,
                                     error=f"Validation failed: {'; '.join(errors)}")
    payload = dict(source_code=request["source_code"],
                   input_state={k: v for k, v in request["input_state"].items()
                                if k in request["input_keys"]},
                   effects=request.get("effects") or {},
                   output_keys=request["output_keys"],
                   allowed_imports=sorted(sandbox.ALLOWED_IMPORTS))
    if guest_workspace is not None:
        payload["workspace"] = guest_workspace
    message = json.dumps(payload, default=str).encode()
    if len(message) > sandbox.MAX_INPUT_BYTES:
        return sandbox.SandboxResult(node_id=request["node_id"], success=False,
                                     error="input too large for node box")

    def answer(line):
        try:
            frame = json.loads(line)
        except ValueError:
            return None
        if not isinstance(frame, dict) or set(frame) != {"rpc"}:
            return None
        rpc = frame["rpc"]
        if (not isinstance(rpc, dict) or set(rpc) != {"id", "action", "kwargs"}
                or type(rpc["id"]) is not int or type(rpc["action"]) is not str
                or type(rpc["kwargs"]) is not dict):
            raise ValueError("invalid node RPC frame")
        return sandbox._rpc_reply_line(rpc["id"], rpc["action"], rpc["kwargs"],
                                       invoke).encode()

    started = time.monotonic()
    done = run_bound(binding, "code-node", ["python", "-I", "-c", sandbox._RUNNER_SCRIPT,
                                             str(timeout), "1", profile],
                     stdin=message + b"\n", wall_seconds=timeout + 5,
                     output_bytes=instance.max_output_bytes,
                     line_handler=answer)
    result = None
    for line in reversed(done.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and type(candidate.get("success")) is bool:
            result = candidate
            break
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        raise RuntimeError("node box returned no structured result")
    printed = result.get("user_stdout", "")
    return sandbox.SandboxResult(
        node_id=request["node_id"], success=result["success"],
        output_state=result.get("output_state", {}), error=result.get("error", ""),
        duration_seconds=time.monotonic() - started, stdout=printed,
        stdout_tail=sandbox._tail(printed), warning=result.get("warning", ""),
        undeclared=[str(k) for k in result.get("undeclared") or []],
        workspace_timeout=bool(result.get("workspace_timeout")),
    )
