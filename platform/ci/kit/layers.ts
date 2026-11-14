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
 * type imported by path, `import()`, `require()` (under any name), a test's module mock and a
 * `/// <reference>` by path. A relative one is held to `may` by the path it lands on, and so
 * is one that names a workspace by its package (`@tinyassets/owner/src/x`). Any other is held
 * to `packages`, where a layer names them; where it names none, any package may be imported,
 * and nothing that names a place another way (an absolute path, a URL, a `#` name).
 * A module a workflow's step names inline (`node -e "import … from './…'"`) is checked as its
 * own layer, `INLINE`.
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
   * The only specifiers other than a path it may import (a package, a runtime module, an
   * absolute path), each a name and its subpaths. Absent: any package, and no absolute path.
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
const SOURCES = Object.freeze(["platform/sources/src/**", "platform/sources/records/*.json"]);
/** A workspace's tests, in its `test/` folders or named `*.test.*`, wherever they sit. */
const NOT_TESTS = Object.freeze(["!**/test/**", "!**/*.test.*"]);

/** What a workspace's tests and guards may import, beside what its own layers may. */
const TESTS_MAY = Object.freeze(["platform/<ws>/**", "platform/contract/**", "platform/sources/**",
                                 "platform/ci/**", "!platform/ci/guards/**"]);

export const LAYERS: readonly Layer[] = Object.freeze([
  {
    name: "the contract",
    files: ["platform/contract/src/**", ...NOT_TESTS],
    may: [...CONTRACT, ...NOT_TESTS],
    packages: ["node:crypto"],
  },
  {
    name: "the source records",
    files: ["platform/sources/src/**", ...NOT_TESTS],
    may: [...SOURCES, ...CONTRACT, ...NOT_TESTS],
    packages: ["node:crypto"],
  },
  {
    // A Worker's own data beside its config (`doors/shared-apps.json`) and its build's
    // output (the sandbox's `dist/runner.mjs`, imported as data) are its own folder's.
    name: "a Worker's source",
    files: ["platform/<worker>/src/**", ...NOT_TESTS],
    may: ["platform/<worker>/src/**", "platform/<worker>/*.json", "platform/<worker>/dist/**",
          ...CONTRACT, ...SOURCES, ...NOT_TESTS],
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
    may: ["platform/doors/app/**", ...NOT_TESTS],
    packages: ["/app/static"],
  },
  {
    name: "the runner",
    files: ["platform/sandbox/runner/**", "!platform/sandbox/runner/{probes,route}.ts",
            ...NOT_TESTS],
    may: ["platform/sandbox/runner/**", "!platform/sandbox/runner/{probes,route}.ts",
          ...CONTRACT, ...SOURCES, ...NOT_TESTS],
  },
  {
    name: "the probes",
    files: ["platform/sandbox/runner/{probes,route}.ts"],
    may: ["platform/sandbox/runner/**", ...CONTRACT, ...SOURCES, ...NOT_TESTS],
  },
  {
    name: "the template",
    files: ["platform/sandbox/template/**", ...NOT_TESTS],
    may: ["platform/sandbox/template/**", "platform/sandbox/runner/**",
          "platform/sandbox/e2b/**", ...CONTRACT, ...SOURCES, ...NOT_TESTS],
  },
  {
    // A Worker's own data beside its config is the deploy kit's to read: it deploys them.
    name: "the deploy kit",
    files: ["platform/deploy/**", "!platform/deploy/guards/**",
            "!platform/deploy/vitest.config.ts", ...NOT_TESTS],
    may: ["platform/deploy/**", "!platform/deploy/guards/**", ...CONTRACT, ...SOURCES,
          "platform/sandbox/e2b/**", "platform/sandbox/template/pair.ts",
          "platform/ci/{paths,wrangler}.ts", "platform/<worker>/*.json", ...NOT_TESTS],
  },
  {
    // The starter's package, to count what it seeds, as the owner reads it.
    name: "the importer",
    files: ["platform/import/**", "!platform/import/guards/**",
            "!platform/import/vitest.config.ts", ...NOT_TESTS],
    may: ["platform/import/**", "!platform/import/guards/**", ...CONTRACT,
          "platform/deploy/{facts,cloudflare}.ts", "platform/ci/paths.ts",
          "platform/owner/src/starter-package.ts", ...NOT_TESTS],
    // Node's file system and SQLite: no process, no socket, no package.
    packages: ["node:crypto", "node:fs", "node:os", "node:path", "node:sqlite"],
  },
  {
    // What the deploy kit and the generators take of the ci kit: where things are, and the
    // Workers' configs as wrangler reads them. Node's own modules alone, so the deploy kit
    // never loads TypeScript or miniflare.
    name: "the wrangler module",
    files: ["platform/ci/{paths,wrangler}.ts"],
    may: ["platform/ci/{paths,wrangler}.ts"],
    packages: ["node:fs", "node:module", "node:path", "node:url"],
  },
  {
    name: "the ci kit",
    files: ["platform/ci/**", "!platform/ci/guards/**", "!platform/ci/{paths,wrangler}.ts"],
    may: ["platform/ci/**", "!platform/ci/guards/**", ...CONTRACT, "platform/deploy/control.json"],
    computed: true,
  },
  {
    // They read what they guard as files, or load it by a computed path; they import the kit.
    name: "the ci guards",
    files: ["platform/ci/guards/**"],
    may: ["platform/ci/**", ...CONTRACT],
    computed: true,
  },
  {
    // A workspace's tools are the scripts at its top but its test configs (d2 §G14, d3 §O6):
    // its generators and builds, run in Node. They take their own workspace, the contract and
    // the records, never the test kit.
    name: "a workspace's tools",
    files: ["platform/<ws>/*.{ts,mts,cts,js,mjs,cjs}", "!platform/{ci,deploy,import}/*",
            "!platform/<ws>/vitest.config.ts", "!platform/sandbox/root-tests.mjs"],
    may: ["platform/<ws>/**", "!platform/<ws>/guards/**", ...CONTRACT, ...SOURCES, ...NOT_TESTS],
  },
  {
    // A workspace's test configs and its test runner sit at its top beside its tools.
    name: "a workspace's tests and memory legs",
    files: ["platform/<ws>/vitest.config.ts", "!platform/ci/*",
            "platform/sandbox/root-tests.mjs",
            "platform/<ws>/**/test/**", "platform/<ws>/**/*.test.*",
            "platform/<ws>/memory/**", "!platform/<ws>/guards/**", "!platform/ci/**"],
    may: TESTS_MAY,
    inherits: true,
  },
  {
    // A guard loads what it reads (a page's module, a built bundle) by a computed path.
    name: "a workspace's guards",
    files: ["platform/<ws>/guards/**", "!platform/ci/**"],
    may: TESTS_MAY,
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
 * A module a workflow's or a composite action's step names inline, however the step runs it:
 * none of the platform's by a path (a workflow reads what it needs from data, or runs a
 * script that is in a layer). The repository's scripts it may load, as two workflows'
 * `github-script` steps do: they hold nothing of the platform's.
 */
export const INLINE: Layer = Object.freeze({
  name: "a workflow's inline module",
  files: [".github/workflows/*.{yml,yaml}", ".github/actions/*/action.{yml,yaml}"],
  may: ["scripts/**"],
});

/** The scripts the layers hold: every file with one of these extensions. */
export const SCRIPT = /\.(?:[cm]?[jt]s|tsx|jsx)$/;
