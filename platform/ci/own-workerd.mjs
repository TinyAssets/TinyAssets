// A memory leg's own workerd (`owner/memory/`, `gateway/memory/`), held for its whole life.
// Two runs of a leg on one host once met: a child that died on a bind error left the leg
// measuring whatever answered on its ports (an orphaned workerd from another worktree), and a
// leg stopped by a timeout left its workerd running, holding the ports. So:
// - its ports are ones the system hands out free (`freePorts`), never fixed ones;
// - the child's early exit fails the leg at once, loudly, before another figure is read;
// - `check()` confirms the child is still alive (after the inspector answers, after a
//   request), so what answered was this leg's own;
// - the child is killed when the leg ends however it ends: `stop()`, process exit, SIGTERM,
//   SIGINT or SIGHUP (the signal's own exit status follows). The handlers are installed
//   before the spawn: a signal between the two would end the leg by its default action and
//   leave the child running.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { dirname, join } from "node:path";

/** The pinned workerd binary, wrangler's own. */
export const WORKERD = join(dirname(createRequire(import.meta.url)
  .resolve("workerd/package.json")), "bin", "workerd");

/**
 * `count` loopback ports the system hands out free, so two runs of a leg on one host (two
 * checkouts' `npm test`) never meet: a fixed port answered the other run's `EADDRINUSE`, or
 * worse, its inspector, measuring another run's isolate. Each port is held open until all are
 * read, so they are distinct; workerd binds them a moment later.
 */
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

/**
 * A bundle's files (`ci/workers.ts` `bundle`), written to `folder` beside a leg's config, as
 * a workerd Worker's modules in that config, each by its kind.
 */
export function capnpModules(folder, built) {
  const kinds = { ESModule: "esModule", CompiledWasm: "wasm", Data: "data" };
  return built.map(({ file, type }) =>
    `(name = "${file}", ${kinds[type]} = embed "${folder}/${file}")`).join(", ");
}

export function ownWorkerd(bin, args, { onEarlyExit } = {}) {
  let child = null;
  let stopping = false;
  let exited = null;
  const kill = () => {
    if (child !== null && exited === null) {
      child.kill("SIGKILL");
    }
  };
  const signals = { SIGTERM: 143, SIGINT: 130, SIGHUP: 129 };
  const onSignal = (signal) => {
    kill();
    process.exit(signals[signal]);
  };
  process.on("exit", kill);
  for (const signal of Object.keys(signals)) {
    process.on(signal, onSignal);
  }
  const release = () => {
    process.off("exit", kill);
    for (const signal of Object.keys(signals)) {
      process.off(signal, onSignal);
    }
  };
  // A signal's handler runs only once this synchronous code is done, so it sees the child.
  child = spawn(bin, args, { stdio: ["ignore", "inherit", "inherit"] });
  // The leg's own work keeps it running; the child alone never does, so a leg that ends
  // without `stop()` exits, and its exit kills the child.
  child.unref();
  child.on("exit", (code, signal) => {
    exited = { code, signal };
    if (!stopping) {
      release();
      console.error(`workerd (pid ${child.pid}) exited before the leg was done (code ${code}, ` +
                    `signal ${signal}): its ports may answer for another process; nothing ` +
                    "measured after this is the leg's own");
      onEarlyExit?.();
      process.exit(1);
    }
  });
  child.on("error", (error) => {
    exited = { code: null, signal: null };
    release();
    console.error(`workerd could not start: ${error.message}`);
    onEarlyExit?.();
    process.exit(1);
  });
  return {
    child,
    /** Throws unless this leg's own child is still running. */
    check() {
      if (exited !== null || child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`workerd (pid ${child.pid}) is not running: what answered is not ` +
                        "this leg's own");
      }
    },
    stop() {
      stopping = true;
      kill();
      release();
    },
  };
}
