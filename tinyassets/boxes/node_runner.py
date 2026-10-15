"""The code-node runner that executes inside the owner's box.

Three source strings, run by ``python -I -c`` in the guest: the rlimit helper,
the workspace helper (``ws`` over the leased checkout) and the runner itself.
The host side is :mod:`tinyassets.boxes.node`.
"""

from __future__ import annotations

import textwrap

MAX_USER_PRINT_BYTES = 64 * 1024

_RLIMIT_HELPER = '''
def _apply_rlimits(resource_module, timeout, profile=None):
    """Apply the code-node resource limits; return a list of failures.

    Every limit is set AND read back: a `setrlimit` that raises, a hard
    ceiling below the target, and a silently ignored call are all failures.
    The caller decides whether a failure is fatal (bwrap: yes) or a warning
    (the tests-only launcher on a platform with no `resource`).

    RLIMIT_NPROC is the exception, because it is per-UID rather than
    per-process: lowering it bounds every process the user already runs, not
    just this child. On a host whose uid is already past our number -- a CI
    runner, a shared account -- setting it makes the very next fork fail with
    EAGAIN, which is not a bound on the node, it is a broken host. So it is
    RAISED toward the cap and never lowered: a limit already tighter than
    ours is somebody else's decision and stands.
    """
    if resource_module is None:
        return ["all limits (this platform has no 'resource' module)"]

    try:
        cpu_seconds = int(timeout) + (0 if timeout == int(timeout) else 1) + 1
    except Exception:
        cpu_seconds = 31

    failures = []
    if profile:
        wanted = [(str(k), int(v)) for k, v in sorted(profile.items())]
        if not any(name == "RLIMIT_CPU" for name, _v in wanted):
            wanted.append(("RLIMIT_CPU", cpu_seconds))
    else:
        wanted = [
            ("RLIMIT_AS", 512 * 1024 * 1024),
            ("RLIMIT_CPU", cpu_seconds),
            ("RLIMIT_FSIZE", 16 * 1024 * 1024),
            ("RLIMIT_NOFILE", 64),
        ]
    for name, want in wanted:
        try:
            res = getattr(resource_module, name)
            soft, hard = resource_module.getrlimit(res)
            if hard == resource_module.RLIM_INFINITY:
                new_hard = want
            else:
                new_hard = min(want, hard)
            target = min(want, new_hard)
            if name == "RLIMIT_NPROC":
                unlimited = soft == resource_module.RLIM_INFINITY
                if unlimited or soft >= target:
                    # Already at least as permissive as the cap, and it is
                    # per-UID: leaving it is the whole point.
                    continue
                resource_module.setrlimit(res, (target, max(new_hard, soft)))
                applied = resource_module.getrlimit(res)[0]
                if applied < target:
                    failures.append(
                        f"{name} (asked for {target}, reads {applied})"
                    )
                continue
            resource_module.setrlimit(res, (target, new_hard))
            applied = resource_module.getrlimit(res)[0]
            if applied != target:
                failures.append(
                    f"{name} (asked for {target}, reads {applied})"
                )
        except Exception as exc:
            failures.append(f"{name} ({type(exc).__name__}: {exc})")
    return failures
'''


