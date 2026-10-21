"""The agent can see the UI it built: a real headless render of its own UI.

Real Chromium (``real_browser``): the shipped frame renders the stored
component, its assets and libraries; the report carries what a person would see
and what went wrong -- a PNG, frames per second, uncaught errors, refused
bridge actions -- and nothing reaches a network.
"""
# ruff: noqa: E501 -- embedded JavaScript bundles are stored verbatim
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest

from tinyassets import app_ui_store as ca
from tinyassets import ui_preview

# `api.app_ui` is imported HERE, before any test can patch what it binds: it
# copies `_base_path` out of `api.helpers` at import time, so a first import
# under such a patch would freeze the stand-in for every later test in the
# process.
from tinyassets.api import app_ui as _app_ui  # noqa: F401

OWNER, HOME = "alice", "u-alice"


@pytest.fixture(autouse=True)
def _isolate_preview_data_dir(tmp_path, monkeypatch):
    """Keep the real host-slot lock in this test's existing data directory."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


@pytest.fixture
def alice_home(tmp_path):
    """Alice is the recorded owner of her center, so her box admits her render."""
    from tinyassets.daemon_server import _connect, initialize_author_server
    from tinyassets.universe_owner import record_creation

    initialize_author_server(tmp_path)
    with _connect(tmp_path) as conn:
        record_creation(conn, universe_id=HOME, owner_id=OWNER)


def _png(rgb=(32, 160, 64), size=8) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _add(base, ui_id, **fields):
    component = {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": ui_id,
                 "name": ui_id, "markup": "", "style": "", "script": "", **fields}
    (Path(base) / HOME).mkdir(exist_ok=True)
    ca.change_app_ui_entry(base, owner_user_id=OWNER, universe_id=HOME,
                           operation="add_ui", payload={"component": component})


def _pixel(png: bytes, x: int, y: int) -> tuple[int, int, int]:
    from PIL import Image

    return Image.open(io.BytesIO(png)).convert("RGB").getpixel((x, y))


def _need_browser():
    if not ui_preview.available():
        pytest.skip("Playwright is not installed on this host")
















# --------------------------------------------------------------------------- #
# writing the screenshot into the agent's own folder
# --------------------------------------------------------------------------- #















# --------------------------------------------------------------------------- #
# the served handle: read_graph target="app_ui_preview"
# --------------------------------------------------------------------------- #










# ---- the preview bridge answers what the app's bridge answers ----------------
# Both sides are parsed out of the shipped source rather than restated here: a
# key list written down in a test is one more copy to drift (Codex 2026-10-03
# found two live keys the preview had never heard of).
#
# The parse is STRUCTURAL -- strings and comments masked, then brace matching
# that tells a function body from a plain block -- because a parse that keys on
# spelling or on indentation fails OPEN. Codex round 2 reproduced both ways
# out: whitespace before a parameter list made an action undiscoverable, and a
# return one block deeper went uncounted, each hiding a key the preview lacked.
# Discovery now fails CLOSED, and the two drifts are regression cases below.

_IN_STRING_STRUCTURE = frozenset("{}[]()<>:;,=/*'\"`!?|&\\")
_BLOCK_HEADS = frozenset({"if", "for", "while", "switch", "catch", "else", "do",
                          "try", "finally", "with"})


def _app_ui_source() -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tinyassets" / "onboarding" / "app_ui.js"
    return path.read_text(encoding="utf-8")


def _masked(source: str) -> str:
    """``source`` with comments and string innards neutralised, length preserved.

    Indices still line up with ``source``, so everything below can match braces
    and split on commas without a quoted ``{`` or a comma in an English comment
    steering it. A string keeps its ordinary characters because a key may be
    quoted (``'packages.list_tryable'``); a comment blanks to WHITESPACE, which
    the splitters strip -- blanked to anything else its words run into the next
    key, because the comma that separated them was inside the comment.
    """
    out: list[str] = []
    state, quote, index = "code", "", 0
    while index < len(source):
        char, pair = source[index], source[index:index + 2]
        if state == "code":
            if pair in ("//", "/*"):
                state = "line" if pair == "//" else "block"
                out += [" ", " "]
                index += 2
                continue
            if char in "'\"`":
                state, quote = "string", char
            out.append(char)
        elif state == "line":
            out.append(char if char == "\n" else " ")
            if char == "\n":
                state = "code"
        elif state == "block":
            if pair == "*/":
                state = "code"
                out += [" ", " "]
                index += 2
                continue
            out.append(char if char == "\n" else " ")
        elif char == "\\":  # an escape, so the next character closes nothing
            out += ["_"] * min(2, len(source) - index)
            index += 2
            continue
        elif char == quote:
            state, quote = "code", ""
            out.append(char)
        else:
            out.append("_" if char in _IN_STRING_STRUCTURE else char)
        index += 1
    masked = "".join(out)
    assert len(masked) == len(source), "the mask moved an index"
    # A regex literal would be read as code and could unbalance this; none ships
    # in app_ui.js today, and this is how that would stop being true quietly.
    assert masked.count("{") == masked.count("}"), "masked source has unbalanced braces"
    return masked


def _closes(masked: str, start: int) -> int:
    """Index of the bracket closing the one at ``masked[start]``."""
    opening, closing = masked[start], {"{": "}", "(": ")", "[": "]"}[masked[start]]
    depth = 0
    for index in range(start, len(masked)):
        if masked[index] == opening:
            depth += 1
        elif masked[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"unbalanced {opening} at {masked[start:start + 60]!r}")


def _opens(masked: str, start: int) -> int:
    """Index of the bracket opening the one at ``masked[start]`` (scanning back)."""
    closing, opening = masked[start], {"}": "{", ")": "(", "]": "["}[masked[start]]
    depth = 0
    for index in range(start, -1, -1):
        if masked[index] == closing:
            depth += 1
        elif masked[index] == opening:
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError(f"unbalanced {closing} before {masked[:start][-60:]!r}")


def _opens_a_function(masked: str, brace: int) -> bool:
    """Whether the ``{`` at ``brace`` starts a function body, not a plain block.

    This is the distinction indentation was standing in for, and the one that
    matters: a ``return`` inside ``if(...){...}`` is the handler's own answer,
    while a ``return`` inside ``.map(row=>{...})`` shapes a row. The grammar
    tells them apart -- a function body follows ``=>``, or follows a parameter
    list whose ``(`` is not introduced by a block keyword.
    """
    import re

    head = masked[:brace].rstrip()
    if head.endswith("=>"):
        return True
    if not head.endswith(")"):
        return False  # `try {`, `else {`, or an object literal
    word = re.search(r"([A-Za-z_$][\w$]*)\s*$", masked[:_opens(masked, len(head) - 1)])
    return not (word and word.group(1) in _BLOCK_HEADS)


def _entries(literal: str) -> dict[str, str]:
    """Top-level ``name -> value text`` of a masked JS object literal.

    Shorthand (``{agents}``) maps to ``""``. Depth counts brackets and parens
    too, so a comma inside ``Object.assign({},x)`` is not a separator.
    """
    import re

    pairs: dict[str, str] = {}

    def flush(part: str) -> None:
        head, level, cut = [], 0, None
        for index, char in enumerate(part):
            if char in "{[(":
                level += 1
            elif char in "}])":
                level -= 1
            elif char == ":" and level == 0:
                cut = index
                break
            head.append(char)
        name = "".join(head).strip().strip("'\"")
        if name:
            assert re.fullmatch(r"[a-z][a-z0-9_.]*", name), f"parse failed on {part[:60]!r}"
            pairs[name] = part[cut + 1:].strip() if cut is not None else ""

    depth, current = 0, []
    for char in literal[1:-1]:
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        if char == "," and depth == 0:
            flush("".join(current))
            current = []
            continue
        current.append(char)
    flush("".join(current))
    return pairs


def _object_at(masked: str, start: int) -> str:
    """The brace-balanced object literal beginning at ``masked[start] == '{'``."""
    assert masked[start] == "{", masked[start:start + 60]
    return masked[start:_closes(masked, start) + 1]


def _handler_spots(masked: str, method: str) -> list[tuple[int, int, int]]:
    """Every definition of ``method``: (params start, params end, body brace).

    A definition is the name at the start of its line, optionally ``async``,
    whose parameter list is followed by a body. Found by the grammar, so
    spacing cannot hide one -- ``foo ()`` and ``foo()`` are the same handler,
    which is drift (1) below. ``async`` is optional because ``prefillChat`` is
    not async, and the line anchor is what keeps the call site
    ``this.prefillChat({...})`` from reading as a second definition.
    """
    import re

    spots: list[tuple[int, int, int]] = []
    pattern = rf"(?m)^[ \t]*(?:async[ \t]+)?{re.escape(method)}[ \t]*\("
    for match in re.finditer(pattern, masked):
        paren = match.end() - 1
        close = _closes(masked, paren)
        probe = close + 1
        while probe < len(masked) and masked[probe].isspace():
            probe += 1
        if probe < len(masked) and masked[probe] == "{":
            spots.append((paren + 1, close, probe))
    return spots


def _handler(masked: str, method: str) -> tuple[int, int, int]:
    """The one definition of ``method``; two of them is not a contract."""
    spots = _handler_spots(masked, method)
    assert len(spots) == 1, f"{method} is defined {len(spots)} times"
    return spots[0]


def _bridge_actions(masked: str) -> dict[str, str]:
    """The app's frozen allowlist: bridge action -> the handler that answers it."""
    literal = _object_at(masked, masked.index("{", masked.index("ACTIONS:Object.freeze(")))
    return {name: value.strip("'\"") for name, value in _entries(literal).items()}


def _function_depth(masked: str, body: int, at: int) -> int:
    """How many nested function bodies are open between ``body`` and ``at``."""
    functions, stack = 0, []
    for index in range(body + 1, at):
        if masked[index] == "{":
            stack.append(_opens_a_function(masked, index))
            functions += stack[-1]
        elif masked[index] == "}":
            functions -= stack.pop() if stack else 0
    return functions


def _response_keys(masked: str, method: str,
                   seen: tuple[str, ...] = ()) -> tuple[set[str], list[str]]:
    """Keys ``method`` can answer with, and any return this cannot classify.

    Every return in the method's own body is one of the shapes a caller can
    receive, so each has to be accounted for:

    * ``return {...}`` contributes its keys;
    * ``return this.other(...)`` (``await`` optional) is followed into
      ``other``, recursively -- ``conversationDesign`` answers
      ``{state:"ambiguous"}`` on one branch and delegates the NORMAL response
      to ``this.describe(...)``, so reading only the literal described a
      contract missing most of its fields (Codex round 3);
    * anything else is REPORTED, never skipped. One recognised branch must not
      be able to vouch for an unread one.

    A return inside a nested function belongs to that function and is not
    counted. Delegation that comes back around is reported rather than walked.
    """
    import re

    if method in seen:
        return set(), [f"{method} delegates back into {' -> '.join(seen)}"]
    spots = _handler_spots(masked, method)
    if len(spots) != 1:
        return set(), [f"{method} is defined {len(spots)} times, so its shape is ambiguous"]
    body = spots[0][2]
    end = _closes(masked, body)
    keys: set[str] = set()
    problems: list[str] = []
    for match in re.finditer(r"\breturn\b", masked[body:end]):
        at = body + match.end()
        if _function_depth(masked, body, body + match.start()) != 0:
            continue  # a nested function's own answer
        rest = masked[at:end].lstrip()
        where = f"{method} returns {rest[:48].splitlines()[0] if rest else '(nothing)'}"
        if rest.startswith("{"):
            keys |= set(_entries(_object_at(masked, end - len(rest))))
            continue
        delegate = re.match(r"(?:await\s+)?this\.([A-Za-z_$][\w$]*)\s*\(", rest)
        if not delegate:
            problems.append(where)
            continue
        inherited, trouble = _response_keys(masked, delegate.group(1), seen + (method,))
        keys |= inherited
        problems += trouble
    return keys, problems


def _handler_return_keys(masked: str, method: str) -> set[str]:
    """The response shape of ``method``; an unread return is a failure, not a gap."""
    keys, problems = _response_keys(masked, method)
    assert not problems, problems
    assert keys, f"no response shape parsed for {method}"
    return keys


def _preview_reads() -> dict[str, set[str]]:
    """What the preview bridge answers: action -> the keys behind it."""
    masked = _masked(ui_preview._PARENT)
    literal = _object_at(masked, masked.index("{", masked.index("const EMPTY_FOR=")))
    return {action: set(_entries(value)) for action, value in _entries(literal).items()}


def _startup_actions(masked: str) -> set[str]:
    """Allowlisted actions whose handler takes no arguments."""
    return {action for action, method in _bridge_actions(masked).items()
            if _handler_spots(masked, method)
            and not _params(masked, _handler(masked, method)).strip()}


def _params(masked: str, spot: tuple[int, int, int]) -> str:
    """The parameter-list text of a handler ``spot``."""
    return masked[spot[0]:spot[1]]


def _parity_violations(source: str, preview: dict[str, set[str]]) -> list[str]:
    """Every way ``source``'s bridge asks for more than ``preview`` answers.

    Fails closed in every direction: an allowlisted action with no handler is a
    violation rather than a silently skipped row, so is a zero-argument action
    the preview has never heard of, and so is a return whose shape this cannot
    read -- otherwise an unread branch passes as agreement.
    """
    masked = _masked(source)
    problems: list[str] = []
    for action, method in sorted(_bridge_actions(masked).items()):
        if not _handler_spots(masked, method):
            problems.append(f"{action}: allowlisted as {method}, which no handler defines")
            continue
        if _params(masked, _handler(masked, method)).strip():
            continue  # takes arguments: not a call a UI makes before it can draw
        if action not in preview:
            problems.append(f"{action}: answered by the app with no arguments, refused here")
            continue
        keys, unread = _response_keys(masked, method)
        problems += [f"{action}: unread response shape -- {note}" for note in unread]
        if not keys and not unread:
            problems.append(f"{action}: no response shape parsed from {method}")
        missing = keys - preview[action]
        if missing:
            problems.append(f"{action}: the preview omits {sorted(missing)}")
    return problems





















        # monkeypatch restores the original poison state and lock globals.


