/**
 * The binder's socket upgrade, as both sides spell it (`sockets.ts`; the doors' `binder.ts`):
 * a module with no imports, so the doors Worker takes these names without the object's code.
 *
 * The doors forward an owner app's upgrade as a fresh request of their own: a `GET` of
 * exactly `SOCKET_URL`, with no query, and exactly the headers `Upgrade: websocket`,
 * `BINDING_HEADER` (the verified credential's `Binding`, as JSON) and `THREAD_HEADER`. No
 * header a client sent survives the hop; the object accepts nothing else.
 */

/** The frames' version, in every frame both ways. */
export const SOCKET_VERSION = 1;
/** The one URL the binder's upgrade names: the object's socket door, with no query. */
export const SOCKET_URL = "https://owner.internal/agent/socket";
/** The bound identity the binder sets, as JSON: the `Binding` of the verified credential. */
export const BINDING_HEADER = "x-ta-binding";
/** The thread the socket watches, the caller's own. */
export const THREAD_HEADER = "x-ta-thread";
/** The binder's upgrade carries these headers and no other, sorted. */
export const SHAPE_HEADERS: readonly string[] = Object.freeze(["upgrade", BINDING_HEADER,
                                                               THREAD_HEADER]);
