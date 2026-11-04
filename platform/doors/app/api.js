// The owner app's one way to the doors: its sign-in, its first-party token, the owner
// surface's routes (`/app/api/*`) and a run's live socket. Same origin only: the session
// cookie is HttpOnly and Strict, so the browser sends it to `/app/api` itself, and the
// token lives in this tab's sessionStorage, never in a URL.

const TOKENS = "ta.app.tokens";
const SIGNIN = "ta.app.signin";

export const CLIENT_ID = `${location.origin}/oauth/clients/web.json`;
export const REDIRECT_URI = `${location.origin}/app/signed-in`;
export const RESOURCE = `${location.origin}/app`;

/** A refusal from the doors: its status and the doors' own message. */
export class Refused extends Error {
  constructor(status, message) {
    super(message);
    this.name = "Refused";
    this.status = status;
  }
}

function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_")
    .replace(/=+$/, "");
}

/** `n` random bytes, base64url. */
export function randomText(n) {
  return base64url(crypto.getRandomValues(new Uint8Array(n)));
}

async function s256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return base64url(new Uint8Array(digest));
}

function stored(key) {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

/** Whether this tab holds a sign-in. */
export function signedIn() {
  return stored(TOKENS) !== null;
}

/** Forget this tab's sign-in. */
export function forget() {
  sessionStorage.removeItem(TOKENS);
}

/** Begin the sign-in: our own authorization server, PKCE, back to `/app/signed-in`. */
export async function signIn() {
  const verifier = randomText(32);
  const state = randomText(16);
  sessionStorage.setItem(SIGNIN, JSON.stringify({ verifier, state }));
  const query = new URLSearchParams({
    response_type: "code", client_id: CLIENT_ID, redirect_uri: REDIRECT_URI, state,
    code_challenge: await s256(verifier), code_challenge_method: "S256", resource: RESOURCE,
  });
  location.assign(`/authorize?${query}`);
}

async function refresh() {
  const held = stored(TOKENS);
  if (held?.refresh_token === undefined) {
    return false;
  }
  const answer = await fetch("/oauth/token", {
    method: "POST", credentials: "same-origin",
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: held.refresh_token,
                                client_id: CLIENT_ID }),
  });
  if (!answer.ok) {
    forget();
    return false;
  }
  const tokens = await answer.json();
  sessionStorage.setItem(TOKENS, JSON.stringify({
    access_token: tokens.access_token, refresh_token: tokens.refresh_token ?? held.refresh_token,
    expires_at: Date.now() + (tokens.expires_in ?? 0) * 1000,
  }));
  return true;
}

async function post(path, body) {
  const held = stored(TOKENS);
  return fetch(`/app/api/${path}`, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json",
               ...(held === null ? {} : { Authorization: `Bearer ${held.access_token}` }) },
    body: JSON.stringify(body),
  });
}

/** Called when the doors no longer take this tab's sign-in. */
let onSignedOut = () => undefined;
export function whenSignedOut(callback) {
  onSignedOut = callback;
}

/**
 * One owner-surface route: its JSON answer, or `Refused` with the doors' own message. A
 * token past its life is refreshed once; a sign-in the doors no longer take signs out.
 */
export async function call(path, body = {}) {
  const held = stored(TOKENS);
  if (held !== null && held.expires_at < Date.now() + 30_000) {
    await refresh();
  }
  let answer = await post(path, body);
  if (answer.status === 401 && await refresh()) {
    answer = await post(path, body);
  }
  if (answer.status === 401) {
    forget();
    onSignedOut();
  }
  const type = answer.headers.get("Content-Type") ?? "";
  const parsed = type.startsWith("application/json") ? await answer.json() : null;
  if (!answer.ok) {
    throw new Refused(answer.status, parsed?.message ?? parsed?.error ?? `${answer.status}`);
  }
  return parsed;
}

/** Sign out: the session closed and this device's grant revoked, then forgotten here. */
export async function signOut() {
  try {
    await call("session/close");
  } finally {
    forget();
  }
}

/**
 * The doors' closes that end a socket for good (`owner/src/relay.ts` `CLOSE`): the owner
 * suspended or erased, signed out, or a frame refused. Any other close (the connection
 * dropped, the token's time up, the object restarted) is followed by a new socket.
 */
const FINAL_CLOSES = [4400, 4403, 4410, 4440];

/**
 * A live socket on one of the owner's conversations: a single-use ticket first (behind the
 * token), then the socket with the ticket as its one credential. `onFrame` gets each frame.
 * A socket that drops is followed by a new one (a new ticket), and `onAttach` is called
 * again so the caller reads the state as it now stands: the frames sent while it was down
 * are not sent again. `onClose` gets the close code and reason when it ends for good: closed
 * here, a final close from the doors, or no new ticket to be had. Answers a handle with
 * `close()` and the current socket's `readyState`.
 */
export async function follow(conversation, onFrame, onClose, onAttach = () => undefined) {
  let socket = null;
  let closed = false;
  const handle = {
    close() {
      closed = true;
      socket?.close();
    },
    get readyState() {
      return closed ? WebSocket.CLOSED : socket?.readyState ?? WebSocket.CONNECTING;
    },
  };
  const attach = async () => {
    const { ticket } = await call("agent/socket-ticket", { conversation_id: conversation });
    const query = new URLSearchParams({ conversation_id: conversation, ticket });
    const opened = new WebSocket(`wss://${location.host}/app/api/agent/socket?${query}`);
    socket = opened;
    let open = false;
    opened.addEventListener("open", () => {
      open = true;
    });
    opened.addEventListener("message", (event) => {
      try {
        onFrame(JSON.parse(event.data));
      } catch {
        onFrame({ kind: "unreadable", event: String(event.data) });
      }
    });
    opened.addEventListener("close", (event) => {
      if (closed || FINAL_CLOSES.includes(event.code)) {
        closed = true;
        onClose(event.code, event.reason);
        return;
      }
      // Dropped: a new socket, then the state read again. One that never opened waits a
      // moment first, so a refused socket is not asked for again at once.
      setTimeout(() => {
        if (closed) {
          return;
        }
        attach().then(() => onAttach(), (error) => {
          closed = true;
          onClose(event.code, error instanceof Error ? error.message : String(error));
        });
      }, open ? 0 : 1000);
    });
  };
  await attach();
  return handle;
}
