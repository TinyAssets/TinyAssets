"""Single-binary role dispatcher used by native desktop packages."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tinyassets.desktop.onboarding import (
    AutostartManager,
    ContentPreservingUninstaller,
    PackagedAuthorityUnavailable,
    require_packaged_authority,
)
from tinyassets.desktop.updater import UpdateService, WindowsInstaller

_UPDATE_PUBLIC_KEY = Path(__file__).with_name("update-public-key.pem")


class PackagedRuntimeUnavailable(RuntimeError):
    """A packaged role cannot start until its runtime authority is valid."""


def _data_root() -> Path:
    from tinyassets.storage import data_dir

    return data_dir()


def _require_authority() -> None:
    try:
        require_packaged_authority(_data_root() / "desktop")
    except PackagedAuthorityUnavailable as exc:
        raise PackagedRuntimeUnavailable(str(exc)) from exc


def _ensure_autostart(*, platform_name: str | None = None) -> None:
    target_platform = platform_name or sys.platform
    if target_platform == "win32":
        return
    AutostartManager(
        command=[sys.executable],
        platform_name=target_platform,
    ).enable()


def _health_probe() -> None:
    root = _data_root()
    (root / "logs").mkdir(parents=True, exist_ok=True)
    import tinyassets_tray  # noqa: F401


def _apply_update(arguments: list[str]) -> None:
    if len(arguments) != 2:
        raise PackagedRuntimeUnavailable(
            "apply-update requires a signed manifest path and artifact path"
        )
    if not _UPDATE_PUBLIC_KEY.is_file():
        raise PackagedRuntimeUnavailable(
            "update verification identity not provisioned; automatic updates are disabled"
        )
    if sys.platform != "win32":
        raise PackagedRuntimeUnavailable(
            f"native updater is not implemented yet for {sys.platform}"
        )
    manifest_path, artifact_path = map(Path, arguments)
    update_root = _data_root() / "updates"
    installed = update_root / "current.json"
    if not installed.is_file():
        raise PackagedRuntimeUnavailable(
            "retained current installer is unavailable; repair the installation"
        )
    import json

    current = json.loads(installed.read_text(encoding="utf-8"))
    host = require_packaged_authority(_data_root() / "desktop")
    service = UpdateService(
        install_root=update_root,
        public_key_pem=_UPDATE_PUBLIC_KEY.read_bytes(),
        product="TinyAssets",
        platform_name=sys.platform,
        architecture="x86_64",
        channel="stable",
        cohort_id=host.host_id,
        installer=WindowsInstaller(),
    )
    staged = service.stage_update(manifest_path.read_bytes(), artifact_path)
    result = service.apply_staged(
        staged,
        health_check=lambda _: subprocess.run(
            [sys.executable, "--packaged-role", "health-probe"],
            check=False,
            timeout=30,
        ).returncode
        == 0,
    )
    if result.status != "activated":
        raise PackagedRuntimeUnavailable(
            f"desktop update rolled back; evidence: {result.evidence_path}"
        )
    if service.current_version() == str(current.get("version")):
        raise PackagedRuntimeUnavailable("desktop update did not advance the active version")


def _uninstall() -> None:
    if sys.platform == "win32":
        raise PackagedRuntimeUnavailable(
            "use Windows Installed apps to run the signed TinyAssets uninstaller"
        )
    if sys.platform == "linux":
        raise PackagedRuntimeUnavailable(
            "native Linux uninstall is not implemented yet; use the package manager"
        )
    executable = Path(sys.executable).resolve()
    app_root = next(
        (parent for parent in executable.parents if parent.suffix == ".app"),
        None,
    )
    if app_root is None:
        raise PackagedRuntimeUnavailable("TinyAssets application bundle was not found")
    autostart = AutostartManager(
        command=[sys.executable],
        platform_name=sys.platform,
    )
    ContentPreservingUninstaller(
        install_root=app_root,
        data_root=_data_root(),
        autostart=autostart,
    ).uninstall()


def dispatch(arguments: list[str] | None = None) -> int:
    """Run the tray or a child daemon/MCP role from one frozen executable."""
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not args:
        _ensure_autostart()
        import tinyassets_tray

        return tinyassets_tray.main()
    if args[0] != "--packaged-role" or len(args) < 2:
        raise SystemExit("unknown packaged runtime arguments")
    role = args[1]
    forwarded = args[2:]
    sys.argv = [sys.argv[0], *forwarded]
    if role == "health-probe":
        _health_probe()
        return 0
    if role == "apply-update":
        try:
            _apply_update(forwarded)
        except (PackagedAuthorityUnavailable, PackagedRuntimeUnavailable) as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if role == "uninstall":
        try:
            _uninstall()
        except PackagedRuntimeUnavailable as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if role == "daemon":
        try:
            _require_authority()
        except PackagedRuntimeUnavailable as exc:
            raise SystemExit(str(exc)) from exc
        # The desktop daemon opens the same data root: the layout guard (storage_layout.py).
        from tinyassets.storage_layout import require_layout

        require_layout()
        from fantasy_daemon.__main__ import main

        main()
        return 0
    if role == "mcp":
        try:
            _require_authority()
        except PackagedRuntimeUnavailable as exc:
            raise SystemExit(str(exc)) from exc
        from tinyassets.universe_server import main

        main(host="0.0.0.0", port=8001, transport="streamable-http")
        return 0
    raise SystemExit(f"unknown packaged runtime role: {role}")


def main() -> int:
    return dispatch()


if __name__ == "__main__":
    raise SystemExit(main())
