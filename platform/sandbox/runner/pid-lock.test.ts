/**
 * The runner's one lock (`pid-lock.ts`): held by a live holder of this boot alone; taken
 * over from a holder that is gone (dead, or of another boot), by one taker alone however
 * many race for it; waited for, sleeping, and refused loudly past the wait's bound.
 */
import { spawn, spawnSync } from "node:child_process";
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

  it("is taken over by one taker alone, however many race for a gone holder's lock",
     async () => {
    // Takers in processes of their own, each told the round at once (a byte on its stdin),
    // every round a lock of its own whose holder is dead: a taker that read the dead holder
    // never removes the lock another took since. Each taker lives to the end, so a lock it
    // took stays held.
    const TAKERS = 4;
    const ROUNDS = 300;
    const base = lockPath();
    for (let round = 0; round < ROUNDS; round += 1) {
      writeFileSync(`${base}.${round}`, `${String(deadPid())} ${bootId()}`);
    }
    const taker = `
      import { readSync, writeSync } from "node:fs";
      const { tryLock } = await import(process.argv[1]);
      const byte = Buffer.alloc(1);
      for (let round = 0; ; round += 1) {
        let read = 0;
        for (;;) {
          try { read = readSync(0, byte, 0, 1, null); break; }
          catch (error) { if (error.code !== "EAGAIN") { throw error; } }
        }
        if (read === 0) { break; }
        writeSync(1, tryLock(process.argv[2] + "." + round) ? "1" : "0");
      }`;
    const takers = Array.from({ length: TAKERS }, () => {
      const child = spawn(process.execPath, ["--input-type=module", "-e", taker,
                                             join(import.meta.dirname, "pid-lock.ts"), base],
                          { stdio: ["pipe", "pipe", "inherit"] });
      const said = { text: "", child };
      child.stdout.on("data", (data: Buffer) => { said.text += data.toString("utf8"); });
      return said;
    });
    const winners: number[] = [];
    for (let round = 0; round < ROUNDS; round += 1) {
      for (const each of takers) {
        each.child.stdin.write("x");
      }
      while (takers.some((each) => each.text.length <= round)) {
        await new Promise((resolve) => setImmediate(resolve));
      }
      winners.push(takers.filter((each) => each.text[round] === "1").length);
    }
    await Promise.all(takers.map((each) => new Promise((resolve) => {
      each.child.on("close", resolve);
      each.child.stdin.end();
    })));
    expect(winners.filter((count) => count !== 1)).toEqual([]);
    // No claim is left beside the locks.
    expect(readdirSync(join(base, "..")).filter((name) => name.endsWith(".gone"))).toEqual([]);
  });
});
