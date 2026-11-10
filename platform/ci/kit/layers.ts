/**
 * The import layers (untangle B1): every script under `platform/` and `scripts/` is in
 * exactly one layer, by its path, and each layer names what it may import. This file is data;
 * the checker is `imports.ts`'s `layerProblems`, which `ci/guards/isolation.test.ts` holds over
 * every file, with what each workspace still owes (`owed.ts`) the only exceptions.
 *
 * Patterns are paths from the repository root:
 * - `*` is any part of one name, `**` any folders (none included), `{a,b}` either;
 * - `<worker>` is a Worker's folder (one holding a `wrangler.jsonc`) and `<ws>` any
 *   workspace's (a folder of `platform/`). In `may`, `<ws>` is the importing file's own
 *   workspace, and so is `<worker>` in a layer whose files are named by it; elsewhere
 *   `<worker>` is any Worker's;
 * - `!` before a pattern takes out what the patterns before it took in. The last pattern that
 *   matches a path decides.
 *
 * An import is any way one module names another: an import or re-export (type ones too), a
 * type imported by path, `import()`, `require()` and `/// <reference path>`. A relative one is
 * held to `may` by the path it lands on; any other to `packages`, where a layer names them.
 * A workflow's run step that imports a module inline (`node -e "import … from './…'"`) is
 * checked as its own layer, `INLINE`.
 */

export interface Layer {
  /** What the layer is, as a problem names it. */
  readonly name: string;
  /** Its files. */
  readonly files: readonly string[];
  /** What it may import, by the path an import lands on. */
  readonly may: readonly string[];
  /** What more one Worker's source may import, by that Worker's folder. */
  readonly also?: Readonly<Record<string, readonly string[]>>;
  /**
   * The only specifiers other than a relative path it may import (a package, a runtime
   * module, an absolute path), each a name and its subpaths. Absent: any package, and no
   * absolute path.
   */
  readonly packages?: readonly string[];
  /** It may import a module named at run time: a guard or a tool loading what it reads. */
  readonly computed?: boolean;
  /**
   * It may import, too, whatever its own workspace's other layers may: a test drives its
   * subject with the subject's own inputs.
   */
  readonly inherits?: boolean;
}

/** The contract: its modules and its data files. */
const CONTRACT = Object.freeze(["platform/contract/src/**", "platform/contract/*.json"]);
/** The source records, a contract module beside their files (untangle A1). */
const SOURCES = Object.freeze(["platform/sources/src/**", "platform/sources/*.json"]);
/** A workspace's tests, in its `test/` folders or named `*.test.*`, wherever they sit. */
const NOT_TESTS = Object.freeze(["!**/test/**", "!**/*.test.*"]);

