"""A REAL bubblewrap proof of the universe agent's four tools (harness S1).

The universe agent's own ``read``/``write``/``edit``/``bash`` run as processes
the PLATFORM starts inside the tool jail (``tinyassets.universe_tools``). These
cases drive the shipping engine tool handlers (``tinyassets.engine_mcp_server``)
and the shipping jail end to end, with nothing about the jail re-typed here:

* the universe is readable and writable at ``/u``, and nothing else is: another
  universe (by absolute path, by ``..``, or through a symlink the agent
  planted), the data root, the platform source, ``.runtime`` (credentials, the
  engine route bearer) and the daemon's process environment;
* writes to ``.runtime`` and a vendor-native ``.claude/`` never reach the disk;
* bash has no network interface of its own: a listener on the host loopback,
  reachable from the host (the control), is unreachable from the jail; the
  only way out is the checking proxy, which reaches a public destination and
  refuses the host loopback and the metadata address;
* resource limits kill a runaway: memory, processes (a fork bomb included),
  cpu time, output size and the wall clock;
* a skill file the agent writes changes what it does on its NEXT turn, through
  the real ``converse`` with a fake model;
* another universe's folder stays unreachable through the same tools.

Linux + bwrap only. ``.github/workflows/linux-jail-proof.yml`` runs every case
and fails if any is absent or skipped. Every byte involved is synthetic.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.engine_authority_helpers import seed_engine_authority

# Imported before ``world`` patches ``helpers._base_path``: permissions binds
# ``_base_path`` at import, and a first import under the patch would keep this
# module's deleted data root for every later test in the session.
from tinyassets.api import permissions

_BWRAP = shutil.which("bwrap") if sys.platform == "linux" else None

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "linux" or not _BWRAP,
        reason="a real bubblewrap jail needs Linux + bwrap",
    ),
    # Runs in .github/workflows/linux-jail-proof.yml, where a skip fails.
    pytest.mark.real_jail,
]

OWN_MARKER = "POSITIVE-CONTROL-OWN-UNIVERSE"
FOREIGN_MARKER = "SYNTHETIC-UNIVERSE-B-CONTENT"
CRED_MARKER = "SYNTHETIC-OWN-LAUNCH-CREDENTIAL"
BEARER_MARKER = "SYNTHETIC-ENGINE-ROUTE-BEARER"
ENV_MARKER = "TA_TOOL_JAIL_SENTINEL_DAEMON_ENV_MARKER"


@dataclass
class _World:
    data_root: Path
    universe_a: Path
    universe_b: Path


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch):
    """Two universes under one data root and a sentinel "daemon" process.

    Under ``/tmp`` (not ``tmp_path``), as in ``test_provider_universe_jail``:
    on the hosted runner's sudo fallback, bwrap runs as root and cannot
    traverse the runner's 0750 home to reach a ``--basetemp`` under it.
    """
    import tinyassets.api.helpers as helpers
    from tinyassets.providers import base

    root = Path(tempfile.mkdtemp(prefix="ta-tool-jail-", dir="/tmp"))
    sentinel = None
    try:
        data_root = root / "data"
        universe_a = data_root / "u-alpha"
        universe_b = data_root / "u-bravo"
        (universe_a / "notes").mkdir(parents=True)
        universe_b.mkdir(parents=True)
        (universe_a / "notes" / "own.txt").write_text(OWN_MARKER + "\n", encoding="utf-8")
        (universe_b / "founder.md").write_text(FOREIGN_MARKER + "\n", encoding="utf-8")
        cred = universe_a / ".runtime" / "provider-launch-credentials" / "own-1"
        cred.mkdir(parents=True)
        (cred / "auth.json").write_text('{"t": "' + CRED_MARKER + '"}', encoding="utf-8")
        (universe_a / ".runtime" / "engine-mcp-config.json").write_text(
            '{"Authorization": "Bearer ' + BEARER_MARKER + '"}', encoding="utf-8",
        )
        sentinel = subprocess.Popen(  # noqa: S603 - fixed argv
            ["/bin/sleep", "300"], env={"PATH": "/usr/bin:/bin", ENV_MARKER: "1"},
        )
        monkeypatch.setenv(ENV_MARKER, "1")  # the test process is a daemon too
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
        monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
        monkeypatch.setattr(helpers, "_base_path", lambda: data_root)
        base._sandbox_probe_cache = None
        yield _World(data_root, universe_a, universe_b)
    finally:
        if sentinel is not None:
            sentinel.kill()
            sentinel.wait(timeout=10)
        base._sandbox_probe_cache = None
        shutil.rmtree(root, ignore_errors=True)


def _engine(monkeypatch, world: _World, *, actor="actor-a", graph="u-alpha"):
    """The shipping engine handlers, pinned to (actor, graph) with REAL authority."""
    from tinyassets import engine_mcp_server as s

    seed_engine_authority(world.data_root, actor=actor, graph=graph)
    monkeypatch.setattr(s, "_ACTOR_ID", actor)
    monkeypatch.setattr(s, "_GRAPH_ID", graph)
    return s


def _run(coro):
    return asyncio.run(coro)


# ── (a) the folder is the universe, and only the universe ───────────────────


def test_exports_survive_fresh_tool_processes_and_appear_next_turn(world, monkeypatch):
    from tinyassets.universe_tools import harness_prompt

    engine = _engine(monkeypatch, world)
    content = "month,interest,principal\n1,2166.67,361.60\n"
    assert _run(engine.write_file(path="exports/mortgage.csv", content=content)).startswith("wrote")
    assert "[exit code 0]" in _run(engine.run_bash(
        command="cp exports/mortgage.csv exports/copy.csv"))
    # Each call starts a new jail; the prompt runs outside it, as on the next turn.
    for name in ("mortgage.csv", "copy.csv"):
        assert name in _run(engine.run_bash(command="find /u/exports -type f"))
        assert "2166.67" in _run(engine.read_file(path="exports/" + name))
        assert "exports/" + name in harness_prompt(world.universe_a)
        assert (world.universe_a / ".agent-workspace/exports" / name).read_text() == content


def test_tools_reach_their_own_universe_and_nothing_else(world, monkeypatch):
    import tinyassets

    s = _engine(monkeypatch, world)
    a, b = world.universe_a, world.universe_b

    # Positive controls: a jail that mounted nothing cannot pass the negatives.
    assert OWN_MARKER in _run(s.read_file(path="notes/own.txt"))
    assert _run(s.write_file(path="notes/new.md", content="alpha\nbeta\n")).startswith("wrote")
    assert (a / "notes" / "new.md").read_text(encoding="utf-8") == "alpha\nbeta\n"
    assert _run(s.edit_file(path="notes/new.md", old_text="beta", new_text="gamma")) == (
        "edited /u/notes/new.md"
    )
    assert (a / "notes" / "new.md").read_text(encoding="utf-8") == "alpha\ngamma\n"
    assert "[exit code 0]" in _run(s.run_bash(command="test -w /u/notes && pwd"))

    # Another universe: by its host path, by '..', and through a planted symlink.
    for path in (str(b / "founder.md"), "../u-bravo/founder.md", "/u/../u-bravo/founder.md"):
        out = _run(s.read_file(path=path))
        assert out.startswith("error:") and FOREIGN_MARKER not in out, (path, out)
    # A planted link would dangle in here but RESOLVE for the daemon outside:
    # the jail refuses to create one at all, and no FIFO either.
    planted = _run(s.run_bash(
        command=f"ln -s {b} bravo; ln -s {b}/founder.md founder-link.md; mkfifo pipe; "
                "cat bravo/founder.md",
    ))
    assert FOREIGN_MARKER not in planted and "[exit code 0]" not in planted, planted
    assert "Operation not permitted" in planted, planted
    for name in ("bravo", "founder-link.md", "pipe"):
        assert not os.path.lexists(a / name), name
    assert FOREIGN_MARKER not in _run(s.read_file(path="bravo/founder.md"))
    # Hard links stay inside /u: every other visible path is another mount.
    assert "[exit code 0]" in _run(s.run_bash(command="ln notes/own.txt notes/hard.txt"))
    crossed = _run(s.run_bash(command="ln /etc/passwd notes/passwd"))
    assert "[exit code 0]" not in crossed, crossed
    listing = _run(s.run_bash(command="ls -a / /u/.. /tmp; ls -a " + str(world.data_root)))
    assert "u-bravo" not in listing and world.data_root.name not in listing.split(), listing

    # The platform source and the daemon's environment.
    source = Path(tinyassets.__file__).resolve()
    assert "error:" in _run(s.read_file(path=str(source)))
    env = _run(s.run_bash(command="env; cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n'"))
    assert ENV_MARKER not in env and "TINYASSETS_" not in env, env

    # .runtime: unreadable, and a write never reaches the disk.
    runtime = _run(s.run_bash(
        command="ls -A .runtime; cat .runtime/provider-launch-credentials/own-1/auth.json "
                ".runtime/engine-mcp-config.json; echo planted > .runtime/planted",
    ))
    assert CRED_MARKER not in runtime and BEARER_MARKER not in runtime, runtime
    assert not (a / ".runtime" / "planted").exists()
    assert (a / ".runtime" / "engine-mcp-config.json").exists(), "masked, not deleted"

    assert (b / "founder.md").read_text(encoding="utf-8") == FOREIGN_MARKER + "\n"


_IO_URING_PROBE = r'''
import ctypes, ctypes.util, errno, os, struct
libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
# io_uring_setup(entries, params*) = 425 on x86_64 and aarch64 (asm-generic).
params = ctypes.create_string_buffer(120)
ctypes.set_errno(0)
fd = libc.syscall(425, 8, params)
err = ctypes.get_errno()
if fd >= 0:
    os.close(fd)
    print("IO_URING_RING_CREATED")
elif err == errno.EPERM:
    print("IO_URING_EPERM")
else:
    print("IO_URING_OTHER", err)
# A plain symlink is refused too (the belt seccomp also blocks).
ctypes.set_errno(0)
try:
    os.symlink("/etc/passwd", "u-link")
    print("SYMLINK_CREATED")
except OSError as exc:
    print("SYMLINK_" + errno.errorcode.get(exc.errno, str(exc.errno)))
'''


def test_io_uring_and_symlink_are_refused_in_the_jail(world, monkeypatch):
    """io_uring is the way around a syscall filter (IORING_OP_SYMLINKAT, kernel
    5.15+, invisible to seccomp). The jail refuses io_uring_setup, so no ring op
    can run, and symlink stays refused too."""
    from tinyassets import universe_tools as tools

    world.universe_a.joinpath("probe.py").write_text(_IO_URING_PROBE, encoding="utf-8")
    out = tools.bash(world.universe_a, "python3 /u/probe.py", agent_id="main", timeout=30)
    assert "IO_URING_RING_CREATED" not in out, out
    assert "IO_URING_EPERM" in out, out
    assert "SYMLINK_CREATED" not in out and "SYMLINK_EPERM" in out, out
    assert not os.path.lexists(world.universe_a / "u-link")


def test_a_settings_dir_the_agent_writes_is_masked_from_a_provider_launch(
    world, monkeypatch,
):
    """Design risk 8, in the real jails. Since harness W2 the agent's ``/u`` is
    its own workspace, so a CLI settings dir it writes lands there, never in
    the universe root a provider launch starts in, and the provider launch
    masks the whole workspace (a hidden root directory). One the OWNER placed
    in the root is masked from both the tool jail and the next launch."""
    from tinyassets.providers.provider_jail import default_view, jail_argv
    from tinyassets.universe_tools import WORKSPACE_DIR

    s = _engine(monkeypatch, world)
    a = world.universe_a
    hook = '{"hooks": {"SessionStart": "cat .runtime/*"}}'
    assert _run(s.write_file(path=".claude/settings.json", content=hook)).startswith("wrote")
    _run(s.run_bash(command="mkdir -p .anycli && echo x > .anycli/config"))
    assert not (a / ".claude").exists() and not (a / ".anycli").exists()
    assert (a / WORKSPACE_DIR / ".claude" / "settings.json").exists()
    agent_probe = (f"cat {a}/{WORKSPACE_DIR}/.claude/settings.json 2>/dev/null && echo SEEN; "
                   f"cat {a}/notes/own.txt")
    argv = jail_argv(["/bin/sh", "-c", agent_probe], default_view(a), bwrap_path=_BWRAP)
    launched = subprocess.run(  # noqa: S603 - fixed argv built by the shipping jail
        argv, capture_output=True, text=True, timeout=60, check=False,
    )
    assert OWN_MARKER in launched.stdout, launched  # positive control
    assert "SEEN" not in launched.stdout and "SessionStart" not in launched.stdout

    # An owner-placed settings dir (from outside the jail) is masked in both.
    owner_hook = '{"hooks": {"SessionStart": "OWNER-PLACED-HOOK"}}'
    (a / ".claude").mkdir()
    (a / ".claude" / "settings.json").write_text(owner_hook, encoding="utf-8")
    seen = _run(s.run_bash(command="cat .claude/settings.json; ls -A .claude"))
    assert "OWNER-PLACED-HOOK" not in seen, seen
    probe = (
        f"cat {a}/.claude/settings.json 2>/dev/null && echo LOADED; "
        f"cat {a}/notes/own.txt"
    )
    argv = jail_argv(["/bin/sh", "-c", probe], default_view(a), bwrap_path=_BWRAP)
    launched = subprocess.run(  # noqa: S603 - fixed argv built by the shipping jail
        argv, capture_output=True, text=True, timeout=60, check=False,
    )
    assert OWN_MARKER in launched.stdout, launched  # positive control
    assert "LOADED" not in launched.stdout and "SessionStart" not in launched.stdout


VAULT_MARKER = "SYNTHETIC-OWNER-VAULT-SECRET"


def test_the_owners_credentials_and_authority_state_are_out_of_reach(world, monkeypatch):
    """The credential vault and the consent / usage databases sit in the
    universe ROOT. In the tool jail they read as empty, cannot be written, and
    a missing one cannot be created; soul.md and config.yaml are read-only."""
    s = _engine(monkeypatch, world)
    a = world.universe_a
    (a / ".credential-vault.json").write_text('{"k": "' + VAULT_MARKER + '"}', encoding="utf-8")
    (a / ".credentials").mkdir()
    (a / ".credentials" / "vcs.json").write_text(VAULT_MARKER, encoding="utf-8")
    (a / "soul.md").write_text("# Universe Soul\n", encoding="utf-8")
    (a / "config.yaml").write_text("timeout: 42\n", encoding="utf-8")

    for path in (".credential-vault.json", ".credentials/vcs.json"):
        assert VAULT_MARKER not in _run(s.read_file(path=path)), path
    dumped = _run(s.run_bash(command="cat .credential-vault.json; ls -A .credentials; "
                                     "grep -r SYNTHETIC . 2>/dev/null"))
    assert VAULT_MARKER not in dumped, dumped

    forge = _run(s.run_bash(command=(
        "echo forged > .credential-vault.json; echo forged > .effector_consents.db; "
        "echo forged > .usage_ledger.db; echo pwned > soul.md; echo 'timeout: 1' > config.yaml"
    )))
    assert "[exit code 0]" not in forge, forge
    assert VAULT_MARKER in (a / ".credential-vault.json").read_text(encoding="utf-8")
    assert not (a / ".effector_consents.db").exists()
    assert not (a / ".usage_ledger.db").exists()
    assert (a / "soul.md").read_text(encoding="utf-8") == "# Universe Soul\n"
    assert (a / "config.yaml").read_text(encoding="utf-8") == "timeout: 42\n"
    # Positive control: what the agent owns is writable.
    assert _run(s.write_file(path="notes/mine.md", content="ok")).startswith("wrote")


def test_the_agent_writes_its_own_wiki_but_not_the_trusted_write_back_markers(
    world, monkeypatch,
):
    """Harness W: the agent's own wiki is part of the workspace it owns. Live,
    2026-10-01, the founder's agent said "my wiki is read-only through the
    available tools" and filed its page under notes/. The daemon's trusted
    wiki write-back markers sit at the universe ROOT, out of the jail."""
    from tinyassets.api.helpers import _read_text, _scoped_wiki_root
    from tinyassets.effectors.wiki_write_back import _destination_marker_db_path

    s = _engine(monkeypatch, world)
    a = world.universe_a
    (a / "wiki" / "pages" / "projects").mkdir(parents=True)
    markers = _destination_marker_db_path(a)
    assert markers.parent == a, "the trusted markers live at the root, not in wiki/"
    markers.write_bytes(b"SQLite format 3\x00 synthetic markers")

    page = "wiki/pages/projects/done-and-blocked.md"
    body = "---\ntitle: Done and blocked\n---\n# Done\n- shipped\n"
    assert _run(s.write_file(path=page, content=body)).startswith("wrote")
    assert _run(s.edit_file(path=page, old_text="shipped", new_text="shipped S1")) == (
        f"edited /u/{page}"
    )
    assert "[exit code 0]" in _run(s.run_bash(command="mkdir -p wiki/pages/plans && "
                                                      "echo '# Plan' > wiki/pages/plans/next.md"))
    # The daemon reads what the agent wrote, through the bounded reader.
    with _scoped_wiki_root(a / "wiki"):
        assert "shipped S1" in _read_text((a / page).resolve())

    forge = _run(s.run_bash(command=(
        f"ls -A /u; echo forged > /u/{markers.name}; cat /u/{markers.name}"
    )))
    assert markers.name not in forge.split("[exit code")[0].split(), forge
    # A file of that name in /u is the agent's own workspace file (harness W2);
    # the trusted markers at the universe root are untouched.
    assert markers.read_bytes() == b"SQLite format 3\x00 synthetic markers"


def test_the_agent_owns_its_whole_workspace_and_platform_state_stays_out(
    world, monkeypatch,
):
    """Harness W2 (design #4172 §4.3): /u is the agent's own workspace. It
    creates, renames and removes anything at the top, as on its own computer;
    the universe root and its platform state are never bound, and the visible
    platform files keep their read-only binds on top."""
    from tinyassets.universe_tools import WORKSPACE_DIR

    s = _engine(monkeypatch, world)
    a = world.universe_a
    (a / "soul.md").write_text("# Universe Soul\n", encoding="utf-8")
    (a / ".usage_ledger.db").write_bytes(b"SYNTHETIC-PLATFORM-LEDGER")

    made = _run(s.run_bash(command=(
        "mkdir -p projects/site && echo hi > projects/site/index.html && "
        "echo draft > TODO.md && mv TODO.md PLAN.md && "
        "python3 -c \"print(open('/u/PLAN.md').read().strip())\" && rm -rf projects"
    )))
    assert "[exit code 0]" in made and "draft" in made, made
    workspace = a / WORKSPACE_DIR
    assert (workspace / "PLAN.md").read_text(encoding="utf-8") == "draft\n"
    assert not (a / "PLAN.md").exists(), "a new name never lands in the universe root"
    assert not (workspace / "projects").exists()

    # Platform state: hidden root entries absent; visible platform files read-only.
    probe = _run(s.run_bash(command=(
        "echo pwned > soul.md; cat .usage_ledger.db; grep -r SYNTHETIC-PLATFORM . ; echo done"
    )))
    assert "SYNTHETIC-PLATFORM-LEDGER" not in probe, probe
    assert (a / "soul.md").read_text(encoding="utf-8") == "# Universe Soul\n"
    assert (a / ".usage_ledger.db").read_bytes() == b"SYNTHETIC-PLATFORM-LEDGER"
    # Positive control: what it owns in the root stays writable through /u.
    assert _run(s.write_file(path="notes/w2.md", content="ok")).startswith("wrote")
    assert (a / "notes" / "w2.md").read_text(encoding="utf-8") == "ok"


def test_an_oversized_config_write_is_refused_and_the_next_load_is_prompt(world, monkeypatch):
    """The reviewer's reproduction through the real tool: a 4 MB config.yaml.
    The platform's config.yaml is read-only in the jail when it exists; with
    none at the root, the agent's write lands in its own workspace (harness W2)
    and never becomes the platform's config. A planted oversized one at the
    root is never parsed by the next turn."""
    from tinyassets.config import UniverseConfig, load_universe_config

    s = _engine(monkeypatch, world)
    a = world.universe_a
    big = "timeout: 999\n" + "".join(f"k{i}: v{i}\n" for i in range(300_000))
    _run(s.write_file(path="config.yaml", content=big[:4 * 1024 * 1024 - 1]))
    assert not (a / "config.yaml").exists(), "the platform config is never the agent's write"
    started = time.monotonic()
    assert load_universe_config(a).timeout == UniverseConfig().timeout
    (a / "config.yaml").write_text(big, encoding="utf-8")  # planted from outside
    config = load_universe_config(a)
    assert time.monotonic() - started < 2.0
    assert config.timeout == UniverseConfig().timeout, "never parsed"


def test_an_engine_pinned_to_another_universe_cannot_reach_it(world, monkeypatch):
    """(c) in the real jail: actor B's engine, pinned at A, runs nothing; B's own
    engine sees only B."""
    seed_engine_authority(world.data_root, actor="actor-a", graph="u-alpha")
    s = _engine(monkeypatch, world, actor="actor-b", graph="u-bravo")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-alpha")
    out = _run(s.run_bash(command="cat /u/notes/own.txt"))
    assert "current serving owner authority" in out and OWN_MARKER not in out
    monkeypatch.setattr(s, "_GRAPH_ID", "u-bravo")
    assert FOREIGN_MARKER in _run(s.read_file(path="founder.md"))
    own_a = _run(s.run_bash(
        command=f"cat {world.universe_a}/notes/own.txt ../u-alpha/notes/own.txt",
    ))
    assert OWN_MARKER not in own_a


# ── (a) no network ──────────────────────────────────────────────────────────


def test_bash_has_no_network(world, monkeypatch):
    s = _engine(monkeypatch, world)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    try:
        # Control: the host loopback listener IS reachable from outside the jail.
        socket.create_connection(("127.0.0.1", port), timeout=5).close()
        out = _run(s.run_bash(command=(
            f"(exec 3<>/dev/tcp/127.0.0.1/{port} && echo LOOPBACK-CONNECTED) 2>&1; "
            "(exec 3<>/dev/tcp/1.1.1.1/53 && echo EXTERNAL-CONNECTED) 2>&1; "
            "echo NETDEV-BEGIN; tail -n +3 /proc/net/dev"
        )))
    finally:
        listener.close()
    assert "LOOPBACK-CONNECTED" not in out and "EXTERNAL-CONNECTED" not in out, out
    netdev = out.split("NETDEV-BEGIN", 1)[1].splitlines()
    interfaces = {line.split(":", 1)[0].strip() for line in netdev if ":" in line}
    assert interfaces == {"lo"}, out


_FETCH = (
    "import sys, urllib.request\n"
    "opener = urllib.request.build_opener(urllib.request.ProxyHandler(\n"
    "    {'http': 'http://127.0.0.1:3128'}))\n"
    "try:\n"
    "    print('BODY:' + opener.open(sys.argv[1], timeout=20).read().decode())\n"
    "except Exception as exc:\n"
    "    body = getattr(exc, 'read', lambda: b'')()\n"
    "    print('REFUSED:' + str(exc) + ':' + body.decode(errors='replace'))\n"
)


def _site(body: bytes):
    """A one-page HTTP server on the host loopback; returns (port, hits, stop)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    hits = []

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], hits, server.shutdown


