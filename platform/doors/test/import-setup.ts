/**
 * The import consumer's tests' bundle (`import.test.ts`), made in Node before the pool starts:
 * the slice-2 builder (`import/bundle.ts`) run on its old-stack-shaped backup copy
 * (`import/guards/fixture.ts`), and one live owner's bundle handed to the workerd tests whole,
 * each file's bytes by its name in the bundle. Deleted at the cutover with the importer.
 */
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { TestProject } from "vitest/node";

import { TIERS } from "../../ci/fixtures";
import { buildBundles } from "../../import/bundle";
import { checkedBundle } from "../../import/stage";
import { ALICE, fixture, FOUNDER, keyOf } from "../../import/guards/fixture";

/** One bundle as the tests take it: its owner, key, digest and every file, base64. */
export interface ImportBundle {
  readonly owner: string;
  readonly key: string;
  readonly bundle: string;
  readonly files: Readonly<Record<string, string>>;
}

declare module "vitest" {
  export interface ProvidedContext {
    importBundle: ImportBundle;
  }
}

export default function setup(project: TestProject): void {
  const made = fixture();
  const out = mkdtempSync(join(tmpdir(), "ta-doors-bundles-"));
  const priv = mkdtempSync(join(tmpdir(), "ta-doors-private-"));
  try {
    buildBundles(made.copy, { out, private: priv, exclude: new Set([FOUNDER]), tiers: TIERS });
    const key = keyOf(ALICE);
    const staged = checkedBundle(join(out, key), key);
    project.provide("importBundle", {
      owner: ALICE, key, bundle: staged.bundle,
      files: Object.fromEntries(staged.parts.map((name) =>
        [name, readFileSync(join(out, key, name)).toString("base64")])),
    });
  } finally {
    for (const dir of [made.work, out, priv]) {
      rmSync(dir, { recursive: true, force: true });
    }
  }
}
