/**
 * The install check (`ci/deps.ts`) on a lockfile and an install of its own, in a temp folder:
 * an install as locked passes, and each way one differs is named.
 */
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { mismatch } from "../deps";

let root = "";

function write(path: string, json: object): void {
  mkdirSync(dirname(join(root, path)), { recursive: true });
  writeFileSync(join(root, path), JSON.stringify(json));
}

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "deps-"));
  write("package-lock.json", { lockfileVersion: 3, packages: {
    "": { name: "root", workspaces: ["w"] },
    w: { name: "@x/w" },
    "node_modules/@x/w": { resolved: "w", link: true },
    "node_modules/a": { version: "1.0.0" },
    "node_modules/b": { version: "2.1.0" },
    "w/node_modules/b": { version: "3.0.0" },
    "node_modules/other-os": { version: "1.0.0", optional: true, os: [`!${process.platform}`] },
    "node_modules/this-os": { version: "1.0.0", optional: true, cpu: [process.arch] },
  } });
  write("w/package.json", { name: "@x/w" });
  write("node_modules/a/package.json", { name: "a", version: "1.0.0" });
  write("node_modules/b/package.json", { name: "b", version: "2.1.0" });
  write("w/node_modules/b/package.json", { name: "b", version: "3.0.0" });
  write("node_modules/this-os/package.json", { name: "this-os", version: "1.0.0" });
  mkdirSync(join(root, "node_modules", "@x"));
  symlinkSync(join("..", "..", "w"), join(root, "node_modules", "@x", "w"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("the install check", () => {
  it("passes an install as locked, an optional package of another platform absent", () => {
    expect(mismatch(root)).toBeNull();
  });

  it("names a package installed at a version other than its locked one", () => {
    write("node_modules/b/package.json", { name: "b", version: "2.5.0" });
    expect(mismatch(root)).toBe("node_modules/b is 2.5.0, locked 2.1.0");
  });

  it("names a missing package, a workspace's own among them", () => {
    rmSync(join(root, "w", "node_modules"), { recursive: true });
    expect(mismatch(root)).toBe("w/node_modules/b is missing (locked 3.0.0)");
  });

  it("names a missing optional package that installs on this machine", () => {
    rmSync(join(root, "node_modules", "this-os"), { recursive: true });
    expect(mismatch(root)).toBe("node_modules/this-os is missing (locked 1.0.0)");
  });

  it("names a package installed but not locked, a nested one among them", () => {
    write("node_modules/a/node_modules/b/package.json", { name: "b", version: "2.0.0" });
    expect(mismatch(root)).toBe("node_modules/a/node_modules/b is installed but not locked");
  });

  it("names a missing workspace link, and one to another package", () => {
    rmSync(join(root, "node_modules", "@x", "w"));
    expect(mismatch(root)).toBe("node_modules/@x/w is not linked to the workspace w");
    symlinkSync(join("..", "a"), join(root, "node_modules", "@x", "w"));
    expect(mismatch(root)).toBe("node_modules/@x/w is not linked to the workspace w");
  });

  it("names an optional package installed at another version", () => {
    write("node_modules/other-os/package.json", { name: "other-os", version: "0.9.0" });
    expect(mismatch(root)).toBe("node_modules/other-os is 0.9.0, locked 1.0.0");
  });
});
