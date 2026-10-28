/**
 * ta-capprobe-callee: what the 32-call cap probe calls
 * (openspec/changes/clean-sheet-platform/cap-probe.md).
 *
 * Throwaway. `.github/workflows/cap-probe.yml` deploys it to workers.dev, and deletes it
 * when the run ends. Nothing in the product imports it.
 */
import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";

/** The Durable Object class the probe reaches through its `PEER` namespace binding (M7). */
export class Peer extends DurableObject {
  async ping(i) {
    return i;
  }
}

export default class Callee extends WorkerEntrypoint {
  /** One service-binding RPC call (M1, M3-M6a, M9, M12). */
  async ping(i) {
    return i;
  }

  /** Answers after `ms`, so a caller can await one call for that long (M10). */
  async hold(ms) {
    await new Promise((resolve) => setTimeout(resolve, ms));
    return ms;
  }

  /** `pong` for any path (M2, M3, M5, M8), or an echoing WebSocket on an upgrade (M9). */
  async fetch(request) {
    if (request.headers.get("Upgrade") === "websocket") {
      const [client, server] = Object.values(new WebSocketPair());
      server.accept();
      server.addEventListener("message", (event) => server.send(event.data));
      return new Response(null, { status: 101, webSocket: client });
    }
    return new Response("pong");
  }
}
