"""Drive the real Claude.ai chat UI via Chrome DevTools Protocol.

Setup (run ONCE by the human host, not by user-sim):

    powershell -Command "$exe = Get-ChildItem \\"$env:LOCALAPPDATA\\ms-playwright\\" -Directory | Where-Object Name -match '^chromium-\\d+$' | Sort-Object Name -Descending | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName 'chrome-win64\\chrome.exe' }; Start-Process $exe -ArgumentList '--user-data-dir=C:\\Users\\Jonathan\\.claude-ai-profile','--remote-debugging-port=9222','--no-first-run','--disable-blink-features=AutomationControlled','https://claude.ai/new'"
    # (picks the NEWEST Playwright Chromium build - a hard-coded build number rots each time
    #  Playwright updates: 1208 -> 1228 silently made the launch a no-op on 2026-08-25.
    #  Verify with: python scripts/claude_chat.py status)

Then log into claude.ai in that window, ensure the TinyAssets custom connector is enabled, and keep the window visible. Single-tab rule: user-sim will navigate the existing tab, not open new ones.

Usage (user-sim):

    python scripts/claude_chat.py ask "your prompt here"
    python scripts/claude_chat.py read              # read last assistant message
    python scripts/claude_chat.py new-chat          # navigate to /new
    python scripts/claude_chat.py status            # is the CDP tab reachable?

Every `ask` appends the sent prompt and the assistant response to
`output/claude_chat_trace.md`. The user-sim then writes a short summary entry
into `output/user_sim_session.md` (not the full trace — keeps the shared log lean).

Does NOT bypass the UI. Types into the same chat input a human would use. Reads
the same rendered text a human would see. No MCP calls, no DOM metadata that
isn't visible to a human reader.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Windows cp1252 chokes on arrows/em-dashes in claude responses. Force utf-8.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True,
    )
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True,
    )

_PLAYWRIGHT_MISSING = "ERROR: playwright not installed. Run: pip install playwright"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CDP = "http://127.0.0.1:9222"  # Chrome binds IPv4 only; localhost->::1 is refused
CLAUDE_HOST = "claude.ai"
NEW_CHAT_URL = "https://claude.ai/new"
ROOT = Path(__file__).resolve().parent.parent
TRACE = ROOT / "output" / "claude_chat_trace.md"
FAILURE_DIR = ROOT / "output" / "claude_chat_failures"
NOTEPAD = ROOT / "output" / "user_sim_session.md"

CHROME_BIN = Path(
    os.environ.get(
        "TINYASSETS_CHROME_BIN",
        r"C:\Users\Jonathan\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe",  # 1208 self-exits (code 3) at startup on this host
    )
)
CHROME_PROFILE = Path(
    os.environ.get(
        "TINYASSETS_CHROME_PROFILE",
        str(Path.home() / ".claude-ai-profile"),
    )
)

# Selector candidates. Claude.ai's DOM shifts — try multiple; use the first that works.
INPUT_SELECTORS = [
    'div[contenteditable="true"][enterkeyhint]',
    'div.ProseMirror[contenteditable="true"]',
    'div[contenteditable="true"]',
    'textarea',
]
SEND_BUTTON_SELECTORS = [
    'button[aria-label*="Send" i]',
    'button[aria-label*="send message" i]',
    'button[type="submit"]',
]
STOP_BUTTON_SELECTORS = [
    'button[aria-label*="Stop" i]',
    'button[aria-label*="stop response" i]',
]
# Interactive selection widgets that claude.ai renders in place of the
# free-text input (e.g. "What do you want to work on?" with clickable
# options). While one is active, `INPUT_SELECTORS` return nothing because
# there's no contenteditable. The recovery helper detects the widget so
# it can try to dismiss it before falling back further.
#
# Evidence: output/claude_chat_failures/20260412T184237_input_not_found.html
# shows <div aria-label="What do you want to work on?"
# aria-activedescendant="ask-user-option-question-0-0"> wrapping four
# role="option" rows, with NO contenteditable="true" anywhere in the DOM.
SELECTION_WIDGET_SELECTORS = [
    '[aria-activedescendant*="ask-user-option" i]',
    '[aria-label*="What do you want to work on" i]',
]
ASSISTANT_MSG_SELECTORS = [
    'div[data-testid="assistant-message"]',
    'div.font-claude-message',
    '[data-is-streaming]',  # usually an assistant turn
]

# Thinking blocks. claude.ai renders an extended-thinking transcript as a
# collapsible panel at the top of the assistant message. The collapsed
# summary is always visible ("Thinking about X"); the expanded content is
# the full reasoning stream. Navigator reads the trace to diagnose
# behavior, so we expand any collapsed block BEFORE reading so the full
# stream lands in output/claude_chat_trace.md.
#
# Selectors are best-effort — claude.ai's DOM shifts. Missing an expander
# degrades the trace (thinking omitted) rather than breaking the turn.
THINKING_CONTAINER_SELECTORS = [
    '[data-testid*="thinking" i]',
    '[aria-label*="thinking" i]',
    'div[class*="thinking" i]',
]
THINKING_EXPAND_BUTTON_SELECTORS = [
    '[data-testid*="thinking" i] button[aria-expanded="false"]',
    'button[aria-expanded="false"][aria-label*="thinking" i]',
    'button[aria-expanded="false"][aria-label*="reasoning" i]',
    'button[aria-expanded="false"][aria-label*="expand" i]',
]

# Artifact + rich-content selectors. claude.ai auto-renders things like
# mermaid diagrams, code blocks, and long-form artifacts alongside the
# text reply. inner_text() misses the non-textual parts. We scan for
# common containers inside the last assistant message and record their
# source. Selectors are best-effort and fail soft — a miss just means the
# trace omits that artifact, not an error.
ARTIFACT_CONTAINER_SELECTORS = [
    '[data-testid*="artifact" i]',
    '[aria-label*="artifact" i]',
    'div[class*="artifact" i]',
]
ARTIFACT_TITLE_SELECTORS = [
    '[data-testid*="artifact-title" i]',
    'header',
    '[class*="title" i]',
]
# Code / mermaid source lives in <pre><code class="language-XYZ">. We
# filter by language attribute so we can label mermaid distinctly.
CODE_BLOCK_SELECTOR = 'pre code'


def _launch_chrome() -> None:
    """Launch the dedicated Chrome profile with CDP enabled. Non-blocking."""
    if not CHROME_BIN.exists():
        raise RuntimeError(
            f"Chrome binary not found at {CHROME_BIN}. "
            f"Set TINYASSETS_CHROME_BIN env var to override."
        )
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    args = [
        str(CHROME_BIN),
        f"--user-data-dir={CHROME_PROFILE}",
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
        NEW_CHAT_URL,
    ]
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP so Chrome survives this script.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = 0x00000008 | 0x00000200
    subprocess.Popen(
        args,
        creationflags=creationflags,
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _connect(auto_launch: bool = True, launch_wait_s: int = 20):
    if sync_playwright is None:
        raise RuntimeError(_PLAYWRIGHT_MISSING)
    pw = sync_playwright().start()
    try:
        return pw, pw.chromium.connect_over_cdp(CDP)
    except Exception:
        if not auto_launch:
            pw.stop()
            raise RuntimeError(f"Cannot connect to Chrome CDP at {CDP}.")

    # Launch and retry with a short backoff while Chrome warms up.
    print(f"Chrome CDP not up — launching {CHROME_BIN.name} ...", file=sys.stderr)
    try:
        _launch_chrome()
    except Exception as exc:
        pw.stop()
        raise RuntimeError(f"Auto-launch failed: {exc}") from exc

    deadline = time.monotonic() + launch_wait_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            return pw, pw.chromium.connect_over_cdp(CDP)
        except Exception as exc:
            last_exc = exc
    pw.stop()
    raise RuntimeError(
        f"Chrome launched but CDP still unreachable after {launch_wait_s}s ({last_exc})"
    )


_AUTO_DISMISS_SCRIPT = r"""
(() => {
  if (window.__workflowAutoDismissInstalled) return;
  window.__workflowAutoDismissInstalled = true;
  // Broadened 2026-04-13 (#73) to cover Extensions/Goals permission
  // gates that use "Use this connector?", "Connect", "Approve",
  // "Enable", "Trust", "Grant access" phrasing instead of the
  // legacy "Allow" / "Always allow".
  const ALLOW_RE = /\b(always\s+allow|allow\s+always|allow\s+for\s+this\s+chat|allow\s+this\s+tool|allow\s+access|grant\s+access|use\s+this\s+connector|enable\s+connector|enable\s+(this\s+)?tool|trust\s+(this\s+)?(connector|server|tool)|approve|connect|enable|allow)\b/i;
  const CONFIRM_RE = /\b(confirm|continue|proceed|accept|got\s+it|ok(?:ay)?)\b/i;
  const REJECT_RE = /\b(cancel|deny|reject|not\s+now|maybe\s+later|don.?t\s+allow|disallow|decline)\b/i;
  // Probable permission-dialog container hints. Extensions/Goals
  // gates have been observed rendering as plain <div> overlays with
  // no role=dialog, so we also accept aria-modal="true" and fixed
  // overlays with high z-index.
  const CONTAINER_SELECTORS = [
    '[role="dialog"]',
    '[role="alertdialog"]',
    '[aria-modal="true"]',
    '[class*="dialog" i]',
    '[class*="modal" i]',
    '[class*="popover" i]',
    '[class*="permission" i]',
    '[class*="connector" i]',
    '[data-state="open"]',
  ].join(',');
  const isInModalishOverlay = (el) => {
    // Walk up the tree; accept the first ancestor that looks like a
    // modal container OR that is position:fixed with a high z-index.
    let cur = el;
    while (cur && cur !== document.body) {
      if (cur.matches && cur.matches(CONTAINER_SELECTORS)) return true;
      const style = window.getComputedStyle ? window.getComputedStyle(cur) : null;
      if (style && style.position === 'fixed') {
        const z = parseInt(style.zIndex || '0', 10);
        if (z >= 10) return true;
      }
      cur = cur.parentElement;
    }
    return false;
  };
  // Before clicking a confirm button, walk up to the dialog container and
  // look for an "always allow" / "don't ask again" checkbox or toggle.
  // Claude.ai's per-tool permission dialogs may render this as a checkbox
  // alongside the "Allow" button — checking it makes the approval persistent
  // so the dialog doesn't re-fire on the next call to the same tool.
  const ALWAYS_ALLOW_TOGGLE_RE = /\b(always\s+allow|don.?t\s+ask|remember|for\s+this\s+chat|for\s+all|every\s+time|persist|don.?t\s+show\s+again)\b/i;
  const checkAlwaysAllowToggle = (btn) => {
    // Walk up from the button to find the nearest dialog-like container.
    let container = btn.parentElement;
    while (container && container !== document.body) {
      if (container.matches && container.matches(CONTAINER_SELECTORS)) break;
      const s = window.getComputedStyle ? window.getComputedStyle(container) : null;
      if (s && s.position === 'fixed' && parseInt(s.zIndex || '0', 10) >= 10) break;
      container = container.parentElement;
    }
    if (!container || container === document.body) return;
    // Find checkboxes, radio buttons, or toggle elements in the container.
    const toggleCandidates = container.querySelectorAll(
      'input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="switch"]'
    );
    toggleCandidates.forEach(t => {
      // Get associated label text from various sources.
      const labelText = (
        (t.labels && t.labels[0] ? t.labels[0].textContent : '') ||
        t.getAttribute('aria-label') ||
        (t.closest('label') ? t.closest('label').textContent : '') ||
        (t.id ? (document.querySelector('[for="' + t.id + '"]') || {textContent: ''}).textContent : '')
      );
      if (!ALWAYS_ALLOW_TOGGLE_RE.test(labelText)) return;
      const isChecked = t.checked || t.getAttribute('aria-checked') === 'true';
      if (!isChecked) {
        try { t.click(); } catch(e) {}
      }
    });
  };
  const tryClick = (btn) => {
    const txt = (btn.innerText || btn.textContent || '').trim();
    if (!txt) return false;
    // Skip reject-style buttons even when the container matches, so we
    // never silently cancel a permission the user actually wanted.
    if (REJECT_RE.test(txt) && !(ALLOW_RE.test(txt) || CONFIRM_RE.test(txt))) return false;
    if (!(ALLOW_RE.test(txt) || CONFIRM_RE.test(txt))) return false;
    if (!isInModalishOverlay(btn)) return false;
    // Before confirming, try to check any "always allow" toggle so the
    // permission persists and the dialog doesn't re-fire on the next call.
    checkAlwaysAllowToggle(btn);
    try {
      btn.click();
      window.__workflowAutoDismissCount = (window.__workflowAutoDismissCount || 0) + 1;
      window.__workflowAutoDismissLast = {text: txt, at: Date.now()};
      // Append to a drain queue so the Python side can mirror each
      // successful auto-dismiss into output/user_sim_session.md.
      // Python reads + clears this array; JS only appends.
      if (!Array.isArray(window.__workflowAutoDismissLog)) {
        window.__workflowAutoDismissLog = [];
      }
      window.__workflowAutoDismissLog.push({text: txt, at: Date.now()});
      return true;
    } catch (e) { return false; }
  };
  const scan = () => {
    // Cover both <button> and role="button" elements — some gates use
    // custom clickable divs with a role attribute instead of real buttons.
    document.querySelectorAll('button, [role="button"]').forEach(tryClick);
  };
  const obs = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const n of m.addedNodes || []) {
        if (!(n instanceof Element)) continue;
        if (n.matches && (n.matches('button') || n.matches('[role="button"]'))) {
          tryClick(n);
          continue;
        }
        if (n.querySelectorAll) {
          n.querySelectorAll('button, [role="button"]').forEach(tryClick);
        }
      }
    }
  });
  obs.observe(document.documentElement, {childList: true, subtree: true});
  // Also sweep once at install time in case a dialog is already open.
  scan();
  // Periodic safety sweep at very low frequency (5s) in case the observer misses deeply-nested mounts.
  setInterval(scan, 5000);
})();
"""


def _log_dialog_to_notepad(
    text: str, outcome: str, tool_name: str = "",
) -> None:
    """Append one permission-dialog event to ``output/user_sim_session.md``.

    One line per event so the host scanning the notepad sees every
    gate Claude.ai surfaced — including auto-dismissed ones. Line
    format:

      ## [YYYY-MM-DD HH:MM] SYSTEM DIALOG <tool> — detected: "<first 80 chars>" auto-dismiss: <ok|failed>

    Best-effort: never raises. Atomic append via ``os.O_APPEND`` so
    concurrent user-sim + dismiss-poll writers don't interleave
    mid-line.
    """
    try:
        NOTEPAD.parent.mkdir(parents=True, exist_ok=True)
        snippet = (text or "").strip().replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:80]
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        tool = tool_name or "-"
        line = (
            f"## [{stamp}] SYSTEM DIALOG {tool} — "
            f'detected: "{snippet}" auto-dismiss: {outcome}\n'
        )
        fd = os.open(
            str(NOTEPAD),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        # Notepad logging is observability, never block the primary flow.
        pass


def _drain_auto_dismiss_log(page) -> int:
    """Read + clear the browser-side auto-dismiss drain queue.

    The MutationObserver in ``_AUTO_DISMISS_SCRIPT`` pushes
    ``{text, at}`` onto ``window.__workflowAutoDismissLog`` on every
    successful click. Python drains the queue here and logs each
    entry via ``_log_dialog_to_notepad``. Returns the number of
    entries drained. Safe no-op on error (never breaks the caller).
    """
    try:
        drained = page.evaluate(
            "(() => { "
            "const a = window.__workflowAutoDismissLog || []; "
            "window.__workflowAutoDismissLog = []; "
            "return a; "
            "})()"
        ) or []
    except Exception:
        return 0
    for entry in drained:
        text = str((entry or {}).get("text", "") or "")
        _log_dialog_to_notepad(text, outcome="ok", tool_name="auto-dismiss")
    return len(drained)


def _install_auto_dismiss(page) -> None:
    """Install a persistent MutationObserver that auto-clicks allow/confirm
    buttons the moment they appear. Idempotent — the script guards its own
    re-install with window.__workflowAutoDismissInstalled.
    """
    try:
        page.evaluate(_AUTO_DISMISS_SCRIPT)
    except Exception:
        # Never let auto-dismiss install break the primary flow.
        pass


def _find_claude_page(browser):
    for ctx in browser.contexts:
        for p in ctx.pages:
            if CLAUDE_HOST in p.url:
                return p
    return None


def _enumerate_pages(browser):
    pages = []
    for ctx in browser.contexts:
        for p in ctx.pages:
            pages.append(p)
    return pages


def _enforce_single_tab(browser) -> int:
    """Host-set forever rule: exactly one Chrome tab at every moment.

    If >1 tab exists, pick the correct mission tab (claude.ai page with a
    /chat/ URL > any claude.ai page > first page) and close every other.
    Idempotent: if already 1 tab, returns 0 without side effects. Returns
    the number of tabs closed so callers can log the heal.

    Emits a single stderr line per heal so user-sim can forward it to
    the session log. Never raises — a failure to close is logged but
    does not block the primary flow.
    """
    pages = _enumerate_pages(browser)
    if len(pages) <= 1:
        return 0

    def _rank(p) -> int:
        url = (getattr(p, "url", "") or "")
        if CLAUDE_HOST in url and "/chat/" in url:
            return 0
        if CLAUDE_HOST in url:
            return 1
        return 2

    pages_sorted = sorted(pages, key=_rank)
    keeper = pages_sorted[0]
    closed_urls = []
    for p in pages_sorted[1:]:
        url = (getattr(p, "url", "") or "(unknown)")
        try:
            p.close()
            closed_urls.append(url)
        except Exception as exc:
            print(
                f"TAB HYGIENE WARN: failed to close tab {url}: {exc}",
                file=sys.stderr,
            )
    if closed_urls:
        keeper_url = (getattr(keeper, "url", "") or "(unknown)")
        print(
            f"TAB HYGIENE: closed {len(closed_urls)} extra tab(s); "
            f"kept {keeper_url}; closed {closed_urls}",
            file=sys.stderr,
        )
    return len(closed_urls)


def _ensure_claude_page(browser):
    """Single-tab rule: navigate the first available tab to claude.ai if no claude tab exists.

    Also installs the persistent auto-dismiss MutationObserver so allow/confirm
    permission dialogs are clicked the moment they appear — not just on the
    1.5s poll cadence.
    """
    p = _find_claude_page(browser)
    if p is None:
        for ctx in browser.contexts:
            if ctx.pages:
                p = ctx.pages[0]
                p.goto(NEW_CHAT_URL, timeout=30000)
                time.sleep(2)
                break
    if p is None:
        raise RuntimeError("No pages in the CDP browser context.")
    _enforce_single_tab(browser)
    _install_auto_dismiss(p)
    return p


DIALOG_DISMISS_SELECTORS = [
    # Legacy permission dialogs (role=dialog / alertdialog containers).
    '[role="dialog"] button:has-text("Always allow")',
    '[role="dialog"] button:has-text("Allow always")',
    '[role="dialog"] button:has-text("Allow for this chat")',
    '[role="dialog"] button:has-text("Allow")',
    '[role="dialog"] button:has-text("Confirm")',
    '[role="dialog"] button:has-text("Continue")',
    '[role="alertdialog"] button:has-text("Allow")',
    # Extensions/Goals permission gates (#73) — observed to use
    # different DOM shapes and different button copy. We broaden the
    # container match to aria-modal containers and include the newer
    # action words. The observer (above) already handles most of these
    # at mutation time; these selectors are the poll-cadence fallback.
    '[aria-modal="true"] button:has-text("Use this connector")',
    '[aria-modal="true"] button:has-text("Connect")',
    '[aria-modal="true"] button:has-text("Approve")',
    '[aria-modal="true"] button:has-text("Enable")',
    '[aria-modal="true"] button:has-text("Trust")',
    '[aria-modal="true"] button:has-text("Grant access")',
    '[aria-modal="true"] button:has-text("Allow")',
    # Same button copy, no aria-modal — the riskiest fallback, so we
    # still require "connector" / "permission" hint in the ancestor.
    '[class*="connector" i] button:has-text("Use this connector")',
    '[class*="permission" i] button:has-text("Approve")',
    '[class*="permission" i] button:has-text("Allow")',
]

_dismiss_cooldown_until = 0.0


_ALWAYS_ALLOW_TOGGLE_SELECTORS = [
    # Checkboxes and toggles labeled "always allow" / "don't ask again"
    # that may live inside a per-tool permission dialog alongside the
    # main "Allow" / "Approve" button.  Checking one of these before
    # clicking the confirm button makes the approval persistent so the
    # dialog doesn't re-fire on the next call to the same tool.
    '[role="dialog"] input[type="checkbox"]:near(:text("Always allow"))',
    '[role="dialog"] [role="checkbox"]:near(:text("Always allow"))',
    '[role="dialog"] [role="switch"]:near(:text("Always allow"))',
    '[aria-modal="true"] input[type="checkbox"]:near(:text("Always allow"))',
    '[aria-modal="true"] [role="checkbox"]:near(:text("Always allow"))',
    '[aria-modal="true"] [role="switch"]:near(:text("Always allow"))',
    # Fallback: any unchecked toggle in an open dialog — checked last so
    # we only use it if the label-specific selectors all miss.
    '[role="dialog"] input[type="checkbox"]',
    '[role="dialog"] [role="checkbox"]',
    '[aria-modal="true"] input[type="checkbox"]',
]

_ALWAYS_ALLOW_LABEL_RE = re.compile(
    r'\b(always\s+allow|don.?t\s+ask|remember|for\s+this\s+chat|'
    r'for\s+all|every\s+time|persist|don.?t\s+show\s+again)\b',
    re.IGNORECASE,
)


def _try_check_always_allow(page) -> bool:
    """Attempt to check an 'always allow' toggle in an open dialog.

    Returns True if a toggle was checked. Safe no-op when no matching
    toggle is visible — never raises.
    """
    # First try selectors with explicit text-proximity (more precise).
    for sel in _ALWAYS_ALLOW_TOGGLE_SELECTORS[:6]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                # Check if it's already active before clicking.
                checked = loc.first.evaluate(
                    "el => el.checked || el.getAttribute('aria-checked') === 'true'"
                )
                if not checked:
                    loc.first.click(force=True)
                    time.sleep(0.1)
                    return True
        except Exception:
            continue
    # Fallback: any unchecked toggle in a dialog — only use if text
    # label matches the always-allow pattern.
    for sel in _ALWAYS_ALLOW_TOGGLE_SELECTORS[6:]:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(count):
                toggle = loc.nth(i)
                if not toggle.is_visible():
                    continue
                # Verify label text matches before clicking.
                label = toggle.evaluate(
                    """el => {
                        const lbl = el.labels && el.labels[0];
                        return (lbl ? lbl.textContent : '') ||
                               el.getAttribute('aria-label') || '';
                    }"""
                )
                if _ALWAYS_ALLOW_LABEL_RE.search(label or ""):
                    checked = toggle.evaluate(
                        "el => el.checked || el.getAttribute('aria-checked') === 'true'"
                    )
                    if not checked:
                        toggle.click(force=True)
                        time.sleep(0.1)
                        return True
        except Exception:
            continue
    return False


_INLINE_ALWAYS_ALLOW_PROBE = r"""
(() => {
  // Claude.ai's newer permission UX renders inline in the chat
  // transcript (not a role=dialog modal).
  //
  // The gate must be scoped to the CARD THAT OWNS THE BUTTON. Matching
  // the brand against the whole page is unsound: a resolved TinyAssets
  // card stays in the transcript DOM forever, so its stale text would
  // vouch for a *different* connector's live card (Google Drive, Gmail)
  // and we would click that card's "Always allow" — silently granting
  // the model standing access to the host's account. Walk up from the
  // button instead and require the prompt AND the brand inside its own
  // card.
  //
  // The connector name is matched EXACTLY, never as a substring. The
  // user-sim talks about TinyAssets constantly, so a Gmail card with
  // "TinyAssets" in its displayed arguments — or a connector named
  // "EvilTinyAssetsBackup" — would sail through a substring test.
  //
  // APPROVED_NAMES keeps the legacy "Universe Server" entry on purpose:
  // this text is the user's install-time connector name in Claude.ai,
  // which the repo-side rename to TinyAssets cannot change
  // retroactively, and this harness drives an existing account. Because
  // the match is exact, the entry can only ever match a connector named
  // precisely "Universe Server". RETIREMENT: drop it once the host
  // confirms no Claude.ai account used for user-sim still has a
  // connector installed under the old name.
  //
  // Real card shape, from a browser capture (2026-04-14,
  // output/claude_chat_failures/20260414T231328_response_timeout.txt):
  //
  //     Claude wants to use
  //     Universe Operations      <- TOOL name
  //     from
  //     Universe Server          <- CONNECTOR name
  //     Always allow
  //
  // The connector is the line after a standalone "from" line. Reading
  // whatever follows "Claude wants to use" instead would capture the
  // TOOL name — which the connector itself controls, so a hostile
  // connector could publish a tool literally named "x from TinyAssets"
  // and vouch for its own card.
  const ASK = /Claude wants to use/i;
  const ASK_ALL = /Claude wants to use/gi;
  const FROM_CONNECTOR = /(?:^|\n)[ \t]*from[ \t]*\n[ \t]*([^\n]+)/gi;
  // Older/simpler single-line shape, used only when no "from" line
  // exists. Deliberately same-line ([ \t], not \s) so it cannot reach
  // down to a tool name on the following line.
  const ASK_INLINE = /Claude wants to use[ \t]+([^\n]+)/i;
  const APPROVED_NAMES = [
    'tinyassets',
    'tinyassets server',
    'universe server',
  ];
  const body = document.body;
  if (!body || !ASK.test(body.innerText || '')) {
    return {found: false, reason: 'no permission card text'};
  }
  const candidates = Array.from(document.querySelectorAll('button')).filter(
    b => /^always allow\b/i.test((b.innerText || '').trim()) &&
         b.offsetParent !== null
  );
  if (!candidates.length) {
    return {found: false, reason: 'no visible Always-allow button'};
  }
  let reason = 'no visible Always-allow button';
  let rejectedName = '';
  for (const btn of candidates) {
    // The card is the smallest ancestor carrying the permission prompt.
    let card = btn.parentElement;
    while (card && card !== body && card !== document.documentElement) {
      if (ASK.test(card.innerText || '')) break;
      card = card.parentElement;
    }
    if (!card || card === body || card === document.documentElement) {
      reason = 'Always-allow button not inside a permission card';
      continue;
    }
    const cardText = card.innerText || '';
    // A scope spanning two prompts cannot attribute the brand to this
    // button. Fail closed rather than guess which card it belongs to.
    if ((cardText.match(ASK_ALL) || []).length !== 1) {
      reason = 'ambiguous permission card (multiple prompts in scope)';
      continue;
    }
    const froms = Array.from(cardText.matchAll(FROM_CONNECTOR));
    let raw;
    if (froms.length === 1) {
      raw = froms[0][1];
    } else if (froms.length === 0) {
      const inline = cardText.match(ASK_INLINE);
      raw = inline ? inline[1] : '';
    } else {
      reason = 'ambiguous permission card (multiple prompts in scope)';
      continue;
    }
    // Whitespace-normalise only (this also folds NBSP and other Unicode
    // spaces into ' '), then compare VERBATIM. Stripping punctuation
    // would make a connector named "TinyAssets!" indistinguishable from
    // ours.
    const connector = raw.replace(/\s+/g, ' ').trim().toLowerCase();
    if (APPROVED_NAMES.indexOf(connector) === -1) {
      reason = 'not TinyAssets Server card';
      rejectedName = connector;
      continue;
    }
    const label = (btn.innerText || '').trim();
    try {
      btn.scrollIntoView({block: 'center'});
      btn.click();
      return {found: true, clicked: true, label: label};
    } catch (e) {
      return {found: true, clicked: false, label: label, error: String(e)};
    }
  }
  return {found: false, reason: reason, name: rejectedName};
})();
"""


# Probe rejections worth telling the host about exactly once. These mean
# "a visible Always-allow button existed and we deliberately refused it" —
# either someone else's connector was on screen, or Claude.ai changed the
# card DOM and our scoping no longer resolves. Both are invisible
# otherwise, and a silently fail-closed gate is undiagnosable. The two
# ordinary misses ("no permission card text", "no visible Always-allow
# button") fire on every poll and are deliberately not listed.
_INLINE_DIAGNOSTIC_REASONS = frozenset({
    "not TinyAssets Server card",
    "ambiguous permission card (multiple prompts in scope)",
    "Always-allow button not inside a permission card",
})
_inline_reject_reasons_logged: set[str] = set()


def _dismiss_inline_permission_card(page) -> int:
    """Click Claude.ai's inline "Always allow" permission card.

    The newer UX is NOT a modal (no role=dialog / aria-modal). It's an
    inline card in the chat transcript — which the dialog-scoped
    selectors miss.

    Only clicks a button whose *own* card names TinyAssets (or the
    legacy "Universe Server" connector name), so another connector's
    permission card is never granted on our behalf. Returns 1 on click,
    0 on no-op / miss.
    """
    try:
        result = page.evaluate(_INLINE_ALWAYS_ALLOW_PROBE) or {}
    except Exception:
        return 0
    if not result.get("found"):
        reason = str(result.get("reason", "") or "")
        # Include the connector name we saw: if Claude.ai reshapes the
        # card and our scoping stops resolving, this line is what makes
        # it tunable instead of a silent stall.
        name = str(result.get("name", "") or "")[:60]
        key = f"{reason}|{name}"
        if (
            reason in _INLINE_DIAGNOSTIC_REASONS
            and key not in _inline_reject_reasons_logged
        ):
            _inline_reject_reasons_logged.add(key)
            detail = f"{reason}: {name}" if name else reason
            _log_dialog_to_notepad(
                f"refused Always-allow ({detail})", outcome="skipped",
                tool_name="_dismiss_inline_permission_card",
            )
        return 0
    label = str(result.get("label", "") or "Always allow")
    if result.get("clicked"):
        _log_dialog_to_notepad(
            label, outcome="ok",
            tool_name="_dismiss_inline_permission_card",
        )
        return 1
    # Detected but click failed — notepad log so host can manually click.
    _log_dialog_to_notepad(
        label, outcome="failed",
        tool_name="_dismiss_inline_permission_card",
    )
    return 0


def _dismiss_dialogs(page) -> int:
    """Click any permission/allow dialog Claude.ai has put up.

    Before clicking the main confirm button, attempts to check any
    "always allow" / "don't ask again" toggle so the permission persists
    across future calls to the same tool (avoids re-prompt loops that
    stall user-sim missions).

    Scoped to dialog-like containers (role=dialog, aria-modal=true, or
    class-hinted overlays) — never clicks arbitrary buttons. Returns
    how many dialogs were dismissed. Cheap no-op when no dialog
    exists, so safe to call before every ask and before every response
    wait.
    """
    global _dismiss_cooldown_until
    if time.monotonic() < _dismiss_cooldown_until:
        return 0
    # Drain any MutationObserver auto-dismiss events the browser
    # captured between polls — the host sees them in the notepad
    # even though we didn't click them ourselves.
    _drain_auto_dismiss_log(page)
    # Inline permission card (Claude.ai's newer non-modal UX) first —
    # the dialog-scoped selectors below can't see it. Strictly gated
    # to TinyAssets-Server cards so we never click arbitrary buttons.
    inline_clicked = _dismiss_inline_permission_card(page)
    if inline_clicked:
        _dismiss_cooldown_until = time.monotonic() + 3.0
        return inline_clicked
    # Before clicking the confirm button, try to toggle "always allow"
    # so this approval persists for the tool across future calls.
    _try_check_always_allow(page)
    clicked = 0
    for sel in DIALOG_DISMISS_SELECTORS:
        try:
            loc = page.locator(sel)
            n = loc.count()
        except Exception:
            continue
        for i in range(n):
            try:
                btn = loc.nth(i)
                if not btn.is_visible():
                    continue
                btn_text = ""
                try:
                    btn_text = (btn.inner_text() or "").strip()
                except Exception:
                    pass
                try:
                    btn.click(force=True)
                except Exception:
                    _log_dialog_to_notepad(
                        btn_text or sel, outcome="failed",
                        tool_name="_dismiss_dialogs",
                    )
                    continue
                clicked += 1
                _log_dialog_to_notepad(
                    btn_text or sel, outcome="ok",
                    tool_name="_dismiss_dialogs",
                )
                _dismiss_cooldown_until = time.monotonic() + 3.0
                time.sleep(0.3)
                break  # one per pass; re-evaluate on next call if more
            except Exception:
                continue
        if clicked:
            break
    return clicked


_SUSPECTED_DIALOG_PROBE = r"""
(() => {
  // Scan for buttons whose text smells like a permission/connector
  // gate. Return the button text + a compact outer-HTML snippet of
  // the nearest plausible container so we can iterate on selectors
  // without a full page dump.
  const PROBE_RE = /\b(use\s+this\s+connector|connect\b|approve\b|enable\b|trust\b|grant\s+access|allow\b|always\s+allow)\b/i;
  const hits = [];
  const nodes = document.querySelectorAll('button, [role="button"]');
  for (const btn of nodes) {
    const txt = (btn.innerText || btn.textContent || '').trim();
    if (!txt || txt.length > 60) continue;
    if (!PROBE_RE.test(txt)) continue;
    let visible = true;
    try {
      const rect = btn.getBoundingClientRect();
      visible = rect.width > 0 && rect.height > 0;
    } catch (e) { /* ignore */ }
    if (!visible) continue;
    // Walk up a few ancestors to get a container snippet.
    let anc = btn;
    for (let i = 0; i < 4 && anc.parentElement; i++) anc = anc.parentElement;
    const snippet = (anc.outerHTML || '').slice(0, 1200);
    hits.push({
      text: txt,
      tag: btn.tagName.toLowerCase(),
      role: btn.getAttribute('role') || '',
      aria_modal_ancestor: !!btn.closest('[aria-modal="true"]'),
      role_dialog_ancestor: !!btn.closest('[role="dialog"], [role="alertdialog"]'),
      container_snippet: snippet,
    });
  }
  return hits;
})();
"""


_suspected_dump_cooldown_until = 0.0


def _dump_suspected_dialog(page, reason: str = "suspected_dialog") -> str:
    """Dump DOM + screenshot when we suspect a permission gate exists
    but our selectors didn't fire.

    The auto-dismiss observer and `_dismiss_dialogs` both rely on
    substring matches against live Claude.ai copy. When they drift
    (as they did between the legacy MCP allow dialog and the newer
    Extensions/Goals gates), we want real samples the next session
    can use to widen the selectors. This helper probes the DOM for
    permission-ish buttons and, if any are visible, falls through to
    `_capture_failure_dump` to write a full artifact triple. Returns
    the dump basename on success, empty string if nothing suspicious.
    """
    global _suspected_dump_cooldown_until
    if time.monotonic() < _suspected_dump_cooldown_until:
        return ""
    try:
        hits = page.evaluate(_SUSPECTED_DIALOG_PROBE) or []
    except Exception:
        return ""
    if not hits:
        return ""
    _suspected_dump_cooldown_until = time.monotonic() + 30.0
    # Host-facing note: our selectors didn't match this gate, so log
    # the first detected button into the notepad as "failed" so the
    # host can manually click it and so we can widen selectors later.
    first_text = str((hits[0] or {}).get("text", "") or "")
    _log_dialog_to_notepad(
        first_text, outcome="failed",
        tool_name="_dump_suspected_dialog",
    )
    note_lines = [
        "Suspected permission-gate buttons (not auto-dismissed):",
        "",
    ]
    for h in hits[:8]:
        note_lines.append(
            f"- text={h.get('text')!r} tag={h.get('tag')} "
            f"role={h.get('role') or '-'} "
            f"aria_modal_anc={h.get('aria_modal_ancestor')} "
            f"role_dialog_anc={h.get('role_dialog_ancestor')}"
        )
        snippet = h.get("container_snippet") or ""
        if snippet:
            note_lines.append(
                "  container: "
                + snippet.replace("\n", " ")[:400]
            )
    return _capture_failure_dump(
        page, reason, note="\n".join(note_lines),
    )


def _first_visible(page, selectors):
    for sel in selectors:
        loc = page.locator(sel)
        count = loc.count()
        for i in range(count):
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:
                continue
    return None


def _locator_text(locator) -> str:
    """Best-effort visible/input text for a Playwright locator."""
    try:
        evaluate = getattr(locator, "evaluate", None)
        if callable(evaluate):
            text = evaluate(
                "(el) => { "
                "if ('value' in el) return el.value || ''; "
                "return el.innerText || el.textContent || ''; "
                "}"
            )
            if text is not None:
                return str(text)
    except Exception:
        pass
    for method_name in ("input_value", "inner_text", "text_content"):
        try:
            method = getattr(locator, method_name, None)
            if callable(method):
                text = method()
                if text is not None:
                    return str(text)
        except Exception:
            continue
    return ""


def _locator_is_disabled(locator) -> bool:
    """Return True when a send button/input is visibly disabled."""
    try:
        get_attribute = getattr(locator, "get_attribute", None)
        if callable(get_attribute):
            disabled = get_attribute("disabled")
            aria_disabled = (get_attribute("aria-disabled") or "").lower()
            if disabled is not None or aria_disabled == "true":
                return True
    except Exception:
        pass
    try:
        evaluate = getattr(locator, "evaluate", None)
        if callable(evaluate):
            return bool(evaluate(
                "(el) => Boolean("
                "el.disabled || "
                "el.getAttribute('disabled') !== null || "
                "el.getAttribute('aria-disabled') === 'true' || "
                "el.closest('[aria-disabled=\"true\"]')"
                ")"
            ))
    except Exception:
        pass
    return False


def _normalize_chat_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _composer_contains_message(page, message: str) -> tuple[bool, str]:
    """Detect the failure mode where `ask` typed but did not submit."""
    inp = _first_usable_input(page)
    if inp is None:
        return False, ""
    composer_text = _locator_text(inp)
    normalized_message = _normalize_chat_text(message)
    normalized_composer = _normalize_chat_text(composer_text)
    if not normalized_message:
        return False, composer_text
    return normalized_message in normalized_composer, composer_text


def _clear_composer(page, inp) -> None:
    """Best-effort clear of the chat composer (keystroke + DOM fallback)."""
    try:
        inp.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
    except Exception:
        pass
    try:
        leftover = (inp.inner_text() or "").strip()
    except Exception:
        leftover = ""
    if leftover:
        try:
            inp.evaluate(
                "(el) => { "
                "if ('value' in el) { el.value = ''; } "
                "else { el.textContent = ''; } "
                "el.dispatchEvent(new Event('input', {bubbles: true})); "
                "}"
            )
        except Exception:
            pass


def _type_message_verified(page, inp, message, *, max_attempts=3):
    """Type ``message`` into the composer and verify the FULL text landed
    BEFORE the caller submits.

    Why this exists (host-reported 2026-05-28, recurring class):
    ``page.keyboard.type`` over a long multi-line message can drop
    keystrokes on a slow render, interleave with leftover composer text,
    or — worst — let a transient focus-steal / auto-dismiss click submit a
    PARTIAL message mid-type. That produces a truncated send. The old
    post-send guard only asked "is the WHOLE message still in the
    composer?", so a truncated composer (empty-ish) read as success and the
    half-message went out silently.

    Defenses:
      * Newlines are entered with Shift+Enter, never a bare Enter, so an
        embedded newline can never trigger claude.ai's Enter-to-send and
        submit the message early.
      * After typing, the composer text is read back and compared to the
        intended message (whitespace-normalized). A mismatch means a
        dropped / early-submitted / interleaved type — clear and retry.

    Returns ``(ok, composer_text)``. ``ok=False`` after ``max_attempts``
    means the full message never landed; the caller MUST NOT send — it
    should dump diagnostics and fail loudly rather than emit a fragment.
    """
    normalized_target = _normalize_chat_text(message)
    composer_text = ""
    for _attempt in range(max_attempts):
        _clear_composer(page, inp)
        # Primary path: atomic insert. `keyboard.insert_text` commits the
        # whole string as ONE input event (the same path IME/paste uses), so
        # there are no per-keystroke events to drop and no bare Enter to
        # submit the message early. This is what fixes the host-reported
        # truncation: char-by-char `keyboard.type` over a long line dropped
        # keystrokes (observed: a 430-char line landing as ~120 chars).
        inserted = False
        try:
            inp.click()
            page.keyboard.insert_text(message)
            inserted = True
        except Exception:
            inserted = False
        if not inserted:
            # Fallback: per-line typing, Shift+Enter for newlines so an
            # embedded newline still can't submit mid-message.
            for idx, line in enumerate(message.split("\n")):
                if idx > 0:
                    try:
                        page.keyboard.press("Shift+Enter")
                    except Exception:
                        pass
                if line:
                    page.keyboard.type(line, delay=12)
        time.sleep(0.3)
        composer_text = _locator_text(inp)
        normalized_composer = _normalize_chat_text(composer_text)
        if not normalized_target:
            return True, composer_text
        if normalized_target in normalized_composer:
            return True, composer_text
        # Mismatch: the composer doesn't hold the full message. Likely a
        # partial/early submit or detached input — re-scan before retry.
        rescanned = _first_usable_input(page)
        if rescanned is not None:
            inp = rescanned
    return False, composer_text


def _visible_submit_block_note(page) -> str:
    """Extract visible rate-limit/send-block hints for diagnostic notes."""
    try:
        visible = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""
    needles = (
        "out of extra usage",
        "limit resets",
        "buy more",
        "message limit",
        "usage limit",
        "rate limit",
    )
    lines = [
        line.strip()
        for line in str(visible).splitlines()
        if any(needle in line.lower() for needle in needles)
    ]
    return " | ".join(lines[:4])


def _first_usable_input(page):
    """Like `_first_visible(INPUT_SELECTORS)` but skips locked inputs.

    Claude.ai sometimes leaves a contenteditable/textarea in the DOM while
    the previous response is still streaming or while a gate is active —
    the node is visible but aria-disabled or contenteditable="false". Typing
    into such an input silently drops the keystrokes. Detecting this here
    lets the caller invoke the recovery ladder instead.
    """
    for sel in INPUT_SELECTORS:
        loc = page.locator(sel)
        count = loc.count()
        for i in range(count):
            cand = loc.nth(i)
            try:
                if not cand.is_visible():
                    continue
                get_attribute = getattr(cand, "get_attribute", None)
                if callable(get_attribute):
                    aria_disabled = (get_attribute("aria-disabled") or "").lower()
                    if aria_disabled == "true":
                        continue
                    contenteditable = (get_attribute("contenteditable") or "").lower()
                    if contenteditable == "false":
                        continue
                evaluate = getattr(cand, "evaluate", None)
                if callable(evaluate):
                    if evaluate("el => el.tagName.toLowerCase() === 'textarea' && el.disabled"):
                        continue
                return cand
            except Exception:
                continue
    return None


def _selection_widget_visible(page) -> bool:
    """Is the `ask-user-option` selection widget currently blocking input?

    Claude.ai renders this when the bot ends a turn with a multiple-choice
    question. While it's active, there is NO contenteditable input in the
    DOM — the user is expected to pick an option. Detecting it lets the
    recovery path tell this apart from "input just hasn't mounted yet".
    """
    for sel in SELECTION_WIDGET_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _try_recover_input(page, *, on_step=None):
    """Escalation ladder to restore a usable chat input.

    Returns the input locator on success, or None if every recovery
    strategy failed. Intended for use from `cmd_ask` when the normal
    input lookup comes up empty. Strategies, in order of blast radius:

      1. Click the chat body area to force focus back to the input.
      2. Press Escape to dismiss any overlay / modal — including the
         `ask-user-option` selection widget. Escape does NOT post a
         choice to the conversation; the widget's Skip button does.
         Escape twice if the widget is still visible after the first
         press. We deliberately never click Skip (persona authenticity
         rule; see the option-select recovery test).
      3. Scroll the message list to the bottom (puts the input back in
         view; sometimes it's virtualized off-screen).
      4. Tab into the input role by keyboard.
      5. Last resort: reload the current chat URL. This preserves chat
         history (same chat id) but re-mounts the full UI. Do NOT
         navigate to /new — that loses the conversation. If the widget
         persists after reload, return None — the caller's failure dump
         emits selection_widget=visible and user-sim's next typed `ask`
         will carry the persona's real answer and re-mount the input.

    Each step re-scans INPUT_SELECTORS; the first one that yields a
    visible input wins. `on_step` is an optional callback invoked with
    the step name for observability — user-sim can route it to stderr.
    """
    def _log(name: str) -> None:
        if on_step is not None:
            try:
                on_step(name)
            except Exception:
                pass

    def _rescan():
        try:
            return _first_usable_input(page)
        except Exception:
            return None

    # Fast path: maybe the input came back on its own between the
    # original check and now. One re-scan is cheap.
    inp = _rescan()
    if inp is not None:
        _log("initial_rescan")
        return inp

    # 1. Click the main chat content to force focus.
    try:
        body_loc = page.locator("main, [role='main']")
        body = body_loc.first
        if body and body.count() > 0 and body.is_visible():
            body.click(timeout=1000)
            time.sleep(0.3)
            inp = _rescan()
            if inp is not None:
                _log("main_click")
                return inp
    except Exception:
        pass

    # 2. Escape to dismiss the selection widget WITHOUT submitting a choice.
    # Earlier implementations clicked the widget's Skip button here; that
    # is NOT a benign dismiss — the model sees Skip as "user picked 'no
    # preference'" and proceeds as though the persona had no view. Persona
    # authenticity requires that user-sim's next typed message be the
    # persona's real answer. Escape closes the widget without posting,
    # so the next `ask` carries the persona's voice verbatim.
    # Full rationale: docs/architecture.md

    # 3. Escape to dismiss overlays. Some Claude.ai widgets take two Esc
    # presses (one to close active element, one to dismiss the wrapper),
    # so we try twice with a rescan between.
    try:
        page.keyboard.press("Escape")
        time.sleep(0.3)
        inp = _rescan()
        if inp is not None:
            _log("escape_once")
            return inp
    except Exception:
        pass
    try:
        widget_visible = _selection_widget_visible(page)
    except Exception:
        widget_visible = False
    if widget_visible:
        try:
            page.keyboard.press("Escape")
            time.sleep(0.4)
            inp = _rescan()
            if inp is not None:
                _log("escape_selection_widget")
                return inp
        except Exception:
            pass

    # 4. Scroll message list to bottom.
    try:
        page.evaluate(
            "() => window.scrollTo({top: document.body.scrollHeight, "
            "behavior: 'instant'})"
        )
        time.sleep(0.3)
        inp = _rescan()
        if inp is not None:
            _log("scroll_bottom")
            return inp
    except Exception:
        pass

    # 5. Tab-cycle to find a focusable textbox role.
    try:
        for _ in range(6):
            page.keyboard.press("Tab")
            time.sleep(0.1)
            inp = _rescan()
            if inp is not None:
                _log("tab_cycle")
                return inp
    except Exception:
        pass

    # 6. Last resort: reload the current chat. Preserves the chat id
    # so conversation history stays intact.
    try:
        current_url = page.url
        if "/chat/" in current_url:
            page.goto(current_url, timeout=20000)
            time.sleep(2.5)
            inp = _rescan()
            if inp is not None:
                _log("chat_reload")
                return inp
            # Widget may have re-rendered after reload. Do NOT click Skip —
            # Skip posts "no preference" and breaks persona authenticity.
            # Fall through to None; caller emits selection_widget=visible
            # diagnostic + user-sim's next typed `ask` re-mounts the input
            # with the persona's real answer.
    except Exception:
        pass

    return None


def _read_last_assistant_text(page) -> str:
    """Return the rendered text of the last assistant message (what a human sees)."""
    for sel in ASSISTANT_MSG_SELECTORS:
        loc = page.locator(sel)
        count = loc.count()
        if count > 0:
            try:
                return loc.nth(count - 1).inner_text().strip()
            except Exception:
                continue
    # Fallback: last message-looking div in an article role
    try:
        articles = page.locator("article, [role='article']")
        n = articles.count()
        if n:
            return articles.nth(n - 1).inner_text().strip()
    except Exception:
        pass
    return ""


def _last_assistant_locator(page):
    """Return a Playwright locator for the last assistant message, or None.

    Extracted so artifact/code scans can scope to the same DOM subtree
    that `_read_last_assistant_text` pulled text from — otherwise we
    could accidentally pick up code blocks from earlier turns.
    """
    for sel in ASSISTANT_MSG_SELECTORS:
        loc = page.locator(sel)
        try:
            count = loc.count()
        except Exception:
            continue
        if count > 0:
            return loc.nth(count - 1)
    try:
        articles = page.locator("article, [role='article']")
        n = articles.count()
        if n:
            return articles.nth(n - 1)
    except Exception:
        pass
    return None


def _expand_thinking_blocks(last) -> int:
    """Click any collapsed thinking-block expander in the last assistant msg.

    Returns the number of expanders clicked. Best-effort — each selector
    + click is wrapped so a missing / shifted DOM fails the capture, not
    the turn. Idempotent: an already-expanded block has aria-expanded=true
    and gets skipped.
    """
    if last is None:
        return 0
    clicked = 0
    for sel in THINKING_EXPAND_BUTTON_SELECTORS:
        try:
            btns = last.locator(sel)
            count = btns.count()
        except Exception:
            continue
        for i in range(count):
            try:
                btn = btns.nth(i)
                if btn.is_visible():
                    btn.click(timeout=2000)
                    clicked += 1
            except Exception:
                # Don't let one bad click abort the rest.
                continue
    if clicked:
        # Small settle delay so the expanded content is fully rendered
        # before any subsequent inner_text() read.
        time.sleep(0.3)
    return clicked


def _read_thinking_text(last) -> str:
    """Read the expanded thinking-block transcript from the last assistant msg.

    Returns "" if no thinking container is present. Callers should have
    already called `_expand_thinking_blocks` so the content is rendered
    rather than collapsed.
    """
    if last is None:
        return ""
    for sel in THINKING_CONTAINER_SELECTORS:
        try:
            containers = last.locator(sel)
            count = containers.count()
        except Exception:
            continue
        if count == 0:
            continue
        # Read each matching container's text, concatenate (usually there's
        # just one per assistant message).
        parts = []
        for i in range(count):
            try:
                text = containers.nth(i).inner_text().strip()
            except Exception:
                continue
            if text:
                parts.append(text)
        if parts:
            return "\n\n".join(parts)
    return ""


def _read_last_assistant_parts(page) -> tuple[str, str]:
    """Read (thinking, reply) for the last assistant message.

    Expands any collapsed thinking block first so the full transcript is
    captured. Reply is the inner_text of the full assistant message with
    the thinking-block text stripped from the front (claude.ai always
    renders thinking above the reply). Stripping is exact-prefix only;
    on no match the reply falls back to the full text (worst case:
    thinking is duplicated in the reply section — not a correctness bug
    for the trace reader, just noise).

    Returns ("", full_text) when no thinking block is detected.
    """
    last = _last_assistant_locator(page)
    if last is None:
        return "", ""

    try:
        _expand_thinking_blocks(last)
    except Exception:
        # Expansion is enhancement-only; fall through to read whatever
        # rendered DOM is available.
        pass

    thinking = ""
    try:
        thinking = _read_thinking_text(last)
    except Exception:
        thinking = ""

    try:
        full = last.inner_text().strip()
    except Exception:
        full = ""

    if thinking and full.startswith(thinking):
        reply = full[len(thinking):].lstrip()
    else:
        reply = full
    return thinking, reply


def _extract_rich_content(page) -> list[dict[str, str]]:
    """Collect artifacts + code blocks from the last assistant message.

    Returns a list of dicts with keys `kind`, `title`, `language`, `text`.
    `kind` is one of:
      - "artifact": content inside an artifact container (title + body)
      - "mermaid":  a <pre><code class="language-mermaid"> block (source)
      - "code":     any other <pre><code class="language-X"> block

    Everything is best-effort. Each selector path is wrapped in try/except
    so a DOM shift on claude.ai's side degrades the trace (missing
    artifacts) instead of crashing user-sim mid-turn.

    No button clicks, no screenshots, no DOM mutation — pure read.
    """
    last = _last_assistant_locator(page)
    if last is None:
        return []

    found: list[dict[str, str]] = []
    seen_texts: set[str] = set()

    # 1. Artifact containers — title + rendered body text.
    for sel in ARTIFACT_CONTAINER_SELECTORS:
        try:
            containers = last.locator(sel)
            count = containers.count()
        except Exception:
            continue
        for i in range(count):
            try:
                node = containers.nth(i)
                body = node.inner_text().strip()
                if not body or body in seen_texts:
                    continue
                title = ""
                for ts in ARTIFACT_TITLE_SELECTORS:
                    try:
                        title_loc = node.locator(ts).first
                        if title_loc.count() > 0:
                            title = title_loc.inner_text().strip()
                            if title:
                                break
                    except Exception:
                        continue
                found.append({
                    "kind": "artifact",
                    "title": title or "(untitled artifact)",
                    "language": "",
                    "text": body,
                })
                seen_texts.add(body)
            except Exception:
                continue

    # 2. Code blocks — distinguish mermaid so diagram source is labeled.
    try:
        code_nodes = last.locator(CODE_BLOCK_SELECTOR)
        count = code_nodes.count()
    except Exception:
        count = 0
    for i in range(count):
        try:
            node = code_nodes.nth(i)
            text = node.inner_text().strip()
            if not text or text in seen_texts:
                continue
            lang = ""
            try:
                cls = node.get_attribute("class") or ""
                for token in cls.split():
                    if token.startswith("language-"):
                        lang = token[len("language-"):]
                        break
            except Exception:
                lang = ""
            kind = "mermaid" if lang.lower() == "mermaid" else "code"
            found.append({
                "kind": kind,
                "title": "",
                "language": lang,
                "text": text,
            })
            seen_texts.add(text)
        except Exception:
            continue

    return found


def _format_rich_content(items: list[dict[str, str]]) -> str:
    """Render rich-content items as trace-friendly markdown sections.

    Empty list → empty string (caller can concat unconditionally).
    """
    if not items:
        return ""
    out: list[str] = []
    for item in items:
        kind = item.get("kind", "code")
        text = item.get("text", "")
        if kind == "artifact":
            title = item.get("title", "(untitled artifact)")
            out.append(f"\n[ARTIFACT: {title}]\n{text}")
        elif kind == "mermaid":
            out.append(f"\n[MERMAID]\n{text}")
        else:
            lang = item.get("language", "")
            label = f"CODE:{lang}" if lang else "CODE"
            out.append(f"\n[{label}]\n{text}")
    return "".join(out)


def _wait_for_response_complete(
    page, prev_text: str, timeout_s: int = 180,
) -> tuple[str, bool]:
    """Poll until a new assistant message appears and stops streaming.

    Returns (text, timed_out). `timed_out=True` means the deadline hit
    without the text going stable — the caller should dump diagnostics.
    Previously this returned only the text, so callers couldn't tell a
    valid short reply from a stuck/streaming session.
    """
    deadline = time.monotonic() + timeout_s
    last_seen = prev_text
    stable_ticks = 0
    while time.monotonic() < deadline:
        time.sleep(1.5)
        # Clear any mid-response permission dialogs (Allow tool call, etc.)
        _dismiss_dialogs(page)
        text = _read_last_assistant_text(page)
        if text and text != prev_text:
            # A new assistant message exists. Watch for streaming to finish.
            stop_btn = _first_visible(page, STOP_BUTTON_SELECTORS)
            if stop_btn is None:
                # No stop button: check stability of text
                if text == last_seen:
                    stable_ticks += 1
                    if stable_ticks >= 2:
                        return text, False
                else:
                    stable_ticks = 0
                    last_seen = text
            else:
                stable_ticks = 0
                last_seen = text
    return last_seen, True  # timed out; return whatever we have


def _format_assistant_trace(
    *,
    response: str,
    thinking: str,
    rich_section: str,
    timed_out: bool = False,
) -> str:
    """Compose the trace body for a CLAUDE -> USER_SIM entry.

    When thinking is non-empty, emits explicit `<thinking>` / `<reply>`
    sections so navigator can see the reasoning chain separately from the
    final visible reply. Otherwise falls back to the plain response body
    (backward-compat with the existing trace format).
    """
    reply_body = response or "(empty response)"
    if thinking:
        return (
            f"<thinking>\n{thinking}\n</thinking>\n"
            f"<reply>\n{reply_body}\n</reply>"
            f"{rich_section}"
        )
    return f"{reply_body}{rich_section}"


def _trace_timestamp() -> str:
    """ISO-8601 with millisecond precision + local tz offset.

    Navigator reads the trace chronologically across provider sessions
    with overlapping time windows; millisecond precision + explicit tz
    offset removes ambiguity on close-in-time events.
    """
    now = dt.datetime.now().astimezone()
    # strftime %f is microseconds (6 digits); trim to ms (3 digits).
    ms = now.strftime("%f")[:3]
    return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms}" + now.strftime("%z")


def _append_trace(kind: str, body: str) -> None:
    TRACE.parent.mkdir(parents=True, exist_ok=True)
    ts = _trace_timestamp()
    with TRACE.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## [{ts}] {kind}\n{body}\n")


def _capture_failure_dump(page, reason: str, *, note: str = "") -> str:
    """Save DOM + screenshot when the driver can't recover.

    Writes three artifacts under `output/claude_chat_failures/`:
      - <ts>_<reason>.html   : full rendered DOM
      - <ts>_<reason>.png    : screenshot
      - <ts>_<reason>.txt    : url + visible text + note

    Returns the shared basename (without extension) so callers can reference
    it in logs. All failures are swallowed — if the dump itself errors, we
    still want the caller to exit cleanly.
    """
    try:
        FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)[:40]
        base = FAILURE_DIR / f"{ts}_{safe_reason}"
    except OSError as exc:
        print(f"WARN: could not create failure dump dir: {exc}", file=sys.stderr)
        return ""

    try:
        html = page.content()
        base.with_suffix(".html").write_text(html, encoding="utf-8")
    except Exception as exc:
        print(f"WARN: failed to dump DOM: {exc}", file=sys.stderr)

    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    except Exception as exc:
        print(f"WARN: failed to screenshot: {exc}", file=sys.stderr)

    try:
        url = ""
        try:
            url = page.url
        except Exception:
            pass
        visible = ""
        try:
            visible = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            pass
        base.with_suffix(".txt").write_text(
            f"reason: {reason}\n"
            f"url: {url}\n"
            f"note: {note}\n"
            f"--- visible text ---\n{visible[:8000]}\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"WARN: failed to write failure context: {exc}", file=sys.stderr)

    return base.name


def cmd_ask(message: str) -> int:
    pw, browser = _connect()
    try:
        page = _ensure_claude_page(browser)
        page.bring_to_front()
        # Clear any pending permission dialogs before reading or typing.
        _dismiss_dialogs(page)
        prev = _read_last_assistant_text(page)

        inp = _first_usable_input(page)
        if inp is None:
            # Recovery: try several automatic strategies before giving up.
            # The most common case (per failure dumps) is that claude.ai
            # replaced the input with an ask-user-option selection widget
            # — Escape usually restores the free-text input without new-chat
            # ing, which would drop conversation context. A visible-but-
            # locked input (aria-disabled, contenteditable=false, or a
            # disabled <textarea>) also lands here so the ladder can try
            # to unblock before we type into a dead node.
            recovery_steps: list[str] = []
            inp = _try_recover_input(
                page, on_step=lambda name: recovery_steps.append(name),
            )
            if inp is None:
                widget_active = _selection_widget_visible(page)
                # When input is missing AND no selection widget is up,
                # the most likely cause is an undetected permission
                # gate (#73). Capture a separate probe dump so we can
                # iterate selectors without needing user-sim to notice.
                suspected_dump = ""
                if not widget_active:
                    suspected_dump = _dump_suspected_dialog(
                        page, reason="suspected_permission_gate",
                    )
                dump = _capture_failure_dump(
                    page, "input_not_found",
                    note=(
                        f"prev_text_len={len(prev)}; "
                        f"message_preview={message[:80]!r}; "
                        f"recovery_attempted={recovery_steps or 'all_failed'}; "
                        f"selection_widget={'visible' if widget_active else 'none'}; "
                        f"suspected_dialog_dump={suspected_dump or 'none'}"
                    ),
                )
                if widget_active:
                    print(
                        "ERROR: claude.ai's ask-user-option selection "
                        "widget is blocking the input and Escape did "
                        "not clear it. Retry this `ask` with your "
                        "persona's freeform answer typed into the "
                        "message — posting a new user turn re-mounts "
                        "the input. Do NOT click Skip (posts 'no "
                        "preference'; see the option-select recovery test). Diagnostic "
                        f"dump: output/claude_chat_failures/{dump}."
                        "{html,png,txt}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "ERROR: could not find chat input on claude.ai "
                        "(even after recovery attempts). Is the page "
                        "loaded? Diagnostic dump: "
                        f"output/claude_chat_failures/{dump}."
                        "{html,png,txt}",
                        file=sys.stderr,
                    )
                return 3
            # Recovery worked. Emit one line to stderr so user-sim knows
            # the chat state was auto-repaired.
            print(
                f"INFO: recovered chat input via {recovery_steps}",
                file=sys.stderr,
            )
        # Type the message AND verify the full text landed in the composer
        # before we submit. This guards the host-reported truncation class:
        # dropped keystrokes / interleaving / a partial early-submit that the
        # post-send check could not catch. Clearing stale text is handled
        # inside the helper (per-attempt). Newlines go in as Shift+Enter so
        # an embedded newline never triggers Enter-to-send mid-message.
        typed_ok, composer_text = _type_message_verified(page, inp, message)
        if not typed_ok:
            block_note = _visible_submit_block_note(page)
            dump = _capture_failure_dump(
                page,
                "message_truncated_typing",
                note=(
                    "composer did not contain the full message after typing "
                    "(dropped keystrokes / interleave / partial early submit); "
                    "nothing was sent. "
                    f"message_len={len(message)}; "
                    f"message_preview={message[:80]!r}; "
                    f"composer_preview={composer_text[:120]!r}; "
                    f"block_note={block_note or 'none'}"
                ),
            )
            print(
                "ERROR: message was NOT sent — the composer never held the "
                "full message after typing (truncation / keystroke-drop "
                "guard). Nothing was submitted, so no fragment went out. "
                "Re-run the same `ask`. Diagnostic dump: "
                f"output/claude_chat_failures/{dump}.{{html,png,txt}}",
                file=sys.stderr,
            )
            return 7
        # Prefer the send button if visible; fall back to Enter.
        send = _first_visible(page, SEND_BUTTON_SELECTORS)
        if send is not None:
            if _locator_is_disabled(send):
                block_note = _visible_submit_block_note(page)
                dump = _capture_failure_dump(
                    page,
                    "send_blocked",
                    note=(
                        "send button disabled before click; "
                        f"message_preview={message[:80]!r}; "
                        f"block_note={block_note or 'none'}"
                    ),
                )
                print(
                    "ERROR: message was not submitted; send button is "
                    "disabled. Diagnostic dump: "
                    f"output/claude_chat_failures/{dump}.{{html,png,txt}}",
                    file=sys.stderr,
                )
                return 6
            send.click()
        else:
            page.keyboard.press("Enter")

        time.sleep(1.0)
        still_in_composer, composer_text = _composer_contains_message(
            page, message,
        )
        assistant_after_send = _read_last_assistant_text(page)
        assistant_changed = (
            bool(assistant_after_send.strip())
            and assistant_after_send.strip() != prev.strip()
        )
        if still_in_composer and not assistant_changed:
            block_note = _visible_submit_block_note(page)
            dump = _capture_failure_dump(
                page,
                "message_not_submitted",
                note=(
                    "prompt remained in composer after send attempt; "
                    f"message_preview={message[:80]!r}; "
                    f"composer_preview={composer_text[:120]!r}; "
                    f"block_note={block_note or 'none'}"
                ),
            )
            print(
                "ERROR: message was not submitted; prompt remains in "
                "composer after the send attempt. Diagnostic dump: "
                f"output/claude_chat_failures/{dump}.{{html,png,txt}}",
                file=sys.stderr,
            )
            return 6

        _append_trace("USER_SIM -> CLAUDE", message)
        response, timed_out = _wait_for_response_complete(page, prev)

        # Capture rich content (artifacts, mermaid, code blocks) from the
        # last assistant turn. inner_text alone misses auto-rendered
        # diagrams and artifacts — user-sim would otherwise see none of it
        # in the trace.
        rich_items: list[dict[str, str]] = []
        try:
            rich_items = _extract_rich_content(page)
        except Exception as exc:
            # Never let trace enrichment break the primary flow.
            print(f"WARN: rich-content capture failed: {exc}", file=sys.stderr)

        rich_section = _format_rich_content(rich_items)

        # Capture expanded thinking transcript separately so navigator can
        # read the chatbot's reasoning chain, not just the final reply.
        thinking_text = ""
        try:
            thinking_text, split_reply = _read_last_assistant_parts(page)
            # If thinking extraction succeeded and the split-out reply
            # roughly matches the `response` already captured, prefer the
            # split reply so we don't double-print the thinking prefix.
            if thinking_text and split_reply and len(split_reply) > 20:
                response = split_reply
        except Exception as exc:
            print(f"WARN: thinking capture failed: {exc}", file=sys.stderr)

        body = _format_assistant_trace(
            response=response,
            thinking=thinking_text,
            rich_section=rich_section,
            timed_out=timed_out,
        )

        if timed_out:
            note = (
                f"prev_len={len(prev)}; response_len={len(response)}; "
                f"message_preview={message[:80]!r}; rich_items={len(rich_items)}"
            )
            dump = _capture_failure_dump(page, "response_timeout", note=note)
            _append_trace(
                "CLAUDE -> USER_SIM",
                f"(TIMEOUT after 180s; partial response below; dump={dump})\n{body}",
            )
            print(response or "(no response captured)")
            print(
                f"WARN: response did not settle within 180s. "
                f"Diagnostic dump: output/claude_chat_failures/{dump}.{{html,png,txt}}",
                file=sys.stderr,
            )
            return 5

        _append_trace("CLAUDE -> USER_SIM", body)

        # Optional per-turn screenshot for artifact visual verification.
        # Opt-in via TINYASSETS_CLAUDE_CHAT_SCREENSHOTS=1 so normal runs stay
        # cheap. Saved alongside the trace under output/claude_chat_turns/.
        if os.environ.get("TINYASSETS_CLAUDE_CHAT_SCREENSHOTS") == "1":
            try:
                shot_dir = ROOT / "output" / "claude_chat_turns"
                shot_dir.mkdir(parents=True, exist_ok=True)
                ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
                page.screenshot(
                    path=str(shot_dir / f"{ts}.png"), full_page=True,
                )
            except Exception as exc:
                print(f"WARN: turn screenshot failed: {exc}", file=sys.stderr)

        # Print a short confirmation plus the response so user-sim can read it.
        print(response or "(no response captured)")
        if rich_items:
            print(
                f"(+{len(rich_items)} rich-content items captured: "
                f"{', '.join(item['kind'] for item in rich_items)})",
                file=sys.stderr,
            )
        return 0
    finally:
        browser.close()
        pw.stop()


def cmd_read() -> int:
    pw, browser = _connect()
    try:
        page = _ensure_claude_page(browser)
        text = _read_last_assistant_text(page)
        print(text or "(no assistant message visible)")
        return 0
    finally:
        browser.close()
        pw.stop()


def cmd_dismiss_dialogs() -> int:
    pw, browser = _connect()
    try:
        page = _ensure_claude_page(browser)
        n = _dismiss_dialogs(page)
        print(f"dismissed {n} dialog(s)")
        return 0
    finally:
        browser.close()
        pw.stop()


def cmd_new_chat() -> int:
    pw, browser = _connect()
    try:
        page = _ensure_claude_page(browser)
        page.goto(NEW_CHAT_URL, timeout=30000)
        time.sleep(2)
        # Post-navigation heal: claude.ai's SPA occasionally opens the
        # target as a new CDP target instead of navigating in-place,
        # which leaves an orphan for the watchdog to clean up. Running
        # _enforce_single_tab here closes the orphan synchronously —
        # watchdog no longer has to heal on every new-chat call.
        _enforce_single_tab(browser)
        print("new chat ready")
        return 0
    finally:
        browser.close()
        pw.stop()


def cmd_status() -> int:
    try:
        pw, browser = _connect(auto_launch=False)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    try:
        _enforce_single_tab(browser)
        page = _find_claude_page(browser)
        if page is None:
            print("CDP reachable; no claude.ai tab found.")
            return 1
        print(f"OK: claude.ai tab at {page.url}")
        return 0
    finally:
        browser.close()
        pw.stop()


def cmd_tabs() -> int:
    """Report open-tab count + URLs. Always also enforces the single-tab rule."""
    try:
        pw, browser = _connect(auto_launch=False)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    try:
        pages = _enumerate_pages(browser)
        urls_before = [(getattr(p, "url", "") or "(unknown)") for p in pages]
        closed = _enforce_single_tab(browser)
        pages_after = _enumerate_pages(browser)
        urls_after = [(getattr(p, "url", "") or "(unknown)") for p in pages_after]
        print(f"TAB HYGIENE: {len(pages_after)} tab(s) open; urls={urls_after}")
        if closed:
            print(f"  (healed from {len(pages)}; closed {closed}; before={urls_before})")
        return 0
    finally:
        browser.close()
        pw.stop()


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("message")
    sub.add_parser("read")
    sub.add_parser("new-chat")
    sub.add_parser("status")
    sub.add_parser("dismiss-dialogs")
    sub.add_parser("tabs")
    ns = p.parse_args()

    if ns.cmd == "ask":
        return cmd_ask(ns.message)
    if ns.cmd == "read":
        return cmd_read()
    if ns.cmd == "new-chat":
        return cmd_new_chat()
    if ns.cmd == "status":
        return cmd_status()
    if ns.cmd == "dismiss-dialogs":
        return cmd_dismiss_dialogs()
    if ns.cmd == "tabs":
        return cmd_tabs()
    return 1


if __name__ == "__main__":
    sys.exit(main())