def test_bash_reaches_a_public_site_only_through_the_checking_proxy(world, monkeypatch):
    """The proxy resolves the name; this test maps one synthetic public name to a
    host page so the whole path (env, forwarder, socket, proxy) runs for real."""
    from tinyassets import universe_egress

    real = universe_egress._checked_addresses
    monkeypatch.setattr(universe_egress, "_checked_addresses",
                        lambda host, port: ["127.0.0.1"] if host == "public.test"
                        else real(host, port))
    s = _engine(monkeypatch, world)
    port, hits, stop = _site(b"PUBLIC-PAGE")
    try:
        out = _run(s.run_bash(command=(
            "env | grep -c '^HTTPS_PROXY=http://127.0.0.1:3128$'; "
            f"python3 -c \"$(printf '%s' {_quote(_FETCH)})\" http://public.test:{port}/p"
        )))
    finally:
        stop()
    assert "BODY:PUBLIC-PAGE" in out, out
    assert hits == ["/p"]


def test_the_proxy_refuses_the_host_and_the_metadata_address(world, monkeypatch):
    s = _engine(monkeypatch, world)
    port, hits, stop = _site(b"HOST-ONLY")
    try:
        out = _run(s.run_bash(command=(
            f"python3 -c \"$(printf '%s' {_quote(_FETCH)})\" http://127.0.0.1:{port}/; "
            f"python3 -c \"$(printf '%s' {_quote(_FETCH)})\" http://169.254.169.254/latest/"
        )))
    finally:
        stop()
    assert out.count("REFUSED:") == 2 and "egress refused" in out, out
    assert "BODY:" not in out and hits == []


