"""Owner-cell transport for the existing nested code-node sandbox.

Only the daemon owns action callbacks. The child receives data and one optional
pinned workspace; it cannot name a mount, executable, environment or identity.
"""
from __future__ import annotations

import json
import os
import queue
import select
import stat
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from tinyassets import node_sandbox as sandbox

INPUT_BOUND = sandbox.MAX_INPUT_BYTES + 65536
OUTPUT_BOUND = 8 * sandbox.MAX_OUTPUT_BYTES


def _encode(value, bound):
    data = json.dumps(value, default=str, ensure_ascii=False,
                      separators=(',', ':')).encode() + b'\n'
    if len(data) > bound:
        raise ValueError('node cell message exceeds its bound')
    return data


def _workspace_fd(workspace):
    if type(workspace) is not sandbox.WorkspaceMount:
        raise ValueError('node requires a typed workspace')
    source = workspace.bind_source
    match = sandbox._PROC_FD_BIND.fullmatch(source)
    if match:
        fd = int(match.group(1))
        if workspace.pass_fds != (fd,):
            raise ValueError('node requires exactly its held workspace descriptor')
        return os.dup(fd)
    if workspace.pass_fds:
        raise ValueError('node cannot inherit unrelated descriptors')
    from tinyassets.workspace_fs import open_dir_nofollow

    sandbox._validate_workspace_bind(source, workspace.allowed_roots, os.path.realpath, ())
    return open_dir_nofollow(source)


def run(instance, *, invoke, workspace, **request):
    from tinyassets import role_decoder
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir

    client = role_decoder._bounded_client
    if client is None or instance.universe_dir is None:
        raise RuntimeError('node requires its bounded owner launcher and command center')
    root = data_dir().resolve()
    center = Path(instance.universe_dir)
    principal = current_identity().user_id
    if (center.parent != root or center.resolve() != center
            or not (get_founder_home(root, principal) == center.name or universe_access_permission(
                root, universe_id=center.name, actor_id=principal) == 'admin')):
        raise PermissionError('node owner scope is not admitted')
    if instance.launcher is not None and type(instance.launcher) is not sandbox.BwrapLauncher:
        raise ValueError('node cell cannot replace the fixed nested jail')
    timeout = request['timeout']
    if (type(timeout) not in (int, float)
            or not 0 < timeout <= sandbox.MAX_WORKSPACE_TIMEOUT_SECONDS
            or type(instance.max_output_bytes) is not int
            or not 0 < instance.max_output_bytes <= sandbox.MAX_OUTPUT_BYTES):
        raise ValueError('invalid node cell limits')
    errors = instance.validate_source(request['source_code'])
    if errors:
        return sandbox.SandboxResult(node_id=request['node_id'], success=False,
                                     error=f"Validation failed: {'; '.join(errors)}")
    request['input_state'] = {k: v for k, v in request['input_state'].items()
                              if k in request['input_keys']}
    request['max_output_bytes'] = instance.max_output_bytes
    request['workspace_limits'] = asdict(workspace.limits) if workspace is not None else None
    payload = _encode(request, INPUT_BOUND)
    identity = owner_identity(root, principal=principal)
    fd = None
    try:
        source = None
        if workspace is not None:
            fd = _workspace_fd(workspace)
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode)
                    or (info.st_uid, info.st_gid) != (identity.uid, identity.gid)):
                raise PermissionError('node workspace does not match owner identity')
            source = [info.st_dev, info.st_ino]
        with client.start_cell(kind='node-sandbox', principal=principal,
                command_center=center.name, identity=identity,
                extra={'workspace': workspace is not None}, directory_fd=fd) as cell:
            cell.stream.setblocking(False)
            outgoing = memoryview(b'')
            pending = bytearray()
            total = 0
            proof_seen = False
            replies = queue.SimpleQueue()
            rpc_pending = False
            deadline = time.monotonic() + timeout + 5
            while True:
                if instance.should_cancel and sandbox._cancel_requested(instance.should_cancel):
                    cell.cancel()
                    return sandbox.SandboxResult(node_id=request['node_id'], success=False,
                                                  error='Node cancelled', cancelled=True)
                if time.monotonic() >= deadline:
                    cell.cancel()
                    return sandbox.SandboxResult(node_id=request['node_id'], success=False,
                                                  error=f'Execution timed out after {timeout}s')
                if rpc_pending:
                    try:
                        reply = replies.get_nowait()
                    except queue.Empty:
                        # Keep cancellation and lifetime supervision independent
                        # of an application callback that may still be blocked.
                        time.sleep(0.01)
                        continue
                    outgoing = memoryview(reply.encode() + b'\n')
                    rpc_pending = False
                if outgoing:
                    if select.select([], [cell.stream], [], 0.05)[1]:
                        sent = cell.stream.send(outgoing[:65536])
                        outgoing = outgoing[sent:]
                    continue
                if b'\n' not in pending:
                    if not select.select([cell.stream], [], [], 0.05)[0]:
                        continue
                    part = cell.stream.recv(65536)
                    if not part:
                        raise RuntimeError('node cell ended without a result')
                    total += len(part)
                    if total > OUTPUT_BOUND:
                        raise RuntimeError('node cell output exceeds its bound')
                    pending.extend(part)
                    continue
                line, _, rest = pending.partition(b'\n')
                pending = bytearray(rest)
                value = json.loads(line)
                if not proof_seen:
                    proof = value['cell']
                    inner = identity.uid - 300000
                    if (proof.get('uid') != inner or proof.get('gid') != inner
                            or proof.get('source') != source or proof.get('fds') != [0, 1, 2]
                            or proof.get('groups') != [] or proof.get('caps') != 'zero'
                            or proof.get('nnp') != 1 or proof.get('profile') != 'cell-nested'):
                        raise RuntimeError('node cell proof is absent')
                    proof_seen = True
                    outgoing = memoryview(payload)
                elif set(value) == {'rpc'}:
                    rpc = value['rpc']
                    if (type(rpc) is not dict or set(rpc) != {'id', 'action', 'kwargs'}
                            or type(rpc['id']) is not int or type(rpc['action']) is not str
                            or type(rpc['kwargs']) is not dict):
                        raise ValueError('invalid node RPC request')
                    def answer(request=rpc):
                        replies.put(sandbox._rpc_reply_line(
                            request['id'], request['action'], request['kwargs'], invoke))
                    threading.Thread(target=answer, daemon=True, name='owner-node-rpc').start()
                    rpc_pending = True
                elif set(value) == {'result'}:
                    if cell.wait(5) != 0:
                        raise RuntimeError('node cell did not exit successfully')
                    return sandbox.SandboxResult(**value['result'])
                else:
                    raise RuntimeError('invalid node cell response')
    finally:
        if fd is not None:
            os.close(fd)


