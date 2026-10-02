"""The isolated document a user-authored UI bundle runs inside.

A bundle is somebody's arbitrary code — often somebody the viewer has never met,
because bundles are shared by publish/remix. Nothing here tries to sanitize it.
The containment is the document's own policy, and it holds whatever the code does:

* ``sandbox allow-scripts allow-forms`` as a **response header** CSP directive, so the opaque
  origin applies however the document was loaded — framed by the app or navigated
  to directly. Without ``allow-same-origin`` the document cannot read the app's
  ``sessionStorage`` (which is where the access token lives), ``localStorage``,
  cookies, or any node of the parent DOM. ``allow-forms`` is there only so a
  bundle's ``<form>`` fires its ``submit`` event for the bundle's own handler;
  without it the browser drops the submit silently. ``form-action 'none'``
  below still refuses every actual submission.
* ``default-src 'none'``, ``form-action 'none'``, and every source limited to
  ``data:`` and ``blob:``: the bundle has no network of its own and no URL to
  exfiltrate through. No source names a host, ``'self'`` or a scheme that leaves
  the browser. Every capability it has arrives over ``postMessage`` and nothing
  else -- its asset and library BYTES included: the authenticated parent fetches
  them and posts them in, and this document turns each into a ``blob:`` URL of its
  own opaque origin. ``connect-src blob: data:`` exists so a loader that
  ``fetch``es its asset (GLTFLoader, Pixi, Phaser, Howler) can read that blob.
* ``frame-src`` inherits ``'none'``, so it cannot nest a frame to shop for a
  weaker context; ``frame-ancestors 'self'`` keeps this document from being
  embedded off-origin.

This response carries **no user content at all** — it is a fixed bootstrap. The
parent posts the bundle in after the frame reports ready. That is why the route
needs no authentication: there is nothing here to authorize, and keeping it
unauthenticated means no cookie or token path terminates inside the sandbox.

``script-src 'unsafe-inline'`` is required and is not a weakening: the whole point
of this document is to execute a bundle's script, and it does so in an origin that
owns nothing. ``'unsafe-eval'`` and ``'wasm-unsafe-eval'`` add nothing to that --
code that can already run any script gains no reach by compiling more -- and Pixi
v8 refuses to start without the first (real Chromium, 2026-10-02); WebAssembly
engines need the second. Reach is what the source lists govern, and none of them
names a network. The app's own page keeps its nonce-only policy.
"""

from __future__ import annotations

from typing import Any