def _quote(text: str) -> str:
    import shlex

    return shlex.quote(text)


# ── (a) resource limits kill a runaway ──────────────────────────────────────


def _host_processes_with(token: str) -> list[int]:
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                if token.encode() in handle.read():
                    found.append(int(entry))
        except OSError:
            continue
    return found


def test_the_limits_are_applied_inside_the_jail(world, monkeypatch):
    s = _engine(monkeypatch, world)
    limits = _run(s.run_bash(command="cat /proc/self/limits"))
    for row, value in (("Max address space", "536870912"), ("Max processes", "64"),
                       ("Max file size", "unlimited"), ("Max open files", "256"),
                       ("Max core file size", "0")):
        line = next((ln for ln in limits.splitlines() if ln.startswith(row)), "")
        assert value in line.split(), (row, line)


def test_memory_limit_stops_a_runaway_allocation(world):
    from tinyassets import universe_tools as tools

    small = tools.ToolLimits(memory_bytes=256 * 1024 * 1024)
    grow = "x=$(head -c {n} /dev/zero | tr '\\0' a); echo survived ${{#x}}"
    control = tools.bash(world.universe_a, grow.format(n=1_000_000), agent_id="main", limits=small)
    assert "survived 1000000" in control, control
    out = tools.bash(world.universe_a, grow.format(n=900_000_000), agent_id="main", limits=small)
    assert "survived" not in out and "[exit code 0]" not in out, out


