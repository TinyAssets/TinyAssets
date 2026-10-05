"""Render a person's own custom UI headlessly, so the agent that built it can see it.

The agent that writes a UI has no browser: it cannot tell whether its village
renders, how it looks, or whether it runs at a usable frame rate (founder's live
test, 2026-10-02). This renders ONE UI of the caller's own library in a headless
Chromium and reports what a person would see: a PNG of the screen, frames per
second over a short window, console errors and uncaught exceptions, the bridge
calls the UI made, and any request that tried to leave.

The render is the real thing, not an imitation. The shipped ``/app/ui-frame``
document runs under its shipped headers, receives the stored component, its
assets and its pinned libraries exactly as the app hands them over, and loads
them from ``blob:`` URLs. Only the parent is a stand-in, playing the app's part:

* **No network.** Every request is intercepted: the stand-in parent, the frame
  document and the bytes are served from memory, anything else is refused and
  reported (a WebSocket is refused by the frame's own policy). Underneath that the
  browser itself has no way out: every host name resolves to nothing and every
  connection goes to a dead proxy, so a channel interception misses still goes
  nowhere. No session, cookie or token exists in that browser at all.
* **Nothing stored runs in the parent.** The parent is a fixed page under a
  nonce-only policy; the UI arrives as a JSON document it fetches, never as text
  spliced into its HTML (Codex, 2026-10-02: a ``</script>`` in stored markup
  would otherwise have run in the unrestricted parent).
* **Read-only bridge.** ``whoami`` answers with the command center's id and the
  component's own alias maps, and the reads answer with every key the live
  bridge returns and no data behind them, so a UI renders its empty state rather
  than failing on a missing key. Every call that would act (``sendMessage``,
  ``emit``, ``setConversationDesign``) is refused as a preview, and each call is
  reported so the agent sees what its UI tried.

It runs as a short-lived subprocess tree (``python -m tinyassets.ui_preview``, its
Playwright driver and Chromium) in a PID namespace, watched from outside: a
wall clock, a resident-memory budget summed over the whole tree and a process
count, and on any breach -- or when the render ends -- the whole namespace is
killed and reaped before the slot frees. One render per HOST (a lock file the
per-universe engine processes share); a second is refused as busy, never
queued. A host without Playwright's Chromium refuses with
``ui_preview_unavailable`` -- never a blank image that looks like a result.
"""

from __future__ import annotations

import base64
import contextlib
import json
import math
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

#: One render at a time in this process; the host-wide lock file bounds the
#: per-universe engine processes together (POSIX).
_SLOT = threading.BoundedSemaphore(1)
_POISONED = ""
_POISONED_HOST_FD: int | None = None
_CONTAINMENT_FAILURE = (
    "ui_preview_failed: the previous render could not be stopped; "
    "previews are disabled until the daemon restarts"
)
#: Resident memory summed over the render's whole process tree, and its size.
TREE_MEMORY_BYTES = 1536 * 1024 * 1024
TREE_PROCESSES = 64
#: Bytes the child may print (the report and a PNG of at most MAX_WIDTH x MAX_HEIGHT).
MAX_CHILD_OUTPUT = 64 * 1024 * 1024
#: Bridge calls counted per action, and distinct actions kept.
MAX_CALLS_PER_ACTION, MAX_ACTIONS = 1000, 50
#: The sandbox the app gives a UI frame (app_ui.js AppUI.SANDBOX); the preview
#: must behave as the app does, forms included.
FRAME_SANDBOX = "allow-scripts allow-forms"
#: ui_ids that are device names on Windows; never a file name.
_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
                       *(f"lpt{i}" for i in range(10))})
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1024, 768
MAX_WIDTH, MAX_HEIGHT = 1920, 1920
#: Wait after the bundle is handed over before sampling, and the sample window.
SETTLE_MS, SAMPLE_MS = 1500, 1000
WALL_SECONDS = 60.0
MAX_REPORTED_LINES = 50
_ORIGIN = "https://preview.tinyassets.invalid"


