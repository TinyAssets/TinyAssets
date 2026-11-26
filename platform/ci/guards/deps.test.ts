/**
 * The install check (`ci/deps.ts`) on a lockfile and an install of its own, in a temp folder:
 * an install as locked passes, and each way one differs is named.
 */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
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

  describe("shared dependencies of optional platform variants", () => {
    const leaf = "node_modules/runtime";
    const engine = "node_modules/wasm";
    let packages: Record<string, Record<string, unknown>>;
    function check(): string | null {
      write("package-lock.json", { lockfileVersion: 3, packages });
      return mismatch(root);
    }
    beforeEach(() => {
      packages = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8")).packages;
      packages["node_modules/other-os"]!.dependencies = { wasm: "1" };
      packages["node_modules/other-cpu"] = { version: "1", optional: true, cpu: [`!${process.arch}`],
        dependencies: { wasm: "1" } };
      packages[leaf] = { version: "2", optional: true };
      packages[engine] = { version: "1", optional: true, dependencies: { runtime: "2" } };
    });
    it("accepts a portable transitive shared only by absent incompatible variants", () => {
      expect(check()).toBeNull();
    });
    it("requires the hoisted runtime when a workspace also asks for it", () => {
      packages.w!.optionalDependencies = { runtime: "2" };
      expect(check()).toBe(leaf + " is missing (locked 2)");
    });
    it("requires children of a variant actually installed on this machine", () => {
      write(engine + "/package.json", { version: "1" });
      expect(check()).toBe(leaf + " is missing (locked 2)");
    });
    it("resolves a workspace's own locked version before the hoisted version", () => {
      packages.w!.devDependencies = { runtime: "3" };
      packages["w/node_modules/runtime"] = { version: "3", optional: true };
      expect(check()).toBe("w/node_modules/runtime is missing (locked 3)");
      write("w/node_modules/runtime/package.json", { version: "3" });
      expect(check()).toBeNull();
    });
    it("requires a nested optional peer when a different hoisted version would load", () => {
      const parent = "w/node_modules/host";
      packages[parent] = { version: "1", peerDependencies: { runtime: "3" },
        peerDependenciesMeta: { runtime: { optional: true } } };
      write(parent + "/package.json", { name: "host", version: "1" });
      write(leaf + "/package.json", { name: "runtime", version: "2" });
      packages["w/node_modules/runtime"] = { version: "3", optional: true, peer: true };
      expect(check()).toBe("w/node_modules/runtime is missing (locked 3)");
      write("w/node_modules/runtime/package.json", { name: "runtime", version: "3" });
      expect(check()).toBeNull();
    });
    it("accepts a cycle reached only through omitted variants", () => {
      packages[leaf]!.dependencies = { wasm: "1" };
      expect(check()).toBeNull();
      packages.w!.dependencies = { wasm: "1" };
      expect(check()).toBe(leaf + " is missing (locked 2)");
    });
    it("keeps an unanchored optional cycle required", () => {
      delete packages["node_modules/other-os"]!.dependencies;
      delete packages["node_modules/other-cpu"]!.dependencies;
      packages[leaf]!.dependencies = { wasm: "1" };
      expect(check()).toBe(leaf + " is missing (locked 2)");
    });
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