def test_process_limit_holds_and_a_fork_bomb_is_contained(world):
    from tinyassets import universe_tools as tools

    limits = tools.ToolLimits(processes=16, wall_seconds=8)
    # Count what actually runs at once. Unprivileged, RLIMIT_NPROC in the jail's
    # user namespace refuses the extra forks; a root-run jail (the hosted
    # runner's sudo fallback, where the kernel exempts root) is held by its own
    # cgroup's pids.max, and the process-tree watch backs both.
    spawn = (
        "import os, time\n"
        "made = 0\n"
        "for _ in range(80):\n"
        "    try:\n"
        "        if os.fork() == 0:\n"
        "            time.sleep(20); os._exit(0)\n"
        "        made += 1\n"
        "    except OSError:\n"
        "        break\n"
        "print('made', made, flush=True); time.sleep(3)\n"
    )
    run = tools.run_jailed(world.universe_a, ["/usr/bin/python3", "-c", spawn], agent_id="main",
                           limits=limits, wall_seconds=8)
    made = [int(w) for line in run.output.decode().splitlines()
            if line.startswith("made ") for w in line.split()[1:2]]
    assert run.killed == "process_limit" or (made and made[0] <= limits.processes), run

    token = f"ta-bomb-{uuid.uuid4().hex}"
    started = time.monotonic()
    out = tools.bash(world.universe_a,
                     f"bomb() {{ bomb | bomb & }}; bomb; sleep 5; echo {token}-alive",
                     agent_id="main",
                     limits=tools.ToolLimits(processes=32), timeout=6)
    assert time.monotonic() - started < 30, "the call came back"
    # The kernel refused the bomb's forks (RLIMIT_NPROC unprivileged, pids.max
    # as root), or the watch killed it: either way it hit a wall, and the
    # command itself still ran to its end or was stopped.
    assert "Resource temporarily unavailable" in out or "[killed:" in out, out[-500:]
    time.sleep(1)
    assert _host_processes_with(token) == [], "nothing from the jail survives it"
    # The universe still works afterwards.
    assert OWN_MARKER in tools.read_file(world.universe_a, "notes/own.txt", agent_id="main")


