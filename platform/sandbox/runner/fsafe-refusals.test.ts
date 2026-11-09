/**
 * `fsafe`'s refusals, each path's: no link followed, no special file read or written
 * through, a directory never replaced, modes and owner set on the descriptor, and the one
 * walker's order and refusals (rva6a's attack, held).
 */
import { execFileSync } from "node:child_process";
import { lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync,
         writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { LinkRefused, safePieces, safeRead, safeReadHead, safeWrite, walkFiles } from "./fsafe";

let t = "";
let victim = "";
let vdir = "";
beforeEach(() => {
  t = mkdtempSync(join(tmpdir(), "ta-fsafe-"));
  victim = join(t, "victim");
  writeFileSync(victim, "SECRET");
  vdir = join(t, "vdir");
  mkdirSync(vdir);
  writeFileSync(join(vdir, "inner"), "SECRET");
});
afterEach(() => {
  rmSync(t, { recursive: true, force: true });
});

describe("fsafe's read path", () => {
  it("reads no link, nothing through a linked directory, and no FIFO, without blocking",
     async () => {
    const base = join(t, "base");
    mkdirSync(join(base, "d"), { recursive: true });
    symlinkSync(victim, join(base, "link"));
    symlinkSync(vdir, join(base, "d", "ldir"));
    execFileSync("mkfifo", [join(base, "fifo")]);
    for (const path of [join(base, "link"), join(base, "d", "ldir", "inner"),
                        join(base, "fifo"), join(base, "d")]) {
      expect(safeRead(base, path), path).toBeNull();
      expect(safeReadHead(base, path, 2), path).toBeNull();
      expect(await safePieces(base, path, 4, () => undefined), path).toBe(false);
    }
  });
});

describe("fsafe's write path", () => {
  it("replaces a link and a FIFO at its name, never writes through one, never over a " +
     "directory, and sets its mode and owner", () => {
    const w = join(t, "w");
    mkdirSync(join(w, "adir"), { recursive: true });
    symlinkSync(victim, join(w, "over"));
    symlinkSync(vdir, join(w, "ldir"));
    execFileSync("mkfifo", [join(w, "fifo")]);
    safeWrite(w, join(w, "over"), Buffer.from("X"), 0o600);
    safeWrite(w, join(w, "fifo"), Buffer.from("X"), 0o600);
    expect(readFileSync(victim, "utf8")).toBe("SECRET");
    expect(lstatSync(join(w, "over")).isFile()).toBe(true);
    expect(lstatSync(join(w, "fifo")).isFile()).toBe(true);
    expect(() => safeWrite(w, join(w, "ldir", "x"), Buffer.from("X"), 0o600))
      .toThrow(LinkRefused);
    expect(() => safeWrite(w, join(w, "adir"), Buffer.from("X"), 0o600)).toThrow(LinkRefused);
    process.umask(0o077);
    try {
      safeWrite(w, join(w, "owned"), Buffer.from("O"), 0o644, { uid: 1234, gid: 4321 });
    } finally {
      process.umask(0o022);
    }
    const stat = statSync(join(w, "owned"));
    expect([stat.mode & 0o777, stat.uid, stat.gid]).toEqual([0o644, 1234, 4321]);
  });
});

describe("fsafe's walker", () => {
  it("offers regular files alone, in byte order when sorted, never entering a link", () => {
    const tree = join(t, "tree");
    mkdirSync(join(tree, "sub"), { recursive: true });
    for (const name of ["b", "a", "C", "_", "B"]) {
      writeFileSync(join(tree, name), name);
    }
    writeFileSync(join(tree, "sub", "x"), "x");
    symlinkSync(vdir, join(tree, "linkdir"));
    symlinkSync(victim, join(tree, "linkfile"));
    execFileSync("mkfifo", [join(tree, "fifo")]);
    expect([...walkFiles(tree, { sorted: true })]).toEqual(["B", "C", "_", "a", "b", "sub/x"]);
    expect([...walkFiles(tree)].sort()).toEqual(["B", "C", "_", "a", "b", "sub/x"]);
  });
});
