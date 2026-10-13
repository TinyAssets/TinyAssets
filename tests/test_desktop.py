"""Tests for the desktop tray, dashboard, notifications, and icon generation.

The tray tests mock pystray since the actual system tray requires a
display server.  Dashboard and notification tests run fully in-process.

pystray is an OPTIONAL dependency. `tinyassets/desktop/tray.py` tolerates its
absence by binding `Icon`/`Menu`/`MenuItem` to None, so on a headless runner
`_build_menu` died at `MenuItem(...)` with `TypeError: 'NoneType' object is
not callable`
and 13 tests here failed on Linux CI while passing on any dev box with pystray
installed — which is why they sat quarantined. `_pystray_stubs` below supplies
faithful stand-ins ONLY when the real package is missing, so CI exercises the
same TrayApp logic a dev box does. Reproduce the CI condition locally by making
the import fail (`sys.modules["pystray"] = None`) before importing the module.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tinyassets.desktop.dashboard import DashboardHandler, DashboardMetrics
from tinyassets.desktop.icon_gen import (
    ACCENT,
    create_icon_image,
    draw_mark,
    generate_icon,
    mark_svg,
)
from tinyassets.desktop.notifications import NotificationManager
from tinyassets.desktop.tray import TrayApp, _create_icon_image

# =====================================================================
# Icon image generation
# =====================================================================


class TestIconImage:
    def test_create_icon_image(self):
        img = _create_icon_image(64)
        assert img.size == (64, 64)
        # The mark is drawn on a rounded paper tile, so the corners are
        # transparent: an app icon with square corners would be wrong.
        assert img.mode == "RGBA"

    def test_create_icon_custom_size(self):
        img = _create_icon_image(128)
        assert img.size == (128, 128)


# =====================================================================
# TrayApp (mocked pystray)
# =====================================================================


class _StubMenuItem:
    """Stand-in for `pystray.MenuItem`, modelled on the real 0.19.5 class.

    Every deviation from the real API here has already produced a false
    green once, so each choice is deliberate:

    * **Strict signature.** The real `__init__` is
      `(text, action, checked=None, radio=False, default=False,
      visible=True, enabled=True)` and rejects anything else — a typo like
      `enable=False` raises TypeError. An earlier version of this stub took
      `**kwargs`, which would have let that typo pass CI and fail on a real
      tray.
    * **`text` resolves callables.** Real `MenuItem.text` is a property that
      calls `self._text()` when it is callable. An earlier version stored it
      verbatim, so `isinstance(item.text, str)` was False for callable text
      where real pystray returns a str — inverting the label comprehensions
      below.
    """

    def __init__(self, text, action, checked=None, radio=False,
                 default=False, visible=True, enabled=True):
        self._text = text
        self.action = action
        self.checked = checked
        self.radio = radio
        self.default = default
        self.visible = visible
        self.enabled = enabled

    @property
    def text(self):
        return self._text() if callable(self._text) else self._text


class _StubMenu:
    """Stand-in for `pystray.Menu`.

    `SEPARATOR` is a real `MenuItem("- - - -", None)`, matching pystray
    (`pystray/_base.py`), NOT a bare sentinel. That distinction is
    load-bearing: the separator therefore HAS `.text`, so the label
    comprehensions below include it exactly as they do under real pystray.
    A sentinel without `.text` was silently skipped, so the stub and the
    real package disagreed about the menu's contents.

    `_items` is a tuple, matching the real attribute these tests introspect.
    """

    def __init__(self, *items):
        self._items = tuple(items)


_StubMenu.SEPARATOR = _StubMenuItem("- - - -", None)


@pytest.fixture(autouse=True)
def _pystray_stubs(monkeypatch):
    """Install stand-ins ONLY when pystray is genuinely unavailable.

    Installing them unconditionally would mean nothing ever exercised the
    real pystray API, and a stub that quietly drifted from it would keep
    these tests green while the tray broke. This way a machine WITH pystray
    keeps testing against the real classes, and the stubs cover only the
    environment that would otherwise fail outright.
    """
    from tinyassets.desktop import tray as _tray

    if _tray.MenuItem is None:
        monkeypatch.setattr(_tray, "MenuItem", _StubMenuItem)
    if _tray.Menu is None:
        monkeypatch.setattr(_tray, "Menu", _StubMenu)




class TestTrayApp:
    def test_init_defaults(self):
        app = TrayApp()
        assert app._status == "Idle"
        assert app._paused is False

    def test_update_status(self):
        app = TrayApp()
        app._icon = MagicMock()
        app.update_status("Writing chapter 3")
        assert app._status == "Writing chapter 3"

    def test_start_creates_icon(self):
        with patch("tinyassets.desktop.tray.Icon") as MockIcon:
            mock_icon = MagicMock()
            MockIcon.return_value = mock_icon

            app = TrayApp()
            app.start()

            MockIcon.assert_called_once()
            mock_icon.run_detached.assert_called_once()

    def test_start_without_pystray_says_why(self, monkeypatch):
        """Headless `start()` must name the real cause.

        The import guard in tray.py binds Icon/Menu/MenuItem to None so that
        importing the module stays safe in a headless container. Starting a
        tray there cannot work, and before this guard the failure was
        `TypeError: 'NoneType' object is not callable` raised from
        `MenuItem(...)` inside `_build_menu` — which says nothing about
        pystray. Forced here rather than skipped, so the check runs on every
        platform including the dev boxes that DO have pystray.
        """
        from tinyassets.desktop import tray as _tray

        monkeypatch.setattr(_tray, "Icon", None)
        app = TrayApp()
        with pytest.raises(RuntimeError) as excinfo:
            app.start()
        assert "pystray" in str(excinfo.value)
        assert app._icon is None, "a failed start must not leave a half-built icon"

    def test_start_is_idempotent(self):
        with patch("tinyassets.desktop.tray.Icon") as MockIcon:
            mock_icon = MagicMock()
            MockIcon.return_value = mock_icon

            app = TrayApp()
            app.start()
            app.start()

            MockIcon.assert_called_once()
            mock_icon.run_detached.assert_called_once()

    def test_stop(self):
        app = TrayApp()
        mock_icon = MagicMock()
        app._icon = mock_icon
        app.stop()
        mock_icon.stop.assert_called_once()
        assert app._icon is None

    def test_notify(self):
        app = TrayApp()
        app._icon = MagicMock()
        app.notify("Chapter Done", "100 words")
        app._icon.notify.assert_called_once_with("100 words", title="Chapter Done")

    def test_pause_handler(self):
        paused = []
        app = TrayApp(on_pause=lambda: paused.append(True))
        app._icon = MagicMock()
        app._handle_pause()
        assert app._paused is True
        assert len(paused) == 1

    def test_resume_handler(self):
        resumed = []
        app = TrayApp(on_resume=lambda: resumed.append(True))
        app._icon = MagicMock()
        app._paused = True
        app._handle_resume()
        assert app._paused is False
        assert len(resumed) == 1

    def test_start_handler(self):
        started = []
        app = TrayApp(on_start=lambda: started.append(True))
        app._icon = MagicMock()
        app._handle_start()
        assert len(started) == 1

    def test_quit_handler(self):
        quit_called = []
        app = TrayApp(on_quit=lambda: quit_called.append(True))
        app._icon = MagicMock()
        app._handle_quit()
        assert len(quit_called) == 1
        assert app._icon is None  # stop() was called


# =====================================================================
# TrayApp -- extended status
# =====================================================================


class TestTrayExtendedStatus:
    def test_update_extended_status_universe(self):
        app = TrayApp()
        app._icon = MagicMock()
        app.update_extended_status(universe_name="ashwater")
        assert app._universe_name == "ashwater"

    def test_update_extended_status_words(self):
        app = TrayApp()
        app._icon = MagicMock()
        app.update_extended_status(word_count=5000)
        assert app._word_count == 5000

    def test_update_extended_status_tunnel(self):
        app = TrayApp()
        app._icon = MagicMock()
        app.update_extended_status(tunnel_url="https://test.trycloudflare.com")
        assert app._tunnel_url == "https://test.trycloudflare.com"

    def test_tooltip_includes_universe_and_words(self):
        app = TrayApp()
        app._icon = MagicMock()
        app.update_extended_status(universe_name="ashwater", word_count=1500)
        # Tooltip should be set on the icon
        assert "ashwater" in app._icon.title
        assert "1,500" in app._icon.title

    def test_menu_shows_extended_status(self):
        app = TrayApp()
        app._icon = MagicMock()
        app._universe_name = "ashwater"
        app._word_count = 3000
        app._tunnel_url = "https://test.trycloudflare.com"
        menu = app._build_menu()
        labels = [
            item.text if isinstance(item.text, str) else ""
            for item in menu._items
            if hasattr(item, "text")
        ]
        assert any("ashwater" in label for label in labels)
        assert any("3,000" in label for label in labels)
        assert any("trycloudflare" in label for label in labels)

    def test_menu_omits_empty_fields(self):
        app = TrayApp()
        app._icon = MagicMock()
        menu = app._build_menu()
        labels = [
            item.text if isinstance(item.text, str) else ""
            for item in menu._items
            if hasattr(item, "text")
        ]
        # No universe or tunnel lines when empty
        assert not any("Universe:" in label for label in labels)
        assert not any("Tunnel:" in label for label in labels)


# =====================================================================
# DashboardMetrics
# =====================================================================


class TestDashboardMetrics:
    def test_record_accept(self):
        m = DashboardMetrics()
        m.record_accept()
        assert m.accept_rate == 1.0
        assert m._accepted == 1
        assert m._evaluated == 1

    def test_record_reject(self):
        m = DashboardMetrics()
        m.record_accept()
        m.record_reject()
        assert m.accept_rate == 0.5

    def test_update_wph(self):
        m = DashboardMetrics()
        m.total_words = 1000
        m.update_wph()
        # Should be > 0 (just started, so very high).
        assert m.words_per_hour > 0

    def test_seed_from_db(self):
        """seed_from_db recovers word_count, scenes, chapters from story.db."""
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "story.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE scene_history ("
                "scene_id TEXT PRIMARY KEY, universe_id TEXT, "
                "book_number INTEGER, chapter_number INTEGER, "
                "scene_number INTEGER, word_count INTEGER DEFAULT 0, "
                "verdict TEXT DEFAULT 'accept', summary TEXT DEFAULT '')"
            )
            conn.execute(
                "INSERT INTO scene_history VALUES "
                "('s1','u',1,1,1,500,'accept',''), "
                "('s2','u',1,1,2,600,'accept',''), "
                "('s3','u',1,2,1,400,'revert','')"
            )
            conn.commit()
            conn.close()

            m = DashboardMetrics()
            m.seed_from_db(db_path)
            assert m.scenes_complete == 3
            assert m.total_words == 1500
            assert m.chapters_complete == 2
            assert m._accepted == 2  # 2 accepts, 1 revert
            assert m._evaluated == 3
            assert m.accept_rate > 0

    def test_seed_from_db_missing_db(self):
        """seed_from_db does nothing when DB doesn't exist."""
        m = DashboardMetrics()
        m.seed_from_db("/nonexistent/path/story.db")
        assert m.scenes_complete == 0
        assert m.total_words == 0

    def test_seed_from_output_dir(self):
        """Fallback: seed from output/book-*/chapter-*/scene-*.md files."""
        with tempfile.TemporaryDirectory() as td:
            # Create output structure
            ch1 = Path(td) / "output" / "book-1" / "chapter-01"
            ch2 = Path(td) / "output" / "book-1" / "chapter-02"
            ch1.mkdir(parents=True)
            ch2.mkdir(parents=True)
            (ch1 / "scene-01.md").write_text("word " * 100, encoding="utf-8")
            (ch1 / "scene-02.md").write_text("word " * 200, encoding="utf-8")
            (ch2 / "scene-01.md").write_text("word " * 150, encoding="utf-8")

            m = DashboardMetrics()
            # Pass a non-existent DB so it falls through to file scan
            m.seed_from_db(str(Path(td) / "story.db"), td)
            assert m.scenes_complete == 3
            assert m.chapters_complete == 2
            assert m.total_words == 450  # 100 + 200 + 150

    def test_seed_from_output_dir_empty(self):
        """seed_from_output_dir handles missing output dir gracefully."""
        with tempfile.TemporaryDirectory() as td:
            m = DashboardMetrics()
            m.seed_from_db(str(Path(td) / "story.db"), td)
            assert m.scenes_complete == 0