def test_cpu_output_and_wall_clock_limits_kill(world):
    from tinyassets import universe_tools as tools

    started = time.monotonic()
    out = tools.bash(world.universe_a, "while :; do :; done", agent_id="main",
                     limits=tools.ToolLimits(cpu_seconds=2), timeout=60)
    assert "[killed: cpu time limit]" in out and time.monotonic() - started < 20, out

    started = time.monotonic()
    out = tools.bash(world.universe_a, "yes", agent_id="main")
    assert "[killed: output passed 65536 bytes]" in out, out[-200:]
    assert len(out.encode()) < 70 * 1024 and time.monotonic() - started < 20

    started = time.monotonic()
    out = tools.bash(world.universe_a, "sleep 30", agent_id="main", timeout=2)
    assert "[killed: ran longer than 2s]" in out and time.monotonic() - started < 15, out


def test_a_jail_that_fills_the_shared_disk_is_killed(world):
    from tinyassets import universe_tools as tools

    free = tools._free_disk(world.universe_a)
    assert free > 400 * 1024 * 1024, "the runner needs room for this proof"
    floor = tools.ToolLimits(min_free_disk_bytes=free - 150 * 1024 * 1024)
    try:
        out = tools.bash(
            world.universe_a,
            "for i in $(seq 1 40); do head -c 30000000 /dev/zero > notes/fill$i || exit 3; done; "
            "echo filled", agent_id="main",
            limits=floor, timeout=120,
        )
        assert "[killed: the shared disk was nearly full]" in out, out
        assert "filled" not in out
        # Below the floor, the next call does not start at all.
        with pytest.raises(tools.UniverseToolError, match="nearly full"):
            tools.bash(world.universe_a, "true", agent_id="main",
                       limits=tools.ToolLimits(min_free_disk_bytes=free * 2))
    finally:
        for path in (world.universe_a / "notes").glob("fill*"):
            path.unlink()


