/**
 * The kernel guard (untangle B7, `ci/kit/kernels.ts`): no source a kernel holds does its work
 * outside its home, but for exactly what each workspace owes (`<workspace>/guards/rules.ts`'s
 * `OWED`, `ci/kit/owed.ts`).
 */
import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { repoScripts } from "../kit/imports";
import { kernelProblems, KERNELS } from "../kit/kernels";
import { owedLists, ratchet } from "../kit/owed";
import { REPO } from "../paths";

/** What the guard finds in one source at `path`, from the repository root. */
const found = (path: string, source: string) =>
  kernelProblems(new Map([[path, source]])).map((each) => `${each.workspace} ${each.what}`);

describe("the kernel guard", () => {
  it("finds every kernel's work outside its home, but for exactly what is owed", async () => {
    expect(KERNELS.every((kernel) => existsSync(join(REPO, kernel.home)))).toBe(true);
    expect(ratchet("kernels", kernelProblems(repoScripts()), await owedLists())).toEqual([]);
  });

  it("counts a source's SHA-256s, by node:crypto or Web Crypto, and no HMAC or other hash",
     () => {
    expect(found("platform/files/src/a.ts", [
      'createHash("sha256").update(a).digest("hex");',
      "crypto.createHash('SHA256');",
      'await crypto.subtle.digest("SHA-256", bytes);',
      'await crypto.subtle.digest({ name: "sha-256" }, bytes);',
      'createHmac("sha256", key);',
      'createHash("sha1");',
      'crypto.subtle.digest("SHA-512", bytes);',
    ].join("\n"))).toEqual(["files platform/files/src/a.ts: SHA-256 ×4"]);
  });

  it("counts the owner's reads of Date.now, called or passed", () => {
    expect(found("platform/owner/src/a.ts", "const a = Date.now(); const b = [Date.now];"))
      .toEqual(["owner platform/owner/src/a.ts: Date.now() ×2"]);
  });

  it("holds each kernel's layers under its folder, and not its home", () => {
    const both = 'createHash("sha256"); Date.now();';
    expect([
      found("platform/contract/src/digest.ts", both),
      found("platform/contract/src/a.ts", both),
      found("platform/owner/src/clock.ts", both),
      found("platform/owner/test/a.test.ts", both),
      found("platform/doors/src/a.ts", both),
      found("platform/sandbox/runner/a.ts", both),
      found("platform/sandbox/template/pair.ts", both),
    ]).toEqual([
      [], ["contract platform/contract/src/a.ts: SHA-256 ×1"],
      ["owner platform/owner/src/clock.ts: SHA-256 ×1"], [],
      ["doors platform/doors/src/a.ts: SHA-256 ×1"],
      ["sandbox platform/sandbox/runner/a.ts: SHA-256 ×1"], [],
    ]);
  });
});
