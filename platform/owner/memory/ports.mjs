// Loopback ports the system hands out free, so two runs of a leg on one host (two checkouts'
// `npm test`) never meet: a fixed port answered the other run's `EADDRINUSE`, or worse, its
// inspector, measuring another run's isolate. Each port is held open until all are read, so
// they are distinct; workerd binds them a moment later.
import { createServer } from "node:net";

export async function freePorts(count) {
  const servers = await Promise.all(Array.from({ length: count }, () =>
    new Promise((resolve, reject) => {
      const server = createServer();
      server.once("error", reject);
      server.listen(0, "127.0.0.1", () => resolve(server));
    })));
  const ports = servers.map((server) => server.address().port);
  await Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve))));
  return ports;
}
