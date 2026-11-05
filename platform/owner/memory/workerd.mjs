// A leg's own workerd, held for its whole life. Two runs of a leg on one host once met: a
// child that died on a bind error left the leg measuring whatever answered on its ports (an
// orphaned workerd from another worktree), and a leg stopped by a timeout left its workerd
// running, holding the ports. So:
// - the child's early exit fails the leg at once, loudly, before another figure is read;
// - `check()` confirms the child is still alive (after the inspector answers, after a
//   request), so what answered was this leg's own;
// - the child is killed when the leg ends however it ends: `stop()`, process exit, SIGTERM,
//   SIGINT or SIGHUP (the signal's own exit status follows).
import { spawn } from "node:child_process";

export function ownWorkerd(bin, args, { onEarlyExit } = {}) {
  const child = spawn(bin, args, { stdio: ["ignore", "inherit", "inherit"] });
  // The leg's own work keeps it running; the child alone never does, so a leg that ends
  // without `stop()` exits, and its exit kills the child.
  child.unref();
  let stopping = false;
  let exited = null;
  const kill = () => {
    if (exited === null) {
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