class PreviewUnavailable(RuntimeError):
    """This host cannot render (no Playwright Chromium), or the slot is busy."""


def _refs(value: Any) -> dict[str, str]:
    """A stored alias map, as the live bridge hands one over.

    The app's ``whoami`` does ``this.active.workflow_refs||{}``: a component
    that declared none gets an empty map rather than a missing key. Anything
    stored that is not a map of names is the same empty map here.
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): str(target) for key, target in value.items()}


# The app's part, played from memory. The bridge answers as a read-only preview:
# the reads a UI starts with answer empty, every action is refused by name.
_PARENT = """<!doctype html><html><head><meta charset="utf-8">
<style nonce="__NONCE__">html,body{margin:0;height:100%}
iframe{border:0;width:100%;height:100%;display:block}</style>
</head><body><script nonce="__NONCE__">
window.__preview={calls:Object.create(null),dropped:0,delivered:false,error:""};
// Every key the app's bridge returns, with empty data behind it, so a UI
// renders its empty state instead of failing on a key that is only missing
// here. A key the app answers and this does not is a UI that works in the app
// and throws in preview (review, 2026-10-03: `readLive()` was refused outright
// and `whoami().workflow_refs` was undefined). The alias maps are the
// COMPONENT's own declaration -- the app reads them off the active component,
// not off the server -- so the preview answers with the real ones and a UI
// resolves its aliases here exactly as it will in the app. Keep every key here
// spelled out rather than computed: tests derive the key sets by parsing this.
const EMPTY_FOR=spec=>({whoami:{protocol:1,command_center_id:spec.universe_id,
    command_center_name:'Preview',
    workflow_refs:Object.assign({},spec.workflow_refs||{}),
    agent_refs:Object.assign({},spec.agent_refs||{})},
  list_agents:{agents:[]},read_conversation:{turns:[],has_more:false,next_before:null},
  list_automations:{automations:[]},list_runs:{runs:[],has_more:false},
  read_live:{as_of:new Date().toISOString(),agents:[]},
  'packages.list_tryable':{packages:[],systems:[],build_prompt:'',can_try:false},
  list_files:{path:'',entries:[],truncated:false},
  conversation_design:{state:'default',agent_definition_id:'',component_key:''}});
