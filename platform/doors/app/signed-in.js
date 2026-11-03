// The sign-in's return page. A classic script, the page's first and only one, so this runs
// before anything else can read the address bar: the code and state leave it at once
// (`history.replaceState`), then the code is exchanged for this tab's first-party token.
"use strict";
(() => {
  const query = new URLSearchParams(location.search);
  const code = query.get("code");
  const state = query.get("state");
  const error = query.get("error");
  // RFC 9207: a response naming an issuer must name ours, or it is not our sign-in.
  const issuer = query.get("iss");
  history.replaceState(null, "", location.pathname);

  const say = (text) => {
    const shown = () => {
      document.getElementById("status").textContent = text;
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", shown, { once: true });
    } else {
      shown();
    }
  };

  let begun = null;
  try {
    begun = JSON.parse(sessionStorage.getItem("ta.app.signin") ?? "null");
  } catch {
    begun = null;
  }
  sessionStorage.removeItem("ta.app.signin");
  if (error !== null || code === null || begun === null || state !== begun.state
      || (issuer !== null && issuer !== location.origin)) {
    say(error !== null ? "The sign-in was not finished. Go back to the app to try again."
                       : "This sign-in was not started in this tab. Go back to the app to try again.");
    return;
  }
  fetch("/oauth/token", {
    method: "POST", credentials: "same-origin",
    body: new URLSearchParams({
      grant_type: "authorization_code", code, redirect_uri: `${location.origin}/app/signed-in`,
      client_id: `${location.origin}/oauth/clients/web.json`, code_verifier: begun.verifier,
      resource: `${location.origin}/app`,
    }),
  }).then(async (answer) => {
    if (!answer.ok) {
      say("The sign-in could not be finished. Go back to the app to try again.");
      return;
    }
    const tokens = await answer.json();
    sessionStorage.setItem("ta.app.tokens", JSON.stringify({
      access_token: tokens.access_token, refresh_token: tokens.refresh_token,
      expires_at: Date.now() + (tokens.expires_in ?? 0) * 1000,
    }));
    location.replace("/app");
  }, () => say("The sign-in could not be finished. Go back to the app to try again."));
})();