# The `ws` capability. Lives in the RUNNER's globals, never the node's: the node
# executes in its own namespace dict, so it cannot reach `os` or `subprocess`
# through `ws` even though `ws` uses them. Every import this needs happens at the
# top of the runner, BEFORE the import allowlist is installed, so the allowlist
# still refuses the same names to node code.
_WORKSPACE_HELPER = '''
def _ws_exact_str(value, label):
    """EXACTLY ``str``, never a subclass.

    A ``str`` subclass can override ``replace``, ``__str__`` or ``__fspath__``
    and run node code inside path validation. Nothing below calls a method on a
    caller's object before its type is known to be the built-in, so there is no
    user code between a check and the open it guards.
    """
    if type(value) is not str:
        raise TypeError(
            "ws " + label + " must be a str, not " + type(value).__name__
        )
    return value


def _ws_exact_int(value, label):
    if type(value) is not int or type(value) is bool:
        raise TypeError(
            "ws " + label + " must be an int, not " + type(value).__name__
        )
    return value


def _ws_split(relpath, kind):
    """Validate a relative path and return its components."""
    _ws_exact_str(relpath, kind)
    if not relpath.strip():
        raise ValueError("workspace " + kind + " must be a non-empty string")
    if chr(0) in relpath:
        raise ValueError("workspace " + kind + " contains a NUL byte")
    norm = relpath.replace(chr(92), "/")
    if norm.startswith("/"):
        raise ValueError(
            "workspace " + kind + " must be relative, not absolute: " + relpath
        )
    if len(norm) > 1 and norm[1] == ":":
        raise ValueError(
            "workspace " + kind + " must be relative, not a drive path: " + relpath
        )
    parts = []
    for piece in norm.split("/"):
        if piece == "..":
            raise ValueError(
                "workspace " + kind + " may not contain " + chr(39) + ".." + chr(39)
                + ": " + relpath
            )
        if piece not in ("", "."):
            parts.append(piece)
    return parts


_WS_DIR_FD_OPS = getattr(os, "supports_dir_fd", set())
_WS_HAS_DIR_FD = (
    hasattr(os, "O_DIRECTORY")
    and os.open in _WS_DIR_FD_OPS
    and os.mkdir in _WS_DIR_FD_OPS
)
_WS_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_WS_BINARY = getattr(os, "O_BINARY", 0)
_WS_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_WS_MAX_DEPTH = 64


class _WsRoot(object):
    """The workspace, held open.

    On a host with ``dir_fd`` support every component is opened relative to the
    previous descriptor with ``O_NOFOLLOW``, so a path is never validated as a
    string and then opened by name: there is no window in which a component can
    be swapped for a link, because after the first open there is no name left to
    swap. Where the platform has no ``dir_fd`` (Windows, and therefore only the
    tests-only launcher, which is not a security boundary) the same rules are
    enforced against resolved paths.
    """

    def __init__(self, path):
        self.path = os.path.realpath(path)
        self.fd = None
        if _WS_HAS_DIR_FD:
            self.fd = os.open(self.path, os.O_RDONLY | _WS_DIRECTORY)

    # -- resolution ----------------------------------------------------------

    def _walk(self, parts, kind, make_parents=False):
        """Return ``(parent_fd, owned)`` for the directory holding the leaf."""
        if len(parts) > _WS_MAX_DEPTH:
            raise ValueError("workspace " + kind + " is nested too deeply")
        current = self.fd
        owned = False
        try:
            for part in parts:
                flags = os.O_RDONLY | _WS_DIRECTORY | _WS_NOFOLLOW
                try:
                    nxt = os.open(part, flags, dir_fd=current)
                except FileNotFoundError:
                    if not make_parents:
                        raise
                    os.mkdir(part, 448, dir_fd=current)
                    nxt = os.open(part, flags, dir_fd=current)
                if owned:
                    os.close(current)
                current = nxt
                owned = True
        except OSError as exc:
            if owned:
                os.close(current)
            raise ValueError(
                "workspace " + kind + " cannot be resolved beneath the workspace: "
                + str(exc)
            )
        return current, owned

    def open_leaf(self, parts, kind, flags, mode=None, make_parents=False):
        """Open the last component with O_NOFOLLOW, relative to its parent."""
        if not parts:
            raise ValueError("workspace " + kind + " names the workspace root")
        parent, owned = self._walk(parts[:-1], kind, make_parents=make_parents)
        try:
            if mode is None:
                return os.open(parts[-1], flags | _WS_NOFOLLOW, dir_fd=parent)
            return os.open(parts[-1], flags | _WS_NOFOLLOW, mode, dir_fd=parent)
        except OSError as exc:
            raise ValueError(
                "workspace " + kind + " cannot be opened beneath the workspace: "
                + str(exc)
            )
        finally:
            if owned:
                os.close(parent)

    def dir_reference(self, parts, kind):
        """A path the child can chdir to, naming an OPENED directory."""
        parent, owned = self._walk(parts, kind)
        try:
            return "/proc/self/fd/" + str(parent), parent, owned
        except BaseException:
            if owned:
                os.close(parent)
            raise

    def scan(self, limit):
        """Every relative path beneath the root, links neither followed nor listed."""
        found = []
        stack = [(self.fd, "", 0)]
        owned = set()
        try:
            while stack and len(found) < limit:
                handle, prefix, depth = stack.pop()
                if depth > _WS_MAX_DEPTH:
                    continue
                try:
                    entries = list(os.scandir(handle))
                except OSError:
                    continue
                for entry in entries:
                    if entry.is_symlink():
                        continue
                    name = prefix + entry.name
                    found.append(name)
                    if len(found) >= limit:
                        break
                    if entry.is_dir(follow_symlinks=False):
                        try:
                            child = os.open(
                                entry.name,
                                os.O_RDONLY | _WS_DIRECTORY | _WS_NOFOLLOW,
                                dir_fd=handle,
                            )
                        except OSError:
                            continue
                        owned.add(child)
                        stack.append((child, name + "/", depth + 1))
        finally:
            for handle in owned:
                try:
                    os.close(handle)
                except OSError:
                    pass
        return found

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def _ws_match(parts, pattern_parts):
    """Glob semantics where ``*`` does not cross a separator and ``**`` does."""
    if not pattern_parts:
        return not parts
    head = pattern_parts[0]
    if head == "**":
        if len(pattern_parts) == 1:
            return True
        for index in range(len(parts) + 1):
            if _ws_match(parts[index:], pattern_parts[1:]):
                return True
        return False
    if not parts:
        return False
    if not fnmatch.fnmatchcase(parts[0], head):
        return False
    return _ws_match(parts[1:], pattern_parts[1:])


class _WorkspaceTail(object):
    """Incremental bounded drain: keeps the last `cap` bytes, counts the rest."""

    def __init__(self, stream, cap):
        self._stream = stream
        self._cap = cap
        self._chunks = []
        self._held = 0
        self.total = 0
        self.thread = threading.Thread(target=self._drain)
        self.thread.daemon = True

    def _drain(self):
        try:
            while True:
                chunk = self._stream.read(65536)
                if not chunk:
                    break
                self.total += len(chunk)
                self._chunks.append(chunk)
                self._held += len(chunk)
                while self._held > self._cap and len(self._chunks) > 1:
                    self._held -= len(self._chunks.pop(0))
        except Exception:
            pass
        finally:
            try:
                self._stream.close()
            except Exception:
                pass

    def text(self):
        data = b"".join(self._chunks)[-self._cap:]
        return data.decode("utf-8", "replace")

    def truncated(self):
        return self.total > self._cap


def _make_workspace(conf, remaining):
    """Return the `ws` object bound to one workspace root."""
    if not _WS_HAS_DIR_FD:
        # The workspace is held open by descriptor and every component is
        # opened relative to it. A host without `dir_fd` cannot do that, and
        # the path stand-in that used to cover it was no boundary at all.
        raise SandboxUnavailableError(
            "workspace nodes need a host with os.open(dir_fd=...) support")
    root = _WsRoot(conf["root"])
    limits = conf.get("limits") or {}
    max_output = int(limits.get("max_output_bytes", 1048576))
    max_read = int(limits.get("max_read_bytes", 1048576))
    max_glob = int(limits.get("max_glob_results", 10000))
    tail_cap = int(limits.get("tail_bytes", 65536))
    default_timeout = limits.get("command_timeout_s")
    counters = {"commands": 0, "bytes": 0}

    # ── The raw doors, as CLOSURES rather than methods ────────────────────
    #
    # `ws` is handed to untrusted node code, so anything hanging off it is
    # part of the public surface no matter how many underscores it starts
    # with. A refute review of #2738 made that concrete: an earlier
    # draft of this refactor exposed `_read_raw` and `_write_raw` as methods,
    # and in the real jail `ws._write_raw(['/tmp/escape'], b'pwned')` wrote 5
    # bytes OUTSIDE the workspace root, while
    # `ws._read_raw('seed.txt', 10**12)` read with a cap the configuration
    # never authorised. Both measured, both real.
    #
    # As closures they are unreachable from `ws` while still being shared by
    # the text door and the binary one, which is what the refactor was for.
    # And they take a RELPATH, not pre-split parts: the split IS the
    # validation, so there is no un-validated entry point left to reach.

    def _cap_for(max_bytes, label):
        if max_bytes is None:
            cap = max_read
        else:
            # CLAMPED, not replaced: a caller cannot raise the node's cap
            # by asking for more than the limits allow.
            cap = min(_ws_exact_int(max_bytes, label + " max_bytes"), max_read)
        if cap <= 0:
            raise ValueError("ws." + label + " max_bytes must be positive")
        return cap

    def _read_leaf(relpath, cap):
        """The bytes of one leaf, resolved and bounded.

        Shared by `read` and `read_bytes` rather than copied, so the
        component-wise NOFOLLOW resolution and the cap cannot drift apart
        between the text door and the binary one.
        """
        parts = _ws_split(relpath, "path")
        handle = root.open_leaf(parts, "path", os.O_RDONLY | _WS_BINARY)
        try:
            data = b""
            while len(data) <= cap:
                chunk = os.read(handle, 65536)
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(handle)
        if len(data) > cap:
            raise RuntimeError(
                "workspace limit: %s is larger than %d bytes" % (relpath, cap)
            )
        return data

    def _write_leaf(relpath, data):
        parts = _ws_split(relpath, "path")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _WS_BINARY
        handle = root.open_leaf(parts, "path", flags, mode=384, make_parents=True)
        try:
            written = 0
            while written < len(data):
                written += os.write(handle, data[written:])
        finally:
            os.close(handle)
        return len(data)

    def _charge(size, label):
        """Count RAW bytes against the node's cumulative output budget.

        RAW, not encoded: base64 is 4/3 the size, so charging the encoded
        length would bill a node a third more than it moved, and capping the
        encoded length would silently shrink what it may read.
        """
        counters["bytes"] += size
        if counters["bytes"] > max_output:
            raise RuntimeError(
                "workspace limit: %s passed %d bytes for this node"
                % (label, max_output)
            )

    class _Workspace(object):
        """The checked-out project, and nothing else."""

        path = root.path

        def run(self, argv, timeout=None, cwd=None, env=None):
            # Types FIRST, before a single method is called on any of it.
            if type(argv) is not list and type(argv) is not tuple:
                raise ValueError("ws.run needs a non-empty argv list")
            argv = list(argv)
            if not argv:
                raise ValueError("ws.run needs a non-empty argv list")
            for item in argv:
                _ws_exact_str(item, "run argv element")
                if chr(0) in item:
                    raise ValueError("ws.run argv contains a NUL byte")
            if timeout is not None and type(timeout) is not int and type(timeout) is not float:
                raise TypeError("ws.run timeout must be a number")
            # `git commit` and `git fetch` fork a detached `gc --auto`, which
            # keeps `.git/objects/pack` open after the node returns and made
            # the lease wipe fail on Windows. Turned off through GIT_CONFIG_*
            # rather than a config file, because the workspace's own `.git`
            # is writable by node code and a file there would not bind.
            child_env = {
                "HOME": "/tmp",
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "gc.auto",
                "GIT_CONFIG_VALUE_0": "0",
                "GIT_CONFIG_KEY_1": "maintenance.auto",
                "GIT_CONFIG_VALUE_1": "false",
            }
            if env is not None:
                if type(env) is not dict:
                    raise TypeError("ws.run env must be a dict")
                for key in sorted(env):
                    _ws_exact_str(key, "run env name")
                    if not _WS_ENV_NAME.match(key):
                        raise ValueError(
                            "ws.run env name is not an env name: " + repr(key)
                        )
                    value = env[key]
                    _ws_exact_str(value, "run env value")
                    if chr(0) in value:
                        raise ValueError("ws.run env value holds a NUL byte")
                    if key.startswith("GIT_CONFIG"):
                        # Refused, not ignored: silently dropping it would let
                        # a node believe it had turned maintenance back on.
                        raise ValueError(
                            "ws.run env may not set " + key + ": the workspace's "
                            "git configuration is fixed"
                        )
                    child_env[key] = value

            # Counted, never capped: a loop's length is the author's decision.
            counters["commands"] += 1

            parts = [] if cwd is None else _ws_split(cwd, "cwd")
            work, handle, owned = root.dir_reference(parts, "cwd")
            try:
                budget = remaining()
                if default_timeout is not None:
                    budget = min(budget, float(default_timeout))
                if timeout is not None:
                    budget = min(budget, float(timeout))
                if budget <= 0:
                    raise RuntimeError(
                        "workspace limit: no time left in the node budget"
                    )
                return self._spawn(argv, work, child_env, budget)
            finally:
                if owned and handle is not None:
                    os.close(handle)

        def _spawn(self, argv, work, child_env, budget):
            popen_kwargs = {}
            if hasattr(os, "setsid"):
                popen_kwargs["start_new_session"] = True
            elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                argv,
                cwd=work,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                **popen_kwargs
            )
            out = _WorkspaceTail(proc.stdout, tail_cap)
            err = _WorkspaceTail(proc.stderr, tail_cap)
            out.thread.start()
            err.thread.start()
            timed_out = False
            try:
                proc.wait(timeout=budget)
            except subprocess.TimeoutExpired:
                timed_out = True
                _ws_kill_group(proc)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            out.thread.join(timeout=2.0)
            err.thread.join(timeout=2.0)

            if timed_out:
                _ws_exit_on_timeout(argv, budget, out.text(), err.text())

            result = {
                "returncode": proc.returncode,
                "stdout_tail": out.text(),
                "stderr_tail": err.text(),
                "truncated": bool(out.truncated() or err.truncated()),
            }
            counters["bytes"] += len(result["stdout_tail"]) + len(result["stderr_tail"])
            if counters["bytes"] > max_output:
                raise RuntimeError(
                    "workspace limit: returned output passed %d bytes for this node"
                    % max_output
                )
            return result

        def read(self, relpath, max_bytes=None):
            return _read_leaf(relpath, _cap_for(max_bytes, "read")).decode(
                "utf-8", "replace"
            )

        def write(self, relpath, text):
            _ws_exact_str(text, "write text")
            return _write_leaf(relpath, text.encode("utf-8"))

        def read_bytes(self, relpath, max_bytes=None):
            """The file's BYTES, base64-encoded into an ASCII str.

            The channel out of this jail is JSON lines, which carries no bytes
            type, and node code cannot call ``open()`` -- so without this a
            node can produce a PNG, a video or a zip and have no way to hand it
            on. Decode it with the allowlisted ``base64`` module::

                import base64
                blob = base64.b64decode(ws.read_bytes("out/clip.mp4"))

            ``max_bytes`` bounds the RAW file, never the encoded string: the
            4/3 expansion is this method's business, not the caller's. It
            CLAMPS to the configured cap exactly as ``ws.read`` does, and the
            raw size is charged to the node's cumulative output budget, so
            reading a file as bytes is not a way around a cap that reading it
            as text would hit.
            """
            data = _read_leaf(relpath, _cap_for(max_bytes, "read_bytes"))
            _charge(len(data), "read_bytes")
            return base64.b64encode(data).decode("ascii")

        def write_bytes(self, relpath, b64):
            """Write the bytes of a base64 string; returns the RAW count.

            The inverse of :meth:`read_bytes`, with the same path rules as
            ``ws.write`` -- relative, no ``..``, every component opened
            ``O_NOFOLLOW``, parents created beneath the root.

            The decode is STRICT (``validate=True``): a string carrying
            anything outside the base64 alphabet is refused by name rather
            than silently truncated at the first bad character, which is what
            ``b64decode`` does by default and would write a corrupt file that
            looks like a successful one.

            The decoded size is charged BEFORE the open, so a payload that
            exceeds the node's budget leaves no partial file behind.

            And the ENCODED length is refused before the decode, which is not
            the same guard. Charging after decoding still lets a node hand in
            an arbitrarily long string and make the interpreter allocate it
            plus its decoded copy before any budget has an opinion -- roughly
            1.75x the string, inside a jail whose memory the host shares.
            Review raised this against #2738 (Q4). The bound is derived from
            `max_output` rather than configured separately, so a node can
            still pass anything it could afford to write.
            """
            _ws_exact_str(b64, "write_bytes b64")
            # 4 encoded chars per 3 raw bytes, plus padding and any newlines a
            # caller's encoder inserted; generous on purpose, since this is a
            # memory guard and `_charge` remains the accounting one.
            encoded_ceiling = 4 * ((max_output + 2) // 3) + 1024
            if len(b64) > encoded_ceiling:
                raise RuntimeError(
                    "workspace limit: write_bytes was handed %d encoded chars, "
                    "more than this node could write even at its full %d byte "
                    "budget" % (len(b64), max_output)
                )
            try:
                data = base64.b64decode(b64, validate=True)
            except Exception as exc:
                raise ValueError(
                    "ws.write_bytes needs strict base64 (see ws.read_bytes): "
                    + str(exc)
                )
            _charge(len(data), "write_bytes")
            return _write_leaf(relpath, data)

        def glob(self, pattern):
            _ws_exact_str(pattern, "glob pattern")
            if not pattern.strip():
                raise ValueError("ws.glob needs a non-empty pattern")
            norm = pattern.replace(chr(92), "/")
            if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
                raise ValueError("ws.glob pattern must be relative: " + pattern)
            pattern_parts = [p for p in norm.split("/") if p not in ("", ".")]
            if any(p == ".." for p in pattern_parts):
                raise ValueError(
                    "ws.glob pattern may not contain " + chr(39) + ".." + chr(39)
                    + ": " + pattern
                )
            found = []
            for name in root.scan(max_glob * 4):
                if _ws_match(name.split("/"), pattern_parts):
                    found.append(name)
                    if len(found) >= max_glob:
                        break
            return sorted(set(found))

        def bundle(self, commit_sha):
            _ws_exact_str(commit_sha, "bundle commit sha")
            if not _WS_SHA.match(commit_sha):
                raise ValueError("ws.bundle needs a 40-character hex commit sha")
            relative = ".tiny-export/" + commit_sha + ".bundle"
            handle = root.open_leaf(
                _ws_split(relative, "path"),
                "path",
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _WS_BINARY,
                mode=384,
                make_parents=True,
            )
            os.close(handle)
            target = os.path.join(root.path, ".tiny-export", commit_sha + ".bundle")
            # -c as well as the environment: a bundle is the one path whose
            # output crosses the jail boundary, so its git runs with
            # maintenance off no matter how it was invoked.
            quiet = [
                "-c", "gc.auto=0",
                "-c", "maintenance.auto=false",
                # One pack thread: RLIMIT_NPROC is per-UID, so on a host whose
                # uid already runs hundreds of processes git's thread pool
                # fails with EAGAIN before it packs anything.
                "-c", "pack.threads=1",
            ]
            base = ["git", "-c", "core.hooksPath=/dev/null"] + quiet + [
                "--no-replace-objects"
            ]
            steps = [
                base + ["update-ref", "refs/tiny/export", commit_sha],
                ["git"] + quiet + ["--no-replace-objects", "bundle", "create",
                                   target, "refs/tiny/export"],
            ]
            try:
                for step in steps:
                    outcome = self.run(step)
                    if outcome["returncode"] != 0:
                        raise RuntimeError(
                            "ws.bundle failed (%s): %s"
                            % (step[-1], outcome["stderr_tail"][-500:])
                        )
            finally:
                self.run(base + ["update-ref", "-d", "refs/tiny/export"])
            return relative

    return _Workspace()
'''