const bytes=async path=>(await (await fetch(path)).arrayBuffer());
const count=action=>{
  const p=window.__preview,name=String(action).slice(0,__ACTION_CHARS__);
  const own=Object.prototype.hasOwnProperty.call(p.calls,name);
  if(!own&&Object.keys(p.calls).length>=__MAX_ACTIONS__){p.dropped++;return;}
  p.calls[name]=Math.min((own?p.calls[name]:0)+1,__MAX_PER_ACTION__);
};
(async()=>{
  // The UI arrives as DATA: a JSON document this page fetches. Nothing stored
  // is ever spliced into this page's HTML.
  const SPEC=await (await fetch('/__preview/spec.json')).json();
  const EMPTY=EMPTY_FOR(SPEC);
  const frame=document.createElement('iframe');
  frame.setAttribute('sandbox','__SANDBOX__');
  frame.setAttribute('referrerpolicy','no-referrer');
  frame.src='/app/ui-frame';
  window.addEventListener('message',async e=>{
    if(e.source!==frame.contentWindow) return;
    const m=e.data;
    if(!m||typeof m!=='object'||m.ta_ui!==1) return;
    if(m.type==='ready'&&!window.__preview.delivered){
      window.__preview.delivered=true;
      try{
        const libraries=[];
        for(const [name,format] of SPEC.libraries)
          libraries.push({name,format,
            bytes:await bytes('/__preview/lib/'+encodeURIComponent(name))});
        const files=[];
        for(const [path,type] of SPEC.files)
          files.push({path,media_type:type,
            bytes:await bytes('/__preview/asset/'+encodeURIComponent(path))});
        const bundle=Object.assign({},SPEC.bundle,{files,libraries});
        frame.contentWindow.postMessage({ta_ui:1,type:'bundle',bundle},'*');
      }catch(err){ window.__preview.error=String(err&&err.message||err).slice(0,300); }
      return;
    }
    if(m.type==='call'&&typeof m.id==='string'){
      const action=String(m.action);
      count(action);
      const known=Object.prototype.hasOwnProperty.call(EMPTY,action);
      frame.contentWindow.postMessage(known
        ?{ta_ui:1,type:'result',id:m.id,ok:true,result:EMPTY[action]}
        :{ta_ui:1,type:'result',id:m.id,ok:false,
          error:'preview: '+action.slice(0,64)+' does not run while previewing'},'*');
    }
  });
  document.body.appendChild(frame);
})().catch(err=>{ window.__preview.error=String(err&&err.message||err).slice(0,300); });
</script></body></html>"""

# Counted inside the frame: animation frames the page actually produced.
_FPS_PROBE = """ms => new Promise(resolve => {
  let frames = 0; const start = performance.now();
  function tick(now) { frames++; if (now - start < ms) requestAnimationFrame(tick);
    else resolve(frames * 1000 / (now - start)); }
  requestAnimationFrame(tick);
})"""


def available() -> bool:
    """Whether this host has Playwright with an installed Chromium."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _spec_for(
    base_path: str | Path, owner_user_id: str, universe_id: str, ui_id: str,
    width: int, height: int,
) -> dict[str, Any]:
    from tinyassets.custom_agents import AgentNotFoundError, get_app_ui

    row = get_app_ui(base_path, owner_user_id=owner_user_id, universe_id=universe_id)
    entry = next((e for e in row["ui_library"]
                  if isinstance(e, dict) and e.get("ui_id") == ui_id), None)
    if entry is None:
        installed = [e.get("ui_id") for e in row["ui_library"] if isinstance(e, dict)]
        raise AgentNotFoundError(f"no UI with ui_id {ui_id!r}; installed: {installed}")
    return _component_spec(base_path, owner_user_id, universe_id, entry, width, height)


def _component_spec(base_path, owner_user_id, universe_id, entry, width, height):
    from tinyassets.onboarding import ui_library_set

    ui_id = entry.get("ui_id", "published")
    names = ui_library_set.load_order(list(entry.get("libraries") or []))
    manifest = ui_library_set.manifest()
    assets = entry.get("assets") or {}
    return {
        "base_path": str(base_path), "owner_user_id": owner_user_id,
        "universe_id": universe_id, "ui_id": ui_id,
        "width": width, "height": height,
        "libraries": [[name, manifest[name]["format"]] for name in names],
        "files": [[path, ref["media_type"]] for path, ref in assets.items()],
        "workflow_refs": _refs(entry.get("workflow_refs")),
        "agent_refs": _refs(entry.get("agent_refs")),
        "hashes": {path: ref["sha256"] for path, ref in assets.items()},
        "bundle": {"markup": entry.get("markup", ""), "style": entry.get("style", ""),
                   "script": entry.get("script", ""),
                   **({"script_type": "module"} if entry.get("script_type") == "module" else {})},
    }


def preview_app_ui(
    base_path: str | Path, *, owner_user_id: str, universe_id: str, ui_id: str,
    width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
    wall_seconds: float = WALL_SECONDS,
) -> dict[str, Any]:
    """Render the caller's own UI ``ui_id``; the report plus ``png`` bytes.

    Raises ``AgentNotFoundError`` for a UI the caller does not have and
    :class:`PreviewUnavailable` when this host cannot render or is rendering.
    """
    if _POISONED:
        raise PreviewUnavailable(_POISONED)
    if not (isinstance(width, int) and isinstance(height, int)
            and 200 <= width <= MAX_WIDTH and 200 <= height <= MAX_HEIGHT):
        raise ValueError(f"width and height must be 200..{MAX_WIDTH} pixels")
    spec = _spec_for(base_path, owner_user_id, universe_id, ui_id, width, height)
    return _render_spec(spec, wall_seconds)


