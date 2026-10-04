---
severity: P3
title: Every whole-row app_ui read carries the whole blank command center
filed: '2026-10-03'
summary: read_app_ui blends a 2,824-byte server constant into the owner's stored row, 97% of a fresh owner's response, resent on every sign-in and row-moving turn
---

# Every whole-row app_ui read carries the whole blank command center

Filed 2026-10-03 from PR #4358 (the command-center "Try one" picker). Not a
blocker for that PR — the lead's call — but it should not stay this way.

`tinyassets/api/app_ui.py::read_app_ui` attaches the platform's blank command
center to every whole-row read:

```python
    selector = (ui_id or "").strip()
    if not selector:
        from tinyassets.command_center_picker import PLATFORM_DEFAULT_UI

        return {"app_ui": {**document, "platform_default": PLATFORM_DEFAULT_UI}}
```

`PLATFORM_DEFAULT_UI` (`tinyassets/command_center_picker.py`) is a complete
bundle: markup, a stylesheet and the script that draws the offer and calls
`packages.list_tryable`. It is a constant, identical for every owner and every
read, and it rides the owner door on each one. The app reads this row on sign-in
(`AppUI.load`) and again after any turn that moved the row
(`AppUI.turnSettled` -> `load`), so it is not a once-per-session cost.

Two consequences already observed:

1. **The read's shape stopped matching the write's.** This broke
   `tests/test_custom_ui_first_install.py::test_the_handle_saves_and_reads_the_callers_own_row`,
   which asserted `_read(...)["app_ui"] == saved["app_ui"]`. The test now
   compares the stored fields and asserts `platform_default` is a read-only
   addition, but the underlying oddity is that one response blends a stored
   row with a server constant under the same key.
2. **A per-read payload nobody caches.** The bundle cannot change between
   reads, so every byte after the first is waste on a surface that
   `AGENTS.md` treats as uptime-critical.

Worth settling before the picker's offer grows (an image asset in the blank
bundle would make this sharply worse).

Shape that would resolve it, for whoever picks it up: serve the blank bundle
from its own read (`read_graph target="command_center_default"` or a static
route the frame fetches) and have `AppUI` request it only when it is about to
mount it — `mountDefault` is the one consumer. That restores `app_ui` to being
the owner's stored row and nothing else.

Measured 2026-10-03 (`json.dumps(PLATFORM_DEFAULT_UI)`): **2,824 bytes** —
markup 395, style 680, script 1,522. A fresh owner's row is 77 bytes on its
own, so the read for someone who has installed nothing goes from 77 to 2,923
bytes: the constant is **97% of the response**, repeated on every sign-in and
every row-moving turn.

That is small in absolute terms, which is why this is a follow-up and not a
blocker. The reason to fix it is the shape, not the bytes — and that the bytes
only grow from here.