# =====================================================================
# DashboardHandler
# =====================================================================


class TestDashboardHandler:
    def test_phase_start(self):
        tray = MagicMock()
        dh = DashboardHandler(tray=tray)
        dh.handle_event({"type": "phase_start", "phase": "orient"})
        assert dh.metrics.current_phase == "orient"
        tray.update_status.assert_called()

    def test_draft_progress(self):
        dh = DashboardHandler()
        dh.handle_event({"type": "draft_progress", "word_count": 500})
        # draft_progress is an in-progress indicator — does not update total_words.
        # total_words is updated by scene_complete or run_book output.
        assert dh.metrics.total_words == 0

    def test_judge_result_accept(self):
        dh = DashboardHandler()
        dh.handle_event({
            "type": "judge_result",
            "judge": "codex",
            "verdict": "accept",
        })
        assert dh.metrics._accepted == 1

    def test_judge_result_revert(self):
        dh = DashboardHandler()
        dh.handle_event({
            "type": "judge_result",
            "judge": "codex",
            "verdict": "revert",
        })
        assert dh.metrics._evaluated == 1
        assert dh.metrics._accepted == 0

    def test_scene_complete(self):
        dh = DashboardHandler()
        dh.handle_event({
            "type": "scene_complete",
            "scene_number": 3,
            "word_count": 200,
        })
        assert dh.metrics.scenes_complete == 1
        assert dh.metrics.total_words == 200

    def test_chapter_complete_notifies(self):
        tray = MagicMock()
        dh = DashboardHandler(tray=tray)
        dh.handle_event({"type": "chapter_complete", "chapter": 5})
        assert dh.metrics.chapters_complete == 1
        tray.notify.assert_called()

    def test_book_complete_notifies(self):
        tray = MagicMock()
        dh = DashboardHandler(tray=tray)
        dh.handle_event({"type": "book_complete", "title": "The Dawn"})
        tray.notify.assert_called()

    def test_stuck_recovery(self):
        tray = MagicMock()
        dh = DashboardHandler(tray=tray)
        dh.handle_event({"type": "stuck_recovery", "level": 2})
        tray.notify.assert_called()

    def test_error_event(self):
        dh = DashboardHandler()
        dh.handle_event({"type": "error", "message": "something broke"})
        # Should not raise.

    def test_unknown_event(self):
        dh = DashboardHandler()
        dh.handle_event({"type": "unknown_event"})
        # Should not raise.

    def test_summary(self):
        dh = DashboardHandler()
        dh.metrics.total_words = 5000
        dh.metrics.chapters_complete = 3
        s = dh.summary()
        assert s["total_words"] == 5000
        assert s["chapters_complete"] == 3
        assert "accept_rate" in s

    def test_no_tray_does_not_crash(self):
        dh = DashboardHandler(tray=None)
        dh.handle_event({"type": "phase_start", "phase": "draft"})
        dh.handle_event({"type": "chapter_complete", "chapter": 1})
        # Should not raise.

    def test_log_callback_called_on_judge_result(self):
        lines = []
        dh = DashboardHandler(log_callback=lines.append)
        dh.handle_event({
            "type": "judge_result",
            "judge": "codex",
            "verdict": "accept",
        })
        assert len(lines) == 1
        assert "ACCEPT" in lines[0]

    def test_log_callback_called_on_scene_complete(self):
        lines = []
        dh = DashboardHandler(log_callback=lines.append)
        dh.handle_event({
            "type": "scene_complete",
            "scene_number": 5,
            "word_count": 300,
        })
        assert len(lines) == 1
        assert "Scene 5 complete" in lines[0]
        assert "300" in lines[0]

    def test_log_callback_called_on_chapter_complete(self):
        lines = []
        dh = DashboardHandler(log_callback=lines.append)
        dh.handle_event({"type": "chapter_complete", "chapter": 3})
        assert len(lines) == 1
        assert "Chapter 3 complete" in lines[0]

    def test_log_callback_called_on_book_complete(self):
        lines = []
        dh = DashboardHandler(log_callback=lines.append)
        dh.handle_event({"type": "book_complete", "title": "The Dawn"})
        assert len(lines) == 1
        assert "The Dawn" in lines[0]

    def test_log_callback_called_on_stuck_recovery(self):
        lines = []
        dh = DashboardHandler(log_callback=lines.append)
        dh.handle_event({"type": "stuck_recovery", "level": 2})
        assert len(lines) == 1
        assert "level 2" in lines[0]

    def test_log_callback_called_on_error(self):
        lines = []
        dh = DashboardHandler(log_callback=lines.append)
        dh.handle_event({"type": "error", "message": "disk full"})
        assert len(lines) == 1
        assert "disk full" in lines[0]

    def test_log_callback_not_called_on_phase_start(self):
        """phase_start does not emit a log line (nodes do that)."""
        lines = []
        dh = DashboardHandler(log_callback=lines.append)
        dh.handle_event({"type": "phase_start", "phase": "orient"})
        assert len(lines) == 0

    def test_log_callback_exception_does_not_propagate(self):
        def bad_callback(line):
            raise RuntimeError("boom")

        dh = DashboardHandler(log_callback=bad_callback)
        # Should not raise
        dh.handle_event({"type": "error", "message": "test"})

    def test_log_method_with_no_callback(self):
        dh = DashboardHandler()
        # _log should be a no-op when no callback set
        dh._log("test line")  # Should not raise