def preview_public_component(component: dict[str, Any]) -> dict[str, Any]:
    """The same app_ui_preview renderer, with only an immutable public screen.

    No owner identity or private blob references reach the browser. Published
    screens currently exclude assets; refuse rather than resolving private ones.
    """
    if component.get("assets"):
        raise PreviewUnavailable("published_preview_assets_unsupported")
    spec = _component_spec("", "", "preview", component, DEFAULT_WIDTH, DEFAULT_HEIGHT)
    return _render_spec(spec, WALL_SECONDS)


def _render_spec(spec: dict[str, Any], wall_seconds: float) -> dict[str, Any]:
    if _POISONED:
        raise PreviewUnavailable(_POISONED)
    if not available():
        raise PreviewUnavailable(
            "ui_preview_unavailable: this host has no headless browser to render with")
    if not _SLOT.acquire(blocking=False):
        raise PreviewUnavailable("ui_preview_busy: another preview is rendering; try again")
    try:
        return _run_child(spec, wall_seconds)
    finally:
        if not _POISONED:
            _SLOT.release()


def _run_child(spec: dict[str, Any], wall_seconds: float) -> dict[str, Any]:
    with _host_slot():
        out, err, code, breach = _supervised(json.dumps(spec).encode("utf-8"), wall_seconds)
    if breach:
        raise PreviewUnavailable(f"ui_preview_{breach}")
    lines = [line for line in out.decode("utf-8", "replace").splitlines()
             if line.startswith("{")]
    if code != 0 or not lines:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise PreviewUnavailable(f"ui_preview_failed: {tail[0][:300]}")
    report = json.loads(lines[-1])
    if report.get("unavailable"):
        raise PreviewUnavailable(f"ui_preview_unavailable: {report['unavailable']}")
    report["png"] = base64.b64decode(report.pop("png_base64"))
    return report


@contextlib.contextmanager
def _host_slot():
    """One render per host: an flock the per-universe engine processes share.
    Busy is a refusal, never a queue. Windows dev hosts: this process only."""
    global _POISONED_HOST_FD
    import os

    try:
        import fcntl
    except ImportError:
        yield
        return
    from tinyassets.storage import data_dir

    path = Path(data_dir()) / ".ui-preview.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PreviewUnavailable(
                "ui_preview_busy: another preview is rendering on this host; try again") from None
        yield
    finally:
        if _POISONED:
            _POISONED_HOST_FD = fd
        else:
            os.close(fd)


def _proc_snapshot() -> dict[int, tuple[int, int, bool, int]]:
    """Host PID -> (parent PID, RSS bytes, zombie, starttime) from Linux /proc."""
    import os

    snapshot = {}
    page = os.sysconf("SC_PAGE_SIZE")
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
        except OSError:
            continue
        snapshot[int(entry)] = (int(fields[1]), int(fields[21]) * page,
                                fields[0] == b"Z", int(fields[19]))
    return snapshot


def _descendants(root: int, snapshot: dict[int, tuple[int, int, bool, int]]) -> set[int]:
    # Traverse zombies too: their live children still belong to this tree.
    children: dict[int, list[int]] = {}
    for pid, (ppid, _, _, _) in snapshot.items():
        children.setdefault(ppid, []).append(pid)
    found = set()
    pending = list(children.get(root, []))
    while pending:
        pid = pending.pop()
        if pid not in found:
            found.add(pid)
            pending.extend(children.get(pid, []))
    return found