# One in-flight bundle message, then the bootstrap stops listening for bundles.
# A bundle cannot be swapped underneath itself; the parent recreates the frame.
BOOTSTRAP_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Command Center UI</title>
<style>
html,body{margin:0;padding:0;height:100%;font:15px/1.5 system-ui,sans-serif;color:#111;background:#fff}
#ta-ui-root{min-height:100%}
#ta-ui-fault{margin:0;padding:12px 14px;font:13px/1.5 system-ui,sans-serif;color:#7a1020;background:#fdf0f2}
</style></head>
<body>
<p id="ta-ui-fault" hidden></p>
<div id="ta-ui-root"></div>
<script>
(function(){
  "use strict";
  var PROTOCOL = 1;
  var parentWindow = window.parent;
  // Nothing to host and no parent to serve: refuse rather than run loose.
  if (!parentWindow || parentWindow === window) { return; }

  // WebRTC is a real egress channel that CSP does not cover: ICE gathering
  // resolves attacker-controlled STUN hostnames, so a bundle could encode the
  // conversation it was shown into DNS lookups without any permission and
  // without touching `connect-src` (review, 2026-09-26). A CSP directive for it
  // does not exist, so the capability is REMOVED from the realm instead -- which
  // is enforced by JS semantics rather than by policy support.
  //
  // There is no route back to a pristine realm: this document's own CSP leaves
  // `frame-src` falling back to `default-src 'none'`, and no `allow-popups`
  // means `window.open` is blocked, so a nested frame and a popup are
  // unavailable as sources of a fresh constructor. A worker (from a blob: URL,
  // for a game's physics or pathfinding) is a fresh realm, but a worker scope has
  // no RTCPeerConnection at all, and its network is this same policy.
  try {
    for (var i = 0, gone = ["RTCPeerConnection", "webkitRTCPeerConnection",
                            "mozRTCPeerConnection", "RTCDataChannel",
                            "RTCIceTransport", "webkitRTCIceTransport"];
         i < gone.length; i++) {
      Object.defineProperty(window, gone[i], {value: undefined, configurable: false, writable: false});
    }
  } catch (_err) { /* already absent on this engine */ }

  var pending = Object.create(null);
  var nextId = 1;
  var started = false;

  function fault(text) {
    var node = document.getElementById("ta-ui-fault");
    node.textContent = String(text);
    node.hidden = false;
  }

  // The bundle's only capability. Every call is a request to the parent, which
  // decides whether the action exists at all; this side never assumes one does.
  function call(action, params) {
    return new Promise(function (resolve, reject) {
      var id = "r" + (nextId++);
      var timer = setTimeout(function () {
        if (pending[id]) { delete pending[id]; reject(new Error("bridge timed out")); }
      }, 180000);
      pending[id] = function (message) {
        clearTimeout(timer);
        if (message && message.ok) { resolve(message.result); }
        else { reject(new Error(String((message && message.error) || "refused"))); }
      };
      parentWindow.postMessage(
        {ta_ui: PROTOCOL, type: "call", id: id, action: String(action),
         params: (params && typeof params === "object") ? params : {}}, "*");
    });
  }

  // Bundle path -> blob: URL, minted here from bytes the parent posted. A blob:
  // URL belongs to this document's opaque origin and names no server.
  var assetUrls = Object.create(null);
  function asset(path) {
    path = String(path);
    return Object.prototype.hasOwnProperty.call(assetUrls, path) ? assetUrls[path] : null;
  }
  function blobUrl(bytes, type) {
    return URL.createObjectURL(new Blob([bytes], {type: String(type || "application/octet-stream")}));
  }
  // `ta-asset:<path>` in markup or style becomes that asset's blob: URL at render
  // time; the stored text is never rewritten. An unknown path is left as written
  // and simply fails to load, visibly.
  function withAssets(text) {
    return String(text || "").replace(/ta-asset:([A-Za-z0-9][A-Za-z0-9._/-]*)/g, function (whole, path) {
      return asset(path) || whole;
    });
  }
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var node = document.createElement("script");
      node.src = src;
      node.onload = resolve;
      node.onerror = function () { reject(new Error("a library failed to load")); };
      document.head.appendChild(node);
    });
  }

  function start(bundle) {
    if (started) { return; }
    started = true;
    var root = document.getElementById("ta-ui-root");
    var imports = Object.create(null), globals = [];
    try {
      var files = Array.isArray(bundle.files) ? bundle.files : [];
      for (var f = 0; f < files.length; f++) {
        var file = files[f];
        if (!file || typeof file.path !== "string" || !(file.bytes instanceof ArrayBuffer)) { continue; }
        var url = blobUrl(file.bytes, file.media_type);
        assetUrls[file.path] = url;
        if (/[.]m?js$/.test(file.path)) {
          // A blob: URL is not hierarchical, so a module asset imports a sibling
          // as "@ui/<path>"; the bundle's own script may also use "./<path>".
          imports["@ui/" + file.path] = url;
          imports["./" + file.path] = url;
        }
      }
      var libraries = Array.isArray(bundle.libraries) ? bundle.libraries : [];
      for (var l = 0; l < libraries.length; l++) {
        var lib = libraries[l];
        if (!lib || typeof lib.name !== "string" || !(lib.bytes instanceof ArrayBuffer)) { continue; }
        var libUrl = blobUrl(lib.bytes, "text/javascript");
        if (lib.format === "module") { imports[lib.name] = libUrl; } else { globals.push(libUrl); }
      }
      // The import map goes in before any module can run; this bootstrap is a
      // classic script, so nothing the bundle does can precede it.
      if (Object.keys(imports).length) {
        var map = document.createElement("script");
        map.type = "importmap";
        map.textContent = JSON.stringify({imports: imports});
        document.head.appendChild(map);
      }
      if (bundle.style) {
        var style = document.createElement("style");
        style.textContent = withAssets(bundle.style);
        document.head.appendChild(style);
      }
      // Markup is assigned, never parsed for scripts: a <script> tag inside
      // innerHTML does not execute, so the only script that runs is the one the
      // bundle declared. Inline handlers in markup do run — inside this origin,
      // which owns nothing, that is the bundle's own business.
      root.innerHTML = withAssets(bundle.markup);
    } catch (err) {
      fault("This UI failed while starting: " + ((err && err.message) || "unknown error"));
      return;
    }
    // Global libraries load in order, then the bundle's own script.
    globals.reduce(function (chain, src) {
      return chain.then(function () { return loadScript(src); });
    }, Promise.resolve()).then(function () {
      if (bundle.script) {
        var script = document.createElement("script");
        if (bundle.script_type === "module") { script.type = "module"; }
        script.textContent = String(bundle.script);
        document.body.appendChild(script);
      }
    }).catch(function (err) {
      fault("This UI failed while starting: " + ((err && err.message) || "unknown error"));
    });
  }

  window.addEventListener("message", function (event) {
    // Only the embedder is heard. A bundle that opens its own channel cannot
    // impersonate the bridge, and no third window can inject a bundle.
    if (event.source !== parentWindow) { return; }
    var message = event.data;
    if (!message || typeof message !== "object" || message.ta_ui !== PROTOCOL) { return; }
    if (message.type === "bundle") {
      var bundle = message.bundle;
      if (!bundle || typeof bundle !== "object") { fault("This UI arrived unreadable."); return; }
      start(bundle);
      return;
    }
    if (message.type === "result" && typeof message.id === "string") {
      var settle = pending[message.id];
      if (settle) { delete pending[message.id]; settle(message); }
    }
  });

  // The narrow client the bundle programs against. Sugar only: every method is
  // the same `call`, so the parent's allowlist is the single source of truth
  // about what exists.
  window.tinyassets = Object.freeze({
    protocol: PROTOCOL,
    call: call,
    asset: asset,
    whoami: function () { return call("whoami", {}); },
    listAgents: function () { return call("list_agents", {}); },
    sendMessage: function (text, agent) { return call("send_message", {text: text, agent: agent || ""}); },
    readConversation: function (limit, before) { return call("read_conversation", {limit: limit || 0, before: before}); },
    listAutomations: function () { return call("list_automations", {}); },
    listRuns: function (options) { return call("list_runs", options || {}); },
    readRun: function (runId) { return call("read_run", {run_id: runId}); },
    readRunOutput: function (runId, field, offset) { return call("read_run_output", {run_id: runId, field: field, offset: offset || 0}); },
    listFiles: function (path) { return call("list_files", {path: path || ""}); },
    readFile: function (path, offset) { return call("read_file", {path: path, offset: offset || 0}); },
    emit: function (name, data) { return call("emit", {name: name, data: data || {}}); },
    conversationDesign: function () { return call("conversation_design", {}); },
    setConversationDesign: function (definitionId, componentKey) {
      return call("set_conversation_design", definitionId
        ? {agent_definition_id: definitionId, component_key: componentKey || ""}
        : {state: "default"});
    }
  });

  parentWindow.postMessage({ta_ui: PROTOCOL, type: "ready"}, "*");
})();
</script>
</body></html>
"""

FRAME_CSP = (
    "sandbox allow-scripts allow-forms; "
    "default-src 'none'; "
    "script-src 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' blob:; "
    "style-src 'unsafe-inline' blob:; "
    "img-src data: blob:; "
    "font-src data: blob:; "
    "media-src data: blob:; "
    "connect-src data: blob:; "
    "worker-src blob:; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'self'"
)

FRAME_HEADERS = {
    "Content-Security-Policy": FRAME_CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    # DNS prefetch is the other egress channel CSP does not cover: a hostname in
    # a link or a `<link rel=dns-prefetch>` is resolved without any request the
    # policy sees. The WebRTC half of that pair is closed in the bootstrap.
    "X-DNS-Prefetch-Control": "off",
    # The document is fixed, but it is also the security boundary; a stale copy
    # after a policy fix is exactly the cache we do not want.
    "Cache-Control": "no-store",
}


async def handle_ui_frame(request: Any) -> Any:
    """Serve the fixed bundle host (GET/HEAD), or 404 when the dark flag is off."""
    from starlette.responses import HTMLResponse, PlainTextResponse

    from tinyassets.onboarding import onboarding_enabled

    if not onboarding_enabled():
        return PlainTextResponse("Not Found", status_code=404)
    return HTMLResponse(BOOTSTRAP_HTML, headers=dict(FRAME_HEADERS))