# =====================================================================
# NotificationManager
# =====================================================================


class TestNotificationManager:
    def test_chapter_complete(self):
        tray = MagicMock()
        nm = NotificationManager(tray=tray)
        nm.chapter_complete(5, word_count=1500)
        tray.notify.assert_called_once()

    def test_book_complete(self):
        tray = MagicMock()
        nm = NotificationManager(tray=tray)
        nm.book_complete("The Dawn", total_words=50000)
        tray.notify.assert_called_once()

    def test_stuck_recovery(self):
        tray = MagicMock()
        nm = NotificationManager(tray=tray)
        nm.stuck_recovery(3)
        tray.notify.assert_called_once()

    def test_error(self):
        tray = MagicMock()
        nm = NotificationManager(tray=tray)
        nm.error("something went wrong")
        tray.notify.assert_called_once()

    def test_no_tray_logs(self):
        nm = NotificationManager(tray=None)
        nm.chapter_complete(1)
        nm.book_complete("Test")
        nm.error("test error")
        # Should not raise.

    def test_tray_notify_failure(self):
        tray = MagicMock()
        tray.notify.side_effect = RuntimeError("no display")
        nm = NotificationManager(tray=tray)
        nm.chapter_complete(1)
        # Should not raise; falls back to logging.


# =====================================================================
# Icon generator (icon_gen.py)
# =====================================================================