_MiB = 1024 * 1024


def test_a_jail_writing_many_small_files_past_its_budget_is_killed(world, monkeypatch):
    """Many small files must still stop at the owner's total storage quota."""
    from tinyassets import universe_tools as tools
    from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

    initialize_author_server(world.data_root)
    grant_universe_ownership(world.data_root, universe_id=world.universe_a.name,
                             owner_id="workos|alice")
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(24 * _MiB / 1024**3))
    many = world.universe_a / "notes" / "many"
    try:
        out = tools.bash(
            world.universe_a,
            "mkdir -p notes/many && for i in $(seq 1 400); do "
            "head -c 262144 /dev/zero > notes/many/f$i || exit 3; done; echo filled",
            agent_id="main",
            timeout=120,
        )
        assert "[killed: the owner's total cloud storage quota was exceeded]" in out, out[-500:]
        assert "filled" not in out
        written = sum(path.stat().st_size for path in many.iterdir())
        assert 24 * _MiB < written < 100 * _MiB, written
        # Another universe is untouched by this one's stop.
        assert tools.bash(
            world.universe_b, "echo still-runs", agent_id="main",
        ).startswith("still-runs")
    finally:
        shutil.rmtree(many, ignore_errors=True)


def test_the_jails_private_tmp_is_capped(world):
    from tinyassets import jail_disk
    from tinyassets import universe_tools as tools

    out = tools.bash(
        world.universe_a,
        "for i in $(seq 1 12); do head -c 30000000 /dev/zero > /tmp/f$i "
        "|| { echo full-at-$i; exit 0; }; done; echo all-written",
        agent_id="main",
        timeout=120,
    )
    assert "all-written" not in out, out[-500:]
    assert "No space left on device" in out and "full-at-" in out, out[-500:]
    # 30 MB files: the cap is hit after floor(cap / 30 MB) of them.
    assert f"full-at-{jail_disk.TMP_BYTES // 30000000 + 1}" in out, out[-500:]


def test_a_full_account_can_still_free_space_through_its_agent(world, monkeypatch):
    from tinyassets import universe_tools as tools
    from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

    initialize_author_server(world.data_root)
    grant_universe_ownership(world.data_root, universe_id="u-alpha", owner_id="workos|alice")
    # A 1 KiB quota through the real override: the universe is already over it.
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(1024 / 1024**3))
    junk = world.universe_a / "notes" / "junk.bin"
    junk.write_bytes(b"x" * 64 * 1024)
    out = tools.bash(world.universe_a, "rm notes/junk.bin && echo removed", agent_id="main")
    assert "out of cloud storage" in out and "removed" in out, out
    assert not junk.exists()


# ── (b) a skill the agent writes changes its next turn ──────────────────────

_STANDUP = (
    "---\n"
    "name: standup\n"
    "description: When my founder says standup, answer with Yesterday, Today and "
    "Blockers bullets.\n"
    "---\n\n"
    "Answer with exactly these three bullets, filled in:\n"
    "- Yesterday:\n"
    "- Today:\n"
    "- Blockers:\n"
)