export const LAYERS: readonly Layer[] = Object.freeze([
  {
    name: "the contract",
    files: ["platform/contract/src/**", ...NOT_TESTS],
    may: CONTRACT,
    packages: ["node:crypto"],
  },
  {
    name: "the source records",
    files: ["platform/sources/src/**", ...NOT_TESTS],
    may: [...SOURCES, ...CONTRACT],
    packages: ["node:crypto"],
  },
  {
    // A Worker's own data beside its config (`doors/shared-apps.json`) and its build's
    // output (the sandbox's `dist/runner.mjs`, imported as data) are its own folder's.
    name: "a Worker's source",
    files: ["platform/<worker>/src/**", "platform/<worker>/worker-configuration.d.ts",
            ...NOT_TESTS],
    may: ["platform/<worker>/src/**", "platform/<worker>/*.json", "platform/<worker>/dist/**",
          ...CONTRACT, ...SOURCES],
    also: {
      owner: ["platform/starter/starter.json"],
      // The Worker↔runner wire: the Worker writes the documents the runner reads.
      sandbox: ["platform/sandbox/runner/protocol.ts"],
    },
  },
  {
    // The owner app's pages, run in the browser: they import one another by the path the
    // doors serve them at (`doors/src/pages.ts`).
    name: "the owner app's pages",
    files: ["platform/doors/app/**", ...NOT_TESTS],
    may: ["platform/doors/app/**"],
    packages: ["/app/static"],
  },
  {
    name: "the runner",
    files: ["platform/sandbox/runner/**", "!platform/sandbox/runner/{probes,route}.ts",
            ...NOT_TESTS],
    may: ["platform/sandbox/runner/**", "!platform/sandbox/runner/{probes,route}.ts",
          ...NOT_TESTS, ...CONTRACT, ...SOURCES],
  },
  {
    name: "the probes",
    files: ["platform/sandbox/runner/{probes,route}.ts"],
    may: ["platform/sandbox/runner/**", ...NOT_TESTS, ...CONTRACT, ...SOURCES],
  },
  {
    name: "the template",
    files: ["platform/sandbox/template/**", ...NOT_TESTS],
    may: ["platform/sandbox/template/**", "platform/sandbox/runner/**", ...NOT_TESTS,
          "platform/sandbox/e2b/**", ...CONTRACT, ...SOURCES],
  },
  {
    // A Worker's own data beside its config is the deploy kit's to read: it deploys them.
    name: "the deploy kit",
    files: ["platform/deploy/**", "!platform/deploy/guards/**",
            "!platform/deploy/vitest.config.ts", ...NOT_TESTS],
    may: ["platform/deploy/**", "!platform/deploy/guards/**", ...CONTRACT, ...SOURCES,
          "platform/sandbox/e2b/**", "platform/sandbox/template/pair.ts",
          "platform/ci/{paths,wrangler}.ts", "platform/<worker>/*.json"],
  },
  {
    // The starter's package, to count what it seeds, as the owner reads it.
    name: "the importer",
    files: ["platform/import/**", "!platform/import/guards/**",
            "!platform/import/vitest.config.ts", ...NOT_TESTS],
    may: ["platform/import/**", "!platform/import/guards/**", ...CONTRACT,
          "platform/deploy/{facts,cloudflare}.ts", "platform/ci/paths.ts",
          "platform/starter/starter.json"],
  },
  {
    name: "the ci kit",
    files: ["platform/ci/**", "!platform/ci/guards/**"],
    may: ["platform/ci/**", "!platform/ci/guards/**", ...CONTRACT],
    computed: true,
  },
  {
    // They read what they guard: the platform, but no Worker's runtime.
    name: "the ci guards",
    files: ["platform/ci/guards/**"],
    may: ["platform/**", "!platform/<worker>/src/**"],
    computed: true,
  },
  {
    // A workspace's tools are the scripts at its top (its test config, its generators).
    name: "a workspace's tests, guards, memory legs and tools",
    files: ["platform/<ws>/*.{ts,mts,cts,js,mjs,cjs}", "!platform/{ci,deploy,import}/*",
            "!platform/*/worker-configuration.d.ts", "platform/{deploy,import}/vitest.config.ts",
            "platform/<ws>/**/test/**", "platform/<ws>/**/*.test.*",
            "platform/<ws>/{guards,memory}/**", "!platform/ci/**"],
    may: ["platform/<ws>/**", "platform/contract/**", "platform/sources/**", "platform/ci/**"],
    computed: true,
    inherits: true,
  },
  {
    name: "the repository's scripts",
    files: ["scripts/**"],
    may: ["scripts/**"],
    computed: true,
  },
]);

/**
 * A workflow's run step that imports a module inline, from the step's working directory: no
 * Worker's source, and not the release kit's entry (a workflow reads what it needs from data).
 */
export const INLINE: Layer = Object.freeze({
  name: "a workflow's inline module",
  files: [".github/workflows/*.{yml,yaml}"],
  may: ["**", "!platform/<worker>/src/**", "!platform/deploy/deploy.ts"],
});

/** The scripts the layers hold: every file with one of these extensions. */
export const SCRIPT = /\.(?:[cm]?[jt]s|tsx|jsx)$/;
