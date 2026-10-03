"""Shared host tray service for per-universe dashboards.

Keeps one physical tray icon per desktop process while exposing
per-universe dashboard bindings that look like a tray to the rest of
the runtime.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Callable

# pystray requires a display (X server / DWM / WindowServer). In
# headless container environments (e.g. the daemon container's
# fantasy_daemon subprocess on the DO droplet), the import fails.
# Defer + tolerate ImportError so the module still loads for its
# non-tray API surface. Any code that actually instantiates the tray
# will get a clear error at call-time.
try:
    from pystray import Menu, MenuItem  # type: ignore[assignment]
except Exception:  # pragma: no cover — headless environments
    Menu = None  # type: ignore[assignment]
    MenuItem = None  # type: ignore[assignment]

from tinyassets.desktop.tray import TrayApp

logger = logging.getLogger(__name__)


def _open_path(path: str) -> None:
    """Open a filesystem path in the platform file manager."""
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        logger.warning("Could not open path from host tray: %s", path, exc_info=True)


@dataclass
class _DashboardEntry:
    """Live tray state for one universe dashboard."""

    key: str
    universe_name: str
    on_show_window: Callable[[], Any]
    on_pause: Callable[[], Any]
    on_resume: Callable[[], Any]
    on_quit: Callable[[], Any]
    output_dir: str
    status: str = "Idle"
    word_count: int = 0
    tunnel_url: str = ""
    paused: bool = False


class UniverseTrayBinding:
    """Per-universe tray facade backed by the shared host tray service."""

    def __init__(self, service: HostTrayService, dashboard_key: str) -> None:
        self._service = service
        self._dashboard_key = dashboard_key

    def start(self) -> None:
        """No-op for compatibility with TrayApp."""
        return

    def stop(self) -> None:
        self._service.unregister_dashboard(self._dashboard_key)

    def update_status(self, status: str) -> None:
        self._service.update_dashboard(
            self._dashboard_key,
            status=status,
        )

    def update_extended_status(
        self,
        *,
        universe_name: str | None = None,
        word_count: int | None = None,
        tunnel_url: str | None = None,
        phase: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if universe_name is not None:
            kwargs["universe_name"] = universe_name
        if word_count is not None:
            kwargs["word_count"] = word_count
        if tunnel_url is not None:
            kwargs["tunnel_url"] = tunnel_url
        if phase is not None:
            kwargs["status"] = phase
        if kwargs:
            self._service.update_dashboard(self._dashboard_key, **kwargs)

    def notify(self, title: str, message: str = "") -> None:
        self._service.notify_dashboard(self._dashboard_key, title, message)


class HostTrayService:
    """One shared tray icon that aggregates live universe dashboards."""

    _instance: HostTrayService | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, _DashboardEntry] = {}
        self._tray = TrayApp(
            on_quit=self._handle_quit_all,
            extra_menu_items_factory=self._build_extra_menu_items,
            show_default_runtime_controls=False,
            show_output_action=False,
            show_window_action=False,
        )

    @classmethod
    def shared(cls) -> HostTrayService:
        """Return the process-wide host tray singleton."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def bind_dashboard(
        self,
        *,
        dashboard_key: str,
        universe_name: str,
        on_show_window: Callable[[], Any],
        on_pause: Callable[[], Any],
        on_resume: Callable[[], Any],
        on_quit: Callable[[], Any],
        output_dir: str,
    ) -> UniverseTrayBinding:
        """Register a live universe dashboard and return its tray facade."""
        with self._lock:
            first_dashboard = not self._entries
            self._entries[dashboard_key] = _DashboardEntry(
                key=dashboard_key,
                universe_name=universe_name,
                on_show_window=on_show_window,
                on_pause=on_pause,
                on_resume=on_resume,
                on_quit=on_quit,
                output_dir=output_dir,
            )
            if first_dashboard:
                self._tray.start()
            self._sync_summary_locked()
            self._tray.refresh_menu()
        return UniverseTrayBinding(self, dashboard_key)

    def unregister_dashboard(self, dashboard_key: str) -> None:
        """Remove a universe dashboard from the shared tray."""
        with self._lock:
            self._entries.pop(dashboard_key, None)
            if not self._entries:
                self._tray.stop()
                return
            self._sync_summary_locked()
            self._tray.refresh_menu()

    def update_dashboard(
        self,
        dashboard_key: str,
        *,
        universe_name: str | None = None,
        status: str | None = None,
        word_count: int | None = None,
        tunnel_url: str | None = None,
        paused: bool | None = None,
    ) -> None:
        """Update one dashboard entry and refresh shared tray state."""
        with self._lock:
            entry = self._entries.get(dashboard_key)
            if entry is None:
                return
            if universe_name is not None:
                entry.universe_name = universe_name
            if status is not None:
                entry.status = status
            if word_count is not None:
                entry.word_count = word_count
            if tunnel_url is not None:
                entry.tunnel_url = tunnel_url
            if paused is not None:
                entry.paused = paused
            self._sync_summary_locked()
            self._tray.refresh_menu()

    def notify_dashboard(self, dashboard_key: str, title: str, message: str = "") -> None:
        """Emit a tray notification scoped to one universe dashboard."""
        with self._lock:
            entry = self._entries.get(dashboard_key)
            if entry is None:
                return
            prefix = entry.universe_name or "Universe"
        self._tray.notify(f"{prefix}: {title}", message)

    def _sync_summary_locked(self) -> None:
        """Update the tray tooltip summary from current live dashboards."""
        entries = list(self._entries.values())
        if not entries:
            self._tray.update_extended_status(
                universe_name="",
                word_count=0,
                phase="Idle",
            )
            return

        if len(entries) == 1:
            entry = entries[0]
            self._tray.update_extended_status(
                universe_name=entry.universe_name,
                word_count=entry.word_count,
                tunnel_url=entry.tunnel_url,
                phase=entry.status,
            )
            return

        total_words = sum(entry.word_count for entry in entries)
        self._tray.update_extended_status(
            universe_name=f"{len(entries)} active command centers",
            word_count=total_words,
            phase="Host dashboard active",
        )

    def _build_extra_menu_items(self) -> list[MenuItem | Menu]:
        """Build dynamic submenu entries for all active dashboards."""
        with self._lock:
            entries = sorted(
                self._entries.values(),
                key=lambda entry: entry.universe_name.lower(),
            )

        if not entries:
            return [
                MenuItem("No active dashboards", lambda *_: None, enabled=False),
            ]

        dashboard_items: list[MenuItem] = []
        for entry in entries:
            dashboard_items.append(
                MenuItem(
                    entry.universe_name,
                    Menu(
                        MenuItem(
                            f"Phase: {entry.status}",
                            lambda *_: None,
                            enabled=False,
                        ),
                        MenuItem(
                            f"Words: {entry.word_count:,}",
                            lambda *_: None,
                            enabled=False,
                        ),
                        MenuItem("Show Dashboard", self._show_cb(entry.on_show_window)),
                        MenuItem(
                            "Pause Daemon",
                            self._call_cb(entry.on_pause),
                            visible=not entry.paused,
                        ),
                        MenuItem(
                            "Resume Daemon",
                            self._call_cb(entry.on_resume),
                            visible=entry.paused,
                        ),
                        MenuItem(
                            "Open Output Folder",
                            self._open_output_cb(entry.output_dir),
                        ),
                        MenuItem("Stop Daemon", self._call_cb(entry.on_quit)),
                    ),
                ),
            )

        return [
            MenuItem(
                f"Dashboards ({len(entries)})",
                Menu(*dashboard_items),
            ),
        ]

    def _handle_quit_all(self) -> None:
        """Quit every registered dashboard from the shared tray."""
        with self._lock:
            callbacks = [entry.on_quit for entry in self._entries.values()]
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.debug("Host tray quit callback failed", exc_info=True)

    @staticmethod
    def _show_cb(callback: Callable[[], Any]) -> Callable[[Any, Any], None]:
        def _wrapped(_icon: Any = None, _item: Any = None) -> None:
            callback()

        return _wrapped

    @staticmethod
    def _call_cb(callback: Callable[[], Any]) -> Callable[[Any, Any], None]:
        def _wrapped(_icon: Any = None, _item: Any = None) -> None:
            callback()

        return _wrapped

    @staticmethod
    def _open_output_cb(path: str) -> Callable[[Any, Any], None]:
        def _wrapped(_icon: Any = None, _item: Any = None) -> None:
            _open_path(path)

        return _wrapped