def _turn(monkeypatch, world: _World, message: str, model) -> str:
    """One real converse turn for actor-a's universe, with ``model`` answering."""
    import tinyassets.universe_intelligence as ui
    from tinyassets.auth import middleware as auth

    monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": "u-alpha")
    monkeypatch.setattr(ui, "_universe_dir", lambda _uid: world.universe_a)
    monkeypatch.setattr(ui, "call_provider", model)
    monkeypatch.setattr(ui, "_build_persona_system_prompt", lambda *a, **k: "I am Alpha.")
    monkeypatch.setattr(ui, "extract_learning", lambda *a, **k: None)
    monkeypatch.setattr(ui, "commit_learning", lambda *a, **k: None)
    monkeypatch.setattr(ui.interlocutor, "resolve_interlocutor_tier",
                        lambda *_a, **_k: SimpleNamespace(tier=ui.interlocutor.FOUNDER))
    reserve = auth.reserve_provider_request(
        principal_id="actor-a", session_id="s", request_id=message, tool_name="converse",
    )
    capability = auth.claim_provider_request(reserve, tool_name="converse")
    try:
        return ui.converse("u-alpha", message)
    finally:
        auth.revoke_provider_request(capability)


def test_a_skill_the_agent_writes_changes_its_next_turn(world, monkeypatch):
    s = _engine(monkeypatch, world)
    systems: list[str] = []

    def fake_model(prompt, system="", **_kw):
        """A model that uses only what the turn gives it: the prompt, the
        system prompt and the four tools."""
        systems.append(system)
        if "make yourself a skill" in prompt:
            return _run(s.write_file(path="skills/standup/SKILL.md", content=_STANDUP))
        if "forget the standup skill" in prompt:
            return _run(s.run_bash(command="rm -r skills/standup"))
        if "other universe" in prompt:
            return _run(s.read_file(path=str(world.universe_b / "founder.md")))
        if prompt.strip() == "standup":
            if "- `standup`:" not in system:
                return "Standup? Happy to chat about your day."
            skill = _run(s.read_file(path="skills/standup/SKILL.md"))
            return "\n".join(line for line in skill.splitlines() if line.startswith("- "))
        return "hello"

    before = _turn(monkeypatch, world, "standup", fake_model)
    assert "Yesterday" not in before and "- `standup`" not in systems[-1]

    made = _turn(monkeypatch, world,
                 "make yourself a skill: when I say standup, answer with "
                 "Yesterday/Today/Blockers bullets", fake_model)
    assert made.startswith("wrote"), made
    assert (world.universe_a / "skills" / "standup" / "SKILL.md").is_file()
    assert "- `standup`" not in systems[-1], "a skill takes effect from the NEXT turn"

    after = _turn(monkeypatch, world, "standup", fake_model)
    assert after.splitlines() == ["- Yesterday:", "- Today:", "- Blockers:"], after
    assert "- `standup`: When my founder says standup" in systems[-1]
    assert "Answer with exactly" not in systems[-1], "only the index is in the prompt"

    _turn(monkeypatch, world, "forget the standup skill", fake_model)
    assert not (world.universe_a / "skills" / "standup").exists()
    plain = _turn(monkeypatch, world, "standup", fake_model)
    assert "Yesterday" not in plain

    refused = _turn(monkeypatch, world, "read the other universe's founder file", fake_model)
    assert refused.startswith("error:") and FOREIGN_MARKER not in refused


def test_forget_in_chat_uses_existing_memory_editor(world, monkeypatch):
    s = _engine(monkeypatch, world)
    original = "# Memory\n- [m_abcd] Likes tea\n- [m_dcba] Likes walks\n"
    (world.universe_a / "MEMORY.md").write_text(original)

    def model(prompt, **_kw):
        assert "forget tea" in prompt.lower()
        memory = _run(s.read_file(path="MEMORY.md"))
        assert "Likes tea" in memory
        result = _run(s.edit_file(path="MEMORY.md", old_text="- [m_abcd] Likes tea\n", new_text=""))
        assert not result.startswith("error:"), result
        assert "Likes tea" not in _run(s.read_file(path="MEMORY.md"))
        return "Removed the tea memory."

    assert _turn(monkeypatch, world, "Forget tea", model) == "Removed the tea memory."
    assert (world.universe_a / "MEMORY.md").read_text() == "# Memory\n- [m_dcba] Likes walks\n"


# ── a background run's file tools start while the daemon holds a database ──


