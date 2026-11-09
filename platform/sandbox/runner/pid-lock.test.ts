/**
 * The runner's one lock (`pid-lock.ts`): held by a live holder of this boot alone; taken
 * over from a holder that is gone (dead, or of another boot); waited for, sleeping, and
 * refused loudly past the wait's bound.
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { bootId, tryLock, unlock, withLock } from "./pid-lock";

const dirs: string[] = [];
function lockPath(): string {
  const dir = mkdtempSync(join(tmpdir(), "ta-lock-"));
  dirs.push(dir);
  return join(dir, "lock");
}
afterEach(() => {
  for (const dir of dirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

/** A pid no process holds now: a child's, once it has exited. */
function deadPid(): number {
  return Number(spawnSync(process.execPath, ["-e", "process.stdout.write(String(process.pid))"],
                          { encoding: "utf8" }).stdout);
}

describe("the runner's lock", () => {
  it("is held by its live holder alone, named whole, until it lets go", () => {
    const lock = lockPath();
    expect(tryLock(lock)).toBe(true);
    expect(readFileSync(lock, "utf8")).toBe(`${process.pid} ${bootId()}`);
    expect(tryLock(lock)).toBe(false);
    // Nothing but the lock beside it: what named it was linked into place, then let go.
    expect(readdirSync(join(lock, ".."))).toEqual(["lock"]);
    unlock(lock);
    expect(tryLock(lock)).toBe(true);
  });

  it("is taken over from a holder that is gone: dead, or of another boot", () => {
    const lock = lockPath();
    writeFileSync(lock, `${String(deadPid())} ${bootId()}`);
    expect(tryLock(lock)).toBe(true);
    writeFileSync(lock, "1 a-boot-before-this-one");  // pid 1: always alive, another boot
    expect(tryLock(lock)).toBe(true);
    writeFileSync(lock, `1 ${bootId()}`);  // pid 1 of this boot: held
    expect(tryLock(lock)).toBe(false);
  });

  it("is waited for while held, and refused loudly past the wait's bound", async () => {
    const lock = lockPath();
    expect(tryLock(lock)).toBe(true);
    setTimeout(() => unlock(lock), 150);
    const order: string[] = [];
    await withLock(lock, 5_000, () => new Error("held too long"), () => {
      order.push("held");
    });
    expect(order).toEqual(["held"]);
    expect(tryLock(lock)).toBe(true);  // let go after its act
    await expect(withLock(lock, 100, () => new Error("held too long"), () => undefined))
      .rejects.toThrow("held too long");
  });

  it("is asked again at its caller's longest sleep at most: a flat retry takes it at once",
     async () => {
    const lock = lockPath();
    expect(tryLock(lock)).toBe(true);
    let freed = 0;
    setTimeout(() => {
      unlock(lock);
      freed = Date.now();
    }, 1_500);
    let took = 0;
    // The store's flat 20 ms retry: never a backed-off second's wait after the lock is free.
    await withLock(lock, 5_000, () => new Error("held too long"), () => {
      took = Date.now();
    }, 20);
    expect(took - freed).toBeLessThan(200);
  });
});
