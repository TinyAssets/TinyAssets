// The owner app's live socket (`app/api.js` `follow()`; slice BRAIN, task #68 and its review):
// a dropped socket is followed by a new one after a wait that doubles while sockets keep
// dropping, and the run's kept frames the doors send again on each new socket (the relay's
// tail, `owner/src/relay.ts`) are shown once. A fake browser: `location`, `sessionStorage`,
// `fetch` (the ticket door) and a WebSocket whose server sends the live run's tail on every
// connect, as the relay does. Run with `npm run test:app` (node's test runner).
import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.location = { origin: "https://doors.test", host: "doors.test" };
const store = new Map();
globalThis.sessionStorage = {
  getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v),
  removeItem: (k) => store.delete(k) };
let tickets = 0;
globalThis.fetch = async () => {
  tickets += 1;
  return new Response(JSON.stringify({ ticket: `t${tickets}` }),
                      { status: 200, headers: { "Content-Type": "application/json" } });
};

/** The live run's frames the relay keeps, sent again on each connect. */
let tail = [];
let behaviour = "tail";  // or "flap": open, then closed 4500 at once
const sockets = [];

class FakeSocket extends EventTarget {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    super();
    this.url = url;
    this.readyState = 0;
    sockets.push(this);
    setTimeout(() => {
      this.readyState = 1;
      this.dispatchEvent(new Event("open"));
      if (behaviour === "flap") {
        this.drop(4500, "socket_lost");
        return;
      }
      for (const frame of tail) {
        this.send(frame);
      }
    }, 1);
  }

  send(frame) {
    const event = new Event("message");
    event.data = JSON.stringify(frame);
    this.dispatchEvent(event);
  }

  drop(code, reason) {
    if (this.readyState === 3) {
      return;
    }
    this.readyState = 3;
    const event = new Event("close");
    event.code = code;
    event.reason = reason;
    this.dispatchEvent(event);
  }

  close() {
    this.drop(1000, "");
  }
}
globalThis.WebSocket = FakeSocket;

const { follow } = await import("../app/api.js");
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const frame = (run, seq) => ({ v: 1, run, seq, at: seq, kind: "event", event: { n: seq } });

test("a dropped socket is followed by a new one, and no frame is shown twice", async () => {
  behaviour = "tail";
  tail = [frame("r1", 1), frame("r1", 2), frame("r1", 3)];
  const shown = [];
  let attached = 0;
  let ended = null;
  const handle = await follow("c1", (f) => shown.push(`${f.run}:${f.seq}`),
                              (code) => { ended = code; }, () => { attached += 1; });
  await wait(20);
  assert.deepEqual(shown, ["r1:1", "r1:2", "r1:3"]);
  sockets.at(-1).drop(1006, "");  // the network drops it
  tail = [...tail, frame("r1", 4)];  // the run went on meanwhile
  await wait(1200);
  assert.equal(attached, 1, "a new socket was attached");
  // The new socket's tail is the whole kept run again: only the frame not yet shown shows.
  assert.deepEqual(shown, ["r1:1", "r1:2", "r1:3", "r1:4"]);
  // A new run in the conversation starts its own count.
  sockets.at(-1).send(frame("r2", 1));
  sockets.at(-1).send(frame("r1", 2));
  assert.deepEqual(shown.slice(4), ["r2:1"]);
  assert.equal(ended, null);
  handle.close();
  assert.equal(ended, 1000);
});

test("a final close ends the follow, with no new socket", async () => {
  behaviour = "tail";
  tail = [];
  const before = sockets.length;
  let ended = null;
  await follow("c3", () => undefined, (code) => { ended = code; });
  await wait(10);
  sockets.at(-1).drop(4440, "signed_out");
  await wait(1200);
  assert.equal(ended, 4440);
  assert.equal(sockets.length, before + 1);
});

test("sockets that keep dropping are asked for again with a doubling wait, not in a loop",
     async () => {
  behaviour = "flap";
  const before = tickets;
  const handle = await follow("c2", () => undefined, () => undefined);
  await wait(2500);
  handle.close();
  // The first ticket at once, then after 1 s, then after 2 s more: two by 2.5 s.
  assert.equal(tickets - before, 2);
});