# Executed in the child as `python -c <script> <timeout>`. In order:
# 1. Resource limits, before anything is read or parsed.
# 2. Replace sys.stdout with a bounded buffer, so node print() can never
#    reach the descriptor the result protocol uses.
# 3. Read the JSON message from stdin (source + state + effects).
# 4. Restrict imports, execute the node source, call run(state[, effects]).
# 5. Write one result JSON object to the real stdout.

_RUNNER_SCRIPT = textwrap.dedent('''\
    import base64
    import fnmatch
    import json
    import os
    import re
    import signal
    import subprocess
    import sys
    import threading

    # Imported HERE, at the top, before the allowlist below replaces
    # __import__: `ws` needs them and node code must still be refused them.
    # The node executes in its own namespace dict and never sees these names.
    # (`base64` is the exception that proves it: `ws.read_bytes` encodes with
    # it here, and node code is separately allowed to import its own copy to
    # decode -- it is on the allowlist, unlike `subprocess` or `signal`.)
    _WS_ENV_NAME = re.compile("^[A-Z_][A-Z0-9_]*$")
    _WS_SHA = re.compile("^[0-9a-f]{40}$")

    # ---- 1. Resource limits FIRST -----------------------------------------
    # Before the message is read at all: a hostile payload must not get any
    # work done under an unbounded process. argv carries the timeout and
    # whether limits are mandatory, precisely so this runs before stdin.
    try:
        _timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    except Exception:
        _timeout = 30.0
    _require_rlimits = len(sys.argv) > 2 and sys.argv[2] == "1"
    _rlimit_profile = None
    _rlimit_profile_error = None
    if len(sys.argv) > 3 and sys.argv[3]:
        try:
            _rlimit_profile = json.loads(sys.argv[3])
        except Exception as exc:
            _rlimit_profile_error = "rlimit profile is unreadable: %s" % (exc,)

    try:
        import resource as _resource
    except Exception:
        _resource = None

    __RLIMIT_HELPER__

    _ws_start = None
    _ws_real_stdout = None

    def _ws_kill_group(proc):
        killpg = getattr(os, "killpg", None)
        getpgid = getattr(os, "getpgid", None)
        sigkill = getattr(signal, "SIGKILL", None)
        if killpg is not None and getpgid is not None and sigkill is not None:
            try:
                killpg(getpgid(proc.pid), sigkill)
                return
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass

    def _ws_exit_on_timeout(argv, budget, out_tail, err_tail):
        payload = {
            "success": False,
            "workspace_timeout": True,
            "error": (
                "workspace command timeout: %r exceeded %.3fs"
                % (argv[:4], budget)
            ),
            "user_stdout": _captured.getvalue(),
            "stdout_tail": out_tail[-2048:],
            "stderr_tail": err_tail[-2048:],
        }
        try:
            _ws_real_stdout.write(json.dumps(payload) + "\\n")
            _ws_real_stdout.flush()
        except Exception:
            pass
        # os._exit, not SystemExit: the node's `except BaseException` must not
        # be able to swallow a timeout and keep running in the jail.
        os._exit(4)

    __WORKSPACE_HELPER__

    _rlimit_failures = _apply_rlimits(_resource, _timeout, _rlimit_profile)
    if _rlimit_profile_error:
        _rlimit_failures = [_rlimit_profile_error] + _rlimit_failures
    if _require_rlimits and _rlimit_failures:
        # The OS sandbox demands these limits. Refuse loudly, before the
        # message is parsed and long before any node code runs.
        sys.stdout.write(json.dumps({
            "success": False,
            "error": "rlimits not applied: " + ", ".join(_rlimit_failures),
        }) + "\\n")
        sys.stdout.flush()
        raise SystemExit(3)

    import inspect

    # ---- 2. Protocol integrity --------------------------------------------
    # The node's print() must not be able to write a result. Capture it into
    # a bounded buffer; the real descriptor is written once, at the end.
    _real_stdout = sys.stdout
    _ws_real_stdout = _real_stdout


    class _BoundedStdout:
        def __init__(self, cap):
            self._cap = cap
            self._parts = []
            self._size = 0
            self.dropped = 0

        def write(self, text):
            if not isinstance(text, str):
                text = str(text)
            room = self._cap - self._size
            if room > 0:
                piece = text[:room]
                self._parts.append(piece)
                self._size += len(piece)
                overflow = len(text) - len(piece)
            else:
                overflow = len(text)
            if overflow > 0:
                self.dropped += overflow
            return len(text)

        def writelines(self, lines):
            for line in lines:
                self.write(line)

        def flush(self):
            return None

        def isatty(self):
            return False

        def getvalue(self):
            return "".join(self._parts)


    _captured = _BoundedStdout(MAX_USER_PRINT_BYTES)
    sys.stdout = _captured

    # ---- 3. Message: ONE line. stdin stays open for RPC replies. ----------
    raw = sys.stdin.readline()
    msg = json.loads(raw)

    source_code = msg["source_code"]
    input_state = msg["input_state"]
    effects = msg.get("effects") or {}
    output_keys = msg["output_keys"]
    allowed_imports = set(msg["allowed_imports"])

    # ---- 3b. The workspace capability, when the node declared one --------
    _ws = None
    if isinstance(msg.get("workspace"), dict):
        import time as _ws_time

        _ws_start = _ws_time.monotonic()

        def _ws_remaining():
            # A command never outlives the node: the parent would kill the
            # jail anyway, and a shorter budget makes the failure legible.
            return max(0.0, _timeout - (_ws_time.monotonic() - _ws_start) - 0.5)

        _ws = _make_workspace(msg["workspace"], _ws_remaining)

    # ---- 4. Import allowlist ----------------------------------------------
    # The node executes in THIS dict. It is created here, before the hook, so
    # the hook can recognise an import made BY node code by identity.
    namespace = {}
    _original_import = (
        __builtins__.__import__
        if hasattr(__builtins__, "__import__")
        else __import__
    )

    # Only imports made BY NODE CODE are name-checked, and the test for that is
    # the calling frame's globals: node code runs in `namespace` and nothing
    # else does. An allowlisted module's own imports (base64 -> binascii) come
    # from that module's globals and are permitted, otherwise half the
    # allowlist would be unimportable; the same is true of the lazy imports
    # `subprocess.Popen` makes on POSIX while `ws` is running.
    #
    # This replaces a recursion COUNTER (review #8). A counter says how deep
    # the stack is, not whose code is running, so anything that called into
    # user code while it was raised -- `ws` calling `.replace()` on a `str`
    # subclass -- handed node code an unchecked import. A frame cannot be
    # held open across a call the way a counter can.
    _get_frame = getattr(sys, "_getframe", None)

    def _restricted_import(name, *args, **kwargs):
        by_node = True
        if _get_frame is not None:
            try:
                by_node = _get_frame(1).f_globals is namespace
            except ValueError:
                by_node = True
        if by_node:
            top_level = name.split(".")[0]
            if top_level not in allowed_imports:
                raise ImportError(
                    f"Import '{name}' is not allowed in sandboxed nodes. "
                    f"Allowed: {sorted(allowed_imports)}"
                )
        return _original_import(name, *args, **kwargs)

    if hasattr(__builtins__, "__import__"):
        __builtins__.__import__ = _restricted_import
    else:
        import builtins
        builtins.__import__ = _restricted_import

    # ---- 4b. invoke_mcp_action: synchronous RPC to the parent -------------
    # The child holds no authority and performs no action. It writes one
    # request line to the real stdout and blocks on stdin for the parent's
    # reply; the parent runs the action under the run's authority. Code
    # needs the answer inside run() (a wiki_read returns the page it then
    # edits), so this is a call, not a deferred queue.
    _rpc = {"next_id": 1, "used": 0}

    def invoke_mcp_action(action_name, **kwargs):
        if not isinstance(action_name, str) or not action_name.strip():
            raise ValueError("invoke_mcp_action requires a non-empty action name")
        try:
            json.dumps(kwargs, default=str)
        except Exception as exc:
            raise ValueError(
                f"invoke_mcp_action kwargs are not JSON-serializable: {exc}"
            )
        # Counted, never capped: the count is evidence on the result.
        _rpc["used"] += 1
        rpc_id = _rpc["next_id"]
        _rpc["next_id"] += 1

        _real_stdout.write(json.dumps(
            {"rpc": {"id": rpc_id, "action": action_name, "kwargs": kwargs}},
            default=str,
        ) + "\\n")
        _real_stdout.flush()

        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("rpc channel closed before a reply arrived")
        try:
            reply = json.loads(line)
        except Exception as exc:
            raise RuntimeError(f"rpc protocol error: unreadable reply ({exc})")
        if not isinstance(reply, dict) or reply.get("id") != rpc_id:
            raise RuntimeError(
                f"rpc protocol error: reply does not answer request {rpc_id}"
            )
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply.get("result")

    # Execute the node source to define the function. A failure here (a
    # blocked module-level import, a raised exception) is the node's failure,
    # reported structurally rather than as a bare traceback on stderr.
    namespace["__builtins__"] = __builtins__
    namespace["invoke_mcp_action"] = invoke_mcp_action
    if _ws is not None:
        namespace["ws"] = _ws
    load_error = None
    func = None
    try:
        exec(source_code, namespace)
    except BaseException as e:
        load_error = f"{type(e).__name__}: {e}"

    # Names the runner injected: never mistaken for the node's function.
    _injected = ("invoke_mcp_action", "ws")

    if load_error is None:
        # The node function is `run`, else the last defined callable.
        func = namespace.get("run")
        if func is None:
            for name, obj in reversed(list(namespace.items())):
                if name in _injected:
                    continue
                if callable(obj) and not name.startswith("_"):
                    func = obj
                    break


    def _wants_effects(fn):
        """True when fn takes a second positional argument (the effects map)."""
        try:
            params = list(inspect.signature(fn).parameters.values())
        except (TypeError, ValueError):
            return False
        positional = [
            p for p in params
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) >= 2:
            return True
        if any(p.kind is p.VAR_POSITIONAL for p in params):
            return True
        return any(p.name == "effects" for p in positional)


    if load_error is not None:
        result = {"success": False, "error": load_error}
    elif func is None:
        result = {"success": False, "error": "No callable function found in node source code."}
    else:
        try:
            if _wants_effects(func):
                output = func(input_state, effects)
            else:
                output = func(input_state)
            if not isinstance(output, dict):
                result = {
                    "success": False,
                    "error": f"Node function must return a dict, got {type(output).__name__}",
                }
            else:
                # The full dict goes back: the compiler applies its
                # single-merge-writer guard before dropping undeclared keys.
                undeclared = [k for k in output if k not in output_keys]
                result = {
                    "success": True,
                    "output_state": output,
                    "undeclared": undeclared,
                }
                if undeclared:
                    result["warning"] = f"Undeclared output keys: {undeclared}"
        except BaseException as e:
            result = {"success": False, "error": f"{type(e).__name__}: {e}"}

    # ---- 5. One result object, on the real descriptor ----------------------
    printed = _captured.getvalue()
    if _captured.dropped:
        printed += f"\\n[{_captured.dropped} more characters dropped]"
    result["user_stdout"] = printed

    # Limits that could not be set are a warning here (they are fatal above
    # when the OS sandbox requires them), never silence.
    if _rlimit_failures:
        notice = "resource limits not applied: " + ", ".join(_rlimit_failures)
        existing = result.get("warning") or ""
        result["warning"] = f"{notice}; {existing}" if existing else notice

    try:
        payload = json.dumps(result)
    except BaseException as e:
        payload = json.dumps({
            "success": False,
            "error": f"Node output is not JSON-serializable: {type(e).__name__}: {e}",
            "user_stdout": printed,
        })

    _real_stdout.write(payload + "\\n")
    _real_stdout.flush()
''').replace(
    "MAX_USER_PRINT_BYTES", str(MAX_USER_PRINT_BYTES)
).replace(
    "__RLIMIT_HELPER__", _RLIMIT_HELPER.strip()
).replace(
    "__WORKSPACE_HELPER__", _WORKSPACE_HELPER.strip()
)