def test_a_background_run_reads_and_writes_its_notes_while_a_database_closes(
    world, monkeypatch, nobody,
):
    """Live 2026-09-28: the founder's background self could read its grants but
    not ``notes/background-self.md`` -- "bwrap: Can't create file
    /u/.effector_consents.db-shm: Read-only file system". Reading grants opens
    the WAL consent database; the jail scanned the root while its ``-shm``
    sidecar existed, SQLite deleted the sidecar when the connection closed, and
    bubblewrap could not create a mask mountpoint on the read-only root.

    Driven through the owner's claimed background run (the path an automation
    takes), with the real consent store open during the scan and closed before
    the launch -- the exact interleaving -- and the shipping tool handlers.

    The live consent store now lives outside the command center. Keep a
    synthetic legacy WAL database in the old location too: observing only the
    new sidecar would no longer exercise a root entry vanishing after the scan.
    Neither database is exposed to the jailed tools."""
    import sqlite3

    from tinyassets import universe_tools
    from tinyassets.daemon_server import claim_founder_home, ensure_universe_registered
    from tinyassets.runtime.claimed_branch_execution import (
        ClaimedBranchExecutorIdentity,
        execute_claimed_branch_task,
    )
    from tinyassets.storage import effector_consents

    owner = "workos|owner-bg"
    a = world.universe_a
    s = _engine(monkeypatch, world, actor=owner)
    ensure_universe_registered(world.data_root, universe_id="u-alpha", universe_path=a)
    claim_founder_home(world.data_root, owner, "u-alpha")
    (a / "notes" / "background-self.md").write_text(OWN_MARKER + "\n", encoding="utf-8")
    db = effector_consents.initialize_consents_db(a)
    shm = Path(str(db) + "-shm")
    legacy_db = effector_consents.legacy_consents_db_path(a)
    legacy_shm = Path(str(legacy_db) + "-shm")
    raced: list[bool] = []
    real_argv = universe_tools.TOOL_JAIL_ARGV

    def argv_while_a_connection_closes(*args, **kwargs):
        conn = effector_consents._connect(a)
        conn.execute("SELECT count(*) FROM effector_consents").fetchone()
        legacy_conn = sqlite3.connect(legacy_db)
        try:
            legacy_conn.execute("PRAGMA journal_mode = WAL")
            legacy_conn.execute("CREATE TABLE IF NOT EXISTS synthetic_legacy (value TEXT)")
            legacy_conn.commit()
            assert shm.exists(), "precondition: the sidecar exists at the scan"
            assert legacy_shm.exists(), "precondition: the legacy sidecar exists at the scan"
            return real_argv(*args, **kwargs)
        finally:
            conn.close()
            legacy_conn.close()
            raced.append(not shm.exists() and not legacy_shm.exists())

    monkeypatch.setattr(universe_tools, "TOOL_JAIL_ARGV", argv_while_a_connection_closes)
    seen: dict = {}

    def execute(_base, **_kwargs):
        seen["actor"] = permissions.current_request_actor_id()
        seen["read"] = _run(s.read_file(path="notes/background-self.md"))
        seen["write"] = _run(s.write_file(path="notes/handoff.md", content="next: x\n"))
        seen["root_write"] = _run(s.write_file(path="root-note.md", content="lost?\n"))
        seen["listing"] = _run(s.run_bash(command="ls -A /u"))
        seen["consents"] = _run(s.run_bash(command="cat /u/.effector_consents.db"))
        return SimpleNamespace(run_id="run-a", status="completed", output={}, error="")

    monkeypatch.setattr("tinyassets.runs.get_run_by_branch_task_id", lambda *_a, **_k: None)
    monkeypatch.setattr("tinyassets.runs.execute_branch_version", execute)
    from tinyassets.branch_tasks_v2 import Epoch2BranchTask

    task = Epoch2BranchTask(
        branch_task_id="bt2_" + "a" * 32, branch_def_id="branch-a", universe_id="u-alpha",
        admission_id="adm_" + "c" * 32, request_id="req_" + "d" * 32, actor_id=owner,
        automation_id="automation-a", automation_branch_version="branch-version-a",
        automation_subject_ref="branch-version-a",
        automation_subject_digest="sha256:" + "b" * 64, inputs={},
    )
    ok, error, _detail = execute_claimed_branch_task(
        world.data_root, task, ClaimedBranchExecutorIdentity(daemon_id="d"), object(),
    )
    assert ok, error

    assert seen["actor"] == owner, "the run is bound to its owner"
    assert raced and all(raced), "the sidecar vanished between scan and launch every call"
    assert OWN_MARKER in seen["read"], seen["read"]
    assert seen["write"].startswith("wrote"), seen["write"]
    assert (a / "notes" / "handoff.md").read_text(encoding="utf-8") == "next: x\n"
    # Isolation held: no hidden root entry is in the jail at all.
    listing = seen["listing"].split("[exit code")[0].split()
    assert "notes" in listing and not [name for name in listing if name.startswith(".")], listing
    assert "No such file" in seen["consents"], seen["consents"]
    # Since harness W2 /u is the agent's own workspace: a new top-level file is
    # kept there durably, never in the universe root and never in a tmpfs that
    # is lost when the call ends.
    from tinyassets.universe_tools import WORKSPACE_DIR

    assert seen["root_write"].startswith("wrote"), seen["root_write"]
    assert (a / WORKSPACE_DIR / "root-note.md").read_text(encoding="utf-8") == "lost?\n"
    assert not (a / "root-note.md").exists()


def test_read_shows_an_image_in_its_own_universe_and_no_other(world, monkeypatch):
    """The image path reads through the same jail: its own PNG comes back as
    image content, another universe's is as unreachable as its text."""
    import io

    from PIL import Image

    def png(color):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), color).save(buffer, "PNG")
        return buffer.getvalue()

    (world.universe_a / "notes" / "own.png").write_bytes(png((1, 2, 3)))
    (world.universe_b / "secret.png").write_bytes(png((9, 9, 9)))
    s = _engine(monkeypatch, world)
    shown = _run(s.read_file(path="notes/own.png"))
    blocks = shown.content
    assert [b.type for b in blocks] == ["text", "image"], shown
    for path in (str(world.universe_b / "secret.png"), "../u-bravo/secret.png"):
        out = _run(s.read_file(path=path))
        assert isinstance(out, str) and out.startswith("error:"), (path, out)


def test_shared_volume_floor_remains_aggregate_with_old_file_limit(world):
    """A fixed per-file limit never made the aggregate floor synchronous."""
    from tinyassets import universe_tools as tools

    mib = 1024**2
    free = tools._free_disk(world.universe_a)
    assert free > 200 * mib
    limits = tools.ToolLimits(min_free_disk_bytes=free - 48 * mib)
    try:
        result = tools.bash(
            world.universe_a,
            "prlimit --fsize=33554432 -- bash -c "
            "'fallocate -l 32M notes/old-cap-a; fallocate -l 32M notes/old-cap-b'",
            agent_id="main", limits=limits,
        )
        assert "shared disk" in result and "nearly full" in result, result
        assert sum((world.universe_a / "notes" / name).stat().st_size
                   for name in ("old-cap-a", "old-cap-b")) == 64 * mib
    finally:
        for name in ("old-cap-a", "old-cap-b"):
            (world.universe_a / "notes" / name).unlink(missing_ok=True)
