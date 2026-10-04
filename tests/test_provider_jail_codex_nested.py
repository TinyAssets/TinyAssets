"""REAL proofs of the provider jail's two seccomp profiles with the real codex.

NON-SERVED: the codex adapter runs a confined non-served call with its own
sandbox off (``--dangerously-bypass-approvals-and-sandbox``), so the jail loads
the full deny profile. A codex command runs, writes the agent workspace, cannot
write or read the masked platform state at the root, and ``ln`` is refused.

SERVED: a served turn keeps codex's ``--sandbox workspace-write``, whose native
``apply_patch`` runs through a filesystem sandbox helper that builds a nested
sandbox. The adapter declares that, the jail loads the permissive profile, and a
real ``apply_patch`` edit lands. The detection control shows the deny profile
would break it. The served path's link residual is covered daemon-side by the
link-refusing reader/writer (#4254) until platform state moves out of the
universe dir.

``codex sandbox`` stands in for what ``codex exec`` does per tool call, so no
model call is needed. Linux + bwrap + the codex CLI only.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_BWRAP = shutil.which("bwrap") if sys.platform == "linux" else None
_CODEX = shutil.which("codex", path="/opt/codex-install/node_modules/.bin:/usr/local/bin:/usr/bin:/bin") \
    if sys.platform == "linux" else None

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "linux" or not _BWRAP or not _CODEX,
        reason="needs Linux, bwrap and the codex CLI",
    ),
    pytest.mark.real_jail,
]

_PATH = "/opt/codex-install/node_modules/.bin:/usr/local/bin:/usr/bin:/bin"


@pytest.fixture
def world():
    root = Path(tempfile.mkdtemp(prefix="ta-cxnest-", dir="/tmp"))
    try:
        u = root / "u-alpha"
        (u / ".runtime" / "codex").mkdir(parents=True)
        (u / ".runs.db").write_text("OWN-DB-SECRET", encoding="utf-8")
        (u / "notes").mkdir()
        other = root / "u-bravo"
        other.mkdir()
        (other / "secret.txt").write_text("B-SECRET", encoding="utf-8")
        from tinyassets.providers import base
        base._sandbox_probe_cache = None
        yield u, other
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run_codex(universe: Path, sandbox_mode: str) -> str:
    from tinyassets.providers.provider_jail import confine_launch, provider_launch_scope

    env = {
        "PATH": _PATH, "HOME": "/tmp", "TMPDIR": "/tmp",
        "CODEX_HOME": str(universe / ".runtime" / "codex"),
        "OPENAI_API_KEY": "sk-proj-" + "x" * 60,
    }
    script = (
        "echo RUNCMD; "
        # Writes the agent workspace (RW) -- what a coding turn must do.
        f"echo hi > {universe}/notes/made && echo WROTE_OWN; "
        # ... but NOT the daemon's masked per-universe state at the root.
        f"echo tamper > {universe}/.runs.db 2>&1 && echo DB_WRITE || echo DB_WRITE_BLOCKED; "
        f"cat {universe}/.runs.db 2>&1 | head -1; "
        f"ln -s {universe.parent}/u-bravo/secret.txt {universe}/notes/lnk "
        "2>&1 && echo LINKED || echo LINK_BLOCKED"
    )
    argv = ["codex", "sandbox", "-c", f'sandbox_mode="{sandbox_mode}"',
            "--", "/bin/sh", "-c", script]
    with provider_launch_scope(universe):
        launch = confine_launch(argv, env=env)
    try:
        r = subprocess.run(launch.argv, env=env, capture_output=True, text=True,
                           timeout=120, pass_fds=launch.pass_fds, cwd="/")
    finally:
        launch.close()
    return r.stdout + r.stderr


def test_codex_runs_with_its_sandbox_off_and_the_jail_refuses_a_planted_link(world):
    universe, _other = world
    out = _run_codex(universe, "danger-full-access")
    # The command ran and wrote the agent workspace (RW) -- a coding turn.
    assert "RUNCMD" in out and "WROTE_OWN" in out, out
    assert (universe / "notes" / "made").read_text(encoding="utf-8") == "hi\n", out
    # ... and NOTHING ELSE: the daemon's per-universe DB at the root is masked,
    # unwritable and unreadable, and its host content is untouched.
    assert "DB_WRITE_BLOCKED" in out and "DB_WRITE\n" not in out, out
    assert "OWN-DB-SECRET" not in out, out
    assert (universe / ".runs.db").read_text(encoding="utf-8") == "OWN-DB-SECRET", out
    # The planted-link vector is closed: symlink creation is refused.
    assert "LINK_BLOCKED" in out and "LINKED" not in out, out
    assert not (universe / "notes" / "lnk").is_symlink(), out


def _codex_binary() -> str:
    """The real codex executable (the npm wrapper execs this musl binary)."""
    found = sorted(Path("/opt/codex-install").glob("**/vendor/*/bin/codex"))
    if not found:
        pytest.skip("the codex native binary is not installed here")
    return str(found[0])


def _served_apply_patch(universe: Path, *, nested_sandbox: bool) -> str:
    """codex's own workspace-write sandbox running its native apply_patch helper,
    as a served turn's apply_patch tool does, inside our jail."""
    from tinyassets.providers.provider_jail import confine_launch, provider_launch_scope

    env = {
        "PATH": _PATH, "HOME": "/tmp", "TMPDIR": "/tmp",
        "CODEX_HOME": str(universe / ".runtime" / "codex"),
    }
    patch = (
        "*** Begin Patch\n"
        "*** Add File: notes/patched.txt\n"
        "+patched\n"
        "*** End Patch\n"
    )
    argv = ["codex", "sandbox", "-c", 'sandbox_mode="workspace-write"', "--",
            _codex_binary(), "--codex-run-as-apply-patch", patch]
    with provider_launch_scope(universe):
        launch = confine_launch(argv, env=env, nested_sandbox=nested_sandbox)
    try:
        r = subprocess.run(launch.argv, env=env, capture_output=True, text=True,
                           timeout=120, pass_fds=launch.pass_fds, cwd="/")
    finally:
        launch.close()
    return r.stdout + r.stderr


def test_a_served_codex_apply_patch_edit_works_under_the_served_profile(world):
    """A served turn keeps codex's own sandbox, and its apply_patch helper builds
    a nested one. Under the served (permissive) profile the edit lands."""
    universe, _other = world
    out = _served_apply_patch(universe, nested_sandbox=True)
    patched = universe / "notes" / "patched.txt"
    assert patched.read_text(encoding="utf-8") == "patched\n", out


def test_detection_control_the_deny_profile_would_break_served_apply_patch(world):
    """Why the served exception exists: under the full deny profile the same
    apply_patch cannot build its nested sandbox, and nothing is written."""
    universe, _other = world
    out = _served_apply_patch(universe, nested_sandbox=False)
    assert not (universe / "notes" / "patched.txt").exists(), out
    assert "namespace" in out or "Operation not permitted" in out, out
