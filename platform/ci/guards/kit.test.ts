/**
 * The guard kit's and the Worker kit's own invariants (`ci/kit/`, `ci/workers.ts`), held once
 * for every workspace that uses them: every config file is found and no dependency's is,
 * every Worker's every source is what the source guards scan, a config that does not parse
 * fails loudly, a fake is built at its Worker's own date, an auxiliary Worker has a namespace
 * of every class it defines and every binding its config declares, a test's bindings merge
 * into its config's by name, a test pool's own Worker is built the same way with every secret
 * stated by the test, no test's Workers carry a host secret or read a config through the
 * pool, a gate is built from a contract's methods, and a job's own
 * sources are the scripts in its folder. The gate and the auxiliary Workers run in
 * `ci/test/gate.test.ts`.
 */
import { randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../paths";
import { gate, methodsOf } from "../gate";
import { configFiles, scriptsIn, workerSources } from "../kit/sources";
import { configBindings, declaredSecrets, fake, hostSecrets, main, objectBindings, once,
         parseWrangler, pool, readWrangler, withChanges, type WorkerConfig, workerConfig,
         workerFolders } from "../workers";

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

  it("merges a test's bindings into its config's by name, and keeps every other", () => {
    const config = { name: "x", compatibility_date: "2026-10-23",
                     vars: { A: "config", B: "config" },
                     kv_namespaces: [{ binding: "K", id: "k-id" }],
                     services: [{ binding: "S", service: "s" },
                                { binding: "T", service: "t", entrypoint: "E" }],
                     queues: { producers: [{ binding: "Q", queue: "q" }] },
                     r2_buckets: [{ binding: "R" }] } as WorkerConfig;
    expect(withChanges(config, {})).toEqual({
      bindings: { A: "config", B: "config" }, kvNamespaces: { K: { id: "k-id" } },
      queueProducers: { Q: { queueName: "q" } },
      serviceBindings: { S: { name: "s" }, T: { name: "t", entrypoint: "E" } },
      r2Buckets: ["R"] });
    expect(withChanges(config, { bindings: { B: "test", SECRET: "s" }, kvNamespaces: ["L"],
                                 serviceBindings: { S: "gate" }, r2Buckets: ["R2"],
                                 outboundService: "fake" })).toEqual({
      bindings: { A: "config", B: "test", SECRET: "s" },
      kvNamespaces: { K: { id: "k-id" }, L: "L" },
      queueProducers: { Q: { queueName: "q" } },
      serviceBindings: { S: "gate", T: { name: "t", entrypoint: "E" } },
      r2Buckets: { R: "R", R2: "R2" } });
    // A kind the config has none of is the test's alone (`auxiliary` keeps it as given).
    expect(withChanges({ name: "y", compatibility_date: "2026-10-23" }, { bindings: { A: "a" } }))
      .toEqual({});
  });

  it("builds a test pool's own Worker with every binding its config declares, a test's " +
     "binding replacing the config's of its name", () => {
    const dir = mkdtempSync(join(tmpdir(), "kit-main-"));
    try {
      writeFileSync(join(dir, "wrangler.jsonc"), JSON.stringify({
        name: "x", main: "src/a.ts", compatibility_date: "2026-10-23",
        compatibility_flags: ["nodejs_compat"],
        rules: [{ type: "Data", globs: ["**/x.bin"], fallthrough: false }],
        vars: { A: "config", B: "config" },
        secrets: { required: ["SECRET"] },
        kv_namespaces: [{ binding: "K", id: "k-id" }],
        services: [{ binding: "S", service: "s" }, { binding: "T", service: "t", entrypoint: "E" }],
        queues: { producers: [{ binding: "Q", queue: "q" }],
                  consumers: [{ queue: "in", max_batch_size: 1, dead_letter_queue: "dlq" }] },
        r2_buckets: [{ binding: "R", bucket_name: "r" }],
        durable_objects: { bindings: [{ name: "O", class_name: "Obj" }] },
        migrations: [{ tag: "v1", new_sqlite_classes: ["Obj"] }],
        assets: { directory: "./app", binding: "ASSETS", run_worker_first: true,
                  html_handling: "none" },
      }));
      const built = main(dir, { bindings: { B: "test", SECRET: "stated" },
                                serviceBindings: { S: "gate" }, outboundService: "fake" });
      expect(built).toEqual({
        main: join(dir, "src/a.ts"),
        miniflare: {
          rootPath: dir, compatibilityDate: "2026-10-23", compatibilityFlags: ["nodejs_compat"],
          modulesRules: [{ type: "Data", include: ["**/x.bin"], fallthrough: false }],
          bindings: { A: "config", B: "test", SECRET: "stated" },
          kvNamespaces: { K: { id: "k-id" } },
          serviceBindings: { S: "gate", T: { name: "t", entrypoint: "E" } },
          queueProducers: { Q: { queueName: "q" } },
          queueConsumers: { in: { maxBatchSize: 1, deadLetterQueue: "dlq" } },
          r2Buckets: ["R"],
          durableObjects: { O: { className: "Obj", useSQLite: true } },
          assets: { directory: join(dir, "app"), binding: "ASSETS", run_worker_first: true,
                    routerConfig: { has_user_worker: true,
                                    invoke_user_worker_ahead_of_assets: true },
                    assetConfig: { html_handling: "none", compatibility_date: "2026-10-23",
                                   compatibility_flags: ["nodejs_compat"] } },
          outboundService: "fake",
        },
      });
      expect(main(dir, { bindings: { SECRET: "stated" } }, "./test/entry.ts").main)
        .toBe("./test/entry.ts");
      expect(() => main(dir, { bindings: { B: "test" } }))
        .toThrow("x: a test states each secret its config requires, and SECRET is not stated");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("refuses every Worker whose config requires a secret the test does not state", () => {
    for (const folder of workerFolders()) {
      const dir = join(PLATFORM, folder);
      const config = workerConfig(dir);
      const secrets = config.secrets?.required ?? [];
      const stated = Object.fromEntries(secrets.map((name) => [name, `test-${name}`]));
      expect(() => main(dir, { bindings: stated }), folder).not.toThrow();
      for (const name of secrets) {
        const short = Object.fromEntries(Object.entries(stated).filter(([key]) => key !== name));
        expect(() => main(dir, { bindings: short }), folder)
          .toThrow(`${config.name}: a test states each secret its config requires, and ` +
                   `${name} is not stated`);
      }
    }
  });

  it("fails loudly on a config key, queue field or assets field the kit does not carry", () => {
    const base = { name: "x", compatibility_date: "2026-10-23" };
    expect(() => configBindings({ ...base, ratelimits: [] } as WorkerConfig))
      .toThrow("x: the Worker kit does not carry ratelimits");
    expect(() => configBindings({ ...base, queues: { other: [] } } as unknown as WorkerConfig))
      .toThrow("x: the Worker kit does not carry queues.other");
    expect(() => configBindings({ ...base, queues: { consumers: [{ queue: "q", odd: 1 }] } }))
      .toThrow("x: the Worker kit does not carry queues.consumers[].odd");
    expect(configBindings({ ...base, queues: { consumers: [
      { queue: "q", max_batch_size: 1, max_concurrency: 4, dead_letter_queue: "d" }] } }))
      .toEqual({ queueConsumers: { q: { maxBatchSize: 1, deadLetterQueue: "d" } } });
    const dir = mkdtempSync(join(tmpdir(), "kit-assets-"));
    try {
      writeFileSync(join(dir, "wrangler.jsonc"), JSON.stringify({ ...base, main: "a.ts",
        assets: { directory: "./app", binding: "A", run_worker_first: true, headers: {} } }));
      expect(() => main(dir, {})).toThrow(/x: the Worker kit reads assets'.*not headers/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("refuses a test's Workers that carry a host secret or read a config through the pool",
     () => {
    const name = declaredSecrets()[0]!;
    expect(name).toMatch(/^[A-Z][A-Z0-9_]+$/);
    // A dummy value, set for this test alone: never a real secret, and never printed.
    const dummy = `dummy-${randomBytes(12).toString("hex")}`;
    const before = process.env[name];
    process.env[name] = dummy;
    try {
      const options = { miniflare: { bindings: { X: dummy, Y: "test" },
                                     workers: [{ name: "w", bindings: { Z: [dummy] } }] } };
      expect(hostSecrets(options)).toEqual([`miniflare.bindings.X is the host's ${name}`,
                                            `miniflare.workers[0].bindings.Z[0] is the host's ${name}`]);
      let refused = "";
      try {
        pool(options);
      } catch (error) {
        refused = String(error);
      }
      expect(refused).toContain(`a test's Workers carry a host secret: miniflare.bindings.X`);
      expect(refused).not.toContain(dummy);
      const clean = { miniflare: { bindings: { X: "test" } } };
      expect(pool(clean)).toBe(clean);
      process.env[name] = "";
      expect(hostSecrets({ bindings: { X: "" } })).toEqual([]);
    } finally {
      if (before === undefined) {
        delete process.env[name];
      } else {
        process.env[name] = before;
      }
    }
    expect(() => pool({ wrangler: {} })).toThrow(/a test pool reads no config itself/);
    expect(() => pool({ experimental: { newConfig: true } }))
      .toThrow(/a test pool reads no config itself \(its experimental option/);
  });

  it("builds every test's Workers through the kit: no config read by the pool or wrangler, " +
     "and every pool checked", () => {
    // The pool's own config options, and wrangler as a module: its conversions and its dev
    // servers (`unstable_dev`, `unstable_startWorker`, `getPlatformProxy`) each fill a
    // Worker's secrets and vars from `.dev.vars`, `.env` and `process.env`. The kit runs only
    // wrangler's command line, to bundle.
    const forbidden: readonly (readonly [string, RegExp])[] = [
      ["configPath", /configPath/], ["newConfig", /newConfig/],
      ["the wrangler module", /(?:\bfrom|\bimport\s*\(|\brequire\s*\()\s*["']wrangler["']/],
    ];
    const problems: string[] = [];
    const walk = (dir: string, at: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = at === "" ? entry.name : `${at}/${entry.name}`;
        if (entry.isDirectory()) {
          if (!["node_modules", ".wrangler", "dist"].includes(entry.name)) {
            walk(join(dir, entry.name), path);
          }
          continue;
        }
        if (!/\.(?:[cm]?[jt]s)$/.test(entry.name) || path === "ci/guards/kit.test.ts") {
          continue;
        }
        const text = readFileSync(join(dir, entry.name), "utf8");
        for (const [word] of forbidden.filter(([, pattern]) => pattern.test(text))) {
          problems.push(`${path} names ${word}`);
        }
        const pools = text.split("cloudflareTest(").length - 1;
        const checked = text.split("cloudflareTest(once(async () => pool(").length - 1;
        if (entry.name === "vitest.config.ts" && pools !== checked) {
          problems.push(`${path} builds a test pool without pool()`);
        }
        const made = text.split("new Miniflare(").length - 1;
        const madeChecked = text.split("new Miniflare(convertV4MiniflareOptions(pool(").length - 1;
        if (made !== madeChecked) {
          problems.push(`${path} starts a Miniflare without pool()`);
        }
      }
    };
    walk(PLATFORM, "");
    expect(problems).toEqual([]);
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
