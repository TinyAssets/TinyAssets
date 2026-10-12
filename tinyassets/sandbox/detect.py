"""Sandbox (bwrap) detection primitives.

Shared module for any component that needs to know whether a bwrap sandbox
is available on the current host. Keeps detection logic out of individual
provider modules so it can be tested and imported independently.

Spec: openspec/specs/scratch-storage/spec.md.
Topic: Loud sandbox-unavailable surface for dev/checker exec nodes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

# ── Exception ─────────────────────────────────────────────────────────────────

class SandboxUnavailableError(Exception):
    """Raised when bwrap execution fails due to host sandbox constraints.

    Fix options:
      1. Run ``sudo sysctl kernel.unprivileged_userns_clone=1``
      2. Use a Docker/Podman container with ``--privileged``
      3. Disable the sandbox (checker exec nodes only, security tradeoff)
    """


# ── Status dataclass ──────────────────────────────────────────────────────────

@dataclass
class SandboxStatus:
    available: bool
    reason: str | None = None
    bwrap_path: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "reason": self.reason,
            "bwrap_path": self.bwrap_path,
            "version": self.version,
        }


# ── Detection ─────────────────────────────────────────────────────────────────

_BWRAP_FAILURE_PATTERNS: tuple[str, ...] = (
    "bwrap: No permissions to create a new namespace",
    "bwrap: No permissions to create new namespace",
    "bwrap: No such file or directory",
    "sandbox initialization failed",
)


def check_bwrap_output(output_text: str) -> None:
    """Raise SandboxUnavailableError if *output_text* contains a bwrap failure.

    No-op on non-Linux platforms (bwrap is not available on Windows/macOS).
    """
    import sys
    if sys.platform != "linux":
        return
    lower = output_text.lower()
    for pattern in _BWRAP_FAILURE_PATTERNS:
        if pattern.lower() in lower:
            raise SandboxUnavailableError(
                f"Sandbox unavailable: {output_text!r}. "
                "Fix options: (1) sudo sysctl kernel.unprivileged_userns_clone=1 "
                "(2) run in privileged container (3) disable checker sandbox."
            )


def _functional_probe_argv(bwrap_path: str) -> list[str]:
    """The probe must ask the question the LAUNCHER asks.

    It used to run ``bwrap --ro-bind / / /bin/sh -c true`` while the launcher
    runs ``--die-with-parent --new-session --unshare-all --clearenv`` with its
    own binds. Those are different questions, and measured side by side on one
    host the probe failed ("Creating new namespace failed: Operation not
    permitted") while the real launch succeeded - so a host where the jail
    works exactly as we use it could be declared unavailable and every code
    node refused. Building the probe from the launcher's own argv is what stops
    the two drifting again; ``python -c ""`` is the command because that
    interpreter is a directory the launcher already binds, whereas ``/bin/sh``
    need not exist inside a private root at all.
    """
    from tinyassets.node_sandbox import _bwrap_argv

    return [*_bwrap_argv(bwrap_path=bwrap_path), sys.executable, "-c", ""]


def detect_bwrap() -> SandboxStatus:
    """Probe whether bwrap is present and executable on the current host.

    Returns a SandboxStatus with:
      - available=True if bwrap is on PATH, ``bwrap --version`` exits 0, and
        a minimal bwrap launch succeeds.
      - available=False with a human-readable reason otherwise.

    Result is NOT cached here — callers should cache at their appropriate scope.
    """
    import shutil
    import subprocess
    import sys

    if sys.platform != "linux":
        return SandboxStatus(
            available=False,
            reason=f"bwrap not supported on {sys.platform}",
        )

    bwrap_path = shutil.which("bwrap")
    if bwrap_path is None:
        return SandboxStatus(
            available=False,
            reason="bwrap not found on PATH",
        )

    try:
        version_result = subprocess.run(
            [bwrap_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError as exc:
        return SandboxStatus(
            available=False,
            bwrap_path=bwrap_path,
            reason=f"probe error: {exc}",
        )

    if version_result.returncode != 0:
        return SandboxStatus(
            available=False,
            bwrap_path=bwrap_path,
            reason=version_result.stderr.strip() or "bwrap --version failed",
        )

    version = version_result.stdout.strip() or version_result.stderr.strip() or None

    try:
        launch_result = subprocess.run(
            _functional_probe_argv(bwrap_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError as exc:
        return SandboxStatus(
            available=False,
            bwrap_path=bwrap_path,
            version=version,
            reason=f"functional probe error: {exc}",
        )

    if launch_result.returncode != 0:
        excerpt = (
            launch_result.stderr.strip()
            or launch_result.stdout.strip()
            or "no output"
        )
        return SandboxStatus(
            available=False,
            bwrap_path=bwrap_path,
            version=version,
            reason=(
                f"bwrap functional probe exited {launch_result.returncode}: "
                f"{excerpt[:200]}"
            ),
        )

    return SandboxStatus(
        available=True,
        bwrap_path=bwrap_path,
        version=version,
    )


__all__ = [
    "SandboxStatus",
    "SandboxUnavailableError",
    "_BWRAP_FAILURE_PATTERNS",
    "check_bwrap_output",
    "detect_bwrap",
]
