/**
 * The binder's socket upgrade, as both sides spell it (`sockets.ts`; the doors' `binder.ts`):
 * a module with no imports, so the doors Worker takes these names without the object's code.
 *
 * The doors forward an owner app's upgrade as a fresh request of their own: a `GET` of
 * exactly `SOCKET_URL`, with no query, and exactly the headers `Upgrade: websocket`,
 * `BINDING_HEADER` (the `Binding` the ticket names, as JSON), `THREAD_HEADER` and
 * `TICKET_HEADER` (the ticket's secret). No header a client sent survives the hop; the
 * object accepts nothing else, and consumes the ticket itself.
 *
 * A socket ticket, as the app holds it: `ta1.<owner, base64url>.<secret, 64 hex>`. The
 * doors read only the owner from it, to reach that owner's object; the secret is the
 * object's to check against the one hash it keeps.
 */

/** The frames' version, in every frame both ways. */
export const SOCKET_VERSION = 1;
/** The one URL the binder's upgrade names: the object's socket door, with no query. */
export const SOCKET_URL = "https://owner.internal/agent/socket";
/** The bound identity the binder sets, as JSON. */
export const BINDING_HEADER = "x-ta-binding";
/** The thread the socket watches, the owner's own. */
export const THREAD_HEADER = "x-ta-thread";
/** The ticket's secret, which the object consumes. */
export const TICKET_HEADER = "x-ta-ticket";
/** The binder's upgrade carries these headers and no other, sorted. */
export const SHAPE_HEADERS: readonly string[] = Object.freeze(["upgrade", BINDING_HEADER,
                                                               THREAD_HEADER, TICKET_HEADER]);

/** A ticket reused, expired, for another thread or a signed-out grant, or no ticket: alike. */
export const TICKET_REFUSED = "ticket_refused: the socket ticket is not good for this socket";
/** An upgrade that failed inside: a fixed code, never its error's text. */
export const SOCKET_FAILED = "socket_failed: the socket could not be opened";

const SECRET = /^[0-9a-f]{64}$/;
const OWNER = /^[A-Za-z0-9_-]{1,512}$/;

/** The ticket the app holds, for `owner`'s object and its `secret`. */
export function socketTicket(owner: string, secret: string): string {
  const bytes = new TextEncoder().encode(owner);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  const encoded = btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `ta1.${encoded}.${secret}`;
}

/** The owner and secret a ticket names, or null for anything that is not one. */
export function ticketParts(ticket: string): { owner: string; secret: string } | null {
  const [version, encoded, secret, ...rest] = ticket.split(".");
  if (version !== "ta1" || encoded === undefined || secret === undefined || rest.length > 0
      || !OWNER.test(encoded) || !SECRET.test(secret)) {
    return null;
  }
  try {
    const binary = atob(encoded.replace(/-/g, "+").replace(/_/g, "/"));
    const owner = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(
      Uint8Array.from(binary, (char) => char.charCodeAt(0)));
    return owner === "" ? null : { owner, secret };
  } catch {
    return null;
  }
}
