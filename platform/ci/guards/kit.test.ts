/**
 * The guard kit's and the Worker kit's own invariants (`ci/kit/`, `ci/workers.ts`), held once
 * for every workspace that uses them: every config file is found and no dependency's is,
 * every Worker's every source is what the source guards scan, a config that does not parse
 * fails loudly, a fake is built at its Worker's own date, an auxiliary Worker has a namespace
 * of every class it defines, a gate is built from a contract's methods, and a job's own
 * sources are the scripts in its folder. The gate and the auxiliary Workers run in
 * `ci/test/gate.test.ts`.
 */
import { mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../paths";
import { gate, methodsOf } from "../gate";
import { configFiles, scriptsIn, workerSources } from "../kit/sources";
import { fake, objectBindings, once, parseWrangler, readWrangler, type WorkerConfig,
         workerConfig } from "../workers";

/**
 * The files under a Worker's `src/` that are no script, each named: data a source imports
 * and the guards check by its import's path. Any other file there is a script the source
 * guards must scan.
 */
const DATA_FILES: readonly string[] = Object.freeze(["owner/src/model-ranking.json"]);

describe("the guard kit", () => {
  it("finds every wrangler config under a root, in order, and none of a dependency's", () => {
    const root = mkdtempSync(join(tmpdir(), "kit-config-"));
    try {
      mkdirSync(join(root, "src"), { recursive: true });
      mkdirSync(join(root, "node_modules", "dep"), { recursive: true });
      mkdirSync(join(root, ".wrangler", "tmp"), { recursive: true });
      // A dependency's, and wrangler's own local state's, are none of the Worker's.
      for (const path of ["wrangler.jsonc", "wrangler.toml", "src/wrangler.json",
                          "src/wrangler.toml", "node_modules/dep/wrangler.toml",
                          ".wrangler/tmp/wrangler.json"]) {
        writeFileSync(join(root, path), "");
      }
      expect(configFiles(root)).toEqual(["src/wrangler.json", "src/wrangler.toml",
                                         "wrangler.jsonc", "wrangler.toml"]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("holds every Worker's every file, a source the source guards scan unless it is a " +
     "named data file", () => {
    const sources = [...workerSources().keys()];
    const workers = readdirSync(PLATFORM, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name !== "node_modules")
      .map((entry) => entry.name)
      .filter((name) => readdirSync(join(PLATFORM, name)).includes("src"));
    expect(workers).toEqual(expect.arrayContaining(["codemode", "doors", "files", "gateway",
                                                    "owner", "sandbox"]));
    const files = workers.flatMap((worker) =>
      readdirSync(join(PLATFORM, worker, "src"), { recursive: true, encoding: "utf8" })
        .filter((path) => extname(path) !== "")
        .map((path) => `${worker}/src/${path.split("\\").join("/")}`));
    for (const data of DATA_FILES) {
      expect(files, data).toContain(data);
    }
    expect([...sources].sort()).toEqual(files.filter((path) => !DATA_FILES.includes(path)).sort());
  });
});

describe("the Worker kit", () => {
  it("reads a Worker's config by its folder or its file, and fails loudly on one that does " +
     "not parse", () => {
    expect(readWrangler(join(PLATFORM, "gateway")))
      .toEqual(readWrangler(join(PLATFORM, "gateway", "wrangler.jsonc")));
    expect(parseWrangler('{ // a comment\n "name": "x", }')).toEqual({ name: "x" });
    expect(() => parseWrangler('{ "name": ', "broken.jsonc")).toThrow(/broken\.jsonc/);
  });

  it("builds a fake at its own Worker's compatibility date, named as its file", () => {
    const built = fake(join(PLATFORM, "gateway", "test", "fake-upstream.ts"), { extra: 1 });
    expect(built.name).toBe("fake-upstream");
    expect(built.compatibilityDate).toBe(workerConfig(join(PLATFORM, "gateway")).compatibility_date);
    expect(built.modules[0]!.contents).not.toMatch(/: Request\b/);
    expect(built.extra).toBe(1);
  });

  it("gives a Worker a namespace of each class it defines that it does not bind itself, and " +
     "binds another script's class by its script name", () => {
    const owner = workerConfig(join(PLATFORM, "owner"));
    expect(objectBindings(owner))
      .toEqual({ OwnerDO: { className: "OwnerDO", useSQLite: true } });
    expect(objectBindings(workerConfig(join(PLATFORM, "codemode"))))
      .toEqual({ CODEMODE: { className: "CodemodeSandbox", useSQLite: true } });
    expect(objectBindings(workerConfig(join(PLATFORM, "doors")))).toEqual({
      OWNER: { className: "OwnerDO", scriptName: owner.name, useSQLite: true } });
    expect(objectBindings(workerConfig(join(PLATFORM, "files")))).toEqual({});
    const renamed = { name: "x", compatibility_date: "2026-10-23", migrations: [
      { tag: "v2", renamed_classes: [{ from: "A", to: "B" }] }] } as WorkerConfig;
    expect(() => objectBindings(renamed)).toThrow(/x: migration v2 has renamed_classes/);
  });

  it("builds a gate before a service or before objects, with a contract's methods", () => {
    const store = methodsOf(join(PLATFORM, "contract", "src", "files.ts"), "ChunkStore");
    expect(store).toEqual(expect.arrayContaining(["mintKey", "putChunks", "getChunks"]));
    expect(store).toHaveLength(14);
    expect(() => methodsOf(join(PLATFORM, "contract", "src", "files.ts"), "NoSuch"))
      .toThrow(/declares no interface NoSuch/);
    const files = gate("files-gate", store, { worker: join(PLATFORM, "files"),
                                              entrypoint: "FilesUpload" });
    const real = workerConfig(join(PLATFORM, "files"));
    expect([files.name, files.compatibilityDate, files.bindings,
            "serviceBindings" in files ? files.serviceBindings : null]).toEqual([
      "files-gate", real.compatibility_date,
      { GATE: JSON.stringify({ methods: store, object: false }) },
      { REAL: { name: real.name, entrypoint: "FilesUpload" } }]);
    const owner = gate("owner-gate", ["policyRead"], { worker: join(PLATFORM, "owner"),
                                                       className: "OwnerDO" });
    expect("durableObjects" in owner ? owner.durableObjects : null).toEqual({
      REAL: { className: "OwnerDO", scriptName: workerConfig(join(PLATFORM, "owner")).name,
              useSQLite: true },
      OwnerDO: { className: "OwnerDO", useSQLite: true } });
    expect(owner.modules[0]!.contents).toMatch(/export \{ GatedObject as OwnerDO \};\s*$/);
    expect(() => gate("g", [], { worker: join(PLATFORM, "files") })).toThrow(/one or more/);
    expect(() => gate("g", ["a", "a"], { worker: join(PLATFORM, "files") })).toThrow(/each once/);
    expect(() => gate("g", ["a"], { worker: join(PLATFORM, "owner"), className: "a-b" }))
      .toThrow(/no class name/);
  });

  it("builds a test pool's Workers once, when first asked", () => {
    let made = 0;
    const options = once(() => ({ made: ++made }));
    expect(made).toBe(0);
    expect(options()).toBe(options());
    expect(made).toBe(1);
  });
});
