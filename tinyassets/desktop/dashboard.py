"""Streaming dashboard -- processes graph events for display.

Translates LangGraph custom stream events into status updates,
notifications, and metrics for the tray and optional web dashboard.

Phase H additions
-----------------
``DispatcherPane`` and ``QueuePane`` are data panes
populated from a ``daemon_overview`` response.  Each pane wraps its
``refresh()`` method in exception isolation so a malformed payload
cannot crash the UI thread (preflight §4.3 invariant 2 / R3).

``DashboardHandler.refresh_from_overview(data)`` fans out to both
panes, also under per-pane exception isolation.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class DashboardMetrics:
    """Accumulated metrics for display."""

    total_words: int = 0
    chapters_complete: int = 0
    scenes_complete: int = 0
    accept_rate: float = 0.0
    current_phase: str = "idle"
    current_chapter: int = 0
    current_scene: int = 0
    words_per_hour: float = 0.0
    start_time: float = field(default_factory=time.monotonic)
    _accepted: int = 0
    _evaluated: int = 0

    def record_accept(self) -> None:
        self._accepted += 1
        self._evaluated += 1
        self._update_rate()

    def record_reject(self) -> None:
        self._evaluated += 1
        self._update_rate()

    def _update_rate(self) -> None:
        if self._evaluated > 0:
            self.accept_rate = self._accepted / self._evaluated

    def update_wph(self) -> None:
        elapsed_hours = (time.monotonic() - self.start_time) / 3600
        if elapsed_hours > 0:
            self.words_per_hour = self.total_words / elapsed_hours

    def seed_from_db(self, db_path: str, universe_path: str = "") -> None:
        """Seed baseline counts from the world state DB.

        Called at daemon startup so that status.json reflects existing
        work rather than starting at zero after a restart or universe
        switch.  Falls back to scanning output files if the DB doesn't
        exist or has no scene_history table.
        """
        seeded = False
        if Path(db_path).exists():
            try:
                conn = sqlite3.connect(db_path)
                try:
                    row = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(word_count), 0), "
                        "COUNT(DISTINCT chapter_number) FROM scene_history"
                    ).fetchone()
                    if row and row[0] > 0:
                        self.scenes_complete = row[0]
                        self.total_words = row[1]
                        self.chapters_complete = row[2]
                        accepted = conn.execute(
                            "SELECT COUNT(*) FROM scene_history "
                            "WHERE verdict IN ('accept', 'second_draft')"
                        ).fetchone()
                        if accepted:
                            self._accepted = accepted[0]
                            self._evaluated = row[0]
                            self._update_rate()
                        seeded = True
                finally:
                    conn.close()
            except (sqlite3.Error, OSError):
                logger.debug("Failed to seed metrics from DB", exc_info=True)

        if not seeded and universe_path:
            self._seed_from_output_dir(universe_path)

    def _seed_from_output_dir(self, universe_path: str) -> None:
        """Fallback: scan output/book-*/chapter-*/scene-*.md files."""
        output_dir = Path(universe_path) / "output"
        if not output_dir.exists():
            return
        try:
            chapters: set[str] = set()
            scenes = 0
            words = 0
            for book_dir in output_dir.iterdir():
                if not book_dir.is_dir() or not book_dir.name.startswith("book-"):
                    continue
                for ch_dir in book_dir.iterdir():
                    if not ch_dir.is_dir() or not ch_dir.name.startswith("chapter-"):
                        continue
                    for scene_file in ch_dir.iterdir():
                        if not scene_file.is_file() or not scene_file.suffix == ".md":
                            continue
                        scenes += 1
                        chapters.add(f"{book_dir.name}/{ch_dir.name}")
                        try:
                            text = scene_file.read_text(encoding="utf-8")
                            words += len(text.split())
                        except OSError:
                            pass
            self.scenes_complete = scenes
            self.chapters_complete = len(chapters)
            self.total_words = words
        except OSError:
            logger.debug("Failed to seed metrics from output dir", exc_info=True)


# ---------------------------------------------------------------------------
# Phase H: daemon_overview panes
# ---------------------------------------------------------------------------

class DispatcherPane:
    """Tier status + config display pane.

    Refreshed from the ``daemon_overview`` response body.  Provides
    ``tier_status`` and ``config`` for tray menu + dashboard window
    consumers.  Exception-isolated per preflight §4.3 invariant 2.
    """

    def __init__(self) -> None:
        self.tier_status: dict[str, str] = {}
        self.config: dict[str, Any] = {}
        self.last_error: str | None = None

    def refresh(self, overview_data: dict[str, Any]) -> None:
        """Update from a ``daemon_overview`` response dict."""
        try:
            dispatcher = overview_data.get("dispatcher", {})
            self.tier_status = dict(dispatcher.get("tier_status_map", {}))
            self.config = dict(dispatcher.get("config", {}))
            self.last_error = None
        except Exception as exc:
            logger.warning("DispatcherPane.refresh failed: %s", exc)
            self.last_error = str(exc)

    def summary(self) -> dict[str, Any]:
        """Snapshot of tier state for tray / dashboard display."""
        return {
            "tier_status": dict(self.tier_status),
            "config": dict(self.config),
            "last_error": self.last_error,
        }


class QueuePane:
    """Top-N pending BranchTask display pane.

    Refreshed from the ``daemon_overview`` response body.  The tray menu
    and dashboard window read ``pending_count`` and ``top_tasks``.
    Exception-isolated per preflight §4.3 invariant 2.
    """

    def __init__(self) -> None:
        self.pending_count: int = 0
        self.top_tasks: list[dict[str, Any]] = []
        self.idle_reason: str | None = None
        self.last_error: str | None = None

    def refresh(self, overview_data: dict[str, Any]) -> None:
        """Update from a ``daemon_overview`` response dict."""
        try:
            queue = overview_data.get("queue", {})
            self.pending_count = int(queue.get("pending_count", 0))
            self.top_tasks = list(queue.get("top", []))
            # Surface daemon idle_reason so QueuePane can expose §4.9 Q9 item #2.
            run_state = overview_data.get("run_state", {})
            self.idle_reason = run_state.get("idle_reason")
            self.last_error = None
        except Exception as exc:
            logger.warning("QueuePane.refresh failed: %s", exc)
            self.last_error = str(exc)

    def summary(self) -> dict[str, Any]:
        """Snapshot of queue state for tray / dashboard display."""
        return {
            "pending_count": self.pending_count,
            "top_tasks": list(self.top_tasks),
            "idle_reason": self.idle_reason,
            "last_error": self.last_error,
        }


class DashboardHandler:
    """Processes graph stream events and updates tray/metrics.

    Parameters
    ----------
    tray : TrayApp | None
        System tray to update.  None disables tray updates.
    log_callback : callable or None
        If provided, called with ``(str,)`` for every human-readable
        activity line.  Used by the launcher GUI to populate its
        scrolling activity feed.
    """

    def __init__(
        self,
        tray: Any = None,
        log_callback: Callable[[str], Any] | None = None,
    ) -> None:
        self._tray = tray
        self._log_callback = log_callback
        self.metrics = DashboardMetrics()
        # Phase H: dispatcher and queue panes
        self.dispatcher_pane = DispatcherPane()
        self.queue_pane = QueuePane()

    def handle_event(self, event: dict[str, Any]) -> None:
        """Dispatch a custom stream event from the graph."""
        event_type = event.get("type", "")

        handler = {
            "phase_start": self._on_phase_start,
            "draft_progress": self._on_draft_progress,
            "judge_result": self._on_judge_result,
            "scene_complete": self._on_scene_complete,
            "chapter_complete": self._on_chapter_complete,
            "book_complete": self._on_book_complete,
            "stuck_recovery": self._on_stuck_recovery,
            "error": self._on_error,
        }.get(event_type)

        if handler:
            handler(event)
        else:
            logger.debug("Unhandled event type: %s", event_type)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _log(self, line: str) -> None:
        """Forward a human-readable activity line to the log callback."""
        if self._log_callback is not None:
            try:
                self._log_callback(line)
            except Exception:
                logger.debug("log_callback failed", exc_info=True)

    def _on_phase_start(self, event: dict[str, Any]) -> None:
        phase = event.get("phase", "unknown")
        self.metrics.current_phase = phase
        self._update_tray(f"Phase: {phase}")

    def _on_draft_progress(self, event: dict[str, Any]) -> None:
        wc = event.get("word_count", 0)
        # Don't overwrite total_words — this is the in-progress scene count.
        # _on_scene_complete accumulates the final count properly.
        self._update_tray(f"Writing... {wc} words")

    def _on_judge_result(self, event: dict[str, Any]) -> None:
        judge = event.get("judge", "?")
        verdict = event.get("verdict", "?")
        if verdict in ("accept", "second_draft"):
            self.metrics.record_accept()
        else:
            self.metrics.record_reject()
        self._update_tray(f"Judge {judge}: {verdict}")
        self._log(f"Judge {judge}: {verdict.upper()}")

    def _on_scene_complete(self, event: dict[str, Any]) -> None:
        self.metrics.scenes_complete += 1
        words = event.get("word_count", 0)
        self.metrics.total_words += words
        self.metrics.update_wph()
        self.metrics.current_scene = event.get("scene_number", 0)
        self._log(
            f"Scene {event.get('scene_number', '?')} complete "
            f"({words:,} words, total {self.metrics.total_words:,})"
        )

    def _on_chapter_complete(self, event: dict[str, Any]) -> None:
        ch = event.get("chapter", "?")
        self.metrics.chapters_complete += 1
        if isinstance(ch, int):
            self.metrics.current_chapter = ch
        elif isinstance(ch, str) and ch.isdigit():
            self.metrics.current_chapter = int(ch)
        else:
            self.metrics.current_chapter = 0
        self._update_tray(f"Chapter {ch} complete!")
        self._notify(f"Chapter {ch} Complete", f"Total words: {self.metrics.total_words}")
        self._log(f"Chapter {ch} complete! ({self.metrics.total_words:,} words total)")

    def _on_book_complete(self, event: dict[str, Any]) -> None:
        title = event.get("title", "Untitled")
        self._update_tray("Book complete!")
        self._notify(f"Book Complete: {title}", f"Total words: {self.metrics.total_words}")
        self._log(f"Book complete: {title} ({self.metrics.total_words:,} words)")

    def _on_stuck_recovery(self, event: dict[str, Any]) -> None:
        level = event.get("level", "?")
        self._update_tray(f"Stuck recovery (level {level})")
        self._notify("Stuck Recovery", f"Initiated at level {level}")
        self._log(f"Stuck recovery initiated (level {level})")

    def _on_error(self, event: dict[str, Any]) -> None:
        msg = event.get("message", "Unknown error")
        logger.error("Dashboard received error event: %s", msg)
        self._update_tray(f"Error: {msg[:50]}")
        self._log(f"ERROR: {msg[:80]}")

    # ------------------------------------------------------------------
    # Tray integration
    # ------------------------------------------------------------------

    def _update_tray(self, status: str) -> None:
        if self._tray is not None:
            try:
                self._tray.update_status(status)
            except Exception:
                logger.debug("Tray update failed", exc_info=True)

    def _notify(self, title: str, message: str) -> None:
        if self._tray is not None:
            try:
                self._tray.notify(title, message)
            except Exception:
                logger.debug("Tray notify failed", exc_info=True)

    # ------------------------------------------------------------------
    # Phase H: daemon_overview refresh
    # ------------------------------------------------------------------

    def refresh_from_overview(self, overview_data: dict[str, Any]) -> None:
        """Fan out a ``daemon_overview`` response to all data panes.

        Each pane is refreshed under its own try/except so a malformed
        payload in one pane cannot crash the others or the UI thread
        (preflight §4.3 invariant 2 / R3).
        """
        for pane in (self.dispatcher_pane, self.queue_pane):
            try:
                pane.refresh(overview_data)
            except Exception as exc:
                # Belt-and-suspenders: each pane already isolates
                # internally, but wrap again at fan-out level.
                logger.warning(
                    "refresh_from_overview: %s.refresh raised: %s",
                    pane.__class__.__name__,
                    exc,
                )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return current metrics as a dict for API/display."""
        m = self.metrics
        return {
            "total_words": m.total_words,
            "chapters_complete": m.chapters_complete,
            "scenes_complete": m.scenes_complete,
            "accept_rate": round(m.accept_rate, 3),
            "current_phase": m.current_phase,
            "words_per_hour": round(m.words_per_hour, 1),
            "dispatcher": self.dispatcher_pane.summary(),
            "queue": self.queue_pane.summary(),
        }