class TestIconGen:
    def test_create_icon_image_default_size(self):
        img = create_icon_image()
        assert img.size == (64, 64)
        # Rounded paper tile: the corners must stay transparent.
        assert img.mode == "RGBA"

    def test_create_icon_image_custom_sizes(self):
        for size in (16, 32, 48, 256):
            img = create_icon_image(size)
            assert img.size == (size, size)

    def test_create_icon_image_has_content(self):
        """Icon should not be a single flat color."""
        img = create_icon_image(64)
        colors = img.getcolors(maxcolors=1000)
        assert colors is not None
        assert len(colors) > 1

    def test_optical_variants_are_distinct(self):
        """Large, app, and favicon art must not be one illustration resized."""
        full = draw_mark(64, tile=False, detail="full")
        compact = draw_mark(64, tile=False, detail="compact")
        micro = draw_mark(64, tile=False, detail="micro")
        assert full.tobytes() != compact.tobytes()
        assert compact.tobytes() != micro.tobytes()
        assert mark_svg(tile=False, detail="full") != mark_svg(
            tile=False, detail="compact"
        )

    def test_favicon_sizes_use_micro_drawing(self):
        assert create_icon_image(16).tobytes() == draw_mark(
            16, tile=True, detail="micro"
        ).tobytes()
        assert create_icon_image(32).tobytes() == draw_mark(
            32, tile=True, detail="micro"
        ).tobytes()
        assert create_icon_image(48).tobytes() == draw_mark(
            48, tile=True, detail="compact"
        ).tobytes()

    def test_icns_sizes_use_matching_optical_drawings(self, monkeypatch, tmp_path):
        from WebSite.brand import render_marks

        calls = []

        class FakeImage:
            def save(self, *_args, **_kwargs):
                return None

        def fake_draw_mark(size, *, tile, detail):
            calls.append((size, tile, detail))
            return FakeImage()

        monkeypatch.setattr(render_marks, "draw_mark", fake_draw_mark)
        monkeypatch.setattr(render_marks, "REPO", tmp_path)
        render_marks._icns(tmp_path / "icon.icns")

        assert calls == [
            (16, True, "micro"),
            (32, True, "micro"),
            (64, True, "compact"),
            (128, True, "compact"),
            (256, True, "compact"),
            (512, True, "compact"),
            (1024, True, "compact"),
        ]

    def test_micro_icon_keeps_the_ember_moon(self):
        icon = create_icon_image(32)
        assert any(
            icon.getpixel((x, y))[:3] == ACCENT
            for y in range(icon.height)
            for x in range(icon.width)
        )

    def test_unknown_detail_fails_loudly(self):
        with pytest.raises(ValueError, match="detail must be"):
            draw_mark(64, detail="miniature")

    def test_generate_icon_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.ico"
            result = generate_icon(out)
            assert result == out
            assert out.exists()
            assert out.stat().st_size > 0

    def test_generate_icon_is_valid_ico(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test.ico"
            generate_icon(out)
            with Image.open(out) as ico:
                assert ico.format == "ICO"


# =====================================================================
# Tray: Show Window & icon_path
# =====================================================================


class TestTrayThrottling:
    """Tests for notification and menu refresh throttling."""

    def test_notify_throttle_suppresses_rapid_calls(self):
        """Second notify within cooldown should be suppressed."""
        app = TrayApp()
        app._icon = MagicMock()

        # First call goes through
        app.notify("First", "msg1")
        assert app._icon.notify.call_count == 1

        # Second call within cooldown is suppressed
        app.notify("Second", "msg2")
        assert app._icon.notify.call_count == 1

    def test_notify_throttle_allows_after_cooldown(self):
        """Notify should work again after the cooldown expires."""
        import time as _time

        app = TrayApp()
        app._icon = MagicMock()

        # First call
        app.notify("First", "msg1")
        assert app._icon.notify.call_count == 1

        # Simulate cooldown expiry by backdating last notify time
        app._last_notify_time = _time.monotonic() - app._NOTIFY_COOLDOWN - 1
        app.notify("Second", "msg2")
        assert app._icon.notify.call_count == 2

    def test_notify_no_icon_is_noop(self):
        app = TrayApp()
        app._icon = None
        app.notify("Test", "msg")  # Should not raise

    def test_update_status_throttles_menu_rebuild(self):
        """Rapid update_status calls should not rebuild menu every time."""
        app = TrayApp()
        app._icon = MagicMock()

        # First update triggers immediate rebuild
        app.update_status("Phase 1")
        assert app._status == "Phase 1"

        assert app._icon.update_menu.call_count == 1, (
            "the first update_status should rebuild the menu immediately"
        )

        # Rapid second update should be deferred, not immediate.
        app.update_status("Phase 2")
        assert app._status == "Phase 2"
        # The point of the test. Without this the test asserted only that two
        # assignments took effect and would have passed with the throttle
        # removed entirely; the original even said "rebuilt at most once" in
        # a comment instead of an assertion.
        assert app._icon.update_menu.call_count == 1, (
            f"second update within the cooldown rebuilt the menu again "
            f"({app._icon.update_menu.call_count} rebuilds); it should be "
            f"deferred to the pending timer"
        )


class TestTrayShowWindow:
    def test_show_window_callback(self):
        shown = []
        app = TrayApp(on_show_window=lambda: shown.append(True))
        app._icon = MagicMock()
        app._handle_show_window()
        assert len(shown) == 1

    def test_default_show_window_noop(self):
        app = TrayApp()
        app._icon = MagicMock()
        app._handle_show_window()  # Should not raise

    def test_icon_path_accepted(self):
        """TrayApp accepts icon_path parameter."""
        app = TrayApp(icon_path="/fake/icon.ico")
        assert app._icon_path == "/fake/icon.ico"

    def test_load_icon_image_fallback(self):
        """_load_icon_image falls back when path does not exist."""
        from tinyassets.desktop.tray import _load_icon_image

        img = _load_icon_image("/nonexistent/path.ico")
        assert img.size == (64, 64)

    def test_load_icon_image_from_file(self):
        """_load_icon_image loads from a real .ico file."""
        from tinyassets.desktop.tray import _load_icon_image

        with tempfile.TemporaryDirectory() as tmpdir:
            ico_path = Path(tmpdir) / "test.ico"
            generate_icon(ico_path)
            img = _load_icon_image(ico_path)
            assert img.size[0] > 0
            assert img.mode == "RGBA"