def _supervised(stdin: bytes, wall_seconds: float,
                argv: list[str] | None = None) -> tuple[bytes, bytes, int, str]:
    """Contain even detached Chromium processes in a Linux PID namespace."""
    global _POISONED
    import os
    import signal

    posix = os.name == "posix"
    linux = sys.platform == "linux"
    command = argv or [sys.executable, "-m", "tinyassets.ui_preview"]
    if linux:
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise PreviewUnavailable("ui_preview_unavailable: previews need bubblewrap")
        # No filesystem isolation: bwrap contains the PID tree only. Chromium's
        # own sandbox remains enabled and nests inside bwrap's user namespace.
        command = [bwrap, "--unshare-pid", "--die-with-parent", "--bind", "/", "/",
                   "--dev", "/dev", "--proc", "/proc", "--", *command]
    elif posix:
        raise PreviewUnavailable("ui_preview_unavailable: previews need Linux")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[1]),
        start_new_session=posix,
        creationflags=0 if posix else getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    chunks: dict[str, list[bytes]] = {"out": [], "err": []}
    sizes = {"out": 0, "err": 0}

    def drain(name: str, stream: Any, cap: int) -> None:
        for chunk in iter(lambda: stream.read(65536), b""):
            if sizes[name] < cap:
                chunks[name].append(chunk[: cap - sizes[name]])
            sizes[name] += len(chunk)

    readers = [threading.Thread(target=drain, args=("out", process.stdout, MAX_CHILD_OUTPUT),
                                daemon=True),
               threading.Thread(target=drain, args=("err", process.stderr, 65536), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(stdin)
        process.stdin.close()
    except OSError:
        pass
    deadline = time.monotonic() + wall_seconds
    breach = ""
    seen: dict[int, int] = {}
    try:
        while (os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
               if linux else process.poll()) is None:
            if linux:
                snapshot = _proc_snapshot()
                descendants = _descendants(process.pid, snapshot)
                seen.update({pid: snapshot[pid][3] for pid in descendants})
                members = [info for pid, info in snapshot.items()
                           if not info[2] and seen.get(pid) == info[3]]
                if sum(rss for _, rss, _, _ in members) > TREE_MEMORY_BYTES:
                    breach = "memory: the render used more memory than a preview may"
                elif len(members) > TREE_PROCESSES:
                    breach = "processes: the render started more processes than a preview may"
            if time.monotonic() > deadline:
                breach = f"timeout: the render did not finish in {wall_seconds:.0f} s"
            if sizes["out"] > MAX_CHILD_OUTPUT:
                breach = "failed: the render printed more than a preview may"
            if breach:
                break
            time.sleep(0.25)
    finally:
        if linux:
            # Keep bwrap unreaped: its PID cannot be reused while cleanup runs.
            # Its parent-death signal kills namespace init, and the kernel then
            # kills every namespace member. Never signal another numeric PID.
            settle = time.monotonic() + 10
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(process.pid, signal.SIGKILL)
            while True:
                snapshot = _proc_snapshot()
                # Once bwrap is dead its children re-parent away from it, so a
                # member still dying is found by identity (pid + starttime) too.
                alive = {pid for pid in _descendants(process.pid, snapshot)
                         if not snapshot[pid][2]}
                alive |= {pid for pid, start in seen.items()
                          if pid in snapshot and snapshot[pid][3] == start
                          and not snapshot[pid][2]}
                if not alive or time.monotonic() >= settle:
                    break
                time.sleep(0.1)
            for reader in readers:
                reader.join(timeout=max(0, settle - time.monotonic()))
            contained = not alive and not any(reader.is_alive() for reader in readers)
            # Reaping is the final cleanup operation, after all tree walks.
            try:
                process.wait(timeout=max(0, settle - time.monotonic()))
            except subprocess.TimeoutExpired:
                contained = False
            if not contained:
                _POISONED = _CONTAINMENT_FAILURE
                raise PreviewUnavailable(_POISONED)
        else:
            # Windows is a dev host only; production containment requires Linux.
            _kill_tree_windows(process.pid)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            process.wait()
            for reader in readers:
                reader.join(timeout=5)

    return b"".join(chunks["out"]), b"".join(chunks["err"]), process.returncode, breach


def _kill_tree_windows(pid: int) -> None:  # pragma: no cover - dev hosts only
    try:
        import psutil
    except ImportError:
        return
    with contextlib.suppress(Exception):
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            with contextlib.suppress(Exception):
                child.kill()


def _child(spec: dict[str, Any]) -> dict[str, Any]:
    from urllib.parse import unquote, urlsplit

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    from tinyassets.custom_agents import read_app_ui_asset
    from tinyassets.onboarding import ui_library_set
    from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_HEADERS

    served: dict[str, bytes] = {}
    for name, _ in spec["libraries"]:
        served["/__preview/lib/" + name] = ui_library_set.library_bytes(name)
    missing = []
    for path, sha in spec["hashes"].items():
        found = read_app_ui_asset(spec["base_path"], owner_user_id=spec["owner_user_id"],
                                  sha256=sha)
        if found is None:
            missing.append(path)
        else:
            served["/__preview/asset/" + path] = found
    blocked: list[str] = []
    console: list[str] = []
    errors: list[str] = []
    import secrets

    nonce = secrets.token_urlsafe(18)
    parent = (_PARENT.replace("__NONCE__", nonce).replace("__SANDBOX__", FRAME_SANDBOX)
              .replace("__ACTION_CHARS__", "64").replace("__MAX_ACTIONS__", str(MAX_ACTIONS))
              .replace("__MAX_PER_ACTION__", str(MAX_CALLS_PER_ACTION)))
    spec_json = json.dumps({k: spec[k] for k in (
        "universe_id", "libraries", "files", "bundle", "workflow_refs", "agent_refs")})
    parent_headers = {
        # The parent runs only this fixed script; it fetches its own origin and
        # frames its own origin, and nothing else is possible from it.
        "Content-Security-Policy": (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'"),
        "X-DNS-Prefetch-Control": "off",
    }

    def route(r):
        url = urlsplit(r.request.url)
        path = unquote(url.path)
        if f"{url.scheme}://{url.netloc}" == _ORIGIN:
            if path == "/":
                return r.fulfill(status=200, content_type="text/html", body=parent,
                                 headers=parent_headers)
            if path == "/__preview/spec.json":
                return r.fulfill(status=200, content_type="application/json", body=spec_json)
            if path == "/app/ui-frame":
                return r.fulfill(status=200, content_type="text/html", body=BOOTSTRAP_HTML,
                                 headers=dict(FRAME_HEADERS))
            if path in served:
                return r.fulfill(status=200, content_type="application/octet-stream",
                                 body=served[path])
        if len(blocked) < MAX_REPORTED_LINES:
            blocked.append(r.request.url[:300])
        return r.abort()

    with sync_playwright() as p:
        try:
            # The UI is somebody's arbitrary code: Chromium's own sandbox stays
            # ON (Playwright turns it off by default), so a renderer exploit is
            # confined the way any browser tab is, not handed the daemon's user.
            # And no network underneath the interception: no name resolves and
            # every connection goes to a dead local proxy.
            browser = p.chromium.launch(
                chromium_sandbox=True,
                args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                      "--disable-dev-shm-usage", "--no-first-run",
                      "--host-resolver-rules=MAP * ~NOTFOUND",
                      "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
                      "--disable-background-networking", "--disable-component-update",
                      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        except PlaywrightError as exc:
            return {"unavailable": str(exc).splitlines()[0][:300]}
        try:
            # No service_workers="block": its injected shim throws inside a
            # sandboxed frame and would be reported as the UI's own error.
            context = browser.new_context(
                viewport={"width": spec["width"], "height": spec["height"]})
            page = context.new_page()
            page.on("console", lambda m: (m.type in ("error", "warning")
                                          and len(console) < MAX_REPORTED_LINES
                                          and console.append(f"{m.type}: {m.text[:500]}")))
            page.on("pageerror", lambda e: len(errors) < MAX_REPORTED_LINES
                    and errors.append(str(e)[:500]))
            # No route_web_socket: its injected shim hangs the sandboxed frame.
            # A WebSocket is refused by the frame's connect-src, and failing
            # that has no resolver and only a dead proxy to reach.
            context.route("**/*", route)
            started = time.monotonic()
            page.goto(_ORIGIN + "/")
            # Polled with evaluate, not wait_for_function: the latter evals its
            # predicate inside the page, which the parent's nonce-only policy refuses.
            for _poll in range(150):
                if page.evaluate("window.__preview && window.__preview.delivered"):
                    break
                page.wait_for_timeout(100)
            page.wait_for_timeout(SETTLE_MS)
            frame = next((f for f in page.frames if f.url.endswith("/app/ui-frame")), None)
            fps = None
            if frame is not None:
                try:
                    fps = round(frame.evaluate(_FPS_PROBE, SAMPLE_MS), 1)
                except PlaywrightError as exc:
                    errors.append(f"frame rate could not be measured: {str(exc)[:200]}")
            png = page.screenshot(type="png")
            # JSON preserves prototype-named keys across Playwright's transport.
            state = json.loads(page.evaluate("JSON.stringify(window.__preview)"))
            loaded_ms = int((time.monotonic() - started) * 1000)
        finally:
            browser.close()
    raw_calls = state.get("calls") if isinstance(state.get("calls"), dict) else {}
    calls = {str(k)[:64]: int(v) for k, v in list(raw_calls.items())[:MAX_ACTIONS]
             if isinstance(v, (int, float)) and math.isfinite(v)}
    return {
        "ui_id": spec["ui_id"], "width": spec["width"], "height": spec["height"],
        "fps": fps, "rendered_ms": loaded_ms,
        "uncaught_errors": errors, "console": console,
        "bridge_calls": calls, "bridge_actions_dropped": int(state.get("dropped") or 0),
        "blocked_requests": blocked,
        "missing_assets": missing, "delivery_error": state.get("error") or "",
        "png_base64": base64.b64encode(png).decode("ascii"),
    }


def main() -> int:
    spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sys.stdout.write(json.dumps(_child(spec)) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


PREVIEW_DIR = "previews"


def write_preview(universe_dir: str | Path, ui_id: str, png: bytes) -> str:
    """Put ``png`` at ``/u/previews/<ui_id>.png``; the path as the agent sees it.

    The folder is the agent's own and the agent can change it WHILE this runs,
    so the bytes go through the one universe writer,
    :func:`tinyassets.universe_files.write_universe_file`, rather than a second
    copy of the same care: it opens every component following no link, creates
    the temp file ``O_EXCL`` in the directory it just verified, and ``os.replace``s
    it in between those same descriptors. So a ``previews`` swapped for a link
    between a check and the write cannot redirect it (Codex, 2026-10-02), and a
    planted hard link at the name is replaced rather than written through.

    The universe root is checked here, not there. ``write_universe_file``
    resolves its root before the no-follow walk, so that walk is link-free only
    BELOW the root and cannot refuse a link at or above the universe dir itself
    (``docs/concerns/2026-10-02-universe-files-resolves-its-root.md``). Today
    the only caller hands over an already-resolved path -- ``api/helpers``'
    ``_universe_dir`` does ``(base / universe_id).resolve()`` -- so this cannot
    fire through ``api/app_ui``; it is here so a future caller that passes an
    unresolved path does not silently write through a linked ancestor.
    """
    import os
    import re

    from tinyassets import workspace_fs as fs
    from tinyassets.universe_files import write_universe_file

    # The server stores any non-empty ui_id; only the app's own id shape names
    # a file, so nothing like "../x" ever becomes a path.
    ui_id = str(ui_id)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", ui_id) or ui_id in _RESERVED:
        raise PreviewUnavailable(
            f"ui_preview_failed: ui_id {ui_id!r} is not lowercase letters, digits and dashes")
    name = f"{ui_id}.png"
    try:
        if fs._POSIX:
            os.close(fs.open_dir_nofollow(universe_dir))
        write_universe_file(universe_dir, f"{PREVIEW_DIR}/{name}", png)
    except Exception as exc:  # noqa: BLE001 - every filesystem refusal is a named failure
        # UniverseFileError (a link or a non-directory on the path), a directory
        # at the target, permissions: the screenshot is not written, and the
        # agent is told why.
        raise PreviewUnavailable(
            f"ui_preview_failed: /u/{PREVIEW_DIR}/{name} could not be written "
            f"({type(exc).__name__}: {str(exc)[:200]})") from None
    return f"/u/{PREVIEW_DIR}/{name}"
