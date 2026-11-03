// A model source's connect return page (`/app/connected`). The callback handed it the code,
// the state and the redirect URI in the URL fragment, which no server sees. A classic
// script, the page's first and only one, so this runs before any other script: the
// fragment is read and cleared from the address bar and the history entry at once
// (`history.replaceState`), then posted to `connections/oauth/finish` in this tab's
// sign-in. The page loads nothing from another origin and sends no referrer.
"use strict";
(() => {
  const handed = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, "", location.pathname);

  const say = (text, done) => {
    const shown = () => {
      document.getElementById("status").textContent = text;
      document.getElementById("back").hidden = !done;
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", shown, { once: true });
    } else {
      shown();
    }
  };

  const state = handed.get("state");
  const code = handed.get("code");
  const redirect = handed.get("redirect_uri");
  if (state === null || redirect === null) {
    say("There is nothing to finish here.", true);
    return;
  }
  if (code === null) {
    say("The connection was not approved, so nothing was connected.", true);
    return;
  }
  let tokens = null;
  try {
    tokens = JSON.parse(sessionStorage.getItem("ta.app.tokens") ?? "null");
  } catch {
    tokens = null;
  }
  if (tokens === null || tokens.expires_at < Date.now()) {
    say("Your sign-in in this tab has ended, so the connection was not finished. Sign in " +
        "and connect again.", true);
    return;
  }
  say("Finishing the connection…", false);
  fetch("/app/api/connections/oauth/finish", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens.access_token}` },
    body: JSON.stringify({ state, code, redirect_uri: redirect }),
  }).then(async (answer) => {
    const body = await answer.json().catch(() => ({}));
    if (!answer.ok) {
      say(`The connection was not finished: ${body.error ?? answer.status}.`, true);
      return;
    }
    say(`Connected ${body.display_name ?? "the source"}.`, true);
  }, () => say("The connection was not finished: the network failed.", true));
})();
