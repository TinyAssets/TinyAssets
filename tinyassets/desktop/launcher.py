"""Tkinter launcher GUI for TinyAssets.

Provides a startup window where the user selects a universe directory,
configures settings, and launches the writing daemon. Wires to
DaemonController for background execution and TrayApp for system tray.

Exports
-------
LauncherApp
    The main GUI class (wraps a tk.Tk root).
main()
    Create a LauncherApp and enter mainloop.
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from tinyassets.universe_soul import (
    ensure_universe_soul,
    premise_from_soul,
    read_legacy_premise,
)

# tkinter requires libtk (system lib). Headless Docker containers
# don't ship it; the daemon container's fantasy_daemon subprocess imports
# the desktop tree at load time before --no-tray is parsed. Tolerate
# ImportError so the module surface stays loadable; call-time use
# (host laptop only) errors with a clear NoneType.
try:
    import tkinter as tk  # type: ignore[assignment]
    from tkinter import filedialog, ttk  # type: ignore[assignment]
except Exception:  # pragma: no cover — headless environments
    tk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_STATS_REFRESH_MS = 2000
_MAX_FEED_LINES = 200

# --- Color scheme (dark fantasy) ---
BG = "#2b2b2b"
BG_LIGHT = "#3c3c3c"
FG = "#e0e0e0"
FG_DIM = "#999999"
ACCENT = "#c9a84c"  # gold accent
ACCENT_HOVER = "#dfc06e"
BTN_BG = "#4a4a4a"
BTN_FG = "#e0e0e0"
START_BG = "#2d6a2e"  # green for start
START_HOVER = "#3a8c3b"


def _default_universe_path() -> str:
    """Return the default universe directory path."""
    home = Path.home()
    return str(home / "Documents" / "TinyAssets" / "default-universe")


class LauncherApp:
    """TinyAssets launcher GUI -- daemon controller.

    Parameters
    ----------
    root : tk.Tk or None
        The tkinter root window.  If None, one is created.
    on_start : callable or None
        Callback invoked when the user clicks "Start Writing".
        Receives ``(universe_path: str, start_minimized: bool, verbose: bool)``.
    """

    def __init__(
        self,
        root: tk.Tk | None = None,
        on_start: Callable[..., Any] | None = None,
    ) -> None:
        self._owns_root = root is None
        self.root = root or tk.Tk()
        self._on_start = on_start
        self._running = False
        self._reloading = False
        self._daemon: Any = None
        self._daemon_thread: threading.Thread | None = None
        self._tray: Any = None
        self._dashboard_handler: Any = None
        self._stats_polling = False

        self.root.title("TinyAssets")
        self.root.geometry("420x620")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        # Try to set the icon
        ico_path = Path(__file__).parent / "app.ico"
        if ico_path.exists():
            try:
                self.root.iconbitmap(str(ico_path))
            except tk.TclError:
                pass

        self._configure_styles()
        self._build_ui()

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Dim.TLabel", background=BG, foreground=FG_DIM)
        header_font = ("Segoe UI", 16, "bold")
        section_font = ("Segoe UI", 10, "bold")
        style.configure("Header.TLabel", background=BG, foreground=ACCENT, font=header_font)
        style.configure("Section.TLabel", background=BG, foreground=ACCENT, font=section_font)

        style.configure(
            "TButton",
            background=BTN_BG,
            foreground=BTN_FG,
            borderwidth=0,
            focusthickness=0,
            padding=(10, 4),
        )
        style.map("TButton", background=[("active", BG_LIGHT)])

        style.configure(
            "Start.TButton",
            background=START_BG,
            foreground="#ffffff",
            font=("Segoe UI", 12, "bold"),
            padding=(10, 10),
        )
        style.map("Start.TButton", background=[("active", START_HOVER)])

        style.configure(
            "Reload.TButton",
            background=BTN_BG,
            foreground=ACCENT,
            padding=(10, 6),
        )
        style.map(
            "Reload.TButton",
            background=[("active", BG_LIGHT), ("disabled", BG)],
            foreground=[("disabled", FG_DIM)],
        )

        style.configure(
            "TCheckbutton",
            background=BG,
            foreground=FG,
        )
        style.map("TCheckbutton", background=[("active", BG)])

        style.configure("TEntry", fieldbackground=BG_LIGHT, foreground=FG, insertcolor=FG)

        style.configure("Status.TLabel", background="#222222", foreground=FG_DIM, padding=(6, 4))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=20)
        container.pack(fill=tk.BOTH, expand=True)

        # --- Title ---
        ttk.Label(container, text="TinyAssets", style="Header.TLabel").pack(pady=(0, 15))

        # --- Universe section ---
        ttk.Label(container, text="Universe", style="Section.TLabel").pack(anchor=tk.W)

        universe_frame = ttk.Frame(container)
        universe_frame.pack(fill=tk.X, pady=(4, 0))

        self._universe_var = tk.StringVar(value=_default_universe_path())
        self._universe_entry = ttk.Entry(universe_frame, textvariable=self._universe_var)
        self._universe_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        browse_btn = ttk.Button(universe_frame, text="Browse...", command=self._browse_universe)
        browse_btn.pack(side=tk.LEFT)

        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill=tk.X, pady=(6, 12))
        ttk.Button(
            btn_frame, text="New Command Center", command=self._new_universe,
        ).pack(side=tk.LEFT)

        self._add_files_btn = ttk.Button(
            btn_frame, text="Add Files...",
            command=self._handle_add_files,
        )
        self._add_files_btn.pack(side=tk.LEFT, padx=(6, 0))

        # --- Canon section ---
        ttk.Label(container, text="Canon Files", style="Section.TLabel").pack(anchor=tk.W)

        canon_frame = ttk.Frame(container)
        canon_frame.pack(fill=tk.X, pady=(4, 12))

        self._canon_label = ttk.Label(canon_frame, text=self._canon_path(), style="Dim.TLabel")
        self._canon_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(canon_frame, text="Open Folder", command=self._open_canon).pack(side=tk.LEFT)

        # Update canon label when universe changes
        self._universe_var.trace_add("write", self._on_universe_changed)

        # --- Settings ---
        ttk.Label(container, text="Settings", style="Section.TLabel").pack(anchor=tk.W, pady=(0, 4))

        self._minimized_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            container,
            text="Start minimized to tray",
            variable=self._minimized_var,
        ).pack(anchor=tk.W, padx=(10, 0))

        self._verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            container,
            text="Verbose logging",
            variable=self._verbose_var,
        ).pack(anchor=tk.W, padx=(10, 0), pady=(0, 15))

        # --- Start button ---
        self._start_btn = ttk.Button(
            container,
            text="Start Writing",
            style="Start.TButton",
            command=self._handle_start,
        )
        self._start_btn.pack(fill=tk.X, pady=(5, 8))

        # --- Reload button (disabled until daemon is running) ---
        self._reload_btn = ttk.Button(
            container,
            text="\u21bb Apply Updates",
            style="Reload.TButton",
            command=self._handle_reload,
            state=tk.DISABLED,
        )
        self._reload_btn.pack(fill=tk.X, pady=(0, 8))

        # --- Quit button ---
        ttk.Button(container, text="Quit", command=self._handle_quit).pack(fill=tk.X)

        # --- Live stats panel (hidden until running) ---
        self._stats_frame = ttk.Frame(container)

        ttk.Label(
            self._stats_frame, text="Live Stats", style="Section.TLabel",
        ).pack(anchor=tk.W, pady=(8, 4))

        self._phase_var = tk.StringVar(value="Phase: -")
        ttk.Label(self._stats_frame, textvariable=self._phase_var).pack(
            anchor=tk.W, padx=(10, 0),
        )

        self._words_var = tk.StringVar(value="Words: 0")
        ttk.Label(self._stats_frame, textvariable=self._words_var).pack(
            anchor=tk.W, padx=(10, 0),
        )

        self._chapters_var = tk.StringVar(value="Chapters: 0")
        ttk.Label(self._stats_frame, textvariable=self._chapters_var).pack(
            anchor=tk.W, padx=(10, 0),
        )

        self._accept_var = tk.StringVar(value="Accept rate: -")
        ttk.Label(self._stats_frame, textvariable=self._accept_var).pack(
            anchor=tk.W, padx=(10, 0),
        )

        self._provider_var = tk.StringVar(value="Provider: -")
        ttk.Label(self._stats_frame, textvariable=self._provider_var).pack(
            anchor=tk.W, padx=(10, 0),
        )

        # --- Activity feed (hidden until running) ---
        self._feed_frame = ttk.Frame(container)

        ttk.Label(
            self._feed_frame, text="Activity", style="Section.TLabel",
        ).pack(anchor=tk.W, pady=(8, 4))

        self._feed_text = tk.Text(
            self._feed_frame,
            height=12,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg="#b0b0b0",
            insertbackground="#b0b0b0",
            relief=tk.FLAT,
            font=("Consolas", 8),
            state=tk.DISABLED,
            cursor="arrow",
        )
        self._feed_text.pack(fill=tk.BOTH, expand=True)

        # --- Spacer ---
        ttk.Frame(container).pack(fill=tk.BOTH, expand=True)

        # --- Status bar ---
        self._status_var = tk.StringVar(value="Idle")
        self._status_label = ttk.Label(
            self.root,
            textvariable=self._status_var,
            style="Status.TLabel",
        )
        self._status_label.pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------------------------------------------------------
    # Universe helpers
    # ------------------------------------------------------------------

    @property
    def universe_path(self) -> str:
        return self._universe_var.get()

    @property
    def start_minimized(self) -> bool:
        return self._minimized_var.get()

    @property
    def verbose(self) -> bool:
        return self._verbose_var.get()

    @property
    def status(self) -> str:
        return self._status_var.get()

    def set_status(self, text: str) -> None:
        self._status_var.set(text)

    def _canon_path(self) -> str:
        return str(Path(self._universe_var.get()) / "canon") + os.sep

    def _on_universe_changed(self, *_args: Any) -> None:
        self._canon_label.configure(text=self._canon_path())

    def _browse_universe(self) -> None:
        path = filedialog.askdirectory(
            title="Select Command Center Directory",
            initialdir=self._universe_var.get(),
        )
        if path:
            self._universe_var.set(path)

    def _new_universe(self) -> None:
        path = Path(self._universe_var.get())
        canon_path = path / "canon"
        try:
            ensure_universe_soul(path)
            canon_path.mkdir(parents=True, exist_ok=True)
            self.set_status(f"Created: {path}")
            logger.info("Created command center directory: %s", path)
        except OSError as exc:
            self.set_status(f"Error: {exc}")
            logger.error("Failed to create command center: %s", exc)

    def _handle_add_files(self) -> None:
        """Open a file dialog and copy selected files to the canon folder."""
        files = filedialog.askopenfilenames(
            title="Select source documents to import",
            filetypes=[
                ("All supported", "*.txt *.md *.pdf *.docx *.json *.yaml *.yml"),
                ("Text files", "*.txt *.md"),
                ("Documents", "*.pdf *.docx"),
                ("Data files", "*.json *.yaml *.yml"),
                ("All files", "*.*"),
            ],
        )
        if not files:
            return

        canon_dir = Path(self.universe_path) / "canon"
        canon_dir.mkdir(parents=True, exist_ok=True)

        imported: list[str] = []
        for src in files:
            src_path = Path(src)
            dest = canon_dir / src_path.name
            try:
                shutil.copy2(src, dest)
                imported.append(src_path.name)
            except OSError as exc:
                logger.warning("Failed to copy %s: %s", src, exc)

        if imported:
            names = ", ".join(imported)
            self._append_feed_line(
                f"Imported {len(imported)} file(s) to canon/: {names}"
            )

    def _open_canon(self) -> None:
        canon = Path(self._universe_var.get()) / "canon"
        if not canon.exists():
            canon.mkdir(parents=True, exist_ok=True)

        path = str(canon)
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            logger.warning("Could not open canon folder: %s", path)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _handle_start(self) -> None:
        if self._running:
            return
        if self._on_start is not None:
            self._on_start(
                self.universe_path,
                self.start_minimized,
                self.verbose,
            )
        self._running = True
        self.set_status("Running")

        if self._daemon is None:
            self._start_daemon()

        self._reload_btn.configure(state=tk.NORMAL)

    def _handle_quit(self) -> None:
        self._stop_stats_polling()
        if self._daemon is not None:
            self._daemon._stop_event.set()
            self._daemon._paused.clear()
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        self.root.destroy()

    # ------------------------------------------------------------------
    # Daemon wiring
    # ------------------------------------------------------------------

    def _start_daemon(self) -> None:
        """Create a DaemonController and run it in a background thread."""
        from tinyassets.__main__ import DaemonController

        self._daemon = DaemonController(
            universe_path=self.universe_path,
            no_tray=True,  # We manage tray ourselves
            log_callback=self._append_feed_line,
        )

        # Bind this launcher to the shared host tray (reuse existing binding
        # during code reloads so the tray icon stays stable).
        if self._tray is None:
            from tinyassets.desktop.host_tray import HostTrayService

            self._tray = HostTrayService.shared().bind_dashboard(
                dashboard_key=str(Path(self.universe_path).resolve()),
                universe_name=Path(self.universe_path).name or "TinyAssets",
                on_show_window=self._show_window,
                on_pause=self._on_tray_pause,
                on_resume=self._on_tray_resume,
                on_quit=self._handle_quit,
                output_dir=self.universe_path,
            )

        # Wire the dashboard so we can read live stats
        from tinyassets.desktop.dashboard import DashboardHandler

        self._dashboard_handler = DashboardHandler(
            tray=self._tray,
            log_callback=self._append_feed_line,
        )
        self._daemon._dashboard = self._dashboard_handler
        self._daemon._tray = self._tray

        # Show stats panel and activity feed
        self._stats_frame.pack(fill=tk.X, before=self._status_label)
        self._feed_frame.pack(fill=tk.BOTH, expand=True, before=self._status_label)

        # Hide launcher if "start minimized" is checked
        if self.start_minimized:
            self.root.withdraw()

        # Start polling for live stats
        self._start_stats_polling()

        # Run daemon in background thread
        self._daemon_thread = threading.Thread(
            target=self._run_daemon_thread,
            daemon=True,
        )
        self._daemon_thread.start()

    def _run_daemon_thread(self) -> None:
        """Run the daemon in a background thread."""
        try:
            if self._daemon is not None:
                self._daemon.start()
        except Exception:
            logger.exception("Daemon thread failed")
        finally:
            self.root.after(0, self._on_daemon_stopped)

    def _on_daemon_stopped(self) -> None:
        """Called on the main thread when the daemon finishes."""
        if self._reloading:
            return  # Reload handler manages the lifecycle
        self._running = False
        self.set_status("Idle")
        self._stop_stats_polling()
        self._reload_btn.configure(state=tk.DISABLED)
        if self._tray is not None:
            self._tray.stop()
            self._tray = None

    def _on_tray_pause(self) -> None:
        if self._daemon is not None:
            self._daemon._paused.set()
        self.set_status("Paused")

    def _on_tray_resume(self) -> None:
        if self._daemon is not None:
            self._daemon._paused.clear()
        self.set_status("Running")

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    # Modules to reimport when code changes are detected.
    _RELOAD_PACKAGES = (
        "domains.fantasy_daemon.phases",
        "tinyassets.providers",
        "tinyassets.evaluation",
        "tinyassets.constraints",
        "tinyassets.planning",
        "tinyassets.knowledge",
        "tinyassets.retrieval",
        "tinyassets.memory",
        "domains.fantasy_daemon.graphs",
        "tinyassets.checkpointing",
        "domains.fantasy_daemon.state",
        "tinyassets.learning",
    )

    def _handle_reload(self) -> None:
        """Button callback -- runs reload on a background thread."""
        if not self._running:
            return
        self._reload_btn.configure(state=tk.DISABLED)
        self.set_status("Reloading...")
        self._append_feed_line("Reloading...")
        threading.Thread(target=self._reload_thread, daemon=True).start()

    def _reload_thread(self) -> None:
        """Background thread that performs the reload."""
        try:
            change_type = self._classify_changes()
            self.root.after(0, self._do_reload, change_type)
        except Exception:
            logger.exception("Reload failed")
            self.root.after(0, self._reload_failed)

    def _reload_failed(self) -> None:
        self._append_feed_line("Reload failed -- check logs")
        self.set_status("Running")
        self._reload_btn.configure(state=tk.NORMAL)

    def _do_reload(self, change_type: str) -> None:
        """Execute the reload on the main thread based on change_type."""
        if change_type == "ui":
            self._append_feed_line(
                "UI changes detected -- restart the application to apply"
            )
            self.set_status("Running (restart needed for UI changes)")
            self._reload_btn.configure(state=tk.NORMAL)
            return

        if change_type == "config":
            self._apply_config_reload()
            self._append_feed_line("Reloading... applied config changes")
            self.set_status("Running")
            self._reload_btn.configure(state=tk.NORMAL)
            return

        # "code" or "unknown" -- stop, reimport, restart
        self._stop_daemon_for_reload()

    def _classify_changes(self) -> str:
        """Classify what changed (staged, unstaged, and untracked).

        Returns one of: "code", "config", "ui", "none".
        """
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(Path(__file__).resolve().parent.parent.parent),
            )
            if result.returncode != 0:
                return "code"  # Assume code changes if git fails
            changed = [
                line[3:] for line in result.stdout.splitlines()
                if len(line) > 3
            ]
        except Exception:
            return "code"

        if not changed:
            return "none"

        ui_paths = (
            "tinyassets/desktop/launcher.py",
            "tinyassets/desktop/tray.py",
            "fantasy_daemon/desktop/launcher.py",
            "fantasy_daemon/desktop/tray.py",
            "fantasy_author/desktop/launcher.py",
            "fantasy_author/desktop/tray.py",
        )

        has_code = False
        has_ui = False

        for path in changed:
            if any(path.endswith(u) for u in ui_paths):
                has_ui = True
            elif path.endswith(".py"):
                has_code = True

        if has_ui and not has_code:
            return "ui"
        if has_code or has_ui:
            return "code"
        # Only non-py files changed (docs, config, etc.)
        return "config"

    def _apply_config_reload(self) -> None:
        """Reload config-only changes into the running daemon."""
        if self._daemon is None:
            return

        # Re-read premise from the compatibility file or soul.md purpose.
        universe_dir = Path(self.universe_path)
        premise = read_legacy_premise(universe_dir).strip()
        if not premise:
            premise = premise_from_soul(universe_dir).strip()
        if premise:
            self._daemon._premise = premise

    def _reimport_modules(self) -> None:
        """Reimport TinyAssets submodules to pick up code changes."""
        reloaded = []
        for mod_name in sorted(sys.modules):
            if not any(mod_name.startswith(pkg) for pkg in self._RELOAD_PACKAGES):
                continue
            mod = sys.modules.get(mod_name)
            if mod is None:
                continue
            try:
                importlib.reload(mod)
                reloaded.append(mod_name)
            except Exception:
                logger.warning("Failed to reload %s", mod_name, exc_info=True)

        logger.info("Reloaded %d modules: %s", len(reloaded), reloaded)

    def _stop_daemon_for_reload(self) -> None:
        """Stop the current daemon, then trigger reimport and restart."""
        if self._daemon is not None:
            self._daemon._stop_event.set()
            self._daemon._paused.clear()
        self._reloading = True
        # The daemon thread will call _on_daemon_stopped, which we
        # intercept with the _reloading flag
        if self._daemon_thread is not None and self._daemon_thread.is_alive():
            # Wait for daemon to stop in a non-blocking way
            threading.Thread(
                target=self._wait_and_restart, daemon=True,
            ).start()
        else:
            # Daemon already stopped
            self._finish_reload()

    def _wait_and_restart(self) -> None:
        """Wait for the daemon thread to finish, then restart."""
        if self._daemon_thread is not None:
            self._daemon_thread.join(timeout=30)
        self.root.after(0, self._finish_reload)

    def _finish_reload(self) -> None:
        """Reimport modules and restart the daemon with same config."""
        self._reloading = False

        # Clean up old daemon state (nil out tray first so _cleanup
        # doesn't stop the tray icon -- we reuse it across reloads)
        if self._daemon is not None:
            self._daemon._tray = None
            self._daemon._cleanup()
        self._daemon = None
        self._daemon_thread = None

        # Reimport changed modules
        self._reimport_modules()

        # Restart daemon with preserved config
        self._start_daemon()
        self._running = True
        self.set_status("Running")
        self._reload_btn.configure(state=tk.NORMAL)
        self._append_feed_line(
            "Reloading... restarted daemon with updated code"
        )

    def reload(self) -> None:
        """Programmatic reload entry point.

        Classifies changes and takes the minimal action needed.
        Can be called from external code, not just the button.
        """
        if not self._running:
            logger.warning("reload() called but daemon is not running")
            return
        self._reload_btn.configure(state=tk.DISABLED)
        self.set_status("Reloading...")
        self._append_feed_line("Reloading...")
        threading.Thread(target=self._reload_thread, daemon=True).start()

    # ------------------------------------------------------------------
    # Window visibility
    # ------------------------------------------------------------------

    def _show_window(self) -> None:
        """Show the launcher window (called from tray double-click)."""
        self.root.after(0, self._do_show)

    def _do_show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self) -> None:
        """Hide the launcher window (minimize to tray)."""
        self.root.withdraw()

    # ------------------------------------------------------------------
    # Activity feed
    # ------------------------------------------------------------------

    def _append_feed_line(self, line: str) -> None:
        """Append a line to the activity feed (thread-safe via root.after).

        Auto-scrolls to the bottom and trims to ``_MAX_FEED_LINES``.
        """
        self.root.after(0, self._do_append_feed, line)

    def _do_append_feed(self, line: str) -> None:
        """Actually insert the line into the Text widget (main-thread)."""
        try:
            self._feed_text.configure(state=tk.NORMAL)
            self._feed_text.insert(tk.END, line + "\n")

            # Trim to max lines
            line_count = int(self._feed_text.index("end-1c").split(".")[0])
            if line_count > _MAX_FEED_LINES:
                overflow = line_count - _MAX_FEED_LINES
                self._feed_text.delete("1.0", f"{overflow + 1}.0")

            self._feed_text.see(tk.END)
            self._feed_text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass  # Widget destroyed

    # ------------------------------------------------------------------
    # Live stats polling
    # ------------------------------------------------------------------

    def _start_stats_polling(self) -> None:
        """Start periodic refresh of the stats panel."""
        if self._stats_polling:
            return
        self._stats_polling = True
        self._poll_stats()

    def _stop_stats_polling(self) -> None:
        self._stats_polling = False

    def _poll_stats(self) -> None:
        """Update stats labels from dashboard, schedule next poll."""
        if not self._stats_polling:
            return

        if self._dashboard_handler is not None:
            summary = self._dashboard_handler.summary()
            self._phase_var.set(f"Phase: {summary.get('current_phase', '-')}")
            self._words_var.set(f"Words: {summary.get('total_words', 0):,}")
            ch = summary.get("chapters_complete", 0)
            self._chapters_var.set(f"Chapters: {ch}")
            rate = summary.get("accept_rate", 0)
            self._accept_var.set(f"Accept rate: {rate:.1%}")

        # Update provider status
        if self._daemon is not None:
            try:
                label = self._daemon.active_provider_label
            except Exception:
                label = "-"
            self._provider_var.set(f"Provider: {label}")

        self.root.after(_STATS_REFRESH_MS, self._poll_stats)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the tkinter mainloop."""
        self.root.mainloop()


def main() -> None:
    """Create and run the launcher GUI."""
    from tinyassets.scoped_reset import prepare_service_writer_barrier
    from tinyassets.storage import data_dir

    writer_barrier = prepare_service_writer_barrier(data_dir())
    try:
        root = tk.Tk()
        app = LauncherApp(root=root)
        app.run()
    finally:
        writer_barrier.release()


if __name__ == "__main__":
    main()
