/**
 * The one lock the runner takes (an engine's store, a pool user, the sync agent's turn): a
 * file naming its holder, `<pid> <boot>`, made whole beside its name and linked into place,
 * so no lock is ever seen half made. A lock is taken over once its holder is gone: its
 * process dead, or of another boot or an unknown one (a pid repeats after a reboot, which a
 * lock on disk outlives), by one taker alone (`takeOver`). A taker that cannot read its own
 * boot's id takes nothing and fails loudly. A wait sleeps, so the waiter's event loop goes
 * on (a run's heartbeats), at `FIRST_MS` and backing off to at most `MOST_MS`, and fails
 * loudly past its bound.
 */
import { randomBytes } from "node:crypto";
import { closeSync, linkSync, lstatSync, openSync, readFileSync, rmSync,
         writeSync } from "node:fs";

/** A wait's first sleep, and its longest. */
const FIRST_MS = 20;
const MOST_MS = 1_000;
/**
 * The longest a takeover's claim stands: a taker holds it for a few calls, so one older is a
 * taker's that died inside its takeover, and is let go. The residual: a taker stalled longer
 * than this inside its takeover (stopped, or its VM frozen) can let the lock be held twice,
 * since another may take it over meanwhile and the stalled one then removes it.
 */
const CLAIM_MS = 10_000;

/** Whether `pid` names a running process. */
export function alive(pid: number | null): boolean {
  if (pid === null || !Number.isInteger(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * This boot's id: a pid names a process only within the boot it was taken in. Unreadable or
 * empty, it throws: without it no lock tells a live holder of this boot from a gone one.
 */
export function bootId(): string {
  const id = readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim();
  if (id === "") {
    throw new Error("this boot's id reads empty: no lock can be taken without it");
  }
  return id;
}

/**
 * Whether the lock at `path` has a holder still there (none, or one gone, is free). A holder
 * of another boot, or of one its lock does not name, is gone.
 */
function held(path: string, boot: string): boolean {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return false;
    }
    throw error;
  }
  const [pid, of] = text.split(" ");
  return of === boot && alive(Number(pid));
}

/** The identity of the file at `path` (its inode), or null when there is none. */
function inode(path: string): string | null {
  try {
    return String(lstatSync(path, { bigint: true }).ino);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

/**
 * A gone holder's lock (the file `lock` at `path`) removed by one taker alone. Its claim is a
 * second name for that very file (`<path>.<lock>.gone`), which only one taker can make; the
 * lock goes only while the claim names that file, its holder still gone, and `path` still
 * names it, so a taker that read the gone holder never removes a lock another took since.
 * False while another taker's claim stands (that taker takes the lock).
 */
function takeOver(path: string, lock: string, boot: string): boolean {
  const claim = `${path}.${lock}.gone`;
  try {
    linkSync(path, claim);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return true;  // the lock went meanwhile: tried again
    }
    if (code !== "EEXIST") {
      throw error;
    }
    try {
      if (Date.now() - lstatSync(claim).ctimeMs > CLAIM_MS) {
        rmSync(claim, { force: true });  // its taker died inside its takeover
      }
    } catch {
      // let go meanwhile
    }
    return false;
  }
  try {
    if (inode(claim) === lock && !held(claim, boot) && inode(path) === lock) {
      rmSync(path, { force: true });
    }
  } finally {
    rmSync(claim, { force: true });
  }
  return true;
}

/** The lock taken now, or false while a holder still there keeps it (or another takes it). */
export function tryLock(path: string): boolean {
  const boot = bootId();
  const temp = `${path}.${randomBytes(6).toString("hex")}`;
  const fd = openSync(temp, "wx", 0o600);
  try {
    writeSync(fd, `${process.pid} ${boot}`);
  } finally {
    closeSync(fd);
  }
  try {
    for (;;) {
      try {
        linkSync(temp, path);
        return true;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
          throw error;
        }
      }
      const lock = inode(path);
      if (lock === null) {
        continue;  // let go meanwhile
      }
      if (held(path, boot) || !takeOver(path, lock, boot)) {
        return false;
      }
    }
  } finally {
    rmSync(temp, { force: true });
  }
}

/** The lock let go. */
export function unlock(path: string): void {
  rmSync(path, { force: true });
}

/**
 * `act` run holding the lock at `path`, waited for at most `waitMs` (unbounded: `Infinity`);
 * past it, `refused` is thrown. Each sleep doubles from `FIRST_MS` to `MOST_MS`.
 */
export async function withLock<T>(path: string, waitMs: number, refused: () => Error,
                                  act: () => T | Promise<T>): Promise<T> {
  const until = Date.now() + waitMs;
  for (let wait = FIRST_MS; !tryLock(path); wait = Math.min(wait * 2, MOST_MS)) {
    if (Date.now() >= until) {
      throw refused();
    }
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
  try {
    return await act();
  } finally {
    unlock(path);
  }
}