def cell_main(mounted):
    """Trusted entry after namespace, identity and descriptor retirement proof."""
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    stream = sys.stdin.buffer
    raw = stream.readline(INPUT_BOUND + 1)
    if len(raw) > INPUT_BOUND or not raw.endswith(b'\n'):
        raise ValueError('invalid node request frame')
    request = json.loads(raw)
    limits = request.pop('workspace_limits')
    if mounted != (limits is not None):
        raise ValueError('node request does not match admitted mount')
    workspace = None
    fd = None
    if mounted:
        from tinyassets.workspace_fs import open_dir_nofollow

        fd = open_dir_nofollow('/workspace')
        workspace = sandbox.WorkspaceMount(f'/proc/self/fd/{fd}',
            limits=sandbox.WorkspaceLimits(**limits), pass_fds=(fd,))
    counter = 0

    def invoke(action, kwargs):
        nonlocal counter
        counter += 1
        sys.stdout.buffer.write(_encode(dict(rpc=dict(id=counter, action=action, kwargs=kwargs)),
                                        OUTPUT_BOUND))
        sys.stdout.buffer.flush()
        raw = stream.readline(INPUT_BOUND + 1)
        if len(raw) > INPUT_BOUND or not raw.endswith(b'\n'):
            raise RuntimeError('invalid node RPC response frame')
        answer = json.loads(raw)
        if answer.get('id') != counter:
            raise RuntimeError('node RPC response identity mismatch')
        if 'error' in answer:
            raise RuntimeError(answer['error'])
        return answer['result']

    try:
        instance = sandbox.NodeSandbox(max_output_bytes=request.pop('max_output_bytes'))
        result = instance.run_sync(**request, workspace=workspace, invoke=invoke)
        try:
            encoded = _encode({'result': asdict(result)}, OUTPUT_BOUND)
        except ValueError:
            result = sandbox.SandboxResult(node_id=request['node_id'], success=False,
                                            error='Node result exceeds the cell transport bound')
            encoded = _encode({'result': asdict(result)}, OUTPUT_BOUND)
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0
    finally:
        if fd is not None:
            os.close(fd)
