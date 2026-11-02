/**
 * The owner app's live socket, as both sides spell it (`relay.ts`; the doors' `binder.ts`
 * and `app.ts`): a module with no imports, so the doors Worker takes these names without
 * the object's code. Salvaged from A6 (`platform-a6`), its thread now a conversation.
 *
 * The doors forward an owner app's upgrade as a fresh request of their own: a `GET` of
 * exactly `SOCKET_URL`, with no query, and exactly the headers `Upgrade: websocket`,
 * `BINDING_HEADER` (the `Binding` the ticket names, as JSON), `CONVERSATION_HEADER` and
 * `TICKET_HEADER` (the SHA-256 of the ticket's secret). No header a client sent survives the
 * hop; the object accepts nothing else, and consumes the ticket itself.
 *
 * The ticket the app holds is a routing-MAC token of the `socket` kind
 * (`doors/src/routing-mac.ts`): the doors verify its MAC and its expiry before any object is
 * reached, so a forged or altered ticket reaches none, and hand the object only its
 * secret's hash, which the object consumes once against the one row it keeps.
 */

/** The frames' version, in every frame both ways. */
export const SOCKET_VERSION = 1;
/** The one URL the binder's upgrade names: the object's socket door, with no query. */
export const SOCKET_URL = "https://owner.internal/agent/socket";
/** The bound identity the binder sets, as JSON. */
export const BINDING_HEADER = "x-ta-binding";
/** The conversation the socket follows, the owner's own. */
export const CONVERSATION_HEADER = "x-ta-conversation";
/** The SHA-256 of the ticket's secret, which the object consumes. */
export const TICKET_HEADER = "x-ta-ticket";
/** The binder's upgrade carries these headers and no other, sorted. */
export const SHAPE_HEADERS: readonly string[] = Object.freeze(["upgrade", BINDING_HEADER,
                                                               CONVERSATION_HEADER,
                                                               TICKET_HEADER]);

/** A ticket reused, expired, for another conversation or a signed-out grant: alike. */
export const TICKET_REFUSED = "ticket_refused: the socket ticket is not good for this socket";
/** An upgrade that failed inside: a fixed code, never its error's text. */
export const SOCKET_FAILED = "socket_failed: the socket could not be opened";
/** How long a socket ticket is good for, in seconds; it is used once. */
export const SOCKET_TICKET_S = 30;
/**
 * Socket tickets outstanding at once, each single-use and at most `SOCKET_TICKET_S` long; past
 * it a mint is refused (`socket_tickets_full`). A technical ceiling of the owner's database.
 */
export const SOCKET_TICKETS = 1024;
